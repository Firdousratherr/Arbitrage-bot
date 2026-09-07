from arbitrage_terminal.bot.ai_fixer import _safe_path, _validate_patch


def test_ai_fix_blocks_sensitive_paths():
    assert _safe_path('src/arbitrage_terminal/main.py')
    assert not _safe_path('.env')
    assert not _safe_path('src/arbitrage_terminal/secret.py')
    assert not _safe_path('.github/workflows/deploy.yml')


def test_ai_fix_validates_python_before_apply():
    ok, errors = _validate_patch({
        'summary': 'test',
        'root_cause': 'test',
        'risk': 'low',
        'files': [{'path': 'src/arbitrage_terminal/example.py', 'content': 'def ok():\n    return 1\n'}],
        'tests': ['pytest -q'],
    })
    assert ok
    assert errors == []


def test_ai_fix_rejects_syntax_error():
    ok, errors = _validate_patch({
        'files': [{'path': 'src/arbitrage_terminal/example.py', 'content': 'def broken(:\n'}]
    })
    assert not ok
    assert any('syntax error' in error for error in errors)
