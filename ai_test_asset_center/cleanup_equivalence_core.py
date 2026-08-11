"""Cleanup equivalence facade with one canonical receipt-success authority.

The historical equivalence engine lives in
``_cleanup_equivalence_core_mechanics``. This facade closes a schema-compatibility
seam only: some formally sealed cleanup execution receipts describe successful
transport with the canonical tuple

``status in {ACCEPTED, COMPLETED, CLEANED} + attempted=true +
transport_reached=true + 2xx``

but omit the legacy convenience boolean ``succeeded``. The old evaluator read
only that boolean and therefore emitted the contradictory diagnosis
``cleanup_transport_failed:status=200``. Downstream finalization then had to
special-case the contradiction.

No HTTP status is trusted by itself. Explicit ``succeeded=False`` is never
overridden, any missing authority leg remains fail-closed, and the original
sealed receipt is never mutated; the alias exists only in a local evaluator
view.
"""
from __future__ import annotations

from typing import Any

from . import _cleanup_equivalence_core_mechanics as _core
from ._cleanup_equivalence_core_mechanics import *  # noqa: F401,F403

_original_evaluate_cleanup_equivalence = _core.evaluate_cleanup_equivalence
_SUCCESS_STATUSES = frozenset({"ACCEPTED", "COMPLETED", "CLEANED"})


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_code(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_cleanup_execution_success_authority(
    receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bridge canonical formal success into the legacy ``succeeded`` alias.

    The returned dict is a local compatibility view. ``receipt`` itself is
    never changed.
    """

    row = dict(_dict(receipt))
    if not row:
        return row
    if _text(row.get("schema_version")) != "qualibug.cleanup-execution-receipt.v1":
        return row
    if "succeeded" in row:
        return row

    status = _text(row.get("status")).upper()
    attempted = row.get("attempted") is True
    transport_reached = row.get("transport_reached") is True
    status_code = _status_code(row.get("status_code"))
    if (
        status in _SUCCESS_STATUSES
        and attempted
        and transport_reached
        and 200 <= status_code < 300
    ):
        row["succeeded"] = True
    return row


def evaluate_cleanup_equivalence(
    *,
    proof: dict[str, Any],
    before_observation: dict[str, Any],
    after_write_observation: dict[str, Any],
    after_cleanup_observation: dict[str, Any],
    runtime_bindings: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    governed_receipt = normalize_cleanup_execution_success_authority(
        cleanup_execution_receipt
    )
    return _original_evaluate_cleanup_equivalence(
        proof=proof,
        before_observation=before_observation,
        after_write_observation=after_write_observation,
        after_cleanup_observation=after_cleanup_observation,
        runtime_bindings=runtime_bindings,
        cleanup_execution_receipt=governed_receipt,
    )


# cleanup_equivalence.py imports this module object and dispatches through its
# evaluator; install the same governed callable into the mechanics namespace so
# all internal calls share one authority.
_core.evaluate_cleanup_equivalence = evaluate_cleanup_equivalence

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "normalize_cleanup_execution_success_authority",
        "evaluate_cleanup_equivalence",
    }
)
