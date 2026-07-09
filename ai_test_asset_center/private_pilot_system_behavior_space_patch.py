from __future__ import annotations

"""Runtime wiring for the System Behavior Space Model.

This patch attaches the broader system-behavior-space model to the existing
BusinessStateGraphBuilder contract.  It does not create a new ingestion system:
when V12 runs inside a project, it loads the existing enterprise knowledge asset
and passes that parsed asset into the behavior-space builder.

The model is also materialized into existing ``behavior_contract['slices']`` as
source-grounded invariant slices.  That keeps execution inside the current V12
scheduler and SemanticScenarioGenerator instead of introducing a second executor.
The generator is patched only to preserve the system-promise metadata in runtime
hints and oracle rules; execution still uses the existing scenario contract.

Oracle integration is also additive: the existing OracleEngine remains the only
engine.  This patch wraps it so oracle failures can be attributed back to the
specific System Behavior Space promise and so direct promise contradictions can
emit a SystemPromiseOracle result.

Confirmed finding integration is additive too: V12's existing confirmed-finding
and regression-ledger writers are wrapped so a system-promise defect keeps its
promise id, dimensions, surfaces and required assets through later regression.

Learning feedback remains handled by existing modules:

* risk_clue_pool.py persists project/private and platform/SaaS learning weights;
* private_pilot_coverage_steering_patch.py feeds those weights into the existing
  V12 behavior-slice scheduler.
"""

import contextvars
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center import business_state_graph as _bsg
from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
    build_system_behavior_space,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_system_behavior_space_patch"
_BEHAVIOR_SPACE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_system_behavior_space_context",
    default={},
)


def _load_existing_enterprise_asset() -> dict[str, Any]:
    context = _BEHAVIOR_SPACE_CONTEXT.get({})
    project = str(context.get("project") or "").strip()
    root_value = context.get("root")
    if not project or root_value is None:
        return {}
    try:
        from ai_test_asset_center.enterprise_knowledge_center import (
            build_enterprise_business_knowledge_asset,
            load_enterprise_business_knowledge_asset,
        )
        root = Path(root_value)
        asset = load_enterprise_business_knowledge_asset(project, root)
        if asset is None:
            asset = build_enterprise_business_knowledge_asset(project, root)
        return asset if isinstance(asset, dict) else {}
    except Exception:
        return {}


