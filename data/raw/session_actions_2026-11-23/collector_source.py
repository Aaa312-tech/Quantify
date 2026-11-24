import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import now, save, sha

root = Path.cwd()
paths = Paths(root)
bundle = load_bundle(paths)
folder = root / "data/raw/session_actions_2026-11-23"
assert not folder.exists()
folder.mkdir(parents=True)
(folder / "collector_source.py").write_bytes(Path(__file__).read_bytes())
codes = {m["code"]: s for s, m in bundle["meta"].items()}
records, equities, funds = [], [], []
save(folder / "protocol.json", {"registered_at": now(), "start": "2026-11-21", "end": "2026-11-24",
    "source_bundle_sha256": sha(paths.bundle), "collector_sha256": sha(folder / "collector_source.py"),
    "scope": "Secondary-source screen of distributions and fund splits. Positive fixed-universe intersections require primary review; absence does not certify source completeness."}, immutable=True)
for page in range(1, 11):
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urlencode({
        "sortColumns": "EX_DIVIDEND_DATE", "sortTypes": "1", "pageSize": "500", "pageNumber": str(page),
        "reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "source": "WEB", "client": "WEB",
        "filter": "(EX_DIVIDEND_DATE>='2026-11-21')(EX_DIVIDEND_DATE<='2026-11-24')"})
    requested = now()
    with urlopen(Request(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=20) as response:
        raw = response.read()
    file = folder / f"equities_{page:02d}.json"
    file.write_bytes(raw)
    data = json.loads(raw)
    if not data.get("success") or data.get("result") is None:
        raise ValueError("Equity action screen did not return a complete result")
    rows = data["result"]["data"]
    equities.extend({"symbol": codes[r["SECURITY_CODE"]], "row": r} for r in rows if r["SECURITY_CODE"] in codes)
    records.append({"kind":"equities","page":page,"url":url,"requested_at":requested,"rows":len(rows),"sha256":sha(file)})
    if page >= data["result"]["pages"]:
        break
    time.sleep(0.5)
else:
    raise ValueError("Equity screen page cap reached")

for kind, variable, dt, rank, date_index in (("dividends","jjfh_data","8","FSRQ",3),("splits","jjcf_data","9","FSRQ",2)):
    for page in range(1, 11):
        url = "https://fund.eastmoney.com/Data/funddataIndex_Interface.aspx?" + urlencode({
            "dt":dt,"page":page,"rank":rank,"sort":"desc","gs":"","ftype":"","year":"2026"})
        requested = now()
        with urlopen(Request(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=20) as response:
            raw=response.read()
        file=folder/f"{kind}_{page:02d}.txt"
        file.write_bytes(raw)
        text=raw.decode("utf-8")
        match=re.search(variable+r"\s*=\s*(\[.*?\])\s*;",text,re.S)
        if not match:
            raise ValueError("Fund screen envelope changed")
        rows=json.loads(match.group(1))
        dates=[r[date_index] for r in rows]
        info=json.loads(re.search(r"pageinfo\s*=\s*(\[.*?\])",text,re.S).group(1))
        funds.extend({"kind":kind,"symbol":codes[r[0]],"row":r} for r in rows if r[0] in codes and "2026-11-21"<=r[date_index]<= "2026-11-24")
        records.append({"kind":kind,"page":page,"url":url,"requested_at":requested,"rows":len(rows),"sha256":sha(file),"first_date":max(dates) if dates else None,"last_date":min(dates) if dates else None})
        if not rows or min(dates)<="2026-11-20" or page>=info[0]:
            break
        time.sleep(0.5)
    else:
        raise ValueError("Fund screen page cap reached")
save(folder/"screen.json", {"completed_at":now(),"status":"COMPLETE","records":records,
    "equity_intersections":equities,"fund_intersections":funds,
    "known_events":[a for a in bundle["actions"] if "2026-11-21"<=a["ex_date"]<="2026-11-24"],
    "activated":False}, immutable=True)
print(json.dumps({"equities":equities,"funds":funds},ensure_ascii=False))
