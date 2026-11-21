"""Adapt the frozen indexer's immutable-output request to the existing storage API."""

import gzip
import json

import index_notice_amendments as indexer

from quantfolio.storage import _atomic_write, native_scalar, now, save, sha


def write_once(path, value, *, immutable):
    if immutable is not True:
        raise ValueError("This source index must be written once")
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=native_scalar
    ).encode("utf-8")
    _atomic_write(path, gzip.compress(encoded, mtime=0), immutable=True)


def main():
    outputs = [
        indexer.BASE / name
        for name in (
            "notice_amendment_index.json.gz",
            "title_amendment_review.json",
            "notice_amendment_index.json",
        )
    ]
    if any(path.exists() for path in outputs):
        raise ValueError("Index output already exists")
    indexer.dump_gzip = write_once
    indexer.main()
    save(
        indexer.BASE / "notice_index_write_closure.json",
        {
            "status": "PASS",
            "completed_at": now(),
            "adapter_sha256": sha(__file__),
            "indexer_sha256": sha(indexer.__file__),
            "original_api_failure_sha256": sha(indexer.BASE / "notice_amendment_index.log"),
            "storage_sha256": sha("src/quantfolio/storage.py"),
            "method": "Only the compressed writer is adapted. It uses the same finite JSON and "
            "deterministic gzip encoding, then exclusively links the completed file. "
            "The executed indexer, source interpretation and runtime storage remain unchanged.",
            "outputs": {p.as_posix(): sha(p) for p in outputs},
            "financial_acceptance": False,
        },
        immutable=True,
    )


if __name__ == "__main__":
    main()
