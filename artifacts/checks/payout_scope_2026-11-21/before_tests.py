"""Current implementation clauses and financial sentences split across PDF pages."""

from decimal import Decimal

import pytest

from quantfolio.distributions import extract_terms


def amounts(pages):
    return {Decimal(row["value"]) for row in extract_terms(pages).get("cash_per_share", [])}


@pytest.mark.parametrize(
    "heading",
    [
        "二、本次实施的 2023 年度权益分派方案",
        "二、本次实施的利润分派方案",
        "二、本公司2024年度中期分红派息方案",
        "2、2023 年度 A 股权益分派方案为：",
        "2.2023 年度 A 股权益分派方案为：",
    ],
)
def test_current_implementation_heading_variants(heading):
    assert amounts([heading + "每10股派发现金红利3元（含税）。"]) == {Decimal("0.3")}


def test_actual_scheme_under_approval_heading_does_not_accept_old_proposal():
    text = (
        "一、股东大会审议通过的利润分配方案 "
        "1、股东大会审议通过的方案为：向全体股东每10股派发现金20元（含税）。"
        "3、公司实施的 2023 年度利润分配方案：以现有可参与分配股份为基数，"
        "向全体股东每10股派发现金30元（含税）。"
    )
    assert amounts([text]) == {Decimal("3")}


@pytest.mark.parametrize("role", ["拟实施的", "曾实施的", "已实施的", "将实施的"])
def test_approval_history_or_future_intent_is_not_a_current_clause(role):
    text = (
        "一、股东大会审议通过的利润分配方案 "
        f"公司{role}2023年度利润分配方案：每10股派发现金30元（含税）。"
    )
    assert not amounts([text])


def test_approved_proposal_reference_before_explicit_current_implementation():
    text = (
        "二、分配方案 根据公司2023年年度股东大会审议通过的《2023年利润分配预案》"
        "及公司回购专户持股情况，公司本次利润分配以扣除回购股份后的股数为基数，"
        "每股派发现金红利0.22元（含税）。"
    )
    assert amounts([text]) == {Decimal("0.22")}


@pytest.mark.parametrize("role", ["拟", "前次", "全年累计", "H股股东", "按除权参考折算"])
def test_new_heading_retains_non_current_amount_exclusions(role):
    assert not amounts(["二、本次实施的利润分派方案 " + role + "每10股派发现金红利3元（含税）。"])


@pytest.mark.parametrize("qualifier", ["税后", "扣税后", "不含税", ""])
def test_new_scope_never_supplies_a_missing_gross_qualifier(qualifier):
    assert not amounts(["二、本次实施的利润分派方案 每10股派发现金红利3元（" + qualifier + "）。"])


@pytest.mark.parametrize("location", ["header", "footer", "none"])
@pytest.mark.parametrize("subject, expected", [("每股", "0.22"), ("每10股", "0.022")])
def test_adjacent_sentence_keeps_both_pages_and_counter_provenance(location, subject, expected):
    left = "二、分配方案 公司本次利润分配以可分配股本为基数，" + subject
    right = "派发现金红利0.22元（含税），不涉及送股。"
    if location == "header":
        pages = ["- 1 -\n" + left, "- 2 -\n" + right]
    elif location == "footer":
        pages = [left + "\n- 1 -", right + "\n- 2 -"]
    else:
        pages = [left, right]
    terms = extract_terms(pages).get("cash_per_share", [])
    assert {Decimal(row["value"]) for row in terms} == {Decimal(expected)}
    spans = [row for row in terms if row.get("pages") == [1, 2]]
    assert len(spans) == 1
    assert spans[0]["page"] == 1
    assert [part["page"] for part in spans[0]["parts"]] == [1, 2]
    assert [part["text"] for part in spans[0]["parts"]] == [subject, "派发现金红利0.22元(含税"]
    assert len(spans[0]["page_number_lines"]) == (0 if location == "none" else 2)


@pytest.mark.parametrize(
    "left, right",
    [
        ("- 7 -\n二、分配方案 每股", "- 2 -\n派发现金红利0.22元（含税）"),
        ("- 1 -\n二、分配方案 每股", "- 9 -\n派发现金红利0.22元（含税）"),
        ("二、分配方案 每股", "- 2 -\n派发现金红利0.22元（含税）"),
        ("- 1 -\n二、分配方案 每股", "派发现金红利0.22元（含税）"),
        ("二、分配方案 每股\n1", "2\n派发现金红利0.22元（含税）"),
        ("二、分配方案 每股\n- 1 -\n其他说明", "- 2 -\n派发现金红利0.22元（含税）"),
        ("二、分配方案 每股", "三、历史方案 派发现金红利0.22元（含税）"),
        ("二、分配方案 每股", "公司简称\n派发现金红利0.22元（含税）"),
        ("二、分配方案 每股", "派发现金红利0.22元（税后）"),
        ("一、股东大会审议情况 每股", "派发现金红利0.22元（含税）"),
        ("二、分配方案 前次每股", "派发现金红利0.22元（含税）"),
        ("二、分配方案 每股派发现金红利0.", "22元（含税）"),
    ],
)
def test_page_boundary_cannot_hide_amounts_or_change_roles(left, right):
    assert not amounts([left, right])


def test_page_numbers_follow_physical_pdf_pages_not_the_fixture_length():
    pages = ["封面", "- 2 -\n二、分配方案 每股", "- 3 -\n派发现金红利0.22元（含税）"]
    terms = extract_terms(pages).get("cash_per_share", [])
    assert {Decimal(row["value"]) for row in terms} == {Decimal("0.22")}
    assert terms[0]["pages"] == [2, 3]
