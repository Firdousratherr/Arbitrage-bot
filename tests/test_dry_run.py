from pathlib import Path
def test_dry_run_has_no_trade_execution_module():
    assert not list(Path('src/arbitrage_terminal').rglob('*trade*.py'))
