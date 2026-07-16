import json, os, hashlib, sys
os.environ['QUALIBUG_JWT_SECRET'] = 'local-dev-only'
os.environ['QUALIBUG_PAGE_AGENT_BRIDGE_URL'] = 'http://127.0.0.1:8797/execute'
os.environ['QUALIBUG_PAGE_AGENT_BRIDGE_MODE'] = 'page_agent_browser_plan'
os.environ['QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START'] = 'true'
os.environ['ENABLE_V12_STATE_GRAPH_ENGINE'] = 'true'

from ai_test_asset_center import experiment_compiler_base
original_ce = experiment_compiler_base.compile_experiments

def debug_ce(obligations, **kw):
    result = original_ce(obligations, **kw)
    blocked = result.get('blocked_experiments', [])
    for e in (blocked or []):
        if isinstance(e, dict):
            cr = e.get('compile_receipt', {})
            if 'BINDING' in cr.get('reason_code', ''):
                oid = e.get('obligation_id', '?')[:40]
                bp = e.get('binding_plan', [])
                detail = cr.get('detail', '')[:60]
                print(f'DEBUG {oid}: plan_len={len(bp)} detail={detail}', flush=True)
                for b in bp[:3]:
                    if isinstance(b, dict):
                        sv = str(b.get('synthetic_value', 'NONE'))[:20]
                        print(f'  binding: target={b.get("target")} status={b.get("status")} sv={sv}', flush=True)
                break
    return result

experiment_compiler_base.compile_experiments = debug_ce

print('Running...', flush=True)
from ai_test_asset_center.__main__ import scan
api = open('projects/benchmark_mall/input/API_SPEC.md').read()
h = hashlib.sha256(api.encode()).hexdigest()
result = scan(project='benchmark_mall', root='.', prd_text='test', api_doc_text=api, base_url='http://127.0.0.1:8080', campaign_context={'target_id':'t','environment_id':'e','environment_type':'test','execution_mode':'approved_sandbox_write','source_manifest':{'source_id':'s','source_hash':h}})
print('DONE', flush=True)
