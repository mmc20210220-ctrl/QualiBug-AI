"""Regression: mainline LLM Reasoner augmentation wiring.

The 11-engine Reasoner (stage_reason_all_v2) used to be reachable only from
side loops (self_improving_loop / sweep_loop); the mainline planning authority
never consumed LLM business reasoning and discovery breadth was structurally
capped. These tests pin the wiring contract of ``build_discovery_plan``:

* reasoner hypotheses flow through the governed bridge
  (``hypothesis_slice_bridge.hypotheses_to_obligations``) and join the plan
  with ``derivation="mainline_reasoner"``;
* the campaign flag ``mainline_reasoner_enabled`` and the env kill-switch
  ``QUALIBUG_MAINLINE_REASONER_DISABLED`` skip collection visibly;
* a reasoner failure never aborts the mainline and never degrades silently —
  the FAILED receipt travels with the planning bundle.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs
from ai_test_asset_center.discovery_mainline_contract import MainlineContractError
from ai_test_asset_center import discovery_runtime_planning as planning

API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {
            "/orders": {
                "get": {"operationId": "listOrders"},
                "post": {"operationId": "createOrder"},
            }
        },
    }
)


def _inputs(tmp_path: Path, *, extra_context: dict | None = None) -> DiscoveryMainlineInputs:
    context = {
        "mainline_authority": "experiment_candidate",
        "run_id": "RUN-1",
        "campaign_id": "CMP-1",
        "target_id": "TARGET-1",
        "environment_id": "ENV-1",
        "policy_id": "policy-1",
        "policy_version": "v2",
        "strategy_fingerprint": "a" * 64,
        "evaluation_mode": "replay",
    }
    context.update(extra_context or {})
    return DiscoveryMainlineInputs(
        project="PROBE-1",
        root=tmp_path,
        prd_text="Orders must be created and listed.",
        api_spec_text=API_SPEC,
        db_schema_text="",
        approved_base_url="http://127.0.0.1:8080",
        campaign_context=context,
    )


def _campaign() -> SimpleNamespace:
    return SimpleNamespace(campaign_id="CMP-1", slice_budget=500)


def _patch_bridge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    obligations: list[dict],
    collect_raises: Exception | None = None,
) -> dict:
    """Patch the reasoner entry + bridge; return a call counter."""
    import ai_test_asset_center.stage_reason_all_v2 as stage
    import ai_test_asset_center.hypothesis_slice_bridge as bridge

    calls = {"collect": 0, "bridge": 0, "world": None, "project": None, "root": None}

    def fake_collect(
        prd_text: str,
        api_text: str,
        *,
        reader_output=None,
        project_id: str = "",
        root: Path | None = None,
    ):
        calls["collect"] += 1
        # The comprehension bridge must be live: the reasoner receives the
        # source-derived world model, never an empty business-world dict.
        calls["world"] = reader_output
        calls["project"] = project_id
        calls["root"] = root
        if collect_raises is not None:
            raise collect_raises
        return (
            [{"hypothesis": "probe", "endpoint_hint": "POST /orders"}],
            {"status": "ok", "total_engines": 1},
        )

    def fake_bridge(hypotheses, *, api_endpoints, behavior_ir, origin):
        calls["bridge"] += 1
        assert origin == "mainline_reasoner"
        return (
            {"obligations": [dict(row) for row in obligations], "coverage_gaps": []},
            {
                "input": len(hypotheses),
                "bound": len(hypotheses),
                "dropped_no_endpoint": 0,
                "adapted_obligation_count": len(obligations),
                "adapter_coverage_gap_count": 0,
            },
        )

    monkeypatch.setattr(stage, "collect_reasoner_hypotheses", fake_collect)
    monkeypatch.setattr(bridge, "hypotheses_to_obligations", fake_bridge)
    return calls


def test_mainline_reasoner_hypotheses_become_source_bound_obligations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # conftest disables the reasoner for suite determinism; this test exercises
    # the live wiring path with patched (offline) entry points.
    monkeypatch.delenv("QUALIBUG_MAINLINE_REASONER_DISABLED", raising=False)
    calls = _patch_bridge(
        monkeypatch,
        obligations=[{"obligation_id": "obl_reasoner_probe_1"}],
    )

    bundle = planning.build_discovery_plan(_inputs(tmp_path), _campaign())

    assert calls == {
        "collect": 1,
        "bridge": 1,
        "world": calls["world"],
        "project": "PROBE-1",
        "root": tmp_path,
    }
    world = calls["world"]
    assert isinstance(world, dict)
    assert "documented_rules" in world and "entities" in world
    assert "state_machines" in world and "relationships" in world
    report = bundle.obligations.get("mainline_reasoner_report")
    assert report is not None
    assert report["schema_version"] == "qualibug.mainline-reasoner-receipt.v1"
    assert report["status"] == "ok"
    assert report["hypotheses_generated"] == 1
    assert report["obligations_added"] == 1
    assert report["bridge_funnel"]["bound"] == 1
    assert isinstance(report.get("world_model"), dict)
    assert report["world_model"]["documented_rules"] == len(
        world.get("documented_rules") or []
    )
    assert report["world_model"]["semantic_hypotheses"] == len(
        world.get("semantic_hypotheses") or []
    )

    obligation_ids = {
        row.get("obligation_id")
        for row in bundle.obligations.get("obligations", [])
        if isinstance(row, dict)
    }
    assert "obl_reasoner_probe_1" in obligation_ids
    reasoner_rows = [
        row
        for row in bundle.obligations.get("obligations", [])
        if isinstance(row, dict)
        and row.get("obligation_id") == "obl_reasoner_probe_1"
    ]
    assert reasoner_rows[0].get("derivation") == "mainline_reasoner"


def test_mainline_reasoner_disabled_flag_skips_collection_visibly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_bridge(monkeypatch, obligations=[])

    bundle = planning.build_discovery_plan(
        _inputs(tmp_path, extra_context={"mainline_reasoner_enabled": False}),
        _campaign(),
    )

    assert calls == {
        "collect": 0,
        "bridge": 0,
        "world": None,
        "project": None,
        "root": None,
    }
    report = bundle.obligations.get("mainline_reasoner_report")
    assert report is not None
    assert report["status"] == "NOT_REQUESTED"
    assert report["obligations_added"] == 0


def test_mainline_reasoner_env_kill_switch_skips_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_bridge(monkeypatch, obligations=[])
    monkeypatch.setenv("QUALIBUG_MAINLINE_REASONER_DISABLED", "1")

    bundle = planning.build_discovery_plan(_inputs(tmp_path), _campaign())

    assert calls == {
        "collect": 0,
        "bridge": 0,
        "world": None,
        "project": None,
        "root": None,
    }
    report = bundle.obligations.get("mainline_reasoner_report")
    assert report is not None
    assert report["status"] == "NOT_REQUESTED"


def test_mainline_reasoner_non_boolean_flag_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_bridge(monkeypatch, obligations=[])

    with pytest.raises(
        MainlineContractError, match="mainline_reasoner_enabled_not_boolean"
    ):
        planning.build_discovery_plan(
            _inputs(tmp_path, extra_context={"mainline_reasoner_enabled": "yes"}),
            _campaign(),
        )


def test_mainline_reasoner_failure_degrades_with_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QUALIBUG_MAINLINE_REASONER_DISABLED", raising=False)
    calls = _patch_bridge(
        monkeypatch,
        obligations=[],
        collect_raises=RuntimeError("provider exploded"),
    )

    bundle = planning.build_discovery_plan(_inputs(tmp_path), _campaign())

    assert calls["collect"] == 1
    assert calls["bridge"] == 0
    report = bundle.obligations.get("mainline_reasoner_report")
    assert report is not None
    assert report["status"] == "FAILED"
    assert "RuntimeError" in report["error"]
    assert report["obligations_added"] == 0
    # Deterministic obligation pool must survive the reasoner failure.
    assert bundle.obligations.get("obligations") is not None
