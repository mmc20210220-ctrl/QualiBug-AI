"""Contract-based oracles with explicit activation requirements.

Business oracles may only fire when the treatment path plus the controls,
fixtures, and observers required by the typed experiment contract are present.
Heuristic-only signals are downgraded to internal clues and must not enter
customer-delivery.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .assertion_dsl import (
    evaluate_assertion,
    materialize_assertion,
    validate_assertion_receipt,
)
from .assertion_control_policy import assertion_requires_control
from .observer_contracts_base import validate_observer_receipt


CONTRACT_EVIDENCE_RECEIPT_SCHEMA = "qualibug.contract-evidence-receipt.v1"
ACTIVATION_RECEIPT_SCHEMA = "qualibug.contract-oracle-activation-receipt.v1"
CONTRACT_ORACLE_RECEIPT_SCHEMA = "qualibug.contract-oracle-receipt.v1"

# V1.7 enrichment fields that the authorization causality / delivery gates
# append to an oracle receipt AFTER its receipt_id was computed. Validation
# recomputes the fingerprint over the original payload only, so every reseal
# path must apply the same exclusion. Single source of truth for the validator
# and sealed_receipt_reseal.reseal_oracle_receipt.
CONTRACT_ORACLE_POST_HOC_FIELDS = frozenset({
    "authorization_causality_gate",
    "authorization_causality_reason_codes",
    "authorization_causality_receipt_id",
    "pre_causality_oracle_verdict",
    "authorization_delivery_gate",
    "authorization_delivery_reason",
    "oracle_validity_gate",
    "oracle_validity_reason_codes",
    "oracle_validity_receipt_id",
    "pre_validity_oracle_verdict",
    "effect_observation_graph_receipt_id",
    "effect_observation_graph_status",
})
_CONTRACT_EVIDENCE_KINDS = frozenset({
    "actor",
    "cleanup",
    "control",
    "fixture",
    "lineage",
    "reproduction",
    "treatment",
})
_CONTRACT_EVIDENCE_STATUSES = frozenset({
    "BLOCKED",
    "COMPLETED",
    "FAILED",
    "NOT_REQUIRED",
    "OBSERVED",
    # Accepted-residue degradation (declared non-production targets): the
    # write was deliberately left uncleaned and the leftover is surfaced,
    # never reported as COMPLETED (no cleanup ran) nor FAILED (the finding
    # is still valid). The delivery gate short-circuits on this status.
    "RESIDUE_ACCEPTED",
})


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


def _content_receipt(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "receipt_id": prefix + hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()[:24],
    }


def build_contract_evidence_receipt(
    *,
    kind: str,
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
    subject_id: str,
    status: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_kind = _text(kind).lower()
    normalized_status = _text(status).upper()
    if normalized_kind not in _CONTRACT_EVIDENCE_KINDS:
        raise ValueError(f"contract_evidence_kind_invalid:{normalized_kind}")
    if normalized_status not in _CONTRACT_EVIDENCE_STATUSES:
        raise ValueError(f"contract_evidence_status_invalid:{normalized_status}")
    if not all(
        _text(value)
        for value in (
            experiment_id,
            obligation_id,
            campaign_id,
            execution_id,
            subject_id,
        )
    ):
        raise ValueError("contract_evidence_identity_missing")
    payload = {
        "schema_version": CONTRACT_EVIDENCE_RECEIPT_SCHEMA,
        "kind": normalized_kind,
        "experiment_id": _text(experiment_id),
        "obligation_id": _text(obligation_id),
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "subject_id": _text(subject_id),
        "status": normalized_status,
        "evidence": dict(evidence or {}),
    }
    return _content_receipt("contract_", payload)


def validate_contract_evidence_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(receipt)
    required = {
        "schema_version",
        "receipt_id",
        "kind",
        "experiment_id",
        "obligation_id",
        "campaign_id",
        "execution_id",
        "subject_id",
        "status",
        "evidence",
    }
    if set(row) != required:
        raise ValueError("contract_evidence_receipt_fields_invalid")
    if row.get("schema_version") != CONTRACT_EVIDENCE_RECEIPT_SCHEMA:
        raise ValueError("contract_evidence_receipt_schema_invalid")
    if not isinstance(row.get("evidence"), dict):
        raise ValueError("contract_evidence_receipt_content_invalid")
    expected = build_contract_evidence_receipt(
        kind=_text(row.get("kind")),
        experiment_id=_text(row.get("experiment_id")),
        obligation_id=_text(row.get("obligation_id")),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
        subject_id=_text(row.get("subject_id")),
        status=_text(row.get("status")),
        evidence=dict(row["evidence"]),
    )
    if row != expected:
        raise ValueError("contract_evidence_receipt_fingerprint_invalid")
    return dict(expected)


def _plan_subjects(experiment: dict[str, Any], key: str, prefix: str) -> list[str]:
    subjects: list[str] = []
    for index, raw in enumerate(_list(experiment.get(key))):
        step = _dict(raw)
        subject = _text(step.get("step_id") or step.get("id"))
        if not subject:
            # Multi-write cleanup expansion stamps source_step_id (control_1 /
            # treatment_1). Prefer that over a generic operation index so each
            # cleanup subject stays 1:1 with the write it compensates.
            source_step = _text(step.get("source_step_id"))
            if source_step:
                subject = f"{prefix}:{source_step}"
            else:
                operation = (
                    _text(step.get("operation_ref"))
                    or _text(step.get("compensates_operation_ref"))
                    or "operation"
                )
                subject = f"{prefix}:{operation}:{index + 1}"
        subjects.append(subject)
    return list(dict.fromkeys(subjects))


def _assertions_require_control(experiment: dict[str, Any]) -> bool:
    for raw in _list(experiment.get("assertions")):
        if not isinstance(raw, dict):
            continue
        assertion = materialize_assertion(raw)
        kind = _text(assertion.get("kind") or assertion.get("type"))
        if assertion_requires_control(
            kind,
            explicitly_required=assertion.get("require_control") is True,
        ):
            return True
    return False


def _experiment_has_field_oracle_assertions(experiment: dict[str, Any]) -> bool:
    """True when experiment carries deep field-oracle assertion kinds.

    V1.6.1 SPEC orders Trace before Cleanup Formal Finding. Activation must not
    suppress Field Oracle evaluation solely for cleanup restoration or soft
    INDETERMINATE observers; those remain visible on the Trace / reason codes.
    """
    deep = {
        "conservation",
        "field_delta",
        "postcondition",
        "state_transition",
        "forbidden_state_transition",
        "cross_entity_consistency",
    }
    for raw in _list(experiment.get("assertions")):
        if not isinstance(raw, dict):
            continue
        kind = _text(raw.get("kind")).lower()
        if kind in deep:
            return True
    contract = _dict(experiment.get("field_oracle_runtime_contract"))
    if contract and _text(contract.get("status")).upper() == "RESOLVED":
        return True
    return False


def _any_assertion_has_template(experiment: dict[str, Any], template: str) -> bool:
    """True when any assertion in the experiment uses the given template."""
    for raw in _list(experiment.get("assertions")):
        if not isinstance(raw, dict):
            continue
        if _text(raw.get("template")) == template:
            return True
        prop = raw.get("property") or {}
        if isinstance(prop, dict) and _text(prop.get("template")) == template:
            return True
    return False


def _fixture_cleanup_requirement_materialized(
    *,
    target: str,
    evidence: dict[str, Any],
) -> bool | None:
    """Return whether fallback fixture setup actually created cleanup work.

    ``force_fixture_setup`` means "use setup when the source-declared resolver
    cannot supply an owned resource". It does not prove that setup ran. The
    binding receipt is the runtime authority. Missing or ambiguous evidence
    returns ``None`` so the static cleanup requirement remains fail-closed.
    """
    subject_id = f"fixture_cleanup:{target}"
    if any(
        _text(_dict(receipt).get("kind")) == "cleanup"
        and _text(_dict(receipt).get("subject_id")) == subject_id
        for receipt in _list(evidence.get("contract_evidence_receipts"))
    ):
        return True
    matching = [
        _dict(receipt)
        for receipt in _list(evidence.get("binding_materialization_receipts"))
        if _text(_dict(receipt).get("target")) == target
    ]
    if len(matching) != 1:
        return None
    receipt = matching[0]
    if (
        _text(receipt.get("fixture_cleanup_status"))
        or _text(receipt.get("source_priority")) == "experiment_setup_response"
    ):
        return True
    if _text(receipt.get("status")).upper() == "BOUND":
        return False
    return None


def contract_activation_requirements(
    experiment: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    exp = _dict(experiment)
    controls = _plan_subjects(exp, "control_plan", "control")
    treatments = _plan_subjects(exp, "treatment_plan", "treatment")
    actors = sorted({
        _text(_dict(step).get("actor_ref"))
        for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan"))
        if _text(_dict(step).get("actor_ref"))
    })
    dag = _dict(exp.get("fixture_dag"))
    nodes = {
        _text(_dict(node).get("node_id")): _dict(node)
        for node in _list(dag.get("nodes"))
        if _text(_dict(node).get("node_id"))
    }
    setup_order = [
        _text(item) for item in _list(dag.get("setup_order")) if _text(item)
    ]
    fixtures = list(dict.fromkeys(setup_order or sorted(nodes)))
    actors = sorted(set(actors).union({
        _text(node.get("actor_ref"))
        for node in nodes.values()
        if _text(node.get("actor_ref"))
    }))
    observers = list(dict.fromkeys(
        _text(_dict(observer).get("observer_id"))
        for observer in _list(exp.get("observers"))
        if _text(_dict(observer).get("observer_id"))
    ))
    cleanup = _plan_subjects(exp, "cleanup_plan", "cleanup")
    for binding in _list(exp.get("binding_plan")):
        row = _dict(binding)
        fixture_setup = _dict(row.get("fixture_setup"))
        if (
            row.get("force_fixture_setup") is True
            and _list(fixture_setup.get("cleanup_operations"))
        ):
            target = _text(row.get("target"))
            materialized = (
                _fixture_cleanup_requirement_materialized(
                    target=target,
                    evidence=_dict(evidence),
                )
                if evidence is not None and target
                else None
            )
            if target and materialized is not False:
                cleanup.append(f"fixture_cleanup:{target}")
    return {
        "control": controls,
        "treatment": treatments,
        "actor": actors,
        "fixture": fixtures,
        "observer": observers,
        "cleanup": list(dict.fromkeys(cleanup)),
    }


def build_contract_oracle_activation_receipt(
    *,
    experiment: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    exp = _dict(experiment)
    ev = _dict(evidence)
    experiment_id = _text(exp.get("experiment_id"))
    obligation_id = _text(exp.get("obligation_id"))
    campaign_id = _text(exp.get("campaign_id"))
    execution_id = _text(exp.get("execution_id"))
    if not experiment_id or not obligation_id or not campaign_id or not execution_id:
        raise ValueError("contract_activation_identity_missing")

    required = contract_activation_requirements(exp, evidence=ev)
    blockers: list[str] = []
    harness_failures: list[str] = []

    # Every verified requirement must resolve to a validated runtime receipt.
    source_refs = [
        dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)
    ]
    if not source_refs:
        blockers.append("SOURCE_REFS_MISSING")
    if _assertions_require_control(exp) and not required["control"]:
        # Permit-only single-actor invocations have an intentionally empty
        # control plan — the treatment alone provides sufficient evidence.
        if not _any_assertion_has_template(exp, "permitted_operation_invocation"):
            blockers.append("CONTROL_PLAN_MISSING")
    if not required["treatment"]:
        # Single-arm response-side observation: a response_field_absent
        # assertion consumes only the control read's body (导出结果禁止包含
        # password), so an empty treatment plan is not an input gap. The same
        # holds for a read-side row-state filter (用户端默认仅返回 ON_SALE
        # 商品) — its single control read observes the real rows the rule
        # constrains.
        _single_arm_read_kinds = {
            "response_field_absent",
            "response_rows_state_filter",
            "ui_state_consistency",
        }
        _requires_treatment = not any(
            _text(_dict(assertion).get("kind")) in _single_arm_read_kinds
            for assertion in _list(exp.get("assertions"))
            if isinstance(assertion, dict)
        )
        if _requires_treatment:
            blockers.append("TREATMENT_PLAN_MISSING")
    if not any(isinstance(item, dict) for item in _list(exp.get("assertions"))):
        blockers.append("TYPED_ASSERTION_MISSING")
    if ev.get("harness_error"):
        harness_failures.append("HARNESS_ERROR_PRESENT")

    raw_contract_receipts = ev.get("contract_evidence_receipts", [])
    contract_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(raw_contract_receipts, list):
        harness_failures.append("CONTRACT_EVIDENCE_RECEIPTS_NOT_LIST")
        raw_contract_receipts = []
    for index, raw in enumerate(raw_contract_receipts):
        try:
            validated = validate_contract_evidence_receipt(raw)
        except Exception as exc:
            harness_failures.append(
                f"CONTRACT_EVIDENCE_RECEIPT_INVALID:{index}:{type(exc).__name__}:{exc}"
            )
            continue
        if (
            _text(validated.get("experiment_id")) != experiment_id
            or _text(validated.get("obligation_id")) != obligation_id
            or _text(validated.get("campaign_id")) != campaign_id
            or _text(validated.get("execution_id")) != execution_id
        ):
            harness_failures.append(
                f"CONTRACT_EVIDENCE_LINEAGE_MISMATCH:{index}"
            )
            continue
        key = (_text(validated.get("kind")), _text(validated.get("subject_id")))
        if key in contract_by_key:
            harness_failures.append(
                f"CONTRACT_EVIDENCE_IDENTITY_DUPLICATE:{key[0]}:{key[1]}"
            )
            continue
        contract_by_key[key] = validated
        status = _text(validated.get("status")).upper()
        if status == "FAILED":
            evidence_row = _dict(validated.get("evidence"))
            # Explicit zero-transport marks are binding/governance gaps, not
            # harness infrastructure failures. Keep FAILED→HF for transport-
            # reached requests that still could not produce a usable receipt.
            zero_transport_block = (
                key[0] in {"control", "treatment", "cleanup"}
                and int(evidence_row.get("status_code") or 0) <= 0
                and evidence_row.get("write_reached_transport") is False
            )
            if zero_transport_block:
                blockers.append(
                    f"{key[0].upper()}_RECEIPT_BLOCKED:{key[1]}"
                )
            else:
                harness_failures.append(
                    f"{key[0].upper()}_RECEIPT_FAILED:{key[1]}"
                )
        elif status == "BLOCKED":
            blockers.append(f"{key[0].upper()}_RECEIPT_BLOCKED:{key[1]}")

    verified: dict[str, list[str]] = {
        "control": [],
        "treatment": [],
        "actor": [],
        "fixture": [],
        "observer": [],
        "cleanup": [],
    }
    for kind in ("control", "treatment", "actor", "fixture", "cleanup"):
        for subject in required[kind]:
            receipt = contract_by_key.get((kind, subject))
            if receipt is None:
                blockers.append(f"MISSING_{kind.upper()}_RECEIPT:{subject}")
                continue
            receipt_status = _text(receipt.get("status")).upper()
            receipt_evidence = _dict(receipt.get("evidence"))
            if kind == "cleanup":
                audit_receipt_ids = [
                    _text(item)
                    for item in _list(receipt_evidence.get("audit_receipt_ids"))
                    if _text(item)
                ]
                completed_with_proof = (
                    receipt_status == "COMPLETED"
                    and receipt_evidence.get("restoration_verified") is True
                    and receipt_evidence.get("state_unchanged") is True
                    and int(receipt_evidence.get("accepted_write_count") or 0) > 0
                    and int(receipt_evidence.get("cleanup_write_count") or 0) > 0
                    and bool(audit_receipt_ids)
                )
                # V1.7: Row already absent means the cleanup target is achieved
                # without a compensating write. restoration_verified + state_unchanged
                # + audit receipts are sufficient proof; cleanup_write_count == 0.
                completed_already_absent = (
                    receipt_status == "COMPLETED"
                    and receipt_evidence.get("restoration_verified") is True
                    and receipt_evidence.get("state_unchanged") is True
                    and int(receipt_evidence.get("accepted_write_count") or 0) > 0
                    and int(receipt_evidence.get("cleanup_write_count") or 0) == 0
                    and bool(audit_receipt_ids)
                )
                accepted_unchanged = (
                    receipt_status == "NOT_REQUIRED"
                    and _text(receipt_evidence.get("reason_code"))
                    == "ACCEPTED_WRITE_STATE_UNCHANGED"
                    and int(receipt_evidence.get("accepted_write_count") or 0) > 0
                    and int(receipt_evidence.get("cleanup_write_count") or 0) == 0
                    and receipt_evidence.get("state_unchanged") is True
                    and bool(audit_receipt_ids)
                )
                rejected_unchanged = (
                    receipt_status == "NOT_REQUIRED"
                    and int(receipt_evidence.get("accepted_write_count") or 0) == 0
                    and int(receipt_evidence.get("cleanup_write_count") or 0) == 0
                    and receipt_evidence.get("state_unchanged") is True
                    # Zero-write plans have no write to audit: state_unchanged
                    # (from real governed before/after observations) is the
                    # complete proof that no restoration was needed. Requiring
                    # audit ids here would turn a genuinely untouched arm
                    # (e.g. treatment re-deleting an id control already
                    # removed) into a fabricated cleanup failure.
                )
                not_required_with_proof = accepted_unchanged or rejected_unchanged
                # Accepted-residue degradation (declared non-production targets):
                # the compiler emits an accepted_residue plan only when no
                # compensator resolves on any declared surface and the target is
                # a declared non-production environment; the executor then leaves
                # the write deliberately uncleaned and surfaces the leftover in
                # the receipt. The delivery gate short-circuits on this status
                # (DELIVERABLE, RESIDUE_ACCEPTED); the oracle must treat it as the
                # same environment-gated outcome instead of demanding a
                # restoration that was never due.
                residue_accepted = (
                    receipt_status == "RESIDUE_ACCEPTED"
                    and receipt_evidence.get("residue") is True
                    and _text(receipt_evidence.get("reason_code"))
                    == "ACCEPTED_RESIDUE_NO_CLEANUP"
                )
                if (
                    completed_with_proof
                    or completed_already_absent
                    or not_required_with_proof
                    or residue_accepted
                ):
                    verified[kind].append(_text(receipt.get("receipt_id")))
                    continue
                if receipt_status not in {"FAILED", "BLOCKED"}:
                    # V1.6.1: Field Oracle Trace must be generated before Formal
                    # Finding waits on cleanup. Defer cleanup proof for deep kinds.
                    if not (
                        kind == "cleanup"
                        and _experiment_has_field_oracle_assertions(exp)
                    ):
                        blockers.append(
                            f"CLEANUP_RESTORATION_NOT_PROVEN:{subject}"
                        )
                continue
            if receipt_status != "OBSERVED":
                # Accepted-residue fixture receipts (declared non-production
                # degradation): the fixture was created and deliberately left
                # for a later environment reset. This is a valid setup outcome,
                # not an observation failure; treatment proof is still gated
                # separately below.
                if (
                    kind == "fixture"
                    and receipt_status == "RESIDUE_ACCEPTED"
                    and receipt_evidence.get("residue") is True
                    and _text(receipt_evidence.get("reason_code"))
                    == "ACCEPTED_RESIDUE_NO_CLEANUP"
                ):
                    verified[kind].append(_text(receipt.get("receipt_id")))
                    continue
                if receipt_status not in {"FAILED", "BLOCKED"}:
                    blockers.append(
                        f"{kind.upper()}_RECEIPT_NOT_SATISFIED:{subject}"
                    )
                continue
            if kind == "control" and not (
                receipt_evidence.get("response_observed") is True
                and receipt_evidence.get("control_succeeded") is True
                and int(receipt_evidence.get("status_code") or 0) > 0
            ):
                # UI page-observation control: the step attests it was planned
                # on the ui_browser surface, and rendering proof lives in the
                # ui_browser observer receipt (a real browser opened the URL).
                # An HTTP status line does not exist on this surface and must
                # never be fabricated.
                if _text(receipt_evidence.get("surface")) != "ui_browser":
                    blockers.append(f"CONTROL_SUCCESS_NOT_PROVEN:{subject}")
                    continue
                _ui_rendered = any(
                    _dict(_dict(row).get("evidence")).get("ui_browser") is True
                    and _text(_dict(_dict(row).get("evidence")).get("ui_url"))
                    for row in _list(ev.get("observer_receipts"))
                    if _text(_dict(row).get("observer_id")) == "ui_browser"
                    and _text(_dict(row).get("status")).upper() == "OBSERVED"
                )
                if not _ui_rendered:
                    blockers.append(f"CONTROL_UI_NOT_RENDERED:{subject}")
                    continue
            if kind == "treatment" and not (
                receipt_evidence.get("response_observed") is True
                and int(receipt_evidence.get("status_code") or 0) > 0
            ):
                blockers.append(f"TREATMENT_OBSERVATION_NOT_PROVEN:{subject}")
                continue
            verified[kind].append(_text(receipt.get("receipt_id")))

    raw_observers = ev.get("observer_receipts", [])
    observer_by_id: dict[str, list[dict[str, Any]]] = {}
    observer_scope_keys: set[tuple[str, str]] = set()
    if not isinstance(raw_observers, list):
        harness_failures.append("OBSERVER_RECEIPTS_NOT_LIST")
        raw_observers = []
    for index, raw in enumerate(raw_observers):
        try:
            observer = validate_observer_receipt(raw)
        except Exception as exc:
            harness_failures.append(
                f"OBSERVER_RECEIPT_INVALID:{index}:{type(exc).__name__}:{exc}"
            )
            continue
        observer_id = _text(observer.get("observer_id"))
        if (
            _text(observer.get("campaign_id")) != campaign_id
            or _text(observer.get("execution_id")) != execution_id
        ):
            harness_failures.append(
                f"OBSERVER_RECEIPT_LINEAGE_MISMATCH:{observer_id or index}"
            )
            continue
        # Exact process-step scoping emits one http_response receipt per plan
        # step (control_1, treatment_1), each carrying evidence.step_id. Those
        # are not duplicates: every receipt proves a different plan step's
        # observation. Only the same observer_id with the same step scope is a
        # true duplicate.
        _obs_scope = _text(_dict(observer.get("evidence")).get("step_id"))
        _obs_key = (observer_id, _obs_scope)
        if _obs_key in observer_scope_keys:
            harness_failures.append(f"OBSERVER_RECEIPT_DUPLICATE:{observer_id}")
            continue
        observer_scope_keys.add(_obs_key)
        observer_by_id.setdefault(observer_id, []).append(observer)
        observer_status = _text(observer.get("status")).upper()
        if observer_status in {"FAILED", "UNSUPPORTED"}:
            observer_evidence = _dict(observer.get("evidence"))
            # Step-scoped http_response FAILED with an explicit zero-transport
            # mark is missing observation, not harness infrastructure failure.
            if (
                observer_id == "http_response"
                and observer_status == "FAILED"
                and int(observer_evidence.get("status_code") or 0) <= 0
                and observer_evidence.get("write_reached_transport") is False
            ):
                blockers.append(f"OBSERVER_RECEIPT_INDETERMINATE:{observer_id}")
            else:
                harness_failures.append(
                    f"OBSERVER_RECEIPT_{observer_status}:{observer_id}"
                )
    for observer_id in required["observer"]:
        observer_rows = observer_by_id.get(observer_id) or []
        if not observer_rows:
            blockers.append(f"MISSING_OBSERVER_RECEIPT:{observer_id}")
            continue
        observed_rows = [
            row
            for row in observer_rows
            if _text(row.get("status")).upper() == "OBSERVED"
        ]
        if not observed_rows:
            # V1.6.1: allow Field Oracle evaluation to emit an INDETERMINATE
            # Trace rather than suppressing the oracle call entirely.
            if (
                any(
                    _text(row.get("status")).upper() == "INDETERMINATE"
                    for row in observer_rows
                )
                and not _experiment_has_field_oracle_assertions(exp)
            ):
                blockers.append(f"OBSERVER_RECEIPT_INDETERMINATE:{observer_id}")
            continue
        # Record one verified receipt per required observer; step-scoped
        # siblings stay in the evidence for assertion evaluation while the
        # verified index remains 1:1 with the required observer set.
        verified["observer"].append(_text(observed_rows[0].get("receipt_id")))

    # V1.7: When authorization_comparison is OBSERVED with leak_detected=True,
    # the authorization violation is already proven by status/effect comparison.
    # Supplementary observers (business_effect, entity_state) that returned
    # INDETERMINATE due to missing readback endpoints are redundant evidence —
    # they cannot invalidate the proven leak. Remove their blockers and count
    # them as verified (redundant) so receipt semantics remain consistent.
    _auth_comp_rows = observer_by_id.get("authorization_comparison") or []
    _auth_comp = _auth_comp_rows[0] if _auth_comp_rows else None
    if _auth_comp and _text(_auth_comp.get("status")).upper() == "OBSERVED":
        _auth_evidence = _auth_comp.get("evidence") or {}
        if isinstance(_auth_evidence, dict) and _auth_evidence.get("leak_detected") is True:
            _redundant_indeterminate = {
                "OBSERVER_RECEIPT_INDETERMINATE:business_effect",
                "OBSERVER_RECEIPT_INDETERMINATE:entity_state",
            }
            blockers = [b for b in blockers if b not in _redundant_indeterminate]
            # Add redundant observers to verified so len(verified)==len(required)
            for _red_oid in ("business_effect", "entity_state"):
                _red_rows = observer_by_id.get(_red_oid) or []
                _red_obs = _red_rows[0] if _red_rows else None
                if (
                    _red_obs
                    and _text(_red_obs.get("status")).upper() == "INDETERMINATE"
                    and _red_oid in required["observer"]
                ):
                    verified["observer"].append(_text(_red_obs.get("receipt_id")))

    # V1.7: Validation/invariant family experiments assert on HTTP response
    # status (400/422/403). The write is expected to be REJECTED, so no state
    # change occurs. business_effect/entity_state observers returning
    # INDETERMINATE is the expected outcome (nothing to observe), not a
    # blocker. The response-status assertions are the primary evidence.
    _exp_family = _text(exp.get("risk_family")).lower()
    if _exp_family in {"validation", "invariant"}:
        _response_only_indeterminate = {
            "OBSERVER_RECEIPT_INDETERMINATE:business_effect",
            "OBSERVER_RECEIPT_INDETERMINATE:entity_state",
        }
        blockers = [b for b in blockers if b not in _response_only_indeterminate]
        for _red_oid in ("business_effect", "entity_state"):
            _red_rows = observer_by_id.get(_red_oid) or []
            _red_obs = _red_rows[0] if _red_rows else None
            if (
                _red_obs
                and _text(_red_obs.get("status")).upper() == "INDETERMINATE"
                and _red_oid in required["observer"]
            ):
                verified["observer"].append(_text(_red_obs.get("receipt_id")))

    reason_codes = sorted(set([*harness_failures, *blockers]))
    status = (
        "HARNESS_FAILED"
        if harness_failures
        else "BLOCKED"
        if blockers
        else "ACTIVE"
    )
    field_oracle_soft = bool(
        _experiment_has_field_oracle_assertions(exp) and status == "ACTIVE"
    )
    payload = {
        "schema_version": ACTIVATION_RECEIPT_SCHEMA,
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "campaign_id": campaign_id,
        "execution_id": execution_id,
        "status": status,
        "reason_codes": reason_codes,
        "required": {key: list(value) for key, value in required.items()},
        "verified_receipt_ids": {
            key: sorted(set(value)) for key, value in verified.items()
        },
        "source_refs": source_refs,
    }
    if field_oracle_soft:
        # Optional enrichment — Trace-before-cleanup soft activation.
        payload["field_oracle_soft_activation"] = True
    return _content_receipt("activation_", payload)


def validate_contract_oracle_activation_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(receipt)
    required_fields = {
        "schema_version",
        "receipt_id",
        "experiment_id",
        "obligation_id",
        "campaign_id",
        "execution_id",
        "status",
        "reason_codes",
        "required",
        "verified_receipt_ids",
        "source_refs",
    }
    if set(row) != required_fields:
        optional_fields = {"field_oracle_soft_activation"}
        unexpected = sorted(set(row) - required_fields - optional_fields)
        absent = sorted(required_fields - set(row))
        if unexpected or absent:
            raise ValueError(
                "activation_receipt_fields_invalid"
                + (f":unexpected={','.join(unexpected)}" if unexpected else "")
                + (f":missing={','.join(absent)}" if absent else "")
            )
    if row.get("schema_version") != ACTIVATION_RECEIPT_SCHEMA:
        raise ValueError("activation_receipt_schema_invalid")
    if _text(row.get("status")) not in {"ACTIVE", "BLOCKED", "HARNESS_FAILED"}:
        raise ValueError("activation_receipt_status_invalid")
    if not all(
        isinstance(row.get(field), expected_type)
        for field, expected_type in (
            ("reason_codes", list),
            ("required", dict),
            ("verified_receipt_ids", dict),
            ("source_refs", list),
        )
    ):
        raise ValueError("activation_receipt_content_invalid")
    if not all(
        _text(row.get(field))
        for field in (
            "experiment_id",
            "obligation_id",
            "campaign_id",
            "execution_id",
        )
    ):
        raise ValueError("activation_receipt_identity_missing")
    requirement_keys = {
        "control",
        "treatment",
        "actor",
        "fixture",
        "observer",
        "cleanup",
    }
    required = _dict(row.get("required"))
    verified = _dict(row.get("verified_receipt_ids"))
    if set(required) != requirement_keys or set(verified) != requirement_keys:
        raise ValueError("activation_receipt_requirement_keys_invalid")
    if not all(
        isinstance(required[key], list) and isinstance(verified[key], list)
        for key in requirement_keys
    ):
        raise ValueError("activation_receipt_requirement_content_invalid")
    status = _text(row.get("status"))
    reason_codes = [_text(item) for item in _list(row.get("reason_codes"))]
    soft_field_oracle = row.get("field_oracle_soft_activation") is True
    if (
        (status == "ACTIVE" and reason_codes)
        or (status != "ACTIVE" and not reason_codes)
        or (
            status == "ACTIVE"
            and (
                not required["treatment"]
                or not row.get("source_refs")
                or any(
                    len(verified[key]) != len(required[key])
                    for key in requirement_keys
                    if not (
                        soft_field_oracle and key in {"cleanup", "observer"}
                    )
                )
            )
        )
    ):
        raise ValueError("activation_receipt_semantics_invalid")
    payload = {key: value for key, value in row.items() if key != "receipt_id"}
    expected = _content_receipt("activation_", payload)
    if row != expected:
        raise ValueError("activation_receipt_fingerprint_invalid")
    return dict(expected)


def activation_requirements_met(
    experiment: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[bool, list[str]]:
    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )
    return activation["status"] == "ACTIVE", list(activation["reason_codes"])


def mark_as_internal_clue(finding: dict[str, Any], *, reason: str) -> dict[str, Any]:
    row = dict(finding)
    row["customer_delivery_status"] = "clue"
    row["gate_passed"] = False
    row["oracle_tier"] = "internal_clue"
    row["oracle_demotion_reason"] = _text(reason)
    return row


def _contract_oracle_receipt(
    *,
    experiment: dict[str, Any],
    status: str,
    verdict: str,
    activation: dict[str, Any],
    assertions: list[dict[str, Any]],
    missing_requirements: list[str],
    demotion_reason: str = "",
) -> dict[str, Any]:
    violations = [item for item in assertions if item.get("status") == "VIOLATION"]
    indeterminate = [
        item for item in assertions if item.get("status") == "INDETERMINATE"
    ]
    field_oracle_traces = [
        dict(item.get("field_oracle_trace"))
        for item in assertions
        if isinstance(item, dict) and isinstance(item.get("field_oracle_trace"), dict)
    ]
    payload = {
        "schema_version": CONTRACT_ORACLE_RECEIPT_SCHEMA,
        "experiment_id": _text(experiment.get("experiment_id")),
        "obligation_id": _text(experiment.get("obligation_id")),
        "campaign_id": _text(experiment.get("campaign_id")),
        "execution_id": _text(experiment.get("execution_id")),
        "status": _text(status).upper(),
        "verdict": _text(verdict),
        "customer_deliverable": False,
        "customer_deliverable_candidate": _text(status).upper() == "VIOLATION",
        "activation_receipt_id": _text(activation.get("receipt_id")),
        "activation_receipt": dict(activation),
        "assertion_receipt_ids": [
            _text(item.get("receipt_id")) for item in assertions
        ],
        "violation_assertion_receipt_ids": [
            _text(item.get("receipt_id")) for item in violations
        ],
        "indeterminate_assertion_receipt_ids": [
            _text(item.get("receipt_id")) for item in indeterminate
        ],
        "assertions": [dict(item) for item in assertions],
        "failed_assertions": [dict(item) for item in violations],
        # V1.6.1: surface Field Oracle Traces at receipt root for audit walkers.
        "field_oracle_traces": field_oracle_traces,
        "field_oracle_trace_count": len(field_oracle_traces),
        "missing_requirements": sorted(
            set(_text(item) for item in missing_requirements if _text(item))
        ),
        "demotion_reason": _text(demotion_reason),
    }
    return _content_receipt("oracle_", payload)


def validate_contract_oracle_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    required_fields = {
        "schema_version",
        "receipt_id",
        "experiment_id",
        "obligation_id",
        "campaign_id",
        "execution_id",
        "status",
        "verdict",
        "customer_deliverable",
        "customer_deliverable_candidate",
        "activation_receipt_id",
        "activation_receipt",
        "assertion_receipt_ids",
        "violation_assertion_receipt_ids",
        "indeterminate_assertion_receipt_ids",
        "assertions",
        "failed_assertions",
        "missing_requirements",
        "demotion_reason",
    }
    if set(row) != required_fields:
        # V1.6.1: field_oracle_traces are optional enrichments on deep oracle receipts.
        # V1.7: authorization_causality_* are optional enrichments when the
        # authorization comparison observer proves a leak and the causality gate
        # annotates the receipt with its assessment.
        optional_fields = {
            "field_oracle_traces",
            "field_oracle_trace_count",
            *CONTRACT_ORACLE_POST_HOC_FIELDS,
        }
        unexpected = sorted(set(row) - required_fields - optional_fields)
        absent = sorted(required_fields - set(row))
        if unexpected or absent:
            # Name the offending keys. "contract_oracle_receipt_fields_invalid" on its own
            # cannot be acted on: the receipt has twenty fields and the check is exact set
            # equality, so the reader has to diff by hand against a list in the source. This
            # exact error failed the whole pipeline on a live target while the scan API
            # reported ok: true, and the message was the only thing available.
            raise ValueError(
                "contract_oracle_receipt_fields_invalid"
                + (f":unexpected={','.join(unexpected)}" if unexpected else "")
                + (f":missing={','.join(absent)}" if absent else "")
            )
    if row.get("schema_version") != CONTRACT_ORACLE_RECEIPT_SCHEMA:
        raise ValueError("contract_oracle_receipt_schema_invalid")
    activation = validate_contract_oracle_activation_receipt(
        _dict(row.get("activation_receipt"))
    )
    if _text(row.get("activation_receipt_id")) != _text(activation.get("receipt_id")):
        raise ValueError("contract_oracle_activation_identity_mismatch")
    for field in (
        "experiment_id",
        "obligation_id",
        "campaign_id",
        "execution_id",
    ):
        if not _text(row.get(field)) or _text(row.get(field)) != _text(
            activation.get(field)
        ):
            raise ValueError("contract_oracle_lineage_mismatch")
    assertions = [
        validate_assertion_receipt(_dict(item))
        for item in _list(row.get("assertions"))
    ]
    if any(
        _text(item.get("campaign_id")) != _text(row.get("campaign_id"))
        or _text(item.get("execution_id")) != _text(row.get("execution_id"))
        for item in assertions
    ):
        raise ValueError("contract_oracle_assertion_lineage_mismatch")
    if [_text(item.get("receipt_id")) for item in assertions] != list(
        row.get("assertion_receipt_ids") or []
    ):
        raise ValueError("contract_oracle_assertion_identity_mismatch")
    violations = [item for item in assertions if item.get("status") == "VIOLATION"]
    indeterminate = [
        item for item in assertions if item.get("status") == "INDETERMINATE"
    ]
    assertion_harness = [
        item for item in assertions if item.get("harness_error") is True
    ]
    activation_status = _text(activation.get("status"))
    if activation_status == "HARNESS_FAILED":
        expected_status = "HARNESS_FAILED"
        expected_verdict = "harness_failure"
        expected_missing = list(activation.get("reason_codes") or [])
        expected_demotion = ""
    elif activation_status != "ACTIVE":
        if assertions:
            raise ValueError("contract_oracle_blocked_assertions_present")
        expected_status = "BLOCKED"
        expected_verdict = "blocked_experiment"
        expected_missing = list(activation.get("reason_codes") or [])
        expected_demotion = ""
    elif assertion_harness:
        expected_status = "HARNESS_FAILED"
        expected_verdict = "harness_failure"
        expected_missing = [
            _text(item.get("reason_code")) for item in assertion_harness
        ]
        expected_demotion = ""
    elif indeterminate:
        expected_status = "INDETERMINATE"
        expected_verdict = "indeterminate"
        expected_missing = [
            _text(item.get("reason_code")) for item in indeterminate
        ]
        expected_demotion = "assertion_evidence_indeterminate"
    elif violations:
        expected_status = "VIOLATION"
        expected_verdict = "customer_deliverable_defect_candidate"
        expected_missing = []
        expected_demotion = ""
    else:
        expected_status = "PROPERTY_HELD"
        expected_verdict = "property_held"
        expected_missing = []
        expected_demotion = ""
    expected_violation_ids = [
        _text(item.get("receipt_id")) for item in violations
    ]
    expected_indeterminate_ids = [
        _text(item.get("receipt_id")) for item in indeterminate
    ]
    if any((
        _text(row.get("status")) != expected_status,
        _text(row.get("verdict")) != expected_verdict,
        row.get("customer_deliverable") is not False,
        row.get("customer_deliverable_candidate")
        is not (expected_status == "VIOLATION"),
        list(row.get("violation_assertion_receipt_ids") or [])
        != expected_violation_ids,
        list(row.get("indeterminate_assertion_receipt_ids") or [])
        != expected_indeterminate_ids,
        list(row.get("failed_assertions") or []) != violations,
        list(row.get("missing_requirements") or [])
        != sorted(set(expected_missing)),
        _text(row.get("demotion_reason")) != expected_demotion,
    )):
        # V1.7: The authorization causality gate legitimately overrides the
        # oracle verdict (VIOLATION → INDETERMINATE) when causal proof is
        # insufficient. When pre_causality_oracle_verdict is present, validate
        # the ORIGINAL verdict against assertions and accept the override.
        # The authorization delivery gate and SPEC oracle validity gates also
        # override verdicts post-hoc (demote only).
        _pre_causality = row.get("pre_causality_oracle_verdict")
        _pre_validity = row.get("pre_validity_oracle_verdict")
        _delivery_gate_override = bool(row.get("authorization_delivery_gate"))
        _validity_gate_override = bool(row.get("oracle_validity_gate"))
        if isinstance(_pre_causality, dict) and _pre_causality:
            _pre_ok = (
                _text(_pre_causality.get("status")) == expected_status
                and _text(_pre_causality.get("verdict")) == expected_verdict
            )
            if not _pre_ok:
                raise ValueError("contract_oracle_semantics_invalid")
        elif isinstance(_pre_validity, dict) and _pre_validity:
            _pre_ok = (
                _text(_pre_validity.get("status")) == expected_status
                and _text(_pre_validity.get("verdict")) == expected_verdict
            )
            if not _pre_ok:
                raise ValueError("contract_oracle_semantics_invalid")
        elif _delivery_gate_override or _validity_gate_override:
            pass  # post-hoc demotion gates legitimately override verdict
        else:
            raise ValueError("contract_oracle_semantics_invalid")
    # V1.7: The authorization causality gate (PASSED path) appends enrichment
    # fields AFTER the receipt_id was computed. Strip them to recover the
    # original payload, verify the fingerprint, then accept the full row.
    _post_hoc_fields = CONTRACT_ORACLE_POST_HOC_FIELDS
    payload = {
        key: value
        for key, value in row.items()
        if key != "receipt_id" and key not in _post_hoc_fields
    }
    expected = _content_receipt("oracle_", payload)
    if _text(row.get("receipt_id")) != _text(expected.get("receipt_id")):
        raise ValueError("contract_oracle_receipt_fingerprint_invalid")
    # Verify base content matches (post-hoc enrichments accepted as-is)
    _base_row = {k: v for k, v in row.items() if k not in _post_hoc_fields}
    if _base_row != expected:
        raise ValueError("contract_oracle_receipt_fingerprint_invalid")
    return dict(row)


def evaluate_contract_oracle(
    *,
    experiment: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Resolve only fully activated typed assertions; gaps never become defects."""
    exp = _dict(experiment)
    ev = _dict(evidence)
    activation = build_contract_oracle_activation_receipt(
        experiment=exp,
        evidence=ev,
    )
    activation_status = _text(activation.get("status"))
    if activation_status == "HARNESS_FAILED":
        return _contract_oracle_receipt(
            experiment=exp,
            status="HARNESS_FAILED",
            verdict="harness_failure",
            activation=activation,
            assertions=[],
            missing_requirements=list(activation.get("reason_codes") or []),
        )
    if activation_status != "ACTIVE":
        return _contract_oracle_receipt(
            experiment=exp,
            status="BLOCKED",
            verdict="blocked_experiment",
            activation=activation,
            assertions=[],
            missing_requirements=list(activation.get("reason_codes") or []),
        )

    assertion_results: list[dict[str, Any]] = []
    try:
        for assertion in _list(exp.get("assertions")):
            if not isinstance(assertion, dict):
                continue
            result = evaluate_assertion(
                assertion,
                observations=ev,
                source_refs=list(exp.get("source_refs") or []),
                campaign_id=_text(exp.get("campaign_id")),
                execution_id=_text(exp.get("execution_id")),
            )
            assertion_results.append(validate_assertion_receipt(result))
    except Exception as exc:
        return _contract_oracle_receipt(
            experiment=exp,
            status="HARNESS_FAILED",
            verdict="harness_failure",
            activation=activation,
            assertions=assertion_results,
            missing_requirements=[
                f"ASSERTION_RECEIPT_INVALID:{type(exc).__name__}:{exc}"
            ],
        )

    assertion_harness = [
        item for item in assertion_results if item.get("harness_error") is True
    ]
    indeterminate = [
        item for item in assertion_results if item.get("status") == "INDETERMINATE"
    ]
    violations = [
        item for item in assertion_results if item.get("status") == "VIOLATION"
    ]
    if assertion_harness:
        return _contract_oracle_receipt(
            experiment=exp,
            status="HARNESS_FAILED",
            verdict="harness_failure",
            activation=activation,
            assertions=assertion_results,
            missing_requirements=sorted(set(
                list(activation.get("reason_codes") or [])
                + [_text(item.get("reason_code")) for item in assertion_harness]
            )),
        )
    if indeterminate:
        return _contract_oracle_receipt(
            experiment=exp,
            status="INDETERMINATE",
            verdict="indeterminate",
            activation=activation,
            assertions=assertion_results,
            missing_requirements=sorted(set(
                list(activation.get("reason_codes") or [])
                + [_text(item.get("reason_code")) for item in indeterminate]
            )),
            demotion_reason="assertion_evidence_indeterminate",
        )
    if violations:
        return _contract_oracle_receipt(
            experiment=exp,
            status="VIOLATION",
            verdict="customer_deliverable_defect_candidate",
            activation=activation,
            assertions=assertion_results,
            missing_requirements=list(activation.get("reason_codes") or []),
        )
    return _contract_oracle_receipt(
        experiment=exp,
        status="PROPERTY_HELD",
        verdict="property_held",
        activation=activation,
        assertions=assertion_results,
        missing_requirements=list(activation.get("reason_codes") or []),
    )


