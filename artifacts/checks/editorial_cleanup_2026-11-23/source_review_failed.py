import ast
import collections
import hashlib
import io
import json
import subprocess
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path.cwd()
output = root / 'artifacts/checks/editorial_cleanup_2026-11-23'
output.mkdir(exist_ok=False)
baseline = '19e6f668af572dae4e850c0eeeca293d7d7afafe'
local = root / '.local/maintenance/editorial_cleanup_2026-11-23'
before = json.loads((local / 'before.json').read_text(encoding='utf-8-sig'))


def executable(source):
    tree = ast.parse(source)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node) is not None:
                doc = node.body.pop(0)
                spans.append((doc.lineno, doc.end_lineno))
    tokens = []
    excluded = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING,
                tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in excluded:
            continue
        if token.type == tokenize.STRING and any(a <= token.start[0] <= b for a, b in spans):
            continue
        tokens.append((token.type, token.string))
    return ast.dump(tree, include_attributes=False), tokens


source_hashes = {}
changed = []
body_groups = collections.defaultdict(list)
for path in sorted((root / 'src/quantfolio').glob('*.py')):
    relative = path.relative_to(root).as_posix()
    old = subprocess.check_output(['git', 'show', f'{baseline}:{relative}'])
    new = path.read_bytes()
    if executable(old.decode('utf-8')) != executable(new.decode('utf-8')):
        raise ValueError(f'Executable source differs: {relative}')
    checksum = hashlib.sha256(new).hexdigest()
    source_hashes[path.name] = checksum
    if hashlib.sha256(old).hexdigest() != before['source_sha256'][path.name]:
        raise ValueError(f'Baseline source differs: {relative}')
    if old != new:
        changed.append(relative)
    for node in ast.walk(ast.parse(new.decode('utf-8'))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body[1:] if ast.get_docstring(node) is not None else node.body
            if len(body) >= 4:
                key = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
                body_groups[key].append(f'{relative}:{node.lineno}:{node.name}')

config_hashes = {}
for name, expected in before['config_sha256'].items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f'Configuration changed: {name}')
    config_hashes[name] = actual

old_manifest = json.loads((local / 'manifest_before.json').read_text(encoding='utf-8-sig'))
current_manifest = json.loads((root / 'configs/integrity_manifest.json').read_text(encoding='utf-8'))
if old_manifest != current_manifest:
    raise ValueError('Integrity manifest changed before validation')
if 'AGENTS.md' in old_manifest['files']:
    raise ValueError('Removed file was frozen evidence')

review = {
    'schema_version': 1,
    'status': 'PASS',
    'completed_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'baseline_commit': baseline,
    'runtime_modules': len(source_hashes),
    'changed_runtime_modules': changed,
    'source_sha256': source_hashes,
    'executable_ast_identical': True,
    'non_documentation_tokens_identical': True,
    'config_sha256': config_hashes,
    'baseline_frozen_entries': len(old_manifest['files']),
    'baseline_manifest_unchanged': True,
    'duplicate_body_scan': {
        'minimum_top_level_statements': 4,
        'method': 'Exact AST equality after removing function docstrings',
        'matches': [group for group in body_groups.values() if len(group) > 1],
    },
    'review': {
        'single_call_helpers': 'Retained unit conversion, independent reconciliation, date selection, serialization and path accessors.',
        'maintained_names': 'No iteration-number modules or generator attribution found in runtime, scripts, examples and maintained documentation.',
        'agent_instructions': 'AGENTS.md moved to Windows Recycle Bin; current maintenance rules are in CONTRIBUTING.md and docs/repository.md.',
        'historical_records': 'Repeated historical instructions linked to the original Git revision; frozen research records are unchanged.',
    },
    'editorial_changes': json.loads((local / 'editorial_changes.json').read_text(encoding='utf-8-sig')),
}
(output / 'source_review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
(output / 'check_source.py').write_bytes(Path(__file__).read_bytes())
print(json.dumps({'runtime_modules':len(source_hashes), 'changed':len(changed), 'edits':len(review['editorial_changes']), 'ast_and_tokens':'PASS', 'configs':len(config_hashes), 'baseline_frozen_entries':len(old_manifest['files'])}))
