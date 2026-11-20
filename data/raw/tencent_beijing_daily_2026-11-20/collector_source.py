import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
bundle = load_bundle(paths)
folder = root / 'data/raw/tencent_beijing_daily_2026-11-20'
assert not folder.exists()
folder.mkdir(parents=True)
(folder / 'collector_source.py').write_bytes(Path(__file__).read_bytes())
symbols = sorted(s for s in bundle['meta'] if s.startswith('bj'))
assert len(symbols) == 26
prior = root / 'artifacts/checks/market_close_2026-11-20/cross_source/protocol.json'
save(folder / 'protocol.json', {
    'registered_at': now(), 'target_date': '2026-11-20', 'start': '2026-11-13',
    'provider': 'tencent_daily_bars', 'symbols': symbols, 'selection': 'All 26 Beijing securities in the frozen universe, including both missing primary target bars.',
    'source_bundle_sha256': sha(paths.bundle), 'collector_sha256': sha(folder / 'collector_source.py'),
    'existing_endpoint_protocol': prior.relative_to(root).as_posix(), 'existing_endpoint_protocol_sha256': sha(prior),
    'request_spacing_seconds': .5, 'timeout_seconds': 15, 'retries': 0,
    'stop_on_first_provider_error': True,
    'purpose': 'Corroborate complete target OHLCV and historical overlap for the Beijing segment. Provider independence is not claimed between Tencent daily bars and Tencent closing quotes.',
    'activated': False,
}, immutable=True)
records, failure = [], None
for symbol in symbols:
    parameters = {'_var': 'kline_day2026', 'param': f'{symbol},day,2026-11-13,2026-11-20,100,', 'r': '0.8205512681390605'}
    url = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?' + urlencode(parameters)
    record = {'symbol': symbol, 'requested_at': now(), 'url': url}
    try:
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15) as response:
            content = response.read()
        file = folder / f'{symbol}.txt'
        file.write_bytes(content)
        payload = json.loads(content.decode('utf-8').split('=', 1)[1].rstrip(';\n '))
        record['response_sha256'] = sha(file)
        if payload.get('code') != 0:
            raise ValueError(f"Provider error {payload.get('code')}: {payload.get('msg')}")
        rows = payload['data'][symbol]['day']
        if not rows or not all(len(row) >= 6 for row in rows):
            raise ValueError('Empty or malformed daily rows')
        record.update(status='RECEIVED', rows=len(rows), target_present=any(r[0] == '2026-11-20' for r in rows), retrieved_at=now())
    except (OSError, ValueError, KeyError, TypeError) as error:
        failure = f'{type(error).__name__}: {error}'
        record.update(status='FAILED', error=failure, completed_at=now())
    records.append(record)
    print(symbol, record['status'], record.get('target_present'), flush=True)
    if failure:
        break
    time.sleep(.5)
save(folder / 'collection.json', {'status': 'COMPLETE' if len(records) == len(symbols) and failure is None else 'PARTIAL',
                                 'completed_at': now(), 'failure': failure, 'records': records, 'activated': False}, immutable=True)
