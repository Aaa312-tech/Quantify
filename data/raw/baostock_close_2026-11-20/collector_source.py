import contextlib
import importlib.metadata
import io
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path('.git/status_deps').resolve()))
import baostock as provider

from quantfolio.settings import Paths
from quantfolio.storage import dump_gzip, now, save, sha

root = Path.cwd()
paths = Paths(root)
folder = root / 'data/raw/baostock_close_2026-11-20'
assert not folder.exists()
assert importlib.metadata.version('baostock') == '0.9.4'
source = Path(__file__)
folder.mkdir(parents=True)
(folder / 'collector_source.py').write_bytes(source.read_bytes())
save(folder / 'protocol.json', {
    'registered_at': now(), 'provider': 'baostock', 'provider_version': '0.9.4',
    'target_date': '2026-11-20', 'source_bundle_sha256': sha(paths.bundle),
    'provider_reference': 'https://baostock.com/mainContent?file=DailyUpdates.md',
    'package_reference': 'https://pypi.org/project/baostock/0.9.4/',
    'implementation_sha256': sha(root / '.git/status_deps/baostock/security/history.py'),
    'collector_sha256': sha(folder / 'collector_source.py'),
    'requests': ['query_daily_history_k_AStock', 'query_daily_history_k_ETF'],
    'max_requests': 2, 'socket_timeout_seconds': 20, 'request_spacing_seconds': 0.5,
    'retries': 0, 'stop_on_first_provider_error': True,
    'purpose': 'Source review of the full target-date response against frozen raw history, all existing primary bars and the failed closing-quote comparison. No parser gate is relaxed and no new data are activated by collection.',
    'scope': 'Retrospective completed daily quotes, not evidence of availability before that day opening.',
    'activated': False,
}, immutable=True)
messages, records, failure = io.StringIO(), [], None
previous_timeout = socket.getdefaulttimeout()
socket.setdefaulttimeout(20)
logged_in = False
try:
    with contextlib.redirect_stdout(messages):
        login = provider.login()
    if login.error_code != '0':
        raise ValueError(f'Provider login failed: {login.error_code} {login.error_msg}')
    logged_in = True
    for kind, method in (('stocks', provider.query_daily_history_k_AStock), ('funds', provider.query_daily_history_k_ETF)):
        started = now()
        with contextlib.redirect_stdout(messages):
            response = method(date='2026-11-20')
            rows = []
            while response.error_code == '0' and response.next():
                rows.append(response.get_row_data())
        payload = {'requested_at': started, 'retrieved_at': now(), 'kind': kind, 'fields': response.fields,
                   'rows': rows, 'error_code': response.error_code, 'error_msg': response.error_msg}
        file = folder / f'{kind}.json.gz'
        dump_gzip(file, payload)
        record = {'kind': kind, 'requested_at': started, 'retrieved_at': payload['retrieved_at'],
                  'rows': len(rows), 'fields': response.fields, 'error_code': response.error_code,
                  'response_sha256': sha(file), 'response': file.relative_to(root).as_posix()}
        records.append(record)
        print(record, flush=True)
        if response.error_code != '0':
            failure = f'{response.error_code}: {response.error_msg}'
            break
        time.sleep(.5)
except (OSError, ValueError, TypeError, KeyError) as exc:
    failure = f'{type(exc).__name__}: {exc}'
finally:
    if logged_in:
        try:
            with contextlib.redirect_stdout(messages):
                provider.logout()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            failure = failure or f'Logout {type(exc).__name__}: {exc}'
    socket.setdefaulttimeout(previous_timeout)
    result = {'status': 'COMPLETE' if len(records) == 2 and failure is None else 'PARTIAL',
              'completed_at': now(), 'failure': failure, 'records': records,
              'provider_messages': messages.getvalue(), 'activated': False}
    save(folder / 'collection.json', result, immutable=True)
print('Collection', result['status'], failure, flush=True)
