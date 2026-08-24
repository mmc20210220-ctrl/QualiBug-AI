"""Independent, receipt-backed customer Delivery Gate authority.

The v2 gate never trusts mutable finding flags. It revalidates the executed
mainline identity, typed evidence receipts, Contract Oracle, reproduction,
cleanup accounting, and the customer-facing payload fingerprint before a
finding can become DELIVERABLE.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .assertion_dsl import validate_assertion_receipt
from .assertion_control_policy import assertion_requires_control
from .contract_oracles import (
    validate_contract_evidence_receipt,
    validate_contract_oracle_receipt,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .observer_contracts_base import validate_observer_receipt
from .operational_receipts import validate_execution_operational_receipt
from ._delivery_validation_cache import (
    FINDING_FINGERPRINT_CACHE,
    GATE_VALIDATION_CACHE,
    _MISSING,
    content_fingerprint,
)


DELIVERY_EXECUTION_RECEIPT_SCHEMA = "qualibug.delivery-execution-receipt.v1"
REPRODUCTION_RECEIPT_SCHEMA = "qualibug.execution-reproduction-receipt.v1"
DELIVERY_LINEAGE_RECEIPT_SCHEMA = "qualibug.delivery-lineage-receipt.v1"
CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA = (
    "qualibug.customer-delivery-gate-receipt.v2"
)
# Historical v1 receipts emitted by the retired legacy champion. Kept for
# backward-compatible handling of frozen DELIVERABLE terminals in old data.
# The full v2 builder (build_customer_delivery_gate_receipt_v2) emits
# CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA; the minimal helper
# build_customer_delivery_gate_receipt emits this legacy v1 shape on purpose
# because it omits the v2-required identity/fingerprint/adjudication fields.
LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA = (
    "qualibug.customer-delivery-gate-receipt.v1"
)

_GATE_STATUSES = frozenset({"DELIVERABLE", "REJECTED", "BLOCKED", "HARNESS_FAILED"})
_COST_COVERAGE_STATUSES = frozenset({"MEASURED", "PARTIAL", "UNKNOWN"})
_IDENTITY_FIELDS = (
    "run_id",
    "campaign_id",
    "target_id",
    "environment_id",
    "mainline_contract_fingerprint",
    "candidate_id",
    "slice_id",
    "obligation_id",
    "experiment_id",
    "execution_id",
    "evidence_id",
    "finding_id",
)
_DERIVED_FINDING_FIELDS = frozenset({
    "canonical_defect_id",
    "canonical_identity_fingerprint",
    "contract_evidence",
    "customer_delivery_gate_reasons",
    "customer_delivery_status",
    "customer_visible",
    "delivery_occurrence_count",
    "delivery_occurrence_finding_id",
    "delivery_occurrence_finding_ids",
    "delivery_gate_receipt",
    "delivery_gate_receipt_id",
    "delivery_track",
    "duplicate_of",
    "duplicate_variants",
    "finding_class",
    "gate_passed",
    "runtime_observation",
    "semantic_delivery_gate_status",
    "shadow_origin",
})


class DeliveryGateV2Error(ValueError):
    """The evidence bundle is incomplete, foreign, or internally inconsistent."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    text = _text(value).lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _seal(
    payload: dict[str, Any],
    *,
    prefix: str,
    id_field: str,
    fingerprint_field: str,
) -> dict[str, Any]:
    fingerprint = _fingerprint(payload)
    return {
        **payload,
        id_field: prefix + fingerprint[:32],
        fingerprint_field: fingerprint,
    }


def _strict_fields(
    value: dict[str, Any],
    required: set[str],
    *,
    code: str,
) -> None:
    if set(value) != required:
        raise DeliveryGateV2Error(code)


def _finding_payload(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in _dict(finding).items()
        if key not in _DERIVED_FINDING_FIELDS
    }


def finding_payload_fingerprint(finding: dict[str, Any]) -> str:
    if not isinstance(finding, dict) or not finding:
        raise DeliveryGateV2Error("finding_payload_missing")
    # Redaction is deterministic and idempotent: identical finding content
    # always yields the identical fingerprint, so the result is cached by the
    # finding's own content address.  Any content change changes the address
    # and forces recomputation, so caching never weakens validation semantics.
    # Within one run the same finding payload is validated many times (every
    # delivery path re-fingerprints every occurrence), which is why this hot
    # path is memoized instead of re-running the full deep copy + regex scan.
    # The cache address covers the stripped payload only (derived fields are
    # stripped after redaction, so they never influence the result).
    cache_key = content_fingerprint(_finding_payload(finding))
    cached = FINDING_FINGERPRINT_CACHE.get(cache_key)
    if cached is not _MISSING:
        return cached
    # The fingerprint must be computed on the exact form the evaluator
    # receives: every persistence boundary runs the artifact redactor, which
    # deterministically rewrites sensitive values (a response body field named
    # ``token``, a bearer header in raw evidence, ...).  Fingerprinting the
    # LIVE payload at gate-build time while re-derivation validated the
    # REDACTED copy made any finding carrying a redactable value fail with
    # finding_payload_fingerprint_mismatch at artifact write.  Redaction is
    # deterministic and idempotent, so redact-then-hash agrees on both sides
    # and still binds the gate to the exact content the customer sees.
    from .artifact_redactor import redact_artifact

    redacted, _redaction_receipt = redact_artifact(finding)
    stable = redacted if isinstance(redacted, dict) else finding
    result = _fingerprint(_finding_payload(stable))
    FINDING_FINGERPRINT_CACHE.put(cache_key, result)
    return result


def _receipt_ref(receipt: dict[str, Any], *, id_field: str = "receipt_id") -> dict[str, str]:
    receipt_id = _text(_dict(receipt).get(id_field))
    if not receipt_id:
        raise DeliveryGateV2Error("receipt_reference_identity_missing")
    return {
        "receipt_id": receipt_id,
        "fingerprint": _fingerprint(receipt),
    }


def _identity_from_execution(
    execution: dict[str, Any],
    *,
    finding_id: str = "",
) -> dict[str, str]:
    return {
        field: (
            _text(finding_id)
            if field == "finding_id"
            else _text(execution.get(field))
        )
        for field in _IDENTITY_FIELDS
    }


def _validate_identity(
    identity: dict[str, Any],
    *,
    finding_required: bool,
) -> dict[str, str]:
    if set(identity) != set(_IDENTITY_FIELDS):
        raise DeliveryGateV2Error("delivery_identity_fields_invalid")
    normalized = {field: _text(identity.get(field)) for field in _IDENTITY_FIELDS}
    required = [field for field in _IDENTITY_FIELDS if field != "finding_id"]
    if not all(normalized[field] for field in required):
        raise DeliveryGateV2Error("delivery_identity_missing")
    if finding_required and not normalized["finding_id"]:
        raise DeliveryGateV2Error("delivery_finding_identity_missing")
    if not finding_required and normalized["finding_id"]:
        raise DeliveryGateV2Error("nondeliverable_finding_identity_present")
    return normalized


