"""Final causal gate for authorization comparison findings.

The Contract Oracle remains the assertion authority. This module consumes its candidate plus
existing typed control/treatment/observer/binding receipts and the compiled authorization
comparison contract. A customer-facing authorization candidate survives only when the runtime
proves that both requests reached the target, the authorized control succeeded, the comparison
observer proved the protected resource relation, the same governed runtime binding identified
the resource, and the declared single identity dimension remained the only varying dimension.

It never executes requests, evaluates the business assertion again, selects credentials, or
creates an independent positive defect verdict.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable

from .authorization_comparison_contract import (
    bind_runtime_actor_identity_context,
    validate_authorization_comparison_contract,
)
from .contract_oracles import validate_contract_evidence_receipt
from .observer_contracts_base import validate_observer_receipt

SCHEMA_VERSION = "qualibug.authorization-oracle-causality-receipt.v1"
_GATE_STATUSES = frozenset({"PASSED", "INDETERMINATE", "NOT_APPLICABLE"})
_AUTHORIZATION_FAMILIES = frozenset({"authorization", "isolation", "visibility"})


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


def _receipt(payload: dict[str, Any]) -> dict[str, Any]:
    status = _text(payload.get("status")).upper()
    if status not in _GATE_STATUSES:
        raise ValueError(f"authorization_causality_status_invalid:{status}")
    body = {"schema_version": SCHEMA_VERSION, **payload}
    return {
        **body,
        "receipt_id": "auth_causality_" + hashlib.sha256(
            _canonical(body).encode("utf-8")
        ).hexdigest()[:24],
    }


def _step_subjects(experiment: dict[str, Any], phase: str) -> list[str]:
    key = f"{phase}_plan"
    result: list[str] = []
    for index, raw in enumerate(_list(experiment.get(key))):
        row = _dict(raw)
        subject = _text(row.get("step_id") or row.get("id"))
        if not subject:
            operation = _text(row.get("operation_ref")) or "operation"
            subject = f"{phase}:{operation}:{index + 1}"
        if subject not in result:
            result.append(subject)
    return result


def _validated_contract_receipts(
    rows: Iterable[Any],
    *,
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    reasons: list[str] = []
    for index, raw in enumerate(rows):
        try:
            receipt = validate_contract_evidence_receipt(_dict(raw))
        except Exception as exc:
            reasons.append(
                f"AUTHORIZATION_CAUSAL_CONTRACT_RECEIPT_INVALID:{index}:{type(exc).__name__}"
            )
            continue
        if (
            _text(receipt.get("experiment_id")) != experiment_id
            or _text(receipt.get("obligation_id")) != obligation_id
            or _text(receipt.get("campaign_id")) != campaign_id
            or _text(receipt.get("execution_id")) != execution_id
        ):
            reasons.append(f"AUTHORIZATION_CAUSAL_CONTRACT_LINEAGE_MISMATCH:{index}")
            continue
        key = (_text(receipt.get("kind")), _text(receipt.get("subject_id")))
        if key in indexed:
            reasons.append(
                f"AUTHORIZATION_CAUSAL_CONTRACT_RECEIPT_DUPLICATE:{key[0]}:{key[1]}"
            )
            continue
        indexed[key] = receipt
    return indexed, reasons


def _phase_receipt_problem(
    indexed: dict[tuple[str, str], dict[str, Any]],
    *,
    phase: str,
    subjects: list[str],
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    receipt_ids: list[str] = []
    if len(subjects) != 1:
        return [f"AUTHORIZATION_CAUSAL_{phase.upper()}_PLAN_SHAPE_INVALID"], []
    subject = subjects[0]
    receipt = indexed.get((phase, subject))
    if not receipt:
        return [f"AUTHORIZATION_CAUSAL_{phase.upper()}_RECEIPT_MISSING:{subject}"], []
    evidence = _dict(receipt.get("evidence"))
    status_code = int(evidence.get("status_code") or 0)
    if _text(receipt.get("status")).upper() != "OBSERVED":
        reasons.append(f"AUTHORIZATION_CAUSAL_{phase.upper()}_NOT_OBSERVED:{subject}")
    if evidence.get("response_observed") is not True or status_code <= 0:
        reasons.append(f"AUTHORIZATION_CAUSAL_{phase.upper()}_TARGET_NOT_REACHED:{subject}")
    if phase == "control" and evidence.get("control_succeeded") is not True:
        reasons.append(f"AUTHORIZATION_CAUSAL_CONTROL_NOT_AUTHORIZED:{subject}")
    receipt_id = _text(receipt.get("receipt_id"))
    if receipt_id:
        receipt_ids.append(receipt_id)
    return reasons, receipt_ids


def _authorization_observer(
    rows: Iterable[Any],
    *,
    campaign_id: str,
    execution_id: str,
    same_resource_required: bool,
) -> tuple[dict[str, Any], list[str]]:
    matches: list[dict[str, Any]] = []
    reasons: list[str] = []
    for index, raw in enumerate(rows):
        try:
            receipt = validate_observer_receipt(_dict(raw))
        except Exception as exc:
            reasons.append(
                f"AUTHORIZATION_CAUSAL_OBSERVER_RECEIPT_INVALID:{index}:{type(exc).__name__}"
            )
            continue
        if _text(receipt.get("observer_id")) != "authorization_comparison":
            continue
        if (
            _text(receipt.get("campaign_id")) != campaign_id
            or _text(receipt.get("execution_id")) != execution_id
        ):
            reasons.append("AUTHORIZATION_CAUSAL_OBSERVER_LINEAGE_MISMATCH")
            continue
        matches.append(receipt)
    if len(matches) != 1:
        reasons.append(
            "AUTHORIZATION_CAUSAL_OBSERVER_MISSING"
            if not matches
            else "AUTHORIZATION_CAUSAL_OBSERVER_AMBIGUOUS"
        )
        return {}, reasons
    receipt = matches[0]
    evidence = _dict(receipt.get("evidence"))
    if _text(receipt.get("status")).upper() != "OBSERVED":
        reasons.append("AUTHORIZATION_CAUSAL_OBSERVER_INDETERMINATE")
    if evidence.get("owner_can_access") is not True:
        reasons.append("AUTHORIZATION_CAUSAL_CONTROL_ACCESS_NOT_PROVEN")
    if same_resource_required and evidence.get("same_resource_proven") is not True:
        reasons.append("AUTHORIZATION_CAUSAL_SAME_RESOURCE_NOT_PROVEN")
    violation_observed = bool(
        evidence.get("viewer_can_access") is True
        or evidence.get("leak_detected") is True
        or int(evidence.get("treatment_effect_count") or 0) > 0
    )
    if not violation_observed:
        reasons.append("AUTHORIZATION_CAUSAL_VIOLATION_NOT_OBSERVED")
    return receipt, reasons


def authorization_resource_identity_proof_targets(
    contract: dict[str, Any],
) -> list[str]:
    """Return the canonical runtime resource-identity proof coordinates.

    Placeholder-backed operations already expose materialization targets. Collection
    and fixed-path operations do not, even though the comparison observer can prove
    both actors observed the same response resource. In that case the compiled
    operation identity is the stable source-backed coordinate; no path parameter is
    invented and no second binding engine is introduced.
    """

    row = _dict(contract)
    explicit = sorted({
        _text(value)
        for value in _list(row.get("resource_identity_binding_targets"))
        if _text(value)
    })
    if explicit:
        return explicit
    control_operation = _text(row.get("control_operation_ref"))
    treatment_operation = _text(row.get("treatment_operation_ref"))
    if not control_operation or control_operation != treatment_operation:
        return []
    return [f"operation:{control_operation}:observed_resource_identity"]


def build_authorization_observer_binding_proofs(
    observer_receipt: dict[str, Any],
    targets: Iterable[Any],
) -> tuple[str, list[dict[str, str]]]:
    """Content-address an observer-proven same-resource identity.

    A comparison observer may prove that control and treatment reached the same
    resource even when the operation has no runtime placeholder binding.  That proof
    must still use the same ``target -> value_fingerprint`` shape as materialized
    bindings; a human-readable sentinel is not a valid identity fingerprint.
    """

    observer = validate_observer_receipt(_dict(observer_receipt))
    evidence = _dict(observer.get("evidence"))
    receipt_id = _text(observer.get("receipt_id"))
    normalized_targets = sorted({
        _text(value) for value in targets if _text(value)
    })
    if (
        _text(observer.get("observer_id")) != "authorization_comparison"
        or _text(observer.get("status")).upper() != "OBSERVED"
        or evidence.get("same_resource_proven") is not True
        or not receipt_id
        or not normalized_targets
    ):
        raise ValueError("authorization_observer_binding_proof_incomplete")
    values: dict[str, str] = {}
    proofs: list[dict[str, str]] = []
    for target in normalized_targets:
        value_fingerprint = hashlib.sha256(_canonical({
            "authority": "authorization_comparison_observer",
            "observer_receipt_id": receipt_id,
            "target": target,
            "same_resource_proven": True,
        }).encode("utf-8")).hexdigest()
        values[target] = value_fingerprint
        proofs.append({
            "receipt_id": receipt_id,
            "target": target,
            "status": "BOUND",
            "value_fingerprint": value_fingerprint,
        })
    return hashlib.sha256(_canonical(values).encode("utf-8")).hexdigest(), proofs


def _binding_proof(
    contract: dict[str, Any], rows: Iterable[Any]
) -> tuple[str, list[str], list[str]]:
    targets = [
        _text(value)
        for value in _list(contract.get("resource_identity_binding_targets"))
        if _text(value)
    ]
    if not targets:
        if contract.get("same_resource_identity_required") is True:
            return "", [], ["AUTHORIZATION_CAUSAL_RESOURCE_TARGETS_MISSING"]
        return "", [], []
    by_target: dict[str, list[dict[str, Any]]] = {target: [] for target in targets}
    for raw in rows:
        row = _dict(raw)
        target = _text(row.get("target") or row.get("binding_target"))
        if target not in by_target:
            continue
        if (
            _text(row.get("status")).upper() == "BOUND"
            and _text(row.get("value_fingerprint"))
        ):
            by_target[target].append(row)
    reasons: list[str] = []
    receipt_ids: list[str] = []
    values: dict[str, str] = {}
    for target in targets:
        matches = by_target[target]
        if len(matches) != 1:
            reasons.append(
                f"AUTHORIZATION_CAUSAL_RESOURCE_BINDING_"
                f"{'MISSING' if not matches else 'AMBIGUOUS'}:{target}"
            )
            continue
        row = matches[0]
        values[target] = _text(row.get("value_fingerprint"))
        receipt_id = _text(row.get("receipt_id") or row.get("materialization_receipt_id"))
        if receipt_id:
            receipt_ids.append(receipt_id)
    if reasons:
        return "", receipt_ids, reasons
    fingerprint = hashlib.sha256(_canonical(values).encode("utf-8")).hexdigest()
    return fingerprint, receipt_ids, []


def build_authorization_causality_receipt(
    *,
    result: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    account_rows: Iterable[Any],
) -> dict[str, Any]:
    """Build a content-addressed causal gate receipt for one final execution result."""
    exp = _dict(experiment)
    contract = _dict(exp.get("authorization_comparison_contract"))
    if not contract:
        return _receipt({
            "status": "NOT_APPLICABLE",
            "experiment_id": _text(exp.get("experiment_id")),
            "obligation_id": _text(exp.get("obligation_id")),
            "campaign_id": _text(_dict(result).get("campaign_id")),
            "execution_id": _text(_dict(result).get("execution_id")),
            "reason_codes": [],
            "comparison_contract_fingerprint": "",
            "runtime_resource_identity_fingerprint": "",
            "verified_receipt_ids": [],
        })

    output = _dict(result)
    experiment_id = _text(output.get("experiment_id") or exp.get("experiment_id"))
    obligation_id = _text(output.get("obligation_id") or exp.get("obligation_id"))
    campaign_id = _text(output.get("campaign_id") or exp.get("campaign_id"))
    execution_id = _text(output.get("execution_id") or exp.get("execution_id"))
    reasons: list[str] = []
    if not all((experiment_id, obligation_id, campaign_id, execution_id)):
        reasons.append("AUTHORIZATION_CAUSAL_LINEAGE_MISSING")

    runtime_ir = bind_runtime_actor_identity_context(behavior_ir, account_rows)
    symmetric, symmetry_reason, symmetry_detail = validate_authorization_comparison_contract(
        exp,
        runtime_ir,
    )
    if not symmetric:
        reasons.append(
            "AUTHORIZATION_CAUSAL_IDENTITY_ASYMMETRIC:"
            + ":".join(value for value in (symmetry_reason, symmetry_detail) if value)
        )

    indexed, receipt_reasons = _validated_contract_receipts(
        _list(output.get("contract_evidence_receipts")),
        experiment_id=experiment_id,
        obligation_id=obligation_id,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    reasons.extend(receipt_reasons)
    verified_ids: list[str] = []
    for phase in ("control", "treatment"):
        phase_reasons, phase_ids = _phase_receipt_problem(
            indexed,
            phase=phase,
            subjects=_step_subjects(exp, phase),
        )
        reasons.extend(phase_reasons)
        verified_ids.extend(phase_ids)

    observer, observer_reasons = _authorization_observer(
        _list(output.get("observer_receipts")),
        campaign_id=campaign_id,
        execution_id=execution_id,
        same_resource_required=contract.get("same_resource_identity_required") is True,
    )
    reasons.extend(observer_reasons)
    if _text(observer.get("receipt_id")):
        verified_ids.append(_text(observer.get("receipt_id")))

    binding_fingerprint, binding_ids, binding_reasons = _binding_proof(
        contract,
        _list(output.get("binding_materialization_receipts")),
    )
    # When the authorization_comparison observer has already proven the same
    # resource, materialized placeholder receipts are optional. The observer is
    # projected into the exact same target/value-fingerprint authority shape.
    _observer_evidence = _dict(observer.get("evidence"))
    _observer_proved_same_resource = (
        _observer_evidence.get("same_resource_proven") is True
    )
    explicit_binding_targets = [
        _text(value)
        for value in _list(contract.get("resource_identity_binding_targets"))
        if _text(value)
    ]
    if _observer_proved_same_resource and not explicit_binding_targets:
        # Fixed-path and collection operations have no runtime placeholder to
        # materialize. The sealed comparison observer is their resource-identity
        # authority. Explicit placeholder targets remain stricter: their exact
        # materialization receipts cannot be replaced by response similarity.
        binding_reasons = []
        if not binding_fingerprint:
            binding_fingerprint, _observer_binding_proofs = (
                build_authorization_observer_binding_proofs(
                    observer,
                    authorization_resource_identity_proof_targets(contract),
                )
            )
    reasons.extend(binding_reasons)
    verified_ids.extend(binding_ids)
    if not _text(contract.get("shared_binding_graph_fingerprint")):
        reasons.append("AUTHORIZATION_CAUSAL_COMPILE_BINDING_FINGERPRINT_MISSING")

    reason_codes = sorted(set(reason for reason in reasons if reason))
    payload = {
        "status": "INDETERMINATE" if reason_codes else "PASSED",
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "campaign_id": campaign_id,
        "execution_id": execution_id,
        "reason_codes": reason_codes,
        "comparison_dimension": _text(contract.get("comparison_dimension")),
        "comparison_contract_fingerprint": _text(
            _dict(exp.get("compile_receipt")).get(
                "authorization_comparison_fingerprint"
            )
        ),
        "compile_binding_graph_fingerprint": _text(
            contract.get("shared_binding_graph_fingerprint")
        ),
        "runtime_resource_identity_fingerprint": binding_fingerprint,
        "control_target_reached": not any(
            "CONTROL_TARGET_NOT_REACHED" in reason for reason in reason_codes
        ),
        "treatment_target_reached": not any(
            "TREATMENT_TARGET_NOT_REACHED" in reason for reason in reason_codes
        ),
        "single_identity_dimension_proven": symmetric,
        "same_resource_proven": _dict(observer.get("evidence")).get(
            "same_resource_proven"
        ) is True,
        "verified_receipt_ids": sorted(set(verified_ids)),
    }
    return _receipt(payload)


def enforce_authorization_oracle_causality(
    *,
    result: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    account_rows: Iterable[Any],
) -> dict[str, Any]:
    """Demote an authorization defect candidate when causal evidence is incomplete."""
    output = deepcopy(_dict(result))
    contract = _dict(_dict(experiment).get("authorization_comparison_contract"))
    if not contract:
        # When no authorization comparison contract exists but the finding is
        # authorization-family, attach an explicit NOT_APPLICABLE receipt so
        # downstream quarantine knows causal delivery is not required.
        _finding = _dict(output.get("finding"))
        _risk = _text(
            _finding.get("risk_family")
            or _dict(output.get("obligation")).get("risk_family")
            or _dict(experiment).get("risk_family")
        ).lower()
        if _risk in _AUTHORIZATION_FAMILIES and _finding:
            _na_receipt = _receipt({
                "status": "NOT_APPLICABLE",
                "experiment_id": _text(
                    output.get("experiment_id") or _dict(experiment).get("experiment_id")
                ),
                "obligation_id": _text(
                    output.get("obligation_id") or _dict(experiment).get("obligation_id")
                ),
                "campaign_id": _text(output.get("campaign_id")),
                "execution_id": _text(output.get("execution_id")),
                "reason_codes": [],
                "comparison_contract_fingerprint": "",
                "runtime_resource_identity_fingerprint": "",
                "verified_receipt_ids": [],
            })
            output["authorization_causality_receipt"] = _na_receipt
            # Embed into finding so delivery_evidence_bundle carries it.
            _finding["authorization_causality_receipt"] = _na_receipt
            output["finding"] = _finding
        return output
    verdict = _dict(output.get("oracle_verdict"))
    finding = _dict(output.get("finding"))
    candidate = bool(
        finding
        or verdict.get("customer_deliverable_candidate") is True
        or _text(verdict.get("verdict")) == "customer_deliverable_defect_candidate"
    )
    receipt = build_authorization_causality_receipt(
        result=output,
        experiment=experiment,
        behavior_ir=behavior_ir,
        account_rows=account_rows,
    )
    output["authorization_causality_receipt"] = receipt
    if not candidate:
        return output

    if _text(receipt.get("status")) != "PASSED":
        original_verdict = {
            key: verdict.get(key)
            for key in (
                "status",
                "verdict",
                "receipt_id",
                "activation_receipt_id",
                "failed_assertions",
            )
            if key in verdict
        }
        output["oracle_verdict"] = {
            **verdict,
            "status": "INDETERMINATE",
            "verdict": "blocked_experiment",
            "customer_deliverable_candidate": False,
            "authorization_causality_gate": "INDETERMINATE",
            "authorization_causality_receipt_id": receipt["receipt_id"],
            "authorization_causality_reason_codes": list(receipt["reason_codes"]),
            "pre_causality_oracle_verdict": original_verdict,
        }
        output["finding"] = None
        if _text(output.get("status")) not in {"BLOCKED", "HARNESS_FAILURE"}:
            output["status"] = "EXECUTED"
        output["reason_code"] = "AUTHORIZATION_CAUSALITY_INDETERMINATE"
        output["detail"] = ",".join(receipt["reason_codes"][:12])
        execution_receipt = dict(_dict(output.get("execution_receipt")))
        execution_receipt.update({
            "status": output["status"],
            "reason_code": output["reason_code"],
            "detail": output["detail"],
            "authorization_causality_receipt_id": receipt["receipt_id"],
        })
        output["execution_receipt"] = execution_receipt
        return output

    output["oracle_verdict"] = {
        **verdict,
        "authorization_causality_gate": "PASSED",
        "authorization_causality_receipt_id": receipt["receipt_id"],
    }
    if finding:
        oracle = dict(_dict(finding.get("oracle")))
        oracle["authorization_causality_receipt_id"] = receipt["receipt_id"]
        oracle["authorization_causality_proven"] = True
        finding["oracle"] = oracle
        evidence = dict(_dict(finding.get("evidence")))
        evidence.update({
            "authorization_causality_receipt_id": receipt["receipt_id"],
            "authorization_comparison_dimension": receipt.get("comparison_dimension"),
            "runtime_resource_identity_fingerprint": receipt.get(
                "runtime_resource_identity_fingerprint"
            ),
            "single_identity_dimension_proven": True,
            "same_resource_proven": receipt.get("same_resource_proven") is True,
        })
        finding["evidence"] = evidence
        output["finding"] = finding
    return output


__all__ = [
    "SCHEMA_VERSION",
    "authorization_resource_identity_proof_targets",
    "build_authorization_causality_receipt",
    "build_authorization_observer_binding_proofs",
    "enforce_authorization_oracle_causality",
]
