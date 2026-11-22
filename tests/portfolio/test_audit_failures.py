"""Keep independent audit diagnostics and check counts stable across decomposition."""

from copy import deepcopy

import pytest

from quantfolio.simulation import simulate
from quantfolio.validation import execution_audit, reconcile
from tests.factories import market_bundle


@pytest.fixture
def account():
    bundle, forecasts = market_bundle()
    summary, ledger = simulate(bundle, 6, 8, forecasts)
    return bundle, summary, ledger


def test_valid_account_retains_all_independent_checks(account):
    bundle, summary, ledger = account
    assert reconcile(ledger, summary, bundle["meta"], []) == {
        "status": "PASS",
        "checks": 115,
        "errors": [],
    }
    assert execution_audit(ledger, bundle) == {"status": "PASS", "checks": 133, "errors": []}


@pytest.mark.parametrize(
    "field,errors",
    [
        ("fee", ["2026-01-13 fees"]),
        ("cash_after", ["2026-01-13 cash after fill"]),
        ("nav", ["2026-01-14 NAV identity", "2026-01-15 prior NAV"]),
        ("quantity", ["2026-01-14 position sh600001"]),
    ],
)
def test_reconciliation_preserves_failure_order_and_context(account, field, errors):
    bundle, summary, ledger = account
    changed = deepcopy(ledger)
    if field in ("fee", "cash_after"):
        changed["trades"][0][field] += 0.01
    elif field == "nav":
        changed["curve"][1]["nav"] += 0.01
    else:
        changed["curve"][1]["positions"]["sh600001"]["quantity"] += 100
    assert reconcile(changed, summary, bundle["meta"], []) == {
        "status": "FAIL",
        "checks": 115,
        "errors": errors,
    }


@pytest.mark.parametrize(
    "field,value,checks,code",
    [("peak", 999999.99, 133, "PEAK_RESET"), ("phase", "UNKNOWN", 25, "UNKNOWN_RISK_PHASE")],
)
def test_risk_audit_preserves_early_exit_and_error_context(account, field, value, checks, code):
    bundle, _, ledger = account
    changed = deepcopy(ledger)
    changed["decisions"][1]["recovery"][field] = value
    assert execution_audit(changed, bundle) == {
        "status": "FAIL",
        "checks": checks,
        "errors": [[code, "2026-01-14"]],
    }
