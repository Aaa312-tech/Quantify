# Project maintenance

## Current scope

- The user paused the research goal on 2026-11-22 after the body-context review. Complete repository consolidation and maintenance; do not resume data collection, alpha experiments, retraining or forward observation without an explicit request to resume.
- Read [current status](docs/status.md) for active outputs and pending work. [Remediation](docs/remediation.md) is a paused backlog, not an instruction to run it.
- The user authorizes validated publication to `https://github.com/Aaa312-tech/Quantify.git`. Work on `main`, fetch and inspect changes first, preserve others' work, and never force-push. No scheduled task is authorized.
- Do not operate a trading app, broker account or WeChat. Confirmed competition rules follow the whitepaper; fees and matching remain unresolved.

## Changes and verification

- Follow `CONTRIBUTING.md`. Code belongs in `src/quantfolio`, tests in `tests`; use behavioral strategy names and semantic software versions.
- Run `scripts/check.ps1` for code changes. Numerical, timing or execution changes also require the registered model, forecast and full-ledger checks in [validation](docs/validation.md). Documentation-only changes do not require refitting.
- Never edit frozen files or executed audit scripts, including failed checks. Create new named outputs; before adding a file, verify that its path is unused. Do not regenerate hashes to hide a byte change.
- Preserve the existing integrity-manifest entries. A requested path migration must retain original hashes, register aliases in `configs/archive.toml` and validate all consumers.
- Before publishing, verify frozen-file integrity. Track code, configuration, research data, models, reports and evidence; exclude credentials, environments, caches and temporary scripts. Keep `.gitattributes` and Git LFS conventions.
- Update affected docs and CHANGELOG. After pushing, inspect the actual three-job GitHub Actions outcome and independently verify changed remote Git/LFS bytes. Queued jobs are not passing checks.

## Research invariants

- Balanced attack, 8% drawdown warning, original high-water mark and latched/non-recovering risk remain in force. Active adoption is `positive_score`; turnover is `legacy`; stock cap is 15% of total capital, below the whitepaper's 20% limit.
- The registered alpha-candidate budget is exhausted. Do not add same-sample candidates, relax gates or choose data treatments by return. No superiority over same-risk momentum or forward-return claim is established.
- `configs/project.toml` selects the recipient-entitlement snapshot, training/evaluation lineage and historical-status review. This consolidation changes none of them.
- Preserve all 1452 accepted source records and 1530 supported fields. The 95-event/115-field proposals and eight extra listing proposals remain unaccepted; 387 events remain source-unverified. Keyword hits, clean extraction and catalogue-period matches do not accept financial facts.
- Latest source checkpoint: `artifacts/checks/distribution_residuals_2026-11-22/body_context_review.json`. Its 172-document/333-context scope is not a complete review of the 1135-document archive. Keep its six narrative-label corrections, six whitespace-span differences, two repeated-sentence page differences and all prior failures.
- Preserve raw numeric whitespace, instrument roles, negations, conditional terms, source conflicts and unknown dates. A conversion suspension is not a stock suspension; declared resumption is not an observed opening. Do not invent bars, cash dates, fills or account rounding.
- Historical universe/publication timing, 111 gap endpoints, complete ETF economic exposure, competition matching and forward evidence remain open. Never normalize partial ETF reports or label source collection as financial acceptance.
- The frozen November 23 paper plan has zero orders and expires at 09:25 Shanghai time. Never extend it by editing its date or fabricate forward executions.

## Historical evidence

[Research history](docs/research_history.md) preserves the former maintenance constraints and stage records. Read the relevant stage and its registered protocol before changing that scope. Old statuses describe their original boundary; the current paused status takes precedence over historical instructions to continue.

Use [repository maintenance](docs/repository.md) for directory ownership and [project status](docs/status.md) for canonical result paths. Keep new progress in the appropriate document instead of appending another historical narrative here.
