from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import stage_reason_all_v2 as stage
from ai_test_asset_center.cognitive_memory_graph import CognitiveMemoryGraph
from ai_test_asset_center.reasoner_graph_context import (
    _STAGE_INSTALL_MARKER,
    _STAGE_ORIGINAL_MARKER,
    _persisted_asset_graph_bridge,
    build_reasoner_graph_context,
    install_reasoner_graph_context_bridge,
    project_behavior_ir_for_graph,
    reasoner_graph_context_scope,
)


def _behavior_ir() -> dict:
    return {
        "entities": [
            {
                "id": "ent_order",
                "name": "Order",
                "confidence": 0.95,
                "fields": [
                    {"name": "status", "semantic_type": "STATE"},
                    {"name": "total", "semantic_type": "AMOUNT_BALANCE"},
                ],
            }
        ],
        "operations": [
            {
                "id": "op_create",
                "method": "POST",
                "path": "/api/orders",
                "confidence": 0.95,
                "entity_refs": ["Order"],
                "source_refs": [
                    {"source_id": "api-doc", "locator": "POST /api/orders"}
                ],
            }
        ],
        "invariants": [
            {
                "id": "inv_total",
                "description": "Order total must remain non-negative",
                "confidence": 0.95,
                "source_refs": [{"source_id": "prd", "locator": "rule:order-total"}],
            }
        ],
        "states": [
            {"id": "state_paid", "name": "PAID", "confidence": 0.95}
        ],
        "relations": [
            {
                "id": "rel_pay",
                "relation_type": "transitions",
                "from_ref": "op_create",
                "to_ref": "state_paid",
                "operation_ref": "op_create",
                "source_refs": [{"source_id": "prd", "locator": "lifecycle:order"}],
            }
        ],
        "observation_surfaces": [
            {
                "id": "obs_order",
                "path": "/api/orders/{id}",
                "confidence": 0.9,
                "entity_refs": ["Order"],
            }
        ],
    }


def test_behavior_ir_projection_matches_existing_graph_sync_contract() -> None:
    projected = project_behavior_ir_for_graph(_behavior_ir())

    assert projected["entities"][0]["state_fields"] == ["status"]
    assert projected["entities"][0]["amount_fields"] == ["total"]
    assert projected["apis"][0]["entity"] == "Order"
    assert projected["candidate_invariants"][0]["definition"] == (
        "Order total must remain non-negative"
    )
    assert projected["candidate_invariants"][0]["evidence"] is True
    assert projected["candidate_lifecycle_transitions"][0]["operation_ref"] == "op_create"
    assert projected["observers"][0]["observer_id"] == "obs_order"


@pytest.mark.parametrize("mode", ["active", "shadow"])
def test_graph_context_pack_uses_existing_persisted_graph_and_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", mode)

    result = build_reasoner_graph_context(
        behavior_ir=_behavior_ir(),
        project_id="graph-bridge",
        environment_id="staging",
        root=tmp_path,
        run_id="run-1",
        policy_version="policy-1",
        source_ref="asset-1",
    )

    assert result["status"] == "READY"
    assert result["pack"]["graph_ready"] is True
    assert result["pack"]["graph_mode"] == mode
    assert result["pack"]["rendered_context"].startswith("QUALIBUG_GRAPH_CONTEXT_V1")
    assert result["stats"]["node_count"] > 0

    graph = CognitiveMemoryGraph("graph-bridge", "staging", tmp_path)
    assert graph.path == (
        tmp_path
        / "platform_workspace"
        / "graph-bridge"
        / "cognitive_memory_graph.sqlite3"
    )
    assert any(
        node["node_type"] == "API"
        and node["label"] == "POST:/api/orders"
        for node in graph.nodes()
    )


