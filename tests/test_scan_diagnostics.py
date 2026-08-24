from app.scan_diagnostics import get_last_scan_diagnostics, set_last_scan_diagnostics
from app.ui import format_scan_count


def test_scan_diagnostics_show_symbol_and_exchange_gap():
    set_last_scan_diagnostics([
        {
            "symbol": "WKC/USDT",
            "gaps": {
                "lbank": "missing/zero bid-ask",
                "xt": "fetch failed: ExchangeNotAvailable: timeout",
            },
        }
    ])

    message = format_scan_count(0)

    assert "WKC/USDT" in message
    assert "lbank" in message
    assert "missing/zero bid-ask" in message
    assert "xt" in message
    assert "fetch failed" in message

    set_last_scan_diagnostics([])


def test_scan_diagnostics_are_bounded():
    set_last_scan_diagnostics([
        {"symbol": f"COIN{i}/USDT", "gaps": {"lbank": "missing/zero bid-ask"}}
        for i in range(20)
    ])

    message = format_scan_count(2)

    assert "Found 2 opportunities" in message
    assert "… and 12 more symbols with data gaps" in message

    set_last_scan_diagnostics([])
