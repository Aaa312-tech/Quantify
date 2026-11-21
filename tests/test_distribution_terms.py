"""Real layout omissions reproduced before changing the dividend extractor."""

import pytest

from quantfolio.distributions import extract_terms, verified_terms


def record():
    return {
        "symbol": "sh600001",
        "record_date": "2024-03-28",
        "ex_date": "2024-03-29",
        "cash_per_share": 2.2,
        "share_multiplier": 1.0,
        "cash_pay_date": None,
    }


DATES = (
    "股权登记日：2024年3月28日；除权除息日：2024年3月29日。"
    "本公司委托中国结算深圳分公司代派的A股股东现金红利将于2024年3月29日"
    "通过股东托管证券公司直接划入其资金账户。"
)


@pytest.mark.parametrize(
    "wording",
    [
        "二、本次实施的利润分配及资本公积金转增股本方案 向全体股东每10股派22元人民币现金（含税）。",
        "二、本次实施的权益分派方案 向全体股东每10股派发现金分红人民币22元（含税）。",
        "二、分配方案 以实施前总股本为基数，每10股派发现金红利22元（含税）。",
    ],
)
def test_current_implementation_amount_layout(wording):
    original = record()
    fields, status = verified_terms(
        original, {**original, "cash_pay_date": "2024-03-29"}, extract_terms([wording, DATES])
    )
    assert status == "VERIFIED_FIELDS"
    assert fields == {"cash_pay_date": "2024-03-29"}


def test_bank_final_dividend_is_separate_from_prior_and_annual_totals():
    original = {**record(), "cash_per_share": 0.13}
    pages = [
        "重要内容提示：A股每股派发2025年末期现金红利人民币0.13元（含税）。",
        "二、分配方案 本次利润分配每股派发2025年末期现金红利人民币0.13元（含税）。"
        "本行已派发2025年中期现金红利每股人民币0.1195元（含税），全年为每股0.2495元（含税）。",
        DATES,
    ]
    fields, status = verified_terms(
        original, {**original, "cash_pay_date": "2024-03-29"}, extract_terms(pages)
    )
    assert status == "VERIFIED_FIELDS" and fields == {"cash_pay_date": "2024-03-29"}


@pytest.mark.parametrize("listing", [False, True])
def test_single_class_date_table_keeps_column_roles(listing):
    original = {**record(), "share_multiplier": 1.4 if listing else 1.0}
    candidate = {
        **original,
        "cash_pay_date": "2024-04-01",
        "share_list_date": "2024-04-02" if listing else None,
    }
    extra = "新增无限售条件流通股份上市日 " if listing else ""
    values = "2024/4/2 " if listing else ""
    page = (
        "二、分配方案 本次利润分配每股派发现金红利2.2元（含税）。"
        "三、相关日期 股权登记日 除权（息）日 " + extra + "现金红利发放日 "
        "2024/3/28 2024/3/29 " + values + "2024/4/1"
    )
    fields, status = verified_terms(original, candidate, extract_terms([page]))
    expected = {"cash_pay_date": "2024-04-01"}
    if listing:
        expected["share_list_date"] = "2024-04-02"
    assert status == "VERIFIED_FIELDS" and fields == expected


def test_line_wrapped_year_in_labelled_payment():
    original = record()
    pages = [
        "二、权益分派方案 向全体股东每10股派22元人民币现金（含税）。",
        DATES.replace("将于2024", "将于202\n4"),
    ]
    fields, status = verified_terms(
        original, {**original, "cash_pay_date": "2024-03-29"}, extract_terms(pages)
    )
    assert status == "VERIFIED_FIELDS" and fields == {"cash_pay_date": "2024-03-29"}


@pytest.mark.parametrize(
    "wording",
    [
        "一、股东大会审议通过利润分配及资本公积金转增股本方案 向全体股东每10股派22元现金（含税）。",
        "二、分配方案 本行已派发每股现金红利2.2元（含税）。",
        "二、分配方案 全年累计每10股派现金22元（含税）。",
        "二、分配方案 H股每股现金红利2.2元（含税）。",
        "二、分配方案 H股每10股派22元现金（含税）。",
        "二、分配方案 每股现金红利2.2元（税后）。",
        "二、分配方案 每10股派22元现金（扣税后）。",
        "三、调整相关参数 向全体股东每10股派22元现金（含税）。",
        "二、分配方案 除权价格折算每股现金红利2.2元（含税）。",
    ],
)
def test_other_amount_roles_do_not_supply_current_gross_dividend(wording):
    original = record()
    fields, _ = verified_terms(
        original,
        {**original, "cash_pay_date": "2024-03-29"},
        extract_terms([wording, DATES]),
    )
    assert not fields


@pytest.mark.parametrize(
    "layout", ["swapped", "truncated", "extra_date", "extra_after_long_day", "unlabelled"]
)
def test_unknown_table_layouts_do_not_infer_column_roles(layout):
    text = (
        "二、分配方案 每股现金红利2.2元（含税）。"
        "三、相关日期 股权登记日 除权（息）日 现金红利发放日 "
        "2024/3/28 2024/3/29 2024/4/1"
    )
    if layout == "swapped":
        text = text.replace("除权（息）日 现金红利发放日", "现金红利发放日 除权（息）日")
    elif layout == "truncated":
        text = text.removesuffix(" 2024/4/1")
    elif layout == "extra_date":
        text += " 2024/4/2"
    elif layout == "extra_after_long_day":
        text = text.removesuffix("2024/4/1") + "2024/4/12 2024/4/13"
    else:
        text = text.replace("股权登记日 除权（息）日 现金红利发放日", "日期")
    original = record()
    fields, _ = verified_terms(
        original, {**original, "cash_pay_date": "2024-04-01"}, extract_terms([text])
    )
    assert not fields


def test_new_current_amounts_must_agree():
    original = record()
    pages = [
        "二、分配方案 每股现金红利2.2元（含税）；每股现金红利2.3元（含税）。",
        DATES,
    ]
    fields, _ = verified_terms(
        original, {**original, "cash_pay_date": "2024-03-29"}, extract_terms(pages)
    )
    assert not fields


def test_split_year_does_not_join_unlabelled_numeric_cells():
    original = record()
    pages = ["二、权益分派方案 每10股派22元人民币现金（含税）。", "202 4 3 28 202 4 3 29"]
    fields, _ = verified_terms(
        original, {**original, "cash_pay_date": "2024-03-29"}, extract_terms(pages)
    )
    assert not fields
