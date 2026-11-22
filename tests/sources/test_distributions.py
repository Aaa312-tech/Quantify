from copy import deepcopy

import pandas as pd
import pytest

from quantfolio.distributions import extract_terms, source_available, verified_terms
from quantfolio.market import gross_entitlement
from quantfolio.snapshots import reviewed_distribution_actions
from quantfolio.storage import digest


def action():
    return {
        "symbol": "sz300033",
        "id": "unchanged",
        "record_date": "2024-03-28",
        "ex_date": "2024-03-29",
        "cash_per_share": 2.2,
        "share_multiplier": 1.0,
        "cash_pay_date": None,
        "cash_available_date": None,
    }


def terms():
    return [
        "二、本次实施的权益分派方案 向全体股东每 10 股派 22.000000 元人民币现金（含税；扣税后每10股19.8元）。",
        "股权登记日为：2024年3月28日；除权除息日为：2024年3月29日。"
        "本公司委托中国结算深圳分公司代派的A股股东现金红利将于2024年3月29日"
        "通过股东托管证券公司（或其他托管机构）直接划入其资金账户。",
    ]


def provider():
    return {**action(), "cash_pay_date": "2024-03-29"}


def test_role_labelled_dates_and_gross_amount():
    evidence = extract_terms(terms())
    facts, status = verified_terms(action(), provider(), evidence)
    assert status == "VERIFIED_FIELDS"
    assert facts == {"cash_pay_date": "2024-03-29"}
    assert evidence["cash_pay_date"][0]["page"] == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda pages: [pages[0], pages[1].replace("2024年3月29日", "2023年3月29日")],
        lambda pages: [pages[0], "2024年3月28日 2024年3月29日"],
        lambda pages: [pages[0].replace("含税", "扣税后"), pages[1]],
        lambda pages: [pages[0] + "向全体股东每10股派23元现金（含税）。", pages[1]],
    ],
)
def test_occurrence_tax_and_conflicting_years_do_not_certify_payment(change):
    assert not verified_terms(action(), provider(), extract_terms(change(terms())))[0]


def test_shanghai_table_column_roles():
    page = (
        "A股每股现金红利人民币2.2元（含税） 股份类别 股权登记日 最后交易日 "
        "除权（息）日 现金红利发放日 Ａ股 2024/3/28 － 2024/3/29 2024/4/1"
    )
    candidate = {**provider(), "cash_pay_date": "2024-04-01"}
    assert verified_terms(action(), candidate, extract_terms([page]))[0] == {
        "cash_pay_date": "2024-04-01"
    }
    swapped = page.replace("除权（息）日 现金红利发放日", "现金红利发放日 除权（息）日")
    assert not verified_terms(action(), candidate, extract_terms([swapped]))[0]


def test_structured_identity_and_rounded_amount_do_not_pass():
    evidence = extract_terms(terms())
    for candidate in (
        {**provider(), "symbol": "sz000001"},
        {**provider(), "cash_per_share": 2.2000001},
    ):
        assert not verified_terms(action(), candidate, evidence)[0]


def test_proposal_amount_cannot_supply_an_unreadable_implementation_amount():
    pages = terms()
    pages[0] = pages[0].replace("二、本次实施的权益分派方案", "一、股东大会审议通过的分配方案")
    pages[0] += "二、本次实施的权益分派方案 本次每股现金金额见另表。"
    assert not verified_terms(action(), provider(), extract_terms(pages))[0]


@pytest.mark.parametrize(
    "title,date",
    [
        ("权益分派实施更正公告", "2024-03-22"),
        ("利润分配预案", "2024-03-22"),
        ("权益分派实施公告", "2024-03-29"),
    ],
)
def test_amended_proposed_and_same_day_catalogues_need_separate_review(title, date):
    assert source_available({"title": title, "catalog_date": date}, action()) is None


def test_date_verification_does_not_certify_share_listing_or_rounding():
    pages = terms() + ["每10股分红比例保留六位小数，最后一位直接截取，不四舍五入。"]
    fields, _ = verified_terms(
        action(), {**provider(), "share_list_date": "2024-03-29"}, extract_terms(pages)
    )
    assert set(fields) == {"cash_pay_date"}


def fact(original, fields):
    return {
        "symbol": original["symbol"],
        "ex_date": original["ex_date"],
        "original_action_sha256": digest(original),
        "verified_fields": fields,
        "source": {},
        "method": "MANUAL_PRIMARY_REVIEW",
        "evidence_pages": [1],
        "known_at": "2024-03-23T00:00:00+08:00",
    }


def test_repair_uses_aggregate_entitlement_and_next_observed_opening():
    original = action()
    calendar = pd.bdate_range("2024-03-28", "2024-04-03")
    fixed, changes = reviewed_distribution_actions(
        [original],
        calendar,
        [fact(original, {"cash_per_share": "3.2", "cash_pay_date": "2024-03-29"})],
    )
    assert original == action()
    assert fixed[0]["cash_available_date"] == "2024-04-01"
    assert fixed[0]["id"] == original["id"]
    assert fixed[0]["cash_per_share"] == 3.2
    assert changes[0]["fields"]["cash_per_share"] == {"before": 2.2, "after": 3.2}


def test_review_binds_every_original_field_and_rejects_unregistered_changes():
    original = action()
    review = fact(original, {"cash_pay_date": "2024-03-29"})
    altered = deepcopy(original)
    altered["record_date"] = "2024-03-27"
    with pytest.raises(ValueError, match="identity"):
        reviewed_distribution_actions([altered], pd.bdate_range("2024-03-28", periods=3), [review])
    with pytest.raises(ValueError, match="Unregistered"):
        reviewed_distribution_actions(
            [original],
            pd.bdate_range("2024-03-28", periods=3),
            [fact(original, {"share_multiplier": 2})],
        )


def test_continuous_wealth_labels_do_not_round_to_account_cents():
    event = {**action(), "cash_per_share": 0.9907442, "cash_rounding": "FLOOR_TO_CENT"}
    wealth, _, _ = gross_entitlement(
        pd.Timestamp("2024-03-28"), pd.Timestamp("2024-03-29"), 10.0, [event]
    )
    assert wealth == pytest.approx(10.9907442, abs=1e-12)
