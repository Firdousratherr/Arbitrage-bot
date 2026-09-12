import pytest

from arbitrage_terminal.ai.recovery import ExchangeRecoveryAdvisor


class FakeAI:
    configured = True

    def __init__(self, response):
        self.response = response

    async def advise_exchange_recovery(self, snapshot):
        return self.response


@pytest.mark.asyncio
async def test_disabled_advisor_never_calls_ai():
    class NeverAI(FakeAI):
        async def advise_exchange_recovery(self, snapshot):
            raise AssertionError('AI must not be called')

    advisor = ExchangeRecoveryAdvisor(NeverAI({}), enabled=False)
    assert await advisor.advise({'exchange': 'x'}) is None


@pytest.mark.asyncio
async def test_valid_safe_repair_decision_is_accepted():
    advisor = ExchangeRecoveryAdvisor(FakeAI({
        'classification': 'ccxt_client',
        'confidence': 0.95,
        'recommended_action': 'repair',
        'safe_to_auto_repair': True,
        'reason': 'Client recreation is appropriate after deterministic repair failed',
        'retry_delay_seconds': 0,
    }), enabled=True)
    decision = await advisor.advise({'exchange': 'x', 'error_type': 'network'})
    assert decision.recommended_action == 'repair'
    assert decision.safe_to_auto_repair is True


@pytest.mark.asyncio
async def test_authentication_decision_cannot_be_auto_repaired():
    advisor = ExchangeRecoveryAdvisor(FakeAI({
        'classification': 'authentication',
        'confidence': 0.99,
        'recommended_action': 'repair',
        'safe_to_auto_repair': False,
        'reason': 'Credentials may be invalid',
        'retry_delay_seconds': 0,
    }), enabled=True)
    decision = await advisor.advise({'exchange': 'x', 'error_type': 'authentication'})
    assert decision.safe_to_auto_repair is False


@pytest.mark.asyncio
async def test_malformed_ai_response_is_rejected():
    advisor = ExchangeRecoveryAdvisor(FakeAI({'recommended_action': 'execute_trade'}), enabled=True)
    assert await advisor.advise({'exchange': 'x'}) is None


@pytest.mark.asyncio
async def test_low_confidence_decision_is_rejected():
    advisor = ExchangeRecoveryAdvisor(FakeAI({
        'classification': 'unknown', 'confidence': 0.4,
        'recommended_action': 'retry', 'safe_to_auto_repair': True,
        'reason': 'uncertain', 'retry_delay_seconds': 0,
    }), enabled=True, min_confidence=0.8)
    assert await advisor.advise({'exchange': 'x'}) is None
