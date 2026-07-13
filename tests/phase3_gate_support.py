"""Test-only builders for a complete, industry-neutral Delivery Gate v2 chain."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.canonical_defect_registry import (
    build_canonical_defect_registry,
    build_defect_identity_consistency,
)
from ai_test_asset_center.customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.discovery_quality_projection import (
    build_formal_count_projection,
)
from ai_test_asset_center.formal_delivery_authority import (
    build_formal_delivery_authority_receipt,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    build_obligation_attempt_ledger,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)
from ai_test_asset_center.evaluator_execution_attestation import (
    PROCESS_BOUNDARY_SCHEMA,
    build_execution_attestation,
)


def _request_semantics_fingerprint(*, phase: str) -> str:
    payload = {
        "operation_ref": "read-resource",
        "method": "GET",
        "path_template": "/resources/{resourceId}",
        "mutation_class": (
            "positive_control"
            if phase == "control"
            else "actor_relation_treatment"
        ),
        "mutation_selector": "",
        "mutation_operator": "",
        "request_body_fingerprint": "c" * 64,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_formal_evaluation_scope(
    findings: list[dict[str, Any]],
    *,
    run_id: str,
    campaign_id: str,
    target_id: str,
    environment_id: str,
    policy_version: str,
    evaluation_mode: str,
    mainline_authority: str = "experiment_candidate",
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Attach real Gate-v2 receipts and one immutable attempt ledger."""

    mainline = build_mainline_run_contract(
        mainline_authority=mainline_authority,
        run_id=run_id,
        campaign_id=campaign_id,
        target_id=target_id,
        environment_id=environment_id,
        policy_version=policy_version,
        evaluation_mode=evaluation_mode,
    )
    if not findings:
        suffix = f"{target_id}-negative-control"
        obligation_id = f"obligation-{suffix}"
        experiment_id = f"experiment-{suffix}"
        execution_id = f"execution-{suffix}"
        operational = build_execution_operational_receipt(
            receipt_id=f"operational-{suffix}",
            execution_status="EXECUTED",
            steps=[{
                "phase": "treatment",
                "method": "GET",
                "path": "/resources/resource-1",
                "status_code": 403,
            }],
            cleanup_failures=0,
        )
        return [], build_obligation_attempt_ledger(
            mainline_run=mainline,
            selected=[{
                "obligation_id": obligation_id,
                "experiment_id": experiment_id,
                "candidate_id": f"candidate-{suffix}",
                "source_refs": [{
                    "kind": "api_contract",
                    "source_id": f"source-{suffix}",
                    "locator": "GET /resources/{resourceId}",
                }],
            }],
            compile_results={obligation_id: {
                "status": "COMPILED",
                "experiment_id": experiment_id,
                "receipt_id": f"compile-{suffix}",
            }},
            execution_results={obligation_id: {
                "status": "EXECUTED",
                "experiment_id": experiment_id,
                "execution_id": execution_id,
                "receipt_id": f"execution-{suffix}",
                "observation_receipt_ids": [f"observation-{suffix}"],
                "oracle_receipt_id": f"oracle-{suffix}",
                "operational_receipt": operational,
            }},
            gate_results={obligation_id: {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
                "receipt_id": f"gate-{suffix}",
            }},
        )
    selected: list[dict[str, Any]] = []
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    formal_findings: list[dict[str, Any]] = []

    for index, raw in enumerate(findings, start=1):
        finding = dict(raw)
        suffix = f"{target_id}-{index}"
        finding_id = str(
            finding.get("finding_id") or finding.get("id") or f"finding-{suffix}"
        )
        candidate_id = f"candidate-{suffix}"
        slice_id = f"slice-{suffix}"
        obligation_id = f"obligation-{suffix}"
        experiment_id = f"experiment-{suffix}"
        execution_id = f"execution-{suffix}"
        evidence_id = f"evidence-{suffix}"
        source_refs = [{
            "kind": "api_contract",
            "source_id": f"source-{suffix}",
            "locator": "GET /resources/{resourceId}",
        }]
        experiment = {
            "experiment_id": experiment_id,
            "obligation_id": obligation_id,
            "campaign_id": campaign_id,
            "execution_id": execution_id,
            "source_refs": source_refs,
            "control_plan": [{
                "step_id": f"control-{suffix}",
                "actor_ref": "owner",
                "operation_ref": "read-resource",
            }],
            "treatment_plan": [{
                "step_id": f"treatment-{suffix}",
                "actor_ref": "viewer",
                "operation_ref": "read-resource",
            }],
            "fixture_dag": {"nodes": [], "setup_order": []},
            "observers": [{"observer_id": "http_response"}],
            "cleanup_plan": [],
            "assertions": [{
                "assertion_id": f"assert-status-{suffix}",
                "kind": "http_status",
                "expected": 403,
            }],
        }
        contract_receipts = [
            build_contract_evidence_receipt(
                kind=kind,
                experiment_id=experiment_id,
                obligation_id=obligation_id,
                campaign_id=campaign_id,
                execution_id=execution_id,
                subject_id=subject_id,
                status="OBSERVED",
                evidence={
                    **evidence,
                    **(
                        {
                            "path_template": "/resources/{resourceId}",
                            "request_body_fingerprint": "c" * 64,
                            "request_semantics_fingerprint": (
                                _request_semantics_fingerprint(phase=kind)
                            ),
                            "mutation_class": (
                                "positive_control"
                                if kind == "control"
                                else "actor_relation_treatment"
                            ),
                            "mutation_selector": "",
                            "mutation_operator": "",
                        }
                        if kind in {"control", "treatment"}
                        else {}
                    ),
                },
            )
            for kind, subject_id, evidence in (
                (
                    "control",
                    f"control-{suffix}",
                    {
                        "response_observed": True,
                        "status_code": 200,
                        "control_succeeded": True,
                    },
                ),
                (
                    "treatment",
                    f"treatment-{suffix}",
                    {"response_observed": True, "status_code": 200},
                ),
                ("actor", "owner", {"role": "public"}),
                ("actor", "viewer", {"role": "public"}),
            )
        ]
        observer = build_observer_receipt(
            observer_id="http_response",
            status="OBSERVED",
            campaign_id=campaign_id,
            execution_id=execution_id,
            evidence={"statuses": [200, 200]},
        )
        oracle = evaluate_contract_oracle(
            experiment=experiment,
            evidence={
                "campaign_id": campaign_id,
                "execution_id": execution_id,
                "status_code": 200,
                "contract_evidence_receipts": contract_receipts,
                "observer_receipts": [observer],
            },
        )
        reproduction = finding.get("reproduction")
        reproduction = reproduction if isinstance(reproduction, dict) else {}
        path = str(reproduction.get("path") or "/resources/resource-1")
        steps = [
            {
                "phase": "control",
                "step_id": f"control-{suffix}",
                "actor_ref": "owner",
                "operation_ref": "read-resource",
                "method": "GET",
                "path": path,
                "status_code": 200,
                "body": {"id": "resource-1"},
                "observation_receipt_id": f"observation-control-{suffix}",
                "path_template": "/resources/{resourceId}",
                "request_body_fingerprint": "c" * 64,
                "request_semantics_fingerprint": (
                    _request_semantics_fingerprint(phase="control")
                ),
                "mutation_class": "positive_control",
                "mutation_selector": "",
                "mutation_operator": "",
            },
            {
                "phase": "treatment",
                "step_id": f"treatment-{suffix}",
                "actor_ref": "viewer",
                "operation_ref": "read-resource",
                "method": "GET",
                "path": path,
                "status_code": 200,
                "body": {"id": "resource-1"},
                "observation_receipt_id": f"observation-treatment-{suffix}",
                "path_template": "/resources/{resourceId}",
                "request_body_fingerprint": "c" * 64,
                "request_semantics_fingerprint": (
                    _request_semantics_fingerprint(phase="treatment")
                ),
                "mutation_class": "actor_relation_treatment",
                "mutation_selector": "",
                "mutation_operator": "",
            },
        ]
        operational = build_execution_operational_receipt(
            receipt_id=f"operational-{suffix}",
            execution_status="EXECUTED",
            steps=steps,
            cleanup_failures=0,
        )
        delivery_execution = build_delivery_execution_receipt(
            mainline_run=mainline,
            candidate_id=candidate_id,
            slice_id=slice_id,
            obligation_id=obligation_id,
            experiment_id=experiment_id,
            execution_id=execution_id,
            evidence_id=evidence_id,
            operational_receipt=operational,
            observation_receipt_ids=[
                f"observation-control-{suffix}",
                f"observation-treatment-{suffix}",
                observer["receipt_id"],
                *[receipt["receipt_id"] for receipt in contract_receipts],
            ],
            oracle_receipt_id=oracle["receipt_id"],
        )
        reproduction_receipt = build_reproduction_receipt(
            execution_receipt=delivery_execution,
            steps=steps,
            oracle_receipt=oracle,
            source_refs=source_refs,
        )
        finding.update({
            "id": finding_id,
            "finding_id": finding_id,
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
            "source_refs": source_refs,
            "mainline_run": {
                "contract_fingerprint": mainline["contract_fingerprint"],
            },
            "failed_assertions": list(oracle.get("assertions") or []),
            "canonical_identity_evidence": {
                "schema_version": "qualibug.canonical-identity-evidence.v1",
                "operation": {
                    "operation_ref": "read-resource",
                    "method": "GET",
                    "path_template": "/resources/{resourceId}",
                },
                "property": {
                    "assertion_id": f"assert-status-{suffix}",
                    "kind": "http_status",
                    "template": "non_owner_access_must_be_denied",
                    "invariant_ref": "",
                    "entity_ref": "resource",
                },
                "actor_relation": {
                    "control_role": "owner",
                    "treatment_role": "viewer",
                    "relation": "owner_to_viewer",
                },
                "resource_identity_class": {
                    "entity_refs": ["resource"],
                    "path_template": "/resources/{resourceId}",
                },
                "mutation": {
                    "class": "actor_relation_treatment",
                    "selector": "",
                    "operator": "",
                    "request_body_fingerprint": "c" * 64,
                    "request_semantics_fingerprint": (
                        _request_semantics_fingerprint(phase="treatment")
                    ),
                },
                "outcome": {
                    "assertion_status": "VIOLATION",
                    "assertion_kind": "http_status",
                    "control_http_status_class": 2,
                    "treatment_http_status_class": 2,
                    "viewer_can_access": True,
                    "leak_detected": None,
                    "invariant_held": None,
                },
            },
        })
        gate = build_customer_delivery_gate_receipt_v2(
            finding=finding,
            execution_receipt=delivery_execution,
            contract_evidence_receipts=contract_receipts,
            observer_receipts=[observer],
            oracle_receipt=oracle,
            reproduction_receipt=reproduction_receipt,
        )
        finding.update({
            "delivery_gate_receipt": gate,
            "delivery_gate_receipt_id": gate["gate_receipt_id"],
            "gate_passed": True,
            "customer_delivery_status": "defect",
        })
        selected.append({
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "source_refs": source_refs,
        })
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "experiment_id": experiment_id,
            "receipt_id": f"compile-{suffix}",
        }
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "receipt_id": delivery_execution["receipt_id"],
            "output_fingerprint": delivery_execution["receipt_fingerprint"],
            "observation_receipt_ids": delivery_execution[
                "observation_receipt_ids"
            ],
            "oracle_receipt_id": oracle["receipt_id"],
            "operational_receipt": operational,
            "delivery_execution_receipt": delivery_execution,
            "contract_evidence_receipts": contract_receipts,
            "observer_receipts": [observer],
            "oracle_receipt": oracle,
            "reproduction_receipt": reproduction_receipt,
            "finding": finding,
        }
        gate_results[obligation_id] = gate
        formal_findings.append(finding)

    ledger = build_obligation_attempt_ledger(
        mainline_run=mainline,
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    return formal_findings, ledger


def build_formal_scope_contract(
    *,
    mainline_run: dict[str, Any],
    findings: list[dict[str, Any]],
    obligation_attempt_ledger: dict[str, Any],
) -> dict[str, Any]:
    registry = build_canonical_defect_registry(
        mainline_run=mainline_run,
        deliverable_occurrences=findings,
        obligation_attempt_ledger=obligation_attempt_ledger,
    )
    formal = build_formal_count_projection(
        findings=findings,
        candidate_findings=[],
        obligation_attempt_ledger=obligation_attempt_ledger,
        mainline_run=mainline_run,
        canonical_defect_registry=registry,
    )
    occurrence_ids = list(formal["delivery_occurrence_finding_ids"])
    canonical_ids = list(formal["canonical_defect_ids"])
    consistency = build_defect_identity_consistency(
        occurrence_scopes={
            "delivery_gate_ids": occurrence_ids,
            "formal_authority_occurrence_ids": occurrence_ids,
            "registry_occurrence_ids": occurrence_ids,
            "formal_projection_occurrence_ids": occurrence_ids,
            "product_projection_occurrence_ids": occurrence_ids,
            "evaluator_submission_occurrence_ids": occurrence_ids,
        },
        canonical_scopes={
            "canonical_registry_ids": canonical_ids,
            "formal_projection_ids": canonical_ids,
            "product_projection_ids": canonical_ids,
            "evaluator_submission_ids": canonical_ids,
        },
    )
    authority = build_formal_delivery_authority_receipt(
        mainline_run=mainline_run,
        findings=findings,
        obligation_attempt_ledger=obligation_attempt_ledger,
    )
    return {
        "formal_count_projection": formal,
        "canonical_defect_registry": registry,
        "defect_identity_consistency": consistency,
        "formal_delivery_authority": authority,
        "delivery_occurrences": findings,
    }


def build_test_execution_authority(
    *,
    mainline_run: dict[str, Any],
    obligation_attempt_ledger: dict[str, Any],
    policy_id: str,
    strategy_fingerprint: str,
    signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Build evaluator-side test observations; never used by product runtime."""

    governance = {
        "cleanup_status": "SUCCEEDED",
        "dirty_environment": False,
        "prepare_receipt_fingerprint": "prepare-test-receipt",
        "cleanup_receipt_fingerprint": "cleanup-test-receipt",
    }
    boundary = {
        "schema_version": PROCESS_BOUNDARY_SCHEMA,
        "isolation": "isolated_subprocess",
        "worker_protocol_schema": (
            "qualibug.observed-product-scan-worker-request.v1"
        ),
        "evaluator_secrets_removed": True,
        "request_fingerprint": hashlib.sha256(
            f"request:{mainline_run['run_id']}".encode("utf-8")
        ).hexdigest(),
        "result_fingerprint": hashlib.sha256(
            f"result:{mainline_run['run_id']}".encode("utf-8")
        ).hexdigest(),
        "exit_code": 0,
    }
    observations: list[dict[str, Any]] = []
    for attempt in obligation_attempt_ledger.get("attempts") or []:
        operational = attempt.get("operational_receipt") or {}
        request_count = int(
            operational.get("http_request_attempt_count") or 0
        )
        if request_count == 0:
            continue
        write_count = int(operational.get("accepted_write_count") or 0)
        source_material = (
            f"{mainline_run['run_id']}:{attempt['obligation_id']}"
        )
        observations.append({
            "obligation_id": attempt["obligation_id"],
            "execution_id": attempt["execution_id"],
            "source_kind": "evaluator_http_proxy",
            "source_receipt_id": f"test-proxy:{attempt['obligation_id']}",
            "source_fingerprint": hashlib.sha256(
                source_material.encode("utf-8")
            ).hexdigest(),
            "target_request_count": request_count,
            "write_count": write_count,
            "production_request_count": int(
                operational.get("production_http_request_count") or 0
            ),
            "audit_receipt_ids": (
                [f"test-audit:{attempt['obligation_id']}"]
                if write_count
                else []
            ),
        })
    attestation = build_execution_attestation(
        mainline_run=mainline_run,
        obligation_attempt_ledger=obligation_attempt_ledger,
        policy_identity={
            "policy_id": policy_id,
            "policy_version": mainline_run["policy_version"],
            "strategy_fingerprint": strategy_fingerprint,
        },
        fixture_governance=governance,
        process_boundary=boundary,
        trusted_observations=observations,
        signing_key=signing_key,
    )
    return {
        "fixture_governance": governance,
        "process_boundary": boundary,
        "execution_attestation": attestation,
    }
