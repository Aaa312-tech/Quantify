"""Source-backed table, adjacent-page and catalogue-title regressions."""

from decimal import Decimal

import pytest

from quantfolio.distributions import extract_terms, notice_role, resolve_companion_notices


def values(pages, field):
    result = {row["value"] for row in extract_terms(pages).get(field, [])}
    return {Decimal(value) for value in result} if field == "cash_per_share" else result


TABLE = (
    "股份类别 股权登记日 最后交易日 除权（息）日 "
    "新增无限售条件流通股份上市日 现金红利发放日 "
    "Ａ股 2024/5/28 － 2024/5/29 2024/5/30 2024/6/3"
)


def test_last_trading_column_does_not_shift_listing_or_payment():
    expected = {
        "record_date": "2024-05-28",
        "ex_date": "2024-05-29",
        "share_list_date": "2024-05-30",
        "cash_pay_date": "2024-06-03",
    }
    for field, value in expected.items():
        assert values([TABLE], field) == {value}


@pytest.mark.parametrize(
    "table",
    [
        TABLE.replace("Ａ股", "B股"),
        TABLE.replace("－", "2024/5/27"),
        TABLE.replace("最后交易日", "其他日期"),
        TABLE.replace("2024/5/30", ""),
        TABLE + " 2024/6/4",
        TABLE.replace("新增无限售条件流通股份上市日", "现金红利发放日"),
    ],
)
def test_incomplete_or_differently_labelled_table_is_not_reordered(table):
    assert not values([table], "share_list_date")
    assert not values([table], "cash_pay_date")


@pytest.mark.parametrize("location", ["header", "footer"])
@pytest.mark.parametrize("style", ["第 {n} 页 共 2 页", "{n} / 2", "- {n} -"])
def test_split_payment_keeps_both_pages_and_recognized_counters(style, location):
    bodies = [
        "五、权益分派方法 本公司委托中国结算深圳分公司代派的A股股东现金红利",
        "将于2024年4月29日通过股东托管证券公司直接划入其资金账户。",
    ]
    pages = [
        style.format(n=i) + "\n" + body if location == "header" else body + "\n" + style.format(n=i)
        for i, body in enumerate(bodies, 1)
    ]
    assert values(pages, "cash_pay_date") == {"2024-04-29"}
    matches = extract_terms(pages)["cash_pay_date"]
    assert len(matches) == 1
    assert matches[0]["pages"] == [1, 2]
    assert [part["page"] for part in matches[0]["parts"]] == [1, 2]
    assert len(matches[0]["page_number_lines"]) == 2


def test_cash_qualifier_and_currency_can_continue_on_adjacent_page():
    pages = [
        "一、审议情况 每10股派发现金9元（含税）。二、权益分派方案 "
        "本公司本次方案为：向全体股东每10股派1.00元人民币",
        "现金（含税；扣税后，每10股派0.90元）。",
    ]
    assert values(pages, "cash_per_share") == {Decimal("0.1")}
    match = extract_terms(pages)["cash_per_share"][0]
    assert match["pages"] == [1, 2]
    assert match["parts"][0]["text"].endswith("元人民币")
    assert match["parts"][1]["text"] == "现金(含税"


def test_cash_word_boundary_with_fraction_counters():
    pages = [
        "1 / 2\n二、分配方案 本次利润分配以现有股本为基数，每股派发现金",
        "2 / 2\n红利0.008元（含税）。",
    ]
    assert values(pages, "cash_per_share") == {Decimal("0.008")}


def test_ex_date_continues_on_adjacent_physical_page():
    pages = [
        "封面",
        "三、股权登记日与除权除息日 本次股权登记日为2024年5月29日，除权除息日为：2024年",
        "5月30日。四、权益分派对象",
        "其他事项",
    ]
    assert values(pages, "record_date") == {"2024-05-29"}
    assert values(pages, "ex_date") == {"2024-05-30"}
    assert extract_terms(pages)["ex_date"][0]["pages"] == [2, 3]


