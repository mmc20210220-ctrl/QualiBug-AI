"""Bridge authoritative Behavior IR into the existing CognitiveMemoryGraph context.

This module is deliberately deterministic: it does not call an LLM and it does not
re-parse raw PRD/API bodies. It projects the already-built Behavior IR into the
schema consumed by ``CognitiveMemoryGraph.sync_context`` and composes the existing
bounded Graph Context pack for the Reasoner.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, Iterator

from .cognitive_memory_graph import CognitiveMemoryGraph, GraphContextComposer


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (value or []) if isinstance(row, dict)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_source_ref(row: dict[str, Any]) -> str:
    for raw in row.get("source_refs") or []:
        if not isinstance(raw, dict):
            continue
        source_id = _text(raw.get("source_id"))
        locator = _text(raw.get("locator"))
        if source_id and locator:
            return f"{source_id}:{locator}"
        if source_id:
            return source_id
        if locator:
            return locator
    return ""


def _project_entity(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    state_fields: list[str] = []
    amount_fields: list[str] = []
    quantity_fields: list[str] = []
    for field in row.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = _text(field.get("name") or field.get("field"))
        semantic_type = _text(field.get("semantic_type")).upper()
        if not name:
            continue
        if semantic_type == "STATE":
            state_fields.append(name)
        elif semantic_type in {"AMOUNT_BALANCE", "AMOUNT_DELTA"}:
            amount_fields.append(name)
        elif semantic_type in {"QUANTITY_BALANCE", "QUANTITY_DELTA"}:
            quantity_fields.append(name)
    if state_fields:
        projected["state_fields"] = list(dict.fromkeys(state_fields))
    if amount_fields:
        projected["amount_fields"] = list(dict.fromkeys(amount_fields))
    if quantity_fields:
        projected["quantity_fields"] = list(dict.fromkeys(quantity_fields))
    return projected


def _project_operation(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    entity_refs = [_text(value) for value in row.get("entity_refs") or [] if _text(value)]
    # Only bind an API to an entity when the IR itself resolves exactly one target.
    if len(entity_refs) == 1:
        projected["entity"] = entity_refs[0]
    return projected


def _project_invariant(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    definition = _text(
        row.get("definition")
        or row.get("description")
        or row.get("invariant")
        or row.get("title")
    )
    if definition:
        projected["definition"] = definition
    source_ref = _first_source_ref(row)
    if source_ref:
        projected["source_ref"] = source_ref
        projected["evidence"] = True
    entity_ref = _text(row.get("entity_ref") or row.get("entity"))
    if entity_ref:
        projected["entity"] = entity_ref
    return projected


def _project_transitions(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    operations = {
        _text(row.get("id")): row
        for row in _rows(behavior_ir.get("operations"))
        if _text(row.get("id"))
    }
    states = {
        _text(row.get("id")): row
        for row in _rows(behavior_ir.get("states"))
        if _text(row.get("id"))
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for relation in _rows(behavior_ir.get("relations")):
        if _text(relation.get("relation_type")).lower() != "transitions":
            continue
        operation_ref = _text(relation.get("operation_ref") or relation.get("from_ref"))
        state_ref = _text(relation.get("to_ref"))
        operation = operations.get(operation_ref, {})
        state = states.get(state_ref, {})
        method = _text(operation.get("method")).upper()
        path = _text(operation.get("path") or operation.get("raw_path"))
        state_name = _text(
            state.get("name") or state.get("state") or state.get("value") or state_ref
        )
        label_parts = [
            f"{method} {path}".strip() if method or path else operation_ref,
            f"to {state_name}".strip() if state_name else state_ref,
        ]
        definition = " ".join(part for part in label_parts if part).strip()
        if not definition:
            continue
        key = "|".join((operation_ref, state_ref, definition))
        if key in seen:
            continue
        seen.add(key)
        source_ref = _first_source_ref(relation)
        result.append(
            {
                "definition": definition,
                "operation_ref": operation_ref,
                "state_ref": state_ref,
                "source_ref": source_ref,
                "evidence": bool(source_ref),
                "relation_id": _text(relation.get("id")),
            }
        )
    return result


def _project_observer(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    projected.setdefault("observer_id", _text(row.get("id") or row.get("name")))
    entity_refs = [_text(value) for value in row.get("entity_refs") or [] if _text(value)]
    if len(entity_refs) == 1:
        projected["entity"] = entity_refs[0]
    # Behavior IR surfaces are already accepted runtime facts; preserve their
    # declared confidence instead of inventing a stronger read-only claim.
    confidence = row.get("confidence")
    try:
        numeric_confidence = float(confidence)
    except (TypeError, ValueError):
        numeric_confidence = 0.0
    projected.setdefault("read_only_confidence", numeric_confidence)
    return projected


def project_behavior_ir_for_graph(
    behavior_ir: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project the existing Behavior IR into ``sync_context``'s input contract."""

    model = behavior_ir if isinstance(behavior_ir, dict) else {}
    return {
        "entities": [_project_entity(row) for row in _rows(model.get("entities"))],
        "apis": [_project_operation(row) for row in _rows(model.get("operations"))],
        "candidate_invariants": [
            _project_invariant(row) for row in _rows(model.get("invariants"))
        ],
        "candidate_lifecycle_transitions": _project_transitions(model),
        "observers": [
            _project_observer(row)
            for row in _rows(model.get("observation_surfaces"))
        ],
    }


