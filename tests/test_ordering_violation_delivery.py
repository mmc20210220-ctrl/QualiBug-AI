"""Regression: runtime-observed ordering violations (FIFO/FEFO) deliver findings.

The body-field probe discovers an undocumented policy-endpoint field and a
source-declared allocation rule (PRD: 普通批次按 FIFO) is violated at runtime.
Three distinct failure modes are covered:

1. The oracle's ordering branch returned a raw assertion dict + empty
   activation, which ``validate_assertion_receipt`` and
   ``validate_contract_oracle_receipt`` reject with
   ``assertion_receipt_fields_invalid`` / ``activation_receipt_fields_invalid``,
   killing the whole experiment group before a finding could be created.
2. The probe-reissued write (a read-only decision-endpoint call) was treated as
   a governed business write requiring cleanup equivalence, so the finalizer
   demoted the proven VIOLATION to ``BLOCKED_CLEANUP_EQUIVALENCE_MISSING``.
3. The policy-probe obligation generator compiled probe obligations for every
   service's endpoints on a single-service run; cross-service paths 404 against
   the target base_url and surface as routing artifacts, never defects.
"""
from __future__ import annotations

import json

from ai_test_asset_center import _contract_oracles_mechanics as _oracle_core
from ai_test_asset_center import _contract_oracles_outcome_mechanics as _oracle_outcome
from ai_test_asset_center._contract_oracles_mechanics import (
    build_contract_evidence_receipt,
)
from ai_test_asset_center.contract_oracles import (
    validate_contract_oracle_receipt,
)
from ai_test_asset_center.experiment_outcome_finalizer_core import (
    _actual_accepted_business_write,
)
from ai_test_asset_center.observer_contracts import observe_experiment_requirements


def _ordering_experiment() -> dict:
    return {
        "experiment_id": "exp_ordering",
        "obligation_id": "obl_ordering",
        "campaign_id": "CMP_ordering",
        "execution_id": "EXEC_ordering",
        "risk_family": "validation",
        "source_refs": [
            {"source_id": "api_spec", "locator": "POST /api/wms/lot-trace-order/check"}
        ],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "actor_ref": "actor_admin",
                "operation_ref": "op_lot_trace",
                "body": {},
            }
        ],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "actor_identity"},
        ],
        "assertions": [{"kind": "http_status_class", "expected_class": 2}],
    }


def _ordering_evidence() -> dict:
    """Step-executor shaped observations for a probe-detected ordering break."""
    observations = {
        "_ordering_violation": {
            "ordering": "FIFO",
            "expected": "earliest batch (FIFO)",
            "observed": {"result": "BATCH-NEW"},
            "path": "/api/v1/wms/inventory/lot-trace-order/check",
            "operation_ref": "op_lot_trace",
            "violation": True,
        },
        "control_observation": None,
        "treatment_observation": {"status_code": 200, "body": {"result": "BATCH-NEW"}},
        "treatment_actor_ref": "actor_admin",
        "control_actor_ref": "",
        "status_code": 200,
        "contract_evidence_receipts": [
            build_contract_evidence_receipt(
                kind="treatment",
                experiment_id="exp_ordering",
                obligation_id="obl_ordering",
                campaign_id="CMP_ordering",
                execution_id="EXEC_ordering",
                subject_id="treatment_1",
                status="OBSERVED",
                evidence={"status_code": 200, "response_observed": True},
            ),
            build_contract_evidence_receipt(
                kind="actor",
                experiment_id="exp_ordering",
                obligation_id="obl_ordering",
                campaign_id="CMP_ordering",
                execution_id="EXEC_ordering",
                subject_id="actor_admin",
                status="OBSERVED",
                evidence={"role": "admin", "credential_material_observed": True},
            ),
        ],
        "execution_steps": [],
        "binding_materialization_receipts": [],
    }
    observations["observer_receipts"] = observe_experiment_requirements(
        _ordering_experiment(),
        observations=observations,
        campaign_id="CMP_ordering",
        execution_id="EXEC_ordering",
    )
    return observations


def test_ordering_violation_oracle_receipt_survives_strict_validation() -> None:
    """The ordering branch seals a valid receipt end-to-end (no ValueError)."""
    exp = _ordering_experiment()
    evidence = _ordering_evidence()
    # The outcome facade validates every assertion as a sealed receipt.
    verdict = _oracle_outcome.evaluate_contract_oracle(
        experiment=exp, evidence=evidence
    )
    assert verdict["status"] == "VIOLATION"
    assert verdict["verdict"] == "customer_deliverable_defect_candidate"
    # The batch executor validates the sealed oracle receipt strictly.
    validated = validate_contract_oracle_receipt(verdict)
    assert validated["status"] == "VIOLATION"
    assert validated["verdict"] == "customer_deliverable_defect_candidate"
    assert validated["assertions"][0]["kind"] == "source_ordering_rule"
    assert validated["assertions"][0]["reason_code"] == "SOURCE_ORDERING_RULE_VIOLATED"


def test_ordering_violation_oracle_activation_is_active_with_real_evidence() -> None:
    """The probe path produces real observer/actor receipts, so activation is ACTIVE."""
    exp = _ordering_experiment()
    evidence = _ordering_evidence()
    activation = _oracle_core.build_contract_oracle_activation_receipt(
        experiment=exp, evidence=evidence
    )
    assert activation["status"] == "ACTIVE"
    assert activation["reason_codes"] == []