def test_graph_failure_is_fail_soft_and_leaves_pack_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_sync(*args, **kwargs):
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(CognitiveMemoryGraph, "sync_context", broken_sync)

    result = build_reasoner_graph_context(
        behavior_ir=_behavior_ir(),
        project_id="graph-fallback",
        environment_id="test",
        root=tmp_path,
    )

    assert result["status"] == "FAILED"
    assert result["pack"] == {}
    assert "RuntimeError" in result["error"]


def test_persisted_asset_bridge_loads_existing_asset_without_rebuilding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_test_asset_center.behavior_ir as behavior_ir_module
    import ai_test_asset_center.enterprise_knowledge_center as knowledge

    calls = {"load": 0, "ir": 0}

    def fake_load(project_id: str, root: Path):
        calls["load"] += 1
        return {"asset_id": "asset-persisted", "project_id": project_id}

    def fake_ir(asset, *, project_id="", **kwargs):
        calls["ir"] += 1
        assert asset["asset_id"] == "asset-persisted"
        return _behavior_ir()

    monkeypatch.setattr(
        knowledge,
        "load_enterprise_business_knowledge_asset",
        fake_load,
    )
    monkeypatch.setattr(
        behavior_ir_module,
        "build_behavior_ir_from_knowledge_asset",
        fake_ir,
    )
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "active")

    with reasoner_graph_context_scope(
        project_id="persisted-project",
        environment_id="staging",
        root=tmp_path,
        run_id="run-persisted",
        policy_version="policy-persisted",
    ):
        world, receipt = _persisted_asset_graph_bridge(
            reader_output={"documented_rules": []},
            project_id="persisted-project",
            root=tmp_path,
        )

    assert calls == {"load": 1, "ir": 1}
    assert receipt["status"] == "READY"
    assert receipt["source"] == "persisted_enterprise_knowledge_asset"
    assert world["_graph_evidence_pack"]["graph_ready"] is True
    assert world["_graph_evidence_pack"]["graph_mode"] == "active"
    assert world["_graph_memory_stats"]["node_count"] > 0


def test_installed_bridge_hydrates_reader_output_but_preserves_raw_fallback_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_test_asset_center.behavior_ir as behavior_ir_module
    import ai_test_asset_center.enterprise_knowledge_center as knowledge

    captured: dict = {}

    def fake_original(
        prd_text,
        api_spec,
        *,
        reader_output=None,
        prior_findings=None,
        project_id="",
        root=None,
    ):
        captured["prd_text"] = prd_text
        captured["api_spec"] = api_spec
        captured["reader_output"] = reader_output
        captured["project_id"] = project_id
        captured["root"] = root
        return [], {"status": "empty"}

    monkeypatch.setattr(stage, "collect_reasoner_hypotheses", fake_original)
    monkeypatch.setattr(stage, _STAGE_INSTALL_MARKER, False, raising=False)
    monkeypatch.setattr(stage, _STAGE_ORIGINAL_MARKER, fake_original, raising=False)
    monkeypatch.setattr(
        knowledge,
        "load_enterprise_business_knowledge_asset",
        lambda project_id, root: {"asset_id": "asset-existing"},
    )
    monkeypatch.setattr(
        behavior_ir_module,
        "build_behavior_ir_from_knowledge_asset",
        lambda asset, *, project_id="", **kwargs: _behavior_ir(),
    )
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "active")

    install_reasoner_graph_context_bridge()
    with reasoner_graph_context_scope(
        project_id="mainline-project",
        environment_id="staging",
        root=tmp_path,
    ):
        stage.collect_reasoner_hypotheses(
            "RAW PRD FALLBACK",
            "RAW API FALLBACK",
            reader_output={"documented_rules": []},
            project_id="mainline-project",
            root=tmp_path,
        )

    assert captured["prd_text"] == "RAW PRD FALLBACK"
    assert captured["api_spec"] == "RAW API FALLBACK"
    pack = captured["reader_output"]["_graph_evidence_pack"]
    assert pack["graph_ready"] is True
    assert pack["graph_mode"] == "active"
