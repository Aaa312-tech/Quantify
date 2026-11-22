"""Atomic JSON, deterministic compressed ledgers and SHA-256 artifact identities."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


def now() -> str:
    """Return a timestamp with the research project's UTC+8 offset."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()


def native_scalar(value: Any) -> Any:
    """Convert a NumPy scalar for JSON encoding."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported persisted value: {type(value).__name__}")


def read(path: str | Path) -> Any:
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
    """Hash arrays using the identity convention recorded in frozen experiments."""
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
    try:
        _atomic_write(path, (encoded + "\n").encode("utf-8"), immutable=immutable)
    except FileExistsError:
        if not immutable:
            raise
        if read(path) != json.loads(encoded):
            raise ValueError(f"Immutable artifact already exists: {path}") from None


def _atomic_write(path: Path, content: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            # Linking a completed file fails atomically if another writer got there first.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def dump_gzip(path: str | Path, value: Any) -> None:
    """Write a deterministic compressed ledger (gzip modification time is zero)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=native_scalar
    ).encode("utf-8")
    _atomic_write(path, gzip.compress(encoded, mtime=0))


def load_gzip(path: str | Path) -> Any:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def log(message: str, **fields: Any) -> None:
    logging.getLogger("quantfolio").info(
        json.dumps(
            {"at": now(), "message": message, **fields}, ensure_ascii=False, default=native_scalar
        )
    )
