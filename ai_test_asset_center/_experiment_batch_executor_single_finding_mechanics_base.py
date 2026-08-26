"""Batch execution of selected compiled experiments.

Extracted from ``experiment_executor``. Invokes ``execute_one_experiment`` per
selected obligation, then attaches operational / delivery-gate receipts. Re-exported
from ``experiment_executor`` for compatibility.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .contract_oracles import validate_contract_oracle_receipt
from .customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from .experiment_runtime_support import (
    _dict,
    _list,
    _stable_id,
    _text,
    load_actor_tokens,
)
from .operational_receipts import build_execution_operational_receipt
from .sandbox_write_executor_base import evaluator_request_trace
from .small_scale_validation_gate import HARD_BUDGET_CAP


def _load_service_base_urls(project: str, root: Path) -> dict[str, str]:
    """Load the project's multi-service base_url map (service name → base_url).

    Reads ``real_project_config.json`` → ``multi_service.services``. The map
    routes cross-service resolvers/fixtures (create an order on scm_trade,
    then drive integration/sales/{so_id}/to-outbound) to the owning service.
    Returns {} when the project declares no multi-service topology.
    """
    try:
        from .project_runtime_config import load_real_project_config

        config = load_real_project_config(str(project), Path(root)) or {}
    except Exception:
        return {}
    multi = config.get("multi_service") or {}
    services = multi.get("services") if isinstance(multi, dict) else None
    if not isinstance(services, dict):
        return {}
    return {
        str(name).strip(): str(url).strip()
        for name, url in services.items()
        if str(name).strip() and str(url).strip()
    }


def _deliverable_dedupe_key(finding: dict[str, Any]) -> str:
    """Collapse repeated deliveries of one operation-level property.

    One authorization/validation property (e.g. "this endpoint has no role
    gate") is compiled into multiple obligations — one per (control,
    treatment) actor pair — and each independently executes and reaches the
    delivery gate. Every pair reports the same operation-level violation, so
    N pairs produce N deliverable findings for one real defect. The
    evaluator's GT dedupe then keeps a single true positive and scores the
    other N-1 as false positives, cratering precision without adding
    discovery value.

    This key groups findings by (assertion kind, HTTP method, path) so only
    the first delivery of a property is kept; later duplicates are marked
    ``duplicate_of`` and counted, never delivered twice.
    """
    title = _text(finding.get("title"))
    import re as _re

    match = _re.search(
        r"\[ContractOracle\]\s+([A-Za-z0-9_]+):\s+\S+\s+(GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\s]+)",
        title,
    )
    if not match:
        return ""
    kind, method, path = match.groups()
    path = path.split("?", 1)[0].rstrip("/")
    return f"{kind}:{method}:{path}"


# ── P0-7: Parameter binding validation ──
import re as _re_bindings

_BINDING_PLACEHOLDER_RE = _re_bindings.compile(r"\{(\w+)\}|:(\w+)")


def _check_required_bindings(
    experiment: dict[str, Any],
    pre_resolved: dict[str, Any],
) -> list[str]:
    """Return list of unresolved required path parameters.

    Only blocks if a path placeholder cannot be resolved from:
    - pre_resolved bindings
    - experiment binding_plan
    - runtime_bindings already in the experiment
    """
    exp = _dict(experiment)
    known_bindings: set[str] = set()
    # From pre-resolved
    known_bindings.update(k for k, v in (pre_resolved or {}).items() if v not in (None, ""))
    # From binding_plan
    for item in _list(exp.get("binding_plan")):
        if isinstance(item, dict) and _text(item.get("target")):
            known_bindings.add(_text(item["target"]))
    # From runtime_bindings
    known_bindings.update(k for k, v in _dict(exp.get("runtime_bindings")).items() if v not in (None, ""))
    # From _pre_resolved_bindings injected at batch level
    known_bindings.update(k for k, v in _dict(exp.get("_pre_resolved_bindings")).items() if v not in (None, ""))

    unresolved: set[str] = set()
    for plan_key in ("treatment_plan", "control_plan"):
        for step in _list(exp.get(plan_key)):
            if not isinstance(step, dict):
                continue
            path = _text(step.get("path") or step.get("path_template"))
            if not path:
                continue
            for match in _BINDING_PLACEHOLDER_RE.finditer(path):
                param = match.group(1) or match.group(2)
                if param and param not in known_bindings:
                    # Common auto-resolvable params are not blockers
                    if param in ("id", "page", "limit", "offset", "sort"):
                        continue
                    unresolved.add(param)
    return sorted(unresolved)


def finalize_finding_evidence_after_delivery_gate(
    finding: dict[str, Any],
    *,
    gate_receipt: dict[str, Any],
    reproduction_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Align evidence pack with an independent v2 DELIVERABLE gate decision.

    Non-DELIVERABLE findings remain fail-closed with pending evidence status.
    Oracle and cleanup rules are never relaxed here. Gate receipt ids are
    stamped by the caller after the gate is rebuilt against this payload.
    """

    row = dict(_dict(finding))
    gate = _dict(gate_receipt)
    if _text(gate.get("status")) != "DELIVERABLE":
        return row

    reproduction = _dict(reproduction_receipt or row.get("reproduction_receipt"))
    can_reproduce = bool(
        _text(reproduction.get("receipt_id"))
        or _list(reproduction.get("steps"))
        or _list(row.get("reproduction_steps"))
    )
    quality = dict(_dict(row.get("evidence_quality")))
    quality.update({
        "level": "validated",
        "score": max(int(quality.get("score") or 0), 90),
        "can_reproduce": True if can_reproduce else bool(quality.get("can_reproduce")),
        "evidence_strength": _text(
            quality.get("evidence_strength")
            or "typed_contract_violation_gate_deliverable"
        ),
    })
    if can_reproduce:
        quality["can_reproduce"] = True

    missing = [
        _text(item)
        for item in _list(_dict(row.get("evidence_status")).get("missing_requirements"))
        if _text(item) and _text(item) != "independent_delivery_gate_receipt"
    ]
    evidence_status = dict(_dict(row.get("evidence_status")))
    evidence_status.update({
        "semantic_verdict": "SEMANTIC_CONFIRMED",
        "business_evidence_status": "VALIDATED",
        "final_review_status": "VALIDATED_CANDIDATE",
        "missing_requirements": missing,
        "delivery_gate_status": "DELIVERABLE",
    })
    row["evidence_quality"] = quality
    row["evidence_status"] = evidence_status
    row["business_evidence_status"] = "VALIDATED"
    row["final_review_status"] = "VALIDATED_CANDIDATE"
    row["semantic_verdict"] = "SEMANTIC_CONFIRMED"
    # Project the receipt adjudication onto the status fields that every
    # field-based downstream consumer reads (delivery re-check, regression
    # suite, closed-loop learning, readiness counters). A DELIVERABLE gate
    # only exists after the receipt chain proved execution, violation,
    # reproduction, and cleanup; leaving these fields at their initial
    # "suspected"/"candidate" values makes the field-based re-check reject
    # exactly the findings the formal gate adjudicated deliverable.
    adjudication = _dict(gate.get("adjudication"))
    if _text(adjudication.get("reproduction")).upper() == "REPRODUCED":
        row["bug_status"] = "reproduced"
    row["confirmation_status"] = "validated_candidate"
    row["execution_status"] = _text(row.get("execution_status")) or "executed"
    refs = _dict(gate.get("receipt_refs"))
    cleanup_decision = _text(adjudication.get("cleanup")).upper()
    if cleanup_decision in ("COMPLETED", "NOT_REQUIRED", "RESIDUE_ACCEPTED"):
        evidence_row = dict(_dict(row.get("evidence")))
        cleanup_refs = [
            _text(_dict(value).get("receipt_id"))
            for value in _list(refs.get("cleanup"))
            if isinstance(value, dict) and _text(_dict(value).get("receipt_id"))
        ]
        if cleanup_decision == "COMPLETED" and cleanup_refs:
            evidence_row["cleanup"] = {
                "status": "completed",
                "receipt_ref": cleanup_refs[0],
                "source": "delivery_gate_receipt_adjudication",
            }
        elif cleanup_decision == "NOT_REQUIRED":
            execution_ref = _text(_dict(refs.get("execution")).get("receipt_id"))
            if execution_ref:
                evidence_row["cleanup"] = {
                    "status": "not_required",
                    "reason_code": "CLEANUP_NOT_REQUIRED_RECEIPT_ATTESTED",
                    "receipt_ref": execution_ref,
                    "source": "delivery_gate_receipt_adjudication",
                }
        elif cleanup_decision == "RESIDUE_ACCEPTED":
            # Accepted residue is a legitimate terminal hygiene state on
            # declared non-production targets (原则14): the finding stands,
            # the leftover stays visible through the referenced receipts.
            residue_ref = (
                cleanup_refs[0]
                if cleanup_refs
                else _text(_dict(refs.get("execution")).get("receipt_id"))
            )
            if residue_ref:
                evidence_row["cleanup"] = {
                    "status": "residue_accepted",
                    "reason_code": "CLEANUP_RESIDUE_RECEIPT_ATTESTED",
                    "receipt_ref": residue_ref,
                    "source": "delivery_gate_receipt_adjudication",
                }
        row["evidence"] = evidence_row
    if reproduction:
        row["reproduction_receipt"] = reproduction
    return row


