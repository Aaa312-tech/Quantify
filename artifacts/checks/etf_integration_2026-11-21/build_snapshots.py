"""Build all registered ETF controls through the normal guarded entry point."""
import logging
from pathlib import Path
from quantfolio.funds import build_fund_snapshot
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
logging.basicConfig(level=logging.INFO, format="%(message)s")
root=Path.cwd(); paths=Paths(root)
pfile=root/"configs/protocols/etf_integration.json"
p=read(pfile)
sources={f.name:sha(f) for f in sorted((root/"src/quantfolio").glob("*.py"))}
results={}
for variant in p["variants"]:
    results[variant]=build_fund_snapshot(paths, pfile.relative_to(root), variant)
    print("BUILT",variant,results[variant]["bundle_sha256"],flush=True)
assert sources=={f.name:sha(f) for f in sorted((root/"src/quantfolio").glob("*.py"))}
save(root/p["checks_output"]/"snapshots.json",{"status":"PASS","completed_at":now(),"source_sha256":sources,"variants":results},immutable=True)
