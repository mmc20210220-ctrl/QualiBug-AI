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
        )

    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    blocked = 0
    executed = 0
    harness = 0
    cleanup_failures = 0
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
    _budget = max(1, min(_budget, 200))  # hard cap at 200
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
        )
        _ordered_ids = _list(_prioritization_receipt.get("ordered_experiment_ids"))
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
    for index, item in enumerate(selected):
        row = _dict(item)
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
        eid = _text(outcome.get("experiment_id")) or eid
        outcome.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "selected_obligation_id": oid,
            "obligation_id": execution_oid,
            "experiment_id": eid,
            "execution_id": execution_id,
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
            "execution_id": execution_id,
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
                "execution_id": execution_id,
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
        # Anything CLAIMING to be a receipt is still validated strictly. Only a verdict
        # that carries neither schema_version nor receipt_id, and says why it was blocked,
        # is carried through as the block it is.
        _is_gate_block = bool(oracle_verdict.get("oracle_blocked_by_completeness_gate")) and not (
            _text(oracle_verdict.get("schema_version")) or _text(oracle_verdict.get("receipt_id"))
        )
        if oracle_verdict and not _is_gate_block:
            validated_oracle = validate_contract_oracle_receipt(oracle_verdict)
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
                execution_id=execution_id,
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
                "execution_id": execution_id,
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
            findings.append(outcome["finding"])
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
        "validation_phase": _phase,
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

