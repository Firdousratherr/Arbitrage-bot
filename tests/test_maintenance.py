import json
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


def test_provider_http_403_is_provider_error(monkeypatch):
    assistant = MaintenanceAssistant("https://provider.test", "key", "primary", "fallback")

    def failing(_model, _prompt):
        raise MaintenanceError("AI provider rejected the request (HTTP 403).")

    monkeypatch.setattr(assistant, "_request", failing)
    with pytest.raises(MaintenanceError, match="provider rejected"):
        __import__("asyncio").run(assistant._ask("prompt"))


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
