"""Check vector-tail placement in float32 Ridge reduction; no estimator is changed."""
from pathlib import Path
import joblib
import numpy as np
from threadpoolctl import threadpool_limits
from quantfolio.dataset import eligible_rows, load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import read, save, array_sha

paths = Paths(Path.cwd())
bundle = load_bundle(paths)
folder = paths.root / 'artifacts/experiments/recipient_entitlements_2026-11-22/training/fold_01'
fold = read(folder / 'result.json')['fold']
rows = eligible_rows(bundle['table'], fold['validation_start'], fold['validation_end'])
model = joblib.load(folder / 'validation_fit/ridge/model.joblib')
matrix = bundle['table'][bundle['features']].to_numpy(dtype=np.float32)[rows]
with threadpool_limits(limits=2):
    matrix = model[:-1].transform(matrix)
weights, intercept = model[-1].coef_, model[-1].intercept_
expected = np.load(folder / 'validation_fit/ridge/predictions.npy')

def add(total, column, fused):
    product = matrix[:, column].astype(np.float64) * float(weights[column])
    return (product + total.astype(np.float64)).astype(np.float32) if fused else total + product.astype(np.float32)

records = []
for fused in (True, False):
    partial = np.zeros((len(matrix), 8), dtype=np.float32)
    for column in range(56):
        partial[:, column % 8] = add(partial[:, column % 8], column, fused)
    for tail_four in ('fused', 'unfused'):
        for last in ('lane_fused', 'lane_unfused', 'post_fused', 'post_unfused'):
            lanes = partial.copy()
            for column in range(56, 60):
                lanes[:, column % 8] = add(lanes[:, column % 8], column, tail_four == 'fused')
            if last.startswith('lane'):
                lanes[:, 4] = add(lanes[:, 4], 60, last == 'lane_fused')
            for order in ('adjacent', 'halves', 'cross'):
                a,b,c,d,e,f,g,h = lanes.T
                if order == 'adjacent':
                    actual = ((a+b)+(c+d))+((e+f)+(g+h))
                elif order == 'halves':
                    actual = ((a+e)+(c+g))+((b+f)+(d+h))
                else:
                    actual = ((a+b)+(e+f))+((c+d)+(g+h))
                if last.startswith('post'):
                    actual = add(actual, 60, last == 'post_fused')
                actual = actual + intercept
                records.append({'fused':fused,'tail_four':tail_four,'last':last,'order':order,'different':int(np.count_nonzero(actual != expected)), 'max_absolute_error':float(np.max(np.abs(actual.astype(np.float64)-expected))), 'prediction_sha256':array_sha(actual)})
save(Path(__file__).with_suffix('.json'), {'new_model_fits':0,'models':1,'attempts':records}, immutable=True)
print(sorted(records, key=lambda r:r['different'])[:5])
