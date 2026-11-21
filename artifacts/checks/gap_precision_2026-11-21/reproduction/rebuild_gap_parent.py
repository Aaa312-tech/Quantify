import logging
from pathlib import Path

from quantfolio.dataset import check_integrity, load_bundle
from quantfolio.preprocessing import build_dataset, compare_history
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

logging.basicConfig(level=logging.INFO, format='%(message)s')
root = Path.cwd()
paths = Paths(root)
p = read(root / 'configs/protocols/gap_precision.json')
assert sha(paths.bundle) == p['source_bundle_sha256']
source = {s.name: sha(s) for s in sorted((root/'src/quantfolio').glob('*.py'))}
integrity = check_integrity(paths)
bundle = load_bundle(paths)
rebuilt = build_dataset(bundle['meta'], bundle['raws'], bundle['actions'], opening_status=bundle['opening_status'])
result = compare_history(bundle, rebuilt)
assert bundle['actions'] == rebuilt['actions'] and bundle['opening_status'] == rebuilt['opening_status']
assert source == {s.name: sha(s) for s in sorted((root/'src/quantfolio').glob('*.py'))}
save(root/p['checks_output']/'parent_rebuild.json', {
    **result, 'checked_at': now(), 'registration_commit': '298bdcc6a900a23661785a1fc85e222c0aad824e',
    'source_sha256': source, 'data_sha256': sha(paths.bundle),
    'protocol_sha256': sha(root/'configs/protocols/gap_precision.json'), 'integrity': integrity,
}, immutable=True)
print('PARENT EXACT REBUILD PASSED', result, flush=True)
