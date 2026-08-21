import asyncio
import io
import json
import subprocess
import urllib.error

import pytest

from app.maintenance import MaintenanceAssistant, MaintenanceError


def proposal(patch=""):
    return {
        "diagnosis": "test diagnosis",
        "root_cause": "test cause",
        "confidence": 0.8,
        "affected_files": ["app/main.py"],
        "changes": ["small change"],
        "patch": patch,
        "tests": ["python -m compileall -q app"],
        "risk": "low",
    }


def test_redacts_sensitive_values():
    text = "api_key=secret authorization: Bearer abc password=hunter2 https://user:pass@example.test"
    redacted = MaintenanceAssistant._redact(text)
    assert "secret" not in redacted
    assert "abc" not in redacted
    assert "hunter2" not in redacted
    assert "user:pass" not in redacted


def test_rejects_malformed_json():
    with pytest.raises(MaintenanceError, match="invalid JSON"):
        MaintenanceAssistant._parse_json("not json")


def test_rejects_protected_patch():
    with pytest.raises(MaintenanceError, match="Unsafe proposal"):
        MaintenanceAssistant._clean_patch("--- a/.env\n+++ b/.env\n@@\n+TOKEN=x")


def test_rejects_invalid_proposal_fields():
    invalid = proposal()
    invalid["risk"] = "unsafe"
    with pytest.raises(MaintenanceError):
        MaintenanceAssistant._validate_proposal(invalid)


def test_successful_groq_request(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())
    assert asyncio.run(assistant._request("openai/gpt-oss-120b", "hello")) == "ok"


def test_provider_http_403_with_json_error(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {"Content-Type": "application/json"},
            io.BytesIO(json.dumps({"error": {"message": "model access denied", "type": "invalid_request_error", "code": "forbidden"}}).encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(MaintenanceError, match="model access denied"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_401_is_reported(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(json.dumps({"error": {"message": "invalid API key"}}).encode()))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(MaintenanceError, match="invalid API key"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_404_model_not_found(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(json.dumps({"error": {"message": "model not found", "code": "model_not_found"}}).encode()))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(MaintenanceError, match="model not found"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_429_is_ratelimit(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, io.BytesIO(json.dumps({"error": {"message": "rate limit exceeded", "code": "rate_limit_exceeded"}}).encode()))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(MaintenanceError, match="rate limit"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_fallback_model_used_when_primary_rejected(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b", "openai/gpt-oss-20b")
    calls = []

    def fake_request(model, prompt):
        calls.append(model)
        if model == "openai/gpt-oss-120b":
            raise MaintenanceError("AI provider rejected the request.\n\nHTTP: 403\nProvider error: model access denied\nCode: forbidden")
        return "fallback ok"

    monkeypatch.setattr(assistant, "_request", fake_request)
    assert asyncio.run(assistant._ask("hello")) == "fallback ok"
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert assistant.last_model == "openai/gpt-oss-20b"


def test_both_models_fail_reports_both_provider_errors(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b", "openai/gpt-oss-20b")

    def fake_request(model, prompt):
        raise MaintenanceError(f"AI provider rejected the request.\n\nHTTP: 403\nProvider error: {model} rejected\nCode: forbidden")

    monkeypatch.setattr(assistant, "_request", fake_request)
    with pytest.raises(MaintenanceError, match="openai/gpt-oss-120b"):
        asyncio.run(assistant._ask("hello"))


def test_malformed_json_response_is_handled(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())
    with pytest.raises(MaintenanceError, match="malformed JSON"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_redacts_sensitive_values_with_env_names():
    text = "TELEGRAM_BOT_TOKEN=abc AI_API_KEY=def ADMIN_SECRET_KEY=ghi Authorization: Bearer xyz exchange_api_key=wow"
    redacted = MaintenanceAssistant._redact(text)
    assert "abc" not in redacted
    assert "def" not in redacted
    assert "ghi" not in redacted
    assert "xyz" not in redacted
    assert "wow" not in redacted


def test_rejected_proposal_is_not_applied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assistant = MaintenanceAssistant("", "", "")
    assistant.proposal_dir.mkdir(parents=True)
    item = proposal()
    item.update(id="reject-me", status="pending")
    assistant.proposals["reject-me"] = item
    assistant._save(item)
    assert "rejected" in assistant.reject("reject-me")
    assert json.loads((tmp_path / "logs/ai-proposals/reject-me.json").read_text())["status"] == "rejected"


def test_apply_rolls_back_when_health_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assistant = MaintenanceAssistant("", "", "")
    assistant.proposal_dir.mkdir(parents=True)
    patch = "--- a/app/main.py\n+++ b/app/main.py\n@@\n-old\n+new\n"
    item = proposal(patch)
    item.update(id="rollback-me", status="validated")
    assistant.proposals["rollback-me"] = item
    assistant._save(item)
    calls = []

    def run(command):
        calls.append(command)
        if command[:3] == ["git", "apply", "--check"]:
            return __import__("subprocess").CompletedProcess(command, 0, "", "")
        if command[:2] == ["git", "apply"] and "--reverse" not in command:
            return __import__("subprocess").CompletedProcess(command, 0, "", "")
        return __import__("subprocess").CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(assistant, "_run", run)
    monkeypatch.setattr(assistant, "health_check", lambda: {"healthy": False, "details": "startup failed"})
    assert "rolled back" in assistant.apply("rollback-me")
    assert ["git", "apply", "--reverse", "logs/ai-proposals/rollback-me.patch"] in calls
