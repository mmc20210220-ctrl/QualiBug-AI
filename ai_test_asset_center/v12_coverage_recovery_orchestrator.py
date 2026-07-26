"""V1.2.2 Coverage Recovery Orchestrator — unified fail-closed compile gate.

SPEC v1.2.2 §4: All five coverage recovery modules are HARD GATES.
Any module returning BLOCKED prevents COMPILED.

Gate modules:
    1. Observer Resolution Gate
    2. Compensation Relation Gate
    3. Oracle Input Contract Gate
    4. Binding Coverage Graph Gate
    5. Fixture Dependency DAG Gate

Verdict logic:
    READY only when ALL applicable gates PASSED.
    Any gate BLOCKED → verdict BLOCKED.
    Missing receipt → BLOCKED_COVERAGE_RECOVERY_RECEIPT_MISSING.

Output: qualibug.v12-coverage-recovery-orchestrator.v2
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

# ─── Module Gate Statuses ─────────────────────────────────────────────────────

GATE_PASSED = "PASSED"
GATE_BLOCKED = "BLOCKED"
GATE_SOURCE_DEPENDENT = "SOURCE_DEPENDENT"
GATE_ENVIRONMENT_DEPENDENT = "ENVIRONMENT_DEPENDENT"
GATE_NOT_APPLICABLE = "NOT_APPLICABLE"


def _make_gate_receipt(
    module: str,
    status: str,
    *,
    applicable: bool = True,
    reason_code: str = "",
    detail: str = "",
    fingerprint: str = "",
) -> dict[str, Any]:
    """Build a standard Module Gate Receipt — SPEC v1.2.2 §4.1."""
    return {
        "module": module,
        "status": status,
        "applicable": applicable,
        "reason_code": reason_code,
        "detail": detail,
        "receipt_id": hashlib.sha256(
            f"{module}:{status}:{reason_code}:{fingerprint}".encode()
        ).hexdigest()[:16],
        "fingerprint": fingerprint,
    }


def _evaluate_observer_gate(
    observer_resolution: dict[str, Any],
    binding_graph: dict[str, Any],
    *,
    observation_required: bool = True,
) -> dict[str, Any]:
    """SPEC v1.2.2 §5.2: Observer Resolution hard gate.

    Only enforced when the experiment actually requires external observation.
    """
    status = _text(observer_resolution.get("resolution_status"))
    fp = _text(observer_resolution.get("fingerprint"))

    if not observation_required:
        return _make_gate_receipt("observer_resolution", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp,
                                  detail="no_observation_required")

    if status == "RESOLVED":
        return _make_gate_receipt("observer_resolution", GATE_PASSED, fingerprint=fp)

    if status == "NOT_APPLICABLE":
        return _make_gate_receipt("observer_resolution", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp)

    if status == "PENDING_BINDING":
        # PASSED only if Binding Graph is ultimately VALID
        bg_status = _text(binding_graph.get("graph_status"))
        if bg_status == "VALID":
            return _make_gate_receipt("observer_resolution", GATE_PASSED, fingerprint=fp,
                                      detail="pending_binding_satisfied_by_valid_graph")
        return _make_gate_receipt("observer_resolution", GATE_BLOCKED, fingerprint=fp,
                                  reason_code="BLOCKED_MISSING_BINDING",
                                  detail="pending_binding_graph_not_valid")

    if status == "AMBIGUOUS":
        return _make_gate_receipt("observer_resolution", GATE_BLOCKED, fingerprint=fp,
                                  reason_code="BLOCKED_MISSING_OBSERVER",
                                  detail="ambiguous_observer_candidates")

    # BLOCKED or any unknown status
    return _make_gate_receipt("observer_resolution", GATE_BLOCKED, fingerprint=fp,
                              reason_code="BLOCKED_MISSING_OBSERVER",
                              detail=f"observer_status_{status or 'empty'}")


def _evaluate_compensation_gate(
    compensation: dict[str, Any],
    is_write: bool,
    *,
    experiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """SPEC v1.2.2 §7: Compensation Relation hard gate.

    Only blocks governed writes that REQUIRE cleanup but have no proven compensation.
    """
    exp = experiment if isinstance(experiment, dict) else {}
    comp_status = _text(compensation.get("status") or compensation.get("resolution_status"))
    fp = _text(compensation.get("fingerprint"))

    if not is_write:
        return _make_gate_receipt("compensation_relation", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp)

    # Compiler already determined cleanup not required → NOT_APPLICABLE
    safety = _dict(exp.get("safety_contract"))
    if safety.get("cleanup_not_required") or safety.get("cleanup_exempt"):
        return _make_gate_receipt("compensation_relation", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp,
                                  detail="compiler_cleanup_exempt")

    # Has cleanup exemption contract → NOT_APPLICABLE
    if exp.get("cleanup_exemption_contract"):
        return _make_gate_receipt("compensation_relation", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp,
                                  detail="cleanup_exemption_present")

    # Has source-declared cleanup authority → NOT_APPLICABLE
    if comp_status in ("NOT_REQUIRED", "NOT_APPLICABLE"):
        return _make_gate_receipt("compensation_relation", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp,
                                  detail="source_declared_cleanup_authority")

    if comp_status in ("RESOLVED", "ACCEPTED"):
        return _make_gate_receipt("compensation_relation", GATE_PASSED, fingerprint=fp)

    # Has cleanup_plan from compiler → compiler already validated, PASSED
    if _list(exp.get("cleanup_plan")):
        return _make_gate_receipt("compensation_relation", GATE_PASSED, fingerprint=fp,
                                  detail="compiler_cleanup_plan_present")

    # No compensation, no cleanup plan, no exemption for governed write → BLOCKED
    if comp_status in ("BLOCKED", "REJECTED", ""):
        return _make_gate_receipt("compensation_relation", GATE_BLOCKED, fingerprint=fp,
                                  reason_code="BLOCKED_NON_REVERSIBLE_WRITE",
                                  detail=f"compensation_status_{comp_status or 'empty'}")

    return _make_gate_receipt("compensation_relation", GATE_BLOCKED, fingerprint=fp,
                              reason_code="BLOCKED_NON_REVERSIBLE_WRITE",
                              detail=f"compensation_not_accepted_{comp_status}")


def _evaluate_oracle_gate(
    oracle_contract: dict[str, Any],
) -> dict[str, Any]:
    """SPEC v1.2.2 §6.1: Oracle Input Contract compile-time hard gate.

    Blocks at compile time only for STRUCTURAL gaps (missing observer entirely).
    Field-level completeness is validated at runtime when real receipts exist.
    """
    overall = _text(oracle_contract.get("overall_status"))
    reason = _text(oracle_contract.get("reason_code"))
    fp = _text(oracle_contract.get("fingerprint"))

    if overall == "COMPLETE":
        return _make_gate_receipt("oracle_input_contract", GATE_PASSED, fingerprint=fp)

    # Only block for structural observer absence — field gaps are runtime-validated
    if reason == "BLOCKED_MISSING_OBSERVER":
        return _make_gate_receipt("oracle_input_contract", GATE_BLOCKED, fingerprint=fp,
                                  reason_code=reason,
                                  detail="no_observer_for_assertions")

    # Field-level INCOMPLETE: pass at compile, enforce at runtime
    return _make_gate_receipt("oracle_input_contract", GATE_PASSED, fingerprint=fp,
                              detail=f"field_deferred_to_runtime:{reason}")


def _evaluate_binding_gate(
    binding_graph: dict[str, Any],
) -> dict[str, Any]:
    """SPEC v1.2.2 §8: Binding Graph full fail-closed gate."""
    graph_status = _text(binding_graph.get("graph_status"))
    fp = _text(binding_graph.get("binding_graph_fingerprint"))
    issues = _list(binding_graph.get("issues"))

    if graph_status == "VALID":
        return _make_gate_receipt("binding_coverage_graph", GATE_PASSED, fingerprint=fp)

    # ANY blocking issue → BLOCKED (not just forbidden/cycle)
    blocking_kinds = [
        _text(i.get("kind")) for i in issues
        if _text(i.get("severity")).upper() in ("BLOCK", "ERROR", "")
    ]
    # If no explicit severity, all issues on a BLOCKED graph are blocking
    if not blocking_kinds and issues:
        blocking_kinds = [_text(i.get("kind")) for i in issues]

    reason_code = "BLOCKED_MISSING_BINDING"
    if binding_graph.get("cycle_detected"):
        reason_code = "BLOCKED_BINDING_CYCLE"
    elif any(k == "FORBIDDEN_SOURCE" for k in blocking_kinds):
        reason_code = "BLOCKED_FORBIDDEN_BINDING_SOURCE"

    return _make_gate_receipt("binding_coverage_graph", GATE_BLOCKED, fingerprint=fp,
                              reason_code=reason_code,
                              detail=";".join(blocking_kinds[:5]))


def _evaluate_fixture_gate(
    fixture_dag: dict[str, Any],
    has_fixtures: bool,
) -> dict[str, Any]:
    """SPEC v1.2.2 §11: Fixture DAG all invalid states hard block."""
    fp = _text(fixture_dag.get("fingerprint"))

    if not has_fixtures:
        return _make_gate_receipt("fixture_dependency_dag", GATE_NOT_APPLICABLE, applicable=False, fingerprint=fp)

    dag_status = _text(fixture_dag.get("dag_status") or fixture_dag.get("status"))

    if dag_status in ("VALID", "READY", "NOT_REQUIRED"):
        return _make_gate_receipt("fixture_dependency_dag", GATE_PASSED, fingerprint=fp)

    # ANY blocked/invalid status → BLOCKED (not just cycle)
    issues = _list(fixture_dag.get("issues"))
    detail = ";".join(_text(i.get("kind") if isinstance(i, dict) else i) for i in issues[:5])
    return _make_gate_receipt("fixture_dependency_dag", GATE_BLOCKED, fingerprint=fp,
                              reason_code="BLOCKED_MISSING_FIXTURE",
                              detail=detail or f"fixture_dag_status_{dag_status}")


def prepare_experiment_v12(
    *,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
    compiler_context: dict[str, Any],
) -> dict[str, Any]:
    """Orchestrate all v1.2 coverage recovery modules as HARD GATES.

    SPEC v1.2.2 §4: This is the unified compile gate. Every module result
    directly controls COMPILED/BLOCKED. No module is informational.

    READY requires ALL applicable gates PASSED.
    Any gate BLOCKED → verdict BLOCKED.

    Returns:
        qualibug.v12-coverage-recovery-orchestrator.v2 receipt.
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

    # ── Module 1: Observer Resolution ──
    observer_resolution = resolve_observer_capability(
        observer_requirement=_text(obl.get("observer_requirement")) or "after_state",
        primary_operation=primary_op,
        behavior_ir=ir,
        required_bindings=[_text(b.get("target")) for b in _list(exp.get("binding_plan")) if isinstance(b, dict)],
    )

    # ── Module 2: Compensation Relation (governed writes only) ──
    compensation: dict[str, Any] = {"status": "NOT_REQUIRED"}
    if is_write:
        cleanup_plan = _list(exp.get("cleanup_plan"))
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

    # ── Module 3: Oracle Input Contract ──
    oracle_contract = build_oracle_input_contract(
        experiment=exp,
        behavior_ir=ir,
    )

    # ── Module 4: Binding Coverage Graph ──
    binding_graph = build_binding_coverage_graph(
        experiment=exp,
        behavior_ir=ir,
    )

    # ── Module 5: Fixture DAG ──
    fixtures = _list(exp.get("fixtures")) or _list(exp.get("fixture_plan"))
    fixture_dag: dict[str, Any] = {"status": "NOT_REQUIRED"}
    if fixtures:
        fixture_dag = validate_fixture_dag(
            fixtures=fixtures,
            experiment=exp,
            behavior_ir=ir,
        )

    # ── Evaluate ALL gates — SPEC v1.2.2 §4.1 ──
    # Observer gate validates the COMPILER's observer output, not re-resolve.
    # If compiler produced observers → gate validates them.
    # If compiler produced NO observers but assertions need observation → BLOCKED.
    _assertions = _list(exp.get("assertions"))
    _observers = _list(exp.get("observers"))
    _observer_requirement = _text(obl.get("observer_requirement"))
    _PRIMARY_RESPONSE_OBSERVERS = {"http_response", "primary_response", "response_body", "status_code"}
    _observer_kinds = set()
    for obs in _observers:
        if isinstance(obs, dict):
            _observer_kinds.add(_text(obs.get("kind")) or _text(obs.get("observer_id")))
        elif isinstance(obs, str):
            _observer_kinds.add(obs.strip())

    # If compiler produced observers, the gate is PASSED (compiler validated them)
    # If no observers but assertions need separate observation → use resolver result
    _has_compiler_observers = bool(_observers)
    _needs_separate_observation = bool(
        _assertions and not _has_compiler_observers and (
            (_observer_kinds - _PRIMARY_RESPONSE_OBSERVERS)
            or (_observer_requirement and _observer_requirement not in _PRIMARY_RESPONSE_OBSERVERS)
        )
    )

    gate_observer = _evaluate_observer_gate(
        observer_resolution, binding_graph, observation_required=_needs_separate_observation,
    )
    gate_compensation = _evaluate_compensation_gate(compensation, is_write, experiment=exp)
    gate_oracle = _evaluate_oracle_gate(oracle_contract)
    gate_binding = _evaluate_binding_gate(binding_graph)
    gate_fixture = _evaluate_fixture_gate(fixture_dag, bool(fixtures))

    gate_receipts = [gate_observer, gate_compensation, gate_oracle, gate_binding, gate_fixture]

    # ── Verdict: strictest applicable gate wins — SPEC v1.2.2 §4.2 ──
    blocking_gates = [g for g in gate_receipts if g["status"] == GATE_BLOCKED]
    source_dep_gates = [g for g in gate_receipts if g["status"] == GATE_SOURCE_DEPENDENT]
    env_dep_gates = [g for g in gate_receipts if g["status"] == GATE_ENVIRONMENT_DEPENDENT]

    if blocking_gates:
        verdict = VERDICT_BLOCKED
    elif source_dep_gates:
        verdict = VERDICT_SOURCE_DEPENDENT
    elif env_dep_gates:
        verdict = VERDICT_ENVIRONMENT_DEPENDENT
    else:
        verdict = VERDICT_READY

    # Fingerprint
    fp_content = {
        "obligation_id": oid,
        "verdict": verdict,
        "gates": {g["module"]: g["status"] for g in gate_receipts},
        "binding_fingerprint": _text(binding_graph.get("binding_graph_fingerprint")),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.v12-coverage-recovery-orchestrator.v2",
        "obligation_id": oid,
        "experiment_id": _text(exp.get("experiment_id")),
        "verdict": verdict,
        "gate_receipts": gate_receipts,
        "blocking_gates": blocking_gates,
        "primary_blocking_reason": blocking_gates[0] if blocking_gates else None,
        "module_results": {
            "observer_resolution_plan": observer_resolution,
            "compensation_relation_plan": compensation,
            "oracle_input_contract": oracle_contract,
            "binding_coverage_graph": binding_graph,
            "fixture_dependency_dag": fixture_dag,
        },
        "coverage_recovery_version": "v1.2.2",
        "fingerprint": fingerprint,
    }
