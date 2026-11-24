from copy import deepcopy
from pathlib import Path
import json
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from quantfolio.allocation import costs
from quantfolio.calibration import map_calibration
from quantfolio.dataset import load_bundle, validate_features
from quantfolio.evaluation import experiment_folds
from quantfolio.industries import metadata_at
from quantfolio.integrity import register_output
from quantfolio.market import risk_warning
from quantfolio.models import candidate_scores, predict_model, prediction_map
from quantfolio.planning import latest_forecasts, make_intent
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha
from quantfolio.verification import verify_models

root=Path.cwd()
paths=Paths(root,"configs/experiments/daily_close_2026-11-23.toml")
bundle=load_bundle(paths)
run=root/"artifacts/experiments/daily_close_2026-11-23"
baseline_run=root/"artifacts/experiments/recipient_entitlements_2026-11-22"
out=root/"artifacts/checks/daily_close_2026-11-23"
assert not (out/"validation.json").exists()
out.mkdir(parents=True,exist_ok=True)
(out/"verification_source.py").write_bytes(Path(__file__).read_bytes())
training,folds=experiment_folds(paths,bundle,run)
assert training["status"]=="COMPLETE" and training["latest_fit_included"]
results=sorted(run.glob("training/fold_*/result.json"))+[run/"final_fit/result.json"]
models=verify_models(bundle,results,root)
assert len(models)==48
features=validate_features(bundle)
historical=[]
for result_path in results[:-1]:
    prior=baseline_run/"training"/result_path.parent.name
    current=read(result_path);old=read(prior/"result.json")
    for field in ("fold","selected_candidate","selected_rules","turnover_policy","initial_calibration","outer_calibration"):
        assert current[field]==old[field],(result_path,field)
    with np.load(result_path.parent/"outer_predictions.npz") as actual,np.load(prior/"outer_predictions.npz") as expected:
        assert set(actual.files)==set(expected.files)
        for name in actual.files:
            np.testing.assert_array_equal(actual[name],expected[name])
    historical.append({"fold":current["fold"]["fold"],"predictions":"EXACT","rules":current["selected_rules"],"model":current["selected_candidate"]})
evaluation=root/paths.config["evaluation"]
current=read(evaluation/"evaluation_summary.json")
prior_evaluation=root/"artifacts/evaluations/recipient_entitlements_2026-11-22"
baseline=read(prior_evaluation/"evaluation_summary.json")
for field in ("results","continuous","accounts","reconciliation_checks","execution_checks","fold_choices","trade_start","trade_end"):
    assert current[field]==baseline[field],("historical_evaluation",field)
assert current["artifacts"]==baseline["artifacts"],"Historical ledger hashes differ"
for relative,checksum in current["artifacts"].items():
    assert sha(evaluation/relative)==checksum

calendar=bundle["calendar"]
warning_records=[]
for symbol,meta in bundle["meta"].items():
    if not meta.get("risk_warning_periods"):
        continue
    warnings=[d for d in calendar if risk_warning(meta,d,as_of=d.tz_localize("Asia/Shanghai")+pd.Timedelta(hours=16)) is not None]
    assert not bundle["panels"]["eligible"].loc[warnings,symbol].any()
    warning_records.append({"symbol":symbol,"warning_sessions":len(warnings),"eligible_warning_rows":0})
assert len(warning_records)==10
current_flags=[]
signal=calendar[-1];at=signal.tz_localize("Asia/Shanghai")+pd.Timedelta(hours=16)
for record in read(root/"data/raw/baostock_close_2026-11-23/collection.json")["records"]:
    if record["kind"]!="stocks":
        continue
    payload=load_gzip(root/record["response"])
    for values in payload["rows"]:
        row=dict(zip(payload["fields"],values));symbol=row["code"].replace(".","")
        if symbol not in bundle["meta"]:
            continue
        expected=risk_warning(bundle["meta"][symbol],signal,as_of=at) is not None
        assert (row["isST"]=="1")==expected,(symbol,"dated_warning_conflict")
        assert row["tradestatus"]=="1"
        current_flags.append({"symbol":symbol,"tradestatus":row["tradestatus"],"isST":row["isST"]})

sensitivity=out/"turnover_sensitivity"
inputs=read(sensitivity/"inputs.json")
assert inputs["source_bundle_sha256"]==sha(paths.bundle)
assert sha(sensitivity/"latest_features.npz")==inputs["feature_file_sha256"]
with np.load(sensitivity/"latest_features.npz") as saved:
    rows=saved["row_indices"];matrix=saved["features"]
result=read(run/"final_fit/result.json")
predictions={}
for family in ("ridge","tree"):
    model=joblib.load(run/"final_fit/outer_fit"/family/"model.joblib")
    with threadpool_limits(limits=2):
        predictions[family]=predict_model(model,matrix)
