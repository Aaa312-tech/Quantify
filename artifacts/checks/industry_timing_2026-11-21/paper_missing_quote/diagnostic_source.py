from pathlib import Path
from quantfolio.dataset import load_bundle
from quantfolio.evaluation import first_difference, load_predictions
from quantfolio.settings import Paths
from quantfolio.storage import dump_gzip, load_gzip, read, save
from quantfolio.verification import _paper_account

root = Path.cwd()
paths = Paths(root, 'configs/experiments/industry_dated.toml')
p = read(root / 'configs/protocols/industry_timing.json')['variants']['dated']
bundle = load_bundle(paths)
maps, schedule, _, _, _ = load_predictions(bundle, root / p['training_output'])
expected = load_gzip(root / p['evaluation_output'] / 'evaluation/10bps/selected_strategy.ledgers.json.gz')[12]
actual, checks = _paper_account(bundle, maps['selected'], schedule, expected, .001)
folder = root / 'artifacts/checks/industry_timing_2026-11-21/paper_missing_quote'
assert not folder.exists()
dump_gzip(folder / 'actual.json.gz', actual)
dump_gzip(folder / 'expected.json.gz', expected)
record = {key: first_difference(expected['ledger'][key], actual['ledger'][key]) for key in expected['ledger']}
save(folder / 'differences.json', record, immutable=True)
print(record)
print('EXPECTED', expected['ledger']['rejections'])
print('ACTUAL', actual['ledger']['rejections'])
print('PERIOD', expected['summary']['start'], expected['summary']['end'])
