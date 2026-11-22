"""Amount roles exposed by whole-inventory preservation checks."""

from decimal import Decimal

import pytest

from quantfolio.distributions import extract_terms


def amounts(pages):
    return {Decimal(row["value"]) for row in extract_terms(pages).get("cash_per_share", [])}


def test_share_capital_composition_is_not_an_h_share_distribution():
    pages = [
        "二、本次实施的权益分派方案 公司以总股本3,039,065,855股为基数",
        "（其中A股1,811,265,855股，H股1,227,800,000股），"
        "向全体股东每10股派发现金红利人民币39.74元（含税）。",
    ]
    assert amounts(pages) == {Decimal("3.974")}


def test_proposal_within_current_section_does_not_conflict_with_actual_amount():
    pages = [
        "A股每股现金红利0.2692元（含税）。二、分配方案 "
        "根据审议通过的利润分配预案，公司拟向股东每10股派发现金红利人民币2.69元（含税）。"
        "根据调整公告，本次权益分派以扣除回购股份后的股数为基数，"
        "每10股派发现金红利人民币2.692元（含税）。",
    ]
    assert amounts(pages) == {Decimal("0.2692")}


@pytest.mark.parametrize(
    "wording",
    [
        "H股1,000股股东每10股派发现金红利39.74元（含税）。",
        "（其中H股1,000股每10股派发现金红利39.74元（含税））。",
        "公司拟向股东每股现金红利3.974元（含税）。",
        "根据原预案，每10股派发现金红利39.74元（含税）。",
    ],
)
def test_non_current_roles_remain_unsupported(wording):
    assert not amounts(["二、分配方案 " + wording])


@pytest.mark.parametrize(
    "wording",
    [
        "公司拟以实施股权登记日登记的总股本扣减回购股为基数，向全体股东每10股派发现金红利0.2元（含税）。",
        "公司年度利润分配预案为：以现有总股本剔除已回购股份为基数，向全体股东每10股派0.200000元人民币现金（含税）。",
    ],
)
def test_established_all_shareholder_implementation_wording_is_preserved(wording):
    # These are existing implementation-section forms, corroborated by the provider.
    assert amounts(["二、权益分派方案 " + wording]) == {Decimal("0.02")}
