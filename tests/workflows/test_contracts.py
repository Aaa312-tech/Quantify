"""Financial schemas and the original-principal boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from quantfolio.accounts import new_account, restore_account, snapshot_account


def test_snapshot_keeps_principal_and_rejects_a_different_ledger():
    account = json.loads(json.dumps(new_account("2026-11-20", "contract_check")))
    book, risk = restore_account(account, {})
    book.cash -= 12345
    carried = snapshot_account(book, account, "2026-11-20", risk, 0, None)
    assert carried["initial_cash"] == account["initial_cash"] == 1_000_000.0
    assert carried["cash"] == 999876.55
    assert type(book.cash) is int
    assert list(carried) == list(account)
    book.initial -= 1
    with pytest.raises(ValueError, match="Ledger principal differs"):
        snapshot_account(book, account, "2026-11-20", risk, 0, None)


def test_type_checker_rejects_invalid_financial_fields(tmp_path):
    source = """
from quantfolio.accounts import AccountSnapshot
from quantfolio.allocation import TargetWeights
from quantfolio.ledger import Ledger, Trade
from quantfolio.models import Forecast
from quantfolio.planning import OpeningQuote

def invalid(f: Forecast, q: OpeningQuote, w: TargetWeights, a: AccountSnapshot, t: Trade) -> None:
    f['mu'] = '0.03'
    q['open'] = '10.00'
    w['sh600000'] = '0.15'
    a['cash'] = '100.00'
    t['quantity'] = 1.5
    Ledger({}).cash = 0.01
"""
    fixture = tmp_path / "invalid_contracts.py"
    fixture.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            "pyproject.toml",
            "--cache-dir",
            str(tmp_path / "mypy"),
            str(fixture),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("error:") == 6, result.stdout
    assert "Found 6 errors in 1 file" in result.stdout
