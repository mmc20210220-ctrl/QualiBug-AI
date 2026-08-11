"""Canonical outcome-aware Contract Oracle authority.

The historical activation and assertion mechanics remain unchanged in the private module.
This facade proves exact mandatory-outcome coverage and deterministically projects one
independent Oracle receipt per violated outcome. Aggregate execution truth is preserved;
customer findings consume only the per-outcome receipts.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import assertion_dsl as _assertions
from . import observer_contracts as _observers
from . import _contract_oracles_mechanics as _core
from ._contract_oracles_mechanics import *  # noqa: F401,F403

from .accepted_residue_oracle import install_accepted_residue_oracle

install_accepted_residue_oracle(_core)
build_contract_oracle_activation_receipt = (
    _core.build_contract_oracle_activation_receipt
)

_original_evaluate_contract_oracle = _core.evaluate_contract_oracle
_original_validate_contract_oracle_receipt = _core.validate_contract_oracle_receipt

_CANONICAL_FIELDS = (
    "canonical_outcome_identity_required",
    "mandatory_outcome_refs",
    "covered_outcome_refs",
    "missing_outcome_refs",
    "duplicate_outcome_refs",
    "foreign_outcome_refs",
    "violation_outcome_refs",
    "indeterminate_outcome_refs",
    "primary_violation_outcome_ref",
    "canonical_outcome_identity_complete",
    "outcome_fanout_required",
    "violation_occurrence_count",
    "parent_oracle_receipt_id",
    "parent_violation_outcome_refs",
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, dict)):
        items = [values]
    else:
        try:
            items = list(values)
        except TypeError:
            items = [values]
    return sorted({_text(value) for value in items if _text(value)})


def _assertion_outcome_ref(assertion: dict[str, Any]) -> str:
    return _text(assertion.get("outcome_ref"))


def _experiment_outcome_contract(
    experiment: dict[str, Any], assertions: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    explicit = _unique(experiment.get("mandatory_outcome_refs"))
    declared = _unique(
        [
            _dict(row).get("outcome_ref")
            for row in _list(experiment.get("assertions"))
            if isinstance(row, dict) and _dict(row).get("mandatory") is not False
        ]
    )
    strict = bool(experiment.get("canonical_outcome_identity_required")) or bool(
        explicit or declared
    )
    mandatory = explicit or declared
    if strict and not mandatory:
        mandatory = _unique([_assertion_outcome_ref(row) for row in assertions])
    return strict, mandatory


def _canonical_projection(
    experiment: dict[str, Any], assertions: list[dict[str, Any]]
) -> dict[str, Any]:
    strict, mandatory = _experiment_outcome_contract(experiment, assertions)
    refs = [_assertion_outcome_ref(row) for row in assertions]
    counts = Counter(ref for ref in refs if ref)
    covered = sorted(counts)
    duplicate = sorted(ref for ref, count in counts.items() if count > 1)
    missing = sorted(set(mandatory) - set(covered))
    foreign = sorted(set(covered) - set(mandatory)) if mandatory else []
    violations = _unique(
        _assertion_outcome_ref(row)
        for row in assertions
        if _text(row.get("status")).upper() == "VIOLATION"
    )
    indeterminate = _unique(
        _assertion_outcome_ref(row)
        for row in assertions
        if _text(row.get("status")).upper() == "INDETERMINATE"
    )
    missing_identity = strict and any(not ref for ref in refs)
    complete = bool(
        strict
        and mandatory
        and not missing
        and not duplicate
        and not foreign
        and not missing_identity
    )
    return {
        "canonical_outcome_identity_required": strict,
        "mandatory_outcome_refs": mandatory,
        "covered_outcome_refs": covered,
        "missing_outcome_refs": missing,
        "duplicate_outcome_refs": duplicate,
        "foreign_outcome_refs": foreign,
        "violation_outcome_refs": violations,
        "indeterminate_outcome_refs": indeterminate,
        "primary_violation_outcome_ref": (
            violations[0] if complete and len(violations) == 1 else ""
        ),
        "canonical_outcome_identity_complete": complete,
        "outcome_fanout_required": bool(complete and len(violations) > 1),
        "violation_occurrence_count": len(violations),
        "parent_oracle_receipt_id": "",
        "parent_violation_outcome_refs": [],
        "assertion_outcome_ref_missing": missing_identity,
    }


def _identity_reason_codes(projection: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if projection.get("canonical_outcome_identity_required"):
        if not projection.get("mandatory_outcome_refs"):
            reasons.append("CANONICAL_MANDATORY_OUTCOME_REFS_MISSING")
        if projection.get("assertion_outcome_ref_missing"):
            reasons.append("ASSERTION_CANONICAL_OUTCOME_REF_MISSING")
        if projection.get("missing_outcome_refs"):
            reasons.append("MANDATORY_OUTCOME_ASSERTION_MISSING")
        if projection.get("duplicate_outcome_refs"):
            reasons.append("DUPLICATE_OUTCOME_ASSERTION_RECEIPTS")
        if projection.get("foreign_outcome_refs"):
            reasons.append("FOREIGN_OUTCOME_ASSERTION_RECEIPTS")
    return sorted(set(reasons))


def _seal_oracle_receipt(
    base: dict[str, Any], projection: dict[str, Any]
) -> dict[str, Any]:
    public_projection = {field: projection.get(field) for field in _CANONICAL_FIELDS}
    payload = {key: value for key, value in dict(base).items() if key != "receipt_id"}
    payload.update(public_projection)
    return _core._content_receipt("oracle_", payload)


def evaluate_contract_oracle(
    *,
    experiment: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    exp = _dict(experiment)
    base = _original_evaluate_contract_oracle(experiment=exp, evidence=evidence)
    base_assertions = [
        _assertions.validate_assertion_receipt(_dict(row))
        for row in _list(base.get("assertions"))
        if isinstance(row, dict)
    ]
    projection = _canonical_projection(exp, base_assertions)
    if not projection["canonical_outcome_identity_required"]:
        return base

    reasons = _identity_reason_codes(projection)
    governed = dict(base)
    governed["assertions"] = base_assertions
    governed["failed_assertions"] = [
        dict(row) for row in base_assertions if row.get("status") == "VIOLATION"
    ]
    governed["assertion_receipt_ids"] = [
        _text(row.get("receipt_id")) for row in base_assertions
    ]
    governed["violation_assertion_receipt_ids"] = [
        _text(row.get("receipt_id"))
        for row in base_assertions
        if row.get("status") == "VIOLATION"
    ]
    governed["indeterminate_assertion_receipt_ids"] = [
        _text(row.get("receipt_id"))
        for row in base_assertions
        if row.get("status") == "INDETERMINATE"
    ]
    if reasons:
        governed.update(
            {
                "status": "INDETERMINATE",
                "verdict": "indeterminate",
                "customer_deliverable": False,
                "customer_deliverable_candidate": False,
                "missing_requirements": sorted(
                    {
                        _text(value)
                        for value in [*_list(base.get("missing_requirements")), *reasons]
                        if _text(value)
                    }
                ),
                "demotion_reason": "canonical_outcome_identity_incomplete",
            }
        )
    return _seal_oracle_receipt(governed, projection)


def _validate_canonical_projection(
    row: dict[str, Any], assertions: list[dict[str, Any]]
) -> dict[str, Any]:
    projection = {field: row.get(field) for field in _CANONICAL_FIELDS}
    normalized = {
        "canonical_outcome_identity_required": bool(
            projection["canonical_outcome_identity_required"]
        ),
        "mandatory_outcome_refs": _unique(projection["mandatory_outcome_refs"]),
        "covered_outcome_refs": _unique(projection["covered_outcome_refs"]),
        "missing_outcome_refs": _unique(projection["missing_outcome_refs"]),
        "duplicate_outcome_refs": _unique(projection["duplicate_outcome_refs"]),
        "foreign_outcome_refs": _unique(projection["foreign_outcome_refs"]),
        "violation_outcome_refs": _unique(projection["violation_outcome_refs"]),
        "indeterminate_outcome_refs": _unique(projection["indeterminate_outcome_refs"]),
        "primary_violation_outcome_ref": _text(
            projection["primary_violation_outcome_ref"]
        ),
        "canonical_outcome_identity_complete": bool(
            projection["canonical_outcome_identity_complete"]
        ),
        "outcome_fanout_required": bool(projection["outcome_fanout_required"]),
        "violation_occurrence_count": int(
            projection["violation_occurrence_count"] or 0
        ),
        "parent_oracle_receipt_id": _text(projection["parent_oracle_receipt_id"]),
        "parent_violation_outcome_refs": _unique(
            projection["parent_violation_outcome_refs"]
        ),
    }
    refs = [_assertion_outcome_ref(item) for item in assertions]
    counts = Counter(ref for ref in refs if ref)
    derived = {
        "covered_outcome_refs": sorted(counts),
        "duplicate_outcome_refs": sorted(
            ref for ref, count in counts.items() if count > 1
        ),
        "missing_outcome_refs": sorted(
            set(normalized["mandatory_outcome_refs"]) - set(counts)
        ),
        "foreign_outcome_refs": sorted(
            set(counts) - set(normalized["mandatory_outcome_refs"])
        ),
        "violation_outcome_refs": _unique(
            _assertion_outcome_ref(item)
            for item in assertions
            if item.get("status") == "VIOLATION"
        ),
        "indeterminate_outcome_refs": _unique(
            _assertion_outcome_ref(item)
            for item in assertions
            if item.get("status") == "INDETERMINATE"
        ),
    }
    for field, value in derived.items():
        if normalized[field] != value:
            raise ValueError(f"contract_oracle_{field}_mismatch")
    expected_complete = bool(
        normalized["canonical_outcome_identity_required"]
        and normalized["mandatory_outcome_refs"]
        and not normalized["missing_outcome_refs"]
        and not normalized["duplicate_outcome_refs"]
        and not normalized["foreign_outcome_refs"]
        and all(refs)
    )
    if normalized["canonical_outcome_identity_complete"] is not expected_complete:
        raise ValueError("contract_oracle_outcome_identity_complete_invalid")
    expected_primary = (
        normalized["violation_outcome_refs"][0]
        if expected_complete and len(normalized["violation_outcome_refs"]) == 1
        else ""
    )
    if normalized["primary_violation_outcome_ref"] != expected_primary:
        raise ValueError("contract_oracle_primary_outcome_ref_invalid")
    expected_fanout = bool(
        expected_complete and len(normalized["violation_outcome_refs"]) > 1
    )
    if normalized["outcome_fanout_required"] is not expected_fanout:
        raise ValueError("contract_oracle_outcome_fanout_invalid")
    if normalized["violation_occurrence_count"] != len(
        normalized["violation_outcome_refs"]
    ):
        raise ValueError("contract_oracle_violation_occurrence_count_invalid")
    if normalized["parent_oracle_receipt_id"]:
        if not normalized["parent_violation_outcome_refs"]:
            raise ValueError("contract_oracle_parent_violation_refs_missing")
        if not set(normalized["violation_outcome_refs"]).issubset(
            set(normalized["parent_violation_outcome_refs"])
        ):
            raise ValueError("contract_oracle_parent_violation_ref_mismatch")
    elif normalized["parent_violation_outcome_refs"]:
        raise ValueError("contract_oracle_parent_identity_incomplete")
    return normalized


def validate_contract_oracle_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    # Post-hoc demotion gates (authorization causality, delivery, SPEC oracle
    # validity) mutate status/verdict after the sealed receipt_id was computed.
    # Accept those annotated receipts when the gate provenance fields are present.
    if (
        row.get("pre_causality_oracle_verdict")
        or row.get("authorization_delivery_gate")
        or row.get("pre_validity_oracle_verdict")
        or row.get("oracle_validity_gate")
    ):
        if not _text(row.get("receipt_id")):
            raise ValueError("contract_oracle_receipt_fingerprint_invalid")
        if _text(row.get("status")) not in {
            "VIOLATION",
            "PROPERTY_HELD",
            "INDETERMINATE",
            "BLOCKED",
            "HARNESS_FAILED",
        }:
            raise ValueError("contract_oracle_semantics_invalid")
        return dict(row)
    if not set(_CANONICAL_FIELDS).intersection(row):
        return _original_validate_contract_oracle_receipt(row)
    if not set(_CANONICAL_FIELDS).issubset(row):
        raise ValueError("contract_oracle_outcome_fields_invalid")
    activation = _core.validate_contract_oracle_activation_receipt(
        _dict(row.get("activation_receipt"))
    )
    if _text(row.get("activation_receipt_id")) != _text(activation.get("receipt_id")):
        raise ValueError("contract_oracle_activation_identity_mismatch")
    assertions = [
        _assertions.validate_assertion_receipt(_dict(item))
        for item in _list(row.get("assertions"))
    ]
    if any(
        _text(item.get("campaign_id")) != _text(row.get("campaign_id"))
        or _text(item.get("execution_id")) != _text(row.get("execution_id"))
        for item in assertions
    ):
        raise ValueError("contract_oracle_assertion_lineage_mismatch")
    normalized = _validate_canonical_projection(row, assertions)
    reasons = _identity_reason_codes(
        {
            **normalized,
            "assertion_outcome_ref_missing": any(
                not _assertion_outcome_ref(item) for item in assertions
            ),
        }
    )
    if reasons:
        if any(
            (
                _text(row.get("status")) != "INDETERMINATE",
                _text(row.get("verdict")) != "indeterminate",
                row.get("customer_deliverable") is not False,
                row.get("customer_deliverable_candidate") is not False,
                _text(row.get("demotion_reason"))
                != "canonical_outcome_identity_incomplete",
                not set(reasons).issubset(
                    {_text(value) for value in _list(row.get("missing_requirements"))}
                ),
            )
        ):
            raise ValueError("contract_oracle_outcome_fail_closed_invalid")
    else:
        base_payload = {
            key: value
            for key, value in row.items()
            if key not in set(_CANONICAL_FIELDS) | {"receipt_id"}
        }
        base = _core._content_receipt("oracle_", base_payload)
        _original_validate_contract_oracle_receipt(base)

    expected = _seal_oracle_receipt(
        {key: value for key, value in row.items() if key not in set(_CANONICAL_FIELDS)},
        normalized,
    )
    if row != expected:
        raise ValueError("contract_oracle_receipt_fingerprint_invalid")
    return dict(expected)


def project_contract_oracle_for_outcome(
    receipt: dict[str, Any], outcome_ref: str
) -> dict[str, Any]:
    """Derive one independently gateable Oracle receipt from an aggregate violation."""
    parent = validate_contract_oracle_receipt(_dict(receipt))
    ref = _text(outcome_ref)
    if not ref:
        raise ValueError("contract_oracle_outcome_ref_missing")
    if not bool(parent.get("canonical_outcome_identity_required")):
        raise ValueError("contract_oracle_outcome_projection_not_canonical")
    if _text(parent.get("status")) != "VIOLATION":
        raise ValueError("contract_oracle_outcome_projection_not_violated")
    parent_refs = _unique(parent.get("violation_outcome_refs"))
    if ref not in parent_refs:
        raise ValueError("contract_oracle_outcome_projection_foreign")
    matches = [
        _assertions.validate_assertion_receipt(_dict(row))
        for row in _list(parent.get("assertions"))
        if isinstance(row, dict)
        and _text(_dict(row).get("status")) == "VIOLATION"
        and _assertion_outcome_ref(_dict(row)) == ref
    ]
    if len(matches) != 1:
        raise ValueError("contract_oracle_outcome_projection_ambiguous")
    assertion = matches[0]
    governed = {
        key: value
        for key, value in parent.items()
        if key not in set(_CANONICAL_FIELDS) | {"receipt_id"}
    }
    governed.update(
        {
            "status": "VIOLATION",
            "verdict": "customer_deliverable_defect_candidate",
            "customer_deliverable": False,
            "customer_deliverable_candidate": True,
            "assertions": [assertion],
            "failed_assertions": [assertion],
            "assertion_receipt_ids": [_text(assertion.get("receipt_id"))],
            "violation_assertion_receipt_ids": [_text(assertion.get("receipt_id"))],
            "indeterminate_assertion_receipt_ids": [],
            "field_oracle_traces": (
                [dict(assertion["field_oracle_trace"])]
                if isinstance(assertion.get("field_oracle_trace"), dict)
                else []
            ),
            "field_oracle_trace_count": (
                1 if isinstance(assertion.get("field_oracle_trace"), dict) else 0
            ),
            "missing_requirements": [],
            "demotion_reason": "",
        }
    )
    projection = {
        "canonical_outcome_identity_required": True,
        "mandatory_outcome_refs": [ref],
        "covered_outcome_refs": [ref],
        "missing_outcome_refs": [],
        "duplicate_outcome_refs": [],
        "foreign_outcome_refs": [],
        "violation_outcome_refs": [ref],
        "indeterminate_outcome_refs": [],
        "primary_violation_outcome_ref": ref,
        "canonical_outcome_identity_complete": True,
        "outcome_fanout_required": False,
        "violation_occurrence_count": 1,
        "parent_oracle_receipt_id": _text(parent.get("receipt_id")),
        "parent_violation_outcome_refs": parent_refs,
    }
    return validate_contract_oracle_receipt(
        _seal_oracle_receipt(governed, projection)
    )


_core.evaluate_assertion = _assertions.evaluate_assertion
_core.validate_assertion_receipt = _assertions.validate_assertion_receipt
_core.validate_observer_receipt = _observers.validate_observer_receipt
_core.validate_contract_oracle_receipt = validate_contract_oracle_receipt

__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {"_core", "_assertions", "_observers"}
)
