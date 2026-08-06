"""Authorization-verdict facade over the stable observer contracts.

The baseline observer implementation remains unchanged except for one explicit
source-contract rule: after an authorized write control proves the operation is
real and effectful, a restricted actor receiving any 2xx response is itself an
authorization enforcement violation. A temporary no-op must not convert an
accepted forbidden request into a passing result.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import observer_contracts_base as _base
from .observer_contracts_base import *  # noqa: F401,F403


_original_authorization_comparison = _base.observe_authorization_comparison


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status(value: Any) -> int:
    row = _dict(value)
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def observe_authorization_comparison(
    *,
    control: dict[str, Any],
    treatment: dict[str, Any],
    require_same_resource: bool,
    business_effect: dict[str, Any] | None = None,
    binding_materialization_receipts: list[dict[str, Any]] | None = None,
    identity_keys: Iterable[str] | None = None,
    comparison_dimension: str = "",
) -> dict[str, Any]:
    baseline = _original_authorization_comparison(
        control=control,
        treatment=treatment,
        require_same_resource=require_same_resource,
        business_effect=business_effect,
        binding_materialization_receipts=binding_materialization_receipts,
        identity_keys=identity_keys,
        comparison_dimension=comparison_dimension,
    )
    control_row = _dict(control)
    treatment_row = _dict(treatment)
    if _text(control_row.get("method")).upper() == "GET":
        return baseline
    control_path = _text(control_row.get("path")).split("?", 1)[0]
    treatment_path = _text(treatment_row.get("path")).split("?", 1)[0]
    same_path = bool(control_path and control_path == treatment_path)
    control_template = _text(control_row.get("path_template"))
    treatment_template = _text(treatment_row.get("path_template"))
    _same_template = bool(
        control_template
        and control_template == treatment_template
    )
    if require_same_resource and not same_path and not _same_template:
        evidence = dict(_dict(baseline.get("evidence")))
        evidence.update({
            "same_resource_proven": False,
            "resource_match_basis": "requested_resource_path_mismatch",
            "owner_can_access": True,
            "viewer_can_access": None,
            "leak_detected": None,
        })
        return _base._receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="SAME_RESOURCE_NOT_PROVEN",
            evidence=evidence,
        )

    effect = _dict(business_effect)
    control_effect = effect.get("control_effect_count")
    treatment_effect = effect.get("treatment_effect_count")
    try:
        proven_control = (
            effect.get("business_effect_observed") is True
            and control_effect is not None
            and int(control_effect) > 0
        )
        proven_zero_treatment_effect = (
            treatment_effect is not None
            and int(treatment_effect) == 0
        )
    except (TypeError, ValueError):
        return baseline
    if not (
        proven_control
        and proven_zero_treatment_effect
        and 200 <= _status(treatment_row) < 300
        and isinstance(control_row.get("governance_receipt"), dict)
        and isinstance(treatment_row.get("governance_receipt"), dict)
    ):
        return baseline

    evidence = dict(_dict(baseline.get("evidence")))
    evidence.update({
        "control_effect_count": int(control_effect),
        "treatment_effect_count": int(treatment_effect),
        "viewer_request_accepted": True,
        "viewer_business_effect_observed": False,
    })
    if require_same_resource and not same_path and not _same_template:
        evidence.update({
            "same_resource_proven": False,
            "resource_match_basis": "requested_resource_path_mismatch",
            "owner_can_access": True,
            "viewer_can_access": None,
            "leak_detected": None,
        })
        return _base._receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="SAME_RESOURCE_NOT_PROVEN",
            evidence=evidence,
        )

    evidence.update({
        "same_resource_proven": same_path or _same_template or not require_same_resource,
        "resource_match_basis": (
            "same_requested_resource_path_and_explicit_deny"
            if same_path
            else "same_path_template_explicit_deny"
            if _same_template
            else "explicit_deny_operation_scope"
        ),
        "owner_can_access": True,
        "viewer_can_access": True,
        "leak_detected": True,
        "authorization_failure_mode": "restricted_write_request_accepted",
    })
    return _base._receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        evidence=evidence,
    )


# ``observe_experiment_requirements`` is defined in the baseline module and
# resolves this global at call time. Patch the baseline global so the complete
# main observer pipeline uses the strengthened verdict, not only direct callers.
_base.observe_authorization_comparison = observe_authorization_comparison
