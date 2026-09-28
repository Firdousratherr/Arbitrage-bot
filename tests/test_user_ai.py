import pytest

from app.user_ai import UserAIAssistant


class FakeMaintenance:
    configured = True

    def __init__(self):
        self.last_prompt = ""
        self.proposals = {}

    def repository_context(self, query):
        return "safe repository evidence"

    async def ask_user(self, prompt):
        self.last_prompt = prompt
        return "Use the scanner settings shown in /filters."

    async def propose_fix(self, issue):
        self.proposals["abc123"] = {
            "diagnosis": "Scanner filter configuration needs review.",
            "root_cause": "A filter value is excluding the opportunity.",
            "confidence": 0.9,
            "affected_files": ["app/filters.py"],
            "status": "validated",
            "validation": "git apply: passed; Python syntax: passed; Tests: passed",
        }
        return "abc123", "internal"

    def load_proposal(self, proposal_id):
        return self.proposals.get(proposal_id)


@pytest.mark.asyncio
async def test_normal_user_ai_chat_is_safe():
    assistant = UserAIAssistant(FakeMaintenance())
    result = await assistant.chat("Why did my scan return no opportunities?")
    assert "scanner settings" in result
    assert ".env" in assistant.maintenance.last_prompt
    assert "NEVER reveal API keys" in assistant.maintenance.last_prompt


@pytest.mark.asyncio
async def test_normal_user_ai_fix_returns_proposal_without_patch():
    assistant = UserAIAssistant(FakeMaintenance())
    proposal_id, result = await assistant.propose_user_fix("My scan is not finding opportunities.")
    assert proposal_id == "abc123"
    assert "AI FIX PROPOSAL" in result
    assert "passed isolated patch" in result
    assert "not been applied or deployed" in result
    assert "app/filters.py" in result
    assert "patch" not in result.lower()


def test_user_ai_sanitizes_secret_like_output():
    assistant = UserAIAssistant(FakeMaintenance())
    assert assistant._sanitize("api_key=SUPERSECRET /app/.env") == "api_key=[REDACTED] [REDACTED_PATH]"
