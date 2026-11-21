import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from quantfolio.disclosures import _capture
from quantfolio.storage import now, save, sha

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            link=dict(attrs).get('href','')
            if link.lower().endswith('.docx'):self.links.append(link)

root=Path.cwd()
folder=root/'data/raw/capco_guideline_2026-11-21'
source=folder/'article.js'
raw=source.read_text(encoding='utf-8')
data=json.loads(re.fullmatch(r'\s*export\s+const\s+allData\s*=\s*(\{.*\})\s*;?\s*',raw,re.S)[1])
parser=Links();parser.feed(data['text'])
assert len(parser.links)==1,parser.links
url=urljoin('https://www.capco.org.cn/',parser.links[0])
save(folder/'attachment_protocol.json',{
    'requested_at':now(),'article_sha256':sha(source),'url':url,
    'purpose':'Archive the classification structure/code appendix linked by the complete official 2023 guideline.',
    'collector_sha256':sha(Path(__file__)),'assignments_applied':0,
},immutable=True)
(folder/'attachment_collector_source.py').write_bytes(Path(__file__).read_bytes())
receipt=_capture(root,folder/'classification_codes.docx',url)
save(folder/'attachment_receipt.json',receipt,immutable=True)
print(receipt,flush=True)