HEURISTIC_BUSINESS_ORACLE_NAMES = frozenset({
    "concurrencyoracle",
    "concurrency",
    "idempotencyoracle",
    "idempotency",
    "permissionoracle",
    "privacyoracle",
    "stateoracle",
    "moneyoracle",
    "inventoryoracle",
    "workfloworacle",
    "quotaoracle",
    "tenantisolationoracle",
})


def contract_effect_evidence_present(evidence: dict[str, Any]) -> bool:
    """True when evidence carries a business effect or final invariant observation."""
    ev = _dict(evidence)
    return (
        ev.get("effect_count") is not None
        or ev.get("invariant_held") is not None
        or ev.get("business_effect_observed") is True
        or ev.get("final_state_observation") is not None
    )


def heuristic_status_only_signal(evidence: dict[str, Any]) -> bool:
    """HTTP dual-2xx / multi-success without contract effect is clue-only."""
    ev = _dict(evidence)
    if contract_effect_evidence_present(ev):
        return False
    return bool(
        ev.get("dual_2xx")
        or ev.get("multi_success_http")
        or (ev.get("effect_count") is None and ev.get("invariant_held") is None)
    )


def scenario_has_contract_activation(scenario: dict[str, Any]) -> bool:
    """Scenario may produce customer-deliverable business-oracle defects only with contract."""
    sc = _dict(scenario)
    embedded = _dict(
        sc.get("contract_oracle_activation_receipt")
        or sc.get("activation_receipt")
    )
    exp = _dict(sc.get("experiment_contract") or sc.get("experiment"))
    evidence = _dict(sc.get("contract_evidence") or sc.get("evidence"))
    if not exp or not isinstance(evidence.get("contract_evidence_receipts"), list):
        return False
    try:
        recomputed = build_contract_oracle_activation_receipt(
            experiment=exp,
            evidence=evidence,
        )
        if embedded:
            validated = validate_contract_oracle_activation_receipt(embedded)
            if validated != recomputed:
                return False
    except ValueError:
        return False
    return recomputed.get("status") == "ACTIVE"


