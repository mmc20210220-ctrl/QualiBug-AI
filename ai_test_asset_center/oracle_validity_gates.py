"""Oracle Validity Gates before PROPERTY_HELD / VIOLATION stand.

Contract Oracle remains the assertion authority. These gates only demote
incomplete causal/contrast/evidence cases to INDETERMINATE. They never upgrade
a verdict, invent evidence, or create findings.

Gates (SPEC QB-DISCOVERY-FACT-TO-EXPERIMENT-V1 §7.7):
  Identity | Contrast | Preconditions | Causal | Evidence
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .effect_observation_graph import (
    build_effect_observation_graph,
    requires_independent_readback,
)

SCHEMA_VERSION = "qualibug.oracle-validity-gates-receipt.v1"
_GATE_STATUSES = frozenset({"PASSED", "INDETERMINATE", "NOT_APPLICABLE"})
_TERMINAL_VERDICTS = frozenset({"PROPERTY_HELD", "VIOLATION"})
_AUTH_FAMILIES = frozenset({"authorization", "isolation", "visibility"})
_STATE_FAMILIES = frozenset(
    {"state", "conservation", "temporal", "idempotency", "concurrency"}
)


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
        raise ValueError(f"oracle_validity_status_invalid:{status}")
    body = {"schema_version": SCHEMA_VERSION, **payload}
    return {
        **body,
        "receipt_id": "ovg_"
        + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()[:24],
    }


def _risk_family(experiment: dict[str, Any], result: dict[str, Any]) -> str:
    return _text(
        _dict(result.get("finding")).get("risk_family")
        or _dict(result.get("obligation")).get("risk_family")
        or experiment.get("risk_family")
        or _dict(experiment.get("property")).get("risk_family")
    ).lower()


def _phase_plan(experiment: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    return [row for row in _list(experiment.get(f"{phase}_plan")) if isinstance(row, dict)]


def _contract_rows(result: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in (
        _list(result.get("contract_evidence_receipts")),
        _list(evidence.get("contract_evidence_receipts")),
        _list(_dict(evidence.get("observations")).get("contract_evidence_receipts")),
    ):
        for raw in source:
            row = _dict(raw)
            if row:
                rows.append(row)
    return rows


def _observer_rows(result: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in (
        _list(result.get("observer_receipts")),
        _list(evidence.get("observer_receipts")),
        _list(_dict(evidence.get("observations")).get("observer_receipts")),
    ):
        for raw in source:
            row = _dict(raw)
            if row:
                rows.append(row)
    return rows


def _step_signature(step: dict[str, Any]) -> dict[str, str]:
    evidence = _dict(step.get("evidence"))
    return {
        "actor_ref": _text(step.get("actor_ref")),
        "method": _text(step.get("method")).upper(),
        "path": _text(step.get("path")),
        "operation_ref": _text(step.get("operation_ref")),
        "body_fingerprint": _text(
            step.get("body_fingerprint")
            or evidence.get("body_fingerprint")
            or evidence.get("request_body_fingerprint")
        ),
        "credential_fingerprint": _text(
            step.get("credential_fingerprint")
            or evidence.get("credential_fingerprint")
            or evidence.get("actor_token_fingerprint")
        ),
    }


def _contrast_fields() -> tuple[str, ...]:
    return (
        "actor_ref",
        "credential_fingerprint",
        "path",
        "method",
        "body_fingerprint",
        "operation_ref",
    )


def _differing_contrast_dimensions(
    control_sigs: list[dict[str, str]],
    treatment_sigs: list[dict[str, str]],
) -> list[str]:
    differing: list[str] = []
    for field in _contrast_fields():
        c_vals = {row.get(field) for row in control_sigs if row.get(field)}
        t_vals = {row.get(field) for row in treatment_sigs if row.get(field)}
        if c_vals and t_vals and c_vals != t_vals:
            differing.append(field)
    return differing


def _contract_phase_signatures(
    result: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Executed control/treatment request identities from contract evidence.

    Plan steps may omit body fingerprints even when governed transport sealed
    distinct request_body_fingerprint values. Contrast must consult those
    receipts or validation dual-arm experiments are falsely VACUOUS_CONTRAST.
    """
    control: list[dict[str, str]] = []
    treatment: list[dict[str, str]] = []
    for raw in _contract_rows(result, evidence):
        kind = _text(raw.get("kind")).lower()
        if kind not in {"control", "treatment"}:
            continue
        # Only observed arms prove an executed request identity contrast.
        if _text(raw.get("status")).upper() != "OBSERVED":
            continue
        ev = _dict(raw.get("evidence"))
        sig = _step_signature(
            {
                "actor_ref": raw.get("actor_ref") or ev.get("actor_ref"),
                "method": raw.get("method") or ev.get("method"),
                "path": raw.get("path") or ev.get("path"),
                "operation_ref": raw.get("operation_ref") or ev.get("operation_ref"),
                "body_fingerprint": (
                    raw.get("body_fingerprint")
                    or ev.get("body_fingerprint")
                    or ev.get("request_body_fingerprint")
                ),
                "credential_fingerprint": (
                    raw.get("credential_fingerprint")
                    or ev.get("credential_fingerprint")
                    or ev.get("actor_token_fingerprint")
                ),
                "evidence": ev,
            }
        )
        if kind == "control":
            control.append(sig)
        else:
            treatment.append(sig)
    return control, treatment


