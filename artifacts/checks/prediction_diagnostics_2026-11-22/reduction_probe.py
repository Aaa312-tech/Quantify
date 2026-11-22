"""Compare explicit float32 reduction orders with the first frozen Ridge prediction."""

from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from quantfolio.dataset import eligible_rows, load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, read, save


def reduce_lanes(values, order):
    values = values.copy()
    if order == 'sum':
        return values.sum(axis=1, dtype=np.float32)
    if order == 'sequential':
        total = values[:, 0].copy()
        for lane in range(1, values.shape[1]):
            total += values[:, lane]
        return total
    while values.shape[1] > 1:
        half = values.shape[1] // 2
        if order == 'adjacent':
            values = values[:, ::2] + values[:, 1::2]
        else:
            values = values[:, :half] + values[:, half:]
    return values[:, 0]


paths = Paths(Path.cwd())
bundle = load_bundle(paths)
folder = paths.root / 'artifacts/experiments/recipient_entitlements_2026-11-22/training/fold_01'
fold = read(folder / 'result.json')['fold']
rows = eligible_rows(bundle['table'], fold['validation_start'], fold['validation_end'])
model = joblib.load(folder / 'validation_fit/ridge/model.joblib')
matrix = bundle['table'][bundle['features']].to_numpy(dtype=np.float32)[rows]
with threadpool_limits(limits=2):
    matrix = model[:-1].transform(matrix)
weights = model[-1].coef_
intercept = model[-1].intercept_
expected = np.load(folder / 'validation_fit/ridge/predictions.npy')
records = []
for lanes in (4, 8, 16, 32, 64):
    for fused in (False, True):
        for scalar_tail in (False, True):
            stop = matrix.shape[1] // lanes * lanes if scalar_tail else matrix.shape[1]
            partial = np.zeros((len(matrix), lanes), dtype=np.float32)
            for column in range(stop):
                lane = column % lanes
                if fused:
                    partial[:, lane] = (matrix[:, column].astype(np.float64) * float(weights[column]) + partial[:, lane].astype(np.float64)).astype(np.float32)
                else:
                    partial[:, lane] += matrix[:, column] * weights[column]
            for order in ('sum', 'sequential', 'adjacent', 'halves'):
                actual = reduce_lanes(partial, order)
                for column in range(stop, matrix.shape[1]):
                    if fused:
                        actual = (matrix[:, column].astype(np.float64) * float(weights[column]) + actual.astype(np.float64)).astype(np.float32)
                    else:
                        actual += matrix[:, column] * weights[column]
                actual = actual + intercept
                records.append({'lanes': lanes, 'fused': fused, 'scalar_tail': scalar_tail, 'order': order, 'different': int(np.count_nonzero(actual != expected)), 'max_absolute_error': float(np.max(np.abs(actual.astype(np.float64) - expected))), 'prediction_sha256': array_sha(actual)})
result = {'new_model_fits': 0, 'input_sha256': array_sha(matrix), 'models': 1, 'attempts': records}
save(Path(__file__).with_name('reduction_probe.json'), result, immutable=True)
print(sorted(records, key=lambda row: row['different'])[:10])
