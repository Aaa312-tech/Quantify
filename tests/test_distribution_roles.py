"""Price notices cannot replace or silently disambiguate dividend implementations."""

import pytest

from quantfolio.distributions import notice_role, resolve_companion_notices

PRIMARY = {"id": "primary", "title": "2024年年度权益分派实施公告"}
COMPANION = {"id": "price", "title": "关于2024年年度权益分派后调整回购股份价格上限的公告"}


def page(candidate, body=""):
    return "证券代码：600000\n" + candidate["title"] + "\n" + body


def documents(body="调整后回购股份价格上限为12.30元/股。"):
    return {PRIMARY["id"]: [page(PRIMARY)], COMPANION["id"]: [page(COMPANION, body)]}


def test_price_notice_requires_independent_body_support():
    accepted = resolve_companion_notices("sh600000", [PRIMARY, COMPANION], documents())
    assert accepted["selected_id"] == "primary"
    assert len(accepted["candidates"]) == 2
    for body in ("", "回购股份价格上限为12.30元/股。", "调整回购股份价格上限。"):
        result = resolve_companion_notices("sh600000", [PRIMARY, COMPANION], documents(body))
        assert result["selected_id"] is None


@pytest.mark.parametrize("change", ["issuer", "title", "empty"])
def test_title_and_labelled_issuer_must_agree(change):
    pages = documents()["price"]
    if change == "issuer":
        pages[0] = pages[0].replace("600000", "600001") + " 公司另提及600000。"
    elif change == "title":
        pages[0] = pages[0].replace("2024年", "2023年")
    else:
        pages = []
    assert notice_role("sh600000", COMPANION, pages)["role"] == "UNKNOWN"


@pytest.mark.parametrize("word", ["更正", "修订", "补充", "取消", "终止", "撤销", "延期"])
def test_changed_notices_remain_unresolved(word):
    candidate = {**COMPANION, "title": COMPANION["title"] + "（" + word + "）"}
    assert notice_role("sh600000", candidate, [page(candidate)])["role"] == "UNKNOWN"


def test_two_primaries_and_mixed_notices_remain_unresolved():
    second = {**PRIMARY, "id": "second"}
    docs = {**documents(), "second": [page(second)]}
    assert (
        resolve_companion_notices("sh600000", [PRIMARY, second, COMPANION], docs)["selected_id"]
        is None
    )
    mixed = {**COMPANION, "title": "权益分派实施公告及调整回购价格公告"}
    assert (
        notice_role("sh600000", mixed, [page(mixed, "调整回购价格至12元。")])["role"] == "UNKNOWN"
    )


def test_duplicate_or_missing_documents_cannot_be_hidden():
    with pytest.raises(ValueError, match="identities"):
        resolve_companion_notices("sh600000", [PRIMARY, PRIMARY], {"primary": [page(PRIMARY)]})
    with pytest.raises(ValueError, match="identities"):
        resolve_companion_notices("sh600000", [PRIMARY, COMPANION], {})


def test_conversion_notice_and_second_page_body():
    candidate = {"id": "bond", "title": "关于权益分派后调整可转债转股价格的公告"}
    role = notice_role(
        "sh600000", candidate, [page(candidate), "本次调整转股价格由12.00元至11.90元。"]
    )
    assert role["role"] == "CONVERSION_PRICE"
    assert role["evidence"][0]["page"] == 2
