from app.exchanges.ccxt_adapter import CcxtExchangeAdapter


def test_ccxt_adapter_exposes_bulk_fee_loader():
    assert hasattr(CcxtExchangeAdapter, "get_taker_fees")


def test_ccxt_adapter_has_bounded_timeout_configuration_source():
    # Keep this as a source-level regression test: the adapter must define a
    # finite request timeout so one exchange cannot stall a complete scan.
    import inspect

    source = inspect.getsource(CcxtExchangeAdapter.__init__)
    assert '"timeout": 15000' in source
    assert '"maxRetriesOnFailure": 2' in source
