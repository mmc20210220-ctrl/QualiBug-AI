from __future__ import annotations

from pathlib import Path


PLANNING = Path("ai_test_asset_center/discovery_runtime_planning.py")
TEST = Path("tests/test_reasoner_persisted_graph_context_bridge.py")


HELPER = r'''

def _graph_project_context_from_knowledge_asset(
    asset: dict[str, Any],
    world: dict[str, Any],
) -> dict[str, Any]:
    """Adapt persisted Enterprise Knowledge into CognitiveMemoryGraph input.

    This bridge is deterministic: it only reshapes facts already present in the
    persisted knowledge asset/world projection. It never invokes Reader/LLM
    parsing and therefore never re-understands raw PRD/API for Graph Context.
    """

    def _source_ref(row: dict[str, Any]) -> str:
        refs = [
            ref
            for ref in _list(row.get("source_refs"))
            if isinstance(ref, dict)
        ]
        if refs:
            first = refs[0]
            return _text(
                first.get("source_id")
                or first.get("document")
                or first.get("source")
                or first.get("locator")
            )
        return _text(row.get("source") or row.get("source_id"))

    entities: list[dict[str, Any]] = []
    for raw in _list(world.get("entities")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if not _text(row.get("name") or row.get("entity") or row.get("title")):
            continue
        # sync_context requires numeric confidence. The projected is_core flag
        # already comes from persisted source confidence, so preserve only that
        # existing distinction rather than inferring a new business fact.
        row["confidence"] = 1.0 if bool(row.get("is_core")) else 0.7
        entities.append(row)

    apis: list[dict[str, Any]] = []
    for raw in _list(asset.get("interfaces")):
        if not isinstance(raw, dict):
            continue
        method = _text(raw.get("method") or raw.get("http_method")) or "GET"
        path = _text(raw.get("path") or raw.get("endpoint") or raw.get("route"))
        if not path:
            continue
        row = dict(raw)
        row["method"] = method.upper()
        row["path"] = path
        if "confidence" not in row:
            row["confidence"] = 1.0
        apis.append(row)

    invariants: list[dict[str, Any]] = []
    for raw in _list(world.get("documented_rules")):
        if not isinstance(raw, dict):
            continue
        definition = _text(
            raw.get("rule")
            or raw.get("definition")
            or raw.get("description")
        )
        if not definition:
            continue
        refs = [
            dict(ref)
            for ref in _list(raw.get("source_refs"))
            if isinstance(ref, dict)
        ]
        invariants.append({
            **dict(raw),
            "definition": definition,
            "source_ref": _source_ref(raw),
            "evidence": refs,
        })

    transitions: list[dict[str, Any]] = []
    for machine in _list(world.get("state_machines")):
        if not isinstance(machine, dict):
            continue
        entity = _text(machine.get("entity") or machine.get("object"))
        source_ref = _source_ref(machine)
        evidence = [
            dict(ref)
            for ref in _list(machine.get("source_refs"))
            if isinstance(ref, dict)
        ]
        for raw_transition in _list(machine.get("transitions")):
            if not isinstance(raw_transition, dict):
                continue
            from_state = _text(raw_transition.get("from"))
            to_state = _text(raw_transition.get("to"))
            trigger = _text(raw_transition.get("trigger"))
            if from_state or to_state:
                definition = f"{from_state or '?'} -> {to_state or '?'}"
            elif trigger:
                definition = "state transition"
            else:
                continue
            if entity:
                definition = f"{entity}: {definition}"
            if trigger:
                definition = f"{definition} via {trigger}"
            transitions.append({
                **dict(raw_transition),
                "definition": definition,
                "entity": entity,
                "source_ref": source_ref,
                "evidence": evidence,
            })

    return {
        "entities": entities,
        "apis": apis,
        "candidate_invariants": invariants,
        "candidate_lifecycle_transitions": transitions,
        "observers": [],
    }


def _attach_persisted_graph_context(
    inputs: DiscoveryMainlineInputs,
    persisted_asset: dict[str, Any],
    reasoner_world: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach persisted Enterprise Knowledge through the existing Graph contract.

    Graph failure degrades only context selection. Raw PRD/API remain available
    to stage_reason_all_v2 as the existing shadow/fallback path, so Graph cannot
    turn an otherwise healthy Reasoner run into a hard planning failure.
    """
    requested_mode = (
        _text(os.environ.get("QUALIBUG_GRAPH_CONTEXT_MODE"))
        or _text(os.environ.get("GRAPH_CONTEXT_MODE"))
        or "shadow"
    ).lower()
    graph_context_pack: dict[str, Any]
    graph_sync: dict[str, Any] = {}
    try:
        from .cognitive_memory_graph import CognitiveMemoryGraph, GraphContextComposer
        from .enterprise_knowledge_center import project_knowledge_world_model

        persisted_world = project_knowledge_world_model(persisted_asset)
        project_context = _graph_project_context_from_knowledge_asset(
            persisted_asset,
            persisted_world,
        )
        if not any(
            _list(project_context.get(key))
            for key in (
                "entities",
                "apis",
                "candidate_invariants",
                "candidate_lifecycle_transitions",
                "observers",
            )
        ):
            raise ValueError("persisted_knowledge_graph_context_empty")

        environment_id = _text(
            inputs.campaign_context.get("environment_id")
            or inputs.campaign_context.get("environment_type")
            or inputs.campaign_context.get("environment_kind")
        ) or "test"
        graph = CognitiveMemoryGraph(
            project_id=inputs.project,
            environment_id=environment_id,
            root=inputs.root,
        )
        asset_ref = (
            _text(persisted_asset.get("asset_id"))
            or "enterprise_knowledge_asset"
        )
        graph_sync = graph.sync_context(
            project_context,
            prd_source_ref=asset_ref,
            api_source_ref=asset_ref,
            run_id=_text(inputs.campaign_context.get("run_id")),
            policy_version=_text(inputs.campaign_context.get("policy_version")),
        )
        graph_context_pack = GraphContextComposer(graph).compose({})
        graph_context_pack["graph_mode"] = requested_mode
        graph_context_pack["knowledge_asset_id"] = _text(
            persisted_asset.get("asset_id")
        )
        graph_context_pack["context_source"] = "persisted_enterprise_knowledge"
    except Exception as exc:
        _planning_logger.warning(
            "reasoner_graph_context_degraded %s: %s",
            type(exc).__name__,
            str(exc)[:240],
        )
        graph_context_pack = {
            "graph_ready": False,
            "graph_mode": "off",
            "context_source": "raw_source_fallback",
            "degradation_reason": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
        graph_sync = {}

    reasoner_world["_graph_evidence_pack"] = graph_context_pack
    reasoner_world["_graph_memory_stats"] = graph_sync
    return graph_context_pack, graph_sync
'''


