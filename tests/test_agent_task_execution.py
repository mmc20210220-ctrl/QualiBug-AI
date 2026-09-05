"""Execution orchestration tests; scan doubles are not discovery evidence."""
from types import SimpleNamespace

import pytest

from ai_test_asset_center import agent_task_store as store
from ai_test_asset_center.agent_task_grounding import _snapshot_ref
from ai_test_asset_center.agent_task_grounding_store import apply_agent_task_grounding
from ai_test_asset_center.private_pilot_agent_task_handlers import AgentTaskHandlersMixin
from ai_test_asset_center.private_pilot_scan_handlers import ScanHandlersMixin
from ai_test_asset_center.enterprise_knowledge_center import composition


@pytest.fixture
def tmp_path(tmp_path_factory):
    # CAS digest sidecars need room within Windows' legacy path limit.
    return tmp_path_factory.mktemp("at")


@pytest.fixture
def setup_task(tmp_path, monkeypatch):
    from ai_test_asset_center import agent_task_grounding, private_pilot_product_catalog, enterprise_source_registry
    monkeypatch.setattr(private_pilot_product_catalog, "_test_intelligence_source_fingerprint", lambda *a: "revision-a")
    monkeypatch.setattr(agent_task_grounding, "_scan_preflight_payload", lambda *a: {"ready": True, "input_checks": {"target": {"target_url": "http://localhost:9000"}, "environment": {"environment_type": "test"}}})
    monkeypatch.setattr(composition, "load_enterprise_business_knowledge_asset", lambda *a: {"version": "a"})
    monkeypatch.setattr(enterprise_source_registry, "compose_project_source_manifest", lambda *a, **kw: {"source_id": "source-a", "source_hash": "a" * 64})
    task = store.create_agent_task(tmp_path, tenant_id="tenant-a", project_id="project-a", goal="Validate existing behavior", intent="verify_changes")
    scope = dict(tenant_id="tenant-a", project_id="project-a", task_id=task["task_id"])
    apply_agent_task_grounding(tmp_path, **scope, grounding={"task_status": "BLOCKED", "source_snapshot": {"status": "PINNED", "snapshot_ref": _snapshot_ref("revision-a")}})

    class Handler(AgentTaskHandlersMixin, ScanHandlersMixin):
        server = SimpleNamespace()
        def _root(self): return tmp_path
        def _request_tenant(self): return "tenant-a"
        def _principal(self): return {"auth_type": "session"}
        def _require_role(self, *a): return True
        def _persist_scan_result(self, *a): return {}
        def _json(self, payload, status=200):
            self.response = (payload, status)
            return self.response

    return Handler(), scope


@pytest.mark.parametrize("disconnect", [False, True])
def test_canonical_scan_is_claimed_once_and_result_survives_disconnect(setup_task, tmp_path, monkeypatch, disconnect):
    from ai_test_asset_center import __main__, private_pilot_scan_handlers as handlers
    from ai_test_asset_center import private_pilot_scan_context_contract as context
    handler, scope = setup_task
    monkeypatch.setattr(context, "prepare_scan_body_for_campaign", lambda p, r, body: body)
    monkeypatch.setattr(handlers, "_prepare_v12_scan_body", lambda p, r, a, b, **kw: {**b, "api_doc": "source content"})
    monkeypatch.setattr(handlers, "_is_local_private_service", lambda *a: True)
    monkeypatch.setattr(handlers, "_validate_scan_base_url", lambda *a, **kw: None)
    monkeypatch.setattr(handlers, "_issue_runtime_approval_for_result", lambda *a, **kw: "")
    monkeypatch.setattr(handlers, "_response_stall_watchdog", lambda *a: {"mark": lambda *a: None})
    calls = []
    def scan(**kw):
        calls.append(kw)
        before = store.get_agent_task(tmp_path, **scope)
        assert before["execution_live"] is True
        assert before["execution_claim_status"] == "CLAIMED"
        assert kw["campaign_context"]["enterprise_understanding_snapshot_ref"] == before["execution_snapshot_ref"]
        assert kw["campaign_context"]["source_manifest"]["source_hash"] == "a" * 64
        kw["on_started"]("scan-test")
        assert store.get_agent_task(tmp_path, **scope)["scan_id"] == "scan-test"
        return {"scan_id": "scan-test", "success": True, "execution_status": "COMPLETED"}
    monkeypatch.setattr(__main__, "scan", scan)
    if disconnect:
        def broken_json(*a, **kw):
            assert store.get_agent_task(tmp_path, **scope)["status"] == "COMPLETED"
            raise BrokenPipeError("test disconnected browser")
        handler._json = broken_json
    request = {"execution_scope": "project", "read_only": True}
    if disconnect:
        with pytest.raises(BrokenPipeError):
            handler._handle_v12_scan("project-a", tmp_path, {"role": "admin"}, request, agent_task_id=scope["task_id"])
    else:
        handler._handle_v12_scan("project-a", tmp_path, {"role": "admin"}, request, agent_task_id=scope["task_id"])
    saved = store.get_agent_task(tmp_path, **scope)
    assert saved["status"] == "COMPLETED"
    assert saved["execution_live"] is False
    duplicate = handler._prepare_agent_task_execution("project-a", scope["task_id"], request, {"token": "successor"})
    assert duplicate["execute"] is False
    assert len(calls) == 1
    events = store.list_agent_task_events(tmp_path, **scope)
    assert [e["event_type"] for e in events][-4:] == ["EXECUTION_CLAIMED", "SCAN_ID_RECORDED", "EXECUTION_STARTED", "EXECUTION_RESULT_RECORDED"]


