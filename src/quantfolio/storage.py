"""Atomic JSON, deterministic compressed ledgers and SHA-256 artifact identities."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np


def now() -> str:
    """Return a timestamp with the research project's UTC+8 offset."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()


def native_scalar(value: Any) -> Any:
    """Convert numpy scalar values while refusing silently lossy serialization."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported persisted value: {type(value).__name__}")


def read(path: str | Path) -> Any:
    """Read UTF-8 JSON without pickle execution."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path: str | Path) -> str:
    """Stream a file into SHA-256 without loading large bundles into memory."""
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value: Any) -> str:
    """Hash canonical, finite JSON content."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=native_scalar,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def array_sha(value: np.ndarray) -> str:
    """Preserve the original V6 array identity convention."""
    return hashlib.sha256(value.tobytes()).hexdigest()


def save(path: str | Path, value: Any, immutable: bool = False) -> None:
    """Atomically write finite JSON, optionally refusing changes to an existing file."""
    path = Path(path)
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, allow_nan=False, default=native_scalar
    )
    if immutable and path.exists():
        if read(path) != json.loads(encoded):
            raise ValueError(f"Immutable artifact already exists: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(encoded + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def dump_gzip(path: str | Path, value: Any) -> None:
    """Write a deterministic compressed ledger (gzip modification time is zero)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=native_scalar
    ).encode("utf-8")
    path.write_bytes(gzip.compress(encoded, mtime=0))


def load_gzip(path: str | Path) -> Any:
    """Load a compressed JSON ledger."""
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def log(message: str, **fields: Any) -> None:
    """Emit structured progress through standard logging, without mutating reference runs."""
    logging.getLogger("quantfolio").info(
        json.dumps(
            {"at": now(), "message": message, **fields}, ensure_ascii=False, default=native_scalar
        )
    )
