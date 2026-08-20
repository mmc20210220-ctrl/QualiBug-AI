"""Adaptive Behavior-IR expansion with low-authority runtime read surfaces.

The complete historical expansion implementation is preserved in
``adaptive_behavior_ir_expansion_base``. This facade changes only the operation
merge authority: during one expansion call, receipt-backed Runtime Fact
Candidates may contribute GET/HEAD implementation identities. They cannot
create business facts, relations, expectations, or write operations.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from . import adaptive_behavior_ir_expansion_base as _base
from .runtime_implementation_candidate_projection import (
    merge_candidate_read_operations,
)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_original_expand = _base.expand_behavior_ir_from_runtime_observations
_original_merge_runtime_operations = _base.merge_runtime_discovered_operations
_candidate_ledger_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "qualibug_runtime_fact_candidate_ledger", default=None
)
_projection_receipt_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "qualibug_runtime_implementation_projection_receipt", default=None
)


def _merge_with_runtime_candidate_reads(
    documented_operations: list[dict[str, Any]],
    observation_receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = _original_merge_runtime_operations(
        documented_operations,
        observation_receipts,
    )
    enriched, receipt = merge_candidate_read_operations(
        merged,
        _candidate_ledger_context.get(),
    )
    _projection_receipt_context.set(receipt)
    return enriched


# Base expansion resolves this symbol from its defining module. ContextVar keeps
# candidate authority call-local under concurrent scans instead of installing a
# mutable process-global candidate ledger.
_base.merge_runtime_discovered_operations = _merge_with_runtime_candidate_reads


def expand_behavior_ir_from_runtime_observations(*args: Any, **kwargs: Any) -> dict[str, Any]:
    knowledge_asset = kwargs.get("knowledge_asset")
    if knowledge_asset is None and len(args) >= 4:
        knowledge_asset = args[3]
    asset = knowledge_asset if isinstance(knowledge_asset, dict) else {}
    ledger = asset.get("runtime_fact_candidate_ledger")
    ledger_token = _candidate_ledger_context.set(
        ledger if isinstance(ledger, dict) else None
    )
    receipt_token = _projection_receipt_context.set(None)
    try:
        result = _original_expand(*args, **kwargs)
        output = dict(result) if isinstance(result, dict) else {}
        receipt = _projection_receipt_context.get() or {
            "schema_version": "qualibug.runtime-implementation-candidate-projection.v1",
            "status": "NOT_APPLIED",
            "candidate_read_operation_count": 0,
            "added_operation_count": 0,
            "added_operations": [],
            "authority": "receipt_backed_runtime_candidate_read_surface_only",
            "write_surface_promoted": False,
            "business_fact_promoted": False,
        }
        output["runtime_implementation_candidate_projection_receipt"] = receipt
        round_receipt = dict(output.get("round_receipt") or {})
        round_receipt["runtime_candidate_read_operations_added"] = int(
            receipt.get("added_operation_count") or 0
        )
        output["round_receipt"] = round_receipt
        return output
    finally:
        _projection_receipt_context.reset(receipt_token)
        _candidate_ledger_context.reset(ledger_token)


__all__ = sorted(
    {
        *[name for name in dir(_base) if not name.startswith("__")],
        "expand_behavior_ir_from_runtime_observations",
    }
)
