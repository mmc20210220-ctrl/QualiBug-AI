"""Manual terminal receipt accounting for discovery execution.

Extracted from ``discovery_runtime_execution_support`` to keep that module
under the architecture extraction budget
(tests/test_architecture_extraction_contracts.py). The symbol is re-exported
from ``discovery_runtime_execution_support``, ``discovery_runtime_execution``
and ``discovery_runtime`` so identity assertions stay valid.
"""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compile_status(experiment: Any) -> str:
    row = _dict(experiment)
    status = _text(_dict(row.get("compile_receipt")).get("status")).upper()
    if not status:
        status = _text(row.get("compile_status")).upper()
    return status


def _terminal_pending_rows(
    *,
    selected_rows: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    obligation_plan: dict[str, Any],
    execution_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild the full pending identity set used only for terminal sealing.

    ``pending_next_round`` is a bounded public preview.  Once continuation has
    stopped with ``pending_count`` greater than that preview, sealing from the
    preview alone turns the omitted tail into OBLIGATION_NOT_IN_PLAN.  Rebuild
    only the transient terminal view from the full accounting scope; do not add
    another unbounded field to the customer-visible obligation plan.

    Coverage-unit plans retain one representative per unfinished unit.  An
    already terminally executed member closes that unit for sealing so backup
    members are not falsely projected as pending work.
    """
    plan = _dict(obligation_plan)
    experiments = _dict(experiments_by_obligation)
    visible = [
        dict(row)
        for row in _list(plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    visible_ids = {
        _text(row.get("obligation_id"))
        for row in visible
        if _text(row.get("obligation_id"))
    }
    declared_pending = int(plan.get("pending_count") or len(visible))
    missing_from_preview = max(0, declared_pending - len(visible))

    retry_rows = [
        dict(row)
        for row in _list(plan.get("blocked_retry_pool"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    if missing_from_preview <= 0 and not retry_rows:
        return visible

    accounting_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in selected_rows
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    terminal_ids = {
        _text(oid)
        for oid, raw in _dict(execution_results).items()
        if _text(oid)
        and _text(_dict(raw).get("status")).upper()
        not in {"", "DEFERRED", "UNRECEIPTED"}
    }

    result = list(visible)
    result_ids = set(visible_ids)

    # Persisted retry authority may contain identities absent from the bounded
    # fresh preview.  If this run already produced a terminal result for one,
    # manual sealing will skip it anyway; otherwise keep it explicitly pending.
    for retry in retry_rows:
        oid = _text(retry.get("obligation_id"))
        if not oid or oid in result_ids or oid in terminal_ids:
            continue
        source = accounting_by_id.get(oid) or {}
        result.append({
            "obligation_id": oid,
            "risk_family": _text(source.get("risk_family")),
            "coverage_unit_id": _text(
                source.get("coverage_unit_id")
                or _dict(experiments.get(oid)).get("coverage_unit_id")
            ),
            "not_in_plan_reason": "CONTINUATION_RETRY_PENDING",
            "continuation_origin": "blocked_retry_pool",
        })
        result_ids.add(oid)

    if missing_from_preview <= 0:
        return result

    def eligible(row: dict[str, Any]) -> bool:
        oid = _text(row.get("obligation_id"))
        if not oid or oid in result_ids or oid in terminal_ids:
            return False
        experiment = _dict(experiments.get(oid))
        return (
            _compile_status(experiment) == "COMPILED"
            and row.get("pre_transport_executable") is not False
        )

    planning_authority = _text(plan.get("plan_authority")).lower()
    restored: list[dict[str, Any]] = []
    if planning_authority == "coverage_unit":
        completed_units = {
            _text(
                _dict(accounting_by_id.get(oid)).get("coverage_unit_id")
                or _dict(experiments.get(oid)).get("coverage_unit_id")
            )
            for oid in terminal_ids
            if _text(
                _dict(accounting_by_id.get(oid)).get("coverage_unit_id")
                or _dict(experiments.get(oid)).get("coverage_unit_id")
            )
        }
        occupied_units = {
            _text(
                row.get("coverage_unit_id")
                or _dict(accounting_by_id.get(_text(row.get("obligation_id")))).get(
                    "coverage_unit_id"
                )
                or _dict(experiments.get(_text(row.get("obligation_id")))).get(
                    "coverage_unit_id"
                )
            )
            for row in result
            if _text(
                row.get("coverage_unit_id")
                or _dict(accounting_by_id.get(_text(row.get("obligation_id")))).get(
                    "coverage_unit_id"
                )
                or _dict(experiments.get(_text(row.get("obligation_id")))).get(
                    "coverage_unit_id"
                )
            )
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        unscoped: list[dict[str, Any]] = []
        for row in accounting_by_id.values():
            if not eligible(row):
                continue
            oid = _text(row.get("obligation_id"))
            unit_id = _text(
                row.get("coverage_unit_id")
                or _dict(experiments.get(oid)).get("coverage_unit_id")
            )
            if not unit_id:
                unscoped.append(row)
                continue
            if unit_id in completed_units or unit_id in occupied_units:
                continue
            grouped.setdefault(unit_id, []).append(row)

        for unit_id in sorted(grouped):
            candidates = sorted(
                grouped[unit_id],
                key=lambda row: (
                    -float(row.get("confidence") or 0.0),
                    _text(row.get("obligation_id")),
                ),
            )
            if not candidates:
                continue
            row = candidates[0]
            restored.append({
                "obligation_id": _text(row.get("obligation_id")),
                "risk_family": _text(row.get("risk_family")),
                "coverage_unit_id": unit_id,
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "terminal_reconstructed_coverage_unit",
            })
        for row in sorted(unscoped, key=lambda item: _text(item.get("obligation_id"))):
            restored.append({
                "obligation_id": _text(row.get("obligation_id")),
                "risk_family": _text(row.get("risk_family")),
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "terminal_reconstructed_unscoped_obligation",
            })
    else:
        for row in sorted(
            (row for row in accounting_by_id.values() if eligible(row)),
            key=lambda item: _text(item.get("obligation_id")),
        ):
            oid = _text(row.get("obligation_id"))
            restored.append({
                "obligation_id": oid,
                "risk_family": _text(row.get("risk_family")),
                "coverage_unit_id": _text(
                    row.get("coverage_unit_id")
                    or _dict(experiments.get(oid)).get("coverage_unit_id")
                ),
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "terminal_reconstructed_obligation",
            })

    # ``pending_count`` is the execution authority's exact outstanding count;
    # retry rows already recovered above consume that same count.  Reconstruct
    # only the remaining fresh slots so mixed fresh+retry queues cannot over-seal.
    fresh_restore_budget = max(0, declared_pending - len(result))
    for row in restored[:fresh_restore_budget]:
        oid = _text(row.get("obligation_id"))
        if oid and oid not in result_ids:
            result.append(row)
            result_ids.add(oid)
    return result


def _manual_terminal_receipts(
    *,
    selected_rows: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    obligation_plan: dict[str, Any],
    runtime_contract: dict[str, Any],
    compile_results: dict[str, dict[str, Any]],
    execution_results: dict[str, dict[str, Any]],
) -> None:
    experiments = _dict(experiments_by_obligation)
    obligation_plan = _dict(obligation_plan)
    pending_rows = _terminal_pending_rows(
        selected_rows=selected_rows,
        experiments_by_obligation=experiments,
        obligation_plan=obligation_plan,
        execution_results=execution_results,
    )
    scheduled_ids = {
        _text(row.get("obligation_id"))
        for row in _list(obligation_plan.get("selected"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    pending_ids = {
        _text(row.get("obligation_id"))
        for row in pending_rows
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    # P0-5: map pending obligation_id -> specific not-in-plan reason
    pending_reasons: dict[str, str] = {
        _text(row.get("obligation_id")): _text(row.get("not_in_plan_reason")) or "BUDGET_EXHAUSTED"
        for row in pending_rows
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    runtime_approved = (
        _text(runtime_contract.get("status")) == "approved"
        and bool(_text(runtime_contract.get("approved_base_url")))
    )
    for row in selected_rows:
        obligation_id = _text(row.get("obligation_id"))
        if not obligation_id or obligation_id in execution_results:
            continue
        # Check variant obligation_ids and map them to the original
        _variant_result = None
        for _vid, _vresult in compile_results.items():
            if _vid.startswith(obligation_id + "__v_"):
                _variant_result = _vresult
                break
        if _variant_result is not None and obligation_id not in compile_results:
            compile_results[obligation_id] = dict(_variant_result)
            if obligation_id in execution_results:
                continue
        existing_compile = _dict(compile_results.get(obligation_id))
        existing_compile_status = _text(existing_compile.get("status")).upper()
        # COMPILED without an execution receipt still needs a terminal — especially
        # budget-deferred rows that remain in pending_next_round. Non-COMPILED
        # compile terminals already close the attempt.
        if existing_compile and existing_compile_status != "COMPILED":
            continue
        experiment = _dict(experiments.get(obligation_id))
        compile_receipt = _dict(experiment.get("compile_receipt"))
        compile_status = existing_compile_status or _text(
            compile_receipt.get("status")
        ).upper()
        experiment_id = _text(
            existing_compile.get("experiment_id")
            or experiment.get("experiment_id")
            or compile_receipt.get("experiment_id")
        )
        if not existing_compile and compile_status in {"BLOCKED", "HARNESS_FAILED"}:
            compile_results[obligation_id] = {
                "status": compile_status,
                "reason_code": _text(compile_receipt.get("reason_code"))
                or "BLOCKED_COMPILE",
                "detail": _text(
                    compile_receipt.get("detail")
                    or compile_receipt.get("reason_detail")
                ),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif (
            not existing_compile
            and compile_status == "DEFERRED"
            and _text(compile_receipt.get("reason_code"))
        ):
            # The compiler already said WHY it deferred -- e.g.
            # MISSING_PRIMARY_OPERATION for an obligation with no operation to
            # call. Falling through to the branches below discarded that reason and
            # relabelled it OBLIGATION_NOT_IN_PLAN / BUDGET_EXHAUSTED, which reads
            # as "we ran out of budget" when the budget was never the constraint.
            # A wrong reason code is worse than a missing one: it sends the next
            # reader looking for capacity they already have.
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": _text(compile_receipt.get("reason_code")),
                "detail": _text(
                    compile_receipt.get("detail")
                    or compile_receipt.get("reason_detail")
                ),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif (
            not existing_compile
            and _text(row.get("selection_status")).upper() == "COMPILE_BLOCKED"
        ):
            compile_results[obligation_id] = {
                "status": "HARNESS_FAILED",
                "reason_code": (
                    "COMPILE_RECEIPT_MISSING"
                    if not compile_status
                    else "BLOCKED_COMPILE"
                ),
                "detail": (
                    "compile_receipt_missing"
                    if not compile_status
                    else "compile_deferred_reason_missing"
                ),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in pending_ids:
            # Preserve a real COMPILED compile receipt; defer at execution so
            # budget exhaustion is not misread as a compile failure.
            if compile_status == "COMPILED":
                if obligation_id not in compile_results:
                    compile_results[obligation_id] = {
                        "status": "COMPILED",
                        "experiment_id": experiment_id,
                        "cost_coverage_status": "UNKNOWN",
                    }
                execution_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_BUDGET_REACHED",
                    "not_in_plan_reason": pending_reasons.get(
                        obligation_id, "BUDGET_EXHAUSTED"
                    ),
                    "detail": "compiled_obligation_deferred_by_execution_budget",
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
            else:
                compile_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_BUDGET_REACHED",
                    "not_in_plan_reason": pending_reasons.get(
                        obligation_id, "BUDGET_EXHAUSTED"
                    ),
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
        elif obligation_id in scheduled_ids and not runtime_approved:
            if obligation_id not in compile_results:
                compile_results[obligation_id] = {
                    "status": "COMPILED" if compile_status == "COMPILED" else compile_status or "COMPILED",
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
            execution_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_RUNTIME_TARGET",
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif existing_compile_status == "COMPILED":
            # Already compiled in a batch/filler, but never executed and not
            # pending — leave the execution gap for
            # ``_ensure_accounting_terminal_receipts`` so status/reason stay
            # aligned (BLOCKED / BLOCKED_EXECUTION), not HARNESS_FAILED.
            continue
        else:
            # Fallback: obligation compiled but not selected/blocked/deferred.
            # Treat as DEFERRED rather than failing the entire run. Do NOT default
            # not_in_plan_reason to BUDGET_EXHAUSTED -- this branch is reached
            # precisely when the obligation is NOT in pending_ids, so the budget is
            # the one explanation that cannot apply. Say it is unattributed instead.
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_NOT_IN_PLAN",
                "not_in_plan_reason": pending_reasons.get(
                    obligation_id, "NOT_IN_PLAN_REASON_UNATTRIBUTED"
                ),
                "detail": _text(compile_receipt.get("detail") or ""),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