def test_lost_owner_is_uncertain_and_cannot_reclaim_or_reground(setup_task, tmp_path):
    handler, scope = setup_task
    request = {"execution_scope": "project"}
    binding = handler._prepare_agent_task_execution("project-a", scope["task_id"], request, {"token": "old-owner"})
    assert binding["execute"] is True
    saved = store.get_agent_task(tmp_path, **scope)
    assert saved["execution_recovery_required"] is True
    assert handler._prepare_agent_task_execution("project-a", scope["task_id"], request, {"token": "new-owner"})["execute"] is False
    with pytest.raises(store.AgentTaskConflict):
        apply_agent_task_grounding(tmp_path, **scope, grounding={})
    with pytest.raises(store.AgentTaskConflict):
        store.cancel_agent_task(tmp_path, **scope)
    with pytest.raises(store.AgentTaskConflict):
        store.finish_agent_task_execution(tmp_path, **scope, claim_id="wrong-claim", result={"ok": True})


@pytest.mark.parametrize("case", ["scope", "read_only", "stale", "preflight", "missing_asset", "analysis"])
def test_rejected_preparation_never_claims(setup_task, tmp_path, monkeypatch, case):
    from ai_test_asset_center import private_pilot_product_catalog, agent_task_grounding
    handler, scope = setup_task
    request = {"execution_scope": "project"}
    if case == "scope": request = {}
    if case == "read_only": request["read_only"] = "false"
    if case == "stale": monkeypatch.setattr(private_pilot_product_catalog, "_test_intelligence_source_fingerprint", lambda *a: "new")
    if case == "preflight": monkeypatch.setattr(agent_task_grounding, "_scan_preflight_payload", lambda *a: {"ready": False})
    if case == "missing_asset": monkeypatch.setattr(composition, "load_enterprise_business_knowledge_asset", lambda *a: None)
    if case == "analysis":
        task = store.create_agent_task(tmp_path, tenant_id="tenant-a", project_id="project-a", goal="Understand", intent="analyze_requirements")
        scope["task_id"] = task["task_id"]
    with pytest.raises((store.AgentTaskError, ValueError)):
        handler._prepare_agent_task_execution("project-a", scope["task_id"], request, {"token": "owner"})
    assert store.get_agent_task(tmp_path, **scope)["execution_claim_status"] == "NOT_CLAIMED"


def test_snapshot_versions_reuse_bytes_and_never_follow_latest(tmp_path, monkeypatch):
    asset = {"version": "a", "facts": ["one"]}
    monkeypatch.setattr(composition, "load_enterprise_business_knowledge_asset", lambda *a: asset)
    first = composition.pin_enterprise_business_knowledge_asset("project-a", tmp_path)
    assert composition.pin_enterprise_business_knowledge_asset("project-a", tmp_path) == first
    asset["facts"].append("two")
    second = composition.pin_enterprise_business_knowledge_asset("project-a", tmp_path)
    assert second != first
    assert composition.load_pinned_enterprise_business_knowledge_asset("project-a", tmp_path, first)["facts"] == ["one"]
    with pytest.raises(Exception):
        composition.load_pinned_enterprise_business_knowledge_asset("other-project", tmp_path, first)


def test_cancellation_cannot_target_successor_lease(tmp_path, monkeypatch):
    from ai_test_asset_center import scan_cancellation as cancel
    monkeypatch.setattr(cancel, "active_scan_owner", lambda *a: {"token": "successor"})
    result = cancel.request_scan_cancel(tmp_path, "project-a", expected_token="old-owner")
    assert result["requested"] is False
    assert result["reason_code"] == "SCAN_OWNER_MISMATCH"
    assert not cancel._cancel_path(tmp_path, "project-a").exists()
