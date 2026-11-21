import html
import json
import re
from pathlib import Path

from quantfolio.disclosures import _capture
from quantfolio.storage import now, read, save, sha

root=Path.cwd()
folder=root/'data/raw/capco_guideline_2026-11-21'
assert not folder.exists()
index=root/'data/raw/capco_industries_2026-11-21/site_index.js'
fragment='xhdt/tzgg/53b8127cbb9d46f5af59a5a9fce32838.js'
assert fragment in index.read_text(encoding='utf-8')
protocol={'requested_at':now(),'purpose':'Retrieve the full guideline linked by the official industry-page More control; retain the earlier first-chapter-only response.',
          'index_source':index.relative_to(root).as_posix(),'index_sha256':sha(index),'url':'https://www.capco.org.cn/dataFile/'+fragment,
          'assignments_applied':0,'source_code_sha256':sha(Path(__file__))}
save(folder/'protocol.json',protocol,immutable=True)
(folder/'collector_source.py').write_bytes(Path(__file__).read_bytes())
request=_capture(root,folder/'article.js',protocol['url'])
record={'completed_at':now(),'request':request,'status':request['status'],'assignments_applied':0}
if request['status']=='RECEIVED':
    raw=(folder/'article.js').read_text(encoding='utf-8')
    match=re.fullmatch(r'\s*export\s+const\s+allData\s*=\s*(\{.*\})\s*;?\s*',raw,re.S)
    assert match,'Unexpected source format'
    data=json.loads(match[1])
    assert data['title']=='中国上市公司协会上市公司行业统计分类指引' and data['date']=='2023-05-21'
    text=html.unescape(re.sub('<[^>]+>','',re.sub(r'</p\s*>','\n',data['text'])))
    (folder/'text.txt').write_text(text,encoding='utf-8',newline='\n')
    record.update(title=data['title'],catalogue_date=data['date'],source=data.get('source'),
                  article_sha256=sha(folder/'article.js'),text_sha256=sha(folder/'text.txt'),
                  has_final_article='第十九条' in text,has_classification_method='分类方法' in text,
                  full_text_characters=len(text),status='FULL_ARTICLE_RECEIVED' if '第十九条' in text else 'INCOMPLETE_ARTICLE')
save(folder/'receipt.json',record,immutable=True)
print(record,flush=True)
