from __future__ import annotations

"""Runtime wiring for the System Behavior Space Model.

This patch attaches the broader system-behavior-space model to the existing
BusinessStateGraphBuilder contract. It does not create a new ingestion system:
when V12 runs inside a project, it loads the existing enterprise knowledge asset
and passes that parsed asset into the behavior-space builder.

The model is materialized into existing ``behavior_contract['slices']`` as
source-grounded invariant slices. Execution stays inside the current V12
scheduler and SemanticScenarioGenerator.

Oracle, confirmed-finding, regression and learning integrations are additive:
existing engines/writers/runners remain the execution paths, while this patch
preserves System Behavior Space promise metadata across them.
"""

import json
from pathlib import Path
from typing import Any

from ai_test_asset_center import business_state_graph as _bsg
from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
    build_system_behavior_space,
)
from ai_test_asset_center.system_behavior_space_context import (
    get_behavior_space_context,
)

from ai_test_asset_center.system_behavior_space_scenario_enricher import (
    _enrich_system_behavior_scenario,
    _scenario_payload,
    _scenario_system_behavior_hints,
)

from ai_test_asset_center.system_behavior_space_oracle import (
    _annotate_oracle_failures_with_system_promise,
    _direct_system_promise_oracle_result,
)


PATCH_SOURCE = "ai_test_asset_center.private_pilot_system_behavior_space_patch"
_SAFE_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_KNOWN_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}


def _load_existing_enterprise_asset() -> dict[str, Any]:
    context = get_behavior_space_context()
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


def _api_route(raw: Any) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {"method": "", "path": ""}
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in _KNOWN_HTTP_METHODS:
        path = parts[1].strip()
        return {"method": parts[0].upper(), "path": path if path.startswith("/") else ""}
    return {"method": "", "path": text if text.startswith("/") else ""}


def _path_only(value: str) -> str:
    return _api_route(value).get("path", "")


def _object_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("objects") if isinstance(space.get("objects"), list) else []
    return {str(item.get("entity") or ""): item for item in rows if isinstance(item, dict)}


def _promise_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("promises") if isinstance(space.get("promises"), list) else []
    return {str(item.get("promise_id") or ""): item for item in rows if isinstance(item, dict)}


def _dedupe_routes(routes: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for route in routes:
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if not path:
            continue
        key = (method, path)
        if key not in seen:
            seen.add(key)
            out.append({"method": method, "path": path})
    return out


def _system_behavior_slices(space: dict[str, Any]) -> list[dict[str, Any]]:
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
        routes = _dedupe_routes([_api_route(raw) for raw in (obj.get("api_paths") if isinstance(obj.get("api_paths"), list) else [])])
        # ── Endpoints for observe steps must be safe-read (GET/HEAD/OPTIONS) ──
        # POST/PUT/DELETE paths generate write steps only; including them in
        # `endpoints` causes the scenario generator to create invalid GET-on-POST
        # observe steps → 404 noise.
        endpoints: list[str] = []
        for route in routes:
            method = str(route.get("method") or "").upper()
            path = str(route.get("path") or "")
            if method in _SAFE_READ_METHODS and path and path not in endpoints:
                endpoints.append(path)
        invariant = str(promise.get("invariant") or probe.get("objective") or "system promise")
        dimensions = [str(item) for item in (promise.get("dimensions") or probe.get("oracle_intent") or []) if str(item)]
        surface_plan = [str(item) for item in (probe.get("surface_plan") or promise.get("surfaces") or []) if str(item)]
        evidence_gaps: list[str] = []
        if "api" in surface_plan and not endpoints:
            evidence_gaps.append("SYSTEM_PROMISE_API_ROUTE_NOT_SOURCE_BOUND")
        if "api" in surface_plan and endpoints and not any(route.get("method") in _SAFE_READ_METHODS for route in routes):
            evidence_gaps.append("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
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
            "source_refs": [{"source_type": "system_behavior_space", "locator": promise_id, "quote": invariant[:500]}],
            "evidence_gaps": evidence_gaps,
            "status": "pending",
            "_selection_family": dimensions[0] if dimensions else "system_promise",
            "_selection_origin": "system_behavior_space",
            "_system_behavior_promise_id": promise_id,
            "_system_behavior_probe_id": str(probe.get("probe_id") or ""),
            "_system_behavior_dimensions": dimensions,
            "_system_behavior_surface_plan": surface_plan,
            "_system_behavior_api_routes": routes,
            "_system_behavior_required_assets": [str(item) for item in (probe.get("required_assets") or []) if str(item)],
        })
    deduped: dict[str, dict[str, Any]] = {}
    for item in slices:
        sid = str(item.get("slice_id") or "")
        if sid and sid not in deduped:
            # ── Route availability scoring ──
            # Slices without any source-bound API route cannot be executed
            # (no safe_read_only path), so they stay as documentation-only
            # plan entries.  Only slices WITH routes generate executable scenarios.
            routes = item.get("_system_behavior_api_routes") if isinstance(item.get("_system_behavior_api_routes"), list) else []
            has_exec_route = any(
                isinstance(r, dict) and str(r.get("method") or "").upper() in _KNOWN_HTTP_METHODS
                and str(r.get("path") or "").startswith("/")
                for r in routes
            )
            if not has_exec_route:
                item["_route_availability"] = "no_source_bound_route"
                item["status"] = "plan_only"
            # ── Server-verified route check ──
            # Route reachability is evaluated by the executor, not this model.
            # Source-bound routes are compiled without any benchmark whitelist.
            # Governed execution produces the authoritative reachability receipt.
            deduped[sid] = item
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
    contract["slices"] = sorted(by_id.values(), key=lambda item: (-float(item.get("priority") or 0.0), str(item.get("entity") or ""), str(item.get("slice_id") or "")))
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


