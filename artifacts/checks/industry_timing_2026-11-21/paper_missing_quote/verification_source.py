"""Recheck complete paper accounts after the missing-quote representation correction."""
import ast
import logging
import sys
from pathlib import Path

from quantfolio.dataset import load_bundle, load_snapshot
from quantfolio.evaluation import load_predictions
from quantfolio.settings import Paths
from quantfolio.storage import digest, load_gzip, now, read, save, sha
from quantfolio.validation import execution_audit, reconcile
from quantfolio.verification import _paper_account, verify_paper

logging.basicConfig(level=logging.INFO, format='%(message)s')
root = Path.cwd()
p = read(root / 'configs/protocols/industry_timing.json')
checks = root / p['checks_output']
fix = checks / 'paper_missing_quote'
registration = read(fix / 'protocol.json')
sources = {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert {key for key in sources if sources[key] != registration['before_source_sha256'][key]} == {'planning.py'}
trees = [ast.parse(file.read_text(encoding='utf-8')) for file in (fix / 'planning_before_fix.py', root / 'src/quantfolio/planning.py')]
addition = ast.dump(ast.parse('if price is None:\n    raws[symbol] = raws[symbol].drop(index=execution)').body[0])
removed = 0
for node in ast.walk(trees[1]):
    if not isinstance(getattr(node, 'body', None), list):
        continue
    matching = [child for child in node.body if ast.dump(child) == addition]
    for child in matching:
        node.body.remove(child)
        removed += 1
assert removed == 1 and ast.dump(trees[0]) == ast.dump(trees[1])
assert b'375 passed, 1 skipped' in (root / '.git/industry_paper_quote_checks.log').read_bytes()
variant = sys.argv[1]
if variant == 'baseline':
    paths = Paths(root)
    bundle = load_snapshot(paths, root / p['source_bundle'], p['source_data_cutoff'])
    run = root / p['baseline_run']
    maps, schedule, _, _, _ = load_predictions(bundle, run, identity=read(run / 'training_status.json')['identity'])
    records = []
    for cost in (10, 20, 30):
        for name in (f'evaluation/{cost}bps/selected_strategy.ledgers.json.gz', f'evaluation/continuous/{cost}bps/selected_strategy.json.gz'):
            stored = load_gzip(root / p['baseline_evaluation'] / name)
            for index, expected in enumerate(stored if isinstance(stored, list) else [stored]):
                expected = {key: expected[key] for key in ('summary', 'ledger')}
                actual, count = _paper_account(bundle, maps['selected'], schedule, expected, cost / 10000)
                audit = execution_audit(actual['ledger'], bundle)
                recon = reconcile(actual['ledger'], actual['summary'], bundle['meta'], bundle['actions'])
                assert actual == expected and audit['status'] == recon['status'] == 'PASS'
                records.append({'path': name, 'account_index': index, 'full_account_equal': True,
                                'expected_sha256': digest(expected), 'actual_sha256': digest(actual),
                                'paper_fill_checks': count, 'execution_audit': audit, 'reconciliation': recon})
    assert len(records) == 75
    save(fix / 'baseline/verification.json', {'status': 'PASS', 'completed_at': now(), 'source_sha256': sources,
         'accounts': 75, 'records': records, 'scope': 'Explicit unchanged old-input diagnostic; no claim that the old training used current execution code.'}, immutable=True)
else:
    setting = p['variants'][variant]
    paths = Paths(root, setting['configuration'])
    result = verify_paper(paths, root / setting['checks_output'] / 'paper_quote_review')
    assert result['status'] == 'PASS' and result['accounts'] == 75
assert sources == {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sha(root / 'configs/project.toml') == registration['active_configuration_sha256']
print('COMPLETE PAPER QUOTE PARITY PASSED', variant, 75, flush=True)
