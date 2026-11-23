"""Compare the Windows CRT FMA switch using saved arithmetic inputs, without fitting."""

import ctypes
from pathlib import Path

import numpy as np

from quantfolio.storage import array_sha, read, save, sha

folder = Path(__file__).parent
root = Path.cwd()
local = read(root / "artifacts/checks/portable_inference_full_2026-11-23/result.json")
cloud = read(root / "artifacts/checks/portable_predictions_2026-11-23/cloud/ci-regression-45046246783/result.json")
runtime = ctypes.CDLL("ucrtbase")
runtime._get_FMA3_enable.argtypes = []
runtime._get_FMA3_enable.restype = ctypes.c_int
runtime._set_FMA3_enable.argtypes = [ctypes.c_int]
runtime._set_FMA3_enable.restype = ctypes.c_int
original = runtime._get_FMA3_enable()
records = []
try:
    for flag in (0, 1):
        active = runtime._set_FMA3_enable(flag)
        for before, after in zip(local["training"]["fit_inputs"], cloud["training"]["fit_inputs"], strict=True):
            path = folder / "local_before" / before["window"] / before["stage"] / "stages.npz"
            with np.load(path, allow_pickle=False) as arrays:
                decay = np.exp2(arrays["exponent"])
                weights = arrays["base_weights"] * decay
                weights /= weights.mean()
                digest = array_sha(weights)
                records.append({
                    "flag": flag, "active": active, "window": before["window"], "stage": before["stage"],
                    "decay_sha256": array_sha(decay), "weights_sha256": digest,
                    "matches_local": digest == before["weights_sha256"],
                    "matches_cloud": digest == after["weights_sha256"],
                    "different_decays": int(np.count_nonzero(decay != arrays["decay"])),
                })
finally:
    runtime._set_FMA3_enable(original)
result = {"scope": "CRT FMA control on frozen arithmetic inputs; no fitting", "script_sha256": sha(__file__), "original_flag": original, "restored_flag": runtime._get_FMA3_enable(), "records": records, "new_model_fits": 0}
save(folder / "fma_control.json", result, immutable=True)
print({flag: {kind: sum(row[kind] for row in records if row["flag"] == flag) for kind in ("matches_local", "matches_cloud")} for flag in (0, 1)})