def _contract_from_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    contract = row.get("regression_contract") if isinstance(row.get("regression_contract"), dict) else {}
    if isinstance(contract.get("system_behavior_space"), dict) and str(contract.get("promise_id") or contract["system_behavior_space"].get("promise_id") or ""):
        hints = dict(contract.get("system_behavior_space") or {})
        if not str(hints.get("promise_id") or ""):
            hints["promise_id"] = str(contract.get("promise_id") or "")
        return _system_behavior_regression_contract(hints)
    hints = row.get("system_behavior_space_evidence") if isinstance(row.get("system_behavior_space_evidence"), dict) else {}
    if hints:
        return _system_behavior_regression_contract(hints)
    return {}


def _attach_regression_contract_fields(target: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict) or not contract:
        return target
    target["regression_contract"] = contract
    target["system_promise_id"] = str(contract.get("promise_id") or "")
    target["system_behavior_space_evidence"] = dict(contract.get("system_behavior_space") or {})
    target["system_behavior_dimensions"] = list(contract.get("dimensions") or [])
    target["system_behavior_surface_plan"] = list(contract.get("surface_plan") or [])
    target["system_behavior_required_assets"] = list(contract.get("required_assets") or [])
    target["system_behavior_source_family"] = str(contract.get("source_family") or "")
    return target


