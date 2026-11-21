"""Execute one preregistered data control without changing the active configuration."""
import logging
import sys
from pathlib import Path

from quantfolio.dataset import check_integrity, load_bundle
from quantfolio.evaluation import evaluate
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.training import run_training
from quantfolio.verification import verify, verify_paper

logging.basicConfig(level=logging.INFO, format='%(message)s')
root = Path.cwd()
protocol_file = root / 'configs/protocols/industry_timing.json'
protocol = read(protocol_file)
variant = sys.argv[1]
setting = protocol['variants'][variant]
paths = Paths(root, setting['configuration'])
checks = root / setting['checks_output']
run = root / setting['training_output']
evaluation = root / setting['evaluation_output']
snapshot = read(root / protocol['checks_output'] / 'snapshot_review.json')
closure = read(root / protocol['checks_output'] / 'implementation/source_guard_closure.json')
assert snapshot['status'] == closure['status'] == 'PASS'
sources = {f.name: sha(f) for f in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sources == snapshot['source_sha256'] == closure['source_sha256']
assert sha(paths.bundle) == snapshot['variants'][variant]['bundle_sha256']
active_config = sha(root / 'configs/project.toml')
assert read(root / 'configs/integrity_manifest.json')['files'][setting['configuration']] == sha(paths.configuration_path)
assert paths.config['selection_mode'] == 'positive_score' and paths.config['turnover_policy'] == 'legacy'
assert not run.exists() and not evaluation.exists()
check_integrity(paths)
save(checks / 'execution_protocol.json', {
    'started_at': now(), 'variant': variant, 'registration_commit': '6b31ec98d341f505ff9c45a6e9688bb8bea010ca',
    'protocol_sha256': sha(protocol_file), 'source_sha256': sources,
    'configuration_sha256': sha(paths.configuration_path), 'bundle_sha256': sha(paths.bundle),
    'active_configuration_sha256': active_config, 'availability_certified': False,
    'scope': setting['role'], 'superiority_assessment': 'NOT_PERFORMED'
}, immutable=True)
print(f'Training {variant}: 48 fits, unchanged candidates and gates', flush=True)
training = run_training(paths, run, include_latest=True)
models = verify(paths, checks / 'models', run=run)
assert training['status'] == 'COMPLETE' and models['models_reloaded'] == 48
print(f'Evaluating {variant}: 1425 accounts', flush=True)
result = evaluate(paths, run, evaluation, [10, 20, 30], all_policies=True)
assert result['accounts'] == 1425
paper = verify_paper(paths, checks / 'paper')
assert paper['status'] == 'PASS' and paper['accounts'] == 75
assert sources == {f.name: sha(f) for f in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sha(root / 'configs/project.toml') == active_config
save(checks / 'training_validation.json', {
    'status': 'PASS', 'completed_at': now(), 'variant': variant, 'source_sha256': sources,
    'configuration_sha256': sha(paths.configuration_path), 'data_sha256': sha(paths.bundle),
    'protocol_sha256': sha(protocol_file), 'identity': training['identity'],
    'models_reloaded': 48, 'fits': 48, 'account_audits': 1425, 'paper': paper,
    'evaluation_sha256': sha(evaluation / 'evaluation_summary.json'),
    'active_configuration_unchanged': True, 'activated': False,
    'scope': 'Training, model reloading and independent accounts completed. Fixed-forecast attribution, historical-status exposure and final integration reviews are separate required checks.',
    'limitations': protocol['limitations']
}, immutable=True)
print('TRAINING AND ACCOUNTS PASSED', variant, flush=True)
