import bisect
from collections import Counter, defaultdict
from pathlib import Path

from quantfolio.dataset import load_snapshot
from quantfolio.settings import Paths
from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

root=Path.cwd();paths=Paths(root);protocol=read(root/'configs/protocols/industry_cell_review.json')
checks=root/protocol['checks_output'];review=read(checks/'review.json')
assert review['source_extraction_complete'] and review['assignments_applied']==0
assert review['source_sha256']==read(checks/'started.json')['source_sha256']==sha(root/'src/quantfolio/industry_sources.py')
bundle_file=root/protocol['source_bundle'];bundle=load_snapshot(paths,bundle_file,'2026-11-20')
assert sha(bundle_file)==protocol['source_bundle_sha256']
meta=bundle['meta'];stocks={s:m for s,m in meta.items() if m['kind']=='stock'}
etfs={s:m for s,m in meta.items() if m['kind']=='etf'}
assert len(stocks)==572 and len(etfs)==22
calendar=[str(d.date()) for d in bundle['calendar']]
sources=[];findings=[];links={};link_records=[];source_hashes={}
for item in review['sources']:
    assert item['status']=='PARSED_AND_CROSS_CHECKED'
    file=root/item['output'];assert sha(file)==item['sha256']
    data=load_gzip(file);assert len(data['records'])==item['rows']
    source_hashes[item['output']]=item['sha256']
    assert all(p['text_comparison']=='PASS' for p in data['pages'])
    if not item['aliases']:
        sources.append(data)
        for row in data['records']:
            for finding in row['taxonomy_findings']:
                findings.append({'catalogue_date':item['catalogue_date'],'source_file':item['source'],
                    'source_sha256':item['source_sha256'],'page':row['page'],'row':row['row'],
                    'code':row['code'],**finding})
        continue
    for row in data['records']:
        old,new='bj'+row['old_code'],'bj'+row['new_code']
        assert old not in links or links[old]==new
        links[old]=new
        link_records.append({'old_symbol':old,'current_symbol':new,'name':row['name'],
            'source_listing_date':row['source_listing_date'],'catalogue_date':item['catalogue_date'],
            'source':item['source'],'source_sha256':item['source_sha256'],'page':row['page'],'row':row['row'],
            'code_effective_date':None,'scope':'Identifier correspondence only; the source listing date is not a code-effective or historical-market-membership date.'})
assert len(links)==len(set(links.values()))
bse_unique_pairs=len(links)
identity=read(checks/'identity_review.json')
for row in identity['records']:
    old,new=row['market']+row['old_code'],row['market']+row['new_code']
    assert old not in links and new not in links.values()
    links[old]=new
    link_records.append({'old_symbol':old,'current_symbol':new,'name':row['new_name'],
        'source':identity['source'],'source_sha256':identity['source_sha256'],
        'code_effective_date':row['effective_date'],'notice_date':identity['notice_date'],
        'source_directory_date':identity['source_directory_date'],'evidence_pages':row['evidence_pages'],
        'scope':'Issuer-notice-confirmed identity continuity; no industry or eligibility is inferred.'})
assert len(links)==bse_unique_pairs+len(identity['records'])
save(checks/'identity_links.json',{'status':'SOURCE_IDENTIFIERS_LINKED','checked_at':now(),
    'source_review_sha256':sha(checks/'review.json'),'manual_identity_review_sha256':sha(checks/'identity_review.json'),
    'records':link_records,'bse_unique_pairs':bse_unique_pairs,'sz_unique_pairs':len(identity['records']),
    'bse_source_rows':sum(item['rows'] for item in review['sources'] if item['aliases']),'assignments_applied':0},immutable=True)
reverse={new:old for old,new in links.items()}
# Every finding remains explicit, even when only the printed category name is shortened.
summary=Counter()
for item in sources:
    for row in item['records']:
        for issue in row['taxonomy_findings']:
            summary[(item['catalogue_date'],issue['field'],issue['source'],issue['expected'],issue['problem'])]+=1
assert len(findings)==320 and {x['problem'] for x in findings}=={'name_mismatch'}
assert all(a+'业'==b for (_,_,a,b,_),count in summary.items())
save(checks/'taxonomy_review.json',{'status':'CODE_HIERARCHY_CHECKED_SOURCE_LABEL_DIFFERENCES_RETAINED','reviewed_at':now(),
    'source_review_sha256':sha(checks/'review.json'),'rows_checked':sum(x['rows'] for x in sources),
    'unrecognized_codes':0,'parent_conflicts':0,'label_mismatch_rows':320,
    'label_variants':[{'catalogue_date':key[0],'field':key[1],'source_label':key[2],'codebook_label':key[3],'problem':key[4],'rows':count} for key,count in sorted(summary.items())],
    'findings':findings,'visual_examples':[{'catalogue_date':date,'pages':[2,4]} for date in ['2024-02-08','2024-04-03']],
    'scope':'All original cells are retained and independently matched. Source codes and parents agree with the 2023 codebook; 320 labels omit the final character. Visual review covers representative occurrences of both variants in both releases, not all 891 pages. No source label is silently replaced.',
    'assignments_applied':0},immutable=True)
