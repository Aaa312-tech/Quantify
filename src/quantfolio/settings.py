"""Portable project paths, independent of the original drive and working directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def project_root(explicit: str | Path | None = None) -> Path:
    """Resolve an explicit root, QUANTFOLIO_ROOT, or a nearby project marker."""
    selected = explicit or os.environ.get("QUANTFOLIO_ROOT")
    if selected:
        candidates = [Path(selected).expanduser().resolve()]
    else:
        cwd = Path.cwd().resolve()
        source = Path(__file__).resolve().parents[2]
        candidates = [cwd, *cwd.parents, source]
    for candidate in candidates:
        marker = candidate / "pyproject.toml"
        if marker.is_file() and 'name = "quantfolio-research"' in marker.read_text("utf-8"):
            return candidate
    raise FileNotFoundError("Project not found. Use --project PATH or set QUANTFOLIO_ROOT.")


@dataclass(frozen=True)
class Paths:
    """The single retained experiment and its portable inputs."""

    root: Path

    @property
    def bundle(self) -> Path:
        return self.root / "data/processed/2026-11-18/bundle.joblib"

    @property
    def reference(self) -> Path:
        return self.root / "artifacts/v6_2026-11-19"

    @property
    def manifest(self) -> Path:
        return self.root / "configs/integrity_manifest.json"

    def new_output(self, path: str | Path, category: str) -> Path:
        """Require a new, separate output directory under the stated category."""
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        target = target.resolve()
        parent = (self.root / "artifacts" / category).resolve()
        if not target.is_relative_to(parent) or target == parent:
            raise ValueError(f"Output must be a named directory under {parent}")
        if target.exists():
            raise FileExistsError(f"Choose a new experiment name; output exists: {target}")
        target.mkdir(parents=True)
        return target
