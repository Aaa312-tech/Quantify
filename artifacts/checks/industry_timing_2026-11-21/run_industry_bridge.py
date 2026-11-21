"""Replay the fixed preceding arm's forecasts on each registered industry control."""
import logging
import sys
from pathlib import Path

import numpy as np

from quantfolio.dataset import load_bundle, load_snapshot
from quantfolio.evaluation import POLICIES, evaluate_accounts, load_predictions
from quantfolio.feasibility import account_differences
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

logging.basicConfig(level=logging.INFO, format='%(message)s')
root = Path.cwd()
p = read(root / 'configs/protocols/industry_timing.json')
variant = sys.argv[1]
setting = p['variants'][variant]
paths = Paths(root, setting['configuration'])
checks = root / setting['checks_output']
evaluation = root / setting['evaluation_output'] / 'fixed_forecasts'
previous_variant = {'coverage': None, 'static': 'coverage', 'dated': 'static'}[variant]
if previous_variant is None:
    old_file = root / p['source_bundle']
    previous_run = root / p['baseline_run']
    previous_evaluation = root / p['baseline_evaluation']
else:
    previous = p['variants'][previous_variant]
    old_file = root / previous['snapshot_output'] / 'bundle.joblib'
    previous_run = root / previous['training_output']
    previous_evaluation = root / previous['evaluation_output']
assert not evaluation.exists()
status = read(previous_run / 'training_status.json')
registration = read(previous_run / 'protocol.json')
assert status['status'] == 'COMPLETE' and status['identity'] == registration['identity'] == digest(registration['specification'])
assert registration['specification']['data_sha256'] == sha(old_file)
sources = {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sources == read(root / p['checks_output'] / 'snapshot_review.json')['source_sha256']
active_config = sha(root / 'configs/project.toml')
old = load_snapshot(paths, old_file, p['source_data_cutoff'])
bundle = load_bundle(paths)
maps, schedule, _, start, end = load_predictions(old, previous_run, identity=status['identity'])
table = bundle['table']
assert np.array_equal(old['table'].date_index, table.date_index)
assert np.array_equal(old['table'].symbol, table.symbol)
if previous_variant is not None:
    np.testing.assert_array_equal(old['table'].eligible, table.eligible)
eligible = {int(day): set(frame.symbol) for day, frame in table[table.eligible].groupby('date_index')}
removed = []
for name, days in maps.items():
    for day, forecasts in days.items():
        absent = set(forecasts) - eligible.get(day, set())
        removed.extend({'map': name, 'date_index': day, 'symbol': symbol} for symbol in sorted(absent))
        days[day] = {symbol: value for symbol, value in forecasts.items() if symbol not in absent}
if previous_variant is not None:
    assert not removed
del old
save(checks / 'fixed_forecast_protocol.json', {
    'started_at': now(), 'variant': variant, 'forecast_source': previous_variant or 'baseline',
    'source_training_identity': status['identity'], 'forecast_bundle_sha256': sha(old_file),
    'execution_bundle_sha256': sha(paths.bundle), 'source_sha256': sources,
    'protocol_sha256': sha(root / 'configs/protocols/industry_timing.json'),
    'removed_now_ineligible_keys': removed, 'forecasts_sha256': digest(maps),
    'schedule_sha256': digest(schedule), 'signal_start': start, 'signal_end': end,
    'scope': 'Explicit fixed-forecast attribution; no recalibration, refit or source-identity bypass in the ordinary evaluator.'
}, immutable=True)
result = evaluate_accounts(bundle, maps, schedule, start, end, [10, 20, 30], POLICIES, evaluation, turnover='legacy')
assert result['accounts'] == 1425
assert sources == {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sha(root / 'configs/project.toml') == active_config
result.update(status='PASS', completed_at=now(), scope='FIXED_PRECEDING_ARM_FORECAST_ATTRIBUTION',
              forecast_source=previous_variant or 'baseline', source_training_identity=status['identity'],
              forecast_bundle_sha256=sha(old_file), execution_bundle_sha256=sha(paths.bundle), source_sha256=sources)
save(evaluation / 'summary.json', result, immutable=True)
print('FIXED FORECAST ACCOUNTS PASSED', variant, result['accounts'], flush=True)
