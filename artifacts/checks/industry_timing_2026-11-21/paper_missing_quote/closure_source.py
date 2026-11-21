from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.evaluation import experiment_folds
from quantfolio.planning import source_hashes
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
pfile = root / 'configs/protocols/industry_timing.json'
p = read(pfile)
checks = root / p['checks_output']
fix = checks / 'paper_missing_quote'
before = read(fix / 'protocol.json')['before_source_sha256']
current = {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert {name for name in current if current[name] != before[name]} == {'planning.py'}
baseline = read(fix / 'baseline/verification.json')
assert baseline['status'] == 'PASS' and baseline['accounts'] == 75 and baseline['source_sha256'] == current
evidence = {'baseline': sha(fix / 'baseline/verification.json')}
for variant, setting in p['variants'].items():
    folder = root / setting['checks_output']
    paths = Paths(root, setting['configuration'])
    run = root / setting['training_output']
    training, _ = experiment_folds(paths, load_bundle(paths), run)
    assert read(run / 'protocol.json')['specification']['source_sha256'] == before
    models = read(folder / 'models/verification.json')
    evaluation = read(root / setting['evaluation_output'] / 'evaluation_summary.json')
    fixed = read(root / setting['evaluation_output'] / 'fixed_forecasts/summary.json')
    paper_file = folder / 'paper_quote_review/verification.json'
    paper = read(paper_file)
    assert models['status'] == paper['status'] == 'PASS' and models['models_reloaded'] == 48
    assert evaluation['accounts'] == fixed['accounts'] == 1425 and fixed['source_sha256'] == before
    assert evaluation['identity'] == training['identity'] and paper['accounts'] == 75
    assert paper['source_sha256'] == {**source_hashes(), 'verification': current['verification.py']}
    save(folder / 'validation.json', {
        'status': 'PASS', 'completed_at': now(), 'variant': variant, 'source_sha256': current,
        'training_source_sha256': before, 'configuration_sha256': sha(paths.configuration_path),
        'data_sha256': sha(paths.bundle), 'protocol_sha256': sha(pfile), 'identity': training['identity'],
        'models_reloaded': 48, 'fits': 48, 'account_audits': 1425, 'fixed_forecast_accounts': 1425,
        'paper': {key: value for key, value in paper.items() if key != 'records'},
        'original_paper_verification_sha256': sha(folder / 'paper/verification.json'),
        'original_paper_verification_status': read(folder / 'paper/verification.json')['status'],
        'active_configuration_unchanged': True, 'activated': False,
        'scope': 'Original models and ordinary account evaluations retain their exact recorded source identities. Only the paper missing-quote representation changed afterward; all numerical training and historical execution modules remain exact. Every paper account was rerun through the ordinary verifier.',
        'paper_fix_protocol_sha256': sha(fix / 'protocol.json'), 'limitations': p['limitations']
    }, immutable=True)
    evidence[variant] = sha(paper_file)
test_log = root / '.git/industry_paper_quote_checks.log'
assert b'375 passed, 1 skipped' in test_log.read_bytes()
for variant in ('baseline', 'coverage', 'static', 'dated'):
    name = 'baseline_final' if variant == 'baseline' else variant
    source = root / '.git' / ('industry_paper_quote_' + name + '.log')
    target = fix / (variant + '.log')
    assert not target.exists()
    target.write_bytes(source.read_bytes())
for source, target in [(test_log, fix / 'after_tests.log'),
                        (root / '.git/verify_industry_paper_fix.py', fix / 'verification_source.py'),
                        (Path(__file__), fix / 'closure_source.py')]:
    assert not target.exists()
    target.write_bytes(source.read_bytes())
save(fix / 'closure.json', {
    'status': 'PASS', 'completed_at': now(), 'source_sha256': current, 'before_source_sha256': before,
    'protocol_sha256': sha(fix / 'protocol.json'), 'paper_accounts_exact': 300, 'old_paper_accounts': 75,
    'new_paper_accounts': 225, 'verification_sha256': evidence, 'tests_passed': 375, 'tests_skipped': 1,
    'ast_proof': 'The verification program removes exactly the single registered price-is-None row-drop statement; every remaining module AST node equals the pre-fix source. Every other package module is byte-identical.',
    'training_and_historical_execution_unchanged': True,
    'inherited_proofs': {'old_model_reloads': 48, 'old_full_accounts_exact': 1425, 'new_model_reloads': 144,
                         'retrained_accounts': 4275, 'fixed_forecast_accounts': 4275},
    'scope': 'No re-fitting or historical ledger rewrite was needed for a paper-only missing-quote representation fix. Both original failures and all original source identities remain preserved.'
}, immutable=True)
print('PAPER QUOTE FIX CLOSED WITH 300 EXACT ACCOUNTS', flush=True)
