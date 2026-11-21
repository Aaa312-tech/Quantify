"""Recover an output-directory collision without weakening normal evaluator safeguards."""
import logging
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.verification import verify_paper

logging.basicConfig(level=logging.INFO, format='%(message)s')
root = Path.cwd().resolve()
pfile = root / 'configs/protocols/industry_timing.json'
p = read(pfile)
setting = p['variants']['coverage']
paths = Paths(root, setting['configuration'])
checks = root / setting['checks_output']
run = root / setting['training_output']
evaluation = (root / setting['evaluation_output']).resolve()
staging = (root / 'artifacts/evaluations/industry_coverage_staging_2026-11-21').resolve()
parent = (root / 'artifacts/evaluations').resolve()
assert evaluation.parent == staging.parent == parent and parent.is_relative_to(root)
assert evaluation != staging and not staging.exists()
assert [file.name for file in evaluation.iterdir()] == ['fixed_forecasts']
sources = {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
execution = read(checks / 'execution_protocol.json')
assert sources == execution['source_sha256']
assert sha(root / 'configs/project.toml') == execution['active_configuration_sha256']
training = read(run / 'training_status.json')
assert training['status'] == 'COMPLETE'
assert read(checks / 'models/verification.json')['models_reloaded'] == 48
assert read(evaluation / 'fixed_forecasts/summary.json')['accounts'] == 1425
original_files = {file.relative_to(evaluation).as_posix(): sha(file) for file in evaluation.rglob('*') if file.is_file()}
failure_log = root / '.git/industry_training_coverage.log'
assert b'FileExistsError' in failure_log.read_bytes()
save(checks / 'evaluation_recovery_protocol.json', {
    'started_at': now(), 'source_sha256': sources, 'failure_log_sha256': sha(failure_log),
    'reason': 'The fixed-forecast run created its registered parent before the normal evaluation reached its fresh-directory guard. The normal evaluator correctly refused to overwrite it.',
    'staging': staging.relative_to(root).as_posix(), 'original_files': original_files,
    'procedure': 'Move the bridge directory as-is inside the project, evaluate into the now-absent registered target using the normal evaluator, rerun paper checks, and restore every staged file with exact hashes. No source or model changes, no guard bypass, no repeated fitting.'
}, immutable=True)
# Both resolved move endpoints were checked above, before moving their contents.
evaluation.rename(staging)
result = evaluate(paths, run, evaluation, [10, 20, 30], all_policies=True)
assert result['accounts'] == 1425
paper = verify_paper(paths, checks / 'paper')
assert paper['status'] == 'PASS' and paper['accounts'] == 75
bridge_source = (staging / 'fixed_forecasts').resolve()
bridge_target = (evaluation / 'fixed_forecasts').resolve()
assert bridge_source.is_relative_to(parent) and bridge_target.is_relative_to(parent)
assert not bridge_target.exists()
bridge_source.rename(bridge_target)
staging.rmdir()  # Empty staging directory only; no recursive deletion.
for relative, checksum in original_files.items():
    assert sha(evaluation / relative) == checksum, relative
assert sources == {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sha(root / 'configs/project.toml') == execution['active_configuration_sha256']
save(checks / 'training_validation.json', {
    'status': 'PASS', 'completed_at': now(), 'variant': 'coverage', 'source_sha256': sources,
    'configuration_sha256': sha(paths.configuration_path), 'data_sha256': sha(paths.bundle),
    'protocol_sha256': sha(pfile), 'identity': training['identity'], 'models_reloaded': 48,
    'fits': 48, 'account_audits': 1425, 'paper': paper,
    'evaluation_sha256': sha(evaluation / 'evaluation_summary.json'),
    'active_configuration_unchanged': True, 'activated': False,
    'scope': 'Original full training and model reloads retained. Ordinary evaluation and paper validation completed after a documented directory-collision recovery; no guards were relaxed.',
    'limitations': p['limitations']
}, immutable=True)
save(checks / 'evaluation_recovery.json', {
    'status': 'PASS', 'completed_at': now(), 'source_sha256': sources,
    'protocol_sha256': sha(checks / 'evaluation_recovery_protocol.json'),
    'restored_files_exact': len(original_files), 'staging_removed_when_empty': not staging.exists(),
    'failed_training_log_sha256': sha(failure_log), 'models_reused_without_refit': 48,
    'account_audits': 1425, 'paper_accounts': 75
}, immutable=True)
print('COVERAGE EVALUATION RECOVERED; ALL PRIOR BRIDGE FILES EXACT', flush=True)
