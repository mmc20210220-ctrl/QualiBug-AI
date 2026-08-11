"""Execution-governance facade that rejects raw pre-resolved binding authority.

The established account/graph/comparison/cleanup governance lives in
``_experiment_executor_governance_mechanics``.  Batch binding pre-resolution is
useful as a discovery/performance hint, but its historical shape is only a
``target -> value`` mapping.  It carries no sealed materialization receipt,
actor identity, resolver operation, collection context, or execution lineage.

Formal execution therefore removes ``_pre_resolved_bindings`` before entering
the governed core.  The targets are retained as diagnostics only.  Every value
used for transport must be re-established by the experiment's own resolver,
fixture, or runtime materialization receipt.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_executor_governance_mechanics as _gov

for _name in dir(_gov):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_gov, _name)

_original_execute_one_experiment = _gov.execute_one_experiment


def __getattr__(name: str) -> Any:
    return getattr(_gov, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_gov)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _formal_experiment_without_raw_prebindings(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate an unreceipted batch cache from formal execution authority."""

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


def _sync_public_hooks_into_mechanics() -> None:
    """Preserve the governance module's established monkeypatch surface."""

    names = set(getattr(_gov, "_HOOK_NAMES", ()))
    names.update(
        {
            "preflight_experiment_executable",
            "load_actor_tokens",
            "_resolve_token",
            "build_proof_fingerprint",
            "validate_cleanup_plan",
        }
    )
    for name in names:
        value = globals().get(name)
        if value is not None and hasattr(_gov, name):
            setattr(_gov, name, value)


def execute_one_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    root: Any,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    execution_id: str,
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute with current-experiment binding evidence only."""

    _sync_public_hooks_into_mechanics()
    governed_exp, diagnostic = _formal_experiment_without_raw_prebindings(
        experiment
    )
    result = _original_execute_one_experiment(
        governed_exp,
        behavior_ir=behavior_ir,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        campaign_id=campaign_id,
        execution_id=execution_id,
        actor_tokens=actor_tokens,
    )
    output = dict(_dict(result))
    if diagnostic["present"]:
        output["pre_resolution_diagnostic"] = diagnostic
    return output


__all__ = sorted(
    {
        *[
            name
            for name in dir(_gov)
            if not name.startswith("__")
        ],
        "execute_one_experiment",
        "_formal_experiment_without_raw_prebindings",
    }
)
