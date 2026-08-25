"""Public obligation compiler facade with Behavior IR v2 additive compatibility."""
from __future__ import annotations
from typing import Any

from . import obligation_compiler_mainline_base as _base
from .schema_validation_seed_authority import (
    append_operation_schema_validation_seeds,
)
from .test_obligation import consolidate_unbound_invariant_obligations

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


def compile_obligations_from_behavior_ir(
    behavior_ir: dict[str, Any], *, root: str = "", project: str = "", target_service_name: str = ""
) -> dict[str, Any]:
    if str(behavior_ir.get("schema_version") or "").strip() == "qualibug.behavior-ir.v2":
        behavior_ir.setdefault("ui_specs", [])
    compiled = _base.compile_obligations_from_behavior_ir(
        behavior_ir, root=root, project=project, target_service_name=target_service_name
    )
    seeded = append_operation_schema_validation_seeds(
        compiled,
        behavior_ir=behavior_ir,
        compiler_base=_base,
    )
    rows, consolidation_receipt = consolidate_unbound_invariant_obligations(
        list(seeded.get("obligations") or [])
    )
    result = dict(seeded)
    result["obligations"] = rows
    result["obligation_consolidation_receipt"] = consolidation_receipt
    # Breadth-loss visibility (原则14) is attached downstream in
    # build_discovery_plan, where the fully-enriched obligations list is the
    # authoritative set — attaching here would read the obligations before the
    # schema-seed pass rebuilds them.
    return result


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