def _gate_identity(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any],
    evidence: dict[str, Any],
    risk: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    control = _phase_plan(experiment, "control")
    treatment = _phase_plan(experiment, "treatment")
    if not control and not treatment:
        return "PASSED", []

    auth_observer = next(
        (
            row
            for row in _observer_rows(result, evidence)
            if _text(row.get("observer_id")) == "authorization_comparison"
        ),
        {},
    )
    same_resource = _dict(auth_observer.get("evidence")).get("same_resource_proven")
    if risk in _AUTH_FAMILIES and control and treatment:
        if same_resource is not True:
            # Fixed-path experiments may still share exact path templates.
            control_paths = {_text(step.get("path")) for step in control if _text(step.get("path"))}
            treatment_paths = {
                _text(step.get("path")) for step in treatment if _text(step.get("path"))
            }
            if not control_paths or not treatment_paths or control_paths != treatment_paths:
                reasons.append("OBJECT_IDENTITY_UNPROVEN")

    for phase, plan in (("control", control), ("treatment", treatment)):
        for index, step in enumerate(plan):
            path = _text(step.get("path"))
            if path and ("{" in path or "<" in path):
                reasons.append(f"IDENTITY_PATH_UNRESOLVED:{phase}:{index}")
    return ("INDETERMINATE" if reasons else "PASSED"), sorted(set(reasons))


