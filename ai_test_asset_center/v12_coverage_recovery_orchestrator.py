"""V1.2.1 Coverage Recovery Orchestrator — unified main-chain entry point.

SPEC v1.2.1 §5 + §6.4: Orchestrates all v1.2 coverage recovery modules
in the correct order and returns a single verdict that directly affects
COMPILED / BLOCKED / execution order.

Call sequence:
    1. Primary Operation Resolution (from compiler context)
    2. Observer Resolution (resolve_observer_capability)
    3. Compensation Relation Resolution (resolve_compensation_relation)
    4. Oracle Input Contract (build_oracle_input_contract)
    5. Binding Coverage Graph (build_binding_coverage_graph)
    6. Fixture DAG Validation (validate_fixture_dag)
    7. Return READY / BLOCKED / SOURCE_DEPENDENT / ENVIRONMENT_DEPENDENT

Output: qualibug.v12-coverage-recovery-orchestrator.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Orchestrator Verdicts ────────────────────────────────────────────────────

VERDICT_READY = "READY"
VERDICT_BLOCKED = "BLOCKED"
VERDICT_SOURCE_DEPENDENT = "SOURCE_DEPENDENT"
VERDICT_ENVIRONMENT_DEPENDENT = "ENVIRONMENT_DEPENDENT"


def prepare_experiment_v12(
    *,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
    compiler_context: dict[str, Any],
) -> dict[str, Any]:
    """Orchestrate all v1.2 coverage recovery modules for a single experiment.

    This is the SINGLE entry point called by the compiler. It runs all
    modules in order and produces a verdict that directly gates COMPILED/BLOCKED.

    Args:
        obligation: The test obligation being compiled.
        behavior_ir: The full Behavior IR graph.
        compiler_context: Dict containing the compiled experiment artifact
            (key "experiment") and any additional compiler state.

    Returns:
        qualibug.v12-coverage-recovery-orchestrator.v1 receipt with:
        - verdict: READY | BLOCKED | SOURCE_DEPENDENT | ENVIRONMENT_DEPENDENT
        - observer_resolution_plan
        - compensation_relation_plan
        - oracle_input_contract
        - binding_coverage_graph
        - fixture_dependency_dag
        - blocking_reason (if not READY)
    """
    from ai_test_asset_center.observer_capability_resolver import resolve_observer_capability
    from ai_test_asset_center.compensation_relation_resolver import resolve_compensation_relation
    from ai_test_asset_center.oracle_input_contract import build_oracle_input_contract
    from ai_test_asset_center.binding_coverage_graph import build_binding_coverage_graph
    from ai_test_asset_center.fixture_dependency_dag import validate_fixture_dag

    obl = _dict(obligation)
    ir = _dict(behavior_ir)
    ctx = _dict(compiler_context)
    exp = _dict(ctx.get("experiment"))

    oid = _text(obl.get("obligation_id"))
    primary_op = _dict(ctx.get("primary_operation"))
    primary_op_id = _text(primary_op.get("id"))
    is_write = _dict(exp.get("safety_contract")).get("governed_write", False)

    # Collect module results
    module_results: dict[str, Any] = {}
    blocking_reasons: list[dict[str, str]] = []

    # ── Step 1: Observer Resolution ──
    observer_resolution = resolve_observer_capability(
        observer_requirement=_text(obl.get("observer_requirement")) or "after_state",
        primary_operation=primary_op,
        behavior_ir=ir,
        required_bindings=[_text(b.get("target")) for b in _list(exp.get("binding_plan")) if isinstance(b, dict)],
    )
    module_results["observer_resolution_plan"] = observer_resolution
    # Observer resolution is informational — the compiler has its own observer logic.
    # Only hard BLOCKED (no eligible operation at all) is recorded but does not block.

    # ── Step 2: Compensation Relation Resolution (only for governed writes) ──
    compensation: dict[str, Any] = {"status": "NOT_REQUIRED"}
    if is_write:
        cleanup_plan = _list(exp.get("cleanup_plan"))
        # Find candidate cleanup operation from IR
        candidate_op: dict[str, Any] = {}
        for op in _list(ir.get("operations")):
            if not isinstance(op, dict):
                continue
            if _text(op.get("method")).upper() in ("DELETE", "PUT", "PATCH") and _text(op.get("id")) != primary_op_id:
                candidate_op = op
                break
        if cleanup_plan and candidate_op:
            compensation = resolve_compensation_relation(
                primary_operation=primary_op,
                candidate_operation=candidate_op,
                behavior_ir=ir,
            )
        elif cleanup_plan:
            compensation = {"status": "RESOLVED", "detail": "cleanup_plan_present"}
        else:
            compensation = {"status": "NOT_REQUIRED", "detail": "no_cleanup_plan"}
    module_results["compensation_relation_plan"] = compensation
    # Compensation is informational — the compiler already validates cleanup.

    # ── Step 3: Oracle Input Contract ──
    oracle_contract = build_oracle_input_contract(
        experiment=exp,
        behavior_ir=ir,
    )
    module_results["oracle_input_contract"] = oracle_contract
    # Oracle input is informational — attached to experiment for runtime use.

    # ── Step 4: Binding Coverage Graph ──
    binding_graph = build_binding_coverage_graph(
        experiment=exp,
        behavior_ir=ir,
    )
    module_results["binding_coverage_graph"] = binding_graph

    # Only block on HARD binding issues: forbidden sources or cycles
    if _text(binding_graph.get("graph_status")) == "BLOCKED":
        issues = _list(binding_graph.get("issues"))
        has_forbidden = any(_text(i.get("kind")) == "FORBIDDEN_SOURCE" for i in issues)
        has_cycle = binding_graph.get("cycle_detected", False)
        if has_forbidden or has_cycle:
            blocking_reasons.append({
                "module": "binding_coverage_graph",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": ";".join(_text(i.get("kind")) for i in issues[:3]),
            })

    # ── Step 5: Fixture DAG Validation (only if fixtures exist) ──
    fixtures = _list(exp.get("fixtures")) or _list(exp.get("fixture_plan"))
    fixture_dag: dict[str, Any] = {"status": "NOT_REQUIRED"}
    if fixtures:
        fixture_dag = validate_fixture_dag(
            fixtures=fixtures,
            experiment=exp,
            behavior_ir=ir,
        )
    module_results["fixture_dependency_dag"] = fixture_dag

    # Only block on fixture cycles
    if _text(fixture_dag.get("status")) == "BLOCKED" and fixture_dag.get("cycle_detected"):
        blocking_reasons.append({
            "module": "fixture_dag",
            "reason_code": "BLOCKED_MISSING_FIXTURE",
            "detail": _text(fixture_dag.get("detail")),
        })

    # ── Verdict ──
    if not blocking_reasons:
        verdict = VERDICT_READY
    else:
        # Determine if source/environment dependent
        reason_codes = [r["reason_code"] for r in blocking_reasons]
        if any(rc in ("BLOCKED_MISSING_ACTOR", "BLOCKED_MISSING_ACTOR_SECRET", "BLOCKED_CONFLICTING_SOURCE") for rc in reason_codes):
            verdict = VERDICT_SOURCE_DEPENDENT
        elif any(rc in ("non_production_environment_required",) for rc in reason_codes):
            verdict = VERDICT_ENVIRONMENT_DEPENDENT
        else:
            verdict = VERDICT_BLOCKED

    # Fingerprint for the full orchestration result
    fp_content = {
        "obligation_id": oid,
        "verdict": verdict,
        "blocking_count": len(blocking_reasons),
        "binding_fingerprint": _text(binding_graph.get("binding_graph_fingerprint")),
        "observer_status": _text(observer_resolution.get("resolution_status")),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.v12-coverage-recovery-orchestrator.v1",
        "obligation_id": oid,
        "experiment_id": _text(exp.get("experiment_id")),
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "primary_blocking_reason": blocking_reasons[0] if blocking_reasons else None,
        "module_results": module_results,
        "coverage_recovery_version": "v1.2.1",
        "fingerprint": fingerprint,
    }
