from pathlib import Path

from quantfolio.disclosures import _capture
from quantfolio.storage import now, read, save, sha

root=Path.cwd(); folder=root/'data/raw/industry_standards_2026-11-21'
assert not folder.exists()
base='https://www.csrc.gov.cn/csrc/c101954/c7520291/'
sources=[
    ('csrc_announcement.html',base+'content.shtml'),
    ('csrc_classification_2024.pdf',base+'7520291/files/%E9%99%84%E4%BB%B61%EF%BC%9A%E3%80%8A%E4%B8%8A%E5%B8%82%E5%85%AC%E5%8F%B8%E8%A1%8C%E4%B8%9A%E7%BB%9F%E8%AE%A1%E5%88%86%E7%B1%BB%E4%B8%8E%E4%BB%A3%E7%A0%81%E3%80%8B.pdf'),
    ('capco_release_2025-09-30.html','https://www.capco.org.cn/pub/zgssgsxh/xhgg/hyfl/hyfljg/202509/20250930/j_2025093014371200017592143530187679.html'),
]
save(folder/'protocol.json',{'started_at':now(),'registration':'configs/protocols/industry_sources.json',
    'registration_sha256':sha(root/'configs/protocols/industry_sources.json'),'purpose':'Preserve the official 2024 standard and source pages for taxonomy/date comparison only.',
    'sources':[{'filename':n,'url':u} for n,u in sources],'collector_sha256':sha(Path(__file__))},immutable=True)
(folder/'collector_source.py').write_bytes(Path(__file__).read_bytes())
records=[]
for name,url in sources:
    record=_capture(root,folder/name,url);records.append(record)
    print(name,record['status'],flush=True)
    if record.get('access_blocked'):break
save(folder/'collection.json',{'completed_at':now(),'status':'COLLECTED' if len(records)==len(sources) and all(r['status']=='RECEIVED' for r in records) else 'PARTIAL','records':records,'assignments_applied':0},immutable=True)
