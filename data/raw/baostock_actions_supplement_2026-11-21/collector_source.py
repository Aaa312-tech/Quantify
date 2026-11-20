import contextlib
import importlib.metadata
import inspect
import io
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path('.git/status_deps').resolve()))
import baostock as provider

from quantfolio.planning import register_output
from quantfolio.settings import Paths
from quantfolio.storage import digest, dump_gzip, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
target = root / 'data/raw/baostock_actions_supplement_2026-11-21'
assert not target.exists()
assert importlib.metadata.version('baostock') == '0.9.4'
queue = read(root / 'data/raw/disclosures_2026-11-21/queue.json')
parent = read(root / 'data/raw/disclosures_2026-11-21/protocol.json')
assert digest(queue) == parent['queue_sha256']
assert sha(paths.bundle) == parent['bundle_sha256']
from quantfolio.dataset import load_bundle
bundle = load_bundle(paths)
original_collection = root / 'data/raw/baostock_actions_2026-11-21/collection.json'
original = read(original_collection)
assert original['status'] == 'COLLECTED' and original['source_bundle_sha256'] == sha(paths.bundle)
already_collected = {(r['symbol'], r['year']) for r in original['requests'] if r['status'] == 'RECEIVED'}
all_supported = [a for a in bundle['actions'] if a['symbol'].startswith(('sh60', 'sh68', 'sz00', 'sz30'))]
requests = sorted({(a['symbol'], a['ex_date'][:4]) for a in all_supported} - already_collected)
supported = [a for a in all_supported if (a['symbol'], a['ex_date'][:4]) in requests]
unsupported = []
target.mkdir(parents=True)
source = target / 'collector_source.py'
source.write_bytes(Path(__file__).read_bytes())
fields = ['code', 'dividPreNoticeDate', 'dividAgmPumDate', 'dividPlanAnnounceDate',
          'dividPlanDate', 'dividRegistDate', 'dividOperateDate', 'dividPayDate',
          'dividStockMarketDate', 'dividCashPsBeforeTax', 'dividCashPsAfterTax',
          'dividStocksPs', 'dividCashStock', 'dividReserveToStockPs']
protocol = {
    'registered_at': now(), 'provider': 'baostock', 'provider_version': '0.9.4',
    'package_reference': 'https://pypi.org/project/baostock/0.9.4/',
    'query': 'query_dividend_data', 'year_type': 'operate',
    'provider_implementation_sha256': sha(inspect.getfile(provider.query_dividend_data)),
    'collector_sha256': sha(source), 'source_bundle_sha256': parent['bundle_sha256'],
    'source_queue_sha256': parent['queue_sha256'],
    'selection': 'Every remaining Shanghai/Shenzhen stock-year in the entire frozen corporate-action inventory, including known dates; subtract only successfully collected issuer-years, without consulting holdings or returns.',
    'prior_collection': {'path': original_collection.relative_to(root).as_posix(), 'sha256': sha(original_collection)},
    'all_inventory_sha256': digest(bundle['actions']), 'all_supported_events': len(all_supported),
    'supported_events': len(supported), 'unsupported_events': unsupported,
    'requests': [{'symbol': symbol, 'year': year} for symbol, year in requests],
    'max_requests': len(requests), 'request_spacing_seconds': 0.15,
    'socket_timeout_seconds': 20, 'stop_on_first_provider_error': True, 'retries': 0,
    'fields': fields,
    'scope': 'Unaltered SDK strings for cross-source review. Repeated rows are preserved; missing fields are not filled. No facts are applied and no historical publication time is inferred.',
}
save(target / 'protocol.json', protocol, immutable=True)
completed = []
failure = None
old_timeout = socket.getdefaulttimeout()
socket.setdefaulttimeout(20)
try:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        login = provider.login()
    if login.error_code != '0':
        raise RuntimeError(login.error_msg)
    for number, (symbol, year) in enumerate(requests, 1):
        code = symbol[:2] + '.' + symbol[2:]
        record = {'requested_at': now(), 'symbol': symbol, 'code': code, 'year': year, 'year_type': 'operate'}
        log = io.StringIO()
        try:
            with contextlib.redirect_stdout(log):
                response = provider.query_dividend_data(code=code, year=year, yearType='operate')
                rows = []
                while response.error_code == '0' and response.next():
                    rows.append(response.get_row_data())
            record.update(error_code=response.error_code, error_msg=response.error_msg,
                          fields=response.fields, rows=rows, provider_output=log.getvalue())
            if response.error_code != '0' or response.fields != fields:
                raise ValueError('Provider error or changed field contract')
            if any(len(row) != len(fields) or row[0] != code for row in rows):
                raise ValueError('Malformed row or a different requested security')
            record['status'] = 'RECEIVED'
        except (OSError, ValueError, RuntimeError) as error:
            record.update(status='FAILED', error=f'{type(error).__name__}: {error}', provider_output=log.getvalue())
            failure = {'symbol': symbol, 'year': year, 'error': record['error']}
        record['completed_at'] = now()
        file = target / 'histories' / f'{symbol}_{year}.json.gz'
        dump_gzip(file, record)
        completed.append({'symbol': symbol, 'year': year, 'status': record['status'],
                          'path': file.relative_to(root).as_posix(), 'sha256': sha(file),
                          'rows': len(record.get('rows', []))})
        if number % 100 == 0 or failure:
            print({'completed': number, 'total': len(requests), 'failure': failure}, flush=True)
        if failure:
            break
        time.sleep(0.15)
except (OSError, ValueError, RuntimeError) as error:
    failure = {'error': f'{type(error).__name__}: {error}'}
finally:
    with contextlib.redirect_stdout(io.StringIO()):
        provider.logout()
    socket.setdefaulttimeout(old_timeout)

assert sha(paths.bundle) == protocol['source_bundle_sha256']
summary = {
    'status': 'COLLECTED' if failure is None and len(completed) == len(requests) else 'PARTIAL',
    'completed_at': now(), 'source_bundle_sha256': protocol['source_bundle_sha256'],
    'source_queue_sha256': protocol['source_queue_sha256'], 'requested': len(requests),
    'completed': len(completed), 'received': sum(r['status'] == 'RECEIVED' for r in completed),
    'rows': sum(r['rows'] for r in completed), 'requests': completed, 'failure': failure,
    'supported_events': len(supported), 'unsupported_events': len(unsupported),
    'facts_applied': 0, 'quality_flags_cleared': 0,
    'scope': 'Supplement to cover every supported issuer-year in the full inventory; primary notices remain necessary and unsupported markets remain unresolved.',
}
save(target / 'collection.json', summary, immutable=True)
register_output(paths, target)
print({key: value for key, value in summary.items() if key != 'requests'}, flush=True)
