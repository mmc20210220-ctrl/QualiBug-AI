from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_test_asset_center import behavior_ir
from ai_test_asset_center import pipeline_runtime
from ai_test_asset_center import private_pilot_http_routing as routing
from ai_test_asset_center import private_pilot_scan_context_contract as scan_context
from ai_test_asset_center import private_pilot_scan_handlers as scan_handlers
from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_documents,
)
from ai_test_asset_center.private_pilot_ui_upload_scenario_routes import (
    install_private_pilot_ui_upload_scenario_routes,
)
from ai_test_asset_center.private_pilot_ui_upload_scenario_scan_gate import (
    install_ui_upload_scenario_scan_gate,
)
from ai_test_asset_center.source_ui_contract_binding import bind_source_ui_contracts
from ai_test_asset_center.ui_upload_fixture_registry import (
    approve_upload_fixture,
    register_upload_fixture,
)
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)
from ai_test_asset_center.ui_upload_scenario_registry import (
    approve_upload_scenario,
    approved_upload_scenario,
    register_upload_scenario,
)
from ai_test_asset_center.ui_upload_scenario_runtime_binding import (
    _hydrate_scenarios,
    install_ui_upload_scenario_runtime_binding,
)
from ai_test_asset_center.ui_upload_scenario_semantic_authority import (
    install_ui_upload_scenario_semantic_authority,
)
from ai_test_asset_center.ui_upload_scenario_source_authority import (
    install_ui_upload_scenario_source_authority,
)

_PROJECT = "upload-scenario-http"
_ACTOR = {"name": "qa-owner", "role": "qa_lead"}
_SAFE_INTERFACE = "api:GET:/customers/upload"


class _Handler:
    def __init__(
        self,
        root: Path,
        *,
        path: str,
        body: bytes = b"",
        role_allowed: bool = True,
    ) -> None:
        self.path = path
        self.command = "POST"
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.server = SimpleNamespace(server_address=("127.0.0.1", 8088))
        self._test_root = root
        self.role_allowed = role_allowed
        self.responses: list[tuple[int, Any]] = []

    def _init_request_context(self) -> None:
        return None

    def _root(self) -> Path:
        return self._test_root

    def _require_actor(self) -> dict[str, str]:
        return dict(_ACTOR)

    def _require_tenant(self, _root: Path) -> str:
        return "tenant-1"

    def _require_project_scope(self, project: str) -> bool:
        return project == _PROJECT

    def _require_role(
        self,
        _actor: dict[str, str],
        _allowed: set[str],
        _action: str,
    ) -> bool:
        if self.role_allowed:
            return True
        self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        return False

    def _body(self) -> dict[str, Any]:
        raw = self.rfile.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _json(self, payload: Any, status: int = 200, **_kwargs: Any) -> None:
        self.responses.append((status, payload))


@pytest.fixture(autouse=True)
def _install() -> None:
    install_upload_fixture_registry_integrity()
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    install_ui_upload_scenario_runtime_binding()
    install_private_pilot_ui_upload_scenario_routes()
    install_ui_upload_scenario_scan_gate()