def build_reasoner_graph_context(
    *,
    behavior_ir: dict[str, Any] | None,
    project_id: str,
    environment_id: str,
    root: str | Path,
    run_id: str = "",
    policy_version: str = "",
    source_ref: str = "",
) -> dict[str, Any]:
    """Sync the existing graph and compose the bounded Reasoner evidence pack.

    Failure is fail-soft by design. Returning an empty ``pack`` leaves
    ``stage_reason_all_v2`` on its existing raw PRD/API fallback path.
    """

    project_context = project_behavior_ir_for_graph(behavior_ir)
    input_counts = {
        key: len(value)
        for key, value in project_context.items()
        if isinstance(value, list)
    }
    # sync_context always creates PRD/API SourceDocument nodes. Without this
    # guard an empty persisted business model could therefore look graph_ready
    # merely because those bookkeeping nodes exist, incorrectly taking active
    # mode away from the raw PRD/API fallback.
    if not any(input_counts.values()):
        return {
            "status": "EMPTY",
            "pack": {},
            "stats": {},
            "input_counts": input_counts,
            "reason": "persisted_business_graph_input_empty",
        }
    try:
        graph = CognitiveMemoryGraph(
            project_id=project_id,
            environment_id=environment_id or "test",
            root=root,
        )
        stats = graph.sync_context(
            project_context,
            prd_source_ref=source_ref or "enterprise_knowledge_asset",
            api_source_ref=source_ref or "enterprise_knowledge_asset",
            run_id=run_id,
            policy_version=policy_version,
        )
        pack = GraphContextComposer(graph).compose()
        return {
            "status": "READY" if bool(pack.get("graph_ready")) else "EMPTY",
            "pack": pack,
            "stats": stats,
            "input_counts": input_counts,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "pack": {},
            "stats": {},
            "input_counts": input_counts,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


_REASONER_GRAPH_SCOPE: ContextVar[dict[str, Any]] = ContextVar(
    "qualibug_reasoner_graph_scope",
    default={},
)
_STAGE_INSTALL_MARKER = "_qualibug_reasoner_graph_context_bridge_installed"
_STAGE_ORIGINAL_MARKER = "_qualibug_reasoner_graph_context_original_collect"


@contextmanager
def reasoner_graph_context_scope(
    *,
    project_id: str,
    environment_id: str,
    root: str | Path,
    run_id: str = "",
    policy_version: str = "",
) -> Iterator[None]:
    """Bind per-run graph identity without process-global environment mutation."""

    token = _REASONER_GRAPH_SCOPE.set(
        {
            "project_id": _text(project_id),
            "environment_id": _text(environment_id) or "test",
            "root": Path(root),
            "run_id": _text(run_id),
            "policy_version": _text(policy_version),
        }
    )
    try:
        yield
    finally:
        _REASONER_GRAPH_SCOPE.reset(token)


def _persisted_asset_graph_bridge(
    *,
    reader_output: dict[str, Any],
    project_id: str,
    root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach Graph Context from the persisted enterprise knowledge asset only."""

    from .behavior_ir import build_behavior_ir_from_knowledge_asset
    from .enterprise_knowledge_center import load_enterprise_business_knowledge_asset

    scope = dict(_REASONER_GRAPH_SCOPE.get() or {})
    effective_project = _text(project_id) or _text(scope.get("project_id"))
    effective_root = Path(root) if root is not None else scope.get("root")
    environment_id = _text(scope.get("environment_id")) or "test"
    if not effective_project or effective_root is None:
        return reader_output, {
            "status": "SKIPPED",
            "reason": "project_or_root_missing",
        }

    asset = load_enterprise_business_knowledge_asset(effective_project, Path(effective_root))
    if not isinstance(asset, dict) or not asset:
        return reader_output, {
            "status": "SKIPPED",
            "reason": "persisted_enterprise_knowledge_asset_missing",
        }

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id=effective_project,
    )
    bridge = build_reasoner_graph_context(
        behavior_ir=behavior_ir,
        project_id=effective_project,
        environment_id=environment_id,
        root=Path(effective_root),
        run_id=_text(scope.get("run_id")),
        policy_version=_text(scope.get("policy_version")),
        source_ref=_text(asset.get("asset_id")) or "enterprise_knowledge_asset",
    )
    enriched = dict(reader_output)
    pack = bridge.get("pack")
    if isinstance(pack, dict) and pack:
        enriched["_graph_evidence_pack"] = pack
    enriched["_graph_memory_stats"] = dict(bridge.get("stats") or {})
    enriched["_graph_context_bridge"] = {
        "status": bridge.get("status"),
        "source": "persisted_enterprise_knowledge_asset",
        "input_counts": dict(bridge.get("input_counts") or {}),
        "error": _text(bridge.get("error")),
        "reason": _text(bridge.get("reason")),
    }
    return enriched, dict(enriched["_graph_context_bridge"])


def install_reasoner_graph_context_bridge() -> None:
    """Install one input-hydration wrapper before Stage owns mode semantics.

    The wrapper never changes ``_stage_reason_all_v2``. It only satisfies the
    pre-existing ``reader_output['_graph_evidence_pack']`` contract when the
    caller did not already provide one.
    """

    from . import stage_reason_all_v2 as stage

    if getattr(stage, _STAGE_INSTALL_MARKER, False):
        return
    original = getattr(stage, _STAGE_ORIGINAL_MARKER, None)
    if original is None:
        original = stage.collect_reasoner_hypotheses
        setattr(stage, _STAGE_ORIGINAL_MARKER, original)

    @wraps(original)
    def _collect_with_persisted_graph_context(
        prd_text: str,
        api_spec: str,
        *,
        reader_output: dict | None = None,
        prior_findings: list | None = None,
        project_id: str = "",
        root: Path | None = None,
    ) -> tuple[list[dict], dict]:
        world = dict(reader_output) if isinstance(reader_output, dict) else {}
        if not isinstance(world.get("_graph_evidence_pack"), dict):
            try:
                world, _ = _persisted_asset_graph_bridge(
                    reader_output=world,
                    project_id=project_id,
                    root=root,
                )
            except Exception as exc:
                # Fail-soft is part of the existing Stage contract: Graph absence
                # keeps raw PRD/API as fallback authority rather than aborting Run.
                world["_graph_context_bridge"] = {
                    "status": "FAILED",
                    "source": "persisted_enterprise_knowledge_asset",
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
        return original(
            prd_text,
            api_spec,
            reader_output=world,
            prior_findings=prior_findings,
            project_id=project_id,
            root=root,
        )

    stage.collect_reasoner_hypotheses = _collect_with_persisted_graph_context
    setattr(stage, _STAGE_INSTALL_MARKER, True)
