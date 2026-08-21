import asyncio
import io
import json
import subprocess
import urllib.error
from pathlib import Path

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

    class FakeMessage:
        content = "ok"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    return FakeResponse()

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    assert asyncio.run(assistant._request("openai/gpt-oss-120b", "hello")) == "ok"


def test_provider_http_403_with_json_error(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    class FakeError(Exception):
        pass

    class FakeAPIStatusError(Exception):
        def __init__(self, body, status_code=403):
            self.body = body
            self.status_code = status_code

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise FakeAPIStatusError(json.dumps({"error": {"message": "model access denied", "type": "invalid_request_error", "code": "forbidden"}}), 403)

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    with pytest.raises(MaintenanceError, match=r"Provider message: model access denied"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_401_is_reported(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    class FakeAPIStatusError(Exception):
        def __init__(self, body, status_code=401):
            self.body = body
            self.status_code = status_code

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise FakeAPIStatusError(json.dumps({"error": {"message": "invalid API key"}}), 401)

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    with pytest.raises(MaintenanceError, match=r"Provider message: invalid API key"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_404_model_not_found(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    class FakeAPIStatusError(Exception):
        def __init__(self, body, status_code=404):
            self.body = body
            self.status_code = status_code

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise FakeAPIStatusError(json.dumps({"error": {"message": "model not found", "code": "model_not_found"}}), 404)

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    with pytest.raises(MaintenanceError, match=r"Provider message: model not found"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_429_is_ratelimit(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")

    class FakeAPIStatusError(Exception):
        def __init__(self, body, status_code=429):
            self.body = body
            self.status_code = status_code

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise FakeAPIStatusError(json.dumps({"error": {"message": "rate limit exceeded", "code": "rate_limit_exceeded"}}), 429)

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    with pytest.raises(MaintenanceError, match=r"Provider message: rate limit exceeded"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_provider_http_413_retries_with_reduced_context(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "openai/gpt-oss-120b")
    prompts = []

    class FakeAPIStatusError(Exception):
        def __init__(self, body, status_code=413):
            self.body = body
            self.status_code = status_code

    class FakeMessage:
        content = "recovered"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    prompts.append(kwargs["messages"][1]["content"])
                    if len(prompts) == 1:
                        raise FakeAPIStatusError(json.dumps({"error": {"message": "too many tokens"}}))
                    return FakeResponse()

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    assert asyncio.run(assistant._request("openai/gpt-oss-120b", "x" * 30000)) == "recovered"
    assert len(prompts) == 2
    assert len(prompts[1]) < len(prompts[0])


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

    class FakeAPIStatusError(Exception):
        def __init__(self, body, status_code=400):
            self.body = body
            self.status_code = status_code

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise FakeAPIStatusError("not-json", 400)

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    with pytest.raises(MaintenanceError, match=r"Provider message: not-json"):
        asyncio.run(assistant._request("openai/gpt-oss-120b", "hello"))


def test_redacts_sensitive_values_with_env_names():
    text = "TELEGRAM_BOT_TOKEN=abc AI_API_KEY=def ADMIN_SECRET_KEY=ghi Authorization: Bearer xyz exchange_api_key=wow"
    redacted = MaintenanceAssistant._redact(text)
    assert "abc" not in redacted
    assert "def" not in redacted
    assert "ghi" not in redacted
    assert "xyz" not in redacted
    assert "wow" not in redacted


def test_repository_discovery_covers_application_tests_and_configuration():
    assistant = MaintenanceAssistant("", "", "", repo_path=str(Path.cwd()))
    files = set(assistant.list_repository_files())
    assert {"app/exchanges/registry.py", "app/handlers.py", "app/maintenance.py", "app/scanner.py", "app/ui.py", "app/config.py", "tests/test_maintenance.py"} <= files
    assert any(item["path"] == "app/handlers.py" for item in assistant.search_repository("opportunity_details"))
    assert any(item["path"] == "app/scanner.py" for item in assistant.search_repository("run_cycle"))
    assert isinstance(assistant.git_status(), str)


def test_repository_discovery_excludes_sensitive_files(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "feature.py").write_text("def investigate_error():\n    return True\n")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=secret\n")
    (tmp_path / "credentials.json").write_text('{"api_key": "secret"}\n')
    (tmp_path / "state.sqlite3").write_bytes(b"\x00secret")
    assistant = MaintenanceAssistant("", "", "", repo_path=str(tmp_path))
    files = assistant.list_repository_files()
    assert "app/feature.py" in files
    assert ".env" not in files
    assert "credentials.json" not in files
    assert "state.sqlite3" not in files
    assert assistant.search_repository("investigate_error")[0]["path"] == "app/feature.py"


def test_repository_context_reads_the_matched_source_window():
    assistant = MaintenanceAssistant("", "", "", repo_path=str(Path.cwd()))
    context = assistant.repository_context("opportunity_details")
    assert "async def opportunity_details" in context
    assert "CallbackQueryHandler" in context


def test_prompt_budget_is_below_configured_limit(monkeypatch):
    assistant = MaintenanceAssistant("https://api.groq.com/openai/v1", "key", "model", max_input_tokens=5500)
    captured = []

    class FakeMessage:
        content = "ok"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    captured.append(kwargs["messages"])
                    return FakeResponse()

    monkeypatch.setattr(assistant, "_client", lambda: FakeClient())
    assert asyncio.run(assistant._request("model", "evidence " * 20000)) == "ok"
    assert sum(assistant._estimate_tokens(message["content"]) for message in captured[0]) <= 5500


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

    def run(command, cwd=None):
        calls.append(command)
        if command[:3] == ["git", "apply", "--check"]:
            return __import__("subprocess").CompletedProcess(command, 0, "", "")
        if command[:2] == ["git", "apply"] and "--reverse" not in command:
            return __import__("subprocess").CompletedProcess(command, 0, "", "")
        return __import__("subprocess").CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(assistant, "_run", run)
    monkeypatch.setattr(assistant, "health_check", lambda: {"healthy": False, "details": "startup failed"})
    assert "rolled back" in assistant.apply("rollback-me")
    assert ["git", "apply", "--reverse", str(tmp_path / "logs/ai-proposals/rollback-me.patch")] in calls
