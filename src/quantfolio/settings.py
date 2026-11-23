"""Project paths and output locations."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import cached_property
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
        if marker.is_file():
            with marker.open("rb") as stream:
                if tomllib.load(stream).get("project", {}).get("name") == "quantfolio-research":
                    return candidate
    raise FileNotFoundError("Project not found. Use --project PATH or set QUANTFOLIO_ROOT.")


@dataclass(frozen=True)
class Paths:
    """Project-relative inputs and isolated output directories."""

    root: Path
    config_file: str | Path = "configs/project.toml"

    def __post_init__(self):
        object.__setattr__(self, "root", self.root.resolve())

    @cached_property
    def configuration_path(self) -> Path:
        relative = Path(self.config_file)
        target = (self.root / relative).resolve()
        parent = (self.root / "configs").resolve()
        if (
            relative.is_absolute()
            or not parent.is_relative_to(self.root)
            or not target.is_relative_to(parent)
            or target.suffix != ".toml"
        ):
            raise ValueError("Configuration must be a TOML file inside project configs")
        return target

    @cached_property
    def config(self) -> dict:
        with self.configuration_path.open("rb") as stream:
            return tomllib.load(stream)

    @cached_property
    def archive(self) -> dict:
        with self._configured_path("archive").open("rb") as stream:
            return tomllib.load(stream)

    def _configured_path(self, name: str) -> Path:
        relative = Path(self.config[name])
        target = (self.root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(self.root):
            raise ValueError(f"Configured {name} must stay inside the project")
        return target

    @property
    def bundle(self) -> Path:
        return self._configured_path("bundle")

    @property
    def reference_bundle(self) -> Path:
        return (
            self._configured_path("reference_bundle")
            if "reference_bundle" in self.config
            else self.bundle
        )

    @property
    def reference(self) -> Path:
        return self._configured_path("reference")

    @property
    def protocol(self) -> Path:
        return self._configured_path("protocol")

    @property
    def benchmark(self) -> Path:
        return self._configured_path("benchmark")

    @property
    def manifest(self) -> Path:
        return self._configured_path("manifest")

    def artifact_path(self, path: str | Path, category: str) -> Path:
        """Resolve an artifact path, including symlinks, inside its project category."""
        if category not in {"checks", "replays", "experiments", "evaluations", "accounts", "plans"}:
            raise ValueError(f"Unknown artifact category: {category}")
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        target = target.resolve()
        parent = (self.root / "artifacts" / category).resolve()
        if (
            not parent.is_relative_to(self.root)
            or not target.is_relative_to(parent)
            or target == parent
        ):
            raise ValueError(f"Output must be a named directory under {parent}")
        return target

    def output_path(self, path: str | Path, category: str) -> Path:
        """Resolve an unused output directory within the selected category."""
        target = self.artifact_path(path, category)
        if target.exists():
            raise FileExistsError(f"Choose a new experiment name; output exists: {target}")
        return target