def test_probe_write_is_not_an_accepted_business_write() -> None:
    """A probe-reissued decision-endpoint write never demands cleanup equivalence."""
    steps_probe = [
        {
            "phase": "treatment",
            "method": "POST",
            "path": "/api/v1/wms/inventory/lot-trace-order/check",
            "status_code": 200,
            "governance_receipt": {
                "accepted": True,
                "_undocumented_field_probe": {"field": "candidates"},
            },
        }
    ]
    assert not _actual_accepted_business_write(
        exp={"safety_contract": {"governed_write": False}}, steps_out=steps_probe
    )
    # A genuine entity write still demands cleanup.
    steps_write = [
        {
            "phase": "treatment",
            "method": "POST",
            "path": "/api/v1/scm/purchase-orders",
            "status_code": 200,
            "governance_receipt": {"accepted": True},
        }
    ]
    assert _actual_accepted_business_write(
        exp={"safety_contract": {"governed_write": False}}, steps_out=steps_write
    )


def test_probe_generator_filters_by_target_service() -> None:
    """The policy-probe obligation generator never emits cross-service probes."""
    from ai_test_asset_center.behavior_ir_core import (
        build_behavior_ir_from_knowledge_asset,
    )
    from ai_test_asset_center.obligation_compiler import (
        compile_obligations_from_behavior_ir,
    )

    asset = {
        "sources": [],
        "interfaces": [
            {
                "method": "POST",
                "path": "/api/v1/wms/inventory/lot-trace-order/check",
                "operation_id": "wms_lot_trace_check",
                "source_ids": ["src_wms"],
                "canonical_contract_source_id": "src_wms",
                "source_locators": [
                    "wms_inventory_service.json#block=json-pointer:/paths/~1api~1v1~1wms~1inventory~1lot-trace-order~1check/post"
                ],
                "request_schema": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "additionalProperties": True}
                        }
                    },
                },
            },
            {
                "method": "POST",
                "path": "/api/v1/auth/controls/reset-token-once/check",
                "operation_id": "auth_reset_token_check",
                "source_ids": ["src_auth"],
                "canonical_contract_source_id": "src_auth",
                "source_locators": [
                    "auth_service.json#block=json-pointer:/paths/~1api~1v1~1auth~1controls~1reset-token-once~1check/post"
                ],
                "request_schema": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "additionalProperties": True}
                        }
                    },
                },
            },
        ],
        "source_inventory": [
            {"source_id": "src_wms", "original_name": "wms_inventory_service.json"},
            {"source_id": "src_auth", "original_name": "auth_service.json"},
        ],
    }
    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="test",
        available_surfaces={"http_response": True},
    )
    # Compile WITHOUT a target service (adaptive-expansion path): the generator
    # itself must still scope probes to the service the run is pinned to when
    # planning passes it; when NO target is given it must NOT emit foreign
    # probes for a run that can only reach its own service. The generator is
    # service-agnostic at compile time; the planning guard scopes execution.
    # Here we assert the guard path: with target=wms_inventory only wms probes.
    pack = compile_obligations_from_behavior_ir(
        ir,
        root=".",
        project="test",
        target_service_name="wms_inventory",
    )
    probes = [
        o
        for o in (pack.get("obligations") or [])
        if (o.get("property") or {}).get("template") == "policy_endpoint_probe"
    ]
    assert len(probes) == 1
    spec = probes[0].get("property") or {}
    assert "wms" in str(spec.get("operation_path_prefix") or "")


def test_probe_write_not_counted_as_accepted_business_write_in_operational() -> None:
    """A probe-reissued write is a read-only observation in the operational receipt."""
    from ai_test_asset_center.operational_receipts import (
        build_execution_operational_receipt,
    )

    probe_steps = [
        {
            "phase": "treatment",
            "method": "POST",
            "path": "/api/v1/wms/inventory/lot-trace-order/check",
            "status_code": 200,
            "governance_receipt": {
                "accepted": True,
                "http_attempt_count": 2,
                "production_http_requests": 2,
                "write_request_attempt_count": 1,
                "_undocumented_field_probe": {"field": "candidates"},
            },
        }
    ]
    rec = build_execution_operational_receipt(
        receipt_id="operational_probe",
        execution_status="EXECUTED",
        steps=probe_steps,
        cleanup_failures=0,
    )
    assert rec["accepted_non_cleanup_write_count"] == 0
    assert rec["accepted_write_count"] == 0
    assert rec["cleanup_outcome"]["status"] == "NOT_REQUIRED"

    # A genuine entity write still counts as accepted.
    normal_steps = [
        {
            "phase": "treatment",
            "method": "POST",
            "path": "/api/v1/scm/purchase-orders",
            "status_code": 200,
            "governance_receipt": {
                "accepted": True,
                "http_attempt_count": 1,
                "production_http_requests": 1,
                "write_request_attempt_count": 1,
            },
        }
    ]
    rec2 = build_execution_operational_receipt(
        receipt_id="operational_normal",
        execution_status="EXECUTED",
        steps=normal_steps,
        cleanup_failures=0,
    )
    assert rec2["accepted_non_cleanup_write_count"] == 1

    # The delivery gate's cleanup adjudication accepts the probe outcome.
    from ai_test_asset_center._customer_delivery_gate_v2_mechanics import (
        _cleanup_gate_decision,
    )

    status, reasons, adj = _cleanup_gate_decision(
        execution={"operational_receipt": rec},
        contracts=[],
    )
    assert status == "DELIVERABLE"
    assert adj == "NOT_REQUIRED"
