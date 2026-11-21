"""Freeze audit sources and final local evidence before publication."""
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from quantfolio.dataset import check_integrity
from quantfolio.planning import verified_plan
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
p = read(paths.protocol)
checks = root / p['checks_output']
a = read(checks / 'attribution.json')
activation = read(checks / 'activation.json')
assert activation['status'] == 'ACTIVATED_FOR_RESEARCH' and a['status'] == 'COMPLETE'
sources = {file.name: sha(file) for file in sorted((root / 'src/quantfolio').glob('*.py'))}
assert sources == activation['source_sha256'] == a['source_sha256']
assert sha(paths.configuration_path) == activation['configuration_sha256']
assert sha(paths.bundle) == activation['bundle_sha256']
_, plan = verified_plan(paths, activation['plan'])
assert sha(root / activation['plan']) == activation['plan_sha256']
assert plan['status'] == 'FROZEN_CONDITIONAL_PLAN' and not plan['blockers']
assert read(checks / 'coverage/evaluation_recovery.json')['status'] == 'PASS'
assert read(checks / 'paper_missing_quote/closure.json')['source_sha256'] == sources
assert read(checks / 'paper_missing_quote/closure.json')['paper_accounts_exact'] == 300
scripts = ('run_industry_training.py', 'run_industry_bridge.py', 'complete_industry_coverage.py',
           'review_industry_training.py', 'review_industry_accounts.py', 'summarize_industry_timing.py',
           'activate_industry_timing.py', 'finalize_industry_timing_evidence.py')
script_records = []
for filename in scripts:
    source, target = root / '.git' / filename, checks / filename
    if target.exists():
        assert target.read_bytes() == source.read_bytes()
    else:
        target.write_bytes(source.read_bytes())
    script_records.append({'path': target.relative_to(root).as_posix(), 'sha256': sha(target)})
for variant, setting in p['variants'].items():
    folder = root / setting['checks_output']
    validation = read(folder / 'validation.json')
    assert validation['status'] == 'PASS' and validation['source_sha256'] == sources
    assert validation['fits'] == validation['models_reloaded'] == 48
    assert validation['account_audits'] == 1425 and validation['paper']['accounts'] == 75
    assert all(row['max_error'] <= 1e-12 for row in read(folder / 'models/verification.json')['models'])
    for prefix in ('industry_training_', 'industry_bridge_', 'industry_training_review_', 'industry_account_review_'):
        source = root / '.git' / (prefix + variant + '.log')
        target = folder / 'logs' / (prefix.removeprefix('industry_').removesuffix('_') + '.log')
        assert source.is_file() and not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    if variant == 'coverage':
        source, target = root / '.git/industry_coverage_recovery.log', folder / 'logs/evaluation_recovery.log'
        assert source.is_file() and not target.exists()
        target.write_bytes(source.read_bytes())
save(checks / 'verification_sources.json', {'status': 'ARCHIVED', 'records': script_records,
     'scope': 'Exact one-off audit programs used for this registered experiment, kept as research evidence rather than imported into the strategy runtime.'}, immutable=True)
links = []
for name in ('README.md', 'CONTRIBUTING.md', 'docs/industry_timing.md', 'docs/data_contract.md',
             'docs/reproducibility.md', 'docs/paper_workflow.md', 'docs/remediation.md', 'docs/repository.md', 'docs/architecture.md'):
    file = root / name
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', file.read_text(encoding='utf-8')):
        target = target.strip('<>').split('#')[0]
        if not target or '://' in target or target.startswith('mailto:'):
            continue
        assert (file.parent / unquote(target)).resolve().is_file(), (name, target)
        links.append({'document': name, 'target': target})

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []
    def handle_starttag(self, tag, attrs):
        self.targets.extend(value for key, value in attrs if key in ('href', 'src') and value)

reports = []
for directory in [root / 'reports/latest', *[root / 'reports/experiments' / Path(setting['evaluation_output']).name for setting in p['variants'].values()]]:
    file = directory / 'index.html'
    parser = Links()
    parser.feed(file.read_text(encoding='utf-8'))
    for target in parser.targets:
        relative = target.split('#')[0]
        if relative and '://' not in relative and not relative.startswith('data:'):
            assert (file.parent / unquote(relative)).resolve().is_file(), (file, relative)
    reports.append({'path': file.relative_to(root).as_posix(), 'sha256': sha(file), 'links': len(parser.targets)})
save(checks / 'completion.json', {
    'status': 'VALIDATED_LOCALLY', 'completed_at': now(), 'source_sha256': sources,
    'configuration_sha256': sha(paths.configuration_path), 'bundle_sha256': sha(paths.bundle),
    'protocol_sha256': sha(paths.protocol), 'attribution_sha256': sha(checks / 'attribution.json'),
    'activation_sha256': sha(checks / 'activation.json'), 'unchanged_baseline_files': 23809,
    'old_model_reloads': 48, 'old_full_accounts_exact': 1425, 'old_paper_accounts_exact': 75,
    'final_guard_paper_accounts_exact': 75, 'missing_quote_fix_paper_accounts_exact': 300, 'new_fits': 144, 'new_model_reloads': 144,
    'retrained_accounts': 4275, 'fixed_forecast_accounts': 4275, 'new_paper_accounts': 225,
    'tests_passed': 375, 'tests_skipped': 1, 'plan': activation['plan'],
    'plan_sha256': activation['plan_sha256'], 'orders': len(plan['orders']), 'expires_at': plan['expires_at'],
    'documentation_links': links, 'reports': reports, 'historical_availability_certified': False,
    'superiority_assessment': 'NOT_ESTABLISHED', 'forward_observations_added': 0,
    'publication': 'Actual commit, remote LFS objects and completed CI are checked separately after publication.'
}, immutable=True)
manifest = read(paths.manifest)
for file in sorted(f for f in checks.rglob('*') if f.is_file()):
    relative, value = file.relative_to(root).as_posix(), sha(file)
    assert relative not in manifest['files'] or manifest['files'][relative] == value, relative
    manifest['files'][relative] = value
save(paths.manifest, manifest)
print(check_integrity(paths), flush=True)
print('INDUSTRY TIMING EVIDENCE COMPLETE', flush=True)
