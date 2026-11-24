from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import datetime as dt
import json
import subprocess

from quantfolio.daily_sources import baostock_bars, compare_target, quote_entries, tencent_beijing_bar
from quantfolio.dataset import load_bundle
from quantfolio.integrity import register_files
from quantfolio.market import risk_warning
from quantfolio.market_data import parse_bars
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
bundle = load_bundle(paths)
date = "2026-11-23"
out = root / "artifacts/checks/session_2026-11-23_reviewed"
assert not out.exists()
out.mkdir(parents=True)
(out / "review_source.py").write_bytes(Path(__file__).read_bytes())
base_sha = sha(paths.bundle)
source_paths = [root / "data/raw" / (name + "_" + date) for name in ("baostock_close", "tencent_close", "tencent_beijing_daily", "session_actions")]
quotes, parsed, errors, checks, share_volumes = {}, {}, [], [], {}
for record in read(source_paths[1] / "collection.json")["records"]:
    quotes.update(quote_entries((root / record["file"]).read_bytes()))
assert set(quotes) == set(bundle["meta"])
for record in read(source_paths[0] / "collection.json")["records"]:
    payload = load_gzip(root / record["response"])
    parsed.update(baostock_bars(payload, bundle["meta"], date))
    for values in payload["rows"]:
        row = dict(zip(payload["fields"], values))
        share_volumes[row["code"].replace(".", "")] = row["volume"]
for record in read(source_paths[2] / "collection.json")["records"]:
    symbol = record["symbol"]
    parsed[symbol] = tencent_beijing_bar((source_paths[2] / f"{symbol}.txt").read_bytes(), quotes[symbol], bundle["meta"][symbol], "2026-11-16", date, bundle["raws"][symbol])
assert set(parsed) == set(bundle["meta"])
primary = read(root / "data/raw/2026-11-23/collection.json")
primary_symbols = {r["symbol"] for r in primary["records"] if r["status"] == "CONSISTENT"}
for record in primary["records"]:
    if record["status"] != "CONSISTENT":
        continue
    s = record["symbol"]
    frame = parse_bars((root / f"data/raw/2026-11-23/{s}.json").read_bytes(), bundle["meta"][s], "2026-11-16", date)
    differences = compare_target(frame.iloc[-1], parsed[s][1], "tencent_beijing")
    if differences:
        errors.append({"symbol":s,"kind":"eastmoney_tencent","differences":differences})
