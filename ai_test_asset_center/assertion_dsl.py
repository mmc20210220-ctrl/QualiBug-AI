"""Canonical outcome-aware assertion receipt authority.

The existing privacy and typed evaluator mechanics remain unchanged in the private
compatibility module. This facade filters foreign outcome observations, requires one
matching outcome receipt for canonical assertions, enforces the final tri-state
truthfulness boundary, and seals ``outcome_ref`` into every assertion verdict.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from . import observer_contracts as _outcome_observers
from . import _assertion_dsl_privacy_mechanics as _privacy
from ._assertion_dsl_privacy_mechanics import *  # noqa: F401,F403

_core = _privacy._core
_original_evaluate_assertion = _privacy.evaluate_assertion
_original_validate_assertion_receipt = _core.validate_assertion_receipt
_original_assertion_receipt = _core._assertion_receipt

_CANONICAL_FIELDS = (
    "semantic_role",
    "outcome_ref",
    "oracle_template_ref",
    "assertion_requirement_ref",
    "canonical_outcome_identity_bound",
)

# These reason codes describe absence of comparison evidence, not an observed
# counterexample.  Older evaluator branches set ``passed=False`` while emitting
# one of these reasons, which sealed a customer-visible VIOLATION from missing
# evidence.  The final assertion authority makes that impossible.
_EVIDENCE_MISSING_VIOLATION_REASONS = frozenset({
    "POSTCONDITION_FIELD_EVIDENCE_MISSING",
    "JSON_COMPARE_EXPECTED_PATH_MISSING",
})


def __getattr__(name: str) -> Any:
    return getattr(_privacy, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _assertion_identity(spec: dict[str, Any]) -> dict[str, Any]:
    outcome_ref = _text(spec.get("outcome_ref"))
    return {
        "semantic_role": _text(
            spec.get("semantic_role")
            or ("MANDATORY_OUTCOME" if outcome_ref else "")
        ).upper(),
        "outcome_ref": outcome_ref,
        "oracle_template_ref": _text(
            spec.get("oracle_template_ref") or spec.get("template_ref")
        ),
        "assertion_requirement_ref": _text(spec.get("assertion_requirement_ref")),
        "canonical_outcome_identity_bound": bool(outcome_ref),
    }


def _receipt_outcome_ref(receipt: dict[str, Any]) -> str:
    return _text(
        receipt.get("outcome_ref")
        or _dict(_dict(receipt.get("evidence")).get("canonical_outcome_identity")).get(
            "outcome_ref"
        )
    )


def _filtered_observations(
    observations: dict[str, Any], outcome_ref: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    obs = dict(_dict(observations))
    raw_receipts = obs.get("observer_receipts")
    if not isinstance(raw_receipts, list):
        return obs, []
    retained: list[dict[str, Any]] = []
    matching: list[dict[str, Any]] = []
    for raw in raw_receipts:
        if not isinstance(raw, dict):
            retained.append(raw)
            continue
        validated = _outcome_observers.validate_observer_receipt(raw)
        receipt_ref = _receipt_outcome_ref(validated)
        if not receipt_ref:
            retained.append(validated)
        elif receipt_ref == outcome_ref:
            retained.append(validated)
            matching.append(validated)
    obs["observer_receipts"] = retained
    return obs, matching


def _seal_assertion_receipt(
    receipt: dict[str, Any], identity: dict[str, Any]
) -> dict[str, Any]:
    if not any(
        _text(identity.get(field))
        for field in (
            "semantic_role",
            "outcome_ref",
            "oracle_template_ref",
            "assertion_requirement_ref",
        )
    ):
        return dict(receipt)
    payload = {
        key: value for key, value in dict(receipt).items() if key != "receipt_id"
    }
    payload.update(identity)
    return {
        **payload,
        "receipt_id": "assert_"
        + hashlib.sha256(_core._canonical(payload).encode("utf-8")).hexdigest()[:24],
    }


def _indeterminate_identity_receipt(
    *,
    spec: dict[str, Any],
    identity: dict[str, Any],
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None,
    campaign_id: str,
    execution_id: str,
    reason_code: str,
    error: str = "",
) -> dict[str, Any]:
    base = _original_assertion_receipt(
        assertion_id=_text(spec.get("assertion_id") or spec.get("id")),
        kind=_text(spec.get("kind") or spec.get("type")),
        status="INDETERMINATE",
        reason_code=reason_code,
        expected=spec.get("expected"),
        actual=None,
        error=error,
        observer_receipt_ids=[
            _text(row.get("receipt_id"))
            for row in _list(_dict(observations).get("observer_receipts"))
            if isinstance(row, dict) and _text(row.get("receipt_id"))
        ],
        source_refs=[
            dict(row)
            for row in _list(
                source_refs if source_refs is not None else spec.get("source_refs")
            )
            if isinstance(row, dict)
        ],
        harness_error=False,
        campaign_id=_text(campaign_id or _dict(observations).get("campaign_id")),
        execution_id=_text(execution_id or _dict(observations).get("execution_id")),
    )
    return _seal_assertion_receipt(base, identity)


def _rebuild_base_receipt(
    prior: dict[str, Any],
    *,
    status: str,
    reason_code: str,
) -> dict[str, Any]:
    """Re-seal one base assertion receipt without changing its evidence payload."""

    rebuilt = _original_assertion_receipt(
        assertion_id=_text(prior.get("assertion_id")),
        kind=_text(prior.get("kind")),
        status=status,
        reason_code=reason_code,
        expected=prior.get("expected"),
        actual=prior.get("actual"),
        error=_text(prior.get("error")),
        observer_receipt_ids=[
            _text(value)
            for value in _list(prior.get("observer_receipt_ids"))
            if _text(value)
        ],
        source_refs=[
            dict(row)
            for row in _list(prior.get("source_refs"))
            if isinstance(row, dict)
        ],
        harness_error=bool(prior.get("harness_error")),
        campaign_id=_text(prior.get("campaign_id")),
        execution_id=_text(prior.get("execution_id")),
    )
    if isinstance(prior.get("field_oracle_trace"), dict):
        rebuilt["field_oracle_trace"] = dict(prior["field_oracle_trace"])
    return rebuilt


def _row_has_declared_state_evidence(row: Any, allowed: set[str]) -> bool:
    """Whether a response row exposes a state value the filter can actually judge."""

    if not isinstance(row, dict):
        return False
    for key, value in row.items():
        normalized = re.sub(r"[^a-z0-9]+", "", str(key).lower())
        if "status" in normalized or "state" in normalized:
            return isinstance(value, (str, int, float, bool)) and _text(value) != ""
    return any(isinstance(value, str) and value in allowed for value in row.values())


def _truthful_tri_state_projection(
    *,
    spec: dict[str, Any],
    observations: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Prevent missing/incomparable evidence from becoming PASS or VIOLATION.

    This is the last verdict boundary before canonical sealing.  A concrete
    observed mismatch is still a VIOLATION.  A missing operand, missing field,
    or row that cannot be interpreted under the source-declared assertion stays
    INDETERMINATE.  No matching/observer semantics are invented here.
    """

    row = dict(receipt)
    kind = _text(spec.get("kind") or spec.get("type"))
    status = _text(row.get("status")).upper()
    reason = _text(row.get("reason_code"))

    if status == "VIOLATION" and reason in _EVIDENCE_MISSING_VIOLATION_REASONS:
        return _rebuild_base_receipt(
            row,
            status="INDETERMINATE",
            reason_code=reason,
        )

    # field_delta historically folded MISSING/NON_NUMERIC rows into the same
    # boolean accumulator as real mismatches.  A real FAIL remains sufficient
    # counterexample evidence; otherwise incomplete field evidence is unknown.
    if status == "VIOLATION" and kind == "field_delta":
        field_results = [
            dict(item)
            for item in _list(_dict(row.get("actual")).get("field_results"))
            if isinstance(item, dict)
        ]
        result_states = {
            _text(item.get("result")).upper() for item in field_results
        }
        if "FAIL" not in result_states and (
            not field_results
            or bool(result_states.intersection({"MISSING", "NON_NUMERIC"}))
        ):
            return _rebuild_base_receipt(
                row,
                status="INDETERMINATE",
                reason_code="FIELD_DELTA_EVIDENCE_MISSING",
            )

    # A row-state filter can PASS only when every returned business row exposes
    # a recognizable state value.  The base evaluator intentionally skipped
    # rows without a state field, but then treated an empty violation list as
    # PASS; that converted absence of state evidence into proof of compliance.
    effective_kind = _core.KIND_ALIASES.get(kind, kind)
    if status == "PASS" and effective_kind == "response_rows_state_filter":
        body = _dict(observations).get("body")
        business_rows = _core._response_rows(body)
        if business_rows:
            allowed = {
                _text(value)
                for value in _list(spec.get("allowed_states"))
                if _text(value)
            }
            if not allowed or any(
                not _row_has_declared_state_evidence(item, allowed)
                for item in business_rows
            ):
                return _rebuild_base_receipt(
                    row,
                    status="INDETERMINATE",
                    reason_code="ROW_STATE_FILTER_STATE_EVIDENCE_MISSING",
                )

    return row


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    spec = _dict(assertion)
    identity = _assertion_identity(spec)
    strict = bool(spec.get("canonical_outcome_identity_required")) or bool(
        identity["outcome_ref"]
    )
    if strict and not identity["outcome_ref"]:
        return _indeterminate_identity_receipt(
            spec=spec,
            identity=identity,
            observations=observations,
            source_refs=source_refs,
            campaign_id=campaign_id,
            execution_id=execution_id,
            reason_code="ASSERTION_CANONICAL_OUTCOME_REF_MISSING",
        )

    governed_observations = dict(_dict(observations))
    matching: list[dict[str, Any]] = []
    if identity["outcome_ref"]:
        try:
            governed_observations, matching = _filtered_observations(
                governed_observations, identity["outcome_ref"]
            )
        except Exception as exc:
            return _indeterminate_identity_receipt(
                spec=spec,
                identity=identity,
                observations=governed_observations,
                source_refs=source_refs,
                campaign_id=campaign_id,
                execution_id=execution_id,
                reason_code="ASSERTION_OUTCOME_OBSERVER_RECEIPT_INVALID",
                error=f"{type(exc).__name__}: {exc}",
            )
        observed_matching = [
            row for row in matching if _text(row.get("status")).upper() == "OBSERVED"
        ]
        if strict and not observed_matching:
            return _indeterminate_identity_receipt(
                spec=spec,
                identity=identity,
                observations=governed_observations,
                source_refs=source_refs,
                campaign_id=campaign_id,
                execution_id=execution_id,
                reason_code="ASSERTION_OUTCOME_OBSERVER_RECEIPT_MISSING",
            )

    base = _original_evaluate_assertion(
        spec,
        observations=governed_observations,
        source_refs=source_refs,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    governed = _truthful_tri_state_projection(
        spec=spec,
        observations=governed_observations,
        receipt=base,
    )
    return _seal_assertion_receipt(governed, identity)


def validate_assertion_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    if not set(_CANONICAL_FIELDS).intersection(row):
        return _original_validate_assertion_receipt(row)
    if not set(_CANONICAL_FIELDS).issubset(row):
        raise ValueError("assertion_receipt_outcome_fields_invalid")
    identity = {
        "semantic_role": _text(row.get("semantic_role")).upper(),
        "outcome_ref": _text(row.get("outcome_ref")),
        "oracle_template_ref": _text(row.get("oracle_template_ref")),
        "assertion_requirement_ref": _text(row.get("assertion_requirement_ref")),
        "canonical_outcome_identity_bound": bool(
            row.get("canonical_outcome_identity_bound")
        ),
    }
    if identity["semantic_role"] == "MANDATORY_OUTCOME" and not identity["outcome_ref"]:
        raise ValueError("assertion_receipt_outcome_ref_missing")
    if identity["canonical_outcome_identity_bound"] is not bool(identity["outcome_ref"]):
        raise ValueError("assertion_receipt_outcome_binding_invalid")

    base_row = {
        key: value for key, value in row.items() if key not in set(_CANONICAL_FIELDS)
    }
    base_expected = _original_assertion_receipt(
        assertion_id=_text(base_row.get("assertion_id")),
        kind=_text(base_row.get("kind")),
        status=_text(base_row.get("status")),
        reason_code=_text(base_row.get("reason_code")),
        expected=base_row.get("expected"),
        actual=base_row.get("actual"),
        error=_text(base_row.get("error")),
        observer_receipt_ids=list(base_row.get("observer_receipt_ids") or []),
        source_refs=[
            dict(item)
            for item in _list(base_row.get("source_refs"))
            if isinstance(item, dict)
        ],
        harness_error=bool(base_row.get("harness_error")),
        campaign_id=_text(base_row.get("campaign_id")),
        execution_id=_text(base_row.get("execution_id")),
    )
    if isinstance(base_row.get("field_oracle_trace"), dict):
        base_expected["field_oracle_trace"] = dict(base_row["field_oracle_trace"])
    _original_validate_assertion_receipt(base_expected)
    for key, value in base_expected.items():
        if key == "receipt_id":
            continue
        if base_row.get(key) != value:
            raise ValueError("assertion_receipt_content_invalid")

    expected = _seal_assertion_receipt(base_expected, identity)
    if row != expected:
        raise ValueError("assertion_receipt_fingerprint_invalid")
    return dict(expected)


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(
            assertion.get("observer_id")
            or assertion.get("assertion_id")
            or "default"
        )
        obs = _dict(
            observations_by_id.get(obs_key) or observations_by_id.get("default")
        )
        results.append(
            evaluate_assertion(
                assertion,
                observations=obs,
                campaign_id=campaign_id,
                execution_id=execution_id,
            )
        )
    return {
        "total": len(results),
        "passed": sum(1 for item in results if item.get("status") == "PASS"),
        "violations": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "indeterminate": sum(
            1 for item in results if item.get("status") == "INDETERMINATE"
        ),
        "failed": sum(1 for item in results if item.get("status") == "VIOLATION"),
        "harness_errors": sum(1 for item in results if item.get("harness_error")),
        "results": results,
    }


# The stable evaluator imports this validator directly into its module globals. Point it
# at the canonical authority so nested evaluation and Contract Oracle validation agree.
_core.validate_observer_receipt = _outcome_observers.validate_observer_receipt
_core.validate_assertion_receipt = validate_assertion_receipt

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name not in {"_privacy", "_core"}
)
