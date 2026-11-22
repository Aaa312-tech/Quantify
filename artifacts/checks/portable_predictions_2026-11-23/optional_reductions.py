"""Isolate the optional pandas reduction backend in the first frozen calibration."""
from pathlib import Path
import importlib.metadata
import numpy as np
import pandas as pd
from quantfolio.calibration import calibrate, map_calibration, rank_by_date
from quantfolio.dataset import eligible_rows, label_mask, load_bundle
from quantfolio.selection import label_clip_bounds
from quantfolio.settings import Paths
from quantfolio.storage import read, save

paths=Paths(Path.cwd())
table=load_bundle(paths)['table']
record=read(paths.root/'artifacts/experiments/recipient_entitlements_2026-11-22/training/fold_01/result.json')
fold=record['fold']
start=fold['train_end']-59
rows=eligible_rows(table,start,fold['train_end'])
fit_rows=np.flatnonzero(label_mask(table,5,fold['train_start'],start-7,start))
rank=rank_by_date(table,rows,table.iloc[rows].momentum_score.to_numpy())
validation_rows=eligible_rows(table,fold['validation_start'],fold['validation_end'])
validation_rank=rank_by_date(table,validation_rows,table.iloc[validation_rows].momentum_score.to_numpy())
validation=table.iloc[validation_rows]
index=np.flatnonzero((validation.date_index.to_numpy()==383)&(validation.symbol.to_numpy()=='bj920207'))[0]
outputs=[]
for enabled in [True,False]:
    with pd.option_context('compute.use_bottleneck',enabled):
        cal=calibrate(table,rows,rank,fold['validation_start'],label_clip_bounds(table,fit_rows))
        _,margins=map_calibration(validation_rank,cal)
    outputs.append({'bottleneck_enabled':enabled,'calibration_equal':cal==record['initial_calibration'],'standard_error':cal['standard_error'],'first_failed_margin':float(margins[index])})
save(Path(__file__).with_suffix('.json'),{'scope':'Existing first calibration only; no strategy or estimator fitting','bottleneck_version':importlib.metadata.version('bottleneck'),'numexpr_version':importlib.metadata.version('numexpr'),'new_model_fits':0,'cases':outputs},immutable=True)
print(outputs)
