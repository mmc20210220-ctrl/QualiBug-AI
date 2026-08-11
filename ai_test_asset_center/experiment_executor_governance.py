"""Outermost execution-governance facade.

The current account/graph/comparison/cleanup authorities live in
``_experiment_executor_governance_authority_mechanics``. This boundary restores
one independent rule that was lost during concurrent facade consolidation:
batch ``_pre_resolved_bindings`` are discovery/performance hints only.

Those raw target->value entries carry no materialization receipt, actor identity,
resolver operation, collection context, or execution lineage. They are removed
before the governed core sees the experiment. Only target names survive in a
non-authoritative diagnostic; formal transport values must be re-established by
the current experiment's resolver/fixture/materialization chain.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_executor_governance_authority_mechanics as _authority

for _name in dir(_authority):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_authority, _name)

_original_execute_one_experiment = _authority.execute_one_experiment


def __getattr__(name: str) -> Any:
    return getattr(_authority, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_authority)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _formal_experiment_without_raw_prebindings(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    exp = dict(_dict(experiment))
    raw = dict(_dict(exp.pop("_pre_resolved_bindings", {})))
    targets = sorted(
        _text(target)
        for target, value in raw.items()
        if _text(target) and value not in (None, "", [], {})
    )
    diagnostic = {
        "schema_version": "qualibug.pre-resolved-binding-diagnostic.v1",
        "present": bool(raw),
        "target_count": len(targets),
        "targets": targets,
        "formal_binding_authority": False,
        "values_forwarded_to_transport": False,
        "reason": (
            "raw_batch_prebinding_has_no_materialization_receipt"
            if raw
            else "not_present"
        ),
    }
    if raw:
        exp["pre_resolution_diagnostic"] = dict(diagnostic)
    return exp, diagnostic


def execute_one_experiment(
    experiment: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    governed, diagnostic = _formal_experiment_without_raw_prebindings(experiment)
    result = _original_execute_one_experiment(governed, **kwargs)
    output = dict(_dict(result))
    if diagnostic["present"]:
        output["pre_resolution_diagnostic"] = diagnostic
    return output


__all__ = sorted(
    {
        *[
            name
            for name in dir(_authority)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "execute_one_experiment",
        "_formal_experiment_without_raw_prebindings",
    }
)
