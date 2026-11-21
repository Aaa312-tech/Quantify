from pathlib import Path

from pypdf import PdfReader

from quantfolio.disclosures import _capture
from quantfolio.storage import now, save, sha

root=Path.cwd();folder=root/'data/raw/industry_identity_2026-11-21'
assert not folder.exists()
url='https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-15/cedb693a-f5ee-4463-9682-ea33d406b569.PDF'
save(folder/'protocol.json',{'started_at':now(),'purpose':'Check the unmatched Shenzhen current code against the issuer implementation notice. No classification, eligibility, or historical return changes.',
    'source_url':url,'current_symbol':'sz302132','observed_prior_code':'300114','collector_sha256':sha(Path(__file__))},immutable=True)
(folder/'collector_source.py').write_bytes(Path(__file__).read_bytes())
result=_capture(root,folder/'sz302132_code_change.pdf',url)
if result['status']=='RECEIVED':
    pages=[page.extract_text() for page in PdfReader(folder/'sz302132_code_change.pdf').pages]
    save(folder/'pages.json',{'pages':pages},immutable=True)
    result.update(pages=len(pages),text_sha256=sha(folder/'pages.json'))
save(folder/'receipt.json',{'completed_at':now(),'request':result,'facts_applied':0},immutable=True)
print({k:result[k] for k in ('status','sha256','pages') if k in result},flush=True)