for symbol, (frame, details) in parsed.items():
    quote = quotes[symbol]
    stamp = dt.datetime.strptime(quote[30], "%Y%m%d%H%M%S")
    assert stamp.date().isoformat() == date and stamp.hour >= 15, (symbol, quote[30])
    assert quote[2] == bundle["meta"][symbol]["code"]
    row = frame.iloc[0]
    differences = []
    for field, index in (("open",5),("close",3),("high",33),("low",34),("volume_lots",6)):
        observed = Decimal(quote[index])
        if field == "volume_lots" and symbol.startswith("sh688"):
            assert observed == Decimal(share_volumes[symbol]), (symbol, "full precision STAR share volume differs")
            observed = (observed / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        delta = abs(Decimal(str(row[field]))-observed)
        if delta > Decimal("0.0000001"):
            differences.append({"field":field,"primary":str(row[field]),"quote":quote[index],"delta":str(delta)})
    checks.append({"symbol":symbol,"quote_timestamp":quote[30],"provider":"tencent" if symbol.startswith("bj") else "baostock",
                   "cross_provider":not symbol.startswith("bj"),"ohlcv_fields":5,"differences":differences,
                   "prior_close_source":details["source_preclose"],"quote_prior_close":quote[4]})
    if differences:
        errors.append({"symbol":symbol,"kind":"closing_quote","differences":differences})
save(out / "cross_source.json", {"completed_at":now(),"status":"PASS" if not errors else "FAIL", "securities":len(parsed),
    "cross_provider_securities":568,"tencent_daily_and_quote_only_securities":21,"eastmoney_corroborated_beijing":len(primary_symbols),
    "checks":checks,"errors":errors,"star_volume_units":{"securities":58,"quote_unit":"shares","raw_baostock_share_values_exact":True,"normalization":"Existing raw lots contract: shares / 100 rounded HALF_UP to integer lots"},"previous_failed_comparison":"artifacts/checks/session_2026-11-23/cross_source.json"},immutable=True)
print("cross_source",len(parsed),"errors",len(errors),json.dumps(errors[:5]),flush=True)
assert not errors, "Cross-source discrepancies require review; see cross_source.json"

screen = read(source_paths[3] / "screen.json")
assert {x["symbol"] for x in screen["equity_intersections"]} == {"sz301308","sh600369"}
assert screen["fund_intersections"] == []
pdf = source_paths[3]/"sh600369_2026-11-17.pdf"
receipt = read(pdf.with_suffix(".receipt.json"))
assert sha(pdf) == receipt["sha256"]
facts = {"reviewed_at":now(),"facts":[{
    "symbol":"sh600369","notice_date":"2026-11-17","record_date":"2026-11-23","ex_date":"2026-11-24",
    "cash_pay_date":"2026-11-24","cash_per_share":0.02,"share_multiplier":1.0,"share_list_date":None,
    "ex_reference_cash_per_share":0.02,"ex_reference_share_multiplier":1.0,
    "source":{"source":receipt["source"],"local_pdf":pdf.relative_to(root).as_posix(),"sha256":sha(pdf)},
    "evidence_pages":[1,2,3],"review_status":"PRIMARY_FILING_REVIEWED"
}]}
save(out/"action_review.json",facts,immutable=True)
assignments = [{"symbol":s,"provider":"eastmoney" if s in primary_symbols else "tencent" if s.startswith("bj") else "baostock"} for s in sorted(bundle["meta"])]
save(out/"source_assignment.json",{"registered_at":now(),"parent_bundle_sha256":base_sha,"assignments":assignments},immutable=True)
def item(folder):
    return {"path":folder.relative_to(root).as_posix(),"protocol_sha256":sha(folder/"protocol.json"),"collection_sha256":sha(folder/"collection.json")}
proto = {
    "name":"session_2026-11-23","registered_at":now(),"baseline_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
    "source_bundle":paths.config["bundle"],"source_bundle_sha256":base_sha,"source_data_cutoff":paths.config["data_cutoff"],
    "collection_source_bundle":paths.config["bundle"],"collection_source_sha256":base_sha,"completed_date":date,
    "collections":[item(root/"data/raw/2026-11-23"),item(root/"data/raw/2026-11-23-retry")],
    "fallback_source_bundle":paths.config["bundle"],"fallback_source_sha256":base_sha,
    "fallback_collections":[
        {**item(source_paths[0]),"provider":"baostock_daily","use_symbols":sorted(s for s in bundle["meta"] if not s.startswith("bj"))},
        {**item(source_paths[2]),"provider":"tencent_beijing","use_symbols":sorted(s for s in bundle["meta"] if s.startswith("bj") and s not in primary_symbols),"closing_quotes":item(source_paths[1])}],
    "source_assignment":{"path":(out/"source_assignment.json").relative_to(root).as_posix(),"sha256":sha(out/"source_assignment.json")},
    "action_reviews":[{"path":(out/"action_review.json").relative_to(root).as_posix(),"sha256":sha(out/"action_review.json"),"mode":"append"}],
    "review_evidence_sha256":{p.relative_to(root).as_posix():sha(p) for folder in [*source_paths,out] for p in folder.rglob("*") if p.is_file()},
    "approved_overlap_fields":[],"quarantined":{"sh688017":"Retain prior quarantine for missing historical amount and turnover."},
    "risk":"Unchanged balanced attack, original high-water mark and 8% latched drawdown warning.",
    "selection_mode":"positive_score","turnover_policy":"legacy",
    "limitations":["21 Beijing securities have Tencent daily/quote agreement without independent-provider corroboration; five have Eastmoney agreement.","Secondary event screening does not certify complete disclosures. Existing dated status, industry, ETF and historical gap limitations remain.","Daily closes are not opening auction quotes. No order is executed by this workflow."],
    "training":"Unchanged registered walk-forward training and rule selection on the extended snapshot; retain every historical artifact."
}
protocol = root/"configs/protocols/session_2026-11-23.json"
save(protocol,proto,immutable=True)
files = [protocol,*[p for folder in [*source_paths,out] for p in folder.rglob("*") if p.is_file()]]
register_files(paths,files)
print("Protocol registered",protocol,flush=True)
