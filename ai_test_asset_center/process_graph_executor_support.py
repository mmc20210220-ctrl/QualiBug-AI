"""Public graph-executor support facade.

The original bookkeeping implementation remains in
``process_graph_executor_support_core``. This facade preserves strict scoped
receipt references when a sub-ledger is copied into a graph master ledger. The
core already copies rows and timeline events; only receipt scope enrichment is
added here.
"""
from __future__ import annotations

from typing import Any

from . import process_graph_executor_support_core as _core


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


_SCOPED_RECEIPT_FIELDS = (
    ("scoped_observation_receipt_ids", "observer_receipt_ids"),
    ("scoped_oracle_receipt_ids", "oracle_receipt_ids"),
    ("scoped_cleanup_receipt_ids", "cleanup_receipt_ids"),
)


def copy_subledger_rows(
    master: Any,
    subledger: Any,
    *,
    graph_context_by_step: dict[str, dict[str, Any]] | None = None,
) -> set[str]:
    """Copy core rows/timeline, then preserve every strict scoped receipt ref."""
    copied = _core.copy_subledger_rows(
        master,
        subledger,
        graph_context_by_step=graph_context_by_step,
    )
    if subledger is None or not hasattr(subledger, "all_rows"):
        return copied
    if not hasattr(master, "append_scoped_receipt_ref"):
        return copied
    for source in subledger.all_rows():
        if not isinstance(source, dict):
            continue
        step_id = str(source.get("step_id") or "").strip()
        if not step_id:
            continue
        for source_field, target_field in _SCOPED_RECEIPT_FIELDS:
            for raw_receipt_id in list(source.get(source_field) or []):
                receipt_id = str(raw_receipt_id or "").strip()
                if not receipt_id:
                    continue
                master.append_scoped_receipt_ref(
                    step_id=step_id,
                    field=target_field,
                    receipt_id=receipt_id,
                    receipt_step_id=step_id,
                )
    return copied


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
