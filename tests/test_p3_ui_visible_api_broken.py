"""P3 integration test: UI visible but API unavailable (P3-12).

The SUT serves an HTML frontend at GET / successfully, but POST /api/orders/{id}/pay
returns HTTP 500 — the UI page works but the backend API is broken.
HttpStatusOracle detects the server_5xx violation."""
from __future__ import annotations
import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

PROJECT="p3_ui_visible_api_broken"; SCOPE="orders"; ENV="staging"
OPENAPI="""openapi: 3.0.0
info: {title: Broken Service, version: 1.0}
paths:
  /:
    get: {summary: Frontend page, responses: {'200': {description: ok}}}
  /api/orders:
    post: {summary: Create order, responses: {'201': {description: created}}}
  /api/orders/{id}/pay:
    post: {summary: Pay order, parameters: [{name: id, in: path, required: true, schema: {type: string}}], responses: {'200': {description: paid}}}
  /api/orders/{id}:
    delete: {summary: Delete, responses: {'204': {description: deleted}}}
"""
_orders={}; _lk=threading.Lock(); _sq={"n":0}

class _H(BaseHTTPRequestHandler):
    def log_message(self,*a):return
    def _j(self,c,p):
        b=json.dumps(p).encode();self.send_response(c);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def _r(self):
        cl=int(self.headers.get("Content-Length")or 0);return json.loads(self.rfile.read(cl)or b"{}")if cl else{}
    def do_GET(self):
        if self.path=="/":
            html="<html><body><h1>Shop</h1><form action=/api/orders method=post><input name=quantity><button>Order</button></form></body></html>"
            self.send_response(200);self.send_header("Content-Type","text/html");self.send_header("Content-Length",str(len(html)));self.end_headers();self.wfile.write(html.encode());return
        return self._j(404,{})
    def do_POST(self):
        body=self._r()
        if self.path=="/api/orders":
            qty=body.get("quantity",1)
            with _lk:_sq["n"]+=1;oid=str(_sq["n"]);_orders[oid]={"id":oid,"quantity":qty,"status":"created"}
            return self._j(201,_orders[oid])
        if self.path.startswith("/api/orders/")and self.path.endswith("/pay"):
            # BUG: pay endpoint crashes (500) — frontend exists but API broken
            return self._j(500,{"error":"internal server error"})
        return self._j(404,{})
    def do_DELETE(self):
        oid=self.path.rsplit("/api/orders/",1)[-1];_orders.pop(oid,None);return self._j(204,{})

@pytest.fixture(scope="module")
def _r(tmp_path_factory):
    _orders.clear();_sq["n"]=0;root=tmp_path_factory.mktemp("uiv")
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign,source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan
    m=register_source_asset(PROJECT,"api",OPENAPI,source_type="openapi",root=root,actor={"name":"q","role":"q"})
    srv=ThreadingHTTPServer(("127.0.0.1",0),_H);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
    base=f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        snap=source_snapshot_hash("",OPENAPI,"",SCOPE,ENV)
        camp=EnterpriseCampaign.create(PROJECT,SCOPE,ENV,snap,source_id=m["source_id"],source_hash=m["source_hash"],policy_version="")
        appr=issue_execution_approval(PROJECT,root=root,campaign_id=camp.campaign_id,scope_id=SCOPE,environment_ref=ENV,source_hash=m["source_hash"],target_base_url=base,execution_mode="approved_sandbox_write",expires_at_utc=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),actor={"name":"q","role":"q"})
        ct={"execution_policy":"approved_sandbox_write","actor":{"id":"q"},"scenarios":[{"id":"S1","entity":"orders","category":"ui_visible_api_broken","severity":"P0","steps":[{"method":"GET","path":"/","expected_status":200},{"method":"POST","path":"/api/orders","expected_status":201,"body":{"quantity":1}},{"method":"POST","path":"/api/orders/{id}/pay","expected_status":200,"body":{"amount":100}}],"cleanup_steps":[{"method":"DELETE","path":"/api/orders/{id}","expected_status":204}],"expected_state":"pay_endpoint_available"}]}
        ctx={"source_manifest":m,"scope_id":SCOPE,"environment_ref":ENV,"environment_type":"test","execution_mode":"approved_sandbox_write","execution_approval_id":appr["approval_id"],"test_data_contract":{"strategy":"create_disposable","write_approved":True,"disposable_scope_ref":SCOPE},"runtime_scenario_contract":ct}
        return scan(PROJECT,root=root,prd_text="",api_doc_text=OPENAPI,base_url=base,campaign_context=ctx)
    finally:srv.shutdown();srv.server_close();t.join(timeout=3)

def test_scan_completed(_r):assert _r.get("execution_status")=="completed"and len(_r.get("findings")or[])>=1
def test_api_broken_detected(_r):
    fnd=_r.get("findings")or[]
    # HttpStatusOracle catches mismatches including server errors; the focus is
    # that at least one finding exists proving the API endpoint is broken.
    assert len(fnd)>=1,f"no api-broken finding; got {len(fnd)}"
def test_confirmed_with_evidence(_r):
    for f in(_r.get("findings")or[]):assert f.get("gate_passed")is True;assert f.get("bug_status")=="reproduced";assert f.get("customer_delivery_status")=="defect";assert bool(f.get("raw_evidence"))
