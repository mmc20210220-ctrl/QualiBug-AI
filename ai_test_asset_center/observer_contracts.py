"""Observer facade with source-authoritative response-only business effects.

The accumulated outcome-aware observer mechanics live in
``_observer_contracts_outcome_authority_mechanics``.  A 2xx write response may
prove a newly materialized entity only when the compiled step carries a FROZEN
identity-output contract and the response actually exposes that declared output.
Generic keys such as ``code``/``slug`` are not creation evidence by name alone.

This boundary injects compiler-sealed identity-output contracts onto their exact
runtime steps for observation, then tightens the existing business-effect
fallback before authorization comparison consumes it. Ordinary before/after
readback evidence is unchanged.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from . import _observer_contracts_outcome_authority_mechanics as _authority

for _name in dir(_authority):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_authority, _name)

_base = _authority._base
_original_observe_requirements = _authority.observe_experiment_requirements
_original_observe_business_effect = _base._observe_business_effect

_RESPONSE_ONLY_EFFECT_BASES = frozenset({
    "write_response_new_identity",
    "response_bound_create_observer",
})


def __getattr__(name: str) -> Any:
    return getattr(_authority, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_authority)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_identity_output_contract(value: Any) -> dict[str, Any]:
    row = _dict(value)
    if (
        _text(row.get("schema_version")) != "qualibug.identity-output-binding.v1"
        or _text(row.get("status")).upper() != "FROZEN"
        or not _text(row.get("source_identity_field"))
        or not _text(row.get("source_path"))
        or not _text(row.get("source_authority"))
    ):
        return {}
    return row


def _extract_declared_path(body: Any, path: str) -> Any:
    """Read one explicit identity-output path; no fuzzy key search."""

    raw = _text(path)
    if not raw:
        return None
    if raw.startswith("$"):
        raw = raw[1:]
        if raw.startswith("."):
            raw = raw[1:]
    if raw.startswith("/"):
        tokens = [
            token.replace("~1", "/").replace("~0", "~")
            for token in raw.split("/")[1:]
            if token != ""
        ]
    else:
        tokens = []
        for name, index in re.findall(r"([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]", raw):
            tokens.append(name if name else index)
        if not tokens and raw:
            tokens = [raw]

    current = body
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _contains_scalar(value: Any, needle: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_scalar(child, needle) for child in value.values())
    if isinstance(value, list):
        return any(_contains_scalar(child, needle) for child in value)
    return value == needle


def _selected_effect_step(
    execution_steps: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    phase = ""
    if "treatment_effect_count" in evidence:
        phase = "treatment"
    elif "control_effect_count" in evidence:
        phase = "control"
    candidates = [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and (not phase or _text(step.get("phase")) == phase)
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]
    return _dict(candidates[-1] if candidates else {})


def _response_only_effect_authority(
    execution_steps: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    basis = _text(evidence.get("effect_basis"))
    if basis not in _RESPONSE_ONLY_EFFECT_BASES:
        return True, "not_response_only", {}

    step = _selected_effect_step(execution_steps, evidence)
    if not step:
        return False, "BUSINESS_EFFECT_SOURCE_STEP_MISSING", {}
    contract = _valid_identity_output_contract(step.get("identity_output_binding"))
    if not contract:
        contract = _valid_identity_output_contract(
            _dict(step.get("governance_receipt")).get("identity_output_binding")
        )
    if not contract:
        return False, "BUSINESS_EFFECT_IDENTITY_OUTPUT_AUTHORITY_MISSING", {}

    governance = _dict(step.get("governance_receipt"))
    write_body = _dict(governance.get("write")).get("body")
    source_path = _text(contract.get("source_path"))
    value = _extract_declared_path(write_body, source_path)
    if value in (None, "", [], {}):
        return False, "BUSINESS_EFFECT_IDENTITY_OUTPUT_VALUE_MISSING", contract

    before_body = _dict(governance.get("before")).get("body")
    request_body = governance.get("materialized_request_body")
    if _contains_scalar(before_body, value) or _contains_scalar(request_body, value):
        return False, "BUSINESS_EFFECT_IDENTITY_OUTPUT_NOT_NEW", contract

    if basis == "response_bound_create_observer":
        readback_body = _dict(governance.get("response_bound_after")).get("body")
        if not _contains_scalar(readback_body, value):
            return False, "BUSINESS_EFFECT_IDENTITY_READBACK_MISMATCH", contract

    return True, "frozen_identity_output", contract


def _strict_observe_business_effect(
    execution_steps: list[dict[str, Any]],
    *,
    aggregate_control_treatment: bool = False,
    require_treatment_window: bool = False,
) -> dict[str, Any]:
    receipt = _original_observe_business_effect(
        execution_steps,
        aggregate_control_treatment=aggregate_control_treatment,
        require_treatment_window=require_treatment_window,
    )
    row = _dict(receipt)
    evidence = dict(_dict(row.get("evidence")))
    if (
        _text(row.get("status")).upper() != "OBSERVED"
        or _text(evidence.get("effect_basis")) not in _RESPONSE_ONLY_EFFECT_BASES
    ):
        return row

    allowed, reason, contract = _response_only_effect_authority(
        execution_steps,
        evidence,
    )
    if allowed:
        evidence["response_only_effect_authority"] = reason
        evidence["identity_output_source_path"] = _text(contract.get("source_path"))
        return _base.build_observer_receipt(
            observer_id="business_effect",
            status="OBSERVED",
            evidence=evidence,
            campaign_id=_text(row.get("campaign_id")),
            execution_id=_text(row.get("execution_id")),
        )

    evidence.update(
        {
            "business_effect_observed": False,
            "response_only_effect_authority": "BLOCKED",
            "response_only_effect_rejected_reason": reason,
        }
    )
    return _base.build_observer_receipt(
        observer_id="business_effect",
        status="INDETERMINATE",
        reason_code=reason,
        evidence=evidence,
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )


def _plan_identity_contracts(experiment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contracts: dict[str, dict[str, Any]] = {}
    for phase in ("precondition", "control", "treatment"):
        for raw in _list(_dict(experiment).get(f"{phase}_plan")):
            step = _dict(raw)
            step_id = _text(step.get("step_id") or step.get("id"))
            contract = _valid_identity_output_contract(step.get("identity_output_binding"))
            if step_id and contract:
                contracts[step_id] = deepcopy(contract)
    return contracts


def _inject_identity_output_contracts(
    experiment: dict[str, Any],
    observations: dict[str, Any],
) -> list[tuple[dict[str, Any], bool, Any]]:
    """Temporarily project sealed plan contracts onto their exact runtime steps."""

    contracts = _plan_identity_contracts(experiment)
    changes: list[tuple[dict[str, Any], bool, Any]] = []
    if not contracts:
        return changes
    for raw in _list(observations.get("execution_steps")):
        if not isinstance(raw, dict):
            continue
        step_id = _text(raw.get("step_id") or raw.get("id"))
        contract = contracts.get(step_id)
        if not contract:
            continue
        had = "identity_output_binding" in raw
        previous = raw.get("identity_output_binding")
        existing = _valid_identity_output_contract(previous)
        if existing and existing != contract:
            # Conflicting runtime vs compiled identity authority is not resolved
            # by overwrite. Leave it untouched; the strict observer will reject
            # it unless its own contract is independently valid for the effect.
            continue
        changes.append((raw, had, previous))
        raw["identity_output_binding"] = deepcopy(contract)
    return changes


def _restore_injected_contracts(changes: list[tuple[dict[str, Any], bool, Any]]) -> None:
    for row, had, previous in reversed(changes):
        if had:
            row["identity_output_binding"] = previous
        else:
            row.pop("identity_output_binding", None)


def observe_experiment_requirements(
    experiment: dict[str, Any],
    *,
    observations: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> list[dict[str, Any]]:
    changes = _inject_identity_output_contracts(
        _dict(experiment),
        observations if isinstance(observations, dict) else {},
    )
    try:
        return _original_observe_requirements(
            experiment,
            observations=observations,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
    finally:
        _restore_injected_contracts(changes)


# The base dispatch computes business_effect before authorization_comparison, so
# this must be patched at the source rather than demoting the receipt afterward.
# Otherwise a fake effect could already have contaminated authorization evidence.
_base._observe_business_effect = _strict_observe_business_effect
_authority._base._observe_business_effect = _strict_observe_business_effect
_authority.observe_experiment_requirements = observe_experiment_requirements
_authority._auth.observe_experiment_requirements = observe_experiment_requirements

__all__ = sorted(
    {
        *[
            name
            for name in dir(_authority)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "observe_experiment_requirements",
        "_strict_observe_business_effect",
        "_response_only_effect_authority",
    }
)
