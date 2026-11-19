# Quantify repository maintenance

## Scope and standing user instruction

The user authorized publishing this entire current research project to
`https://github.com/Aaa312-tech/Quantify.git` and maintaining that repository going forward.
After completing substantive work in this project, validate it, commit the relevant changes,
push them to the configured repository, and check the resulting GitHub Actions run.
Do not stop at proposing a plan or ask for the same publication authorization again.
This applies to project work performed for the user; it is not a request for a scheduled task.

## Repository and files

- The primary branch is `main`. Inspect the actual branch and remote before any push.
- Fetch first and inspect remote changes. Preserve other people's work; never force-push,
  delete remote history, or overwrite unexpected remote files as part of routine sync.
- Track source, configurations, tests, documentation, data, models, reports and research evidence.
  `.gitignore` excludes only credentials, local environments, caches and packaging outputs.
- Keep `*.joblib` in Git LFS. Check that large objects have actually uploaded; a pointer alone
  is insufficient. Never put access tokens, passwords or private keys in files or remote URLs.
- Preserve `.gitattributes` rules that disable newline conversion for frozen evidence.
  The original bytes must match `configs/integrity_manifest.json` after a fresh clone.
- Recycled obsolete versions are outside the active project and are not part of the sync scope.

## Research constraints

- Maintain only the active `quantfolio` package and the retained V6 strategy unless a new
  research task explicitly changes the version. Balanced attack and the 8% drawdown alert remain.
- Preserve frozen V6 inputs and experiment records. New experiments use new named directories.
- Never claim an engineering replay proves investment outperformance. The current superiority
  gate is false, and data still ends on 2026-11-18 until a verified new snapshot is introduced.
- Do not operate a trading app, broker account or WeChat.

## Validation and reporting

- Follow `CONTRIBUTING.md`; use Ruff formatting/lint and the relevant pytest tests.
- For numerical or execution changes, also run the frozen verification/replay needed to show
  whether behavior changed. Pure documentation edits do not require retraining.
- Check input integrity before publishing research artifacts. For changed LFS files, verify
  their availability from the remote repository.
- Update README, CHANGELOG and the affected methodology/reproduction documents when behavior
  changes. Keep previous dated maintenance records as historical evidence.
- Report the commit link, local validation result and actual CI result. If a check fails,
  investigate and fix it within scope; never describe queued or unrun CI as passing.