def _seed_authorities(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "paths": {
            "/customers/upload": {
                "get": {
                    "operationId": "getCustomerUploadPage",
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "submitCustomerUpload",
                    "responses": {"200": {"description": "ok"}},
                },
            }
        },
    }
    permissions = {
        "permissions": [
            {
                "role": "admin",
                "resource": "/customers/upload",
                "actions": ["read"],
                "decision": "allow",
            }
        ]
    }
    source_result = ingest_enterprise_knowledge_documents(
        _PROJECT,
        [
            {
                "text": "# Bulk upload\nAdmin uploads a CSV and sees 上传成功.",
                "filename": "bulk-upload-ui.md",
                "source_type": "uiux_spec",
            },
            {
                "text": json.dumps(openapi),
                "filename": "upload-openapi.json",
                "source_type": "openapi",
            },
            {
                "text": json.dumps(permissions),
                "filename": "upload-permissions.json",
                "source_type": "permission_matrix",
            },
        ],
        root=tmp_path,
        actor=_ACTOR,
    )
    source = next(
        row
        for row in source_result["created"]
        if row.get("original_name") == "bulk-upload-ui.md"
    )
    # Build the knowledge asset once before any fixture/registry writes. The
    # scenario semantic authority reads this persisted asset when it binds the
    # source, safe prerequisite operation and actor role; without it the first
    # register_upload_scenario call falls back to a full knowledge build whose
    # source-sync pass re-ingests the fixture bytes and can race the fixture
    # registry's atomic write on Windows, intermittently removing the registry
    # file and failing with active_approved_upload_fixture_not_found.
    build_enterprise_business_knowledge_asset(
        _PROJECT,
        tmp_path,
        options={"enable_semantic_extraction": False},
    )
    fixture_path = tmp_path / "platform_inputs" / _PROJECT / "inbox" / "bulk.csv"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text("id,name\n1,Alice\n", encoding="utf-8")
    registered = register_upload_fixture(
        _PROJECT,
        file_path=fixture_path,
        fixture_name="bulk-csv",
        root=tmp_path,
        actor=_ACTOR,
    )
    fixture = approve_upload_fixture(
        _PROJECT,
        fixture_id=registered["fixture"]["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]
    return source, fixture


def _scenario_payload(source_id: str, binding_ref: str) -> dict[str, Any]:
    return {
        "title": "客户批量上传",
        "source_id": source_id,
        "source_locator": "heading:Bulk upload",
        "operation_ref": _SAFE_INTERFACE,
        "actor_role": "admin",
        "start_url": "/customers/upload",
        "fixture_binding_refs": [binding_ref],
        "upload_selector": "input[type=file]",
        "submission_mode": "click_submit",
        "submit_selector": "#upload-submit",
        "cleanup_selector": "#remove-upload",
        "assertion_selector": "#upload-result",
        "assertion_text": "上传成功",
        "rendered_probe_selector": "#upload-result",
        "persistent_probe_url": "/api/customers/import/state",
        "persistent_json_pointer": "/count",
    }


def _approved_scenario(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source, fixture = _seed_authorities(tmp_path)
    candidate = register_upload_scenario(
        _PROJECT,
        _scenario_payload(source["source_id"], fixture["binding_ref"]),
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]
    approved = approve_upload_scenario(
        _PROJECT,
        scenario_id=candidate["scenario_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["scenario"]
    return candidate, approved


def test_project_routes_register_approve_list_and_revoke(tmp_path: Path) -> None:
    source, fixture = _seed_authorities(tmp_path)
    register_body = json.dumps(
        {
            "action": "register",
            "payload": _scenario_payload(source["source_id"], fixture["binding_ref"]),
        }
    ).encode("utf-8")
    register = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-scenarios",
        body=register_body,
    )
    routing.HttpRoutingMixin.do_POST(register)
    assert register.responses[-1][0] == 201
    candidate = register.responses[-1][1]["scenario"]
    assert candidate["authority"] == "source_declared_candidate"

    approve = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-scenarios",
        body=json.dumps(
            {"action": "approve", "scenario_id": candidate["scenario_id"]}
        ).encode("utf-8"),
    )
    routing.HttpRoutingMixin.do_POST(approve)
    approved = approve.responses[-1][1]["scenario"]
    assert approved["authority"] == "approved_copy"
    assert approved["scenario_ref"].startswith("uisr_")

    listing = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-scenarios",
    )
    listing.command = "GET"
    routing.HttpRoutingMixin.do_GET(listing)
    authorities = {
        row["authority"] for row in listing.responses[-1][1]["scenarios"]
    }
    assert authorities == {"source_declared_candidate", "approved_copy"}

    revoke = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-scenarios",
        body=json.dumps(
            {
                "action": "revoke",
                "scenario_id": candidate["scenario_id"],
                "reason": "contract replaced",
            }
        ).encode("utf-8"),
    )
    routing.HttpRoutingMixin.do_POST(revoke)
    assert revoke.responses[-1][1]["status"] == "REVOKED"
    assert len(revoke.responses[-1][1]["revoked_records"]) == 2


def test_project_route_requires_configuration_role(tmp_path: Path) -> None:
    handler = _Handler(
        tmp_path,
        path=f"/api/v1/projects/{_PROJECT}/ui-upload-scenarios",
        body=json.dumps({"action": "register", "payload": {}}).encode("utf-8"),
        role_allowed=False,
    )
    routing.HttpRoutingMixin.do_POST(handler)
    assert handler.responses == [(403, {"ok": False, "error": "FORBIDDEN"})]


def test_typed_scan_gate_rejects_missing_and_malformed_scenarios(tmp_path: Path) -> None:
    missing = _Handler(tmp_path, path="/api/v1/scan")
    scan_handlers.ScanHandlersMixin._handle_v12_scan(
        missing,
        _PROJECT,
        tmp_path,
        _ACTOR,
        {"ui_upload_scenario_ids": ["uisr_00000000000000000000"]},
    )
    assert missing.responses[-1][0] == 409
    assert missing.responses[-1][1]["error"] == "UPLOAD_SCENARIO_NOT_ACTIVE"

    malformed = _Handler(tmp_path, path="/api/v1/scan")
    scan_handlers.ScanHandlersMixin._handle_v12_scan(
        malformed,
        _PROJECT,
        tmp_path,
        _ACTOR,
        {"ui_upload_scenario_ids": "not-a-list"},
    )
    assert malformed.responses[-1][0] == 400
    assert malformed.responses[-1][1]["error"] == "UPLOAD_SCENARIO_BAD_REQUEST"


def test_scenario_summary_reaches_campaign_and_runtime_contract(tmp_path: Path) -> None:
    _candidate, approved = _approved_scenario(tmp_path)
    prepared = _hydrate_scenarios(
        _PROJECT,
        tmp_path,
        {"ui_upload_scenario_ids": [approved["scenario_ref"]]},
    )
    context = scan_context.build_campaign_context_from_scan_body(prepared)
    runtime = pipeline_runtime._runtime_contract(context, "", "")

    assert context["ui_upload_scenario_binding_summary"]["scenario_count"] == 1
    assert runtime["ui_upload_scenario_binding_summary"]["scenario_refs"] == [
        approved["scenario_ref"]
    ]


def test_scenario_binds_through_formal_overlay_and_behavior_ir(tmp_path: Path) -> None:
    _candidate, approved = _approved_scenario(tmp_path)
    materialized = approved_upload_scenario(
        _PROJECT,
        approved["scenario_ref"],
        root=tmp_path,
    )
    asset = build_enterprise_business_knowledge_asset(
        _PROJECT,
        tmp_path,
        options={"enable_semantic_extraction": False},
    )
    from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
    from ai_test_asset_center import scan_ui_contract_overlay as overlay

    overlaid, overlay_receipt = overlay.overlay_scan_ui_contracts(
        asset,
        {"ui_execution_requests": [materialized["ui_execution_request"]]},
    )
    model = behavior_ir.build_behavior_ir_from_knowledge_asset(
        overlaid,
        project_id=_PROJECT,
        runtime_actors=[
            {
                "role": "admin",
                "account_ref": "admin-e2e",
                "secret_ref": "vault://qualibug/admin-e2e",
                "status": "active",
            }
        ],
    )
    bound_model, binding_receipt = bind_source_ui_contracts(model, overlaid)

    assert overlay_receipt["source_registry_guard"]["status"] == "ACCEPTED"
    assert overlay_receipt["contract_added_count"] == 1
    assert binding_receipt["status"] == "BOUND"
    assert binding_receipt["bound_invariant_count"] == 1
    assert binding_receipt["coverage_gap_count"] == 0
    invariant = next(
        row
        for row in bound_model["invariants"]
        if row.get("ui_contract_id") == materialized["contract_id"]
    )
    assert invariant["ui_request"]["actor_role"] == "admin"
    assert invariant["ui_request"]["operation_ref"] == _SAFE_INTERFACE
    steps = invariant["ui_request"]["browser_plan"]["steps"]
    assert [(row["phase"], row["action"]) for row in steps] == [
        ("setup", "goto"),
        ("treatment", "set_input_files"),
        ("treatment", "click"),
        ("assertion", "expect_text"),
        ("cleanup", "click"),
        ("cleanup", "set_input_files"),
    ]
