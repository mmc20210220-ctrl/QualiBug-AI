"""Canonical outcome-aware observer receipt authority.

Authorization comparison mechanics remain in the private compatibility module. This
public facade preserves legacy receipt bytes when no canonical outcome identity is
present, and content-addresses ``outcome_ref`` whenever an observer proves one mandatory
business outcome.
"""
from __future__ import annotations

import copy
from typing import Any

from . import _observer_contracts_authorization_mechanics as _auth
from ._observer_contracts_authorization_mechanics import *  # noqa: F401,F403

_base = _auth._base

_original_receipt = _base._receipt
_original_validate_observer_receipt = _base.validate_observer_receipt
_original_observe_experiment_requirements = _base.observe_experiment_requirements

_CANONICAL_IDENTITY_FIELDS = (
    "semantic_role",
    "outcome_ref",
    "oracle_template_ref",
    "assertion_requirement_ref",
)
_CANONICAL_EVIDENCE_KEY = "canonical_outcome_identity"
_BASE_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "campaign_id",
    "execution_id",
    "observer_id",
    "status",
    "reason_code",
    "evidence",
}


def __getattr__(name: str) -> Any:
    return getattr(_auth, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity_values(
    evidence: dict[str, Any] | None,
    *,
    semantic_role: str = "",
    outcome_ref: str = "",
    oracle_template_ref: str = "",
    assertion_requirement_ref: str = "",
) -> dict[str, str]:
    embedded = _dict(_dict(evidence).get(_CANONICAL_EVIDENCE_KEY))
    resolved_outcome = _text(outcome_ref or embedded.get("outcome_ref"))
    resolved_role = _text(
        semantic_role
        or embedded.get("semantic_role")
        or ("MANDATORY_OUTCOME" if resolved_outcome else "")
    ).upper()
    return {
        "semantic_role": resolved_role,
        "outcome_ref": resolved_outcome,
        "oracle_template_ref": _text(
            oracle_template_ref or embedded.get("oracle_template_ref")
        ),
        "assertion_requirement_ref": _text(
            assertion_requirement_ref or embedded.get("assertion_requirement_ref")
        ),
    }


def _canonical_identity_present(identity: dict[str, str]) -> bool:
    return any(_text(identity.get(field)) for field in _CANONICAL_IDENTITY_FIELDS)


def _receipt(
    *,
    observer_id: str,
    status: str,
    reason_code: str = "",
    evidence: dict[str, Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
    semantic_role: str = "",
    outcome_ref: str = "",
    oracle_template_ref: str = "",
    assertion_requirement_ref: str = "",
) -> dict[str, Any]:
    identity = _identity_values(
        evidence,
        semantic_role=semantic_role,
        outcome_ref=outcome_ref,
        oracle_template_ref=oracle_template_ref,
        assertion_requirement_ref=assertion_requirement_ref,
    )
    if not _canonical_identity_present(identity):
        return _original_receipt(
            observer_id=observer_id,
            status=status,
            reason_code=reason_code,
            evidence=evidence,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )

    normalized_status = _text(status).upper()
    if normalized_status not in _base.OBSERVER_STATUSES:
        raise ValueError(f"observer_status_invalid:{normalized_status}")
    safe_evidence = copy.deepcopy(evidence or {})
    safe_evidence[_CANONICAL_EVIDENCE_KEY] = dict(identity)
    payload = {
        "schema_version": _base.SCHEMA_VERSION,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "observer_id": _text(observer_id),
        "status": normalized_status,
        "reason_code": _text(reason_code),
        "evidence": safe_evidence,
        **identity,
    }
    return {
        **payload,
        "receipt_id": "obs_" + _base._fingerprint(payload),
    }


def build_observer_receipt(
    *,
    observer_id: str,
    status: str,
    reason_code: str = "",
    evidence: dict[str, Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
    semantic_role: str = "",
    outcome_ref: str = "",
    oracle_template_ref: str = "",
    assertion_requirement_ref: str = "",
) -> dict[str, Any]:
    if not _text(observer_id):
        raise ValueError("observer_id_missing")
    return _receipt(
        observer_id=observer_id,
        status=status,
        reason_code=reason_code,
        evidence=evidence,
        campaign_id=campaign_id,
        execution_id=execution_id,
        semantic_role=semantic_role,
        outcome_ref=outcome_ref,
        oracle_template_ref=oracle_template_ref,
        assertion_requirement_ref=assertion_requirement_ref,
    )


def validate_observer_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    if not set(_CANONICAL_IDENTITY_FIELDS).intersection(row):
        return _original_validate_observer_receipt(row)
    expected_fields = _BASE_RECEIPT_FIELDS | set(_CANONICAL_IDENTITY_FIELDS)
    if set(row) != expected_fields:
        raise ValueError("observer_receipt_fields_invalid")
    if row.get("schema_version") != _base.SCHEMA_VERSION:
        raise ValueError("observer_receipt_schema_invalid")
    if not isinstance(row.get("evidence"), dict):
        raise ValueError("observer_receipt_content_invalid")
    identity = {field: _text(row.get(field)) for field in _CANONICAL_IDENTITY_FIELDS}
    if identity["semantic_role"] == "MANDATORY_OUTCOME" and not identity["outcome_ref"]:
        raise ValueError("observer_receipt_outcome_ref_missing")
    embedded = _identity_values(row["evidence"])
    if identity != embedded:
        raise ValueError("observer_receipt_outcome_identity_mismatch")
    expected = _receipt(
        observer_id=_text(row.get("observer_id")),
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        evidence=dict(row["evidence"]),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
        **identity,
    )
    if row != expected:
        raise ValueError("observer_receipt_fingerprint_invalid")
    return dict(expected)


def bind_observer_receipt_lineage(
    receipt: dict[str, Any],
    *,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    resolved_campaign_id = _text(campaign_id)
    resolved_execution_id = _text(execution_id)
    if not resolved_campaign_id or not resolved_execution_id:
        raise ValueError("observer_receipt_lineage_missing")
    validated = validate_observer_receipt(receipt)
    existing_campaign_id = _text(validated.get("campaign_id"))
    existing_execution_id = _text(validated.get("execution_id"))
    if existing_campaign_id and existing_campaign_id != resolved_campaign_id:
        raise ValueError("observer_receipt_campaign_mismatch")
    if existing_execution_id and existing_execution_id != resolved_execution_id:
        raise ValueError("observer_receipt_execution_mismatch")
    identity = {
        field: _text(validated.get(field)) for field in _CANONICAL_IDENTITY_FIELDS
    }
    return _receipt(
        observer_id=_text(validated.get("observer_id")),
        status=_text(validated.get("status")),
        reason_code=_text(validated.get("reason_code")),
        evidence=dict(_dict(validated.get("evidence"))),
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
        **identity,
    )


def _declaration_identity(declaration: dict[str, Any]) -> dict[str, str]:
    outcome_ref = _text(declaration.get("outcome_ref"))
    role = _text(declaration.get("semantic_role")).upper()
    if not role and outcome_ref:
        role = "MANDATORY_OUTCOME"
    return {
        "semantic_role": role,
        "outcome_ref": outcome_ref,
        "oracle_template_ref": _text(
            declaration.get("oracle_template_ref")
            or declaration.get("template_ref")
        ),
        "assertion_requirement_ref": _text(
            declaration.get("assertion_requirement_ref")
        ),
    }


def observe_experiment_requirements(
    experiment: dict[str, Any],
    *,
    observations: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> list[dict[str, Any]]:
    exp = _dict(experiment)
    declarations = [
        _dict(row) for row in _list(exp.get("observers")) if isinstance(row, dict)
    ]
    generated = _original_observe_experiment_requirements(
        exp,
        observations=observations,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    strict = bool(exp.get("canonical_outcome_identity_required")) or any(
        _text(row.get("outcome_ref")) for row in declarations
    )
    governed: list[dict[str, Any]] = []
    for index, raw in enumerate(generated):
        receipt = validate_observer_receipt(_dict(raw))
        declaration = declarations[index] if index < len(declarations) else {}
        identity = _declaration_identity(declaration)
        if not _canonical_identity_present(identity):
            governed.append(receipt)
            continue
        status = _text(receipt.get("status"))
        reason = _text(receipt.get("reason_code"))
        if (
            strict
            and identity["semantic_role"] == "MANDATORY_OUTCOME"
            and not identity["outcome_ref"]
        ):
            status = "FAILED"
            reason = "OBSERVER_CANONICAL_OUTCOME_REF_MISSING"
        governed.append(
            _receipt(
                observer_id=_text(receipt.get("observer_id")),
                status=status,
                reason_code=reason,
                evidence=dict(_dict(receipt.get("evidence"))),
                campaign_id=_text(receipt.get("campaign_id")),
                execution_id=_text(receipt.get("execution_id")),
                **identity,
            )
        )
    return governed


# Patch the stable mechanics globals so callers that imported the base module (the
# experiment finalizer does this intentionally) still preserve canonical outcome identity.
_base._receipt = _receipt
_base.build_observer_receipt = build_observer_receipt
_base.validate_observer_receipt = validate_observer_receipt
_base.bind_observer_receipt_lineage = bind_observer_receipt_lineage
_base.observe_experiment_requirements = observe_experiment_requirements

# The authorization mechanics module re-exports the base names captured at import time;
# publish the governed functions explicitly from this authority.
_auth.build_observer_receipt = build_observer_receipt
_auth.validate_observer_receipt = validate_observer_receipt
_auth.bind_observer_receipt_lineage = bind_observer_receipt_lineage
_auth.observe_experiment_requirements = observe_experiment_requirements

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name not in {"_auth", "_base"}
)
