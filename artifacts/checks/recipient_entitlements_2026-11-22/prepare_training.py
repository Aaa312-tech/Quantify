"""Create the isolated experiment configuration after all input expectations pass."""

import tomllib
from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
base = root / p["checks_output"]
require(not (root / p["training_output"]).exists(), "Training has started")
require(sha(paths.configuration_path) == p["active_configuration_sha256"], "Active config changed")
numeric = read(base / "numeric_acceptance.json")
compatibility = read(base / "compatibility/validation.json")
expectations = read(base / "target_expectations.json")
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
require(numeric["status"] == compatibility["status"] == "PASS", "Input validation incomplete")
require(
    numeric["source_sha256"]
    == compatibility["source_sha256"]
    == expectations["source_sha256"]
    == sources,
    "Accepted runtime identities differ",
)
require(expectations["status"] == "FROZEN_BEFORE_TRAINING", "Fit expectations incomplete")
require(
    expectations["numeric_acceptance_sha256"] == sha(base / "numeric_acceptance.json"),
    "Fit expectations bind different input acceptance",
)
require(
    len(expectations["records"])
    == 2 * (p["retraining"]["windows"] + p["retraining"]["include_latest"]),
    "Fit stage inventory differs",
)
expected_keys = {
    "reference_rows_sha256",
    "fit_rows_sha256",
    "target_sha256",
    "weights_sha256",
    "feature_matrix_sha256",
}
require(
    all(expected_keys <= record.keys() for record in expectations["records"]),
    "Missing fit matrix or target identity",
)
updates = {
    "bundle": p["snapshot_output"] + "/bundle.joblib",
    "evaluation": p["evaluation_output"],
    "historical_status_review": p["checks_output"] + "/status_review.json",
    "protocol": pfile.relative_to(root).as_posix(),
}
lines = []
for line in paths.configuration_path.read_text(encoding="utf-8").splitlines():
    name = line.split("=", 1)[0].strip()
    lines.append(f'{name} = "{updates[name]}"' if name in updates else line)
configuration = "\n".join(lines) + "\n"
parsed = tomllib.loads(configuration)
require(parsed == {**paths.config, **updates}, "Unregistered configuration change")
target = root / p["configuration"]
with target.open("x", encoding="utf-8", newline="\n") as output:
    output.write(configuration)
save(
    base / "fit_preparation.json",
    {
        "status": "FROZEN_BEFORE_TRAINING",
        "created_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "data_sha256": numeric["bundle_sha256"],
        "configuration_sha256": sha(target),
        "numeric_acceptance_sha256": sha(base / "numeric_acceptance.json"),
        "target_expectations_sha256": sha(base / "target_expectations.json"),
        "stages": len(expectations["records"]),
        "fits": p["retraining"]["fits"],
        "active_configuration_unchanged": True,
        "historical_status_review_pending_new_account_checks": True,
        "scope": "No label/feature equality or monotonic cohort assumption. The isolated experiment uses independently accepted inputs; fresh account/status checks remain required before activation or a live paper plan.",
    },
    immutable=True,
)
manifest = read(paths.manifest)
for file in [target, *sorted(base.glob("*.py")), base / "fit_preparation.json"]:
    key, value = file.relative_to(root).as_posix(), sha(file)
    require(
        key not in manifest["files"] or manifest["files"][key] == value,
        "Frozen input changed: " + key,
    )
    manifest["files"][key] = value
save(paths.manifest, manifest)
print("ISOLATED_EXPERIMENT_PREPARED", p["configuration"], flush=True)
