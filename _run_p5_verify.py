import json, os, sys, tempfile, threading, time, shutil, glob as _glob
os.environ.setdefault('QUALIBUG_JWT_SECRET','dev-mode-only'); os.environ.setdefault('QUALIBUG_LOCAL_DEV_ACTOR','1')
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

P='p5_verify'; S='orders'; E='staging'
O='openapi: 3.0.0\ninfo:\n  title: Buggy Shop\n  version: 1.0.0\npaths:\n  /api/orders:\n    post:\n      summary: Create order (quantity must be > 0)\n      responses:\n        "201": {description: created}\n        "400": {description: invalid_quantity}\n  /api/orders/{id}:\n    delete:\n      summary: Delete order\n      parameters:\n        - name: id\n          in: path\n          required: true\n          schema: {type: string}\n      responses:\n        "204": {description: deleted}\n'

ROOT=Path(tempfile.mkdtemp())
_ORD={}; _LK=threading.Lock(); _SQ={'n':0}

def make_sut(validate_quantity=False):
    class H(BaseHTTPRequestHandler):
        def log_message(s,*a): return
        def _j(s,c,p):
            b=json.dumps(p).encode(); s.send_response(c); s.send_header('Content-Type','application/json'); s.send_header('Content-Length',str(len(b))); s.end_headers(); s.wfile.write(b)
        def _r(s):
            n=int(s.headers.get('Content-Length') or 0)
            return json.loads(s.rfile.read(n) or b'{}') if n else {}
        def do_POST(s):
            body=s._r()
            if s.path=='/api/orders':
                qty=body.get('quantity',1)
                if validate_quantity and int(qty)<=0:
                    return s._j(400,{'error':'quantity must be > 0'})
                with _LK: _SQ['n']+=1; oid=str(_SQ['n']); _ORD[oid]={'id':oid,'quantity':qty,'status':'created'}
                return s._j(201,_ORD[oid])
            return s._j(404,{})
        def do_DELETE(s):
            oid=s.path.rsplit('/',1)[-1]; _ORD.pop(oid,None); return s._j(204,{})
    return H

# PHASE 1: scan BUGGY SUT -> confirmed defect + regression suite
H1=make_sut(validate_quantity=False)
srv1=ThreadingHTTPServer(('127.0.0.1',0),H1); t1=threading.Thread(target=srv1.serve_forever,daemon=True); t1.start()
base1=f'http://127.0.0.1:{srv1.server_address[1]}'

from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
from ai_test_asset_center.execution_approvals import issue_execution_approval
from ai_test_asset_center.__main__ import scan, _persist_customer_ready_static_artifacts
from ai_test_asset_center.regression_suite_builder import build_regression_suite
from ai_test_asset_center.regression_runner import run_regression_suite

m=register_source_asset(P,'api',O,source_type='openapi',root=ROOT,actor={'name':'qa','role':'qa_lead'})
snap=source_snapshot_hash('',O,'',S,E)
camp=EnterpriseCampaign.create(P,S,E,snap,source_id=m['source_id'],source_hash=m['source_hash'],policy_version='')
appr=issue_execution_approval(P,root=ROOT,campaign_id=camp.campaign_id,scope_id=S,environment_ref=E,source_hash=m['source_hash'],target_base_url=base1,execution_mode='approved_sandbox_write',expires_at_utc=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),actor={'name':'qa','role':'qa_lead'})

c={'execution_policy':'approved_sandbox_write','actor':{'id':'qa_lead'},'scenarios':[{'id':'S1','entity':'orders','category':'parameter_boundary','severity':'P1','steps':[{'method':'POST','path':'/api/orders','expected_status':400,'body':{'quantity':-5}}],'cleanup_steps':[{'method':'DELETE','path':'/api/orders/{id}','expected_status':204}],'expected_state':'neg_qty_rejected'}]}
ctx={'source_manifest':m,'scope_id':S,'environment_ref':E,'execution_mode':'approved_sandbox_write','execution_approval_id':appr['approval_id'],'test_data_contract':{'strategy':'create_disposable','write_approved':True,'disposable_scope_ref':S},'runtime_scenario_contract':c}
res=scan(P,root=ROOT,prd_text='',api_doc_text=O,base_url=base1,campaign_context=ctx)
srv1.shutdown(); t1.join(timeout=2)

