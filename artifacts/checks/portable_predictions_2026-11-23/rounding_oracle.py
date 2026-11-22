"""Independent float32 fused-add check, including scaled midpoint and overflow cases."""
import ctypes
from pathlib import Path
import numpy as np
import quantfolio.models as models
from quantfolio.storage import save, sha

fmaf = ctypes.CDLL('ucrtbase').fmaf
fmaf.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
fmaf.restype = ctypes.c_float
generator = np.random.default_rng(20261123)
values = generator.integers(0, 2**32, size=(300000, 3), dtype=np.uint32).view(np.float32)
values = values[np.isfinite(values).all(axis=1)]
boundaries = []
for exponent in range(-120, 120):
    for sign in (-1, 1):
        for offset in (-1, 0, 1):
            boundaries.append([sign * (1 + 2**-23), 1.5 * 2.0**exponent, offset * 2.0**(exponent-60)])
for sign in (-1, 1):
    for offset in (-1, 0, 1):
        boundaries.append([sign * 31, 1082401 * 2.0**103, offset])
with np.errstate(over='ignore',under='ignore'):
    boundaries = np.array(boundaries, dtype=np.float32)
values = np.concatenate([values,boundaries])
expected = np.array([fmaf(float(a),float(b),float(c)) for a,b,c in values], dtype=np.float32)
actual = models.float32_fma(values[:,0], values[:,1], values[:,2])
different = np.flatnonzero(actual.view(np.uint32) != expected.view(np.uint32))
result = {'model_source_sha256':sha(models.__file__),'oracle':'Windows C runtime fmaf','seed':20261123,'samples':len(values),'targeted_midpoints':len(boundaries),'different_bits':len(different),'examples':[{'operands_hex':[hex(int(x)) for x in values[i].view(np.uint32)],'expected_hex':hex(int(expected[i].view(np.uint32))),'actual_hex':hex(int(actual[i].view(np.uint32)))} for i in different[:8]],'status':'PASS' if not len(different) else 'FAIL','new_model_fits':0}
save(Path(__file__).with_suffix('.json'), result, immutable=True)
print(result)
raise SystemExit(bool(len(different)))