release_maps=[];releases=[];join_records=[];history=defaultdict(list)
for item in sorted(sources,key=lambda x:x['catalogue_date']):
    by_code={r['code']:r for r in item['records']};matches={};direct=alias=0;unknown=[]
    for symbol in sorted(stocks):
        candidates=[code for code in [symbol[2:],reverse.get(symbol,'')[2:]] if code and code in by_code]
        assert len(candidates)<=1,(symbol,candidates,item['catalogue_date'])
        if not candidates:unknown.append(symbol);continue
        code=candidates[0];row=by_code[code]
        mode='DIRECT_CODE' if code==symbol[2:] else 'DOCUMENTED_CODE_ALIAS'
        direct+=mode=='DIRECT_CODE';alias+=mode=='DOCUMENTED_CODE_ALIAS'
        record={'catalogue_date':item['catalogue_date'],'symbol':symbol,'source_code':code,'source_name':row['name'],
                'current_name':stocks[symbol]['name'],'join':mode,'section':row['section'],'subclass':row['subclass'],
                'division':row['division'],'division_name':row['division_name'],'taxonomy_findings':row['taxonomy_findings'],
                'source':item['source'],'source_sha256':item['source_sha256'],'page':row['page'],'row':row['row']}
        matches[symbol]=record;join_records.append(record);history[symbol].append(record)
    following=[day for day in calendar if day>item['catalogue_date']]
    available=following[0] if following else None
    releases.append({'catalogue_date':item['catalogue_date'],'title':item['title'],'source_rows':item['rows'],
        'pages':len(item['pages']),'current_universe_stocks':len(stocks),'direct_code_matches':direct,
        'alias_matches':alias,'matched':len(matches),'unmatched':unknown,'following_observed_session':available})
    release_maps.append((item['catalogue_date'],matches))
dump_gzip(root/protocol['output']/'universe_join.json.gz',join_records)
changes=[]
for symbol,records in history.items():
    for before,after in zip(records,records[1:]):
        if before['division']!=after['division']:
            changes.append({'symbol':symbol,'previous_catalogue_date':before['catalogue_date'],
                            'catalogue_date':after['catalogue_date'],'before_division':before['division'],
                            'after_division':after['division'],'before_source_code':before['source_code'],
                            'after_source_code':after['source_code']})
dates=[d for d,_ in release_maps];missing=Counter();eligible_count=0;unknown_samples=[];daily=[]
eligible=bundle['table'][bundle['table'].eligible & bundle['table'].symbol.isin(stocks)]
for date_index,part in eligible.groupby('date_index',sort=True):
    day=calendar[int(date_index)];index=bisect.bisect_left(dates,day)-1
    mapping=release_maps[index][1] if index>=0 else {}
    absent=sorted(s for s in part.symbol if s not in mapping)
    eligible_count+=len(part);missing.update(absent)
    daily.append({'date':day,'catalogue_date':dates[index] if index>=0 else None,'eligible_stocks':len(part),'unknown':absent})
    unknown_samples.extend({'date':day,'symbol':s,'catalogue_date':dates[index] if index>=0 else None} for s in absent)
dump_gzip(checks/'coverage_by_session.json.gz',daily)
latest=release_maps[-1][1];pairs=Counter((stocks[s]['industry'],r['section']+r['division']) for s,r in latest.items())
save(checks/'coverage.json',{'status':'RETROSPECTIVE_SOURCE_JOIN_ONLY','checked_at':now(),
    'source_bundle_sha256':sha(bundle_file),'source_review_sha256':sha(checks/'review.json'),
    'identity_links_sha256':sha(checks/'identity_links.json'),'input_sha256':source_hashes,
    'releases':releases,'stocks':len(stocks),'etfs_outside_company_classification':sorted(etfs),
    'documented_aliases_relevant_to_current_stocks':sum(s in reverse for s in stocks),
    'published_division_changes':changes,'eligible_stock_rows':eligible_count,'eligible_stock_rows_without_current_release_match':sum(missing.values()),
    'missing_eligible_rows_by_symbol':dict(sorted(missing.items())),
    'missing_eligible_examples':unknown_samples[:20],'daily_coverage_sha256':sha(checks/'coverage_by_session.json.gz'),
    'universe_join_sha256':sha(root/protocol['output']/'universe_join.json.gz'),
    'latest_taxonomy_crosswalk':[{'current_static_label':a,'published_division':b,'stocks':n} for (a,b),n in sorted(pairs.items())],
    'availability_convention':'Diagnostic only: a table is considered from the first observed session strictly after its catalogue date. No statistical-period backdating, same-day publication assumption, future-table backfill, or carrying an omitted security from a prior release.',
    'limitations':['Current-universe conditioning remains; a source row is not proof of historical membership or trading status.',
                   'Issuer aliases join stable identities retrospectively; their listing-date fields do not establish code-effective dates.',
                   'Catalogue dates and current file metadata do not prove exact intraday historical publication or unchanged historical bytes.',
                   'ETF handling, unknown-industry behavior and model/risk integration require a separate registered comparison.'],
    'assignments_applied':0,'models_retrained':0},immutable=True)
print('Release coverage',[(r['catalogue_date'],r['matched'],r['alias_matches'],len(r['unmatched'])) for r in releases],flush=True)
print('Eligible rows',eligible_count,'unmatched',sum(missing.values()),'symbols',dict(missing),flush=True)
print('Division changes',len(changes),'relevant aliases',sum(s in reverse for s in stocks),flush=True)