@pytest.mark.parametrize(
    "left,right",
    [
        ("第1页共3页\n每10股派1.00元人民币", "第2页共3页\n现金（含税）"),
        ("第1页共2页\n每10股派1.00元人民币", "2 / 2\n现金（含税）"),
        ("第1页共2页\n每10股派1.00元人民币", "现金（含税）"),
        ("每10股派1.00元人民币\n1", "2\n现金（含税）"),
        ("每10股派1.00元人民币", "公司简称\n现金（含税）"),
        ("每10股派1.00元人民币", "三、历史方案 现金（含税）"),
        ("每10股派0.", "22元（含税）"),
        ("每10股派1", "0元（含税）"),
        ("每10股派1.00元人民币", "现金（税后）"),
    ],
)
def test_joining_never_strips_arbitrary_text_or_repairs_numeric_tokens(left, right):
    if left.startswith("第"):
        marker, body = left.split("\n", 1)
        left = marker + "\n二、权益分派方案 " + body
    else:
        left = "二、权益分派方案 " + left
    assert not values([left, right], "cash_per_share")


def test_cross_page_history_does_not_become_current_cash():
    assert not values(
        ["一、股东大会审议情况 向全体股东每10股派1.00元人民币", "现金（含税）"],
        "cash_per_share",
    )


def test_listing_start_is_not_inferred_from_the_ex_date():
    text = (
        "本次除权除息日为2024年5月29日。"
        "六、本次所转增的无限售条件流通股的起始交易日为2024年5月30日。"
    )
    assert values([text], "share_list_date") == {"2024-05-30"}
    assert not values([text.replace("本次所转增的无限售条件流通股", "其他股票")], "share_list_date")


def candidate(identifier, title):
    return {"id": identifier, "title": title}


@pytest.mark.parametrize("separator", ["", "：", ":"])
def test_catalogue_short_name_is_bound_to_labelled_security_name(separator):
    title = "关于实施2023年度权益分派后调整回购股份价格上限的公告"
    page = (
        "证券代码：600095 证券简称：湘财股份 公告编号：临2024-037\n"
        "湘财股份有限公司\n" + title + "\n调整后回购价格上限为10.01元/股。"
    )
    role = notice_role("sh600095", candidate("a", "湘财股份" + separator + title), [page])
    assert role["role"] == "REPURCHASE_PRICE"
    assert role["title"] == "湘财股份" + separator + title
    assert role["title_match"]["body_title"] == title
    assert role["title_match"]["security_short_name"] == "湘财股份"


@pytest.mark.parametrize(
    "symbol,catalogue,heading",
    [
        ("sh600096", "湘财股份", "证券简称：湘财股份"),
        ("sh600095", "其他公司", "证券简称：湘财股份"),
        ("sh600095", "湘财股份", "公司简称：湘财股份"),
        ("sh600095", "湘财股份", "证券简称：其他公司"),
    ],
)
def test_catalogue_prefix_needs_matching_issuer_and_explicit_short_name(symbol, catalogue, heading):
    title = "2023年度权益分派实施公告"
    page = "证券代码：600095 " + heading + " 公告编号：2024-1\n湘财股份有限公司\n" + title
    assert notice_role(symbol, candidate("a", catalogue + title), [page])["role"] == "UNKNOWN"


def test_short_name_removal_does_not_rewrite_a_different_document_title():
    page = (
        "证券代码：600901 证券简称：江苏金租 公告编号：2024-035\n"
        "江苏金融租赁股份有限公司\n关于实施权益分派调整可转债转股价格公告"
    )
    role = notice_role(
        "sh600901", candidate("a", "江苏金租：关于利润分配调整可转债转股价格公告"), [page]
    )
    assert role["role"] == "UNKNOWN"


def test_title_alias_never_resolves_a_mixed_candidate_set():
    candidates = [
        candidate("primary", "湘财股份2023年度权益分派实施公告"),
        candidate("other", "湘财股份关于2023年度权益分派补充说明的公告"),
    ]
    header = "证券代码：600095 证券简称：湘财股份 公告编号：2024-1\n湘财股份有限公司\n"
    docs = {row["id"]: [header + row["title"][4:]] for row in candidates}
    result = resolve_companion_notices("sh600095", candidates, docs)
    assert result["status"] == "UNRESOLVED_CANDIDATES"
    assert result["selected_id"] is None