def _path_only(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
        return parts[1] if parts[1].startswith("/") else ""
    return text if text.startswith("/") else ""


def _object_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("objects") if isinstance(space.get("objects"), list) else []
    return {str(item.get("entity") or ""): item for item in rows if isinstance(item, dict)}


def _promise_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("promises") if isinstance(space.get("promises"), list) else []
    return {str(item.get("promise_id") or ""): item for item in rows if isinstance(item, dict)}


def _system_behavior_slices(space: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert system promises into existing behavior-slice contract rows.

    We intentionally use kind='invariant' because SemanticScenarioGenerator
    already knows how to turn invariant slices into read-only/runtime-upgraded
    source-bound scenarios.  Extra metadata is additive and ignored by older
    consumers.
    """
    if not isinstance(space, dict):
        return []
    objects = _object_index(space)
    promises = _promise_index(space)
    probes = space.get("probe_candidates") if isinstance(space.get("probe_candidates"), list) else []
    slices: list[dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        promise_id = str(probe.get("promise_id") or "")
        promise = promises.get(promise_id)
        if not promise:
            continue
        entity = str(probe.get("entity") or promise.get("entity") or "system")
        obj = objects.get(entity, {})
        endpoints = []
        for raw in obj.get("api_paths") if isinstance(obj.get("api_paths"), list) else []:
            path = _path_only(str(raw))
            if path and path not in endpoints:
                endpoints.append(path)
        invariant = str(promise.get("invariant") or probe.get("objective") or "system promise")
        dimensions = [str(item) for item in (promise.get("dimensions") or probe.get("oracle_intent") or []) if str(item)]
        surface_plan = [str(item) for item in (probe.get("surface_plan") or promise.get("surfaces") or []) if str(item)]
        evidence_gaps = []
        if "api" in surface_plan and not endpoints:
            evidence_gaps.append("SYSTEM_PROMISE_API_ROUTE_NOT_SOURCE_BOUND")
        if "db" in surface_plan and not (obj.get("db_tables") if isinstance(obj.get("db_tables"), list) else []):
            evidence_gaps.append("SYSTEM_PROMISE_DB_TABLE_NOT_SOURCE_BOUND")
        if "ui" in surface_plan and not (obj.get("ui_routes") if isinstance(obj.get("ui_routes"), list) else []):
            evidence_gaps.append("SYSTEM_PROMISE_UI_ROUTE_NOT_SOURCE_BOUND")
        sid = _bsg.behavior_slice_id("system_promise", entity, promise_id)
        slices.append({
            "slice_id": sid,
            "entity": entity,
            "kind": "invariant",
            "states": [f"system_promise:{dimension}" for dimension in dimensions[:6]],
            "endpoints": endpoints,
            "priority": max(float(probe.get("priority") or 0.0), float(promise.get("confidence") or 0.0)),
            "source_refs": [{
                "source_type": "system_behavior_space",
                "locator": promise_id,
                "quote": invariant[:500],
            }],
            "evidence_gaps": evidence_gaps,
            "status": "pending",
            "_selection_family": dimensions[0] if dimensions else "system_promise",
            "_selection_origin": "system_behavior_space",
            "_system_behavior_promise_id": promise_id,
            "_system_behavior_probe_id": str(probe.get("probe_id") or ""),
            "_system_behavior_dimensions": dimensions,
            "_system_behavior_surface_plan": surface_plan,
            "_system_behavior_required_assets": [str(item) for item in (probe.get("required_assets") or []) if str(item)],
        })
    deduped: dict[str, dict[str, Any]] = {}
    for item in slices:
        deduped.setdefault(str(item.get("slice_id") or ""), item)
    return [item for _, item in sorted(deduped.items(), key=lambda kv: (-float(kv[1].get("priority") or 0.0), str(kv[1].get("entity") or ""), kv[0]))]


def _attach_system_behavior_slices(contract: dict[str, Any], space: dict[str, Any]) -> dict[str, Any]:
    existing = contract.get("slices") if isinstance(contract.get("slices"), list) else []
    generated = _system_behavior_slices(space)
    if not generated:
        return contract
    by_id: dict[str, dict[str, Any]] = {}
    for item in existing:
        if isinstance(item, dict) and str(item.get("slice_id") or ""):
            by_id[str(item.get("slice_id"))] = item
    added = 0
    for item in generated:
        sid = str(item.get("slice_id") or "")
        if sid and sid not in by_id:
            by_id[sid] = item
            added += 1
    merged = list(by_id.values())
    contract["slices"] = sorted(merged, key=lambda item: (-float(item.get("priority") or 0.0), str(item.get("entity") or ""), str(item.get("slice_id") or "")))
    summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
    by_kind: dict[str, int] = {}
    for item in contract["slices"]:
        kind = str(item.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    summary["total_slices"] = len(contract["slices"])
    summary["by_kind"] = dict(sorted(by_kind.items()))
    summary["system_behavior_materialized_slice_count"] = len(generated)
    summary["system_behavior_added_slice_count"] = added
    contract["summary"] = summary
    return contract


def _system_behavior_hints(slice_meta: dict[str, Any]) -> dict[str, Any]:
    if str(slice_meta.get("_selection_origin") or "") != "system_behavior_space":
        return {}
    promise_id = str(slice_meta.get("_system_behavior_promise_id") or "").strip()
    if not promise_id:
        return {}
    return {
        "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "promise_id": promise_id,
        "probe_id": str(slice_meta.get("_system_behavior_probe_id") or ""),
        "dimensions": [str(item) for item in (slice_meta.get("_system_behavior_dimensions") or []) if str(item)],
        "surface_plan": [str(item) for item in (slice_meta.get("_system_behavior_surface_plan") or []) if str(item)],
        "required_assets": [str(item) for item in (slice_meta.get("_system_behavior_required_assets") or []) if str(item)],
        "source_slice_id": str(slice_meta.get("slice_id") or ""),
        "source_family": str(slice_meta.get("_selection_family") or "system_promise"),
    }


def _scenario_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        try:
            payload = value.to_dict()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {
        "id": str(getattr(value, "id", "") or ""),
        "title": str(getattr(value, "title", "") or ""),
        "category": str(getattr(value, "category", "") or ""),
        "runtime_hints": dict(getattr(value, "runtime_hints", {}) or {}),
        "behavior_slice_id": str(getattr(value, "behavior_slice_id", "") or ""),
        "behavior_slice_kind": str(getattr(value, "behavior_slice_kind", "") or ""),
        "selection_origin": str(getattr(value, "selection_origin", "") or ""),
    }


def _scenario_system_behavior_hints(scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scenario, dict):
        return {}
    runtime_hints = scenario.get("runtime_hints") if isinstance(scenario.get("runtime_hints"), dict) else {}
    hints = runtime_hints.get("system_behavior_space") if isinstance(runtime_hints.get("system_behavior_space"), dict) else {}
    if hints:
        return hints
    fallback = scenario.get("system_behavior_space_evidence")
    return fallback if isinstance(fallback, dict) else {}


def _slice_invariant_text(slice_meta: dict[str, Any]) -> str:
    for ref in slice_meta.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("quote") or "").strip():
            return str(ref.get("quote") or "").strip()
    return str(slice_meta.get("entity") or "system_promise")


def _enrich_system_behavior_scenario(item: Any, slice_meta: dict[str, Any], discovery_round: int) -> Any:
    hints = _system_behavior_hints(slice_meta)
    if not hints:
        return item
    try:
        from ai_test_asset_center.semantic_scenario_generator import ExecutableScenario, ScenarioStep
    except Exception:
        return item
    invariant = _slice_invariant_text(slice_meta)
    entity = str(slice_meta.get("entity") or "system")
    endpoints = [str(value) for value in (slice_meta.get("endpoints") or []) if str(value).startswith("/")]
    evidence_gaps = [str(value) for value in (slice_meta.get("evidence_gaps") or []) if str(value)]
    if item is None:
        steps = []
        execution_policy = "plan_only_requires_fixture"
        if endpoints:
            steps = [ScenarioStep(order=1, action="observe_system_promise_surface", api_method="GET", api_path=endpoints[0], expected_status=200, actor="readonly")]
            execution_policy = "safe_read_only"
        item = ExecutableScenario(
            id=f"system_promise:{hints['promise_id']}",
            title=f"[System promise] {entity}: {hints['source_family']}",
            description=invariant[:300],
            category="system_promise",
            severity="P1",
            entity=entity,
            preconditions=["系统行为承诺来自 System Behavior Space，执行必须保留证据链。"],
            actors=["readonly"],
            steps=steps,
            oracle_rules=[],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.45),
            execution_policy=execution_policy,
            evidence_gaps=evidence_gaps,
            source_refs=[dict(ref) for ref in (slice_meta.get("source_refs") or []) if isinstance(ref, dict)],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="system_promise",
            discovery_round=discovery_round,
            selection_origin="system_behavior_space",
        )
    item.category = "system_promise"
    item.behavior_slice_kind = "system_promise"
    item.selection_origin = "system_behavior_space"
    if not str(item.title).startswith("[System promise]"):
        item.title = f"[System promise] {item.title}"
    rules = list(getattr(item, "oracle_rules", []) or [])
    for rule in ["SystemPromiseOracle.open_ended_promise_violation", *(f"SystemPromiseOracle.dimension:{dim}" for dim in hints["dimensions"][:8])]:
        if rule not in rules:
            rules.append(rule)
    item.oracle_rules = rules
    runtime_hints = dict(getattr(item, "runtime_hints", {}) or {})
    runtime_hints["system_behavior_space"] = hints
    runtime_hints["system_promise_invariant"] = invariant[:500]
    item.runtime_hints = runtime_hints
    item.evidence_gaps = list(dict.fromkeys([*(getattr(item, "evidence_gaps", []) or []), *evidence_gaps]))
    return item


def _response_bodies(trace: dict[str, Any]) -> list[Any]:
    bodies: list[Any] = []
    for step in trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []:
        if not isinstance(step, dict):
            continue
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        if isinstance(response, dict) and "body" in response:
            bodies.append(response.get("body"))
    return bodies


def _walk_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for key, child in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_walk_values(child, child_key))
        return out
    if isinstance(value, list):
        out = []
        for index, child in enumerate(value[:50]):
            out.extend(_walk_values(child, f"{prefix}[{index}]"))
        return out
    return [(prefix, value)]


def _direct_system_promise_oracle_result(scenario: dict[str, Any], trace: dict[str, Any], hints: dict[str, Any]) -> Any:
    try:
        from ai_test_asset_center.oracle_engine import OracleResult
    except Exception:
        return None
    dims = {str(item).lower() for item in hints.get("dimensions") or [] if str(item)}
    promise_id = str(hints.get("promise_id") or "")
    invariant = str((scenario.get("runtime_hints") or {}).get("system_promise_invariant") or "system promise")[:500]
    money_like = {"money", "amount", "price", "balance", "refund", "payment", "fee", "total", "quantity", "stock", "inventory", "conservation"}
    if dims.intersection({"money", "quantity", "conservation", "data_consistency"}):
        for body in _response_bodies(trace):
            for key, value in _walk_values(body):
                lowered = key.lower()
                if not any(token in lowered for token in money_like):
                    continue
                if isinstance(value, (int, float)) and value < 0:
                    return OracleResult(
                        False,
                        "SystemPromiseOracle",
                        "L7",
                        f"system_promise_dimension_violation:{promise_id}",
                        f"系统承诺必须保持金额/数量/守恒类维度有效：{invariant}",
                        f"{key}={value}",
                        "P0",
                        0.88,
                        f"System Behavior Space promise {promise_id} 被运行时响应直接反证。",
                    )
    steps = trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        status = int(response.get("status_code") or step.get("status") or 0) if isinstance(response, dict) else int(step.get("status") or 0)
        expected = int(step.get("expected_status") or 0)
        if expected in {401, 403} and status == 200:
            return OracleResult(
                False,
                "SystemPromiseOracle",
                "L7",
                f"system_promise_authorization_violation:{promise_id}",
                f"系统承诺必须保持权限/角色类维度有效：{invariant}",
                "期望拒绝但实际 HTTP 200",
                "P0",
                0.9,
                f"System Behavior Space promise {promise_id} 的授权维度被运行时结果反证。",
            )
    return OracleResult(
        True,
        "SystemPromiseOracle",
        "L7",
        explanation=f"System Behavior Space promise {promise_id} 已进入 oracle 评估；当前可观测响应未直接反证。",
    )


def _annotate_oracle_failures_with_system_promise(results: list[Any], scenario: dict[str, Any], hints: dict[str, Any]) -> None:
    promise_id = str(hints.get("promise_id") or "")
    if not promise_id:
        return
    invariant = str((scenario.get("runtime_hints") or {}).get("system_promise_invariant") or "")[:300]
    dims = ",".join(str(item) for item in hints.get("dimensions") or [] if str(item))
    for result in results:
        if bool(getattr(result, "passed", True)):
            continue
        if str(getattr(result, "oracle_name", "")) == "SystemPromiseOracle":
            continue
        explanation = str(getattr(result, "explanation", "") or "")
        marker = f"SystemPromise={promise_id}"
        if marker not in explanation:
            setattr(result, "explanation", (explanation + f" | {marker}; dimensions={dims}; invariant={invariant}").strip(" |")[:1200])


def _system_behavior_regression_contract(hints: dict[str, Any]) -> dict[str, Any]:
    if not hints or not str(hints.get("promise_id") or ""):
        return {}
    return {
        "contract_type": "system_behavior_promise_regression",
        "system_behavior_space_version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "system_behavior_space": hints,
        "promise_id": str(hints.get("promise_id") or ""),
        "probe_id": str(hints.get("probe_id") or ""),
        "dimensions": [str(item) for item in hints.get("dimensions") or [] if str(item)],
        "surface_plan": [str(item) for item in hints.get("surface_plan") or [] if str(item)],
        "required_assets": [str(item) for item in hints.get("required_assets") or [] if str(item)],
        "source_slice_id": str(hints.get("source_slice_id") or ""),
        "source_family": str(hints.get("source_family") or ""),
    }


def _attach_system_behavior_to_finding(finding: dict[str, Any], hints: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(finding, dict) or not hints:
        return finding
    promise_id = str(hints.get("promise_id") or "").strip()
    if not promise_id:
        return finding
    regression_contract = _system_behavior_regression_contract(hints)
    finding["system_promise_id"] = promise_id
    finding["system_behavior_space_evidence"] = hints
    finding["system_behavior_dimensions"] = regression_contract.get("dimensions", [])
    finding["system_behavior_surface_plan"] = regression_contract.get("surface_plan", [])
    finding["system_behavior_required_assets"] = regression_contract.get("required_assets", [])
    finding["system_behavior_source_family"] = regression_contract.get("source_family", "")
    finding["regression_contract"] = regression_contract
    finding["learning_signal"] = {
        "source": "system_behavior_space",
        "promise_id": promise_id,
        "dimensions": regression_contract.get("dimensions", []),
        "surfaces": regression_contract.get("surface_plan", []),
        "entity": str(finding.get("category") or scenario.get("entity") or "system"),
    }
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence["system_promise_id"] = promise_id
    evidence["system_behavior_space"] = hints
    finding["evidence"] = evidence
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    raw["system_behavior_space"] = hints
    raw["regression_contract"] = regression_contract
    finding["raw_evidence"] = raw
    status = finding.get("evidence_status") if isinstance(finding.get("evidence_status"), dict) else {}
    if finding.get("gate_passed") is True:
        status["system_promise_verdict"] = "SYSTEM_PROMISE_CONFIRMED"
    else:
        status["system_promise_verdict"] = "SYSTEM_PROMISE_CANDIDATE"
    finding["evidence_status"] = status
    return finding


def _install_system_behavior_scenario_patch() -> None:
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
    except Exception:
        return
    if getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", False):
        return
    original = getattr(_ssg.SemanticScenarioGenerator, "_invariant_from_meta", None)
    if not callable(original):
        return

    def _invariant_from_meta_with_system_behavior(self: Any, slice_meta: dict[str, Any], discovery_round: int, api_doc: str) -> Any:
        item = original(self, slice_meta, discovery_round, api_doc)
        return _enrich_system_behavior_scenario(item, slice_meta, discovery_round)

    _ssg.SemanticScenarioGenerator._ORIGINAL_INVARIANT_FROM_META_SYSTEM_BEHAVIOR = original  # type: ignore[attr-defined]
    _ssg.SemanticScenarioGenerator._invariant_from_meta = _invariant_from_meta_with_system_behavior  # type: ignore[method-assign]
    _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_oracle_patch() -> None:
    try:
        from ai_test_asset_center import oracle_engine as _oe
    except Exception:
        return
    if getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", False):
        return
    original_evaluate = getattr(_oe.OracleEngine, "evaluate", None)
    original_build = getattr(_oe.EvidenceGraphBuilder, "build", None)
    if not callable(original_evaluate) or not callable(original_build):
        return

    def _evaluate_with_system_behavior(self: Any, scenario: dict[str, Any], trace: dict[str, Any], snapshots: Any = None) -> list[Any]:
        results = list(original_evaluate(self, scenario, trace, snapshots) or [])
        hints = _scenario_system_behavior_hints(scenario)
        if not hints:
            return results
        _annotate_oracle_failures_with_system_promise(results, scenario, hints)
        direct = _direct_system_promise_oracle_result(scenario, trace, hints)
        if direct is not None:
            if not bool(getattr(direct, "passed", True)) or not any(str(getattr(item, "oracle_name", "")) == "SystemPromiseOracle" for item in results):
                results.append(direct)
        return results

    def _build_with_system_behavior_evidence(self: Any, scenario: dict[str, Any], trace: dict[str, Any], snapshots: Any, oracle_results: list[Any]) -> Any:
        hints = _scenario_system_behavior_hints(scenario)
        if hints:
            enriched = dict(scenario)
            enriched["system_behavior_space_evidence"] = hints
            enriched["system_promise_id"] = str(hints.get("promise_id") or "")
            scenario = enriched
        return original_build(self, scenario, trace, snapshots, oracle_results)

    _oe.OracleEngine._ORIGINAL_EVALUATE_SYSTEM_BEHAVIOR = original_evaluate  # type: ignore[attr-defined]
    _oe.EvidenceGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR = original_build  # type: ignore[attr-defined]
    _oe.OracleEngine.evaluate = _evaluate_with_system_behavior  # type: ignore[method-assign]
    _oe.EvidenceGraphBuilder.build = _build_with_system_behavior_evidence  # type: ignore[method-assign]
    _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_finding_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", False):
        return
    original_confirmed = getattr(_v12, "_confirmed_oracle_finding", None)
    original_persist = getattr(_v12, "_persist_confirmed_findings", None)
    if not callable(original_confirmed) or not callable(original_persist):
        return

    def _confirmed_oracle_finding_with_system_behavior(
        scenario: Any,
        trace: dict[str, Any],
        oracle_result: Any,
        evidence: Any,
        *,
        campaign_id: str,
        discovery_round: int,
        base_url: str,
    ) -> dict[str, Any]:
        finding = original_confirmed(
            scenario,
            trace,
            oracle_result,
            evidence,
            campaign_id=campaign_id,
            discovery_round=discovery_round,
            base_url=base_url,
        )
        scenario_payload = _scenario_payload(scenario)
        hints = _scenario_system_behavior_hints(scenario_payload)
        if not hints and hasattr(evidence, "to_dict"):
            try:
                evidence_payload = evidence.to_dict()
                if isinstance(evidence_payload, dict):
                    hints = _scenario_system_behavior_hints(evidence_payload.get("scenario") if isinstance(evidence_payload.get("scenario"), dict) else {})
            except Exception:
                hints = {}
        return _attach_system_behavior_to_finding(finding, hints, scenario_payload)

    def _persist_confirmed_findings_with_system_behavior(root: Path, project: str, findings: list[dict[str, Any]]) -> int:
        saved = int(original_persist(root, project, findings) or 0)
        system_findings = {
            str(item.get("evidence_id") or ""): item
            for item in findings or []
            if isinstance(item, dict)
            and str(item.get("evidence_id") or "")
            and isinstance(item.get("system_behavior_space_evidence"), dict)
        }
        if not system_findings:
            return saved
        try:
            path = _v12._confirmed_findings_path(root, project)
            payload = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
            ledger = payload if isinstance(payload, dict) else {}
            changed = False
            for evidence_id, finding in system_findings.items():
                if evidence_id not in ledger or not isinstance(ledger.get(evidence_id), dict):
                    continue
                hints = dict(finding.get("system_behavior_space_evidence") or {})
                regression_contract = dict(finding.get("regression_contract") or _system_behavior_regression_contract(hints))
                ledger[evidence_id]["system_promise_id"] = str(hints.get("promise_id") or "")
                ledger[evidence_id]["system_behavior_space_evidence"] = hints
                ledger[evidence_id]["system_behavior_dimensions"] = list(regression_contract.get("dimensions") or [])
                ledger[evidence_id]["system_behavior_surface_plan"] = list(regression_contract.get("surface_plan") or [])
                ledger[evidence_id]["system_behavior_required_assets"] = list(regression_contract.get("required_assets") or [])
                ledger[evidence_id]["regression_contract"] = regression_contract
                ledger[evidence_id]["learning_signal"] = dict(finding.get("learning_signal") or {})
                changed = True
            if changed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            return saved
        return saved

    _v12._ORIGINAL_CONFIRMED_ORACLE_FINDING_SYSTEM_BEHAVIOR = original_confirmed  # type: ignore[attr-defined]
    _v12._ORIGINAL_PERSIST_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR = original_persist  # type: ignore[attr-defined]
    _v12._confirmed_oracle_finding = _confirmed_oracle_finding_with_system_behavior  # type: ignore[assignment]
    _v12._persist_confirmed_findings = _persist_confirmed_findings_with_system_behavior  # type: ignore[assignment]
    _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = True  # type: ignore[attr-defined]


def install_system_behavior_space_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    if getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", False):
        return

    original_build = getattr(_bsg.BusinessStateGraphBuilder, "build")
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "behavior_contract")

    def _build_with_system_behavior_space(self: Any, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, Any]:
        graphs = original_build(self, prd_text, api_spec_text, db_schema_text)
        try:
            asset = getattr(self, "system_behavior_space_knowledge_asset", None)
            if not isinstance(asset, dict) or not asset:
                asset = _load_existing_enterprise_asset()
            space = build_system_behavior_space(prd_text, api_spec_text, db_schema_text, knowledge_asset=asset)
            self.system_behavior_space = space.to_dict()
        except Exception as exc:
            self.system_behavior_space = {
                "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
                "status": "unavailable",
                "reason": f"system_behavior_space_build_failed:{type(exc).__name__}",
                "summary": {
                    "object_count": 0,
                    "promise_count": 0,
                    "probe_candidate_count": 0,
                    "coverage_gap_count": 1,
                },
            }
        return graphs

    def _behavior_contract_with_system_behavior_space(self: Any) -> dict[str, Any]:
        contract = original_contract(self)
        space = getattr(self, "system_behavior_space", None)
        if isinstance(space, dict) and space:
            contract["system_behavior_space"] = space
            contract = _attach_system_behavior_slices(contract, space)
            summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
            space_summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
            summary["system_behavior_space_version"] = str(space.get("version") or SYSTEM_BEHAVIOR_SPACE_VERSION)
            summary["system_promise_count"] = int(space_summary.get("promise_count") or 0)
            summary["system_probe_candidate_count"] = int(space_summary.get("probe_candidate_count") or 0)
            summary["system_behavior_object_count"] = int(space_summary.get("object_count") or 0)
            summary["system_behavior_source_coverage"] = space_summary.get("source_coverage") if isinstance(space_summary.get("source_coverage"), dict) else {}
            summary["system_behavior_goal"] = "open_ended_system_promise_discovery_across_all_surfaces"
            contract["summary"] = summary
            gaps = contract.get("coverage_gaps") if isinstance(contract.get("coverage_gaps"), list) else []
            system_gaps = space.get("coverage_gaps") if isinstance(space.get("coverage_gaps"), list) else []
            for gap in system_gaps:
                if isinstance(gap, dict):
                    gaps.append({**gap, "source": "system_behavior_space"})
            contract["coverage_gaps"] = gaps
        return contract

    _bsg.BusinessStateGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE = original_build  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder._ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE = original_contract  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder.build = _build_with_system_behavior_space  # type: ignore[method-assign]
    _bsg.BusinessStateGraphBuilder.behavior_contract = _behavior_contract_with_system_behavior_space  # type: ignore[method-assign]
    _install_v12_behavior_space_context_patch()
    _install_system_behavior_scenario_patch()
    _install_system_behavior_oracle_patch()
    _install_system_behavior_finding_patch()
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = True  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def _install_v12_behavior_space_context_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", False):
        return
    original = getattr(_v12, "run_v12_pipeline", None)
    if not callable(original):
        return

    def _run_v12_pipeline_with_behavior_space_context(project: str, root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _BEHAVIOR_SPACE_CONTEXT.set({"project": str(project or ""), "root": Path(root)})
        try:
            return original(project, root, *args, **kwargs)
        finally:
            _BEHAVIOR_SPACE_CONTEXT.reset(token)

    _v12._ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_SPACE_CONTEXT = original  # type: ignore[attr-defined]
    _v12.run_v12_pipeline = _run_v12_pipeline_with_behavior_space_context  # type: ignore[assignment]
    _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = True  # type: ignore[attr-defined]


def prepare_system_behavior_space_learning_context(builder: Any, *, project: str, root: Any) -> Any:
    """Compatibility helper retained for older tests/callers.

    It now attaches the existing enterprise knowledge asset when possible.  The
    name is kept stable because earlier code/tests may still import it.
    """
    try:
        from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
        asset = load_enterprise_business_knowledge_asset(project, Path(root))
        if isinstance(asset, dict):
            setattr(builder, "system_behavior_space_knowledge_asset", asset)
    except Exception:
        pass
    return builder


def restore_system_behavior_space_patch() -> None:
    original_build = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE", None)
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE", None)
    if callable(original_build):
        _bsg.BusinessStateGraphBuilder.build = original_build  # type: ignore[method-assign]
    if callable(original_contract):
        _bsg.BusinessStateGraphBuilder.behavior_contract = original_contract  # type: ignore[method-assign]
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        original_v12 = getattr(_v12, "_ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_SPACE_CONTEXT", None)
        if callable(original_v12):
            _v12.run_v12_pipeline = original_v12  # type: ignore[assignment]
        _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
        original_scenario = getattr(_ssg.SemanticScenarioGenerator, "_ORIGINAL_INVARIANT_FROM_META_SYSTEM_BEHAVIOR", None)
        if callable(original_scenario):
            _ssg.SemanticScenarioGenerator._invariant_from_meta = original_scenario  # type: ignore[method-assign]
        _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import oracle_engine as _oe
        original_eval = getattr(_oe.OracleEngine, "_ORIGINAL_EVALUATE_SYSTEM_BEHAVIOR", None)
        original_build = getattr(_oe.EvidenceGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR", None)
        if callable(original_eval):
            _oe.OracleEngine.evaluate = original_eval  # type: ignore[method-assign]
        if callable(original_build):
            _oe.EvidenceGraphBuilder.build = original_build  # type: ignore[method-assign]
        _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        original_confirmed = getattr(_v12, "_ORIGINAL_CONFIRMED_ORACLE_FINDING_SYSTEM_BEHAVIOR", None)
        original_persist = getattr(_v12, "_ORIGINAL_PERSIST_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR", None)
        if callable(original_confirmed):
            _v12._confirmed_oracle_finding = original_confirmed  # type: ignore[assignment]
        if callable(original_persist):
            _v12._persist_confirmed_findings = original_persist  # type: ignore[assignment]
        _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = False  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
