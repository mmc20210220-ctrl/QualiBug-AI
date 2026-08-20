"""Manual terminal receipt accounting for discovery execution.

Extracted from ``discovery_runtime_execution_support`` to keep that module
under the architecture extraction budget
(tests/test_architecture_extraction_contracts.py). The symbol is re-exported
from ``discovery_runtime_execution_support``, ``discovery_runtime_execution``
and ``discovery_runtime`` so identity assertions stay valid.
"""
from __future__ import annotations

from typing import Any

from ._obligation_attempt_ledger_single_occurrence_mechanics import (
    _base_obligation_id,
)


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


def _formal_accounting_id(
    obligation_id: str,
    accounting_by_id: dict[str, dict[str, Any]],
) -> str:
    """Map a compiler variant to its formal accounting owner when present."""
    oid = _text(obligation_id)
    base = _base_obligation_id(oid)
    return base if base in accounting_by_id else oid


def _experiment_faces(
    experiments: dict[str, Any],
    formal_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Return the direct experiment plus compiler-expanded faces of one base."""
    result: list[tuple[str, dict[str, Any]]] = []
    direct = _dict(experiments.get(formal_id))
    if direct:
        result.append((formal_id, direct))
    for raw_id, raw_experiment in experiments.items():
        oid = _text(raw_id)
        if not oid or oid == formal_id or _base_obligation_id(oid) != formal_id:
            continue
        experiment = _dict(raw_experiment)
        if experiment:
            result.append((oid, experiment))
    return result


def _compiled_experiment_face(
    experiments: dict[str, Any],
    formal_id: str,
) -> tuple[str, dict[str, Any]]:
    """Return the executable compiled face that can justify pending sealing."""
    faces = _experiment_faces(experiments, formal_id)
    for oid, experiment in faces:
        if _compile_status(experiment) == "COMPILED":
            return oid, experiment
    return faces[0] if faces else ("", {})


def _coverage_unit_for_identity(
    obligation_id: str,
    *,
    accounting_by_id: dict[str, dict[str, Any]],
    experiments: dict[str, Any],
) -> str:
    raw_id = _text(obligation_id)
    formal_id = _formal_accounting_id(raw_id, accounting_by_id)
    raw_experiment = _dict(experiments.get(raw_id))
    formal_row = _dict(accounting_by_id.get(formal_id))
    _, compiled_face = _compiled_experiment_face(experiments, formal_id)
    return _text(
        raw_experiment.get("coverage_unit_id")
        or formal_row.get("coverage_unit_id")
        or compiled_face.get("coverage_unit_id")
    )


def _terminal_pending_rows(
    *,
    selected_rows: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    obligation_plan: dict[str, Any],
    execution_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild the full formal pending set used only for terminal sealing.

    ``pending_next_round`` is a bounded public preview and may contain
    compiler-expanded ``base__v_*`` identities that do not exist as separate
    formal accounting rows. Terminal sealing projects every pending face back
    to its formal base. Exact retry and budget-deferral pools are consumed
    before any inference from ``pending_count`` so omitted identities cannot be
    replaced by a different compiled row during sealing.

    A real terminal receipt for any face closes the formal base for the current
    run. Coverage-unit plans retain one representative per unfinished unit.
    Reconstruction is transient only.
    """
    plan = _dict(obligation_plan)
    experiments = _dict(experiments_by_obligation)
    accounting_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in selected_rows
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }

    terminal_raw_ids = {
        _text(oid)
        for oid, raw in _dict(execution_results).items()
        if _text(oid)
        and _text(_dict(raw).get("status")).upper()
        not in {"", "DEFERRED", "UNRECEIPTED"}
    }
    terminal_formal_ids = {
        _formal_accounting_id(oid, accounting_by_id)
        for oid in terminal_raw_ids
    }

    raw_visible = [
        dict(row)
        for row in _list(plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    declared_pending = int(plan.get("pending_count") or len(raw_visible))
    missing_from_preview = max(0, declared_pending - len(raw_visible))

    visible: list[dict[str, Any]] = []
    visible_ids: set[str] = set()
    for raw in raw_visible:
        raw_id = _text(raw.get("obligation_id"))
        formal_id = _formal_accounting_id(raw_id, accounting_by_id)
        if not formal_id or formal_id in visible_ids or formal_id in terminal_formal_ids:
            continue
        row = dict(raw)
        row["obligation_id"] = formal_id
        if raw_id != formal_id:
            row["continuation_origin"] = "variant_preview_formal_projection"
        if not _text(row.get("coverage_unit_id")):
            unit_id = _coverage_unit_for_identity(
                raw_id,
                accounting_by_id=accounting_by_id,
                experiments=experiments,
            )
            if unit_id:
                row["coverage_unit_id"] = unit_id
        visible.append(row)
        visible_ids.add(formal_id)

    retry_rows = [
        dict(row)
        for row in _list(plan.get("blocked_retry_pool"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    budget_deferred_rows = [
        dict(row)
        for row in _list(plan.get("budget_deferred_pool"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    if missing_from_preview <= 0 and not retry_rows and not budget_deferred_rows:
        return visible

    result = list(visible)
    result_ids = set(visible_ids)

    # Retry is a stronger current-state statement than budget deferral. Support
    # keeps the pools mutually exclusive, but terminal sealing remains defensive
    # if an older persisted plan contains overlap.
    for retry in retry_rows:
        raw_id = _text(retry.get("obligation_id"))
        formal_id = _formal_accounting_id(raw_id, accounting_by_id)
        if not formal_id or formal_id in result_ids or formal_id in terminal_formal_ids:
            continue
        source = accounting_by_id.get(formal_id) or {}
        result.append({
            "obligation_id": formal_id,
            "risk_family": _text(source.get("risk_family")),
            "coverage_unit_id": _coverage_unit_for_identity(
                raw_id,
                accounting_by_id=accounting_by_id,
                experiments=experiments,
            ),
            "not_in_plan_reason": "CONTINUATION_RETRY_PENDING",
            "continuation_origin": (
                "blocked_retry_variant_formal_projection"
                if raw_id != formal_id
                else "blocked_retry_pool"
            ),
        })
        result_ids.add(formal_id)

    # This is exact capacity-deferral authority, not inferred pending work.
    # Selected identities are therefore allowed here even though generic
    # continuation reconstruction intentionally does not widen selected work.
    for deferred in budget_deferred_rows:
        raw_id = _text(deferred.get("obligation_id"))
        formal_id = _formal_accounting_id(raw_id, accounting_by_id)
        if not formal_id or formal_id in result_ids or formal_id in terminal_formal_ids:
            continue
        source = accounting_by_id.get(formal_id) or {}
        result.append({
            "obligation_id": formal_id,
            "risk_family": _text(source.get("risk_family")),
            "coverage_unit_id": _coverage_unit_for_identity(
                raw_id,
                accounting_by_id=accounting_by_id,
                experiments=experiments,
            ),
            "not_in_plan_reason": "BUDGET_DEFERRED",
            "continuation_origin": (
                "budget_deferred_variant_formal_projection"
                if raw_id != formal_id
                else "budget_deferred_pool"
            ),
        })
        result_ids.add(formal_id)

    if missing_from_preview <= 0:
        return result

    def eligible(row: dict[str, Any]) -> bool:
        formal_id = _text(row.get("obligation_id"))
        if (
            not formal_id
            or formal_id in result_ids
            or formal_id in terminal_formal_ids
            or row.get("pre_transport_executable") is False
        ):
            return False
        _, experiment = _compiled_experiment_face(experiments, formal_id)
        return _compile_status(experiment) == "COMPILED"

    planning_authority = _text(plan.get("plan_authority")).lower()
    restored: list[dict[str, Any]] = []
    if planning_authority == "coverage_unit":
        completed_units = {
            unit_id
            for raw_id in terminal_raw_ids
            if (
                unit_id := _coverage_unit_for_identity(
                    raw_id,
                    accounting_by_id=accounting_by_id,
                    experiments=experiments,
                )
            )
        }
        occupied_units = {
            unit_id
            for row in result
            if (
                unit_id := _text(row.get("coverage_unit_id"))
                or _coverage_unit_for_identity(
                    _text(row.get("obligation_id")),
                    accounting_by_id=accounting_by_id,
                    experiments=experiments,
                )
            )
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        unscoped: list[dict[str, Any]] = []
        for row in accounting_by_id.values():
            if not eligible(row):
                continue
            formal_id = _text(row.get("obligation_id"))
            unit_id = _coverage_unit_for_identity(
                formal_id,
                accounting_by_id=accounting_by_id,
                experiments=experiments,
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
            formal_id = _text(row.get("obligation_id"))
            restored.append({
                "obligation_id": formal_id,
                "risk_family": _text(row.get("risk_family")),
                "coverage_unit_id": _coverage_unit_for_identity(
                    formal_id,
                    accounting_by_id=accounting_by_id,
                    experiments=experiments,
                ),
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "terminal_reconstructed_obligation",
            })

    # ``pending_count`` counts execution identities (including variants), while
    # terminal accounting counts formal bases. Exact pools already consume that
    # outstanding count, so inference may fill only the remainder.
    fresh_restore_budget = max(0, declared_pending - len(result))
    for row in restored[:fresh_restore_budget]:
        formal_id = _text(row.get("obligation_id"))
        if formal_id and formal_id not in result_ids:
            result.append(row)
            result_ids.add(formal_id)
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
    accounting_by_id = {
        _text(item.get("obligation_id")): dict(item)
        for item in selected_rows
        if isinstance(item, dict) and _text(item.get("obligation_id"))
    }
    pending_rows = _terminal_pending_rows(
        selected_rows=selected_rows,
        experiments_by_obligation=experiments,
        obligation_plan=obligation_plan,
        execution_results=execution_results,
    )
    scheduled_ids = {
        _formal_accounting_id(_text(row.get("obligation_id")), accounting_by_id)
        for row in _list(obligation_plan.get("selected"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    pending_ids = {
        _text(row.get("obligation_id"))
        for row in pending_rows
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
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
        _variant_result = None
        for _vid, _vresult in compile_results.items():
            if _base_obligation_id(_text(_vid)) == obligation_id and _text(_vid) != obligation_id:
                _variant_result = _vresult
                break
        if _variant_result is not None and obligation_id not in compile_results:
            compile_results[obligation_id] = dict(_variant_result)
            if obligation_id in execution_results:
                continue
        existing_compile = _dict(compile_results.get(obligation_id))
        existing_compile_status = _text(existing_compile.get("status")).upper()
        if existing_compile and existing_compile_status != "COMPILED":
            continue
        _, experiment = _compiled_experiment_face(experiments, obligation_id)
        compile_receipt = _dict(experiment.get("compile_receipt"))
        compile_status = existing_compile_status or _text(compile_receipt.get("status")).upper()
        experiment_id = _text(
            existing_compile.get("experiment_id")
            or experiment.get("experiment_id")
            or compile_receipt.get("experiment_id")
        )
        if not existing_compile and compile_status in {"BLOCKED", "HARNESS_FAILED"}:
            compile_results[obligation_id] = {
                "status": compile_status,
                "reason_code": _text(compile_receipt.get("reason_code")) or "BLOCKED_COMPILE",
                "detail": _text(compile_receipt.get("detail") or compile_receipt.get("reason_detail")),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif not existing_compile and compile_status == "DEFERRED" and _text(compile_receipt.get("reason_code")):
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": _text(compile_receipt.get("reason_code")),
                "detail": _text(compile_receipt.get("detail") or compile_receipt.get("reason_detail")),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif not existing_compile and _text(row.get("selection_status")).upper() == "COMPILE_BLOCKED":
            compile_results[obligation_id] = {
                "status": "HARNESS_FAILED",
                "reason_code": "COMPILE_RECEIPT_MISSING" if not compile_status else "BLOCKED_COMPILE",
                "detail": "compile_receipt_missing" if not compile_status else "compile_deferred_reason_missing",
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in pending_ids:
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
                    "not_in_plan_reason": pending_reasons.get(obligation_id, "BUDGET_EXHAUSTED"),
                    "detail": "compiled_obligation_deferred_by_execution_budget",
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
            else:
                compile_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_BUDGET_REACHED",
                    "not_in_plan_reason": pending_reasons.get(obligation_id, "BUDGET_EXHAUSTED"),
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
            continue
        else:
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_NOT_IN_PLAN",
                "not_in_plan_reason": pending_reasons.get(obligation_id, "NOT_IN_PLAN_REASON_UNATTRIBUTED"),
                "detail": _text(compile_receipt.get("detail") or ""),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
