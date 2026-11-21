import ast
import subprocess
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_snapshot, validate_features
from quantfolio.ledger import causal_price_index
from quantfolio.preprocessing import compare_history
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
file = root/'configs/protocols/gap_precision.json'
p = read(file)
checks = root/p['checks_output']
old = load_snapshot(paths, root/p['source_bundle'], p['source_data_cutoff'])
new_file = root/p['snapshot_output']/'bundle.joblib'
new = load_snapshot(paths, new_file, p['source_data_cutoff'])
parent = read(checks/'parent_rebuild.json')
assert parent['status'] == 'PASS' and parent['data_sha256'] == p['source_bundle_sha256']
assert parent['protocol_sha256'] == sha(file)
for name, value in parent['source_sha256'].items():
    content = subprocess.check_output(['git','show',p['baseline_commit']+':src/quantfolio/'+name])
    import hashlib
    assert hashlib.sha256(content).hexdigest() == value, name
source = subprocess.check_output(['git','show',p['baseline_commit']+':src/quantfolio/ledger.py'],text=True,encoding='utf-8')
tree = ast.parse(source)
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'causal_price_index')
namespace = {'pd': pd, 'np': np}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'<frozen causal_price_index>','exec'), namespace)
previous_index = namespace['causal_price_index']
assert old['actions'] == new['actions'] and old['opening_status'] == new['opening_status']
comparison = dict(new, table=new['table'].copy())
for name in ('gap','rank_gap'):
    pd.testing.assert_series_equal(old['table'][name].isna(), new['table'][name].isna(), check_exact=True)
    comparison['table'][name] = old['table'][name]
unchanged = compare_history(old, comparison)
grouped = defaultdict(list)
for event in old['actions']:
    grouped[event['symbol']].append(event)
maximum_error = maximum_stored_difference = 0.0
observations = same_price = event_rows = mapped_events = outside_events = 0
records = []
for symbol, raw in old['raws'].items():
    actions = grouped[symbol]
    prior, old_splits = previous_index(raw, actions)
    actual, splits = causal_price_index(raw, actions)
    pd.testing.assert_frame_equal(prior, actual.drop(columns='opening_gap'), check_exact=True)
    pd.testing.assert_series_equal(old_splits, splits, check_exact=True)
    events = defaultdict(list)
    for event in sorted(actions,key=lambda x:x['ex_date']):
        ex = pd.Timestamp(event['ex_date'])
        quotes = [d for d in raw.index if d >= ex]
        if raw.index[0] <= ex <= raw.index[-1] and quotes:
            events[quotes[0]].append(event)
            mapped_events += 1
        else:
            outside_events += 1
    expected = np.full(len(raw), np.nan)
    for i, current in enumerate(raw.itertuples()):
        if i == 0:
            assert pd.isna(actual.opening_gap.iloc[i])
            continue
        quantity, cash = Fraction(1), Fraction(0)
        for event in events.get(current.Index, []):
            cash += quantity * Fraction(str(event['cash_per_share']))
            quantity *= Fraction(str(event['share_multiplier']))
        close = Fraction(str(current.close))
        previous = Fraction(str(raw.close.iloc[i-1]))
        wealth = quantity * close + cash
        reference = float((wealth / previous) * Fraction(str(current.open)) / close - 1)
        value = actual.opening_gap.iloc[i]
        error = abs(value - reference)
        assert error <= 16 * np.finfo(float).eps * max(1,abs(reference)), (symbol,current.Index,error)
        expected[i] = value
        maximum_error = max(maximum_error, error)
        event_rows += bool(events.get(current.Index))
        if not events.get(current.Index) and current.open == raw.close.iloc[i-1]:
            same_price += 1
            assert value == 0.0, (symbol,current.Index,value)
        observations += 1
    stored = new['table'].loc[new['table'].symbol.eq(symbol)]
    series = pd.Series(expected, index=raw.index).reindex(new['calendar']).astype('float32')
    np.testing.assert_array_equal(stored.gap.to_numpy(), series.to_numpy())
    old_values = old['table'].loc[old['table'].symbol.eq(symbol),'gap'].to_numpy()
    maximum_stored_difference = max(maximum_stored_difference,float(np.nanmax(np.abs(old_values-series.to_numpy()))))
    records.append({'symbol':symbol,'quoted_rows':len(raw),'same_price_non_event_rows':sum((raw.open==raw.close.shift()) & ~raw.index.isin(events)),'mapped_event_dates':len(events)})

table = new['table']
expected_rank = np.full(len(table), np.nan,dtype='float32')
for _, group in table.groupby('date_index',sort=False):
    usable = group.loc[group.eligible & group.gap.notna(),'gap']
    ordered = np.sort(usable.to_numpy())
    lower = np.searchsorted(ordered,usable.to_numpy(),side='left')
    upper = np.searchsorted(ordered,usable.to_numpy(),side='right')
    if len(ordered):
        expected_rank[usable.index] = ((lower+1+upper)/2/len(ordered)-0.5).astype('float32')
np.testing.assert_array_equal(expected_rank,table.rank_gap.to_numpy())

# Re-run the previously diagnosed SF correction with the local formula only.
before_action_file = root/'data/processed/2026-11-20-reviewed-resumption/bundle.joblib'
before = load_snapshot(paths,before_action_file,p['source_data_cutoff'])
symbol = 'sz002352'
old_actions = [a for a in before['actions'] if a['symbol']==symbol]
a,_ = causal_price_index(old['raws'][symbol],old_actions)
b,_ = causal_price_index(old['raws'][symbol],grouped[symbol])
changed = ~(a.opening_gap.eq(b.opening_gap) | (a.opening_gap.isna() & b.opening_gap.isna()))
assert list(a.index[changed]) == [pd.Timestamp('2024-11-07')]
differences = {name:int((~(old['table'][name].eq(new['table'][name]) | (old['table'][name].isna() & new['table'][name].isna()))).sum()) for name in ('gap','rank_gap')}
result = {
    'status':'PASS','checked_at':now(),'protocol_sha256':sha(file),'bundle_sha256':sha(new_file),
    'source_sha256':{f.name:sha(f) for f in sorted((root/'src/quantfolio').glob('*.py'))},
    'parent_exact_rebuild':parent,'unaffected_fields_exact':unchanged,
    'adjusted_price_and_split_series_exact':len(records),'raw_gap_reference_observations':observations,
    'reference':'Independent rational entitlement wealth, converted through raw open/close; no production event aggregator used.',
    'maximum_float64_reference_error':maximum_error,'same_price_non_event_exact_zero':same_price,
    'event_rows':event_rows,'mapped_events':mapped_events,'events_outside_observed_interval':outside_events,
    'all_stored_gaps_exact_to_local_float32':True,'all_ranks_exact_to_independent_order_statistics':True,
    'changed_rows':differences,'maximum_stored_gap_difference':maximum_stored_difference,
    'historical_entitlement_correction_changed_local_gap_dates':['2024-11-07'],
    'historical_entitlement_correction_changes_no_later_local_gaps':True,
    'feature_check':validate_features(new),'records':records,
}
save(checks/'snapshot_review.json',result,immutable=True)
print({k:v for k,v in result.items() if k not in ('source_sha256','records','parent_exact_rebuild')},flush=True)
