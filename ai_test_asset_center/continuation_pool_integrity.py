"""Fail-closed integrity guard for exact continuation resume pools.

A persisted ``*_pool_count`` is an assertion about the complete identity set.
If it disagrees with the unique IDs actually present in the pool, some resume
authority has already been truncated or corrupted. Continuing from the shorter
list would silently lose Recall, so execution stops explicitly instead of
inventing identities from counts.
"""
from __future__ import annotations

from typing import Any


_POOL_SPECS = (
    ("fresh_pending_pool", "fresh_pending_pool_count"),
    ("budget_deferred_pool", "budget_deferred_pool_count"),
    ("blocked_retry_pool", "blocked_retry_pool_count"),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _unique_ids(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        _text(row.get("obligation_id"))
        for row in rows
        if _text(row.get("obligation_id"))
    ))


def validate_continuation_pool_integrity(
    obligation_plan: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return normalized plan and whether exact resume authority is usable.

    Missing count fields are legacy metadata gaps and can be reconstructed from
    a structurally valid list itself. A present count mismatch, a non-list pool,
    or a malformed pool row is not recoverable without guessing identity and
    therefore fails closed.
    """
    plan = dict(obligation_plan) if isinstance(obligation_plan, dict) else {}
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for pool_key, count_key in _POOL_SPECS:
        field_present = pool_key in plan
        count_present = count_key in plan
        if not field_present and not count_present:
            continue

        raw_pool = plan.get(pool_key)
        structural_failure = ""
        invalid_row_count = 0
        if field_present and not isinstance(raw_pool, list):
            structural_failure = "POOL_TYPE_INVALID"
        elif isinstance(raw_pool, list):
            invalid_row_count = sum(
                1
                for row in raw_pool
                if not isinstance(row, dict)
                or not _text(row.get("obligation_id"))
            )
            if invalid_row_count:
                structural_failure = "POOL_ROW_INVALID"

        rows = _rows(raw_pool)
        unique_ids = _unique_ids(rows)
        actual_count = len(unique_ids)
        declared_raw = plan.get(count_key)
        try:
            declared_count = int(declared_raw) if count_present else actual_count
        except (TypeError, ValueError):
            declared_count = -1

        status = "PASS"
        failure_kind = ""
        if structural_failure:
            status = "FAIL"
            failure_kind = structural_failure
            failures.append({
                "pool": pool_key,
                "count_field": count_key,
                "failure_kind": structural_failure,
                "declared_count": declared_count,
                "actual_unique_count": actual_count,
                "invalid_row_count": invalid_row_count,
                "actual_type": type(raw_pool).__name__,
            })
        elif count_present and declared_count != actual_count:
            status = "FAIL"
            failure_kind = "COUNT_MISMATCH"
            failures.append({
                "pool": pool_key,
                "count_field": count_key,
                "failure_kind": "COUNT_MISMATCH",
                "declared_count": declared_count,
                "actual_unique_count": actual_count,
            })
        elif not count_present:
            # A complete, structurally valid list without a count is an older
            # metadata shape, not evidence of truncation. Normalize it so every
            # later persistence point has a checkable cardinality assertion.
            plan[count_key] = actual_count
            status = "NORMALIZED_LEGACY_COUNT"

        checks.append({
            "pool": pool_key,
            "count_field": count_key,
            "status": status,
            "failure_kind": failure_kind,
            "declared_count": declared_count,
            "actual_unique_count": actual_count,
            "invalid_row_count": invalid_row_count,
        })

    receipt = {
        "schema_version": "qualibug.continuation-pool-integrity.v1",
        "status": "FAIL" if failures else "PASS",
        "checks": checks,
        "failure_count": len(failures),
        "failures": failures,
        "authority": "exact_resume_pool_cardinality_and_structure",
    }
    plan["continuation_pool_integrity_receipt"] = receipt
    if failures:
        failed_pools = ",".join(dict.fromkeys(row["pool"] for row in failures))
        has_structural_failure = any(
            row.get("failure_kind") in {"POOL_TYPE_INVALID", "POOL_ROW_INVALID"}
            for row in failures
        )
        reason = (
            f"CONTINUATION_AUTHORITY_POOL_INVALID:{failed_pools}"
            if has_structural_failure
            else f"CONTINUATION_AUTHORITY_COUNT_MISMATCH:{failed_pools}"
        )
        plan["early_stop_reason"] = reason
        plan["stop_condition"] = reason
        plan["continuation_authority_corrupt"] = True
        return plan, False
    plan.pop("continuation_authority_corrupt", None)
    return plan, True


__all__ = ["validate_continuation_pool_integrity"]
