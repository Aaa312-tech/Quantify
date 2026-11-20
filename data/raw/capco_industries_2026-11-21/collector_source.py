"""Archive declared releases from CAPCO's public article data, without executing JavaScript."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from quantfolio.disclosures import _capture
from quantfolio.planning import register_output
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = {'url': dict(attrs).get('href', '').strip(), 'title': ''}

    def handle_data(self, data):
        if self.current is not None:
            self.current['title'] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None


root = Path.cwd()
target = root / 'data/raw/capco_industries_2026-11-21'
assert not target.exists()
index = read(root / '.git/industry_capco_index.json')
selected = [row for row in index if re.fullmatch(r'202[3-6]年[上下]半年上市公司行业分类结果', row['title'])]
selected += [row for row in index if row['article_path'] == 'zsxfb/hyfl/a36d94097a6d41f4bd2f72122c623e8d.js']
target.mkdir(parents=True)
source = target / 'collector_source.py'
source.write_bytes(Path(__file__).read_bytes())
protocol = {
    'registered_at': now(), 'collector_sha256': sha(source),
    'source_bundle_sha256': sha(Paths(root).bundle), 'articles': selected,
    'site_bundle_url': 'https://www.capco.org.cn/assets/index-DAeop0xZ.js',
    'selection': 'All semiannual industry releases from 2023 through the research cutoff, plus their classification guideline. Collect stock-code order and code-alias attachments; no holdings or returns used.',
    'scope': 'Retrospectively archived official documents. Declared publication dates and storage paths do not establish original intraday availability. No industry assignments are activated.',
}
save(target / 'protocol.json', protocol, immutable=True)
index_receipt = _capture(root, target / 'site_index.js', protocol['site_bundle_url'])
save(target / 'site_index_receipt.json', index_receipt, immutable=True)
assert index_receipt['status'] == 'RECEIVED'
records = []
for article in selected:
    lower = article['published_date']
    folder = target / lower
    url = 'https://www.capco.org.cn/dataFile/' + article['article_path']
    receipt = _capture(root, folder / 'article.txt', url)
    item = {**article, 'article': receipt, 'attachments': [], 'status': 'FAILED'}
    if receipt['status'] == 'RECEIVED':
        try:
            raw = (root / receipt['path']).read_text('utf-8')
            match = re.fullmatch(r'\s*export const allData\s*=\s*(\{.*\})\s*;?\s*', raw, re.DOTALL)
            if not match:
                raise ValueError('Unexpected article data format')
            data = json.loads(match.group(1))
            if data['title'] != article['title'] or data['date'] != lower:
                raise ValueError('Article and declared index identity differ')
            parser = Links()
            parser.feed(data['text'])
            item['declared_links'] = parser.links
            links = [link for link in parser.links if any(term in link['title'] for term in ('股票代码', '对照表', '分类指引'))]
            if not links:
                raise ValueError('No selected official attachment')
            for number, link in enumerate(links, 1):
                address = urljoin(url, link['url'])
                parsed = urlsplit(address)
                if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('sp.capco.org.cn', 'www.capco.org.cn', 'capcofile.oss-cn-beijing.aliyuncs.com'):
                    raise ValueError('Unreviewed attachment host')
                file = folder / f'attachment_{number:02d}.pdf'
                downloaded = _capture(root, file, address)
                if downloaded['status'] == 'RECEIVED' and not file.read_bytes().startswith(b'%PDF'):
                    downloaded['status'] = 'INVALID_DOCUMENT'
                item['attachments'].append({**link, **downloaded})
                if downloaded.get('access_blocked'):
                    break
            item['status'] = 'COLLECTED' if all(a['status'] == 'RECEIVED' for a in item['attachments']) else 'PARTIAL'
        except (OSError, KeyError, ValueError) as error:
            item['error'] = f'{type(error).__name__}: {error}'
    save(folder / 'receipt.json', item, immutable=True)
    records.append(item)
    print(lower, item['status'], len(item['attachments']), flush=True)
    if receipt.get('access_blocked') or any(a.get('access_blocked') for a in item['attachments']):
        break
result = {
    'completed_at': now(), 'status': 'COLLECTED' if len(records) == len(selected) and all(r['status'] == 'COLLECTED' for r in records) else 'PARTIAL',
    'articles': records, 'requested_articles': len(selected), 'facts_applied': 0,
    'scope': protocol['scope'],
}
save(target / 'collection.json', result, immutable=True)
register_output(Paths(root), target)
print(result['status'], flush=True)
