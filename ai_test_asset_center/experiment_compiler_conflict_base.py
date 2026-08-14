"""Runtime-principal-aware facade for experiment compilation.

Permission, isolation, privacy, and visibility experiments are meaningful only
when control and treatment resolve to different real principals. Distinct IR
node IDs are insufficient: duplicated actor nodes may reference the same test
account or the same credential secret. This facade blocks such false contrasts
before they consume execution budget or produce misleading evidence.
"""
from __future__ import annotations

from typing import Any

from . import experiment_compiler_base as _base
from .experiment_compiler_base import *  # noqa: F401,F403


_original_compile_experiment = _base.compile_experiment_for_obligation
_SENSITIVE_FAMILIES = frozenset({
    "authorization",
    "isolation",
    "privacy",
    "visibility",
})


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _principal_material(actor: dict[str, Any]) -> dict[str, str]:
    row = _dict(actor)
    return {
        "account": _text(
            row.get("account_ref")
            or row.get("account_id")
            or row.get("principal_ref")
        ).casefold(),
        "secret": _text(
            row.get("credential_secret_ref")
            or row.get("secret_ref")
        ),
        "subject": _text(
            row.get("runtime_subject_ref")
            or row.get("subject_ref")
            or row.get("principal_id")
        ).casefold(),
        "role": _text(row.get("role_key") or row.get("role")).casefold(),
    }


def _pair_refs(obligation: dict[str, Any]) -> tuple[str, str]:
    obl = _dict(obligation)
    prop = _dict(obl.get("property"))
    required = [
        _text(value)
        for value in _list(obl.get("required_actors"))
        if _text(value)
    ]
    control = _text(
        prop.get("control_actor_ref")
        or prop.get("owner_actor_ref")
        or (required[0] if required else "")
    )
    treatment = _text(
        prop.get("treatment_actor_ref")
        or prop.get("viewer_actor_ref")
        or (required[1] if len(required) > 1 else "")
    )
    return control, treatment


def _runtime_pair_problem(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> str:
    family = _text(_dict(obligation).get("risk_family"))
    if family not in _SENSITIVE_FAMILIES:
        return ""
    # Single-actor permitted invocation is intentionally not a control/treatment
    # pair — IR had permits without an executable deny counterpart.
    if (
        _text(_dict(_dict(obligation).get("property")).get("template"))
        == "permitted_operation_invocation"
    ):
        return ""
    # Single-arm credential-gated write guard: the anonymous write is the whole
    # experiment (rejection-expected), there is no second principal to pair —
    # same shape rationale as permitted_operation_invocation.
    if (
        _text(_dict(_dict(obligation).get("property")).get("template"))
        == "credential_gated_write"
    ):
        return ""
    control_ref, treatment_ref = _pair_refs(obligation)
    if not control_ref or not treatment_ref:
        return "actor_pair_missing"
    if control_ref == treatment_ref:
        return "same_actor_ref"

    actors = {
        _text(actor.get("id")): actor
        for actor in _list(_dict(behavior_ir).get("actors"))
        if isinstance(actor, dict) and _text(actor.get("id"))
    }
    control_actor = actors.get(control_ref)
    treatment_actor = actors.get(treatment_ref)
    if not isinstance(control_actor, dict) or not isinstance(
        treatment_actor,
        dict,
    ):
        # The stable compiler owns missing-node diagnostics.
        return ""

    control = _principal_material(control_actor)
    treatment = _principal_material(treatment_actor)
    if control["account"] and control["account"] == treatment["account"]:
        return "shared_account_ref"
    if control["secret"] and control["secret"] == treatment["secret"]:
        return "shared_credential_secret_ref"
    if control["subject"] and control["subject"] == treatment["subject"]:
        return "shared_runtime_subject_ref"

    public_roles = {"anonymous", "public"}
    control_public = control["role"] in public_roles
    treatment_public = treatment["role"] in public_roles
    control_proven = bool(
        control["account"] or control["secret"] or control["subject"]
    )
    treatment_proven = bool(
        treatment["account"] or treatment["secret"] or treatment["subject"]
    )
    if (
        control_public
        and treatment_public
        and not control_proven
        and not treatment_proven
    ):
        return "shared_anonymous_runtime_context"
    if not control_proven and not control_public:
        return "control_principal_not_proven"
    if not treatment_proven and not treatment_public:
        return "treatment_principal_not_proven"
    return ""


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: set[str] | None = None,
) -> dict[str, Any]:
    problem = _runtime_pair_problem(obligation, behavior_ir)
    if problem:
        obligation_id = (
            _text(_dict(obligation).get("obligation_id"))
            or "unknown_obligation"
        )
        return _base.blocked_experiment(
            obligation_id,
            "BLOCKED_RUNTIME_ACTOR_PAIR_NOT_DISTINCT",
            f"runtime_actor_pair_not_distinct:{problem}",
        )
    return _original_compile_experiment(
        obligation,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )


def compile_experiments(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
) -> dict[str, Any]:
    """Compile a batch with this facade's principal-pair validator."""
    return _base.compile_experiments(
        obligations,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        compile_one=compile_experiment_for_obligation,
    )