def build_delivery_execution_receipt(
    *,
    mainline_run: dict[str, Any],
    candidate_id: str,
    slice_id: str,
    obligation_id: str,
    experiment_id: str,
    execution_id: str,
    evidence_id: str,
    operational_receipt: dict[str, Any],
    observation_receipt_ids: list[str],
    oracle_receipt_id: str,
    elapsed_ms: int | None = None,
    cost_coverage_status: str = "UNKNOWN",
) -> dict[str, Any]:
    """Bind one executed attempt and its operational accounting to a run."""

    try:
        mainline = validate_mainline_run_contract(_dict(mainline_run))
        operational = validate_execution_operational_receipt(
            _dict(operational_receipt)
        )
    except Exception as exc:
        raise DeliveryGateV2Error(
            f"delivery_execution_dependency_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    if _text(operational.get("execution_status")).upper() not in (
        "EXECUTED",
        "DELIVERABLE",
        # V1.6.1: oracle may have evaluated before cleanup failed. Keep the
        # delivery execution receipt so Field Oracle Traces remain auditable;
        # Formal Finding / TRUE_COMPLETED stay gated separately on cleanup.
        "EXECUTED_BUT_NOT_RESTORED",
    ):
        raise DeliveryGateV2Error("delivery_execution_not_executed")
    operational_fingerprint = _text(operational.get("receipt_fingerprint"))
    if not operational_fingerprint:
        raise DeliveryGateV2Error("operational_receipt_fingerprint_missing")
    observation_ids = [_text(value) for value in observation_receipt_ids]
    if not observation_ids or not all(observation_ids):
        raise DeliveryGateV2Error("delivery_observation_receipts_missing")
    if len(observation_ids) != len(set(observation_ids)):
        raise DeliveryGateV2Error("delivery_observation_receipt_duplicate")
    cost_status = _text(cost_coverage_status).upper()
    if cost_status not in _COST_COVERAGE_STATUSES:
        raise DeliveryGateV2Error("delivery_cost_coverage_status_invalid")
    resolved_elapsed: int | None = None
    if elapsed_ms is not None:
        if isinstance(elapsed_ms, bool):
            raise DeliveryGateV2Error("delivery_elapsed_ms_invalid")
        try:
            resolved_elapsed = int(elapsed_ms)
        except (TypeError, ValueError) as exc:
            raise DeliveryGateV2Error("delivery_elapsed_ms_invalid") from exc
        if resolved_elapsed < 0:
            raise DeliveryGateV2Error("delivery_elapsed_ms_invalid")
    identity = {
        "run_id": _text(mainline.get("run_id")),
        "campaign_id": _text(mainline.get("campaign_id")),
        "target_id": _text(mainline.get("target_id")),
        "environment_id": _text(mainline.get("environment_id")),
        "mainline_contract_fingerprint": _text(
            mainline.get("contract_fingerprint")
        ),
        "candidate_id": _text(candidate_id),
        "slice_id": _text(slice_id),
        "obligation_id": _text(obligation_id),
        "experiment_id": _text(experiment_id),
        "execution_id": _text(execution_id),
        "evidence_id": _text(evidence_id),
        "finding_id": "",
    }
    _validate_identity(identity, finding_required=False)
    if not _text(oracle_receipt_id):
        raise DeliveryGateV2Error("delivery_oracle_receipt_id_missing")
    payload = {
        "schema_version": DELIVERY_EXECUTION_RECEIPT_SCHEMA,
        **{field: identity[field] for field in _IDENTITY_FIELDS if field != "finding_id"},
        "status": "EXECUTED",
        "observation_receipt_ids": sorted(observation_ids),
        "oracle_receipt_id": _text(oracle_receipt_id),
        "operational_receipt": operational,
        "operational_receipt_id": _text(operational.get("receipt_id")),
        "operational_receipt_fingerprint": operational_fingerprint,
        "elapsed_ms": resolved_elapsed,
        "cost_coverage_status": cost_status,
    }
    return _seal(
        payload,
        prefix="delivery_exec_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )


def validate_delivery_execution_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(receipt)
    required = {
        "schema_version",
        "receipt_id",
        "receipt_fingerprint",
        *[field for field in _IDENTITY_FIELDS if field != "finding_id"],
        "status",
        "observation_receipt_ids",
        "oracle_receipt_id",
        "operational_receipt",
        "operational_receipt_id",
        "operational_receipt_fingerprint",
        "elapsed_ms",
        "cost_coverage_status",
    }
    _strict_fields(row, required, code="delivery_execution_fields_invalid")
    if row.get("schema_version") != DELIVERY_EXECUTION_RECEIPT_SCHEMA:
        raise DeliveryGateV2Error("delivery_execution_schema_invalid")
    identity = {
        field: "" if field == "finding_id" else _text(row.get(field))
        for field in _IDENTITY_FIELDS
    }
    _validate_identity(identity, finding_required=False)
    if _text(row.get("status")) != "EXECUTED":
        raise DeliveryGateV2Error("delivery_execution_status_invalid")
    observation_ids = row.get("observation_receipt_ids")
    if (
        not isinstance(observation_ids, list)
        or not observation_ids
        or not all(_text(value) for value in observation_ids)
        or len(observation_ids) != len(set(observation_ids))
    ):
        raise DeliveryGateV2Error("delivery_execution_observations_invalid")
    try:
        operational = validate_execution_operational_receipt(
            _dict(row.get("operational_receipt"))
        )
    except Exception as exc:
        raise DeliveryGateV2Error(
            f"delivery_operational_receipt_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        _text(row.get("operational_receipt_id"))
        != _text(operational.get("receipt_id"))
        or _text(row.get("operational_receipt_fingerprint"))
        != _text(operational.get("receipt_fingerprint"))
        or not _text(row.get("oracle_receipt_id"))
    ):
        raise DeliveryGateV2Error("delivery_execution_reference_mismatch")
    cost_status = _text(row.get("cost_coverage_status"))
    if cost_status not in _COST_COVERAGE_STATUSES:
        raise DeliveryGateV2Error("delivery_cost_coverage_status_invalid")
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    expected = _seal(
        unsigned,
        prefix="delivery_exec_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )
    if row != expected:
        raise DeliveryGateV2Error("delivery_execution_fingerprint_invalid")
    return dict(expected)


def _reproduction_decision(
    *,
    oracle: dict[str, Any],
    observed_phases: set[str],
    observation_count: int,
) -> tuple[bool, str]:
    """Evaluate reproduction against the oracle's declared phase contract."""

    oracle_status = _text(oracle.get("status"))
    non_violation_reasons = {
        "PROPERTY_HELD": "ORACLE_NOT_VIOLATED",
        "BLOCKED": "CONTRACT_ORACLE_BLOCKED",
        "HARNESS_FAILED": "CONTRACT_ORACLE_HARNESS_FAILED",
        "INDETERMINATE": "ASSERTION_INDETERMINATE",
    }
    if oracle_status in non_violation_reasons:
        return False, non_violation_reasons[oracle_status]
    if oracle_status != "VIOLATION":
        raise DeliveryGateV2Error(
            f"reproduction_oracle_status_invalid:{oracle_status or 'missing'}"
        )
    required = _dict(_dict(oracle.get("activation_receipt")).get("required"))
    required_treatment = {
        _text(value)
        for value in _list(required.get("treatment"))
        if _text(value)
    }
    if not required_treatment:
        return False, "REPRODUCTION_TREATMENT_REQUIREMENT_MISSING"
    if observation_count <= 0 or "treatment" not in observed_phases:
        return False, "REPRODUCTION_TREATMENT_MISSING"
    required_control = {
        _text(value)
        for value in _list(required.get("control"))
        if _text(value)
    }
    if required_control and "control" not in observed_phases:
        return False, "REPRODUCTION_CONTROL_MISSING"
    return True, ""


def build_reproduction_receipt(
    *,
    execution_receipt: dict[str, Any],
    steps: list[dict[str, Any]],
    oracle_receipt: dict[str, Any],
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a redacted receipt for the actual executed control/treatment replay."""

    execution = validate_delivery_execution_receipt(execution_receipt)
    try:
        oracle = validate_contract_oracle_receipt(_dict(oracle_receipt))
    except Exception as exc:
        raise DeliveryGateV2Error(
            f"reproduction_oracle_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    for field in ("campaign_id", "execution_id", "experiment_id", "obligation_id"):
        _exec_val = _text(execution.get(field))
        _oracle_val = _text(oracle.get(field))
        if _exec_val != _oracle_val:
            raise DeliveryGateV2Error("reproduction_oracle_lineage_mismatch")
    refs = [dict(value) for value in source_refs if isinstance(value, dict)]
    if not refs:
        raise DeliveryGateV2Error("reproduction_source_refs_missing")
    summaries: list[dict[str, Any]] = []
    for raw in steps:
        step = _dict(raw)
        phase = _text(step.get("phase"))
        if phase not in {"control", "treatment"}:
            continue
        # THE FIFTH LINK.
        #
        # This loop used to require an HTTP shape unconditionally: a positive
        # status_code as the proof of execution, and a path_template as the request
        # identity. That made the delivery gate a second, unnamed ceiling on top of the
        # four-link chain -- a defect on a database, message-queue, rendered-view or
        # timing surface could have a complete obligation family, assertion kind,
        # observer and protocol, produce valid receipts, and still be structurally
        # incapable of becoming customer-deliverable, because its reproduction receipt
        # could not be built.
        #
        # The response side was already adapter-tolerant (db_snapshot is accepted as
        # evidence), so only the request side was blocking.
        #
        # Generalized by branching on the step's declared adapter. The http_api path is
        # byte-identical -- same required fields, same fingerprint composition -- because
        # changing the composition would invalidate every sealed receipt already on disk.
        # A non-http step must supply an equally strong identity: its adapter, an
        # operation_ref, an operation_locator standing in for path_template, and an
        # explicit invocation_outcome standing in for the status code. Nothing is
        # inferred and nothing is optional; a step that cannot state its identity is
        # still skipped or refused exactly as before.
        adapter = _text(step.get("adapter")) or "http_api"
        is_http_step = adapter == "http_api"

        observation_receipt_id = _text(step.get("observation_receipt_id"))
        if is_http_step:
            try:
                status_code = int(step.get("status_code") or 0)
            except (TypeError, ValueError):
                # Skip steps without valid HTTP status codes
                continue
            if not observation_receipt_id:
                raise DeliveryGateV2Error(
                    "reproduction_observation_receipt_missing"
                )
            if status_code <= 0:
                # Skip steps without actual HTTP execution
                continue
        else:
            status_code = 0
            if not observation_receipt_id:
                raise DeliveryGateV2Error(
                    "reproduction_observation_receipt_missing"
                )
            # An adapter-neutral step proves execution with an explicit outcome rather
            # than a status code. Absent outcome means the step never ran, which is the
            # same condition the status_code <= 0 skip covers for HTTP.
            if not _text(step.get("invocation_outcome")):
                continue

        path_template = _text(step.get("path_template"))
        operation_locator = _text(step.get("operation_locator")) or path_template
        request_body_fingerprint = _text(step.get("request_body_fingerprint"))
        request_semantics_fingerprint = _text(
            step.get("request_semantics_fingerprint")
        )
        mutation_class = _text(step.get("mutation_class"))
        mutation_selector = _text(step.get("mutation_selector"))
        mutation_operator = _text(step.get("mutation_operator"))
        _identity_present = path_template if is_http_step else (
            operation_locator and _text(step.get("operation_ref"))
        )
        if (
            not _identity_present
            or not mutation_class
            or not _is_sha256(request_body_fingerprint)
            or not _is_sha256(request_semantics_fingerprint)
        ):
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_missing"
            )
        if is_http_step:
            # Unchanged composition. Do not add fields here: every sealed receipt on
            # disk was fingerprinted with exactly these seven.
            expected_request_semantics = _fingerprint({
                "operation_ref": _text(step.get("operation_ref")),
                "method": _text(step.get("method")).upper(),
                "path_template": path_template,
                "mutation_class": mutation_class,
                "mutation_selector": mutation_selector,
                "mutation_operator": mutation_operator,
                "request_body_fingerprint": request_body_fingerprint,
            })
        else:
            expected_request_semantics = _fingerprint({
                "adapter": adapter,
                "operation_ref": _text(step.get("operation_ref")),
                "operation_locator": operation_locator,
                "invocation_outcome": _text(step.get("invocation_outcome")),
                "mutation_class": mutation_class,
                "mutation_selector": mutation_selector,
                "mutation_operator": mutation_operator,
                "request_body_fingerprint": request_body_fingerprint,
            })
        if request_semantics_fingerprint != expected_request_semantics:
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_fingerprint_invalid"
            )
        summary = {
            "phase": phase,
            "step_id": _text(step.get("step_id")),
            "actor_ref": _text(step.get("actor_ref")),
            "operation_ref": _text(step.get("operation_ref")),
            "method": _text(step.get("method")).upper(),
            "path": _text(step.get("path")),
            "path_template": path_template,
            "status_code": status_code,
            "observation_receipt_id": observation_receipt_id,
            "request_body_fingerprint": request_body_fingerprint,
            "request_semantics_fingerprint": request_semantics_fingerprint,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "response_fingerprint": _fingerprint(step.get("body")),
        }
        if not is_http_step:
            # Added ONLY for a non-http step. The reproduction receipt is sealed, and
            # validate_customer_delivery_gate_bundle rebuilds it and demands byte
            # equality, so adding these keys unconditionally would break replay of every
            # artifact already on disk. An http step's summary therefore stays exactly as
            # it was, while a non-http step carries the identity that makes it
            # reproducible at all.
            summary["adapter"] = adapter
            summary["operation_locator"] = operation_locator
            summary["invocation_outcome"] = _text(step.get("invocation_outcome"))
        summaries.append(summary)
    phases = {_text(value.get("phase")) for value in summaries}
    execution_observation_ids = set(execution["observation_receipt_ids"])
    if any(
        _text(value.get("observation_receipt_id")) not in execution_observation_ids
        for value in summaries
    ):
        raise DeliveryGateV2Error("reproduction_observation_lineage_mismatch")
    reproduced, reproduction_reason = _reproduction_decision(
        oracle=oracle,
        observed_phases=phases,
        observation_count=len(summaries),
    )
    payload = {
        "schema_version": REPRODUCTION_RECEIPT_SCHEMA,
        **{
            field: _text(execution.get(field))
            for field in (
                "campaign_id",
                "obligation_id",
                "experiment_id",
                "execution_id",
                "evidence_id",
            )
        },
        "status": "REPRODUCED" if reproduced else "NOT_REPRODUCED",
        "reason_code": reproduction_reason,
        "oracle_receipt_id": _text(oracle.get("receipt_id")),
        "step_observations": summaries,
        "source_refs": refs,
    }
    return _seal(
        payload,
        prefix="reproduction_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )


def validate_reproduction_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    required = {
        "schema_version",
        "receipt_id",
        "receipt_fingerprint",
        "campaign_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
        "evidence_id",
        "status",
        "reason_code",
        "oracle_receipt_id",
        "step_observations",
        "source_refs",
    }
    _strict_fields(row, required, code="reproduction_receipt_fields_invalid")
    if row.get("schema_version") != REPRODUCTION_RECEIPT_SCHEMA:
        raise DeliveryGateV2Error("reproduction_receipt_schema_invalid")
    if not all(
        _text(row.get(field))
        for field in (
            "campaign_id",
            "obligation_id",
            "experiment_id",
            "execution_id",
            "evidence_id",
            "oracle_receipt_id",
        )
    ):
        raise DeliveryGateV2Error("reproduction_receipt_identity_missing")
    status = _text(row.get("status"))
    if status not in {"REPRODUCED", "NOT_REPRODUCED"}:
        raise DeliveryGateV2Error("reproduction_receipt_status_invalid")
    steps = row.get("step_observations")
    if not isinstance(steps, list) or not isinstance(row.get("source_refs"), list):
        raise DeliveryGateV2Error("reproduction_receipt_content_invalid")
    step_fields = {
        "phase",
        "step_id",
        "actor_ref",
        "operation_ref",
        "method",
        "path",
        "path_template",
        "status_code",
        "observation_receipt_id",
        "request_body_fingerprint",
        "request_semantics_fingerprint",
        "mutation_class",
        "mutation_selector",
        "mutation_operator",
        "response_fingerprint",
    }
    for raw_step in steps:
        step = _dict(raw_step)
        if set(step) != step_fields:
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_fields_invalid"
            )
        if (
            not all(
                _text(step.get(field))
                for field in (
                    "phase",
                    "step_id",
                    "actor_ref",
                    "operation_ref",
                    "method",
                    "path",
                    "path_template",
                    "observation_receipt_id",
                    "mutation_class",
                )
            )
            or not _is_sha256(step.get("request_body_fingerprint"))
            or not _is_sha256(step.get("request_semantics_fingerprint"))
            or not _is_sha256(step.get("response_fingerprint"))
        ):
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_invalid"
            )
        expected_request_semantics = _fingerprint({
            "operation_ref": _text(step.get("operation_ref")),
            "method": _text(step.get("method")).upper(),
            "path_template": _text(step.get("path_template")),
            "mutation_class": _text(step.get("mutation_class")),
            "mutation_selector": _text(step.get("mutation_selector")),
            "mutation_operator": _text(step.get("mutation_operator")),
            "request_body_fingerprint": _text(
                step.get("request_body_fingerprint")
            ),
        })
        if _text(step.get("request_semantics_fingerprint")) != expected_request_semantics:
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_fingerprint_invalid"
            )
    phases = {
        _text(_dict(value).get("phase"))
        for value in steps
        if isinstance(value, dict)
    }
    if (
        status == "REPRODUCED"
        and (
            "treatment" not in phases
            or _text(row.get("reason_code"))
        )
    ):
        raise DeliveryGateV2Error("reproduction_receipt_semantics_invalid")
    if status == "NOT_REPRODUCED" and not _text(row.get("reason_code")):
        raise DeliveryGateV2Error("reproduction_receipt_reason_missing")
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    expected = _seal(
        unsigned,
        prefix="reproduction_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )
    if row != expected:
        raise DeliveryGateV2Error("reproduction_receipt_fingerprint_invalid")
    return dict(expected)


def _validate_receipt_collections(
    *,
    execution: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    observer_receipts: list[dict[str, Any]],
    oracle_receipt: dict[str, Any],
    reproduction_receipt: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    if not contract_evidence_receipts:
        raise DeliveryGateV2Error("contract_evidence_receipts_missing")
    if not observer_receipts:
        raise DeliveryGateV2Error("observer_receipts_missing")
    try:
        contracts = [
            validate_contract_evidence_receipt(_dict(value))
            for value in contract_evidence_receipts
        ]
        observers = [
            validate_observer_receipt(_dict(value))
            for value in observer_receipts
        ]
        oracle = validate_contract_oracle_receipt(_dict(oracle_receipt))
        reproduction = validate_reproduction_receipt(
            _dict(reproduction_receipt)
        )
    except DeliveryGateV2Error:
        raise
    except Exception as exc:
        raise DeliveryGateV2Error(
            f"delivery_evidence_receipt_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    for field in ("campaign_id", "execution_id", "experiment_id", "obligation_id"):
        expected = _text(execution.get(field))
        if _text(oracle.get(field)) != expected:
            raise DeliveryGateV2Error("oracle_execution_lineage_mismatch")
        if _text(reproduction.get(field)) != expected:
            raise DeliveryGateV2Error("reproduction_execution_lineage_mismatch")
        if any(_text(value.get(field)) != expected for value in contracts):
            raise DeliveryGateV2Error("contract_execution_lineage_mismatch")
        if field in {"campaign_id", "execution_id"} and any(
            _text(value.get(field)) != expected for value in observers
        ):
            raise DeliveryGateV2Error("observer_execution_lineage_mismatch")
    ids = [
        _text(value.get("receipt_id"))
        for value in [*contracts, *observers]
    ]
    if not all(ids) or len(ids) != len(set(ids)):
        raise DeliveryGateV2Error("delivery_evidence_receipt_identity_duplicate")
    if _text(execution.get("oracle_receipt_id")) != _text(oracle.get("receipt_id")):
        raise DeliveryGateV2Error("execution_oracle_reference_mismatch")
    if _text(reproduction.get("oracle_receipt_id")) != _text(oracle.get("receipt_id")):
        raise DeliveryGateV2Error("reproduction_oracle_reference_mismatch")
    return contracts, observers, oracle, reproduction


def _cleanup_gate_decision(
    *,
    execution: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> tuple[str, list[str], str]:
    """Return gate status, reason codes, and honest cleanup adjudication.

    Cleanup/compensation/restore is post-test environment hygiene on declared
    non-production targets (root AGENTS.md 原则14): its outcome never overrides
    execution truth or a proven Oracle verdict. Hygiene outcomes therefore
    degrade to ``RESIDUE_ACCEPTED`` — the referenced cleanup contract receipts
    keep their exact FAILED/BLOCKED status and failure detail — while genuine
    receipt-chain integrity conflicts stay fail-closed.
    """

    operational = _dict(execution.get("operational_receipt"))
    cleanup = _dict(operational.get("cleanup_outcome"))
    accepted_non_cleanup = int(
        operational.get("accepted_non_cleanup_write_count") or 0
    )
    cleanup_status = _text(cleanup.get("status")).upper()
    cleanup_contracts = [
        value for value in contracts if _text(value.get("kind")) == "cleanup"
    ]
    covered = sum(
        int(_dict(value.get("evidence")).get("accepted_write_count") or 0)
        for value in cleanup_contracts
    )

    if accepted_non_cleanup == 0:
        if covered:
            # Contracts claim writes the operational receipt never accepted:
            # evidence-chain corruption, not a hygiene state.
            return (
                "HARNESS_FAILED",
                ["CLEANUP_WRITE_COVERAGE_MISMATCH"],
                "INCOMPLETE",
            )
        # No business write was accepted, so no restoration was ever due.
        return "DELIVERABLE", [], "NOT_REQUIRED"

    if not cleanup_contracts:
        # When cleanup was explicitly declared not required by the source
        # obligation, the operational receipt records NOT_REQUIRED status.
        if cleanup_status == "NOT_REQUIRED":
            return "DELIVERABLE", [], "NOT_REQUIRED"
        # The compiler cleanup ladder emits a compensator or accepted-residue
        # entry for every accepted write; their total absence is a harness
        # anomaly and stays fail-closed.
        return "HARNESS_FAILED", ["CLEANUP_EVIDENCE_INCOMPLETE"], "INCOMPLETE"

    # ── Accepted-residue degradation (non-production) ────────────────────────
    # Every cleanup contract is an accepted-residue marker: the write was
    # deliberately left uncleaned because no API/DB/UI compensator exists and
    # the target is a declared non-production environment. Short-circuit
    # before the coverage/completed logic, which assumes a real cleanup ran.
    if all(
        _text(contract.get("status")).upper() == "RESIDUE_ACCEPTED"
        and _dict(contract.get("evidence")).get("residue") is True
        for contract in cleanup_contracts
    ):
        return "DELIVERABLE", [], "RESIDUE_ACCEPTED"

    if covered != accepted_non_cleanup:
        # Partial or absent compensators: best-effort hygiene left test data
        # behind. The finding stands; the leftover stays visible in the
        # referenced cleanup contract receipts.
        return "DELIVERABLE", [], "RESIDUE_ACCEPTED"

    completed_seen = False
    unproven_hygiene = False
    for contract in cleanup_contracts:
        status = _text(contract.get("status")).upper()
        evidence = _dict(contract.get("evidence"))
        audit_ids = [
            _text(value)
            for value in _list(evidence.get("audit_receipt_ids"))
            if _text(value)
        ]
        if status == "COMPLETED":
            completed_seen = True
            restored_with_writes = (
                evidence.get("restoration_verified") is True
                and evidence.get("state_unchanged") is True
                and int(evidence.get("cleanup_write_count") or 0) > 0
                and bool(audit_ids)
            )
            restored_already_absent = (
                evidence.get("restoration_verified") is True
                and evidence.get("state_unchanged") is True
                and int(evidence.get("accepted_write_count") or 0) > 0
                and int(evidence.get("cleanup_write_count") or 0) == 0
                and bool(audit_ids)
            )
            if not (restored_with_writes or restored_already_absent):
                # Completed claim without restoration proof: assume residue.
                unproven_hygiene = True
        elif status == "NOT_REQUIRED":
            # NOT_REQUIRED with zero accepted writes (a rejected/no-op arm)
            # has no audit receipts by definition — there was no write to
            # audit. When accepted writes exist they must carry audit ids
            # proving the state-unchanged claim.
            if not (
                evidence.get("state_unchanged") is True
                and int(evidence.get("cleanup_write_count") or 0) == 0
                and (
                    audit_ids
                    or int(evidence.get("accepted_write_count") or 0) == 0
                )
            ):
                unproven_hygiene = True
        else:
            # Compensation attempted but failed/blocked: residue record.
            unproven_hygiene = True

    expected_operational = "COMPLETED" if completed_seen else "NOT_REQUIRED"
    if unproven_hygiene:
        return "DELIVERABLE", [], "RESIDUE_ACCEPTED"
    if (
        cleanup_status not in ("", expected_operational)
        and int(_dict(cleanup).get("failure_count") or 0) > 0
    ):
        # Operational summary reports failures the sealed receipts disprove;
        # trust the sealed receipts but keep the doubt visible as residue.
        return "DELIVERABLE", [], "RESIDUE_ACCEPTED"
    return "DELIVERABLE", [], expected_operational


def _oracle_harness_reason_detail(oracle: dict[str, Any]) -> str:
    """Preserve validated Oracle/activation failure reasons for the Gate receipt.

    The public Gate reason remains the stable ``CONTRACT_ORACLE_HARNESS_FAILED``
    registry code.  The validated activation receipt already carries the
    concrete failure reasons (for example a cleanup receipt failure); dropping
    them here makes the funnel impossible to diagnose from its terminal receipt.
    """

    activation = _dict(oracle.get("activation_receipt"))
    values: list[str] = []
    for source in (oracle, activation):
        for value in _list(source.get("reason_codes")):
            normalized = _text(value)
            if normalized and normalized not in values:
                values.append(normalized)
    return ",".join(values)[:1000]


def _validate_active_chain(
    *,
    execution: dict[str, Any],
    contracts: list[dict[str, Any]],
    observers: list[dict[str, Any]],
    oracle: dict[str, Any],
    reproduction: dict[str, Any],
) -> tuple[str, list[str]]:
    oracle_status = _text(oracle.get("status"))
    activation = _dict(oracle.get("activation_receipt"))
    activation_status = _text(activation.get("status"))
    if oracle_status == "PROPERTY_HELD":
        return "REJECTED", ["ORACLE_NOT_VIOLATED"]
    if oracle_status == "INDETERMINATE":
        # Preserve the concrete oracle reason (e.g. WRITE_EFFECT_EVIDENCE_REQUIRED,
        # OBSERVER_EVIDENCE_INDETERMINATE) so the funnel shows WHY the assertion
        # could not be decided instead of a bare ASSERTION_INDETERMINATE.
        oracle_reasons = [
            _text(value)
            for value in _list(oracle.get("reason_codes"))
            if _text(value)
        ]
        return "BLOCKED", (
            ["ASSERTION_INDETERMINATE"] + oracle_reasons
            if oracle_reasons
            else ["ASSERTION_INDETERMINATE"]
        )
    if oracle_status == "BLOCKED":
        return "BLOCKED", ["CONTRACT_ORACLE_BLOCKED"]
    if oracle_status == "HARNESS_FAILED":
        return "HARNESS_FAILED", ["CONTRACT_ORACLE_HARNESS_FAILED"]
    if oracle_status != "VIOLATION" or activation_status != "ACTIVE":
        raise DeliveryGateV2Error("delivery_oracle_semantics_invalid")

    assertions = [
        validate_assertion_receipt(_dict(value))
        for value in _list(oracle.get("assertions"))
    ]
    violations = [
        value for value in assertions if value.get("status") == "VIOLATION"
    ]
    if (
        not assertions
        or not violations
        or any(
            value.get("status") == "INDETERMINATE"
            or value.get("harness_error") is True
            for value in assertions
        )
    ):
        raise DeliveryGateV2Error("delivery_assertion_semantics_invalid")
    if len(violations) > 1:
        # Multiple violations: select the most specific one as primary.
        # Prefer domain-specific assertions over generic http_status_class.
        _GENERIC_KINDS = {"http_status_class"}
        _specific = [
            v for v in violations
            if _text(v.get("kind")) not in _GENERIC_KINDS
        ]
        violations = [_specific[0]] if _specific else [violations[0]]
    if len(violations) != 1:
        return "BLOCKED", ["AMBIGUOUS_MULTI_ASSERTION_OCCURRENCE"]
    # V1.6.0 P0-16: field-level formal findings require a field oracle trace.
    # HTTP-only assertions remain on the shallow delivery path and are not
    # upgraded to field-oracle formal evidence by status codes alone.
    _primary_violation = violations[0]
    _FIELD_ORACLE_KINDS = {
        "conservation",
        "field_delta",
        "postcondition",
        "state_transition",
        "cross_entity_consistency",
    }
    if _text(_primary_violation.get("kind")) in _FIELD_ORACLE_KINDS:
        if not isinstance(_primary_violation.get("field_oracle_trace"), dict):
            return "BLOCKED", ["FIELD_ORACLE_TRACE_MISSING"]
        _trace = _dict(_primary_violation.get("field_oracle_trace"))
        if not (
            _trace.get("before_values") is not None
            or _trace.get("after_values") is not None
            or _trace.get("actual") is not None
        ):
            return "BLOCKED", ["FIELD_ORACLE_EVIDENCE_INCOMPLETE"]
    contract_ids = {_text(value.get("receipt_id")) for value in contracts}
    observer_ids = {_text(value.get("receipt_id")) for value in observers}
    required = _dict(activation.get("required"))
    if (
        assertion_requires_control(violations[0].get("kind"))
        and not [
            value for value in _list(required.get("control")) if _text(value)
        ]
    ):
        return "BLOCKED", ["ACTOR_SENSITIVE_CONTROL_MISSING"]
    verified = _dict(activation.get("verified_receipt_ids"))
    soft_field_oracle = activation.get("field_oracle_soft_activation") is True
    for kind in ("control", "treatment", "actor", "fixture", "cleanup"):
        required_subjects = {
            _text(value) for value in _list(required.get(kind)) if _text(value)
        }
        verified_ids = {
            _text(value) for value in _list(verified.get(kind)) if _text(value)
        }
        subject_receipts = [
            value
            for value in contracts
            if _text(value.get("kind")) == kind
            and _text(value.get("subject_id")) in required_subjects
        ]
        observed_subjects = [
            _text(value.get("subject_id")) for value in subject_receipts
        ]
        if (
            set(observed_subjects) != required_subjects
            or len(observed_subjects) != len(set(observed_subjects))
        ):
            raise DeliveryGateV2Error(
                f"delivery_{kind}_subject_receipt_mapping_invalid"
            )
        matching = {
            _text(value.get("receipt_id"))
            for value in subject_receipts
        }
        if matching != verified_ids:
            # Soft field-oracle activation intentionally defers cleanup proof so
            # Trace can emit before restoration is sealed. Fail closed as BLOCKED
            # delivery — never crash the campaign, never waive as deliverable.
            # H28: soft ACTIVE may verify a proper SUBSET of required cleanup
            # receipts (partial restoration). Empty-only deferral left that
            # shape raising after H27 restored VIOLATIONs and aborted the scan.
            soft_cleanup_deferred = (
                soft_field_oracle
                and kind == "cleanup"
                and bool(matching)
                and verified_ids.issubset(matching)
            )
            if soft_cleanup_deferred:
                return "BLOCKED", ["CLEANUP_PROOF_DEFERRED_FIELD_ORACLE"]
            if kind == "cleanup":
                # Exact-id mismatch remains non-deliverable, but must not abort
                # the whole campaign via an uncaught DeliveryGateV2Error.
                return "HARNESS_FAILED", [
                    "CLEANUP_ACTIVATION_REFERENCE_MISMATCH"
                ]
            raise DeliveryGateV2Error(
                f"delivery_{kind}_activation_reference_mismatch"
            )
    verified_observers = {
        _text(value) for value in _list(verified.get("observer")) if _text(value)
    }
    # V1.8: The gate enforces reference integrity, not set equality. Soft
    # field-oracle activation may legitimately verify a SUBSET of the required
    # observers (the rest delivered as INDETERMINATE), and the runtime may
    # deliver supplementary observer receipts (authorization_comparison,
    # redundant effect observers) that were never part of the activation
    # contract. Both shapes are valid evidence. What must hold:
    #   1. every required observer was attempted (delivered), and
    #   2. every receipt the activation verified actually exists in the
    #      delivered observer evidence.
    # A verified reference that is missing from delivery remains a hard
    # mismatch (fail closed), mirroring the subject-scoped contract checks.
    required_observer_ids = {
        _text(value) for value in _list(required.get("observer")) if _text(value)
    }
    delivered_observer_ids = {
        _text(value.get("receipt_id")) for value in observers if _text(value.get("receipt_id"))
    }
    delivered_observer_kinds = {
        _text(value.get("observer_id")) for value in observers if _text(value.get("observer_id"))
    }
    if not required_observer_ids.issubset(delivered_observer_kinds):
        _missing_kinds = sorted(required_observer_ids - delivered_observer_kinds)
        raise DeliveryGateV2Error(
            "delivery_observer_activation_reference_mismatch"
            f":missing_required_observers={_missing_kinds[:8]}"
        )
    if not verified_observers.issubset(delivered_observer_ids):
        # Soft field-oracle may activate before observer receipts are sealed.
        if soft_field_oracle and not verified_observers and observer_ids:
            return "BLOCKED", ["OBSERVER_PROOF_DEFERRED_FIELD_ORACLE"]
        _extra = sorted(delivered_observer_ids - verified_observers)
        _missing = sorted(verified_observers - delivered_observer_ids)
        raise DeliveryGateV2Error(
            "delivery_observer_activation_reference_mismatch"
            f":required={sorted(required_observer_ids)[:8]}"
            f":verified={sorted(verified_observers)[:8]}"
            f":delivered_extra={_extra[:8]}:delivered_missing={_missing[:8]}"
        )
    if not contract_ids.issubset(set(execution["observation_receipt_ids"])):
        raise DeliveryGateV2Error("execution_contract_receipts_missing")
    if not observer_ids.issubset(set(execution["observation_receipt_ids"])):
        raise DeliveryGateV2Error("execution_observer_receipts_missing")

    steps = [
        _dict(value) for value in _list(reproduction.get("step_observations"))
        if isinstance(value, dict)
    ]
    step_subjects = {
        (_text(value.get("phase")), _text(value.get("step_id")))
        for value in steps
    }
    for kind in ("control", "treatment"):
        for subject in _list(required.get(kind)):
            if (kind, _text(subject)) not in step_subjects:
                raise DeliveryGateV2Error(
                    f"delivery_{kind}_execution_observation_missing"
                )
            step = next(
                value
                for value in steps
                if _text(value.get("phase")) == kind
                and _text(value.get("step_id")) == _text(subject)
            )
            contract = next(
                (
                    value
                    for value in contracts
                    if _text(value.get("kind")) == kind
                    and _text(value.get("subject_id")) == _text(subject)
                ),
                None,
            )
            if contract is None:
                raise DeliveryGateV2Error(
                    f"delivery_{kind}_request_semantics_contract_missing"
                )
            contract_evidence = _dict(contract.get("evidence"))
            for field in (
                "path_template",
                "request_body_fingerprint",
                "request_semantics_fingerprint",
                "mutation_class",
                "mutation_selector",
                "mutation_operator",
            ):
                if _text(contract_evidence.get(field)) != _text(step.get(field)):
                    raise DeliveryGateV2Error(
                        f"delivery_{kind}_request_semantics_mismatch:{field}"
                    )
    required_actors = {
        _text(value) for value in _list(required.get("actor")) if _text(value)
    }
    observed_actors = {
        _text(value.get("actor_ref")) for value in steps if _text(value.get("actor_ref"))
    }
    if not required_actors.issubset(observed_actors):
        raise DeliveryGateV2Error("delivery_actor_execution_observation_missing")
    control_operations = {
        (_text(value.get("operation_ref")), _text(value.get("method")), _text(value.get("path")))
        for value in steps
        if _text(value.get("phase")) == "control"
    }
    treatment_operations = {
        (_text(value.get("operation_ref")), _text(value.get("method")), _text(value.get("path")))
        for value in steps
        if _text(value.get("phase")) == "treatment"
    }
    if not control_operations or control_operations != treatment_operations:
        # Accept when operation_refs match or when there are no control steps
        ctrl_refs = {r for r, m, p in control_operations} if control_operations else set()
        trt_refs = {r for r, m, p in treatment_operations} if treatment_operations else set()
        if ctrl_refs and trt_refs and ctrl_refs != trt_refs:
            raise DeliveryGateV2Error("control_treatment_operation_mismatch")
    for fixture in (
        value for value in contracts if _text(value.get("kind")) == "fixture"
    ):
        evidence = _dict(fixture.get("evidence"))
        fixture_kind = _text(evidence.get("fixture_kind"))
        if fixture_kind == "runtime_read_binding" and not _text(
            evidence.get("value_fingerprint")
        ):
            raise DeliveryGateV2Error("delivery_fixture_binding_proof_missing")

    cleanup_gate_status, cleanup_reasons, _cleanup_adjudication = (
        _cleanup_gate_decision(execution=execution, contracts=contracts)
    )
    if cleanup_gate_status != "DELIVERABLE":
        return cleanup_gate_status, cleanup_reasons
    if _text(reproduction.get("status")) != "REPRODUCED":
        return "BLOCKED", ["REPRODUCTION_NOT_PROVEN"]
    return "DELIVERABLE", []


def _build_lineage_receipt(
    *,
    identity: dict[str, str],
    execution: dict[str, Any],
    contracts: list[dict[str, Any]],
    observers: list[dict[str, Any]],
    oracle: dict[str, Any],
    reproduction: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": DELIVERY_LINEAGE_RECEIPT_SCHEMA,
        "identity": dict(identity),
        "status": "CONSISTENT",
        "execution_receipt_id": _text(execution.get("receipt_id")),
        "contract_receipt_ids": sorted(
            _text(value.get("receipt_id")) for value in contracts
        ),
        "observer_receipt_ids": sorted(
            _text(value.get("receipt_id")) for value in observers
        ),
        "assertion_receipt_ids": list(oracle.get("assertion_receipt_ids") or []),
        "oracle_receipt_id": _text(oracle.get("receipt_id")),
        "reproduction_receipt_id": _text(reproduction.get("receipt_id")),
        "source_refs_fingerprint": _fingerprint(
            _list(reproduction.get("source_refs"))
        ),
    }
    return _seal(
        payload,
        prefix="lineage_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )


def _reconstruct_finding_from_receipt_chain(
    *,
    execution: dict[str, Any],
    oracle: dict[str, Any],
    reproduction: dict[str, Any],
    contracts: list[dict[str, Any]],
    observers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """原则14 fail-safe: rebuild an evidence-backed finding from a fully
    validated VIOLATION receipt chain when the upstream finalizer attached no
    finding payload.

    The receipt chain has already been adjudicated (execution EXECUTED,
    activation ACTIVE, >=1 assertion VIOLATION, oracle VIOLATION,
    reproduction PROVEN). Reconstructing from that evidence never invents a
    request body, rule, actor, entity/table name, SQL, or impact claim — it
    only re-packages what the governed chain already proved. The marker
    ``reconstructed_from_receipt_chain`` keeps the gap observable (原则3/原则4).
    Returns None only when the chain is not a validated VIOLATION.
    """
    exec_row = _dict(execution)
    oracle_row = _dict(oracle)
    if _text(oracle_row.get("status")) != "VIOLATION":
        return None
    failed = [
        item for item in _list(oracle_row.get("assertions"))
        if _dict(item).get("status") == "VIOLATION"
    ]
    if not failed:
        return None
    evidence_id = _text(exec_row.get("evidence_id")) or _text(exec_row.get("execution_id"))
    if not evidence_id:
        return None
    finding_id = f"finding_{evidence_id}"
    failed_assertions = [_dict(item) for item in failed]
    first = failed_assertions[0]
    risk_family = _text(first.get("family")) or _text(first.get("risk_family")) or ""
    expected = first.get("expected")
    actual = first.get("actual")
    if expected is None:
        expected = _text(first.get("expected_value"))
    if actual is None:
        actual = _text(first.get("actual_value"))
    repro_row = _dict(reproduction)
    repro_steps = []
    if isinstance(repro_row.get("steps"), list):
        for step in repro_row["steps"]:
            s = _dict(step)
            repro_steps.append({
                "step": _text(s.get("step") or s.get("name")),
                "method": _text(s.get("method")),
                "path": _text(s.get("path")),
                "request_hint": _text(s.get("request_hint")),
                "expected": _text(s.get("expected")),
                "observed": _text(s.get("observed")),
            })
    return {
        "finding_id": finding_id,
        "id": finding_id,
        "reconstructed_from_receipt_chain": True,
        "reconstruction_diagnostic": (
            "upstream finalizer dropped finding payload despite a fully "
            "validated VIOLATION receipt chain"
        ),
        "campaign_id": _text(exec_row.get("campaign_id")),
        "candidate_id": _text(exec_row.get("candidate_id")),
        "slice_id": _text(exec_row.get("slice_id")),
        "obligation_id": _text(exec_row.get("obligation_id")),
        "experiment_id": _text(exec_row.get("experiment_id")),
        "execution_id": _text(exec_row.get("execution_id")),
        "evidence_id": evidence_id,
        "mainline_contract_fingerprint": _text(
            exec_row.get("mainline_contract_fingerprint")
        ),
        "risk_family": risk_family,
        "category": _text(first.get("kind")) or "contract_oracle_violation",
        "title": f"validated violation: {_text(first.get('assertion_id')) or risk_family or 'contract-oracle'}",
        "summary": _text(first.get("reason_code")) or "contract oracle violation",
        "description": _text(first.get("assertion_text")) or _text(first.get("reason")),
        "severity": _text(first.get("severity")) or "high",
        "confidence": "medium",
        "verdict": _text(oracle_row.get("verdict")),
        "expected": expected,
        "actual": actual,
        "assertions": failed_assertions,
        "failed_assertions": failed_assertions,
        "oracle": {
            "receipt_id": _text(oracle_row.get("receipt_id")),
            "status": _text(oracle_row.get("status")),
            "verdict": _text(oracle_row.get("verdict")),
        },
        "reproduction": {"status": _text(repro_row.get("status")), "steps": repro_steps},
        "evidence_refs": {
            "execution_receipt_id": _text(exec_row.get("receipt_id")),
            "oracle_receipt_id": _text(oracle_row.get("receipt_id")),
            "reproduction_receipt_id": _text(repro_row.get("receipt_id")),
        },
    }


def build_customer_delivery_gate_receipt_v2(
    *,
    finding: dict[str, Any] | None,
    execution_receipt: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    observer_receipts: list[dict[str, Any]],
    oracle_receipt: dict[str, Any],
    reproduction_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Independently adjudicate a complete receipt bundle."""

    execution = validate_delivery_execution_receipt(execution_receipt)
    contracts, observers, oracle, reproduction = _validate_receipt_collections(
        execution=execution,
        contract_evidence_receipts=contract_evidence_receipts,
        observer_receipts=observer_receipts,
        oracle_receipt=oracle_receipt,
        reproduction_receipt=reproduction_receipt,
    )
    # Adjudication has exactly one authority: the validated receipt chain.
    # A mutable field on the caller-supplied finding dict must never be able to
    # short-circuit it (see the module docstring).  A BLOCKED oracle stays
    # BLOCKED: "the treatment returned 2xx" is an observation, not an activated
    # contract, and it cannot substitute for control/cleanup/reproduction proof.
    status, reason_codes = _validate_active_chain(
        execution=execution,
        contracts=contracts,
        observers=observers,
        oracle=oracle,
        reproduction=reproduction,
    )
    _, _, cleanup_adjudication = _cleanup_gate_decision(
        execution=execution,
        contracts=contracts,
    )
    deliverable = status == "DELIVERABLE"
    finding_row = _dict(finding)
    finding_id = _text(finding_row.get("finding_id") or finding_row.get("id"))
    if deliverable and not finding_id:
        # 原则14: a fully-validated VIOLATION receipt chain must never be silently
        # dropped merely because the upstream finalizer failed to attach a finding
        # payload (observed for some authorization/isolation/visibility/idempotency/
        # validation obligations where the executor augments but never instantiates
        # the finding). Reconstruct an evidence-backed finding from the validated
        # receipt chain so the proven violation is delivered, and surface a clear
        # diagnostic pointing at the upstream drop (原则3/原则4: make it observable).
        reconstructed = _reconstruct_finding_from_receipt_chain(
            execution=execution,
            oracle=oracle,
            reproduction=reproduction,
            contracts=contracts,
            observers=observers,
        )
        if reconstructed:
            finding = reconstructed
            finding_row = reconstructed
            finding_id = _text(reconstructed.get("finding_id") or reconstructed.get("id"))
            import sys as _sys_rec

            _sys_rec.stderr.write(
                "[DELIVERY-RECOVERY] reconstructed finding for "
                f"obligation={_text(identity.get('obligation_id'))} "
                f"experiment={_text(identity.get('experiment_id'))}: "
                "upstream finalizer dropped finding payload despite validated VIOLATION\n"
            )
        else:
            status = "BLOCKED"
            reason_codes = ["VIOLATION_FINDING_MISSING"]
            deliverable = False
    if deliverable:
        for field in (
            "campaign_id",
            "candidate_id",
            "slice_id",
            "obligation_id",
            "experiment_id",
            "execution_id",
            "evidence_id",
        ):
            if _text(finding_row.get(field)) != _text(execution.get(field)):
                raise DeliveryGateV2Error(f"finding_execution_identity_mismatch:{field}")
        observed_mainline = _text(
            _dict(finding_row.get("mainline_run")).get("contract_fingerprint")
        )
        if observed_mainline != _text(
            execution.get("mainline_contract_fingerprint")
        ):
            raise DeliveryGateV2Error("finding_mainline_identity_mismatch")
    identity = _identity_from_execution(
        execution,
        finding_id=finding_id if deliverable else "",
    )
    _validate_identity(identity, finding_required=deliverable)
    payload_fingerprint = (
        finding_payload_fingerprint(finding_row) if deliverable else ""
    )
    lineage = _build_lineage_receipt(
        identity=identity,
        execution=execution,
        contracts=contracts,
        observers=observers,
        oracle=oracle,
        reproduction=reproduction,
    )
    refs = {
        "execution": _receipt_ref(execution),
        "actors": [
            _receipt_ref(value)
            for value in contracts
            if _text(value.get("kind")) == "actor"
        ],
        "fixtures": [
            _receipt_ref(value)
            for value in contracts
            if _text(value.get("kind")) == "fixture"
        ],
        "controls": [
            _receipt_ref(value)
            for value in contracts
            if _text(value.get("kind")) == "control"
        ],
        "treatments": [
            _receipt_ref(value)
            for value in contracts
            if _text(value.get("kind")) == "treatment"
        ],
        "observers": [_receipt_ref(value) for value in observers],
        "assertions": [
            _receipt_ref(value)
            for value in _list(oracle.get("assertions"))
            if isinstance(value, dict)
        ],
        "oracle": _receipt_ref(oracle),
        "reproduction": _receipt_ref(reproduction),
        "cleanup": [
            _receipt_ref(value)
            for value in contracts
            if _text(value.get("kind")) == "cleanup"
        ],
        "lineage": _receipt_ref(lineage),
    }
    adjudication = {
        "execution": "EXECUTED",
        "activation": _text(
            _dict(oracle.get("activation_receipt")).get("status")
        ),
        "assertion": (
            "VIOLATION"
            if any(
                _dict(value).get("status") == "VIOLATION"
                for value in _list(oracle.get("assertions"))
            )
            else "INDETERMINATE"
            if any(
                _dict(value).get("status") == "INDETERMINATE"
                for value in _list(oracle.get("assertions"))
            )
            else "PASS"
        ),
        "oracle": _text(oracle.get("status")),
        "reproduction": _text(reproduction.get("status")),
        "cleanup": cleanup_adjudication,
        "lineage": "CONSISTENT",
    }
    # The adjudication block is a transcript of the observed receipt chain and is
    # never rewritten.  Overwriting it would seal a receipt that asserts an
    # execution history the referenced receipts contradict, and because
    # validate_customer_delivery_gate_receipt_v2 only checks the block against
    # the expected clean values, such a receipt would be permanently
    # self-consistent and undetectable by receipt validation.
    reasons = sorted(set(_text(value) for value in reason_codes if _text(value)))
    input_fingerprint = _fingerprint({
        "identity": identity,
        "finding_payload_fingerprint": payload_fingerprint,
        "receipt_refs": refs,
    })
    payload = {
        "schema_version": CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
        "status": status,
        "reason_code": reasons[0] if reasons else "",
        "reason_codes": reasons,
        "identity": identity,
        "finding_payload_fingerprint": payload_fingerprint,
        "receipt_refs": refs,
        "adjudication": adjudication,
        "cost_coverage_status": _text(
            execution.get("cost_coverage_status") or "UNKNOWN"
        ).upper(),
        "input_fingerprint": input_fingerprint,
    }
    if status == "HARNESS_FAILED":
        reason_detail = _oracle_harness_reason_detail(oracle)
        if reason_detail:
            payload["reason_detail"] = reason_detail
    gate = _seal(
        payload,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )
    return validate_customer_delivery_gate_receipt_v2(
        gate,
        finding=finding_row if deliverable else None,
    )


def validate_customer_delivery_gate_receipt_v2(
    receipt: dict[str, Any],
    *,
    finding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _dict(receipt)
    # The validator is a deterministic pure function of (receipt, finding):
    # identical inputs always yield the identical validated receipt, so the
    # result is cached by the content address of both inputs.  A content
    # change changes the address and forces recomputation; failed
    # validations are never cached and re-raise on every call, so no
    # fail-closed gate is relaxed.  Within one run every occurrence's gate is
    # validated many times (formal findings path runs ~7 times per run), and
    # each validation re-fingerprints the whole finding payload, which is why
    # this hot path is memoized instead of re-deriving everything.
    finding_key = None
    if finding is not None:
        finding_row = _dict(finding)
        finding_id = _text(finding_row.get("finding_id") or finding_row.get("id"))
        try:
            finding_fp = finding_payload_fingerprint(finding_row)
        except DeliveryGateV2Error:
            # Empty/invalid finding: the validator below decides (it may
            # raise finding_payload_missing); never cache a decision made
            # without the fingerprint.
            finding_fp = ""
        finding_key = (finding_id, finding_fp)
    cache_key = (finding_key, content_fingerprint(row))
    cached = GATE_VALIDATION_CACHE.get(cache_key)
    if cached is not _MISSING:
        return copy.deepcopy(cached)
    required = {
        "schema_version",
        "status",
        "reason_code",
        "reason_codes",
        "identity",
        "finding_payload_fingerprint",
        "receipt_refs",
        "adjudication",
        "cost_coverage_status",
        "input_fingerprint",
        "gate_receipt_id",
        "output_fingerprint",
    }
    actual_fields = set(row)
    if actual_fields == required | {"reason_detail"}:
        if _text(row.get("status")).upper() != "HARNESS_FAILED":
            raise DeliveryGateV2Error("delivery_gate_reason_detail_status_invalid")
        reason_detail = row.get("reason_detail")
        if (
            not isinstance(reason_detail, str)
            or not reason_detail.strip()
            or len(reason_detail) > 1000
        ):
            raise DeliveryGateV2Error("delivery_gate_reason_detail_invalid")
    else:
        _strict_fields(row, required, code="delivery_gate_fields_invalid")
    if row.get("schema_version") != CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
        raise DeliveryGateV2Error("delivery_gate_schema_invalid")
    status = _text(row.get("status"))
    if status not in _GATE_STATUSES:
        raise DeliveryGateV2Error("delivery_gate_status_invalid")
    reason_codes = row.get("reason_codes")
    if not isinstance(reason_codes, list):
        raise DeliveryGateV2Error("delivery_gate_reason_codes_invalid")
    normalized_reasons = sorted(set(_text(value) for value in reason_codes if _text(value)))
    if normalized_reasons != reason_codes:
        raise DeliveryGateV2Error("delivery_gate_reason_codes_not_canonical")
    if _text(row.get("reason_code")) != (reason_codes[0] if reason_codes else ""):
        raise DeliveryGateV2Error("delivery_gate_reason_code_mismatch")
    deliverable = status == "DELIVERABLE"
    identity = _validate_identity(
        _dict(row.get("identity")),
        finding_required=deliverable,
    )
    if deliverable and reason_codes:
        raise DeliveryGateV2Error("deliverable_gate_has_rejection_reason")
    if not deliverable and not reason_codes:
        raise DeliveryGateV2Error("nondeliverable_gate_reason_missing")
    payload_fingerprint = _text(row.get("finding_payload_fingerprint"))
    if deliverable and not payload_fingerprint:
        raise DeliveryGateV2Error("finding_payload_fingerprint_missing")
    if not deliverable and payload_fingerprint:
        raise DeliveryGateV2Error("nondeliverable_finding_fingerprint_present")
    if deliverable and finding is not None:
        if finding_payload_fingerprint(_dict(finding)) != payload_fingerprint:
            # TEMP-DIAG (fingerprint mismatch root-cause hunt): report the
            # finding's identity, redaction idempotency, and any sensitive
            # markers so the mismatching payload shape is visible.
            try:
                import json as _json

                from .artifact_redactor import redact_artifact

                _diag = _dict(finding)
                _raw = _json.dumps(_diag, ensure_ascii=False, default=str)
                _red1, _ = redact_artifact(_diag)
                _red2, _ = redact_artifact(_diag)
                _sensitive = [k for k in ("token", "authorization", "bearer", "password", "secret")
                              if k in _raw.lower()]
                _raw_keys = sorted(_diag.keys())
                _red_keys = sorted(_red1.keys()) if isinstance(_red1, dict) else []
                _recomputed = finding_payload_fingerprint(_diag)
                print(
                    f"FINGERPRINT_DIAG finding_id={_text(_diag.get('finding_id') or _diag.get('id'))} "
                    f"expected={payload_fingerprint[:16]} recomputed={_recomputed[:16]} "
                    f"redact_idempotent={_json.dumps(_red1, ensure_ascii=False, sort_keys=True, default=str) == _json.dumps(_red2, ensure_ascii=False, sort_keys=True, default=str)} "
                    f"raw_chars={len(_raw)} sensitive={','.join(_sensitive) or 'none'} "
                    f"raw_keys={','.join(_raw_keys)[:300]} red_keys={','.join(_red_keys)[:300]} "
                    f"redact_added_keys={','.join(sorted(set(_red_keys) - set(_raw_keys)))[:200]} "
                    f"redact_dropped_keys={','.join(sorted(set(_raw_keys) - set(_red_keys)))[:200]}",
                    flush=True,
                )
            except Exception as _diag_exc:
                print(f"FINGERPRINT_DIAG_FAILED: {_diag_exc}", flush=True)
            raise DeliveryGateV2Error("finding_payload_fingerprint_mismatch")
        if _text(_dict(finding).get("finding_id") or _dict(finding).get("id")) != identity["finding_id"]:
            raise DeliveryGateV2Error("finding_identity_mismatch")
    refs = _dict(row.get("receipt_refs"))
    ref_keys = {
        "execution",
        "actors",
        "fixtures",
        "controls",
        "treatments",
        "observers",
        "assertions",
        "oracle",
        "reproduction",
        "cleanup",
        "lineage",
    }
    if set(refs) != ref_keys:
        raise DeliveryGateV2Error("delivery_gate_receipt_refs_invalid")
    for key in ("execution", "oracle", "reproduction", "lineage"):
        ref = _dict(refs.get(key))
        if set(ref) != {"receipt_id", "fingerprint"} or not all(
            _text(value) for value in ref.values()
        ):
            raise DeliveryGateV2Error("delivery_gate_receipt_ref_invalid")
    for key in (
        "actors",
        "fixtures",
        "controls",
        "treatments",
        "observers",
        "assertions",
        "cleanup",
    ):
        if not isinstance(refs.get(key), list):
            raise DeliveryGateV2Error("delivery_gate_receipt_ref_list_invalid")
    adjudication = _dict(row.get("adjudication"))
    if set(adjudication) != {
        "execution",
        "activation",
        "assertion",
        "oracle",
        "reproduction",
        "cleanup",
        "lineage",
    }:
        raise DeliveryGateV2Error("delivery_gate_adjudication_invalid")
    if deliverable:
        expected_adjudication = {
            "execution": "EXECUTED",
            "activation": "ACTIVE",
            "assertion": "VIOLATION",
            "oracle": "VIOLATION",
            "reproduction": "REPRODUCED",
            "cleanup": _text(adjudication.get("cleanup")),
            "lineage": "CONSISTENT",
        }
        if adjudication != expected_adjudication or adjudication["cleanup"] not in {
            "COMPLETED",
            "NOT_REQUIRED",
            "RESIDUE_ACCEPTED",
        }:
            raise DeliveryGateV2Error("deliverable_gate_adjudication_invalid")
    cost_status = _text(row.get("cost_coverage_status"))
    if cost_status not in _COST_COVERAGE_STATUSES:
        raise DeliveryGateV2Error("delivery_gate_cost_coverage_invalid")
    expected_input_fingerprint = _fingerprint({
        "identity": identity,
        "finding_payload_fingerprint": payload_fingerprint,
        "receipt_refs": refs,
    })
    if _text(row.get("input_fingerprint")) != expected_input_fingerprint:
        raise DeliveryGateV2Error("delivery_gate_input_fingerprint_invalid")
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"gate_receipt_id", "output_fingerprint"}
    }
    expected = _seal(
        unsigned,
        prefix="gate_",
        id_field="gate_receipt_id",
        fingerprint_field="output_fingerprint",
    )
    if row != expected:
        raise DeliveryGateV2Error("delivery_gate_output_fingerprint_invalid")
    validated = dict(expected)
    GATE_VALIDATION_CACHE.put(cache_key, validated)
    return validated


def validate_customer_delivery_gate_bundle(
    gate_receipt: dict[str, Any],
    *,
    finding: dict[str, Any] | None,
    execution_receipt: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    observer_receipts: list[dict[str, Any]],
    oracle_receipt: dict[str, Any],
    reproduction_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild the full adjudication and require byte-for-byte equality."""

    validated = validate_customer_delivery_gate_receipt_v2(
        gate_receipt,
        finding=finding if _text(_dict(gate_receipt).get("status")) == "DELIVERABLE" else None,
    )
    rebuilt = build_customer_delivery_gate_receipt_v2(
        finding=finding,
        execution_receipt=execution_receipt,
        contract_evidence_receipts=contract_evidence_receipts,
        observer_receipts=observer_receipts,
        oracle_receipt=oracle_receipt,
        reproduction_receipt=reproduction_receipt,
    )
    if validated != rebuilt:
        raise DeliveryGateV2Error("delivery_gate_bundle_mismatch")
    return validated


# ── Merged from customer_delivery_gate.py v1 ──

CUSTOMER_READY_MIN_EVIDENCE_SCORE = 90
_ALLOWED_FINAL_REVIEW_STATUSES = {"PENDING_REVIEW", "VALIDATED_CANDIDATE", "CUSTOMER_READY"}
_BLOCKED_LANE_MARKERS = {
    "route_blocked",
    "auth_blocked",
    "environment_blocked",
    "coverage_gap",
    "validation_lead",
    "not_reproduced",
}
_SYNTHETIC_MARKERS = {"simulation", "simulated", "demo", "synthetic", "mock"}
_MAINLINE_IDENTITY_FIELDS = (
    "candidate_id",
    "slice_id",
    "obligation_id",
    "experiment_id",
    "execution_id",
    "evidence_id",
    "finding_id",
)

REJECTION_REASON_EXPLANATIONS: dict[str, dict[str, str]] = {
    "INVALID_FINDING_PAYLOAD": {
        "label": "结果结构异常",
        "detail": "当前 finding 不是合法对象，无法进入客户交付。",
        "next_action": "检查上游结果格式化与序列化流程。",
    },
    "NOT_MARKED_FOR_DEFECT_DELIVERY": {
        "label": "未标记为客户缺陷轨道",
        "detail": "当前结果仍属于内部线索或其他轨道。",
        "next_action": "完成补证并重新通过客户交付 Gate。",
    },
    "BUG_STATUS_NOT_REPRODUCED": {
        "label": "缺陷状态不是已复现",
        "detail": "没有达到 reproduced 状态，不能对客户声称为 Bug。",
        "next_action": "补跑复现步骤并记录请求、响应、断言与时间戳。",
    },
    "GATE_NOT_PASSED": {
        "label": "证据门控未通过",
        "detail": "上游 Gate 未确认该结果具备可交付证据。",
        "next_action": "查看 gate_failures 或 evidence_status，补齐缺失证据。",
    },
    "IDENTITY_CHAIN_INCOMPLETE": {
        "label": "主链身份不完整",
        "detail": "finding 没有完整关联 candidate、slice、obligation、experiment、execution 和 evidence 身份。",
        "next_action": "让候选统一经过 Experiment Executor 并持久化全链路身份后重新评估。",
    },
    "SYNTHETIC_OR_DEMO_EVIDENCE": {
        "label": "证据来源不真实",
        "detail": "结果包含模拟、演示、mock 或 synthetic 信号。",
        "next_action": "在客户测试环境中重新执行真实请求或浏览器复现。",
    },
    "NOT_EXECUTED": {
        "label": "尚未真实执行",
        "detail": "当前只有计划、候选或线索，没有真实运行时执行结果。",
        "next_action": "执行对应 API/页面路径，并保存状态码、响应体和执行时间。",
    },
    "NOT_CONFIRMED": {
        "label": "尚未形成确认结论",
        "detail": "当前仍需人工或二次验证确认。",
        "next_action": "完成语义验证和业务证据验证后再提交客户页。",
    },
    "EVIDENCE_CONSISTENCY_REJECTED": {
        "label": "声明与证据不一致",
        "detail": "当前证据不能支撑缺陷声明，或证据已被判定缺失。",
        "next_action": "重新绑定 finding 与真实请求响应，确保方法、路径、实体和断言一致。",
    },
    "EVIDENCE_QUALITY_NOT_VALIDATED": {
        "label": "证据质量未达标",
        "detail": "证据质量不是 validated，或分数低于客户交付阈值。",
        "next_action": "补齐文档来源、真实响应、断言、日志、DB 快照或复现资产。",
    },
    "BUSINESS_EVIDENCE_NOT_VALIDATED": {
        "label": "业务证据未验证通过",
        "detail": "语义结论、业务证据状态、最终评审状态或 missing requirements 未达标。",
        "next_action": "补齐 before/after、业务实体绑定、规则来源和复现流。",
    },
    "MISSING_REAL_REPLAY_ASSET": {
        "label": "缺少真实复现资产",
        "detail": "没有可回放的真实方法、路径和 HAR/响应证据。",
        "next_action": "生成真实 curl/HAR/Playwright 复现资产并绑定到该 finding。",
    },
    "MISSING_CUSTOMER_FACING_HARD_EVIDENCE": {
        "label": "缺少客户可核验证据",
        "detail": "请求、响应、失败断言、时间戳或真实证据标记不完整。",
        "next_action": "补齐 request_raw、response_raw、expected/actual 断言和 timestamp。",
    },
    "CLEANUP_NOT_SUCCEEDED": {
        "label": "写探测清理未成功",
        "detail": "受治理写探测声明了 cleanup，但清理没有成功完成，当前结果不能进入客户缺陷清单。",
        "next_action": "恢复测试环境、完成补偿清理并生成新的 cleanup 审计收据后重新评估。",
    },
    "CLEANUP_EVIDENCE_MISSING": {
        "label": "缺少写探测清理证据",
        "detail": "结果来自写方法，但证据链没有声明 cleanup 结果或显式只读语义，不能证明受治理写生命周期完整。",
        "next_action": "由 sandbox executor 绑定 cleanup 状态、收据和审计记录；只读 POST 必须显式标记 read_only。",
    },
    "CLEANUP_RECEIPT_MISSING": {
        "label": "缺少清理审计收据",
        "detail": "写探测 cleanup 已声明完成，但没有绑定不可变 cleanup receipt。",
        "next_action": "补齐 sandbox executor cleanup receipt，并将其绑定到 finding 证据链。",
    },
    "BLOCKED_AUTH_BLOCKED": {
        "label": "认证或权限配置阻断",
        "detail": "当前响应只能说明认证/权限配置阻断，不能证明业务缺陷。",
        "next_action": "补充真实测试账号、角色和 token 后重新执行。",
    },
    "BLOCKED_ROUTE_BLOCKED": {
        "label": "路由或网关阻断",
        "detail": "请求没有到达目标业务接口，不能证明当前业务缺陷。",
        "next_action": "检查服务映射、网关路由和目标 base URL。",
    },
    "BLOCKED_ENVIRONMENT_BLOCKED": {
        "label": "环境不可达或被拦截",
        "detail": "目标环境阻断了复现动作，不能作为已复现缺陷。",
        "next_action": "修复网络、白名单、测试环境地址或服务启动状态。",
    },
    "BLOCKED_COVERAGE_GAP": {
        "label": "覆盖缺口",
        "detail": "当前只是覆盖不足或待执行路径，不是已复现缺陷。",
        "next_action": "补跑对应场景并沉淀真实运行时证据。",
    },
    "BLOCKED_NOT_REPRODUCED": {
        "label": "未复现",
        "detail": "已执行但没有触发可证明的异常。",
        "next_action": "调整测试数据、账号、状态前置条件后定向复测。",
    },
    "BLOCKED_VALIDATION_LEAD": {
        "label": "仍是验证线索",
        "detail": "当前可以作为内部验证线索，但不能进入客户缺陷清单。",
        "next_action": "按缺失证据列表继续补证。",
    },
}








def _v1_upper(value: Any) -> str:
    return _text(value).upper()


def _v1_lower(value: Any) -> str:
    return _text(value).lower()


def _v1_number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if parsed == parsed else default


def has_validated_evidence_quality(item: dict[str, Any]) -> bool:
    quality = _dict(item.get("evidence_quality"))
    return (
        _v1_lower(quality.get("level")) == "validated"
        and _v1_number(quality.get("score")) >= CUSTOMER_READY_MIN_EVIDENCE_SCORE
        and bool(quality.get("can_reproduce"))
    )


def has_passed_business_evidence_status(item: dict[str, Any]) -> bool:
    status = _dict(item.get("evidence_status"))
    if not status:
        return False
    if _v1_upper(status.get("semantic_verdict")) != "SEMANTIC_CONFIRMED":
        return False
    if _v1_upper(status.get("business_evidence_status")) != "VALIDATED":
        return False
    if _v1_upper(status.get("final_review_status")) not in _ALLOWED_FINAL_REVIEW_STATUSES:
        return False
    return len(_list(status.get("missing_requirements"))) == 0


def has_complete_mainline_identity(item: dict[str, Any]) -> bool:
    return all(_text(item.get(field)) for field in _MAINLINE_IDENTITY_FIELDS)


def has_legacy_runtime_identity(item: dict[str, Any]) -> bool:
    """Accept legacy V12 runtime findings when their evidence chain is auditable."""

    if not _text(item.get("evidence_id")):
        return False
    if not _text(item.get("source") or item.get("origin")):
        return False
    raw_evidence = _dict(item.get("raw_evidence"))
    if not raw_evidence.get("has_real_evidence"):
        return False
    if not _text(raw_evidence.get("timestamp") or item.get("timestamp")):
        return False
    request_raw = _dict(raw_evidence.get("request_raw"))
    response_raw = _dict(raw_evidence.get("response_raw"))
    if not (_text(request_raw.get("method")) and _text(request_raw.get("path"))):
        return False
    db_snapshot = _dict(raw_evidence.get("db_snapshot"))
    if not (
        response_raw.get("status_code")
        or response_raw.get("body")
        or db_snapshot.get("status") == "captured"
    ):
        return False
    execution_trace = _dict(raw_evidence.get("execution_trace"))
    if execution_trace and _text(execution_trace.get("evidence_id")):
        return _text(execution_trace.get("evidence_id")) == _text(item.get("evidence_id"))
    return True


def has_traceable_delivery_identity(item: dict[str, Any]) -> bool:
    return has_complete_mainline_identity(item) or has_legacy_runtime_identity(item)


def has_explicit_failure_assertion(item: dict[str, Any]) -> bool:
    if _list(item.get("failed_assertions")):
        return True
    comparison = _dict(item.get("expected_actual_comparison"))
    if _text(comparison.get("difference")):
        return True
    expected = _text(item.get("expected") or comparison.get("expected"))
    actual = _text(item.get("actual") or comparison.get("actual"))
    return bool(expected and actual and expected != actual)


def has_customer_facing_hard_evidence(item: dict[str, Any]) -> bool:
    raw_evidence = _dict(item.get("raw_evidence"))
    reproduction = _dict(item.get("reproduction"))
    request_raw = _dict(raw_evidence.get("request_raw"))
    response_raw = _dict(raw_evidence.get("response_raw"))
    db_snapshot = _dict(raw_evidence.get("db_snapshot"))
    har = _dict(reproduction.get("har_evidence"))
    if not har:
        har = _dict(item.get("har_evidence"))

    has_request = bool(request_raw.get("method") and request_raw.get("path")) or bool(
        reproduction.get("method") and reproduction.get("path")
    )
    has_response = bool(
        response_raw.get("status_code")
        or response_raw.get("body")
        or db_snapshot.get("before")
        or db_snapshot.get("after")
        or db_snapshot.get("assertion")
        or har.get("status_code")
        or har.get("response_body")
    )
    has_timestamp = bool(raw_evidence.get("timestamp") or item.get("timestamp"))
    has_real_evidence = bool(raw_evidence.get("has_real_evidence") or har)

    return has_request and has_response and has_explicit_failure_assertion(item) and has_timestamp and has_real_evidence


def has_customer_replay_asset(item: dict[str, Any]) -> bool:
    raw_evidence = _dict(item.get("raw_evidence"))
    reproduction = _dict(item.get("reproduction"))
    db_snapshot = _dict(raw_evidence.get("db_snapshot"))
    har = _dict(reproduction.get("har_evidence"))
    if not har:
        har = _dict(item.get("har_evidence"))
    method = _text(reproduction.get("method") or item.get("repro_method")).upper()
    path = _text(reproduction.get("path") or item.get("repro_path"))
    if not method or not path:
        request_raw = _dict(raw_evidence.get("request_raw"))
        method = _text(request_raw.get("method")).upper()
        path = _text(request_raw.get("path"))
    if not method or not path:
        return False
    if bool(reproduction.get("is_synthetic")):
        return False
    if har.get("status_code") or har.get("response_body"):
        return True
    return bool(
        (db_snapshot.get("before") and db_snapshot.get("after"))
        or db_snapshot.get("assertion")
    )


def governed_cleanup_rejection_reasons(item: dict[str, Any]) -> list[str]:
    """Fail closed when a write-shaped finding omits its cleanup contract."""

    evidence = _dict(item.get("evidence"))
    raw_evidence = _dict(item.get("raw_evidence"))
    sandbox_write = _dict(raw_evidence.get("sandbox_write"))
    cleanup = {
        **_dict(sandbox_write.get("cleanup")),
        **_dict(evidence.get("cleanup")),
    }
    reproduction = _dict(item.get("reproduction"))
    request_raw = _dict(raw_evidence.get("request_raw"))
    method = _text(
        reproduction.get("method")
        or item.get("repro_method")
        or request_raw.get("method")
    ).upper()
    # GET/HEAD are inherently read-only. A write method cannot become read-only
    # through a label; its governed lifecycle must prove cleanup or no mutation.
    if method in {"GET", "HEAD"}:
        return []
    if not cleanup:
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return ["CLEANUP_EVIDENCE_MISSING"]
        return []
    status = _v1_lower(cleanup.get("status"))
    if status == "not_required":
        strategy = _v1_lower(
            cleanup.get("strategy")
            or cleanup.get("reason")
            or _dict(sandbox_write.get("cleanup")).get("strategy")
            or _dict(sandbox_write.get("cleanup")).get("reason")
        )
        rejection_proved_unchanged = strategy in {
            "rejected_write_observer_unchanged",
            "setup_rejected_observer_unchanged",
        }
        # The receipt-chain delivery gate is the sole adjudication authority:
        # a DELIVERABLE gate with adjudication.cleanup == NOT_REQUIRED has
        # already proven no dirty state. Its projection carries the attested
        # reason code plus a receipt reference instead of a legacy strategy
        # label; accepting it keeps the field re-check consistent with the
        # formal gate instead of rejecting receipt-proven findings.
        receipt_attested = (
            _text(cleanup.get("reason_code")).upper()
            == "CLEANUP_NOT_REQUIRED_RECEIPT_ATTESTED"
            and bool(_text(cleanup.get("receipt_ref")))
        )
        if not rejection_proved_unchanged and not receipt_attested:
            return ["CLEANUP_NOT_SUCCEEDED"]
    elif status == "residue_accepted":
        # Accepted residue on a declared non-production target is a terminal
        # hygiene state, never a delivery rejection (原则14). Require the
        # attested receipt reference so the leftover stays traceable.
        if not (
            _text(cleanup.get("reason_code")).upper()
            == "CLEANUP_RESIDUE_RECEIPT_ATTESTED"
            and bool(_text(cleanup.get("receipt_ref")))
        ):
            return ["CLEANUP_NOT_SUCCEEDED"]
    elif status not in {"completed", "success", "succeeded"}:
        return ["CLEANUP_NOT_SUCCEEDED"]
    if not _text(cleanup.get("receipt_ref")):
        return ["CLEANUP_RECEIPT_MISSING"]
    return []


def customer_delivery_rejection_reasons(item: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not isinstance(item, dict):
        return ["INVALID_FINDING_PAYLOAD"]
    if item.get("customer_delivery_status") not in {None, "defect"}:
        reasons.append("NOT_MARKED_FOR_DEFECT_DELIVERY")
    if _text(item.get("bug_status")) != "reproduced":
        reasons.append("BUG_STATUS_NOT_REPRODUCED")
    if not bool(item.get("gate_passed")):
        reasons.append("GATE_NOT_PASSED")
    if not has_traceable_delivery_identity(item):
        reasons.append("IDENTITY_CHAIN_INCOMPLETE")

    execution_status = _v1_lower(item.get("execution_status"))
    confirmation_status = _v1_lower(item.get("confirmation_status"))
    evidence_level = _v1_lower(item.get("evidence_level"))
    execution_source = _v1_lower(item.get("execution_source"))
    if any(marker in evidence_level or marker in execution_source for marker in _SYNTHETIC_MARKERS):
        reasons.append("SYNTHETIC_OR_DEMO_EVIDENCE")
    if execution_status and execution_status != "executed":
        reasons.append("NOT_EXECUTED")
    if confirmation_status and confirmation_status not in {"confirmed", "validated_candidate"}:
        reasons.append("NOT_CONFIRMED")

    consistency = _dict(item.get("evidence_consistency"))
    if _v1_lower(consistency.get("verdict")) in {"rejected", "missing"}:
        reasons.append("EVIDENCE_CONSISTENCY_REJECTED")

    lane = " ".join(
        _v1_lower(item.get(key))
        for key in ("value_lane", "_value_lane", "execution_block", "block_reason")
    )
    for marker in sorted(_BLOCKED_LANE_MARKERS):
        if marker in lane:
            reasons.append(f"BLOCKED_{marker.upper()}")

    if not has_validated_evidence_quality(item):
        reasons.append("EVIDENCE_QUALITY_NOT_VALIDATED")
    if not has_passed_business_evidence_status(item):
        reasons.append("BUSINESS_EVIDENCE_NOT_VALIDATED")
    if not has_customer_replay_asset(item):
        reasons.append("MISSING_REAL_REPLAY_ASSET")
    if not has_customer_facing_hard_evidence(item):
        reasons.append("MISSING_CUSTOMER_FACING_HARD_EVIDENCE")
    reasons.extend(governed_cleanup_rejection_reasons(item))
    return reasons


def build_customer_delivery_gate_receipt(
    item: dict[str, Any] | None,
    *,
    obligation_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Return the Delivery Gate terminal receipt without copying evidence payloads."""

    resolved_obligation_id = _text(obligation_id)
    if not resolved_obligation_id:
        raise ValueError("delivery gate obligation_id is required")
    resolved_execution_id = _text(execution_id)
    if not resolved_execution_id:
        raise ValueError("delivery gate execution_id is required")
    if item is None:
        reasons = ["ORACLE_NOT_VIOLATED"]
        finding_id = ""
        oracle_receipt_id = ""
        evidence_id = ""
        cost_coverage_status = "UNKNOWN"
    elif not isinstance(item, dict):
        raise ValueError("delivery gate finding must be an object or None")
    else:
        item_obligation_id = _text(item.get("obligation_id"))
        if item_obligation_id and item_obligation_id != resolved_obligation_id:
            raise ValueError("delivery gate obligation identity mismatch")
        item_execution_id = _text(item.get("execution_id"))
        if item_execution_id and item_execution_id != resolved_execution_id:
            raise ValueError("delivery gate execution identity mismatch")
        reasons = customer_delivery_rejection_reasons(item)
        finding_id = _text(item.get("finding_id") or item.get("id")) if not reasons else ""
        if not reasons and not finding_id:
            raise ValueError("deliverable finding_id is required")
        oracle_receipt_id = _text(
            item.get("oracle_receipt_id")
            or _dict(item.get("oracle")).get("receipt_id")
        )
        evidence_id = _text(item.get("evidence_id"))
        cost_coverage_status = _text(item.get("cost_coverage_status") or "UNKNOWN").upper()
    if cost_coverage_status not in {"MEASURED", "PARTIAL", "UNKNOWN"}:
        raise ValueError("delivery gate cost coverage status is invalid")
    status = "REJECTED" if reasons else "DELIVERABLE"
    payload: dict[str, Any] = {
        "schema_version": LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
        "obligation_id": resolved_obligation_id,
        "execution_id": resolved_execution_id,
        "status": status,
        "reason_code": reasons[0] if reasons else "",
        "reason_codes": list(reasons),
        "finding_id": finding_id,
        "oracle_receipt_id": oracle_receipt_id,
        "evidence_id": evidence_id,
        "cost_coverage_status": cost_coverage_status,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    payload["gate_receipt_id"] = "gate_" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:32]
    payload["output_fingerprint"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    return payload


def explain_rejection_reason(reason: str) -> dict[str, str]:
    return REJECTION_REASON_EXPLANATIONS.get(reason, {
        "label": reason,
        "detail": "未知 Gate 拒绝原因。",
        "next_action": "检查后端 Gate 配置并补充解释文案。",
    })


def customer_delivery_rejection_explanations(item: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"code": reason, **explain_rejection_reason(reason)}
        for reason in customer_delivery_rejection_reasons(item)
    ]


def is_customer_deliverable_defect(item: dict[str, Any]) -> bool:
    return not customer_delivery_rejection_reasons(item)


def split_customer_delivery_tracks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    defects: list[dict[str, Any]] = []
    clues: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rejection_reasons = customer_delivery_rejection_reasons(item)
        if not rejection_reasons:
            defects.append({
                **item,
                "delivery_track": "defect",
                "customer_delivery_status": "defect",
                "customer_delivery_label": "客户可交付缺陷",
                "customer_visible": True,
                "customer_delivery_gate_reasons": [],
                "customer_delivery_gate_explanations": [],
            })
        else:
            clues.append({
                **item,
                "delivery_track": "clue",
                "customer_delivery_status": "clue",
                "customer_delivery_label": "内部待验证线索",
                "customer_visible": False,
                "customer_delivery_gate_reasons": rejection_reasons,
                "customer_delivery_gate_explanations": [
                    {"code": reason, **explain_rejection_reason(reason)}
                    for reason in rejection_reasons
                ],
            })
    return defects, clues


def apply_governed_campaign_cleanup(
    items: list[dict[str, Any]],
    cleanup_receipt: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compatibility projection that never changes a terminal Gate decision.

    Campaign reset may prove the environment clean for subsequent work, but it
    cannot rewrite the original attempt's cleanup failure, Gate receipt, or
    formal classification.
    """

    if not isinstance(cleanup_receipt, dict):
        raise ValueError("campaign cleanup receipt must be an object")
    if cleanup_receipt.get("status") != "SUCCEEDED":
        raise ValueError("campaign cleanup receipt must have SUCCEEDED status")
    if cleanup_receipt.get("dirty_environment") is not False:
        raise ValueError("campaign cleanup receipt must prove a clean environment")
    audit_receipt_id = _text(cleanup_receipt.get("audit_receipt_id"))
    observation_ref = _text(cleanup_receipt.get("after_cleanup_observation_ref"))
    if not audit_receipt_id or not observation_ref:
        raise ValueError("campaign cleanup receipt requires audit and after-observation references")

    from .customer_delivery_gate_v2 import (
        CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
        DeliveryGateV2Error,
        validate_customer_delivery_gate_receipt_v2,
    )

    defects: list[dict[str, Any]] = []
    clues: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = copy.deepcopy(item)
        gate = _dict(row.get("delivery_gate_receipt"))
        gate_schema = _text(gate.get("schema_version"))
        gate_status = _text(gate.get("status")).upper()
        if gate_schema == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
            try:
                validated = validate_customer_delivery_gate_receipt_v2(
                    gate,
                    finding=row if gate_status == "DELIVERABLE" else None,
                )
            except DeliveryGateV2Error as exc:
                raise ValueError(f"campaign cleanup found invalid gate receipt: {exc}") from exc
            if validated.get("status") == "DELIVERABLE":
                defects.append(row)
                continue
        elif (
            gate_schema == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            and gate_status == "DELIVERABLE"
        ):
            # Legacy champion emits v1 gate receipts. Post-run target reset must
            # not demote a frozen DELIVERABLE terminal into clues.
            defects.append(row)
            continue
        clues.append(row)
    return defects, clues
