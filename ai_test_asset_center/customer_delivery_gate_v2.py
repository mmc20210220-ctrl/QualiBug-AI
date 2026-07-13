"""Independent, receipt-backed customer Delivery Gate authority.

The v2 gate never trusts mutable finding flags. It revalidates the executed
mainline identity, typed evidence receipts, Contract Oracle, reproduction,
cleanup accounting, and the customer-facing payload fingerprint before a
finding can become DELIVERABLE.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .assertion_dsl import validate_assertion_receipt
from .contract_oracles import (
    validate_contract_evidence_receipt,
    validate_contract_oracle_receipt,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .observer_contracts_base import validate_observer_receipt
from .operational_receipts import validate_execution_operational_receipt


DELIVERY_EXECUTION_RECEIPT_SCHEMA = "qualibug.delivery-execution-receipt.v1"
REPRODUCTION_RECEIPT_SCHEMA = "qualibug.execution-reproduction-receipt.v1"
DELIVERY_LINEAGE_RECEIPT_SCHEMA = "qualibug.delivery-lineage-receipt.v1"
CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA = (
    "qualibug.customer-delivery-gate-receipt.v2"
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
    "customer_delivery_gate_reasons",
    "customer_delivery_status",
    "customer_visible",
    "delivery_occurrence_count",
    "delivery_occurrence_finding_id",
    "delivery_occurrence_finding_ids",
    "delivery_gate_receipt",
    "delivery_gate_receipt_id",
    "delivery_track",
    "finding_class",
    "gate_passed",
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
    return _fingerprint(_finding_payload(finding))


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
    if _text(operational.get("execution_status")).upper() != "EXECUTED":
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
        if _text(execution.get(field)) != _text(oracle.get(field)):
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
        try:
            status_code = int(step.get("status_code") or 0)
        except (TypeError, ValueError) as exc:
            raise DeliveryGateV2Error("reproduction_status_code_invalid") from exc
        observation_receipt_id = _text(step.get("observation_receipt_id"))
        if status_code <= 0 or not observation_receipt_id:
            raise DeliveryGateV2Error("reproduction_observation_missing")
        path_template = _text(step.get("path_template"))
        request_body_fingerprint = _text(
            step.get("request_body_fingerprint")
        )
        request_semantics_fingerprint = _text(
            step.get("request_semantics_fingerprint")
        )
        mutation_class = _text(step.get("mutation_class"))
        mutation_selector = _text(step.get("mutation_selector"))
        mutation_operator = _text(step.get("mutation_operator"))
        if (
            not path_template
            or not mutation_class
            or not _is_sha256(request_body_fingerprint)
            or not _is_sha256(request_semantics_fingerprint)
        ):
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_missing"
            )
        expected_request_semantics = _fingerprint({
            "operation_ref": _text(step.get("operation_ref")),
            "method": _text(step.get("method")).upper(),
            "path_template": path_template,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "request_body_fingerprint": request_body_fingerprint,
        })
        if request_semantics_fingerprint != expected_request_semantics:
            raise DeliveryGateV2Error(
                "reproduction_request_semantics_fingerprint_invalid"
            )
        summaries.append({
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
        })
    phases = {_text(value.get("phase")) for value in summaries}
    execution_observation_ids = set(execution["observation_receipt_ids"])
    if any(
        _text(value.get("observation_receipt_id")) not in execution_observation_ids
        for value in summaries
    ):
        raise DeliveryGateV2Error("reproduction_observation_lineage_mismatch")
    reproduced = (
        _text(oracle.get("status")) == "VIOLATION"
        and phases == {"control", "treatment"}
        and bool(summaries)
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
        "reason_code": "" if reproduced else "ORACLE_NOT_VIOLATED",
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
            phases != {"control", "treatment"}
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
        return "BLOCKED", ["ASSERTION_INDETERMINATE"]
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
    if len(violations) != 1:
        return "BLOCKED", ["AMBIGUOUS_MULTI_ASSERTION_OCCURRENCE"]
    contract_ids = {_text(value.get("receipt_id")) for value in contracts}
    observer_ids = {_text(value.get("receipt_id")) for value in observers}
    required = _dict(activation.get("required"))
    verified = _dict(activation.get("verified_receipt_ids"))
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
            raise DeliveryGateV2Error(
                f"delivery_{kind}_activation_reference_mismatch"
            )
    verified_observers = {
        _text(value) for value in _list(verified.get("observer")) if _text(value)
    }
    if verified_observers != observer_ids:
        raise DeliveryGateV2Error("delivery_observer_activation_reference_mismatch")
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

    operational = _dict(execution.get("operational_receipt"))
    cleanup = _dict(operational.get("cleanup_outcome"))
    accepted_non_cleanup = int(
        operational.get("accepted_non_cleanup_write_count") or 0
    )
    cleanup_failures = int(cleanup.get("failure_count") or 0)
    if cleanup_failures:
        return "HARNESS_FAILED", ["CLEANUP_COMPENSATION_FAILED"]
    cleanup_status = _text(cleanup.get("status")).upper()
    cleanup_contracts = [
        value for value in contracts if _text(value.get("kind")) == "cleanup"
    ]
    if accepted_non_cleanup == 0:
        if cleanup_status != "NOT_REQUIRED":
            raise DeliveryGateV2Error("cleanup_not_required_status_mismatch")
    else:
        if cleanup_status != "COMPLETED" or not cleanup_contracts:
            return "HARNESS_FAILED", ["CLEANUP_EVIDENCE_INCOMPLETE"]
        covered = sum(
            int(_dict(value.get("evidence")).get("accepted_write_count") or 0)
            for value in cleanup_contracts
        )
        if covered != accepted_non_cleanup:
            return "HARNESS_FAILED", ["CLEANUP_WRITE_COVERAGE_MISMATCH"]
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
    status, reason_codes = _validate_active_chain(
        execution=execution,
        contracts=contracts,
        observers=observers,
        oracle=oracle,
        reproduction=reproduction,
    )
    deliverable = status == "DELIVERABLE"
    finding_row = _dict(finding)
    finding_id = _text(finding_row.get("finding_id") or finding_row.get("id"))
    if deliverable and not finding_id:
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
        "cleanup": _text(
            _dict(_dict(execution.get("operational_receipt")).get("cleanup_outcome")).get("status")
        ).upper(),
        "lineage": "CONSISTENT",
    }
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
    if deliverable and adjudication != {
        "execution": "EXECUTED",
        "activation": "ACTIVE",
        "assertion": "VIOLATION",
        "oracle": "VIOLATION",
        "reproduction": "REPRODUCED",
        "cleanup": "COMPLETED" if refs["cleanup"] else "NOT_REQUIRED",
        "lineage": "CONSISTENT",
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
    return dict(expected)


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
