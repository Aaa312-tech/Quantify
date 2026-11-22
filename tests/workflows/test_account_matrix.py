"""Account-matrix boundaries: repeated policies, cost order and failed audits."""

import pytest

from quantfolio import evaluation
from tests.factories import market_bundle


def test_duplicate_policies_keep_their_account_count(tmp_path):
    bundle, forecasts = market_bundle()
    result = evaluation.evaluate_accounts(
        bundle,
        {"momentum": forecasts},
        "baseline",
        9,
        18,
        [20, 10, 20],
        ["matched_momentum", "matched_momentum"],
        tmp_path / "evaluation",
    )
    assert list(result["results"]) == ["10bps", "20bps"]
    assert result["accounts"] == 8
    assert len(result["artifacts"]) == 4
    assert all((tmp_path / "evaluation" / relative).is_file() for relative in result["artifacts"])


@pytest.mark.parametrize("audit", ["reconcile", "execution_audit"])
def test_failed_account_audit_publishes_no_ledger(tmp_path, monkeypatch, audit):
    bundle, forecasts = market_bundle()
    monkeypatch.setattr(
        evaluation, audit, lambda *args: {"status": "FAIL", "checks": 0, "errors": ["injected"]}
    )
    output = tmp_path / "evaluation"
    with pytest.raises(ValueError, match="Account audit failed: 10bps/matched_momentum"):
        evaluation.evaluate_accounts(
            bundle, {"momentum": forecasts}, "baseline", 9, 18, [10], ["matched_momentum"], output
        )
    assert not output.exists()
