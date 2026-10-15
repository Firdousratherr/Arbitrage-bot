from types import SimpleNamespace

import pytest

from arbitrage_terminal.bot.code_repair import CodeRepairManager


def manager():
    return CodeRepairManager(SimpleNamespace(configured=True), SimpleNamespace())


def test_repair_validation_accepts_valid_python():
    manager().validate({
        'files': [
            {'path': 'src/arbitrage_terminal/example.py', 'content': 'def fixed():\n    return True\n'},
            {'path': 'tests/test_example.py', 'content': 'def test_fixed():\n    assert True\n'},
        ]
    })


def test_repair_validation_rejects_workflow_paths():
    with pytest.raises(RuntimeError, match='unsafe path'):
        manager().validate({'files': [{'path': '.github/workflows/test.yml', 'content': 'name: x'}]})


def test_repair_validation_rejects_invalid_python():
    with pytest.raises(RuntimeError, match='invalid Python'):
        manager().validate({'files': [{'path': 'src/arbitrage_terminal/broken.py', 'content': 'def broken(:\n'}]})
