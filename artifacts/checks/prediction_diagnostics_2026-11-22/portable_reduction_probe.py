"""Validate one fixed float32 reduction over every frozen Ridge model and fused-add edges."""
from pathlib import Path
import ctypes
import joblib
import numpy as np
from threadpoolctl import threadpool_limits
from quantfolio.dataset import eligible_rows, load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import read, save, array_sha

def fused_add(left, right, total):
    product = left.astype(np.float64) * np.float64(right)
    addend = total.astype(np.float64)
    summed = product + addend
    with np.errstate(over='ignore'):
        rounded = summed.astype(np.float32)
    finite = np.isfinite(rounded)
    direction = np.where(summed > rounded.astype(np.float64), np.float32(np.inf), np.float32(-np.inf))
    with np.errstate(over='ignore'):
        adjacent = np.nextafter(rounded, direction)
    midpoint = (rounded.astype(np.float64) + adjacent.astype(np.float64)) * 0.5
    ties = finite & (summed == midpoint)
    if ties.any():
        virtual = summed[ties] - product[ties]
        error = (product[ties] - (summed[ties] - virtual)) + (addend[ties] - virtual)
        move = ((error > 0) & (adjacent[ties] > rounded[ties])) | ((error < 0) & (adjacent[ties] < rounded[ties]))
        rounded[ties] = np.where(move, adjacent[ties], rounded[ties])
    return rounded

def predict(matrix, weights, intercept):
    lanes = np.zeros((len(matrix), 8), dtype=np.float32)
    for column in range(matrix.shape[1]):
        lane = column % 8
        lanes[:, lane] = fused_add(matrix[:, column], weights[column], lanes[:, lane])
    a,b,c,d,e,f,g,h = lanes.T
    return (((a+b)+(e+f))+((c+d)+(g+h))) + intercept

paths = Paths(Path.cwd())
bundle = load_bundle(paths)
source = paths.root / 'artifacts/experiments/recipient_entitlements_2026-11-22'
matrix = bundle['table'][bundle['features']].to_numpy(dtype=np.float32)
results = sorted(source.glob('training/fold_*/result.json')) + [source / 'final_fit/result.json']
records = []
for result_path in results:
    fold = read(result_path)['fold']
    for stage, start, end in [('validation_fit',fold['validation_start'],fold['validation_end']),('outer_fit',fold['test_start'],fold['test_end'])]:
        rows = eligible_rows(bundle['table'], start, end)
        folder = result_path.parent / stage / 'ridge'
        model = joblib.load(folder / 'model.joblib')
        expected = np.load(folder / 'predictions.npy')
        with threadpool_limits(limits=2):
            transformed = model[:-1].transform(matrix[rows])
        actual = predict(transformed, model[-1].coef_, model[-1].intercept_)
        records.append({'path':str(folder.relative_to(source)),'count':len(rows),'different':int(np.count_nonzero(actual!=expected)),'max_error':float(np.max(np.abs(actual.astype(np.float64)-expected))),'prediction_sha256':array_sha(actual)})
        print(records[-1], flush=True)

# The C runtime fmaf provides an independent IEEE float32 fused-add oracle.
runtime = ctypes.CDLL('ucrtbase')
fmaf = runtime.fmaf
fmaf.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
fmaf.restype = ctypes.c_float
random = np.random.default_rng(20261122)
values = random.integers(0, 2**32, size=(100000, 3), dtype=np.uint32).view(np.float32)
values = values[np.isfinite(values).all(axis=1)]
oracle = np.array([fmaf(float(a),float(b),float(c)) for a,b,c in values], dtype=np.float32)
actual = fused_add(values[:,0],values[:,1],values[:,2])
edges = {'samples':len(values),'different_bits':int(np.count_nonzero(actual.view(np.uint32)!=oracle.view(np.uint32)))}
save(Path(__file__).with_suffix('.json'), {'new_model_fits':0,'models':records,'fused_add_oracle':edges}, immutable=True)
print(edges)
raise SystemExit(int(any(r['different'] for r in records) or edges['different_bits']))
