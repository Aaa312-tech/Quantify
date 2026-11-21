"""Reuse the frozen original-page renderer for the independent inventory findings."""

import importlib.util
from pathlib import Path

from quantfolio.storage import now, save, sha

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "render_financial_review.py"
spec = importlib.util.spec_from_file_location("financial_renderer", SOURCE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
renderer.CHECKS = HERE
renderer.VISUALS = HERE / "visuals"
save(
    HERE / "render_adapter_identity.json",
    {
        "started_at": now(),
        "adapter_sha256": sha(Path(__file__)),
        "original_renderer_sha256": sha(SOURCE),
        "output_override": HERE.as_posix(),
    },
    immutable=True,
)
renderer.main()
