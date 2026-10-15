import pytest
from arbitrage_terminal.ai import AIAssistant
from arbitrage_terminal.domain.models import AIMode
@pytest.mark.asyncio
async def test_ai_off():
    ai=AIAssistant('https://example.invalid','key','model');assert await ai.analyze(AIMode.OFF,'x',{}) is None
