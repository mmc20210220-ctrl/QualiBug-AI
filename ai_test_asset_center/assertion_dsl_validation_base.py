"""Validation-aware assertion facade built on the stable restricted DSL."""
from __future__ import annotations

from typing import Any

from . import assertion_dsl_base as _base
from .assertion_dsl_base import *  # noqa: F401,F403


VALIDATION_REJECTION_KIND = "validation_rejection"
SUPPORTED_KINDS = set(_base.SUPPORTED_KINDS) | {VALIDATION_REJECTION_KIND}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_only_observations(observations: dict[str, Any]) -> dict[str, Any]:
    """Exclude the effect observer so a proven transport violation is not masked."""

    filtered = dict(_dict(observations))
    raw_receipts = filtered.get("observer_receipts")
    if isinstance(raw_receipts, list):
        filtered["observer_receipts"] = [
            receipt
            for receipt in raw_receipts
            if _text(_dict(receipt).get("observer_id")) != "business_effect"
        ]
    filtered.pop("business_effect_observer_receipt", None)
    return filtered


def _validation_receipt(
    *,
    status_probe: dict[str, Any],
    status: str,
    reason_code: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    effect_receipt_id: str = "",
    error: str = "",
    harness_error: bool = False,
) -> dict[str, Any]:
    observer_ids = [
        _text(value)
        for value in status_probe.get("observer_receipt_ids") or []
        if _text(value)
    ]
    if _text(effect_receipt_id):
        observer_ids.append(_text(effect_receipt_id))
    return _base._assertion_receipt(
        assertion_id=_text(status_probe.get("assertion_id")),
        kind=VALIDATION_REJECTION_KIND,
        status=status,
        reason_code=reason_code,
        expected=expected,
        actual=actual,
        error=error,
        observer_receipt_ids=observer_ids,
        source_refs=[
            dict(item)
            for item in status_probe.get("source_refs") or []
            if isinstance(item, dict)
        ],
        harness_error=harness_error,
        campaign_id=_text(status_probe.get("campaign_id")),
        execution_id=_text(status_probe.get("execution_id")),
    )


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Evaluate validation rejection as transport rejection plus zero side effect."""

    spec = _dict(assertion)
    kind = _text(spec.get("kind") or spec.get("type"))
    if kind != VALIDATION_REJECTION_KIND:
        return _base.evaluate_assertion(
            assertion,
            observations=observations,
            source_refs=source_refs,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )

    try:
        expected_class = int(spec.get("expected_class", 4))
        expected_effect_count = int(spec.get("expected_effect_count", 0))
    except (TypeError, ValueError) as exc:
        probe = _base.evaluate_assertion(
            {
                **spec,
                "kind": "http_status_class",
                "expected_class": 4,
            },
            observations=_status_only_observations(observations),
            source_refs=source_refs,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
        return _validation_receipt(
            status_probe=probe,
            status="INDETERMINATE",
            reason_code="VALIDATION_ASSERTION_SPEC_INVALID",
            expected={
                "status_class": spec.get("expected_class"),
                "treatment_effect_count": spec.get("expected_effect_count"),
            },
            actual={
                "status_code": _dict(observations).get("status_code"),
                "treatment_effect_count": _dict(observations).get(
                    "treatment_effect_count"
                ),
            },
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )

    probe = _base.evaluate_assertion(
        {
            **spec,
            "kind": "http_status_class",
            "expected_class": expected_class,
        },
        observations=_status_only_observations(observations),
        source_refs=source_refs,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    obs = _dict(observations)
    effect_count = obs.get("treatment_effect_count")
    if effect_count is None:
        effect_count = obs.get("effect_count")
    expected = {
        "status_class": expected_class,
        "treatment_effect_count": expected_effect_count,
    }
    actual = {
        "status_code": obs.get("status_code"),
        "treatment_effect_count": effect_count,
    }

    if probe.get("harness_error") is True:
        return _validation_receipt(
            status_probe=probe,
            status="INDETERMINATE",
            reason_code=_text(probe.get("reason_code"))
            or "VALIDATION_TRANSPORT_EVIDENCE_INVALID",
            expected=expected,
            actual=actual,
            error=_text(probe.get("error")),
            harness_error=True,
        )
    if _text(probe.get("status")) == "INDETERMINATE":
        return _validation_receipt(
            status_probe=probe,
            status="INDETERMINATE",
            reason_code=_text(probe.get("reason_code"))
            or "VALIDATION_TRANSPORT_EVIDENCE_MISSING",
            expected=expected,
            actual=actual,
            error=_text(probe.get("error")),
        )
    if _text(probe.get("status")) == "VIOLATION":
        # 2xx soft business reject still counts as a validation rejection miss
        # when transport looked successful but the body denied the write.
        if (
            expected_class == 4
            and int(obs.get("status_code") or 0) // 100 == 2
            and (
                obs.get("business_rejected") is True
                or _dict(obs.get("business_outcome")).get("business_rejected") is True
                or obs.get("zero_effect_on_accepted_write") is True
            )
        ):
            return _validation_receipt(
                status_probe=probe,
                status="VIOLATION",
                reason_code="VALIDATION_SOFT_FAIL_ACCEPTED",
                expected=expected,
                actual={
                    **actual,
                    "business_rejected": True,
                    "zero_effect_on_accepted_write": obs.get(
                        "zero_effect_on_accepted_write"
                    ),
                },
            )
        return _validation_receipt(
            status_probe=probe,
            status="VIOLATION",
            reason_code="VALIDATION_REJECTION_NOT_ENFORCED",
            expected=expected,
            actual=actual,
        )

    effect_receipt_id = ""
    effect_receipt = _dict(obs.get("business_effect_observer_receipt"))
    if effect_receipt:
        try:
            validated_effect = _base.validate_observer_receipt(effect_receipt)
        except Exception as exc:
            return _validation_receipt(
                status_probe=probe,
                status="INDETERMINATE",
                reason_code="BUSINESS_EFFECT_RECEIPT_INVALID",
                expected=expected,
                actual=actual,
                error=f"{type(exc).__name__}: {exc}",
                harness_error=True,
            )
        effect_receipt_id = _text(validated_effect.get("receipt_id"))
        if (
            _text(validated_effect.get("campaign_id"))
            and _text(validated_effect.get("campaign_id"))
            != _text(probe.get("campaign_id"))
        ) or (
            _text(validated_effect.get("execution_id"))
            and _text(validated_effect.get("execution_id"))
            != _text(probe.get("execution_id"))
        ):
            return _validation_receipt(
                status_probe=probe,
                status="INDETERMINATE",
                reason_code="BUSINESS_EFFECT_RECEIPT_LINEAGE_MISMATCH",
                expected=expected,
                actual=actual,
                effect_receipt_id=effect_receipt_id,
                harness_error=True,
            )
        if _text(validated_effect.get("status")).upper() != "OBSERVED":
            return _validation_receipt(
                status_probe=probe,
                status="INDETERMINATE",
                reason_code=(
                    _text(validated_effect.get("reason_code"))
                    or "BUSINESS_EFFECT_EVIDENCE_INDETERMINATE"
                ),
                expected=expected,
                actual=actual,
                effect_receipt_id=effect_receipt_id,
                harness_error=(
                    _text(validated_effect.get("status")).upper()
                    in {"FAILED", "UNSUPPORTED"}
                ),
            )

    # Session/token exchanges can be validation targets without producing a
    # durable entity effect. Only accept this scope when both the compiled
    # assertion and the runtime projection declare it; an observation-only flag
    # must never bypass the ordinary business-effect evidence gate.
    if (
        _text(spec.get("business_effect_requirement")).upper()
        == "NOT_APPLICABLE"
        and obs.get("business_effect_not_applicable") is True
    ):
        if effect_receipt:
            return _validation_receipt(
                status_probe=probe,
                status="INDETERMINATE",
                reason_code="VALIDATION_BUSINESS_EFFECT_SCOPE_CONFLICT",
                expected={
                    **expected,
                    "business_effect_status": "NOT_APPLICABLE",
                },
                actual={
                    **actual,
                    "business_effect_status": "OBSERVED_RECEIPT_PRESENT",
                },
                effect_receipt_id=effect_receipt_id,
                harness_error=True,
            )
        if expected_effect_count != 0:
            return _validation_receipt(
                status_probe=probe,
                status="INDETERMINATE",
                reason_code="VALIDATION_BUSINESS_EFFECT_REQUIRED",
                expected=expected,
                actual=actual,
            )
        return _validation_receipt(
            status_probe=probe,
            status="PASS",
            reason_code="",
            expected={
                **expected,
                "business_effect_status": "NOT_APPLICABLE",
            },
            actual={
                **actual,
                "business_effect_status": "NOT_APPLICABLE",
                "business_effect_requirement_basis": _text(
                    obs.get("business_effect_not_applicable_basis")
                ),
            },
        )

    if obs.get("business_effect_observed") is not True or effect_count is None:
        return _validation_receipt(
            status_probe=probe,
            status="INDETERMINATE",
            reason_code="VALIDATION_BUSINESS_EFFECT_MISSING",
            expected=expected,
            actual=actual,
            effect_receipt_id=effect_receipt_id,
        )
    try:
        normalized_effect_count = int(effect_count)
    except (TypeError, ValueError) as exc:
        return _validation_receipt(
            status_probe=probe,
            status="INDETERMINATE",
            reason_code="VALIDATION_BUSINESS_EFFECT_INVALID",
            expected=expected,
            actual=actual,
            effect_receipt_id=effect_receipt_id,
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )
    actual["treatment_effect_count"] = normalized_effect_count
    if normalized_effect_count != expected_effect_count:
        return _validation_receipt(
            status_probe=probe,
            status="VIOLATION",
            reason_code="VALIDATION_REJECTION_SIDE_EFFECT",
            expected=expected,
            actual=actual,
            effect_receipt_id=effect_receipt_id,
        )
    return _validation_receipt(
        status_probe=probe,
        status="PASS",
        reason_code="",
        expected=expected,
        actual=actual,
        effect_receipt_id=effect_receipt_id,
    )


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    results = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(
            assertion.get("observer_id")
            or assertion.get("assertion_id")
            or "default"
        )
        obs = _dict(
            observations_by_id.get(obs_key)
            or observations_by_id.get("default")
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
        "passed": sum(
            1 for item in results if item.get("status") == "PASS"
        ),
        "violations": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "indeterminate": sum(
            1
            for item in results
            if item.get("status") == "INDETERMINATE"
        ),
        "failed": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "harness_errors": sum(
            1 for item in results if item.get("harness_error")
        ),
        "results": results,
    }