scores=candidate_scores(bundle["table"],rows,predictions)
mean,margin=map_calibration(scores["momentum"],result["outer_calibration"])
name=result["selected_candidate"]
with np.load(run/"final_fit/outer_predictions.npz") as saved:
    assert np.array_equal(saved["row_indices"],rows)
    np.testing.assert_array_equal(saved[name+"_rank"],scores[name])
    np.testing.assert_array_equal(saved["common_mu"],mean)
    np.testing.assert_array_equal(saved["common_margin"],margin)
alternative=prediction_map(bundle["table"],{"row_indices":rows,name+"_rank":scores[name],"common_mu":mean,"common_margin":margin},name)[len(calendar)-1]
forecasts,fitted,_=latest_forecasts(paths,bundle)
assert forecasts==alternative
account=read(root/"artifacts/accounts/confirmed_2026-11-23/account.json")
assert account["cash"]==1_000_000 and not account["lots"] and account["risk"]["peak"]==1_000_000
plan=make_intent(bundle,deepcopy(account),forecasts,fitted["selected_rules"],"2026-11-24",0.001,turnover="legacy")
other=make_intent(bundle,deepcopy(account),alternative,fitted["selected_rules"],"2026-11-24",0.001,turnover="legacy")
assert plan==other
save(sensitivity/"result.json",{"status":"PASS","completed_at":now(),"adopted_candidate":name,"forecast_rows":len(rows),
    "selected_forecasts_exact":True,"intention_exact":True,"new_fits_for_sensitivity":0,
    "input_record_sha256":sha(sensitivity/"inputs.json"),"fitted_result_sha256":sha(run/"final_fit/result.json")},immutable=True)

meta=metadata_at(bundle["meta"],at)
ranked=[]
for symbol,prediction in forecasts.items():
    buy,sell=costs(meta,symbol,0.001)
    ranked.append({"symbol":symbol,"code":meta[symbol]["code"],"name":meta[symbol]["name"],"kind":meta[symbol]["kind"],
        **prediction,"round_trip_cost_rate":buy+sell,"net_edge":prediction["mu"]-prediction["margin"]-buy-sell,
        "close":float(bundle["raws"][symbol].at[signal,"close"]),"eligible":bool(bundle["panels"]["eligible"].at[signal,symbol]),
        "warning_on_execution":risk_warning(bundle["meta"][symbol],pd.Timestamp("2026-11-24"),as_of=at) is not None})
ranked.sort(key=lambda r:(-r["rank"],r["symbol"]))
save(out/"signals.json",{"as_of":"2026-11-23","execution_date":"2026-11-24","horizon_sessions":5,"model":name,
    "rule_config":fitted["selected_rules"],"slippage_per_side_bps":10,"positive_net_edge":sum(r["net_edge"]>0 for r in ranked),
    "risk_state":plan["state"],"cap":plan["cap"],"decision":plan["decision"],"target_weights":plan["target_weights"],"ranked":ranked},immutable=True)
save(out/"validation.json",{"status":"PASS","completed_at":now(),"features":features,"models_reloaded":len(models),"models":models,
    "historical_folds":historical,"historical_accounts_exact":current["accounts"],"historical_ledger_files_exact":len(current["artifacts"]),
    "reconciliation_checks":current["reconciliation_checks"],"execution_checks":current["execution_checks"],
    "warning_records":warning_records,"current_stock_status_count":len(current_flags),"current_stock_status":current_flags,
    "turnover_sensitivity":"PASS","source_bundle_sha256":sha(paths.bundle)},immutable=True)
old_review=root/"artifacts/checks/recipient_entitlements_2026-11-22/status_review.json"
source_review=read(old_review)
save(out/"status_review.json",{"status":"REGISTERED_STATUS_REPAIR_VERIFIED","blocking":False,"reviewed_at":now(),
    "source_bundle_sha256":sha(paths.bundle),"previous_review":old_review.relative_to(root).as_posix(),"previous_review_sha256":sha(old_review),
    "source_review":source_review["source_review"],"source_review_sha256":source_review["source_review_sha256"],
    "corrected_securities":len(warning_records),"warning_sessions":sum(r["warning_sessions"] for r in warning_records),
    "remaining_reviewed_warning_training_rows":0,"models_reloaded":len(models),"historical_accounts_exact":current["accounts"],
    "evidence":{"validation.json":sha(out/"validation.json"),"turnover_sensitivity/result.json":sha(sensitivity/"result.json")},
    "scope":"Rechecked the ten existing dated warning intervals on the extended snapshot, every current Shanghai/Shenzhen stock flag, 48 model reloads and all historical account/ledger outputs. Existing other historical coverage limitations remain; this does not certify unknown historic status or future opening tradability.",
    "limitations":source_review["limitations"]},immutable=True)
register_output(paths,out)
print(json.dumps({"status":"PASS","models":len(models),"historical_accounts":current["accounts"],"ledgers":len(current["artifacts"]),
    "selected_candidate":name,"cap":plan["cap"],"decision":plan["decision"],"target_weights":plan["target_weights"],"top_ranked":ranked[:8]},ensure_ascii=False,indent=2),flush=True)
