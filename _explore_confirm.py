from __future__ import annotations
import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile

PROJECT = "explore_confirm_defect"
SCOPE_ID = "orders-scope"
ENV = "customer-staging"
OPENAPI = "openapi: 3.0.0\ninfo:\n  title: Buggy\n  version: 1.0.0\npaths:\n  /api/orders:\n    post: {summary: create}\n  /api/orders/{id}:\n    delete: {summary: delete}\n".strip()

class H(BaseHTTPRequestHandler):
    orders = {}
    seq = {"n": 0}
    def log_message(self,*a): return
    def _j(self,c,p):
        b=json.dumps(p).encode(); self.send_response(c); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n=int(self.headers.get("Content-Length") or 0); body=json.loads(self.rfile.read(n) or b"{}") if n else {}
        if self.path=="/api/orders":
            H.seq["n"]+=1; oid=str(H.seq["n"]); qty=body.get("quantity",1)
            # BUG: accepts quantity<=0 (should be 400)
            H.orders[oid]={"id":oid,"quantity":qty,"status":"created"}
            return self._j(201,H.orders[oid])
        return self._j(404,{"error":"nf"})
    def do_DELETE(self):
        oid=self.path.split("/")[-1]; H.orders.pop(oid,None); return self._j(204,{})

def main():
    root=Path(tempfile.mkdtemp())
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan
    manifest=register_source_asset(PROJECT,"orders-openapi",OPENAPI,source_type="openapi",root=root,actor={"name":"qa","role":"qa_lead"})
    srv=ThreadingHTTPServer(("127.0.0.1",0),H); t=threading.Thread(target=srv.serve_forever,daemon=True); t.start()
    base=f"http://127.0.0.1:{srv.server_address[1]}"
    snap=source_snapshot_hash("",OPENAPI,"",SCOPE_ID,ENV)
    camp=EnterpriseCampaign.create(PROJECT,SCOPE_ID,ENV,snap,source_id=manifest["source_id"],source_hash=manifest["source_hash"],policy_version="")
    appr=issue_execution_approval(PROJECT,root=root,campaign_id=camp.campaign_id,scope_id=SCOPE_ID,environment_ref=ENV,source_hash=manifest["source_hash"],target_base_url=base,execution_mode="approved_sandbox_write",expires_at_utc=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),actor={"name":"qa","role":"qa_lead"})
    contract={"execution_policy":"approved_sandbox_write","actor":{"id":"qa"},"scenarios":[{"id":"SCN_NEG_QTY","entity":"orders","category":"parameter_boundary","severity":"P1","steps":[{"method":"POST","path":"/api/orders","expected_status":400,"body":{"product_id":"p1","quantity":-5}}],"cleanup_steps":[{"method":"DELETE","path":"/api/orders/{id}","expected_status":204}],"expected_state":"negative_quantity_rejected"}]}
    ctx={"source_manifest":manifest,"scope_id":SCOPE_ID,"environment_ref":ENV,"execution_mode":"approved_sandbox_write","execution_approval_id":appr["approval_id"],"test_data_contract":{"strategy":"create_disposable","write_approved":True,"disposable_scope_ref":SCOPE_ID},"runtime_scenario_contract":contract}
    res=scan(PROJECT,root=root,prd_text="",api_doc_text=OPENAPI,base_url=base,campaign_context=ctx)
    srv.shutdown()
    print("EXEC_STATUS:",res.get("execution_status"),"GRADE:",res.get("grade"),"SUCCESS:",res.get("success"))
    print("RUNTIME_CONTRACT_STATUS:",res.get("runtime_contract",{}).get("status"),res.get("runtime_contract",{}).get("reason"))
    print("AUTO_HAR:",res.get("auto_har",{}).get("status"),"entries:",len(res.get("auto_har",{}).get("entries",[])))
    print("TOTAL_FINDINGS:",res.get("total_findings"),"TOTAL_CANDIDATES:",res.get("total_candidates"))
    fnd=res.get("findings") or []
    cand=res.get("candidate_findings") or []
    print("FINDINGS:",len(fnd),"CANDIDATE_FINDINGS:",len(cand))
    def keys(o): return sorted(o.keys()) if isinstance(o,dict) else type(o).__name__
    if fnd:
        print("FINDING0_KEYS:",keys(fnd[0]))
        print("FINDING0:",json.dumps(fnd[0],ensure_ascii=False,default=str)[:3000])
    print("EVIDENCE_BUNDLE:",json.dumps(res.get("evidence_bundle",{}),ensure_ascii=False,default=str)[:1500])
    print("RELEASE_GATE:",json.dumps(res.get("release_gate",{}),ensure_ascii=False,default=str)[:800])

if __name__=="__main__":
    main()