def demote_heuristic_business_oracle_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Downgrade legacy heuristic business oracle hits to internal clues."""
    row = _dict(finding)
    oracle = _dict(row.get("oracle"))
    name = _text(oracle.get("oracle_name") or oracle.get("name") or row.get("category")).lower()
    compact = name.replace(" ", "")
    evidence = _dict(row.get("evidence") or row.get("raw_evidence"))
    if oracle.get("oracle_tier") == "internal_clue" or oracle.get("customer_deliverable") is False:
        return mark_as_internal_clue(row, reason=_text(oracle.get("demotion_reason") or "oracle_marked_non_deliverable"))
    matched = compact in HEURISTIC_BUSINESS_ORACLE_NAMES or any(
        token in compact for token in HEURISTIC_BUSINESS_ORACLE_NAMES
    )
    if not matched:
        return row
    has_control = bool(evidence.get("control_succeeded") or evidence.get("authorized_control") or evidence.get("control_observation"))
    has_effect = contract_effect_evidence_present(evidence)
    # Status-only concurrency/idempotency heuristics never enter customer delivery.
    if "concurr" in compact or "idempot" in compact:
        if not has_control or not has_effect or heuristic_status_only_signal(evidence):
            return mark_as_internal_clue(row, reason="heuristic_business_oracle_without_contract")
    elif not has_control or not has_effect:
        return mark_as_internal_clue(row, reason="heuristic_business_oracle_without_contract")
    return row
