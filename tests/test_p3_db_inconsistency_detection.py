"""P3 integration test: DB state inconsistency detection.

The SUT stores orders in both memory (a dict) and a SQLite database. When an order
is paid, the in-memory dict is updated but the DB is NOT — creating a DB state
inconsistency. The DBSnapshotVerifier captures before/after snapshots and the scan
attaches DB evidence to the confirmed defect.
"""

from __future__ import annotations

import json, os, sqlite3, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_db_inconsistency"
SCOPE = "orders"; ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: DB-backed Shop
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order
      responses:
        '201': {description: created}
  /api/orders/{id}/pay:
    post:
      summary: Pay order
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '200': {description: paid}
  /api/orders/{id}:
    delete:
      summary: Delete order
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '204': {description: deleted}
"""

_db_path: Path | None = None
_orders: dict[str, dict] = {}
_lock = threading.Lock()
_seq = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None: return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _read(self):
        cl = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(cl) or b"{}") if cl else {}

    def do_POST(self):
        body = self._read()
        if self.path == "/api/orders":
            qty = body.get("quantity", 1)
            with _lock:
                _seq["n"] += 1; oid = str(_seq["n"])
                order = {"id": oid, "quantity": qty, "status": "created", "amount_paid": 0}
                _orders[oid] = order
                # Insert row into DB (correct initial state)
                conn = sqlite3.connect(str(_db_path))
                conn.execute("INSERT INTO orders(id,quantity,status,amount_paid) VALUES(?,?,?,?)",
                             (oid, qty, "created", 0))
                conn.commit(); conn.close()
            return self._json(201, order)

        if self.path.startswith("/api/orders/") and self.path.endswith("/pay"):
            oid = self.path.split("/")[3]
            with _lock:
                o = _orders.get(oid)
                if not o: return self._json(404, {"error": "nf"})
                amount = body.get("amount", 100)
                o["amount_paid"] += amount
                o["status"] = "paid"
                # BUG: in-memory dict updated but DB NOT updated — DB inconsistency
                # Correct behavior would also UPDATE orders SET amount_paid=... WHERE id=oid
            return self._json(200, o)
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/", 1)[-1]
        with _lock:
            _orders.pop(oid, None)
            conn = sqlite3.connect(str(_db_path))
            conn.execute("DELETE FROM orders WHERE id=?", (oid,))
            conn.commit(); conn.close()
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    global _db_path
    _orders.clear(); _seq["n"] = 0
    root = tmp_path_factory.mktemp("db_inc")

    # Create SQLite DB with orders table
    _db_path = root / "sut.db"
    conn = sqlite3.connect(str(_db_path))
    conn.execute("CREATE TABLE orders(id TEXT PRIMARY KEY,quantity INTEGER,status TEXT,amount_paid INTEGER)")
    conn.commit(); conn.close()
    os.environ["QUALIBUG_DB_DSN"] = f"sqlite:///{_db_path.resolve()}"

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "shop-openapi", OPENAPI, source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    try:
        snap = source_snapshot_hash("", OPENAPI, "", SCOPE, ENV)
        campaign = EnterpriseCampaign.create(
            PROJECT, SCOPE, ENV, snap,
            source_id=manifest["source_id"], source_hash=manifest["source_hash"], policy_version="",
        )
        approval = issue_execution_approval(
            PROJECT, root=root, campaign_id=campaign.campaign_id,
            scope_id=SCOPE, environment_ref=ENV, source_hash=manifest["source_hash"],
            target_base_url=base, execution_mode="approved_sandbox_write",
            expires_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            actor={"name": "qa_lead", "role": "qa_lead"},
        )
        contract = {
            "execution_policy": "approved_sandbox_write",
            "actor": {"id": "qa_lead"},
            "scenarios": [{
                "id": "SCN_DB",
                "entity": "orders",
                "category": "db_inconsistency",
                "severity": "P0",
                "steps": [
                    {"method": "POST", "path": "/api/orders", "expected_status": 201,
                     "body": {"quantity": 1}},
                    {"method": "POST", "path": "/api/orders/{id}/pay", "expected_status": 200,
                     "body": {"amount": 100}},
                    # After pay: memory has amount_paid=100, but DB still has amount_paid=0
                    # because the BUG omits the DB update. The DBSnapshotVerifier should
                    # capture this state gap.
                ],
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204},
                ],
                "expected_state": "db_consistent_with_memory",
            }],
        }
        ctx = {
            "source_manifest": manifest, "scope_id": SCOPE, "environment_ref": ENV,
            "execution_mode": "approved_sandbox_write", "execution_approval_id": approval["approval_id"],
            "test_data_contract": {"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": SCOPE},
            "runtime_scenario_contract": contract,
        }
        result = scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI, base_url=base, campaign_context=ctx)
        return result
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=3)


def test_scan_completed_with_defects(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    assert len(_result.get("findings") or []) >= 1


def test_db_evidence_attached_to_finding(_result: dict) -> None:
    """At least one finding must carry DB evidence from the snapshot verifier."""
    findings = _result.get("findings") or []
    db_evidence_findings = [
        f for f in findings
        if isinstance(f.get("db_evidence"), dict) and f["db_evidence"].get("status") == "captured"
    ]
    assert len(db_evidence_findings) >= 1, f"no finding with captured db_evidence; got {[(f.get('db_evidence',{}).get('status')) for f in findings]}"


def test_finding_is_confirmed_with_full_chain(_result: dict) -> None:
    findings = _result.get("findings") or []
    for f in findings:
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
        assert bool(f.get("raw_evidence")), f