TEST_CONTENT = r'''from pathlib import Path

from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs
from ai_test_asset_center.discovery_runtime_planning import (
    _attach_persisted_graph_context,
    _graph_project_context_from_knowledge_asset,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    project_knowledge_world_model,
)


def _asset():
    return {
        "asset_id": "knowledge_asset:bridge-test:v1",
        "project_id": "bridge-test",
        "business_objects": [
            {
                "object": "Order",
                "confidence": 0.95,
                "source": "prd",
                "source_id": "prd-1",
            }
        ],
        "interfaces": [
            {
                "interface_id": "create-order",
                "method": "POST",
                "path": "/orders",
                "resource": "Order",
                "source_id": "api-1",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule-1",
                "statement": "Order amount must be positive",
                "severity": "P1",
                "source_id": "prd-1",
            }
        ],
        "state_machines": [
            {
                "state_machine_id": "sm-order",
                "object": "Order",
                "states": ["draft", "paid"],
                "transitions": [
                    {"from": "draft", "to": "paid", "trigger": "pay"}
                ],
                "source_id": "prd-1",
            }
        ],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "semantic_candidates": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
    }


def _inputs(root: Path):
    return DiscoveryMainlineInputs(
        project="bridge-test",
        root=root,
        prd_text="RAW PRD FALLBACK",
        api_spec_text="RAW API FALLBACK",
        db_schema_text="",
        approved_base_url="",
        campaign_context={
            "environment_id": "test",
            "run_id": "run-bridge",
            "policy_version": "policy-v1",
        },
    )


def test_persisted_asset_maps_to_existing_graph_sync_contract():
    asset = _asset()
    world = project_knowledge_world_model(asset)
    context = _graph_project_context_from_knowledge_asset(asset, world)

    assert context["entities"][0]["name"] == "Order"
    assert context["apis"][0]["method"] == "POST"
    assert context["apis"][0]["path"] == "/orders"
    assert context["candidate_invariants"][0]["definition"] == (
        "Order amount must be positive"
    )
    assert context["candidate_lifecycle_transitions"][0]["definition"] == (
        "Order: draft -> paid via pay"
    )


def test_active_mode_attaches_persisted_graph_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "active")
    asset = _asset()
    world = project_knowledge_world_model(asset)

    pack, stats = _attach_persisted_graph_context(_inputs(tmp_path), asset, world)

    assert pack["graph_ready"] is True
    assert pack["graph_mode"] == "active"
    assert pack["context_source"] == "persisted_enterprise_knowledge"
    assert pack["knowledge_asset_id"] == asset["asset_id"]
    assert "QUALIBUG_GRAPH_CONTEXT_V1" in pack["rendered_context"]
    assert pack["context_refs"]
    assert stats["node_count"] > 0
    assert world["_graph_evidence_pack"] is pack
    assert world["_graph_memory_stats"] == stats
    assert (
        tmp_path
        / "platform_workspace"
        / "bridge-test"
        / "cognitive_memory_graph.sqlite3"
    ).exists()


def test_graph_context_uses_persisted_asset_not_runtime_world(tmp_path, monkeypatch):
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "active")
    asset = _asset()
    runtime_world = project_knowledge_world_model(asset)
    runtime_world["entities"].append(
        {"name": "RuntimeOnlyRawOverlayEntity", "is_core": True}
    )

    pack, _stats = _attach_persisted_graph_context(
        _inputs(tmp_path), asset, runtime_world
    )

    assert pack["graph_ready"] is True
    assert "Order" in pack["rendered_context"]
    assert "RuntimeOnlyRawOverlayEntity" not in pack["rendered_context"]


def test_shadow_mode_carries_graph_without_removing_raw_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "shadow")
    inputs = _inputs(tmp_path)
    asset = _asset()
    world = project_knowledge_world_model(asset)

    pack, _stats = _attach_persisted_graph_context(inputs, asset, world)

    assert pack["graph_ready"] is True
    assert pack["graph_mode"] == "shadow"
    assert inputs.prd_text == "RAW PRD FALLBACK"
    assert inputs.api_spec_text == "RAW API FALLBACK"
    assert world["_graph_evidence_pack"] is pack


def test_graph_failure_preserves_reasoner_fallback(tmp_path, monkeypatch):
    import ai_test_asset_center.cognitive_memory_graph as graph_module

    class BrokenGraph:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("graph unavailable")

    monkeypatch.setattr(graph_module, "CognitiveMemoryGraph", BrokenGraph)
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "active")
    inputs = _inputs(tmp_path)
    world = project_knowledge_world_model(_asset())

    pack, stats = _attach_persisted_graph_context(inputs, _asset(), world)

    assert pack["graph_ready"] is False
    assert pack["graph_mode"] == "off"
    assert pack["context_source"] == "raw_source_fallback"
    assert "graph unavailable" in pack["degradation_reason"]
    assert stats == {}
    assert inputs.prd_text == "RAW PRD FALLBACK"
    assert inputs.api_spec_text == "RAW API FALLBACK"
    assert world["_graph_evidence_pack"] is pack


def test_mainline_reasoner_bridge_keeps_raw_only_as_fallback():
    source = Path("ai_test_asset_center/discovery_runtime_planning.py").read_text(
        encoding="utf-8"
    )
    assert "persisted_knowledge_asset = asset" in source
    assert "_attach_persisted_graph_context(\n                inputs,\n                persisted_knowledge_asset," in source
    assert "collect_reasoner_hypotheses(\n                inputs.prd_text,\n                _reasoner_api_text," in source
'''