def _gate_contrast(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any],
    evidence: dict[str, Any],
    risk: str,
) -> tuple[str, list[str]]:
    control = _phase_plan(experiment, "control")
    treatment = _phase_plan(experiment, "treatment")
    if not control or not treatment:
        # Single-arm experiments have no vacuous-contrast risk.
        return "PASSED", []

    control_sigs = [_step_signature(step) for step in control]
    treatment_sigs = [_step_signature(step) for step in treatment]
    reasons: list[str] = []

    differing_dimensions = _differing_contrast_dimensions(control_sigs, treatment_sigs)

    property_row = _dict(experiment.get("property"))
    if _text(property_row.get("control_actor_ref")) and _text(
        property_row.get("treatment_actor_ref")
    ):
        if _text(property_row.get("control_actor_ref")) != _text(
            property_row.get("treatment_actor_ref")
        ):
            if "actor_ref" not in differing_dimensions:
                differing_dimensions.append("actor_ref")

    # H27: plan steps often omit body fingerprints; executed contract evidence is
    # the sealed request-identity authority for dual-arm contrast.
    obs_control_sigs, obs_treatment_sigs = _contract_phase_signatures(result, evidence)
    observed_differing: list[str] = []
    if obs_control_sigs and obs_treatment_sigs:
        observed_differing = _differing_contrast_dimensions(
            obs_control_sigs, obs_treatment_sigs
        )
        for dimension in observed_differing:
            if dimension not in differing_dimensions:
                differing_dimensions.append(dimension)

    if not differing_dimensions:
        reasons.append("VACUOUS_CONTRAST")

    # region agent log
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path

        _log_path = (
            _Path(__file__).resolve().parents[1] / ".cursor" / "debug-0de9ac.log"
        )
        _payload = {
            "sessionId": "0de9ac",
            "runId": "h27-vacuous-contrast",
            "hypothesisId": "H27c",
            "location": "oracle_validity_gates.py:_gate_contrast",
            "message": "contrast_gate_dimensions",
            "data": {
                "risk": risk,
                "plan_differing": _differing_contrast_dimensions(
                    control_sigs, treatment_sigs
                ),
                "observed_differing": observed_differing,
                "merged_differing": list(differing_dimensions),
                "vacuous": "VACUOUS_CONTRAST" in reasons,
                "obs_control_body_fps": sorted(
                    {
                        row.get("body_fingerprint")
                        for row in obs_control_sigs
                        if row.get("body_fingerprint")
                    }
                ),
                "obs_treatment_body_fps": sorted(
                    {
                        row.get("body_fingerprint")
                        for row in obs_treatment_sigs
                        if row.get("body_fingerprint")
                    }
                ),
                "experiment_id": _text(
                    experiment.get("experiment_id") or result.get("experiment_id")
                ),
            },
            "timestamp": int(_time.time() * 1000),
        }
        with _log_path.open("a", encoding="utf-8") as _fh:
            _fh.write(_json.dumps(_payload, ensure_ascii=False) + "\n")
        try:
            import urllib.request as _urlreq

            _req = _urlreq.Request(
                "http://127.0.0.1:7369/ingest/150ffea9-b73d-4706-a6b2-26c8a08966e8",
                data=_json.dumps(_payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Debug-Session-Id": "0de9ac",
                },
                method="POST",
            )
            _urlreq.urlopen(_req, timeout=1)
        except Exception:
            pass
    except Exception:
        pass
    # endregion

    if risk in _AUTH_FAMILIES:
        control_actors = {row.get("actor_ref") for row in control_sigs if row.get("actor_ref")}
        treatment_actors = {
            row.get("actor_ref") for row in treatment_sigs if row.get("actor_ref")
        }
        if control_actors and treatment_actors and control_actors == treatment_actors:
            reasons.append("SAME_ACTOR_NO_CONTRAST")
        control_creds = {
            row.get("credential_fingerprint")
            for row in control_sigs
            if row.get("credential_fingerprint")
        }
        treatment_creds = {
            row.get("credential_fingerprint")
            for row in treatment_sigs
            if row.get("credential_fingerprint")
        }
        # When fingerprints are present on both arms they must differ.
        if control_creds and treatment_creds and control_creds == treatment_creds:
            reasons.append("SAME_CREDENTIAL_NO_CONTRAST")

        auth_observer = next(
            (
                row
                for row in _observer_rows(result, evidence)
                if _text(row.get("observer_id")) == "authorization_comparison"
            ),
            {},
        )
        if auth_observer and _dict(auth_observer.get("evidence")).get(
            "same_resource_proven"
        ) is not True:
            # Contrast without shared target is not a valid authorization disproof.
            if "OBJECT_IDENTITY_UNPROVEN" not in reasons:
                pass

    return ("INDETERMINATE" if reasons else "PASSED"), sorted(set(reasons))