def _attach_system_behavior_to_finding(finding: dict[str, Any], hints: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(finding, dict) or not hints:
        return finding
    promise_id = str(hints.get("promise_id") or "").strip()
    if not promise_id:
        return finding
    regression_contract = _system_behavior_regression_contract(hints)
    _attach_regression_contract_fields(finding, regression_contract)
    finding["learning_signal"] = {"source": "system_behavior_space", "promise_id": promise_id, "dimensions": regression_contract.get("dimensions", []), "surfaces": regression_contract.get("surface_plan", []), "entity": str(finding.get("category") or scenario.get("entity") or "system")}
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence["system_promise_id"] = promise_id
    evidence["system_behavior_space"] = hints
    finding["evidence"] = evidence
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    raw["system_behavior_space"] = hints
    raw["regression_contract"] = regression_contract
    finding["raw_evidence"] = raw
    status = finding.get("evidence_status") if isinstance(finding.get("evidence_status"), dict) else {}
    status["system_promise_verdict"] = "SYSTEM_PROMISE_CONFIRMED" if finding.get("gate_passed") is True else "SYSTEM_PROMISE_CANDIDATE"
    finding["evidence_status"] = status
    return finding


def _system_behavior_learning_refresh_summary(project: str, root: Path) -> dict[str, Any]:
    try:
        from ai_test_asset_center.risk_clue_pool import get_platform_learning, refresh_project_learning
        project_learning = refresh_project_learning(project, root)
        platform_learning = get_platform_learning(root)
        return {
            "status": "refreshed",
            "project_learning_version": str(project_learning.get("version") or ""),
            "project_signal_count": int(project_learning.get("signal_count") or 0),
            "project_system_promise_signal_count": int(project_learning.get("system_promise_signal_count") or 0),
            "platform_learning_version": str(platform_learning.get("version") or ""),
            "platform_signal_count": int(platform_learning.get("signal_count") or 0),
        }
    except Exception as exc:
        return {"status": "refresh_failed", "reason": type(exc).__name__}


def _install_system_behavior_scenario_patch() -> None:
    """Register first-class scenario enricher — do not replace SSG methods."""
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
        from ai_test_asset_center.semantic_scenario_generator import register_scenario_enricher
    except Exception:
        return
    if getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", False):
        return

    def _enrich(item: Any, slice_meta: dict[str, Any], discovery_round: int, api_doc: str = "") -> Any:
        return _enrich_system_behavior_scenario(item, slice_meta, discovery_round, api_doc=api_doc)

    register_scenario_enricher(_enrich)
    _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = True  # type: ignore[attr-defined]
    _ssg._SYSTEM_BEHAVIOR_SCENARIO_MODE = "first_class_hook"  # type: ignore[attr-defined]


def _install_system_behavior_oracle_patch() -> None:
    """Register first-class oracle hooks — do not replace OracleEngine methods."""
    try:
        from ai_test_asset_center import oracle_engine as _oe
        from ai_test_asset_center.oracle_engine import (
            register_evidence_scenario_hook,
            register_oracle_evaluate_hook,
        )
    except Exception:
        return
    if getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", False):
        return

    def _evaluate_hook(
        self: Any,
        scenario: dict[str, Any],
        trace: dict[str, Any],
        snapshots: Any,
        results: list[Any],
    ) -> list[Any]:
        del self, snapshots
        hints = _scenario_system_behavior_hints(scenario)
        if not hints:
            return results
        _annotate_oracle_failures_with_system_promise(results, scenario, hints)
        direct = _direct_system_promise_oracle_result(scenario, trace, hints)
        if direct is not None and (
            not bool(getattr(direct, "passed", True))
            or not any(str(getattr(item, "oracle_name", "")) == "SystemPromiseOracle" for item in results)
        ):
            results.append(direct)
        return results

    def _evidence_scenario_hook(
        scenario: dict[str, Any],
        trace: dict[str, Any],
        snapshots: Any,
        oracle_results: list[Any],
    ) -> dict[str, Any]:
        del trace, snapshots, oracle_results
        hints = _scenario_system_behavior_hints(scenario)
        if hints:
            return {
                **scenario,
                "system_behavior_space_evidence": hints,
                "system_promise_id": str(hints.get("promise_id") or ""),
            }
        return scenario

    register_oracle_evaluate_hook(_evaluate_hook)
    register_evidence_scenario_hook(_evidence_scenario_hook)
    _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = True  # type: ignore[attr-defined]
    _oe._SYSTEM_BEHAVIOR_ORACLE_MODE = "first_class_hook"  # type: ignore[attr-defined]


def _install_system_behavior_finding_patch() -> None:
    """Register first-class finding enricher — do not replace v12 symbols."""
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        from ai_test_asset_center.v12_legacy_oracle_findings import register_finding_enricher
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", False):
        return

    def _enrich_finding(
        finding: dict[str, Any],
        scenario: Any,
        trace: dict[str, Any],
        oracle_result: Any,
        evidence: Any,
        *,
        campaign_id: str,
        discovery_round: int,
        base_url: str,
    ) -> dict[str, Any]:
        del trace, oracle_result, campaign_id, discovery_round, base_url
        scenario_payload = _scenario_payload(scenario)
        hints = _scenario_system_behavior_hints(scenario_payload)
        if not hints and hasattr(evidence, "to_dict"):
            try:
                evidence_payload = evidence.to_dict()
                if isinstance(evidence_payload, dict):
                    hints = _scenario_system_behavior_hints(
                        evidence_payload.get("scenario")
                        if isinstance(evidence_payload.get("scenario"), dict)
                        else {}
                    )
            except Exception:
                hints = {}
        return _attach_system_behavior_to_finding(finding, hints, scenario_payload)

    register_finding_enricher(_enrich_finding)
    _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = True  # type: ignore[attr-defined]
    _v12._SYSTEM_BEHAVIOR_FINDING_MODE = "first_class_hook"  # type: ignore[attr-defined]


def _install_system_behavior_regression_patch() -> None:
    """Register first-class regression hooks — do not replace runner symbols.

    Confirmed-findings probe loading already forwards system-behavior metadata
    in the base suite builder; no load hook is required.
    """
    try:
        from ai_test_asset_center import regression_runner as _rr
        from ai_test_asset_center.regression_runner import (
            register_append_history_hook,
            register_judge_probe_hook,
            register_reverify_hook,
        )
    except Exception:
        return
    if getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_PATCHED", False):
        return

    def _judge_hook(
        probe: dict[str, Any],
        execution: dict[str, Any],
        item: dict[str, Any],
        *,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> dict[str, Any]:
        del execution, skipped, skip_reason
        contract = _contract_from_row(probe)
        if contract:
            item["oracle_intent"] = [
                f"SystemPromiseOracle.dimension:{dim}" for dim in contract.get("dimensions") or []
            ]
        return item

    def _reverify_hook(
        project: str,
        root: Path,
        cfg: dict[str, Any],
        safety_boundary: dict[str, Any],
        timeout: float,
        dry_run: bool,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        del project, root, cfg, safety_boundary, timeout, dry_run
        if isinstance(result, dict):
            result["system_promise_reverification_count"] = sum(
                1
                for item in result.get("verdicts", [])
                if isinstance(item, dict) and item.get("system_promise_id")
            )
        return result

    def _append_history_hook(
        project: str,
        root: Path,
        result: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not history:
            return history
        try:
            refresh = _system_behavior_learning_refresh_summary(project, root)
            result["risk_clue_pool_learning_refresh"] = refresh
            last = history[-1]
            last["risk_clue_pool_learning_refresh"] = refresh
            history[-1] = last
            _rr._write_json(
                root / "platform_outputs" / project / "regression_run" / "regression_run_history.json",
                history,
            )
            _rr._write_json(
                root / "platform_workspace" / project / "defect_discovery" / "regression_run_history.json",
                history,
            )
        except Exception:
            return history
        return history

    register_judge_probe_hook(_judge_hook)
    register_reverify_hook(_reverify_hook)
    register_append_history_hook(_append_history_hook)
    _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = True  # type: ignore[attr-defined]
    _rr._SYSTEM_BEHAVIOR_REGRESSION_MODE = "first_class_hook"  # type: ignore[attr-defined]


def install_system_behavior_space_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    """Register first-class BSG hooks and the rest of the SBS chain."""
    if getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", False):
        return

    from ai_test_asset_center.business_state_graph import (
        register_bsg_build_hook,
        register_bsg_contract_hook,
    )

    def _build_hook(self: Any, prd_text: str, api_spec_text: str, db_schema_text: str) -> None:
        try:
            asset = getattr(self, "system_behavior_space_knowledge_asset", None)
            if not isinstance(asset, dict) or not asset:
                asset = _load_existing_enterprise_asset()
            self.system_behavior_space = build_system_behavior_space(
                prd_text, api_spec_text, db_schema_text, knowledge_asset=asset
            ).to_dict()
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

    def _contract_hook(self: Any, contract: dict[str, Any]) -> dict[str, Any]:
        space = getattr(self, "system_behavior_space", None)
        if not (isinstance(space, dict) and space):
            return contract
        contract["system_behavior_space"] = space
        contract = _attach_system_behavior_slices(contract, space)
        summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
        space_summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
        summary["system_behavior_space_version"] = str(space.get("version") or SYSTEM_BEHAVIOR_SPACE_VERSION)
        summary["system_promise_count"] = int(space_summary.get("promise_count") or 0)
        summary["system_probe_candidate_count"] = int(space_summary.get("probe_candidate_count") or 0)
        summary["system_behavior_object_count"] = int(space_summary.get("object_count") or 0)
        summary["system_behavior_source_coverage"] = (
            space_summary.get("source_coverage")
            if isinstance(space_summary.get("source_coverage"), dict)
            else {}
        )
        summary["system_behavior_goal"] = "open_ended_system_promise_discovery_across_all_surfaces"
        contract["summary"] = summary
        gaps = contract.get("coverage_gaps") if isinstance(contract.get("coverage_gaps"), list) else []
        for gap in space.get("coverage_gaps") if isinstance(space.get("coverage_gaps"), list) else []:
            if isinstance(gap, dict):
                gaps.append({**gap, "source": "system_behavior_space"})
        contract["coverage_gaps"] = gaps
        return contract

    register_bsg_build_hook(_build_hook)
    register_bsg_contract_hook(_contract_hook)
    _install_v12_behavior_space_context_patch()
    _install_system_behavior_scenario_patch()
    _install_system_behavior_oracle_patch()
    _install_system_behavior_finding_patch()
    _install_system_behavior_regression_patch()
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = True  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_MODE = "first_class_hook"  # type: ignore[attr-defined]


def _install_v12_behavior_space_context_patch() -> None:
    """Mark first-class context binder readiness — do not wrap run_v12_pipeline."""
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        from ai_test_asset_center.system_behavior_space_context import (
            FIRST_CLASS_CONTEXT_BINDER,
        )
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", False):
        return
    if not FIRST_CLASS_CONTEXT_BINDER:
        return
    # Context is bound inside run_v12_pipeline; no symbol replacement.
    _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
    _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_MODE = "first_class"  # type: ignore[attr-defined]


def prepare_system_behavior_space_learning_context(builder: Any, *, project: str, root: Any) -> Any:
    try:
        from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
        asset = load_enterprise_business_knowledge_asset(project, Path(root))
        if isinstance(asset, dict):
            setattr(builder, "system_behavior_space_knowledge_asset", asset)
    except Exception:
        pass
    return builder


def restore_system_behavior_space_patch() -> None:
    """Clear first-class SBS hooks and readiness flags — no method restore."""
    try:
        from ai_test_asset_center.business_state_graph import clear_bsg_hooks

        clear_bsg_hooks()
    except Exception:
        pass
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        # First-class binder owns run_v12_pipeline; only clear the readiness flag.
        _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
        if hasattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_MODE"):
            delattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_MODE")
    except Exception:
        pass
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
        from ai_test_asset_center.semantic_scenario_generator import clear_scenario_enricher

        clear_scenario_enricher()
        _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = False  # type: ignore[attr-defined]
        if hasattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_MODE"):
            delattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_MODE")
    except Exception:
        pass
    try:
        from ai_test_asset_center import oracle_engine as _oe
        from ai_test_asset_center.oracle_engine import clear_oracle_hooks

        clear_oracle_hooks()
        _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = False  # type: ignore[attr-defined]
        if hasattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_MODE"):
            delattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_MODE")
    except Exception:
        pass
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        from ai_test_asset_center.v12_legacy_oracle_findings import clear_finding_enricher

        clear_finding_enricher()
        _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = False  # type: ignore[attr-defined]
        if hasattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_MODE"):
            delattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_MODE")
    except Exception:
        pass
    try:
        from ai_test_asset_center import regression_runner as _rr
        from ai_test_asset_center.regression_runner import clear_regression_hooks

        clear_regression_hooks()
        _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = False  # type: ignore[attr-defined]
        if hasattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_MODE"):
            delattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_MODE")
    except Exception:
        pass
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = False  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_MODE"):
        delattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_MODE")
