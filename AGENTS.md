# Project maintenance

The user authorized publishing this project to `https://github.com/Aaa312-tech/Quantify.git`
and keeping it updated after substantive work. Validate, commit, push and check the actual
GitHub Actions result. Do not ask again for this scope or create a scheduled task.

## Workflow

- Work on `main`. Fetch and inspect local/remote changes first; preserve others' work.
  Never force-push or overwrite unexpected history.
- Follow `CONTRIBUTING.md`. Run Ruff and relevant pytest tests. Numerical/execution changes
  also require frozen model/ledger parity checks; documentation alone does not need refitting.
- Keep frozen inputs and experiment files unchanged. Check `configs/integrity_manifest.json`
  before publishing. New experiments get new named directories.
- Track code, configuration, docs, data, models, reports and research evidence. Ignore credentials,
  environments, caches and build outputs. Keep `*.joblib` in LFS and verify changed objects on remote.
  Preserve `.gitattributes` newline rules. Recycled obsolete versions are outside the sync scope.
- Update affected docs and CHANGELOG. Keep research verification summaries; Git history preserves obsolete machine cleanup logs. Do not commit local recycle-bin inventories or one-off cleanup scripts. Report the
  commit link, local checks and actual CI conclusion; queued checks are not passing checks.

## Research constraints

- Maintain the active `quantfolio` package and walk-forward momentum strategy unless a research task changes it.
  Balanced attack, the 8% alert and non-recovering risk remain in force.
- Name strategies by behavior, not iteration numbers. Keep old identifiers only where required
  to read or trace frozen evidence; use semantic software versions for package releases.
- The superiority gate is false. Engineering parity does not prove outperformance; data ends
  on 2026-11-18 until a verified new snapshot is added.
- Do not operate a trading app, broker account or WeChat.
