from arbitrage_terminal.bot.live_scan import _progress_percent, _progress_bar


def test_exchange_progress_is_derived_from_completed_exchanges():
    assert _progress_percent('exchange', {'completed': 0, 'total': 15}, 15) == 10
    assert _progress_percent('exchange', {'completed': 7, 'total': 15}, 15) == 26
    assert _progress_percent('exchange', {'completed': 15, 'total': 15}, 15) == 45


def test_progress_mapping_uses_stage_not_human_readable_label():
    assert _progress_percent('markets', {}, 15) == 50
    assert _progress_percent('fees', {}, 15) == 65
    assert _progress_percent('candidates', {}, 15) == 80
    assert _progress_percent('complete', {}, 15) == 100


def test_progress_bar_is_bounded():
    assert len(_progress_bar(0)) == 10
    assert len(_progress_bar(50)) == 10
    assert len(_progress_bar(100)) == 10
