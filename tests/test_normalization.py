import pytest
from arbitrage_terminal.domain.normalization import normalize_symbol
def test_symbol_forms():
    assert normalize_symbol('BTC/USDT')[0]=='BTC/USDT';assert normalize_symbol('BTC-USDT')[0]=='BTC/USDT';assert normalize_symbol('BTC_USDT')[0]=='BTC/USDT';assert normalize_symbol('BTCUSDT')[0]=='BTC/USDT'
def test_invalid():
    with pytest.raises(ValueError):normalize_symbol('UNKNOWN')
