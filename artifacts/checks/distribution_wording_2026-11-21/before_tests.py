"""Gross-cash forms observed in the frozen unsupported source inventory."""

from decimal import Decimal

import pytest

from quantfolio.distributions import extract_terms


def amounts(text):
    evidence = extract_terms([text])
    return {Decimal(row["value"]) for row in evidence.get("cash_per_share", [])}


@pytest.mark.parametrize(
    "heading",
    [
        "权益分配方案",
        "本次实施的权益分配方案",
        "本次实施的2023年度利润分配方案",
        "本次实施的2025年半年度权益分派方案",
        "2025年中期分红派息方案",
    ],
)
def test_explicit_current_scheme_headings(heading):
    assert amounts(f"二、{heading} 向全体股东每10股派3.15元人民币现金（含税）。") == {
        Decimal("0.315")
    }


@pytest.mark.parametrize("quote", ['“元”', '"元"'])
def test_currency_definition_between_amount_and_gross_qualifier(quote):
    text = (
        "二、本次实施的权益分派方案 向全体股东每10股派3.15元人民币"
        f"（以下简称{quote}）现金（含税；扣税后每10股派2.835元）。"
    )
    assert amounts(text) == {Decimal("0.315")}


@pytest.mark.parametrize(
    "wording, expected",
    [
        ("每10股派送现金红利0.4873元（含税）", "0.04873"),
        ("每10股分配现金0.26元（含税）", "0.026"),
        ("每10股派发现金股息3.064元（含税）", "0.3064"),
        ("每股派发现金股息人民币0.3064元（含税）", "0.3064"),
        ("每股现金股利人民币0.3064元（含税）", "0.3064"),
    ],
)
def test_gross_cash_nouns_and_verbs(wording, expected):
    assert amounts("二、分配方案 公司以现有股本为基数，" + wording) == {Decimal(expected)}


@pytest.mark.parametrize(
    "wording",
    [
        "向全体股东每10股派3.15元人民币（以下简称美元）现金（含税）",
        "向全体股东每10股派3.15元人民币（调整前金额）现金（含税）",
        "向全体股东每10股派3.15元人民币（以下简称“元”）现金（税后）",
        "向全体股东每10股派3.15元人民币（以下简称“元”）现金",
        "H股股东每10股派送现金红利3.15元（含税）",
        "前次每10股分配现金3.15元（含税）",
        "全年累计每10股派送现金红利3.15元（含税）",
        "按除权参考折算每10股分配现金3.15元（含税）",
        "根据原预案，公司拟每10股分配现金3.15元（含税）",
        "公司拟每股现金股息3.15元（含税）",
    ],
)
def test_new_wording_does_not_bypass_scope_or_gross_qualification(wording):
    assert not amounts("二、权益分配方案 " + wording)


@pytest.mark.parametrize("heading", ["股东大会审议情况", "2024年度利润分配预案", "除息参考价"])
def test_wrong_sections_remain_unsupported(heading):
    assert not amounts(f"二、{heading} 向全体股东每10股派送现金红利3.15元（含税）")