def _gate_preconditions(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    property_row = _dict(experiment.get("property"))
    assertion_kind = _text(
        property_row.get("assertion_kind") or property_row.get("kind")
    ).lower()
    compiled_observer_ids = {
        _text(row.get("observer_id") or row.get("id"))
        for row in _list(experiment.get("observers"))
        if isinstance(row, dict)
    }
    requires_before = bool(
        property_row.get("required_pre_state")
        or property_row.get("pre_state")
        or property_row.get("required_state")
        or assertion_kind in {"state_transition", "entity_state"}
        or "before_state" in compiled_observer_ids
    )
    observers = _observer_rows(result, evidence)
    before = [
        row
        for row in observers
        if _text(row.get("observer_id")) in {"before_state", "entity_state"}
    ]
    if requires_before:
        observed_before = any(
            _text(row.get("status")).upper() == "OBSERVED" for row in before
        )
        if not before:
            reasons.append("MISSING_BEFORE_STATE")
        elif not observed_before:
            reasons.append("BEFORE_STATE_NOT_OBSERVED")

    for raw in _contract_rows(result, evidence):
        if _text(raw.get("kind")).lower() != "fixture":
            continue
        status = _text(raw.get("status")).upper()
        if status in {"FAILED", "BLOCKED"}:
            # Fixture/setup failure must never become a SUT defect verdict.
            reasons.append(f"FIXTURE_PRECONDITION_FAILED:{status}")
    return ("INDETERMINATE" if reasons else "PASSED"), sorted(set(reasons))


def _gate_causal(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any],
    evidence: dict[str, Any],
    risk: str,
    effect_graph: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if risk not in _STATE_FAMILIES and not requires_independent_readback(experiment):
        return "PASSED", []

    observers = _observer_rows(result, evidence)
    effect = next(
        (
            row
            for row in observers
            if _text(row.get("observer_id")) in {"business_effect", "after_state", "final_state"}
        ),
        {},
    )
    if not effect:
        if _text(effect_graph.get("status")) == "WRITE_RESPONSE_ONLY":
            reasons.append("EFFECT_NOT_ATTRIBUTABLE_WRITE_RESPONSE_ONLY")
        return ("INDETERMINATE" if reasons else "PASSED"), sorted(set(reasons))

    evidence_body = _dict(effect.get("evidence"))
    if _text(effect.get("status")).upper() != "OBSERVED":
        reasons.append("EFFECT_OBSERVER_NOT_OBSERVED")
    if evidence_body.get("business_effect_observed") is False:
        # Explicit false is fine for held properties; causal attribution still needs
        # before/after windows when a state transition was claimed.
        if risk in _STATE_FAMILIES and not (
            evidence_body.get("before_fingerprint") and evidence_body.get("after_fingerprint")
        ):
            reasons.append("EFFECT_WINDOW_MISSING")
    return ("INDETERMINATE" if reasons else "PASSED"), sorted(set(reasons))


def _gate_evidence(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any],
    evidence: dict[str, Any],
    effect_graph: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    property_row = _dict(experiment.get("property"))
    required: list[str] = []
    for observer_id in (
        _text(value) for value in _list(property_row.get("required_observers"))
    ):
        if observer_id:
            required.append(observer_id)
    # Only enforce effect-bearing compiled observers — http_response alone is
    # never sufficient for persistence claims and is checked via the graph.
    for raw in _list(experiment.get("observers")):
        row = _dict(raw)
        observer_id = _text(row.get("observer_id") or row.get("id"))
        if observer_id in {
            "before_state",
            "after_state",
            "final_state",
            "business_effect",
            "entity_state",
            "authorization_comparison",
        }:
            required.append(observer_id)

    observed_ids = {
        _text(row.get("observer_id"))
        for row in _observer_rows(result, evidence)
        if _text(row.get("status")).upper() == "OBSERVED"
    }
    for observer_id in sorted(set(required)):
        if observer_id and observer_id not in observed_ids:
            present = any(
                _text(row.get("observer_id")) == observer_id
                for row in _observer_rows(result, evidence)
            )
            if present:
                reasons.append(f"REQUIRED_OBSERVER_NOT_OBSERVED:{observer_id}")
            else:
                reasons.append(f"REQUIRED_OBSERVER_MISSING:{observer_id}")

    if requires_independent_readback(experiment):
        graph_status = _text(effect_graph.get("status")).upper()
        if graph_status == "WRITE_RESPONSE_ONLY":
            reasons.append("WRITE_RESPONSE_ONLY_EVIDENCE")
        elif graph_status == "INCOMPLETE":
            reasons.append("INDEPENDENT_READBACK_NOT_OBSERVED")

    return ("INDETERMINATE" if reasons else "PASSED"), sorted(set(reasons))


def build_oracle_validity_receipt(
    *,
    result: dict[str, Any],
    experiment: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate Identity/Contrast/Preconditions/Causal/Evidence gates."""
    exp = _dict(experiment)
    out = _dict(result)
    ev = _dict(evidence)
    if not ev:
        ev = {
            "observer_receipts": _list(out.get("observer_receipts")),
            "contract_evidence_receipts": _list(out.get("contract_evidence_receipts")),
            "observations": _dict(out.get("observations")),
        }
    verdict = _dict(out.get("oracle_verdict"))
    status = _text(verdict.get("status")).upper()
    risk = _risk_family(exp, out)
    effect_graph = build_effect_observation_graph(
        experiment=exp,
        result=out,
        evidence=ev,
    )

    if status not in _TERMINAL_VERDICTS:
        return _receipt(
            {
                "status": "NOT_APPLICABLE",
                "experiment_id": _text(out.get("experiment_id") or exp.get("experiment_id")),
                "obligation_id": _text(out.get("obligation_id") or exp.get("obligation_id")),
                "campaign_id": _text(out.get("campaign_id")),
                "execution_id": _text(out.get("execution_id")),
                "reason_codes": [],
                "gate_results": {},
                "effect_observation_graph_receipt_id": effect_graph.get("receipt_id"),
                "effect_observation_graph_status": effect_graph.get("status"),
                "oracle_status_before_gates": status or "MISSING",
            }
        )

    identity_status, identity_reasons = _gate_identity(
        experiment=exp, result=out, evidence=ev, risk=risk
    )
    contrast_status, contrast_reasons = _gate_contrast(
        experiment=exp, result=out, evidence=ev, risk=risk
    )
    precond_status, precond_reasons = _gate_preconditions(
        experiment=exp, result=out, evidence=ev
    )
    causal_status, causal_reasons = _gate_causal(
        experiment=exp,
        result=out,
        evidence=ev,
        risk=risk,
        effect_graph=effect_graph,
    )
    evidence_status, evidence_reasons = _gate_evidence(
        experiment=exp,
        result=out,
        evidence=ev,
        effect_graph=effect_graph,
    )
    reason_codes = sorted(
        set(
            identity_reasons
            + contrast_reasons
            + precond_reasons
            + causal_reasons
            + evidence_reasons
        )
    )
    return _receipt(
        {
            "status": "INDETERMINATE" if reason_codes else "PASSED",
            "experiment_id": _text(out.get("experiment_id") or exp.get("experiment_id")),
            "obligation_id": _text(out.get("obligation_id") or exp.get("obligation_id")),
            "campaign_id": _text(out.get("campaign_id")),
            "execution_id": _text(out.get("execution_id")),
            "reason_codes": reason_codes,
            "gate_results": {
                "identity": {"status": identity_status, "reason_codes": identity_reasons},
                "contrast": {"status": contrast_status, "reason_codes": contrast_reasons},
                "preconditions": {
                    "status": precond_status,
                    "reason_codes": precond_reasons,
                },
                "causal": {"status": causal_status, "reason_codes": causal_reasons},
                "evidence": {"status": evidence_status, "reason_codes": evidence_reasons},
            },
            "effect_observation_graph_receipt_id": effect_graph.get("receipt_id"),
            "effect_observation_graph_status": effect_graph.get("status"),
            "effect_observation_graph_fingerprint": effect_graph.get("graph_fingerprint"),
            "oracle_status_before_gates": status,
        }
    )


def enforce_oracle_validity_gates(
    *,
    result: dict[str, Any],
    experiment: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Demote PROPERTY_HELD/VIOLATION when any validity gate fails."""
    output = deepcopy(_dict(result))
    exp = _dict(experiment)
    ev = _dict(evidence)
    if not ev:
        ev = {
            "observer_receipts": _list(output.get("observer_receipts")),
            "contract_evidence_receipts": _list(output.get("contract_evidence_receipts")),
            "observations": _dict(output.get("observations")),
        }
    effect_graph = build_effect_observation_graph(
        experiment=exp,
        result=output,
        evidence=ev,
    )
    output["effect_observation_graph"] = effect_graph
    receipt = build_oracle_validity_receipt(
        result=output,
        experiment=exp,
        evidence=ev,
    )
    # Keep graph id aligned with the receipt evaluation.
    receipt = dict(receipt)
    receipt["effect_observation_graph_receipt_id"] = effect_graph.get("receipt_id")
    receipt["effect_observation_graph_status"] = effect_graph.get("status")
    receipt["effect_observation_graph_fingerprint"] = effect_graph.get(
        "graph_fingerprint"
    )
    # Re-seal receipt_id after alignment fields.
    body = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt = _receipt(body)
    output["oracle_validity_receipt"] = receipt

    verdict = _dict(output.get("oracle_verdict"))
    if _text(receipt.get("status")) == "NOT_APPLICABLE":
        if verdict:
            output["oracle_verdict"] = {
                **verdict,
                "oracle_validity_gate": "NOT_APPLICABLE",
                "oracle_validity_receipt_id": receipt["receipt_id"],
                "effect_observation_graph_receipt_id": effect_graph.get("receipt_id"),
            }
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
                "customer_deliverable_candidate",
            )
            if key in verdict
        }
        output["oracle_verdict"] = {
            **verdict,
            "status": "INDETERMINATE",
            "verdict": "indeterminate",
            "customer_deliverable": False,
            "customer_deliverable_candidate": False,
            "oracle_validity_gate": "INDETERMINATE",
            "oracle_validity_receipt_id": receipt["receipt_id"],
            "oracle_validity_reason_codes": list(receipt["reason_codes"]),
            "pre_validity_oracle_verdict": original_verdict,
            "effect_observation_graph_receipt_id": effect_graph.get("receipt_id"),
            "effect_observation_graph_status": effect_graph.get("status"),
        }
        output["finding"] = None
        if _text(output.get("status")) not in {"BLOCKED", "HARNESS_FAILURE"}:
            output["status"] = "EXECUTED"
        output["reason_code"] = "ORACLE_VALIDITY_INDETERMINATE"
        output["detail"] = ",".join(list(receipt["reason_codes"])[:12])
        execution_receipt = dict(_dict(output.get("execution_receipt")))
        execution_receipt.update(
            {
                "status": output["status"],
                "reason_code": output["reason_code"],
                "detail": output["detail"],
                "oracle_validity_receipt_id": receipt["receipt_id"],
            }
        )
        output["execution_receipt"] = execution_receipt
        return output

    if verdict:
        output["oracle_verdict"] = {
            **verdict,
            "oracle_validity_gate": "PASSED",
            "oracle_validity_receipt_id": receipt["receipt_id"],
            "effect_observation_graph_receipt_id": effect_graph.get("receipt_id"),
            "effect_observation_graph_status": effect_graph.get("status"),
        }
    finding = _dict(output.get("finding"))
    if finding:
        oracle = dict(_dict(finding.get("oracle")))
        oracle["oracle_validity_receipt_id"] = receipt["receipt_id"]
        oracle["oracle_validity_proven"] = True
        finding["oracle"] = oracle
        evidence_row = dict(_dict(finding.get("evidence")))
        evidence_row.update(
            {
                "oracle_validity_receipt_id": receipt["receipt_id"],
                "effect_observation_graph_receipt_id": effect_graph.get("receipt_id"),
                "effect_observation_graph_status": effect_graph.get("status"),
            }
        )
        finding["evidence"] = evidence_row
        output["finding"] = finding
    return output


__all__ = [
    "SCHEMA_VERSION",
    "build_oracle_validity_receipt",
    "enforce_oracle_validity_gates",
]