def stamp_finding_delivery_gate_refs(
    finding: dict[str, Any],
    *,
    gate_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Attach derived gate refs excluded from finding payload fingerprints."""

    row = dict(_dict(finding))
    gate = _dict(gate_receipt)
    row["delivery_gate_receipt"] = gate
    row["delivery_gate_receipt_id"] = _text(gate.get("gate_receipt_id"))
    row["gate_passed"] = _text(gate.get("status")) == "DELIVERABLE"
    row["customer_delivery_status"] = (
        "defect" if row["gate_passed"] else "candidate"
    )
    row["customer_visible"] = bool(row["gate_passed"])
    row["customer_delivery_gate_reasons"] = list(gate.get("reason_codes") or [])
    return row


def _operation_coverage_budget(
    selected: list[Any],
    budget: int,
    hard_cap: int = HARD_BUDGET_CAP,
) -> int:
    """Floor the batch budget at one experiment per distinct operation.

    The planner auto-scales the slice budget to the compiled pool, but the
    per-batch execution budget is phase-based and can be far smaller. A
    global-priority truncation under a tiny budget lets whole operations
    starve at OBLIGATION_BUDGET_REACHED (measured: 859 compiled, 233
    executed, 539 deferred). Raising the budget to the distinct-operation
    count (still bounded by the hard cap) makes the prioritizer's
    operation-fair tier able to fit every operation, so no operation can be
    excluded from a batch purely by global rank.

    Operation identity is accepted on either carrier the mainline can hand
    down: the planner row's ``operation_key`` or the intent row's
    ``operation_refs`` (agent-intent rows carry operation_refs, not
    operation_key; counting only operation_key silently disabled the floor
    on the mainline batch path — run16: 1200 selected, per-batch budget
    stayed at the phase default while 1100 compiled obligations deferred).
    """
    distinct_operations = len({
        _text(_dict(item).get("operation_key"))
        for item in _list(selected)
        if _text(_dict(item).get("operation_key"))
    } | {
        _text(ref)
        for item in _list(selected)
        for ref in _list(_dict(item).get("operation_refs"))
        if _text(ref)
    })
    return max(int(budget), min(distinct_operations, int(hard_cap)))


def _family_coverage_budget(
    selected: list[Any],
    budget: int,
    hard_cap: int = HARD_BUDGET_CAP,
) -> int:
    """Floor the batch budget so family-fair and operation-fair both fit.

    Family-fair execution budget (distribution balance): the operation floor
    guarantees every operation a slot, but a family whose obligations are
    all second-tier rows of operations dominated by authorization would still
    starve. The prioritizer promotes one row per family ABOVE the
    operation tier; when several families' top rows land on the same
    operation, the family tier and the operation tier together can need up
    to ``#operations + #families`` distinct rows before every operation AND
    every family is inside the budget. Raising the budget to that union
    bound (capped at the same hard cap) keeps BOTH guarantees intact, so
    state/idempotency/conservation/validation/privacy obligations can no
    longer be pushed out of a batch purely by global rank. Families come
    from the obligation rows themselves (the product's open family
    registry), never hardcoded.
    """
    distinct_operations = len({
        _text(_dict(item).get("operation_key"))
        for item in _list(selected)
        if _text(_dict(item).get("operation_key"))
    } | {
        _text(ref)
        for item in _list(selected)
        for ref in _list(_dict(item).get("operation_refs"))
        if _text(ref)
    })
    distinct_families = len({
        _text(_dict(item).get("risk_family"))
        for item in _list(selected)
        if _text(_dict(item).get("risk_family"))
    })
    if distinct_operations and distinct_families:
        union_floor = distinct_operations + distinct_families
    else:
        union_floor = max(distinct_operations, distinct_families)
    return max(int(budget), min(union_floor, int(hard_cap)))


def _pending_scan_cancel(root: Path, project: str) -> dict[str, Any]:
    """Read-only view of a pending operator cancel request for this scan lease.

    Cancellation is cooperative observability: a failed check never aborts or
    fakes a cancellation — the scan continues and the failure stays logged.
    The marker is consumed once by the mainline executor entry, never here, so
    parallel serial groups all observe the same pending request.
    """

    try:
        from .scan_cancellation import read_scan_cancel_request

        return read_scan_cancel_request(Path(root), project)
    except Exception as exc:
        logger.warning("scan_cancel_check_failed error_type=%s error=%s", type(exc).__name__, str(exc)[:200])
        return {}


def execute_selected_experiments(
    selected: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str = "",
    experiment_budget: int = 100,
    validation_phase: str = "",  # "small_scale" or "formal" or "" (auto)
) -> dict[str, Any]:
    """Execute every selected experiment; each yields EXECUTED or BLOCKED receipt.
    
    experiment_budget: maximum number of experiments to execute (default 100).
    validation_phase: "small_scale" (≤20) or "formal" (≤100) or "" (auto-detect).
    Experiments beyond the budget are marked BUDGET_EXCEEDED.
    """
    # Lazy import avoids a package cycle with experiment_executor re-exports.
    from .experiment_executor import execute_one_experiment

    selected_ids = [_text(_dict(item).get("obligation_id")) for item in selected]
    if not all(selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected_obligation_identity_invalid")
    run_contract = _dict(mainline_run)
    if not run_contract or _text(run_contract.get("campaign_id")) != _text(campaign_id):
        raise ValueError("experiment batch mainline campaign identity mismatch")
    tokens = load_actor_tokens(root, project, base_url=base_url)
    service_base_urls = _load_service_base_urls(project, root)

    # ── Phase 2: Auto-resolve runtime bindings before execution ──
    # Pre-resolve path placeholders by calling GET list endpoints from Behavior IR.
    _pre_resolved_bindings: dict[str, str] = {}
    _state_scoped_bindings: dict[str, dict[str, str]] = {}
    if base_url and tokens:
        from .runtime_binding_resolver import (
            auto_resolve_bindings,
            collect_placeholder_collection_hints,
        )
        _exps_for_placeholders = [
            experiments_by_obligation.get(_text(_dict(s).get("obligation_id")), {})
            for s in selected
        ]
        _ph_hints = collect_placeholder_collection_hints(
            _exps_for_placeholders, behavior_ir
        )
        _required_phs = set(_ph_hints)
        if _required_phs:
            _resolution = auto_resolve_bindings(
                behavior_ir, tokens, base_url,
                required_placeholders=_required_phs,
                placeholder_collection_hints=_ph_hints,
                service_base_urls=service_base_urls,
            )
            _pre_resolved_bindings = dict(_resolution.get("bindings") or {})
        # State-scoped placeholders cannot share one batch value: a
        # state-machine experiment needs an entity in its declared source
        # state (CANCELLED order for cancel, PAID order for ship), and
        # different experiments on the same placeholder need different
        # states. Resolve those per experiment and let the per-experiment
        # values override the batch value.
        from .runtime_binding_resolver import (
            resolve_state_scoped_bindings,
        )
        _state_scoped_bindings = resolve_state_scoped_bindings(
            _exps_for_placeholders, tokens, base_url,
            service_base_urls=service_base_urls,
        )

    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    delivered_finding_ids: dict[str, dict[str, Any]] = {}
    duplicate_delivery_count = 0
    blocked = 0
    executed = 0
    harness = 0
    cleanup_failures = 0
    operator_cancelled_count = 0
    operator_cancel_request: dict[str, Any] = {}
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    batch_nonce = str(time.time_ns())
    # ── Experiment budget enforcement with validation phase ──
    # Read budget from runtime_contract or use parameter default.
    # Prevents runaway execution (e.g. 2700+ experiments in 4.5h).
    # Phase-aware: small_scale ≤20, formal ≤100.
    from .small_scale_validation_gate import (
        get_validation_budget,
        SMALL_SCALE_BUDGET,
        FORMAL_BUDGET,
    )
    _phase = str(validation_phase or _dict(runtime_contract).get("validation_phase") or "").strip().lower()
    if _phase in ("small_scale", "small", "validation"):
        _phase = "small_scale"
    elif _phase in ("formal", "full", "production"):
        _phase = "formal"
    else:
        _phase = "small_scale"  # Default to small-scale for safety
    _budget = get_validation_budget(runtime_contract, phase=_phase)
    _budget = max(1, min(_budget, HARD_BUDGET_CAP))
    # ── Operation-coverage floor ──
    # One experiment per distinct operation minimum, bounded by the same hard
    # cap. See _operation_coverage_budget for the rationale.
    _budget = _operation_coverage_budget(
        selected, _budget, hard_cap=HARD_BUDGET_CAP
    )
    # ── Family-coverage floor (family-fair execution budget) ──
    # One experiment per distinct risk family minimum, bounded by the same
    # hard cap, so the prioritizer's family-fair tier can fit every family
    # (authorization can no longer crowd out state/idempotency/conservation/
    # validation/privacy). The quota itself is operator-configurable through
    # the runtime contract (family_execution_quota, default 1).
    _budget = _family_coverage_budget(
        selected, _budget, hard_cap=HARD_BUDGET_CAP
    )
    _family_quota = max(
        1, int(_dict(runtime_contract).get("family_execution_quota") or 0) or 1
    )
    _total_selected = len(selected)
    # Rows past the per-batch budget are handed back so the caller can run them
    # in a later round. Dropping them here leaves the obligation with no terminal
    # receipt and makes a throttled batch look like an empty plan.
    budget_deferred: list[dict[str, Any]] = []

    # ── SPEC v1.2.2 §12: Prioritize experiments before budget enforcement ──
    # Prioritizer failure in formal campaign → HARNESS_FAILURE, no transport.
    _prioritization_receipt: dict[str, Any] = {}
    _prioritization_failed = False
    _prioritization_error = ""
    try:
        from .safe_experiment_prioritizer import prioritize_experiments
        _exps_for_priority = [
            experiments_by_obligation.get(_text(_dict(s).get("obligation_id")), {})
            for s in selected
        ]
        _obligations_for_priority = [
            _dict(s) for s in selected
        ]
        _prioritization_receipt = prioritize_experiments(
            experiments=_exps_for_priority,
            obligations=_obligations_for_priority,
            behavior_ir=behavior_ir,
            budget=_budget,
            family_quota=_family_quota,
            family_cap_shares=_dict(runtime_contract).get(
                "family_execution_cap_shares"
            ),
        )
        # The prioritizer's canonical output is the "prioritized" scored list
        # (obligation_id per row); reading a non-existent key silently disabled
        # the ordering — the budget cut then ran in planner order and the
        # family-fair / operation-fair tiers never reached execution.
        _ordered_ids = [
            _text(_dict(row).get("obligation_id"))
            for row in _list(_prioritization_receipt.get("prioritized"))
            if _text(_dict(row).get("obligation_id"))
        ]
        if _ordered_ids:
            # Reorder selected based on prioritizer output
            _id_to_item = {_text(_dict(s).get("obligation_id")): s for s in selected}
            _reordered = [_id_to_item[oid] for oid in _ordered_ids if oid in _id_to_item]
            # Append any items not in the ordered list
            _remaining = [s for s in selected if _text(_dict(s).get("obligation_id")) not in set(_ordered_ids)]
            selected = _reordered + _remaining
    except Exception as exc:
        _prioritization_failed = True
        _prioritization_error = str(exc)
        logger.warning("batch prioritization failed: %s", exc)

    if _total_selected > _budget:
        logger.info(
            "Experiment budget: deferring %d of %d selected to a later round (budget=%d)",
            _total_selected - _budget, _total_selected, _budget,
        )
        budget_deferred = [dict(_dict(item)) for item in selected[_budget:]]
        selected = selected[:_budget]
    budget_exceeded = _total_selected - len(selected)
    # ── Single-service execution scope ──
    # The IR carries every service's operations so cross-service resolvers
    # stay available, but a single-base_url run must never EXECUTE another
    # service's operations (they 404 on this base_url). Filter the selected
    # set by the experiment's control/treatment operation service. The target
    # service is derived from the approved base_url port.
    _target_svc_name = ""
    try:
        from .discovery_runtime_planning import _target_service_name_from_base_url

        _target_svc_name = _target_service_name_from_base_url(base_url)
    except Exception:
        _target_svc_name = ""
    if _target_svc_name:
        _op_service_map: dict[str, str] = {}
        for _op_row in _list(_dict(behavior_ir).get("operations")):
            if isinstance(_op_row, dict) and _text(_op_row.get("id")):
                _op_service_map[_text(_op_row.get("id"))] = _text(
                    _op_row.get("_service_name") or _op_row.get("service")
                )
        _service_filtered: list[Any] = []
        _cross_service_skipped = 0
        for _item in selected:
            _oid = _text(_dict(_item).get("obligation_id"))
            _exp = experiments_by_obligation.get(_oid) or {}
            _op_refs: list[str] = []
            for _plan_name in ("control_plan", "treatment_plan"):
                for _step in _list(_exp.get(_plan_name)):
                    if isinstance(_step, dict) and _text(_step.get("operation_ref")):
                        _op_refs.append(_text(_step.get("operation_ref")))
            _op_refs.extend(_text(v) for v in _list(_exp.get("operation_refs")) if _text(v))
            _foreign = [
                _op_service_map.get(op_ref)
                for op_ref in _op_refs
                if _op_service_map.get(op_ref) and _op_service_map[op_ref] != _target_svc_name
            ]
            if _foreign:
                _cross_service_skipped += 1
                continue
            _service_filtered.append(_item)
        if _cross_service_skipped:
            print(
                f"[execution-scope] skipped {_cross_service_skipped} experiments "
                f"whose operations belong to other services (target={_target_svc_name})",
                flush=True,
            )
        selected = _service_filtered
    for index, item in enumerate(selected):
        row = _dict(item)
        # ── Cooperative operator-cancel checkpoint ──
        # Checked before every experiment boundary. The in-flight experiment is
        # never killed mid-transport; everything not yet started receives a
        # terminal DEFERRED receipt (never budget_deferred, which would
        # auto-continue the cancelled work in a later round of this scan).
        if root and project:
            _cancel_payload = _pending_scan_cancel(Path(root), project)
            if _cancel_payload:
                operator_cancel_request = dict(_cancel_payload)
                for _defer_index in range(index, len(selected)):
                    _defer_row = _dict(selected[_defer_index])
                    _oid = _text(_defer_row.get("obligation_id"))
                    _eid = _text(_defer_row.get("experiment_id"))
                    if not _oid or _oid in execution_results:
                        continue
                    execution_results[_oid] = {
                        "status": "DEFERRED",
                        "reason_code": "OPERATOR_CANCELLED",
                        "detail": "scan_cancelled_by_operator_before_execution",
                        "experiment_id": _eid,
                        "cost_coverage_status": "UNKNOWN",
                    }
                    results.append({
                        "schema_version": "qualibug.experiment-execution.v1",
                        "candidate_id": _text(_defer_row.get("candidate_id")),
                        "slice_id": _text(
                            _defer_row.get("slice_id")
                            or _defer_row.get("behavior_slice_id")
                        ),
                        "obligation_id": _oid,
                        "experiment_id": _eid,
                        "campaign_id": campaign_id,
                        "status": "DEFERRED",
                        "reason_code": "OPERATOR_CANCELLED",
                        "detail": "scan_cancelled_by_operator_before_execution",
                        "finding": None,
                        "execution_receipt": {
                            "status": "DEFERRED",
                            "reason_code": "OPERATOR_CANCELLED",
                            "obligation_id": _oid,
                            "experiment_id": _eid,
                            "campaign_id": campaign_id,
                        },
                    })
                    operator_cancelled_count += 1
                logger.warning(
                    "batch execution stopped by operator cancel: project=%s "
                    "campaign=%s deferred_before_execution=%d requested_by=%s",
                    project,
                    campaign_id,
                    operator_cancelled_count,
                    str(operator_cancel_request.get("requester") or {}),
                )
                break
        oid = _text(row.get("obligation_id"))
        eid = _text(row.get("experiment_id"))
        candidate_id = _text(row.get("candidate_id")) or _stable_id("cand", project, oid or index)
        slice_id = _text(row.get("slice_id") or row.get("behavior_slice_id")) or _stable_id("slice", project, oid or candidate_id)
        execution_id = _stable_id("exec", project, campaign_id, eid, oid, batch_nonce, index)
        evidence_id = _stable_id("evidence", execution_id)
        exp = experiments_by_obligation.get(oid)
        if not isinstance(exp, dict):
            blocked += 1
            compile_results[oid] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "experiment_id": eid,
                "receipt_id": _stable_id("compile", project, campaign_id, oid, batch_nonce),
            }
            missing_outcome = {
                "schema_version": "qualibug.experiment-execution.v1",
                "candidate_id": candidate_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "selected_obligation_has_no_compiled_experiment",
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "candidate_id": candidate_id,
                    "slice_id": slice_id,
                    "obligation_id": oid,
                    "experiment_id": eid,
                    "execution_id": execution_id,
                    "evidence_id": evidence_id,
                    "campaign_id": campaign_id,
                },
            }
            results.append(missing_outcome)
            continue
        execution_oid = _text(exp.get("obligation_id"))
        if not execution_oid:
            raise ValueError("compiled_experiment_obligation_identity_missing")
        if execution_oid != oid:
            execution_id = _stable_id(
                "exec",
                project,
                campaign_id,
                eid,
                execution_oid,
                batch_nonce,
                index,
            )
            evidence_id = _stable_id("evidence", execution_id)
        compile_receipt = _dict(exp.get("compile_receipt"))
        compile_status = _text(compile_receipt.get("status")).upper()
        if compile_status != "COMPILED":
            terminal_status = (
                compile_status
                if compile_status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}
                else "HARNESS_FAILED"
            )
            reason_code = _text(compile_receipt.get("reason_code")) or (
                "COMPILE_RECEIPT_MISSING"
                if not compile_status
                else "COMPILE_STATUS_INVALID"
            )
            compile_results[oid] = {
                "status": terminal_status,
                "reason_code": reason_code,
                "experiment_id": _text(exp.get("experiment_id") or eid),
                "receipt_id": _text(compile_receipt.get("receipt_id"))
                or _stable_id("compile", project, campaign_id, oid, batch_nonce),
            }
            results.append({
                "schema_version": "qualibug.experiment-execution.v1",
                "candidate_id": candidate_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": _text(exp.get("experiment_id") or eid),
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "status": terminal_status,
                "reason_code": reason_code,
                "detail": (
                    "experiment_compile_receipt_not_executable:"
                    f"{compile_status or 'MISSING'}"
                ),
                "finding": None,
                # When compile fails, execution_receipt must NOT have terminal status
                # to avoid duplicate_terminal_receipt in ledger validation.
                "execution_receipt": {
                    "status": "NOT_EXECUTED",
                    "reason_code": reason_code,
                    "obligation_id": oid,
                    "experiment_id": _text(exp.get("experiment_id") or eid),
                    "campaign_id": campaign_id,
                },
            })
            if terminal_status == "BLOCKED":
                blocked += 1
            else:
                harness += 1
            continue
        compile_results[oid] = {
            "status": "COMPILED",
            "reason_code": "",
            "experiment_id": _text(exp.get("experiment_id") or eid),
            "receipt_id": _text(compile_receipt.get("receipt_id"))
            or _stable_id("compile", project, campaign_id, oid, batch_nonce),
            "input_fingerprint": _text(compile_receipt.get("input_fingerprint")),
        }
        with evaluator_request_trace({
            "run_id": _text(run_contract.get("run_id")),
            "campaign_id": campaign_id,
            "target_id": _text(run_contract.get("target_id")),
            "obligation_id": execution_oid,
            "execution_id": execution_id,
        }):
            # Inject pre-resolved bindings into experiment for runtime use
            _exp_bindings = dict(_pre_resolved_bindings)
            _exp_bindings.update(
                _dict(_state_scoped_bindings.get(execution_oid))
            )
            if _exp_bindings:
                exp = dict(exp)
                exp["_pre_resolved_bindings"] = _exp_bindings
            # ── P0-7: Validate parameter bindings before execution ──
            _unresolved_params = _check_required_bindings(exp, _exp_bindings)
            if _unresolved_params:
                blocked += 1
                compile_results[oid] = {
                    "status": "BLOCKED",
                    "reason_code": "PARAMETER_BINDING_BLOCKED",
                    "experiment_id": _text(exp.get("experiment_id") or eid),
                    "unresolved_parameters": _unresolved_params,
                }
                results.append({
                    "schema_version": "qualibug.experiment-execution.v1",
                    "candidate_id": candidate_id,
                    "slice_id": slice_id,
                    "obligation_id": oid,
                    "experiment_id": _text(exp.get("experiment_id") or eid),
                    "execution_id": execution_id,
                    "evidence_id": evidence_id,
                    "campaign_id": campaign_id,
                    "status": "BLOCKED",
                    "reason_code": "PARAMETER_BINDING_BLOCKED",
                    "detail": f"unresolved_parameters:{','.join(_unresolved_params[:10])}",
                    "finding": None,
                    "execution_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "PARAMETER_BINDING_BLOCKED",
                        "obligation_id": oid,
                        "experiment_id": _text(exp.get("experiment_id") or eid),
                        "campaign_id": campaign_id,
                    },
                })
                continue
            # ── [exec-trace] per-experiment wall clock (WARNING-level so
            # standalone CLI runs surface it; INFO is filtered there) ──
            import time as _t_exp

            _exp_t0 = _t_exp.perf_counter()
            outcome = execute_one_experiment(
                exp,
                behavior_ir=behavior_ir,
                root=root,
                project=project,
                base_url=base_url,
                runtime_contract=runtime_contract,
                campaign_id=campaign_id,
                execution_id=execution_id,
                actor_tokens=tokens,
            )
            logger.warning(
                "[exec-trace] experiment eid=%s obligation=%s total_ms=%d status=%s",
                _text(outcome.get("experiment_id")) or eid,
                execution_oid,
                int((_t_exp.perf_counter() - _exp_t0) * 1000),
                _text(_dict(outcome.get("execution_receipt")).get("status")),
            )
        eid = _text(outcome.get("experiment_id")) or eid
        # The executor may run under a per-attempt execution identity (runtime
        # actor exploration appends ``_a{attempt_index}``). The loop's
        # ``execution_id`` is the pre-exploration identity; the outcome already
        # carries the identity the evidence chain was ACTUALLY sealed under.
        # Every delivery receipt below must bind that same identity — otherwise
        # ``build_reproduction_receipt`` raises
        # ``reproduction_oracle_lineage_mismatch`` and an already-found
        # VIOLATION finding is discarded as a harness failure.
        effective_execution_id = _text(outcome.get("execution_id") or execution_id)
        outcome.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "selected_obligation_id": oid,
            "obligation_id": execution_oid,
            "experiment_id": eid,
            "execution_id": effective_execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
        })
        receipt = _dict(outcome.get("execution_receipt"))
        receipt.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "selected_obligation_id": oid,
            "obligation_id": execution_oid,
            "experiment_id": eid,
            "execution_id": effective_execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
        })
        outcome["execution_receipt"] = receipt
        if isinstance(outcome.get("finding"), dict):
            finding = outcome["finding"]
            finding_id = _text(finding.get("id") or finding.get("finding_id")) or _stable_id("finding", evidence_id)
            finding.update({
                "id": finding_id,
                "finding_id": finding_id,
                "candidate_id": candidate_id,
                "behavior_slice_id": slice_id,
                "slice_id": slice_id,
                "selected_obligation_id": oid,
                "obligation_id": execution_oid,
                "experiment_id": eid,
                "execution_id": effective_execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "mainline_run": {
                    "contract_fingerprint": _text(
                        run_contract.get("contract_fingerprint")
                    ),
                },
            })
            finding_evidence = _dict(finding.get("evidence"))
            finding_evidence["evidence_id"] = evidence_id
            finding_evidence["execution_id"] = execution_id
            finding["evidence"] = finding_evidence
        observation_receipt_ids: list[str] = []
        for step_index, step in enumerate(_list(outcome.get("steps"))):
            if not isinstance(step, dict):
                continue
            observation_receipt_id = _stable_id(
                "observation",
                execution_id,
                step_index,
                step.get("phase"),
                step.get("method"),
                step.get("path"),
            )
            step["observation_receipt_id"] = observation_receipt_id
            observation_receipt_ids.append(observation_receipt_id)
        for observer_receipt in _list(outcome.get("observer_receipts")):
            if not isinstance(observer_receipt, dict):
                continue
            observer_receipt_id = _text(observer_receipt.get("receipt_id"))
            if observer_receipt_id:
                observation_receipt_ids.append(observer_receipt_id)
        for contract_receipt in _list(outcome.get("contract_evidence_receipts")):
            if not isinstance(contract_receipt, dict):
                continue
            contract_receipt_id = _text(contract_receipt.get("receipt_id"))
            if contract_receipt_id:
                observation_receipt_ids.append(contract_receipt_id)
        observation_receipt_ids = list(dict.fromkeys(observation_receipt_ids))
        oracle_verdict = _dict(outcome.get("oracle_verdict"))
        oracle_receipt_id = ""
        # A completeness-gate block is NOT an oracle receipt. The finalizer returns a
        # deliberately minimal verdict -- {status, verdict, reason_codes, assertions,
        # missing_requirements, oracle_blocked_by_completeness_gate, completeness_proof}
        # -- precisely because the Oracle must not be called on incomplete input.
        # Validating it as a full 20-field receipt raised
        # contract_oracle_receipt_fields_invalid and killed the entire pipeline: on a live
        # target the scan failed outright while the HTTP envelope still said ok: true.
        #
        # The authorization delivery gate produces the same shape of non-receipt
        # block: {status, verdict, customer_deliverable_candidate,
        # authorization_delivery_gate, authorization_delivery_reason} with no
        # schema_version / receipt_id. Treat both as non-receipt blocks so neither
        # is validated as a sealed receipt (which raised
        # contract_oracle_receipt_fingerprint_invalid and discarded the outcome as
        # HARNESS_FAILED).
        #
        # Anything CLAIMING to be a receipt is still validated strictly. Only a verdict
        # that carries neither schema_version nor receipt_id, and says why it was blocked,
        # is carried through as the block it is.
        _is_gate_block = (
            bool(
                oracle_verdict.get("oracle_blocked_by_completeness_gate")
                or oracle_verdict.get("authorization_delivery_gate")
            )
            and not (
                _text(oracle_verdict.get("schema_version"))
                or _text(oracle_verdict.get("receipt_id"))
            )
        )
        if oracle_verdict and not _is_gate_block:
            try:
                validated_oracle = validate_contract_oracle_receipt(oracle_verdict)
            except Exception as _oracle_validate_exc:
                # Diagnostic-only capture: dump the rejected receipt so a
                # bounded run exposes the exact schema/activation mismatch
                # (never affects product behavior).
                import os as _diag_os

                _diag_path = str(
                    _diag_os.environ.get("QUALIBUG_ORACLE_DIAG_PATH") or ""
                ).strip()
                if _diag_path:
                    try:
                        import json as _diag_json

                        with open(_diag_path, "a", encoding="utf-8") as _diag_fh:
                            _diag_fh.write(_diag_json.dumps({
                                "event": "ORACLE_RECEIPT_REJECTED",
                                "error": f"{type(_oracle_validate_exc).__name__}:{_oracle_validate_exc}",
                                "obligation_id": _text(exp.get("obligation_id")),
                                "experiment_id": _text(exp.get("experiment_id")),
                                "oracle_verdict": oracle_verdict,
                            }, ensure_ascii=False, default=str) + "\n")
                    except OSError:
                        pass
                raise
            oracle_receipt_id = _text(validated_oracle.get("receipt_id"))
            outcome["oracle_verdict"] = validated_oracle
        status = _text(outcome.get("status")).upper()
        if status not in {"EXECUTED", "BLOCKED", "HARNESS_FAILURE", "HARNESS_FAILED", "DELIVERABLE", "EXECUTED_BUT_NOT_RESTORED"}:
            raise ValueError(f"experiment_execution_status_invalid:{status or 'MISSING'}")
        outcome_cleanup_failures = int(outcome.get("cleanup_failures") or 0)
        if outcome_cleanup_failures:
            # Record cleanup issues as metadata without overriding execution status.
            execution_receipt = _dict(outcome.get("execution_receipt"))
            execution_receipt["cleanup_failures"] = outcome_cleanup_failures
            execution_receipt["cleanup_warning"] = True
            outcome["execution_receipt"] = execution_receipt
        operational_receipt = build_execution_operational_receipt(
            receipt_id=_stable_id("operational", execution_id),
            execution_status=status,
            steps=[
                row
                for row in _list(outcome.get("steps"))
                if isinstance(row, dict)
            ],
            cleanup_failures=outcome_cleanup_failures,
        )
        outcome["operational_receipt"] = operational_receipt
        outcome_execution_receipt = _dict(outcome.get("execution_receipt"))
        outcome_execution_receipt["operational_receipt_id"] = _text(
            operational_receipt.get("receipt_id")
        )
        outcome["execution_receipt"] = outcome_execution_receipt
        results.append(outcome)
        if status == "BLOCKED":
            blocked += 1
            execution_results[oid] = {
                "status": "BLOCKED",
                "reason_code": _text(outcome.get("reason_code"))
                or "BLOCKED_EXECUTION",
                "detail": _text(
                    outcome.get("detail")
                    or outcome.get("reason_detail")
                    or _dict(outcome.get("execution_receipt")).get("detail")
                ),
                "reason_detail": _text(
                    outcome.get("reason_detail")
                    or outcome.get("detail")
                    or _dict(outcome.get("execution_receipt")).get("detail")
                ),
                "selected_obligation_id": oid,
                "executed_obligation_id": execution_oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "operational_receipt": operational_receipt,
            }
        elif status == "HARNESS_FAILURE":
            harness += 1
            execution_results[oid] = {
                "status": "HARNESS_FAILED",
                "reason_code": _text(outcome.get("reason_code"))
                or "HARNESS_FAILURE",
                "detail": _text(
                    outcome.get("detail")
                    or outcome.get("reason_detail")
                    or _dict(outcome.get("execution_receipt")).get("detail")
                ),
                "reason_detail": _text(
                    outcome.get("reason_detail")
                    or outcome.get("detail")
                    or _dict(outcome.get("execution_receipt")).get("detail")
                ),
                "selected_obligation_id": oid,
                "executed_obligation_id": execution_oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "operational_receipt": operational_receipt,
            }
        else:
            delivery_execution_receipt = build_delivery_execution_receipt(
                mainline_run=run_contract,
                candidate_id=candidate_id,
                slice_id=slice_id,
                obligation_id=execution_oid,
                experiment_id=eid,
                execution_id=effective_execution_id,
                evidence_id=evidence_id,
                operational_receipt=operational_receipt,
                observation_receipt_ids=observation_receipt_ids,
                oracle_receipt_id=oracle_receipt_id,
                elapsed_ms=outcome.get("elapsed_ms"),
                cost_coverage_status="UNKNOWN",
            )
            reproduction_receipt = build_reproduction_receipt(
                execution_receipt=delivery_execution_receipt,
                steps=[
                    row
                    for row in _list(outcome.get("steps"))
                    if isinstance(row, dict)
                ],
                oracle_receipt=oracle_verdict,
                source_refs=[
                    dict(row)
                    for row in _list(exp.get("source_refs"))
                    if isinstance(row, dict)
                ],
            )
            gate_receipt = build_customer_delivery_gate_receipt_v2(
                finding=(
                    outcome.get("finding")
                    if isinstance(outcome.get("finding"), dict)
                    else None
                ),
                execution_receipt=delivery_execution_receipt,
                contract_evidence_receipts=[
                    dict(row)
                    for row in _list(outcome.get("contract_evidence_receipts"))
                    if isinstance(row, dict)
                ],
                observer_receipts=[
                    dict(row)
                    for row in _list(outcome.get("observer_receipts"))
                    if isinstance(row, dict)
                ],
                oracle_receipt=oracle_verdict,
                reproduction_receipt=reproduction_receipt,
            )
            if (
                gate_receipt.get("status") == "DELIVERABLE"
                and isinstance(outcome.get("finding"), dict)
            ):
                # Seal the deliverable payload with finalized evidence, then
                # rebuild the gate so finding_payload_fingerprint stays consistent.
                finalized = finalize_finding_evidence_after_delivery_gate(
                    outcome["finding"],
                    gate_receipt=gate_receipt,
                    reproduction_receipt=reproduction_receipt,
                )
                outcome["finding"] = finalized
                gate_receipt = build_customer_delivery_gate_receipt_v2(
                    finding=finalized,
                    execution_receipt=delivery_execution_receipt,
                    contract_evidence_receipts=[
                        dict(row)
                        for row in _list(outcome.get("contract_evidence_receipts"))
                        if isinstance(row, dict)
                    ],
                    observer_receipts=[
                        dict(row)
                        for row in _list(outcome.get("observer_receipts"))
                        if isinstance(row, dict)
                    ],
                    oracle_receipt=oracle_verdict,
                    reproduction_receipt=reproduction_receipt,
                )
            executed += 1
            # execution_results must NEVER have DELIVERABLE status.
            # DELIVERABLE is a gate-stage decision, not execution-stage.
            # If execution_results has DELIVERABLE and gate_results has BLOCKED,
            # we get duplicate_terminal_receipt error in ledger validation.
            execution_results[oid] = {
                "status": "EXECUTED",
                "reason_code": "",
                "selected_obligation_id": oid,
                "executed_obligation_id": execution_oid,
                "experiment_id": eid,
                "execution_id": effective_execution_id,
                "receipt_id": _text(delivery_execution_receipt.get("receipt_id")),
                "output_fingerprint": _text(
                    delivery_execution_receipt.get("receipt_fingerprint")
                ),
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "cost_coverage_status": "UNKNOWN",
                "operational_receipt": operational_receipt,
                "delivery_execution_receipt": delivery_execution_receipt,
                "contract_evidence_receipts": list(
                    outcome.get("contract_evidence_receipts") or []
                ),
                "observer_receipts": list(outcome.get("observer_receipts") or []),
                "oracle_receipt": oracle_verdict,
                "reproduction_receipt": reproduction_receipt,
            }
            gate_results[oid] = gate_receipt
            outcome["delivery_execution_receipt"] = delivery_execution_receipt
            outcome["reproduction_receipt"] = reproduction_receipt
            outcome["delivery_gate_receipt"] = gate_receipt
            if isinstance(outcome.get("finding"), dict):
                finding = stamp_finding_delivery_gate_refs(
                    outcome["finding"],
                    gate_receipt=gate_receipt,
                )
                outcome["finding"] = finding
                execution_results[oid]["finding"] = dict(finding)
        cleanup_failures += outcome_cleanup_failures
        if status in ("EXECUTED", "DELIVERABLE") and isinstance(outcome.get("finding"), dict):
            finding = outcome["finding"]
            dedupe_key = _deliverable_dedupe_key(finding)
            if dedupe_key and dedupe_key in delivered_finding_ids:
                # Same operation-level property already delivered: keep the
                # traceable reference and fold this variant's actor into the
                # first finding's derived duplicate_variants field (exempt
                # from the payload fingerprint) so the delivered report still
                # proves the property for every tried actor pair — never
                # deliver a second copy of the same property, and never mutate
                # a sealed payload field (description) after gate build.
                duplicate_delivery_count += 1
                first = delivered_finding_ids[dedupe_key]
                variants = _list(first.get("duplicate_variants"))
                variant_note = _text(finding.get("title")) or dedupe_key
                if variant_note not in variants:
                    variants.append(variant_note)
                first["duplicate_variants"] = variants
                finding["duplicate_of"] = (
                    _text(first.get("finding_id") or first.get("id")) or dedupe_key
                )
                outcome["finding"] = finding
            else:
                if dedupe_key:
                    delivered_finding_ids[dedupe_key] = finding
                findings.append(finding)
    # ── Operator cancel closure ──
    # The marker itself is consumed once by the outer mainline entry
    # (experiment_batch_executor_base) after ALL parallel groups stopped;
    # consuming here would race other groups' boundary checks. The lease
    # directory removal still guarantees cleanup when no mainline entry ran.
    # ── Build validation gate summary ──
    from .small_scale_validation_gate import check_validation_gate
    _batch_result = {
        "schema_version": "qualibug.experiment-execution-batch.v1",
        "selected_count": len(selected),
        "executed_count": executed,
        "blocked_count": blocked,
        "harness_failure_count": harness,
        "cleanup_failures": cleanup_failures,
        "budget_exceeded_count": budget_exceeded,
        "budget_deferred": budget_deferred,
        "experiment_budget": _budget,
        "family_execution_quota": _family_quota,
        "duplicate_delivery_count": duplicate_delivery_count,
        "validation_phase": _phase,
        "operator_cancelled_count": operator_cancelled_count,
        "findings": findings,
        "results": results,
        "compile_results": compile_results,
        "execution_results": execution_results,
        "gate_results": gate_results,
        "every_experiment_has_receipt": all(
            isinstance(item.get("execution_receipt"), dict) for item in results
        ),
    }
    # Attach validation gate check
    _validation_gate = check_validation_gate(
        _batch_result,
        campaign_id=campaign_id,
        run_id=_text(run_contract.get("run_id")),
        phase=_phase,
    )
    _batch_result["validation_gate"] = _validation_gate
    if operator_cancel_request:
        _batch_result["operator_cancelled_receipt"] = {
            "schema": "qualibug.scan-cancel-request.v1",
            "requested_at_utc": _text(operator_cancel_request.get("requested_at_utc")),
            "requester": dict(operator_cancel_request.get("requester") or {}),
            "deferred_count": operator_cancelled_count,
        }

    # ── SPEC v1.2.2 §12: Attach funnel, attribution, and priority receipts ──
    # Failure is NOT silent — campaign_validation_status must reflect it.
    _funnel_failed = False
    _funnel_error = ""
    _attribution_failed = False
    _attribution_error = ""
    try:
        from .execution_coverage_funnel import build_execution_coverage_funnel
        from .blocker_attribution import attribute_all_blockers
        _all_exps = [experiments_by_obligation.get(oid, {}) for oid in selected_ids]
        _all_obls = [_dict(s) for s in selected]
        _all_exec_results = list(execution_results.values())
        _funnel = build_execution_coverage_funnel(
            obligations=_all_obls,
            experiments=_all_exps,
            execution_results=_all_exec_results,
            findings=findings,
            campaign_id=campaign_id,
        )
        _batch_result["execution_coverage_funnel"] = _funnel
    except Exception as exc:
        _funnel_failed = True
        _funnel_error = str(exc)
        logger.warning("batch funnel generation failed: %s", exc)
    try:
        from .blocker_attribution import attribute_all_blockers
        _all_exps2 = [experiments_by_obligation.get(oid, {}) for oid in selected_ids]
        _all_obls2 = [_dict(s) for s in selected]
        _all_exec_results2 = list(execution_results.values())
        _attribution = attribute_all_blockers(
            obligations=_all_obls2,
            experiments=_all_exps2,
            execution_results=_all_exec_results2,
            behavior_ir=behavior_ir,
        )
        _batch_result["blocker_attribution"] = _attribution
    except Exception as exc:
        _attribution_failed = True
        _attribution_error = str(exc)
        logger.warning("batch attribution failed: %s", exc)
    if _prioritization_receipt:
        _batch_result["prioritization_receipt"] = _prioritization_receipt

    # ── SPEC v1.2.2 §12.4: Campaign Validation Receipt ──
    # Mandatory: prioritization_receipt, execution_coverage_funnel, blocker_attribution.
    # Missing any → campaign_validation_status != PASSED.
    _campaign_validation_status = "PASSED"
    _campaign_validation_reasons: list[str] = []
    if _prioritization_failed:
        _campaign_validation_status = "FAILED"
        _campaign_validation_reasons.append(f"HARNESS_PRIORITIZATION_FAILED:{_prioritization_error[:100]}")
    if _funnel_failed:
        _campaign_validation_status = "FAILED"
        _campaign_validation_reasons.append(f"HARNESS_COVERAGE_FUNNEL_FAILED:{_funnel_error[:100]}")
    if _attribution_failed:
        _campaign_validation_status = "FAILED"
        _campaign_validation_reasons.append(f"HARNESS_BLOCKER_ATTRIBUTION_FAILED:{_attribution_error[:100]}")
    if not _batch_result.get("execution_coverage_funnel"):
        _campaign_validation_status = "FAILED"
        _campaign_validation_reasons.append("missing_execution_coverage_funnel")
    if not _batch_result.get("blocker_attribution"):
        _campaign_validation_status = "FAILED"
        _campaign_validation_reasons.append("missing_blocker_attribution")
    _batch_result["campaign_validation_receipt"] = {
        "schema_version": "qualibug.campaign-validation-receipt.v1",
        "campaign_validation_status": _campaign_validation_status,
        "reasons": _campaign_validation_reasons,
        "prioritization_present": bool(_batch_result.get("prioritization_receipt")),
        "funnel_present": bool(_batch_result.get("execution_coverage_funnel")),
        "attribution_present": bool(_batch_result.get("blocker_attribution")),
        "degraded_mode": _prioritization_failed and _campaign_validation_status == "FAILED",
        "customer_deliverable": _campaign_validation_status == "PASSED",
    }

    return _batch_result

