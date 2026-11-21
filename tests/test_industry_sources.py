"""Industry source cells must retain their identity, hierarchy and complete inventory."""

import pytest

from quantfolio.industry_sources import (
    alias_row,
    classification_row,
    codebook_rows,
    compact,
    verify_page,
)


def paragraphs(*values):
    return [[value] if isinstance(value, str) else value for value in values]


@pytest.fixture
def codebook():
    rows = [
        paragraphs("A", "", "", "", "农、林、牧、渔业", ""),
        paragraphs("", "", "02", ["", "021", "022"], ["林业", "林木育种和育苗", "造林和更新"], ""),
        paragraphs("C", "", "", "", "制造业", ""),
        paragraphs("", "CH", "", "", "电气、电子及通讯", ""),
        paragraphs("", "", "39", "", "计算机、通信和其他电子设备制造业", ""),
        paragraphs("J", "", "", "", "金融业", ""),
        paragraphs("", "", "66", "", "货币金融服务", ""),
    ]
    return {(row["level"], row["code"]): row for row in codebook_rows([rows])}


def test_merged_codebook_row_keeps_division_separate_from_groups(codebook):
    row = codebook["division", "02"]
    assert row["name"] == "林业"
    assert row["section"] == "A" and row["subclass"] is None
    assert row["raw_paragraphs"][3] == ["", "021", "022"]


def test_wrapped_cells_and_leading_zero_code_are_preserved(codebook):
    cells = [
        "000020",
        "深华发 A",
        "C",
        "制造业",
        "CH",
        "电气、电子及通讯",
        "39",
        "计算机、通信和其他电\n子设备制造业",
    ]
    row = classification_row(cells, codebook)
    assert row["code"] == "000020" and row["name"] == "深华发A"
    assert row["raw_cells"] == cells


@pytest.mark.parametrize("column,value", [(2, "A"), (4, ""), (5, ""), (6, "66"), (7, "未知行业")])
def test_manufacturing_hierarchy_cannot_be_shifted_or_filled(codebook, column, value):
    cells = [
        "000020",
        "深华发A",
        "C",
        "制造业",
        "CH",
        "电气、电子及通讯",
        "39",
        "计算机、通信和其他电子设备制造业",
    ]
    cells[column] = value
    with pytest.raises(ValueError):
        classification_row(cells, codebook)


def test_blank_nonmanufacturing_subclass_is_valid_but_merged_cell_is_not(codebook):
    cells = ["000001", "平安银行", "J", "金融业", "", "", "66", "货币金融服务"]
    assert classification_row(cells, codebook)["subclass"] == ""
    cells[4] = None
    with pytest.raises(ValueError, match="unmerged"):
        classification_row(cells, codebook)


def test_independent_text_checks_all_cells_and_extra_rows(codebook):
    cells = ["000001", "平安银行", "J", "金融业", "", "", "66", "货币金融服务"]
    row = {**classification_row(cells, codebook), "row": 3}
    text = " ".join(cells)
    verify_page([row], text)
    with pytest.raises(ValueError, match="text differs"):
        verify_page([row], text.replace("货币金融服务", "其他金融业"))
    with pytest.raises(ValueError, match="inventory"):
        verify_page([row], text + "\n000002 万科A")
    with pytest.raises(ValueError, match="inventory"):
        verify_page([row], text + "\n" + text)


def test_alias_keeps_source_date_without_treating_it_as_code_effective_date():
    cells = ["4", "佳先股份", "2020-07-27", "430489", "920489"]
    row = alias_row(cells)
    assert row["source_listing_date"] == "2020-07-27"
    assert "available_at" not in row and "effective_date" not in row
    verify_page([{**row, "row": 5}], " ".join(cells), aliases=True)
    cells[-1] = "830489"
    with pytest.raises(ValueError, match="Beijing"):
        alias_row(cells)


@pytest.mark.parametrize("text", ["金融\ufffd业", "金融\x00业", 66])
def test_unreadable_source_is_rejected(text):
    with pytest.raises(ValueError, match="Unreadable"):
        compact(text)


def test_duplicate_or_unparented_codes_are_rejected():
    parent = paragraphs("J", "", "", "", "金融业", "")
    division = paragraphs("", "", "66", "", "货币金融服务", "")
    with pytest.raises(ValueError, match="Duplicate"):
        codebook_rows([[parent, division, division]])
    with pytest.raises(ValueError, match="parent"):
        codebook_rows([[division]])
