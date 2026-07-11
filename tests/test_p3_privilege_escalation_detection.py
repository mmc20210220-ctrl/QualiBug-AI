"""P3 integration test: privilege escalation detection via runtime scenario contract.

A buggy SUT accepts role=admin on public registration (privilege escalation).
The HttpStatusOracle must detect the expected_status (403) vs actual (201)
mismatch and produce a confirmed defect with full evidence chain.
"""

from __future__ import annotations

import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_priv_escalation_integration"
SCOPE = "auth-scope"
ENV = "customer-staging"

OPENAPI = """
openapi: 3.0.0
info:
  title: Buggy Auth API
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
            # BUG: accepts role=admin from any request (privilege escalation)
            _BuggyAuthHandler._store[username] = {"username": username, "role": role}
            return self._json(201, {"username": username, "role": role})
        return self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:
        username = self.path.replace("/api/users/", "")
        _BuggyAuthHandler._store.pop(username, None)
        return self._json(204, {})


def _run_scan(root: Path, base_url: str) -> dict:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "buggy-auth-openapi", OPENAPI,
        source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    snap = source_snapshot_hash("", OPENAPI, "", SCOPE, ENV)
    campaign = EnterpriseCampaign.create(
        PROJECT, SCOPE, ENV, snap,
        source_id=manifest["source_id"],
        source_hash=manifest["source_hash"],
        policy_version="",
    )
    approval = issue_execution_approval(
        PROJECT, root=root,
        campaign_id=campaign.campaign_id,
        scope_id=SCOPE, environment_ref=ENV,
        source_hash=manifest["source_hash"],
        target_base_url=base_url,
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
            "id": "SCN_PRIV_ESC",
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
            "expected_state": "privilege_escalation_blocked",
        }],
    }
    ctx = {
        "source_manifest": manifest,
        "scope_id": SCOPE, "environment_ref": ENV,
        "environment_type": "test",
        "execution_mode": "approved_sandbox_write",
        "execution_approval_id": approval["approval_id"],
        "test_data_contract": {
            "strategy": "create_disposable",
            "write_approved": True,
            "disposable_scope_ref": SCOPE,
        },
        "runtime_scenario_contract": contract,
    }
    return scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI,
                base_url=base_url, campaign_context=ctx)


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("priv_esc")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyAuthHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        return _run_scan(root, base)
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


# ── assertions ────────────────────────────────────────────────────────────────


def test_scan_produces_privilege_escalation_finding(_result: dict) -> None:
    findings = _result.get("candidate_findings") or []
    assert _result.get("execution_status") == "completed"
    assert _result.get("grade") == "partial_coverage"
    assert _result.get("coverage_honesty", {}).get("downgraded") is True
    assert _result.get("findings") == []
    assert len(findings) >= 1, f"must detect privilege escalation; got {len(findings)}"


def test_privilege_escalation_finding_is_confirmed_with_evidence(_result: dict) -> None:
    findings = _result.get("candidate_findings") or []
    priv = [
        f for f in findings
        if any(token in str(f).lower()
               for token in ("expected_status_mismatch", "privilege", "admin", "403"))
    ]
    assert len(priv) >= 1, f"no privilege escalation finding among {len(findings)}"
    f = priv[0]
    assert f.get("severity") in ("P0", "P1"), f
    assert f.get("gate_passed") is False, f
    assert f.get("customer_delivery_status") == "candidate", f
    reasons = f.get("customer_delivery_gate_reasons") or []
    assert "CLEANUP_NOT_SUCCEEDED" in reasons, f
    assert "IDENTITY_CHAIN_INCOMPLETE" not in reasons, f
    # Evidence completeness
    oracle = f.get("oracle") or {}
    assert oracle.get("violated_rule") == "expected_status_mismatch", oracle
    assert "403" in str(f.get("expected", "")), f"expected must mention 403: {f.get('expected')}"
    assert "201" in str(f.get("actual", "")), f"actual must mention 201: {f.get('actual')}"
    assert len(f.get("reproduction_steps") or []) >= 1, f
    assert bool(f.get("raw_evidence")), f
