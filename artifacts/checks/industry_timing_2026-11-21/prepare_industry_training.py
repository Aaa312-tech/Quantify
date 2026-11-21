from pathlib import Path

from quantfolio.dataset import check_integrity
from quantfolio.settings import Paths
from quantfolio.storage import read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_path = root / 'configs/protocols/industry_timing.json'
protocol = read(protocol_path)
checks = root / protocol['checks_output']
assert read(checks / 'snapshot_review.json')['status'] == 'PASS'
assert read(checks / 'implementation/source_guard_closure.json')['status'] == 'PASS'
active = paths.configuration_path.read_text(encoding='utf-8')
manifest = read(paths.manifest)
for variant, setting in protocol['variants'].items():
    target = root / setting['configuration']
    assert not target.exists()
    text = active
    updates = {'bundle': setting['snapshot_output'] + '/bundle.joblib',
               'evaluation': setting['evaluation_output'],
               'protocol': protocol_path.relative_to(root).as_posix(),
               'historical_status_review': setting['checks_output'] + '/status_review.json'}
    for key, value in updates.items():
        old = f'{key} = "{paths.config[key]}"'
        assert text.count(old) == 1
        text = text.replace(old, f'{key} = "{value}"')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8', newline='\n')
    manifest['files'][setting['configuration']] = sha(target)
for filename in ('audit_industry_snapshots.py', 'run_industry_training.py', 'prepare_industry_training.py'):
    output = checks / filename
    assert not output.exists()
    output.write_bytes((root / '.git' / filename).read_bytes())
for folder in (checks, root / 'artifacts/evaluations/industry_timing_parity_2026-11-21'):
    for file in sorted(f for f in folder.rglob('*') if f.is_file()):
        relative, value = file.relative_to(root).as_posix(), sha(file)
        assert relative not in manifest['files'] or manifest['files'][relative] == value
        manifest['files'][relative] = value
save(paths.manifest, manifest)
print(check_integrity(paths))
print('THREE ISOLATED CONFIGURATIONS READY; ACTIVE CONFIGURATION UNCHANGED')