fnd=res.get('findings',[]) or []
print(f'PHASE1: exec={res.get("execution_status")} findings={len(fnd)}')
if fnd:
    f=fnd[0]; print(f'  DEFECT: gp={f.get("gate_passed")} bs={f.get("bug_status")} cds={f.get("customer_delivery_status")}')

_persist_customer_ready_static_artifacts(P, ROOT, res)
suite=build_regression_suite(project_id=P,root=ROOT,options={'mode':'smoke'})
sm=suite.get('summary',{}); md=suite.get('modes',{})
items=md.get('smoke',{}).get('items',[])
print(f'  SUITE: total={sm.get("total_probe_count")} smoke_items={len(items)}')
if items:
    p=items[0]; print(f'  PROBE: {p.get("regression_probe_id")} {p.get("method")} {p.get("path")}')

# PHASE 2: FIXED SUT -> regression should PASS
_ORD.clear(); _SQ['n']=0
H2=make_sut(validate_quantity=True)
srv2=ThreadingHTTPServer(('127.0.0.1',0),H2); t2=threading.Thread(target=srv2.serve_forever,daemon=True); t2.start()
base2=f'http://127.0.0.1:{srv2.server_address[1]}'

for pat in ['platform_outputs','platform_workspace']:
    for path in _glob.glob(str(ROOT/pat/P/'defect_discovery'/'fix_regression_probes.json')):
        data=json.loads(Path(path).read_text('utf-8'))
        if isinstance(data.get('items'),list):
            for item in data['items']:
                if isinstance(item,dict): item['base_url']=base2; item['target']=base2
            Path(path).write_text(json.dumps(data),'utf-8')
    for path in _glob.glob(str(ROOT/pat/P/'real_project'/'real_project_defect_data.json')):
        data=json.loads(Path(path).read_text('utf-8'))
        data['_regression_base_url']=base2
        Path(path).write_text(json.dumps(data),'utf-8')

reg=run_regression_suite(project_id=P,root=ROOT,options={'mode':'smoke','dry_run':False,'base_url':base2})
reg_sm=reg.get('summary',{}) if isinstance(reg,dict) else {}
print(f'PHASE2-FIXED: ok={reg.get("ok")} exec={reg_sm.get("executed_count")} pass={reg_sm.get("passed_count")} fail={reg_sm.get("failed_count")} gate={str(reg.get("ci_feedback",{}))[:100]}')
srv2.shutdown(); t2.join(timeout=2)

# PHASE 3: RE-BROKEN SUT -> regression should FAIL
_ORD.clear(); _SQ['n']=0
H3=make_sut(validate_quantity=False)
srv3=ThreadingHTTPServer(('127.0.0.1',0),H3); t3=threading.Thread(target=srv3.serve_forever,daemon=True); t3.start()
base3=f'http://127.0.0.1:{srv3.server_address[1]}'

for pat in ['platform_outputs','platform_workspace']:
    for path in _glob.glob(str(ROOT/pat/P/'defect_discovery'/'fix_regression_probes.json')):
        data=json.loads(Path(path).read_text('utf-8'))
        if isinstance(data.get('items'),list):
            for item in data['items']:
                if isinstance(item,dict): item['base_url']=base3; item['target']=base3
            Path(path).write_text(json.dumps(data),'utf-8')
    for path in _glob.glob(str(ROOT/pat/P/'real_project'/'real_project_defect_data.json')):
        data=json.loads(Path(path).read_text('utf-8'))
        data['_regression_base_url']=base3
        Path(path).write_text(json.dumps(data),'utf-8')

reg2=run_regression_suite(project_id=P,root=ROOT,options={'mode':'smoke','dry_run':False,'base_url':base3})
reg2_sm=reg2.get('summary',{}) if isinstance(reg2,dict) else {}
print(f'PHASE3-BROKEN: ok={reg2.get("ok")} exec={reg2_sm.get("executed_count")} pass={reg2_sm.get("passed_count")} fail={reg2_sm.get("failed_count")} gate={str(reg2.get("ci_feedback",{}))[:100]}')
srv3.shutdown(); t3.join(timeout=2)

print(f'\nPASS_FIXED={"true" if reg_sm.get("passed_count",0)>0 else "false"}')
print(f'PASS_BROKEN_FAILED={"true" if reg2_sm.get("failed_count",0)>0 else "false"}')
