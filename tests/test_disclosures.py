"""Incomplete or ambiguous source acquisition must not certify historical events."""

import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.error import HTTPError

import pandas as pd
import pytest

from quantfolio import disclosures
from quantfolio.settings import Paths
from quantfolio.storage import read, save, sha


def bundle():
    calendar = pd.bdate_range("2026-01-05", periods=6)
    return {
        "calendar": calendar,
        "cutoff": str(calendar[-1].date()),
        "raws": {"bj920001": pd.DataFrame({"close": [10.0] * 4}, index=calendar[[0, 3, 4, 5]])},
        "actions": [
            {
                "symbol": "bj920001",
                "ex_date": "2026-01-09",
                "cash_per_share": 0.5,
                "share_multiplier": 1.0,
                "cash_pay_date": None,
                "share_list_date": None,
            }
        ],
    }


def notice(identifier="123", code="832001", title="2025年年度权益分派实施公告"):
    return {
        "announcementId": identifier,
        "secCode": code,
        "orgId": "issuer",
        "announcementTitle": title,
        "announcementTime": 1767542400000,
        "adjunctUrl": f"finalpage/2026-01-05/{identifier}.PDF",
    }


def page(records, total=None, more=False):
    return {
        "announcements": records,
        "totalAnnouncement": len(records) if total is None else total,
        "hasMore": more,
    }


def test_queue_covers_cash_share_dates_and_gaps_without_selecting_on_holdings():
    data = bundle()
    data["actions"].append(
        {
            **data["actions"][0],
            "symbol": "sh600001",
            "cash_per_share": 0,
            "share_multiplier": 1.2,
            "capitalization_per_share": 0.2,
        }
    )
    data["actions"].append({**data["actions"][0], "cash_pay_date": "2026-01-09"})
    original = deepcopy(data)
    queue = disclosures.source_queue(data)
    assert len(queue["actions"]) == 2
    assert queue["actions"][1]["missing_fields"] == ["share_list_date"]
    assert queue["gaps"][0]["missing_sessions"] == ["2026-01-06", "2026-01-07"]
    assert {x["term"] for x in queue["queries"]} == {"分", "转增", "牌"}
    assert "opening_status" not in data
    assert data["actions"] == original["actions"]
    pd.testing.assert_frame_equal(data["raws"]["bj920001"], original["raws"]["bj920001"])


def test_reviewed_resume_requires_a_bound_review_to_leave_the_queue():
    data = bundle()
    status = {"suspended": False, "reference_date": "2026-01-05"}
    data["opening_status"] = {"2026-01-08": {"bj920001": status}}
    assert len(disclosures.source_queue(data)["gaps"]) == 1
    status["review_sha256"] = "frozen_review"
    queue = disclosures.source_queue(data)
    assert not queue["gaps"] and queue["already_reviewed_gap_endpoints"] == 1


def test_historical_code_is_kept_under_the_same_issuer_without_inventing_publication_time():
    records, total, more = disclosures.parse_page(
        page([notice()]), "issuer", "分", "2026-01-01", "2026-01-12"
    )
    assert total == 1 and more is False
    assert records[0]["published_code"] == "832001"
    assert records[0]["catalog_date"] == "2026-01-05" and "known_at" not in records[0]


@pytest.mark.parametrize("fault", ["issuer", "filter", "date", "path", "total", "empty_page"])
def test_invalid_index_responses_are_not_accepted(fault):
    data = page([notice()])
    if fault == "issuer":
        data["announcements"][0]["orgId"] = "other"
    elif fault == "filter":
        data["announcements"][0]["announcementTitle"] = "季度报告"
    elif fault == "date":
        data["announcements"][0]["adjunctUrl"] = "finalpage/2026-01-06/123.PDF"
    elif fault == "path":
        data["announcements"][0]["adjunctUrl"] = "../../untrusted.PDF"
    elif fault == "total":
        data["totalAnnouncement"] = "1"
    else:
        data.update(announcements=[], hasMore=True)
    with pytest.raises(ValueError):
        disclosures.parse_page(data, "issuer", "分", "2026-01-01", "2026-01-12")


