from arbitrage_terminal.bot.commands import dashboard_cmd, diagnostics_cmd, filters_cmd, help_cmd, settings_cmd, status_cmd
from arbitrage_terminal.domain.filters import ScanFilters


def test_command_sections_import():
    assert all(callable(x) for x in (dashboard_cmd, status_cmd, diagnostics_cmd, filters_cmd, settings_cmd, help_cmd))


def test_filter_inputs_are_normalized():
    f = ScanFilters(selected_coins={'btc', 'Eth'}, quote_currency='usdt', validation_mode='LOOSE')
    assert f.selected_coins == {'BTC', 'ETH'}
    assert f.quote_currency == 'USDT'
    assert f.validation_mode == 'loose'