def apply() -> None:
    text = PLANNING.read_text(encoding="utf-8")

    if "def _graph_project_context_from_knowledge_asset(" not in text:
        anchor = "\ndef _source_snapshot_hash("
        pos = text.find(anchor)
        if pos < 0:
            raise RuntimeError("helper insertion anchor missing")
        text = text[:pos] + HELPER + text[pos:]

    persisted_anchor = "    runtime_source_overlay = build_runtime_source_knowledge_overlay(\n"
    persisted_replacement = (
        "    # Preserve the load/build result as the Reasoner's persistent business\n"
        "    # context authority. Runtime source overlay may enrich execution/IR, but\n"
        "    # it must not replace persisted Enterprise Understanding in Graph Context.\n"
        "    persisted_knowledge_asset = asset\n"
        "    runtime_source_overlay = build_runtime_source_knowledge_overlay(\n"
    )
    if "persisted_knowledge_asset = asset" not in text:
        if persisted_anchor not in text:
            raise RuntimeError("persisted asset anchor missing")
        text = text.replace(persisted_anchor, persisted_replacement, 1)

    old_reasoner = '''            _reasoner_world = project_knowledge_world_model(asset)\n            _reasoner_hypotheses, _reasoner_meta = collect_reasoner_hypotheses(\n                inputs.prd_text,\n                _reasoner_api_text,\n                reader_output=_reasoner_world,\n                project_id=inputs.project,\n                root=inputs.root,\n            )'''
    new_reasoner = '''            _reasoner_world = project_knowledge_world_model(asset)\n            # Root-cause fix: synchronize the PERSISTED Enterprise Knowledge\n            # asset into the existing CognitiveMemoryGraph contract before\n            # stage_reason_all_v2 decides active/shadow/fallback authority.\n            # Raw PRD/API stay present only for shadow/fallback semantics.\n            _graph_context_pack, _graph_sync = _attach_persisted_graph_context(\n                inputs,\n                persisted_knowledge_asset,\n                _reasoner_world,\n            )\n            _reasoner_hypotheses, _reasoner_meta = collect_reasoner_hypotheses(\n                inputs.prd_text,\n                _reasoner_api_text,\n                reader_output=_reasoner_world,\n                project_id=inputs.project,\n                root=inputs.root,\n            )'''
    if old_reasoner in text:
        text = text.replace(old_reasoner, new_reasoner, 1)
    elif new_reasoner not in text:
        raise RuntimeError("reasoner bridge anchor missing")

    PLANNING.write_text(text, encoding="utf-8")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")


if __name__ == "__main__":
    apply()
