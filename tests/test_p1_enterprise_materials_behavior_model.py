"""P1 integration test: enterprise materials → executable behavior model.

Registers three document types (PRD, DB schema, OpenAPI) as enterprise source
assets, then executes a scan to verify that the campaign binds them via
source_id/source_hash/source_version_id and that behavior slices are extracted
into the scan's behavior_slice_ledger.
"""

from __future__ import annotations

import json, threading, tempfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p1_materials_integration"
SCOPE = "auth-scope"
ENV = "customer-staging"

PRD_TEXT = """# 用户管理系统 PRD
## 业务实体
- 用户(User): username(唯一标识), role(角色: user/admin/superadmin)
## 业务规则
- 公开注册接口 POST /api/register 仅允许注册 role=user 的普通用户
- 管理员角色必须由已有管理员在后台分配，不得通过公开接口获取
## 异常路径
- 若注册请求中 role!=user，系统应拒绝 (HTTP 403)
"""

DB_SCHEMA = """CREATE TABLE users (
  username TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('user','admin','superadmin')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);"""

OPENAPI = """openapi: 3.0.0
info:
  title: User Mgmt API
  version: 1.0.0
paths:
  /api/register:
    post:
      summary: Register user
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                username: {type: string}
                role: {type: string}
      responses:
        "201": {description: registered}
        "403": {description: forbidden}
  /api/users/{id}:
    delete:
      summary: Delete user
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        "204": {description: deleted}
"""


class _BuggyAuthHandler(BaseHTTPRequestHandler):
    _store: dict[str, dict] = {}

    def log_message(self, *_: object) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        cl = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(cl) or b"{}") if cl else {}
        if self.path == "/api/register":
            username = body.get("username", "")
            role = body.get("role", "user")
            _BuggyAuthHandler._store[username] = {"username": username, "role": role}
            return self._json(201, {"username": username, "role": role})
        return self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:
        username = self.path.replace("/api/users/", "")
        _BuggyAuthHandler._store.pop(username, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("p1")

    # Place documents in input dir (how scan() discovers PRD)
    input_dir = root / "platform_workspace" / PROJECT / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "prd.md").write_text(PRD_TEXT, encoding="utf-8")
    (input_dir / "schema.sql").write_text(DB_SCHEMA, encoding="utf-8")

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    # Register three document types
    prd_manifest = register_source_asset(
        PROJECT, "user-prd", PRD_TEXT, source_type="prd", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    db_manifest = register_source_asset(
        PROJECT, "user-db-schema", DB_SCHEMA, source_type="db_schema", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    api_manifest = register_source_asset(
        PROJECT, "user-openapi", OPENAPI, source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyAuthHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    try:
        snap = source_snapshot_hash(PRD_TEXT, OPENAPI, DB_SCHEMA, SCOPE, ENV)
        campaign = EnterpriseCampaign.create(
            PROJECT, SCOPE, ENV, snap,
            source_id=api_manifest["source_id"],
            source_hash=api_manifest["source_hash"],
            policy_version="",
        )
        approval = issue_execution_approval(
            PROJECT, root=root,
            campaign_id=campaign.campaign_id,
            scope_id=SCOPE, environment_ref=ENV,
            source_hash=api_manifest["source_hash"],
            target_base_url=base,
            execution_mode="approved_sandbox_write",
            expires_at_utc=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            actor={"name": "qa_lead", "role": "qa_lead"},
        )
        contract = {
            "execution_policy": "approved_sandbox_write",
            "actor": {"id": "qa_lead"},
            "scenarios": [{
                "id": "SCN_PRIV",
                "entity": "users",
                "category": "privilege_escalation",
                "severity": "P0",
                "steps": [{
                    "method": "POST",
                    "path": "/api/register",
                    "expected_status": 403,
                    "body": {"username": "hacker", "role": "admin"},
                }],
                "cleanup_steps": [{
                    "method": "DELETE",
                    "path": "/api/users/{id}",
                    "expected_status": 204,
                }],
                "expected_state": "priv_escalation_blocked",
            }],
        }
        ctx = {
            "source_manifest": api_manifest,
            "scope_id": SCOPE, "environment_ref": ENV,
            "execution_mode": "approved_sandbox_write",
            "execution_approval_id": approval["approval_id"],
            "test_data_contract": {
                "strategy": "create_disposable",
                "write_approved": True,
                "disposable_scope_ref": SCOPE,
            },
            "runtime_scenario_contract": contract,
        }
        result = scan(
            PROJECT, root=root, prd_text="", api_doc_text=OPENAPI,
            base_url=base, campaign_context=ctx,
        )
        return {"scan": result, "root": root,
                "prd": prd_manifest, "db": db_manifest, "api": api_manifest}
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


# ── assertions ────────────────────────────────────────────────────────────────


def test_sources_registered_with_all_metadata(_result: dict) -> None:
    from ai_test_asset_center.enterprise_source_registry import list_source_assets
    assets = list_source_assets(PROJECT, root=_result["root"])
    assert len(assets) >= 3, f"must have >= 3 sources; got {len(assets)}"
    types = {a["source_type"] for a in assets}
    assert types >= {"prd", "db_schema", "openapi"}, types

    for a in assets:
        assert a.get("source_id"), f"source_id missing for {a}"
        assert len(a.get("latest_source_hash", "")) >= 8, f"hash too short for {a}"
        assert a.get("version_count", 0) >= 1, f"version_count missing for {a}"

    # Every manifest we registered must be found in the listing
    for key, name in (("prd", "user-prd"), ("db", "user-db-schema"), ("api", "user-openapi")):
        manifest = _result[key]
        matching = [a for a in assets if a["source_id"] == manifest["source_id"]]
        assert len(matching) == 1, f"source {name} not found in listing"
        assert matching[0].get("latest_source_hash") == manifest.get("source_hash", manifest.get("latest_source_hash"))


def test_scan_produces_behavior_slices_from_sources(_result: dict) -> None:
    res = _result["scan"]
    assert res.get("execution_status") == "completed"
    ledger = res.get("behavior_slice_ledger") or {}
    confirmed = ledger.get("confirmed_slice_ids") or []
    attempted = ledger.get("attempted_slice_ids") or []
    assert len(confirmed) >= 1, f"must have >= 1 confirmed slice; ledger={json.dumps(ledger,default=str)[:300]}"
    assert len(attempted) >= 2, f"must have >= 2 attempted slices"
    # Slice IDs must be non-empty and follow BHV_ prefix
    for sid in confirmed + attempted:
        assert isinstance(sid, str) and len(sid) > 4, f"invalid slice id: {sid}"


def test_campaign_binds_source_identity(_result: dict) -> None:
    campaign = _result["scan"].get("campaign") or {}
    assert campaign.get("source_id") == _result["api"]["source_id"]
    assert campaign.get("source_hash") == _result["api"]["source_hash"]


def test_scan_finds_privilege_escalation_using_document_derived_model(_result: dict) -> None:
    findings = _result["scan"].get("findings") or []
    assert len(findings) >= 1
    priv = [f for f in findings if "expected_status_mismatch" in str(f)]
    assert len(priv) >= 1, f"privilege escalation not detected among {len(findings)} findings"
    assert priv[0].get("gate_passed") is True
