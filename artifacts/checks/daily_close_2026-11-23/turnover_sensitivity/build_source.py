from copy import deepcopy
from pathlib import Path
import numpy as np
import pandas as pd

from quantfolio.dataset import eligible_rows, load_bundle
from quantfolio.preprocessing import build_dataset
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, now, read, save, sha

root=Path.cwd()
paths=Paths(root,"configs/experiments/daily_close_2026-11-23.toml")
bundle=load_bundle(paths)
out=root/"artifacts/checks/daily_close_2026-11-23/turnover_sensitivity"
assert not out.exists()
out.mkdir(parents=True)
(out/"build_source.py").write_bytes(Path(__file__).read_bytes())
source=root/"artifacts/checks/session_2026-11-23_reviewed/field_comparison.json"
changes=read(source)["differences"]
raws={s:frame.copy() for s,frame in bundle["raws"].items()}
day=bundle["calendar"][-1]
for change in changes:
    assert change["field"]=="turnover"
    symbol=change["symbol"]
    assert float(change["baostock"])==raws[symbol].at[day,"turnover"]
    raws[symbol].at[day,"turnover"]=float(change["tencent"])
alternative=build_dataset(bundle["meta"],raws,bundle["actions"],quarantined=["sh688017"],opening_status=bundle.get("opening_status"))
for section in ("panels","scores"):
    assert bundle[section].keys()==alternative[section].keys()
    for name,frame in bundle[section].items():
        pd.testing.assert_frame_equal(frame,alternative[section][name],check_exact=True)
last=len(bundle["calendar"])-1
old=bundle["table"].date_index<last
pd.testing.assert_frame_equal(bundle["table"].loc[old],alternative["table"].loc[old],check_exact=True)
changed=[column for column in bundle["table"] if not bundle["table"][column].equals(alternative["table"][column])]
assert all("turnover" in column for column in changed),changed
rows=eligible_rows(bundle["table"],last,last)
assert np.array_equal(rows,eligible_rows(alternative["table"],last,last))
features=alternative["table"].iloc[rows][bundle["features"]].to_numpy(dtype=np.float32)
np.savez_compressed(out/"latest_features.npz",row_indices=rows,features=features)
save(out/"inputs.json",{
    "status":"INPUTS_VERIFIED", "completed_at":now(), "source_bundle_sha256":sha(paths.bundle),
    "comparison_path":source.relative_to(root).as_posix(),"comparison_sha256":sha(source),
    "raw_values_changed":changes,"raw_values_changed_count":len(changes),
    "historical_rows_exact":int(old.sum()),"all_panels_and_scores_exact":True,
    "changed_table_columns":changed,"features":bundle["features"],"eligible_rows":len(rows),
    "feature_values_sha256":array_sha(features),"feature_file_sha256":sha(out/"latest_features.npz"),
    "purpose":"Recompute only final-date predictions with the alternative provider's 18 turnover values. Historical inputs, all fitted targets and validation decisions are unchanged; adoption requires identical selected forecasts and orders."
},immutable=True)
print({"status":"INPUTS_VERIFIED","changed_columns":changed,"eligible_rows":len(rows)},flush=True)
