import re
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha

root=Path.cwd();file=root/'data/raw/industry_standards_2026-11-21/csrc_classification_2024.pdf'
old=read(root/'data/processed/industry_classifications_2026-11-21/codebook.json')
older={(r['level'],r['code']):r for r in old['records']}
doc=fitz.open(file);pdf=PdfReader(file);records=[];section=subclass=None;seen=set()
normalize=lambda value:''.join((value or '').split())
for page_index in range(7,42):
    page=doc[page_index];independent=normalize(pdf.pages[page_index].extract_text())
    for table_index,table in enumerate(page.find_tables().tables,1):
        for row_index,cells in enumerate(table.extract(),1):
            if len(cells)!=6:continue
            values=[normalize(x) for x in cells]
            top=[i for i in range(3) if values[i] and re.fullmatch([r'[A-S]',r'C[A-Z]',r'[0-9]{2}'][i],values[i])]
            if not top:continue
            assert len(top)==1,(page_index,row_index,cells)
            i=top[0];code=values[i];name=values[4]
            if i==0:section,subclass=code,None
            elif i==1:assert section=='C';subclass=code
            else:assert section
            level=['section','subclass','division'][i]
            key=(level,code);assert key not in seen,key;seen.add(key)
            assert code+name in independent,(page_index,row_index,code,name)
            records.append({'level':level,'code':code,'name':name,'section':section,'subclass':subclass,
                            'page':page_index+1,'table':table_index,'row':row_index,'raw_cells':cells,
                            'independent_code_and_name_match':True})
new={(r['level'],r['code']):r for r in records}
differences=[]
for key in sorted(older.keys()|new.keys()):
    before,after=older.get(key),new.get(key)
    a={k:before[k] for k in ('name','section','subclass')} if before else None
    b={k:after[k] for k in ('name','section','subclass')} if after else None
    if a!=b:differences.append({'level':key[0],'code':key[1],'before':a,'after':b})
result={'status':'COMPARED_PUBLISHED_LEVELS','checked_at':now(),
    'older_codebook':'data/raw/capco_guideline_2026-11-21/classification_codes.docx',
    'older_source_sha256':sha(root/'data/raw/capco_guideline_2026-11-21/classification_codes.docx'),
    'older_extraction_sha256':sha(root/'data/processed/industry_classifications_2026-11-21/codebook.json'),
    'later_codebook':file.relative_to(root).as_posix(),'later_source_sha256':sha(file),
    'stated_publication_and_effective_date':'2024-11-20','older_counts':{k:sum(r['level']==k for r in old['records']) for k in ('section','subclass','division')},
    'later_counts':{k:sum(r['level']==k for r in records) for k in ('section','subclass','division')},
    'differences':differences,'later_records':records,
    'scope':'Comparison of names, codes and parents at the three levels published in stock tables. Group definitions and full descriptive text are not certified equivalent. No later standard is backdated; no source label is replaced.',
    'visual_review_pages':[1,8],'assignments_applied':0}
save(root/'artifacts/checks/industry_standards_2026-11-21/codebook_comparison.json',result,immutable=True)
print({k:result[k] for k in ('status','older_counts','later_counts','differences')},flush=True)
