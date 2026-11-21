import logging
from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from quantfolio.dataset import check_integrity, eligible_rows, load_bundle
from quantfolio.evaluation import POLICIES, evaluate_accounts, load_predictions
from quantfolio.feasibility import account_differences
from quantfolio.settings import Paths
from quantfolio.storage import digest, load_gzip, now, read, save, sha
from quantfolio.validation import execution_audit, reconcile
from quantfolio.verification import _paper_account

logging.basicConfig(level=logging.INFO, format='%(message)s')
root=Path.cwd(); paths=Paths(root); p=read(root/'configs/protocols/etf_integration.json')
checks=root/p['checks_output']/'implementation'; run=root/p['baseline_run']; previous=root/p['baseline_evaluation']
source_hashes={f.name:sha(f) for f in sorted((root/'src/quantfolio').glob('*.py'))}
integrity=check_integrity(paths); bundle=load_bundle(paths)
assert sha(paths.bundle)==p['source_bundle_sha256']
spec=read(run/'protocol.json'); status=read(run/'training_status.json')
assert spec['identity']==status['identity']==digest(spec['specification'])
assert spec['specification']['data_sha256']==sha(paths.bundle)
assert spec['specification']['source_sha256']['models.py']==source_hashes['models.py']
from quantfolio.evaluation import experiment_folds
try:
    experiment_folds(paths,bundle,run)
except ValueError as error:
    assert str(error)=='Execution source changed since training: simulation.py'
    failure=str(error).encode('utf-8')
else:
    raise AssertionError('Source identity guard unexpectedly accepted changed ledger code')
checks.mkdir(parents=True,exist_ok=True)
(checks/'direct_evaluation_block.log').write_bytes(failure)
save(checks/'replay_protocol.json',{'started_at':now(),'registration_commit':'f6b07fc8c590f131d59c8681c8a5d5e8206ad06e',
    'source_sha256':source_hashes,'baseline_identity':status['identity'],'bundle_sha256':sha(paths.bundle),
    'scope':'Explicit frozen-input compatibility diagnostic using stored predictions, new execution, and separate outputs. The ordinary evaluate/verify source-identity guard correctly rejected changed code and remains enabled. This replay does not certify the old training as produced by current execution code.',
    'ordinary_guard_log_sha256':sha(checks/'direct_evaluation_block.log'),'integrity':integrity},immutable=True)
matrix=bundle['table'][bundle['features']].to_numpy(dtype=np.float32); models=[]
for file in sorted((run/'training').glob('fold_*/result.json'))+[run/'final_fit/result.json']:
    result=read(file); fold=result['fold']
    for stage,start,end in [('validation_fit',fold['validation_start'],fold['validation_end']),('outer_fit',fold['test_start'],fold['test_end'])]:
        rows=eligible_rows(bundle['table'],start,end)
        for family in ('ridge','tree'):
            folder=file.parent/stage/family; meta=read(folder/'fit.json')
            assert sha(folder/'model.joblib')==meta['model_sha256']
            with threadpool_limits(limits=2):actual=joblib.load(folder/'model.joblib').predict(matrix[rows])
            expected=np.load(folder/'predictions.npy'); np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-12)
            assert meta['latest_training_label_end']<start+int(result['final_fit'] and stage=='outer_fit')
            models.append({'path':folder.relative_to(root).as_posix(),'predictions':len(rows),'max_error':float(np.max(np.abs(actual-expected)))})
assert len(models)==48
save(checks/'models/verification.json',{'status':'PASS','models_reloaded':48,'models':models,'scope':'Stored estimator predictions on unchanged input matrix; no new-code training identity asserted.'},immutable=True)
maps,schedule,_,start,end=load_predictions(bundle,run,identity=status['identity'])
before=read(previous/'evaluation_summary.json')
for rel,expected in before['artifacts'].items():assert sha(previous/rel)==expected
target=root/'artifacts/evaluations/etf_integration_parity_2026-11-21'
print('Replaying 1425 old-input accounts',flush=True)
result=evaluate_accounts(bundle,maps,schedule,start,end,[10,20,30],POLICIES,target,turnover='legacy')
result.update(scope='UNCHANGED_INPUT_COMPATIBILITY_DIAGNOSTIC',source_sha256=source_hashes)
save(target/'summary.json',result,immutable=True)
comparison=account_differences(previous,target,result['artifacts'],exact=True)
save(checks/'account_parity.json',comparison,immutable=True)
print('Replaying 75 old paper accounts',flush=True)
records=[]
for cost in (10,20,30):
    for name in (f'evaluation/{cost}bps/selected_strategy.ledgers.json.gz',f'evaluation/continuous/{cost}bps/selected_strategy.json.gz'):
        stored=load_gzip(previous/name)
        for index,reference in enumerate(stored if isinstance(stored,list) else [stored]):
            expected={k:reference[k] for k in ('summary','ledger')}
            actual,n=_paper_account(bundle,maps['selected'],schedule,expected,cost/10000,turnover='legacy')
            audit=execution_audit(actual['ledger'],bundle)
            recon=reconcile(actual['ledger'],actual['summary'],bundle['meta'],bundle['actions'])
            assert actual==expected and audit['status']==recon['status']=='PASS'
            records.append({'path':name,'account_index':index,'sessions':actual['summary']['sessions'],'full_account_equal':True,'expected_sha256':digest(expected),'actual_sha256':digest(actual),'paper_fill_checks':n,'execution_audit':audit,'reconciliation':recon})
assert len(records)==75
save(checks/'paper/verification.json',{'status':'PASS','accounts':75,'sessions':sum(r['sessions'] for r in records),'records':records},immutable=True)
assert source_hashes=={f.name:sha(f) for f in sorted((root/'src/quantfolio').glob('*.py'))}
tests=(root/'.git/etf_integration_checks_ready.log').read_bytes();assert b'392 passed, 1 skipped' in tests
(checks/'local_checks.log').write_bytes(tests)
save(checks/'validation.json',{'status':'PASS','completed_at':now(),'source_sha256':source_hashes,'models_reloaded':48,'full_accounts_exact':len(comparison),'paper_accounts_exact':75,'paper_sessions':sum(r['sessions'] for r in records),'tests_passed':392,'tests_skipped':1,'local_checks_sha256':sha(checks/'local_checks.log'),'evaluation':target.relative_to(root).as_posix(),'scope':'Explicit old-input diagnostic; ordinary source-identity safeguards remain unchanged.'},immutable=True)
print('UNCHANGED INPUT PARITY PASSED',flush=True)
