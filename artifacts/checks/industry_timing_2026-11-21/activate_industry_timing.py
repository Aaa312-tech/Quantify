"""Activate the dated research input only after every preregistered comparison passes."""
import json
import subprocess
from pathlib import Path

from quantfolio.dataset import check_integrity
from quantfolio.planning import freeze_plan, source_hashes
from quantfolio.reporting import build_report
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / 'configs/protocols/industry_timing.json'
p = read(protocol_file)
checks = root / p['checks_output']
attribution = read(checks / 'attribution.json')
closure = read(checks / 'implementation/source_guard_closure.json')
snapshot = read(checks / 'snapshot_review.json')
sources = {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert attribution['status'] == 'COMPLETE' and closure['status'] == snapshot['status'] == 'PASS'
paper_closure = read(checks / 'paper_missing_quote/closure.json')
assert paper_closure['status'] == 'PASS'
assert sources == attribution['source_sha256'] == paper_closure['source_sha256']
original_sources = paper_closure['before_source_sha256']
assert original_sources == closure['source_sha256'] == snapshot['source_sha256']
assert attribution['fits'] == attribution['models_reloaded'] == 144
assert attribution['retrained_accounts'] == attribution['fixed_forecast_accounts'] == 4275
assert attribution['paper_accounts'] == 225
assert paths.config['bundle'] == p['source_bundle']
assert sha(paths.bundle) == p['source_bundle_sha256']
old_configuration = sha(paths.configuration_path)
baseline = json.loads(subprocess.check_output(['git', 'show', p['baseline_commit'] + ':configs/integrity_manifest.json'], text=True, encoding='utf-8'))
assert len(baseline['files']) == p['baseline_frozen_files'] == 23809
for name, value in baseline['files'].items():
    assert sha(root / name) == value, name
status_protocol = read(root / 'configs/protocols/historical_status_repair.json')
for variant, setting in p['variants'].items():
    folder = root / setting['checks_output']
    training = read(folder / 'training_review.json')
    accounts = read(folder / 'account_review.json')
    validation = read(folder / 'validation.json')
    assert training['status'] == accounts['status'] == validation['status'] == 'PASS'
    assert sources == accounts['source_sha256'] == validation['source_sha256']
    assert original_sources == training['source_sha256'] == validation['training_source_sha256']
    assert validation['active_configuration_unchanged']
    assert training['warning_reference_occurrences'] == training['warning_fit_occurrences'] == 0
    assert training['unknown_industry_training_rows'] == training['warning_inner_buys'] == 0
    assert all(item['warning_buy_occurrences'] == 0 for item in accounts['arms'].values())
    save(folder / 'status_review.json', {
        'status': 'REGISTERED_STATUS_REPAIR_VERIFIED', 'blocking': False, 'reviewed_at': now(),
        'source_bundle_sha256': validation['data_sha256'], 'variant': variant,
        'registration_commit': attribution['registration_commit'],
        'previous_review': paths.config['historical_status_review'],
        'previous_review_sha256': sha(root / paths.config['historical_status_review']),
        'source_review': status_protocol['review'], 'source_review_sha256': status_protocol['review_sha256'],
        'corrected_securities': 10, 'warning_sessions': 2755,
        'remaining_reviewed_warning_training_rows': 0, 'remaining_reviewed_warning_buy_occurrences': 0,
        'models_reloaded': 48, 'new_account_audits': 1425, 'fixed_forecast_account_audits': 1425,
        'paper_accounts_exact': 75,
        'evidence': {name: sha(folder / name) for name in ('validation.json', 'training_review.json', 'account_review.json')},
        'scope': 'Rechecks only the ten previously sourced warning intervals on this industry dataset, all fit/reference rows, and all inner and outer buys. Industry source completeness does not certify other security status history.',
        'limitations': attribution['limitations'],
    }, immutable=True)

def freeze_outputs():
    manifest = read(paths.manifest)
    folders = [checks, root / 'artifacts/evaluations/industry_timing_parity_2026-11-21']
    for setting in p['variants'].values():
        folders.extend([root / setting['training_output'], root / setting['evaluation_output']])
    for folder in folders:
        for file in sorted(f for f in folder.rglob('*') if f.is_file()):
            relative, value = file.relative_to(root).as_posix(), sha(file)
            assert relative not in manifest['files'] or manifest['files'][relative] == value, relative
            manifest['files'][relative] = value
    save(paths.manifest, manifest)

freeze_outputs()
dated = p['variants']['dated']
paths.configuration_path.write_bytes((root / dated['configuration']).read_bytes())
paths = Paths(root)
plan_dir = 'artifacts/plans/dated_industries_2026-11-23'
freeze_plan(paths, 'artifacts/accounts/forward_2026-11-23/account.json', plan_dir)
plan_file = root / plan_dir / 'plan.json'
plan = read(plan_file)
assert plan['status'] == 'FROZEN_CONDITIONAL_PLAN' and not plan['blockers']
assert plan['identity'] == digest({key: value for key, value in plan.items() if key != 'identity'})
assert plan['source_sha256'] == source_hashes()
assert plan['signal_date'] == '2026-11-20' and plan['execution_date'] == '2026-11-23'
for name, value in plan['input_sha256'].items():
    assert sha(root / name) == value
signal = attribution['arms']['dated']['latest_signal']
assert plan['input_sha256'][dated['training_output'] + '/final_fit/outer_predictions.npz'] == signal['forecast_sha256']
if signal['cost_scenarios']['10bps']['passes_new_buy_edge_filter'] == 0:
    assert not plan['orders']
save(checks / 'activation.json', {
    'status': 'ACTIVATED_FOR_RESEARCH', 'activated_at': now(), 'variant': 'dated',
    'configuration_sha256': sha(paths.configuration_path), 'previous_configuration_sha256': old_configuration,
    'source_sha256': sources, 'protocol_sha256': sha(protocol_file),
    'bundle': paths.config['bundle'], 'bundle_sha256': sha(paths.bundle), 'data_cutoff': p['source_data_cutoff'],
    'training': dated['training_output'], 'identity': attribution['arms']['dated']['identity'],
    'evaluation': dated['evaluation_output'], 'status_review': paths.config['historical_status_review'],
    'attribution_sha256': sha(checks / 'attribution.json'), 'unchanged_baseline_files': 23809,
    'plan': plan_file.relative_to(root).as_posix(), 'plan_sha256': sha(plan_file),
    'orders': len(plan['orders']), 'expires_at': plan['expires_at'],
    'selection_mode': 'positive_score', 'turnover_policy': 'legacy', 'risk': p['risk'],
    'historical_availability_certified': False, 'superiority_assessment': 'NOT_ESTABLISHED',
    'forward_observations_added': 0, 'limitations': attribution['limitations'],
    'scope': 'Dated research input adopted after complete engineering checks, independently of return direction. Coverage and static controls remain attribution-only. Remote publication is verified separately.'
}, immutable=True)
freeze_outputs()
print(check_integrity(paths), flush=True)
print(build_report(paths), flush=True)
for variant, setting in p['variants'].items():
    print(build_report(Paths(root, setting['configuration']), setting['evaluation_output']), flush=True)
print('DATED INDUSTRY RESEARCH INPUT ACTIVATED', plan['intent']['decision'], flush=True)
