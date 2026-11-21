"""Close the paper-source dependency after a constant-only guard extension."""
import ast
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.evaluation import load_predictions
from quantfolio.planning import source_hashes
from quantfolio.settings import Paths
from quantfolio.storage import digest, load_gzip, now, read, save, sha
from quantfolio.validation import execution_audit, reconcile
from quantfolio.verification import _paper_account

root = Path.cwd()
paths = Paths(root)
protocol = read(root / 'configs/protocols/industry_timing.json')
checks = root / protocol['checks_output'] / 'implementation'
old = read(checks / 'validation.json')
current = {f.name: sha(f) for f in sorted((root / 'src/quantfolio').glob('*.py'))}
assert {name for name in current if current[name] != old['source_sha256'][name]} == {'planning.py'}
before = root / '.git/planning_before_guard.py'
assert sha(before) == old['source_sha256']['planning.py']
trees = [ast.parse(f.read_text(encoding='utf-8')) for f in (before, root / 'src/quantfolio/planning.py')]
values = []
for tree in trees:
    binding = [node for node in tree.body if isinstance(node, ast.Assign) and
               any(isinstance(target, ast.Name) and target.id == 'SOURCE_MODULES' for target in node.targets)]
    assert len(binding) == 1
    values.append(ast.literal_eval(binding[0].value))
    tree.body.remove(binding[0])
assert ast.dump(trees[0]) == ast.dump(trees[1])
assert values[1] == (*values[0], 'validation')
assert 'validation' in source_hashes()
test_log = root / '.git/industry_timing_guard_checks.log'
assert b'373 passed, 1 skipped' in test_log.read_bytes()
bundle = load_bundle(paths)
run = root / protocol['baseline_run']
maps, schedule, _, _, _ = load_predictions(bundle, run, identity=read(run / 'training_status.json')['identity'])
records = []
for cost in (10, 20, 30):
    for name in (f'evaluation/{cost}bps/selected_strategy.ledgers.json.gz', f'evaluation/continuous/{cost}bps/selected_strategy.json.gz'):
        stored = load_gzip(root / protocol['baseline_evaluation'] / name)
        for index, reference in enumerate(stored if isinstance(stored, list) else [stored]):
            expected = {key: reference[key] for key in ('summary', 'ledger')}
            actual, count = _paper_account(bundle, maps['selected'], schedule, expected, cost / 10000, turnover='legacy')
            audit = execution_audit(actual['ledger'], bundle)
            recon = reconcile(actual['ledger'], actual['summary'], bundle['meta'], bundle['actions'])
            assert actual == expected and audit['status'] == recon['status'] == 'PASS'
            records.append({'path': name, 'account_index': index, 'full_account_equal': True,
                            'expected_sha256': digest(expected), 'actual_sha256': digest(actual),
                            'paper_fill_checks': count, 'execution_audit': audit, 'reconciliation': recon})
assert len(records) == 75
assert current == {f.name: sha(f) for f in sorted((root / 'src/quantfolio').glob('*.py'))}
for source, destination in [(before, checks / 'planning_before_guard.py'),
                            (root / '.git/replay_industry_baseline.py', checks / 'replay_source.py'),
                            (Path(__file__), checks / 'source_guard_closure_source.py'),
                            (test_log, checks / 'source_guard_checks.log')]:
    assert not destination.exists()
    destination.write_bytes(source.read_bytes())
save(checks / 'source_guard_closure.json', {
    'status': 'PASS', 'completed_at': now(), 'source_sha256': current,
    'parent_validation_sha256': sha(checks / 'validation.json'),
    'only_change': 'Add validation.py to planning.SOURCE_MODULES; all other AST nodes and package modules are exact.',
    'inherited_model_reloads': 48, 'inherited_full_accounts_exact': 1425,
    'paper_accounts_rerun_exact': 75, 'paper_records': records,
    'tests_passed': 373, 'tests_skipped': 1, 'tests_sha256': sha(checks / 'source_guard_checks.log'),
    'scope': 'The full numerical baseline proof is retained. Only paper identity binding changed, and all 75 paper accounts were rerun under the final source. Ordinary stale-source rejection is preserved.'
}, immutable=True)
print('FINAL SOURCE GUARD AND 75 PAPER ACCOUNTS PASSED', flush=True)