@pytest.mark.parametrize("fault", ["missing_page", "duplicate", "changing_count"])
def test_pagination_must_reconcile_before_a_query_can_complete(tmp_path, monkeypatch, fault):
    responses = [page([notice()], total=2, more=True), page([notice("124")], total=2)]
    if fault == "missing_page":
        responses[0]["hasMore"] = False
    elif fault == "duplicate":
        responses[1] = page([notice()], total=2)
    else:
        responses[1]["totalAnnouncement"] = 3
    monkeypatch.setattr(disclosures, "_download", lambda *_: json.dumps(responses.pop(0)).encode())
    monkeypatch.setattr(disclosures.time, "sleep", lambda _: None)
    result = disclosures._collect_query(
        tmp_path,
        tmp_path / "index",
        {"symbol": "bj920001", "term": "分"},
        {"code": "920001", "orgId": "issuer"},
        "2026-01-01",
        "2026-01-12",
    )
    assert result["status"] == "FAILED" and result["error"]
    assert (tmp_path / "index/page_001.json").is_file()


def test_rate_limit_stops_requests_and_preserves_failure_evidence(tmp_path, monkeypatch):
    calls = []

    def denied(url, form):
        calls.append(url)
        raise HTTPError(url, 429, "rate limited", {}, None)

    monkeypatch.setattr(disclosures, "_download", denied)
    monkeypatch.setattr(disclosures.time, "sleep", lambda _: None)
    record = disclosures._capture(tmp_path, tmp_path / "response.json", disclosures.QUERY_URL, {})
    assert len(calls) == 1 and record["access_blocked"]
    assert record["attempts"][0]["http_status"] == 429 and not (tmp_path / "response.json").exists()


def test_collection_freezes_sources_without_modifying_dataset_or_certifying_facts(
    tmp_path, monkeypatch
):
    config = tmp_path / "configs"
    config.mkdir()
    (config / "project.toml").write_text(
        'bundle="bundle.joblib"\nmanifest="configs/integrity_manifest.json"\n', encoding="utf-8"
    )
    (tmp_path / "bundle.joblib").write_bytes(b"trusted fixture")
    save(
        config / "integrity_manifest.json",
        {"files": {"bundle.joblib": sha(tmp_path / "bundle.joblib")}},
    )
    paths = Paths(tmp_path)
    data = bundle()
    calls = []

    def download(url, form=None):
        calls.append((url, form))
        if url == disclosures.LOOKUP_URL:
            return json.dumps({"stockList": [{"code": "920001", "orgId": "issuer"}]}).encode()
        if form is not None:
            records = [notice()] if form["searchkey"] == "分" else []
            return json.dumps(page(records)).encode()
        return b"%PDF-1.4\narchived source fixture"

    monkeypatch.setattr(disclosures, "load_bundle", lambda _: data)
    monkeypatch.setattr(disclosures, "_download", download)
    monkeypatch.setattr(disclosures.time, "sleep", lambda _: None)
    result = disclosures.collect_disclosures(paths, "data/raw/notices")
    assert result["status"] == "COLLECTED" and result["facts_applied"] == 0
    assert result["document_status"] == {"RECEIVED": 1} and len(calls) == 4
    assert data["actions"][0]["cash_pay_date"] is None
    assert "opening_status" not in data
    manifest = read(paths.manifest)["files"]
    assert all(sha(tmp_path / name) == value for name, value in manifest.items())
    receipt = read(tmp_path / "data/raw/notices/documents/123.json")
    assert receipt["review_status"] == "NOT_REVIEWED" and receipt["published_code"] == "832001"
    with pytest.raises(FileExistsError):
        disclosures.collect_disclosures(paths, "data/raw/notices")


def test_collection_path_cannot_escape_raw_storage(tmp_path):
    with pytest.raises(ValueError, match="data/raw"):
        disclosures.collect_disclosures(SimpleNamespace(root=tmp_path), "../elsewhere")
