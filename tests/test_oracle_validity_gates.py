from __future__ import annotations

from ai_test_asset_center.effect_observation_graph import (
    SCHEMA_VERSION as EOG_SCHEMA,
    build_effect_observation_graph,
    requires_independent_readback,
)
from ai_test_asset_center.oracle_validity_gates import (
    SCHEMA_VERSION as OVG_SCHEMA,
    enforce_oracle_validity_gates,
)


def _base_result(*, status: str = "VIOLATION") -> dict:
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "campaign_id": "camp_1",
        "execution_id": "exec_1",
        "status": "EXECUTED",
        "oracle_verdict": {
            "status": status,
            "verdict": "customer_deliverable_defect_candidate",
            "customer_deliverable_candidate": True,
            "receipt_id": "oracle_abc",
        },
        "finding": {"risk_family": "authorization", "title": "candidate"},
        "observer_receipts": [],
        "contract_evidence_receipts": [],
    }


def test_observed_body_fingerprint_contrast_keeps_validation_violation() -> None:
    """H27c: plan without body fingerprints must not false-VACUOUS when
    executed control/treatment contract evidence seals distinct request bodies.
    """
    experiment = {
        "experiment_id": "exp_val",
        "obligation_id": "obl_val",
        "risk_family": "validation",
        "control_plan": [
            {
                "actor_ref": "buyer",
                "method": "POST",
                "path": "/api/cart/items",
                "operation_ref": "op_cart",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "buyer",
                "method": "POST",
                "path": "/api/cart/items",
                "operation_ref": "op_cart",
            }
        ],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
        ],
        "property": {
            "control_actor_ref": "buyer",
            "treatment_actor_ref": "buyer",
            "assertion_kind": "validation_rejection",
        },
    }
    result = _base_result(status="VIOLATION")
    result["finding"] = {"risk_family": "validation", "title": "candidate"}
    result["observer_receipts"] = [
        {
            "observer_id": "http_response",
            "status": "OBSERVED",
            "evidence": {"phase": "control", "status_code": 201},
        },
        {
            "observer_id": "http_response",
            "status": "OBSERVED",
            "evidence": {"phase": "treatment", "status_code": 500},
        },
        {
            "observer_id": "business_effect",
            "status": "OBSERVED",
            "evidence": {
                "business_effect_observed": True,
                "before_fingerprint": "before",
                "after_fingerprint": "after",
            },
        },
    ]
    result["contract_evidence_receipts"] = [
        {
            "kind": "control",
            "status": "OBSERVED",
            "evidence": {
                "method": "POST",
                "path": "/api/cart/items",
                "operation_ref": "op_cart",
                "request_body_fingerprint": "a" * 64,
            },
        },
        {
            "kind": "treatment",
            "status": "OBSERVED",
            "evidence": {
                "method": "POST",
                "path": "/api/cart/items",
                "operation_ref": "op_cart",
                "request_body_fingerprint": "b" * 64,
            },
        },
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["oracle_verdict"]["status"] == "VIOLATION"
    assert "VACUOUS_CONTRAST" not in (
        out.get("oracle_validity_receipt") or {}
    ).get("reason_codes", [])
    assert (out.get("oracle_validity_receipt") or {}).get("status") == "PASSED"


def test_identical_observed_bodies_still_vacuous() -> None:
    experiment = {
        "experiment_id": "exp_val",
        "risk_family": "validation",
        "control_plan": [
            {
                "actor_ref": "buyer",
                "method": "GET",
                "path": "/api/orders/1",
                "operation_ref": "op_get",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "buyer",
                "method": "GET",
                "path": "/api/orders/1",
                "operation_ref": "op_get",
            }
        ],
        "observers": [{"observer_id": "http_response"}],
    }
    result = _base_result(status="VIOLATION")
    result["finding"] = {"risk_family": "validation", "title": "candidate"}
    same_fp = "c" * 64
    result["contract_evidence_receipts"] = [
        {
            "kind": "control",
            "status": "OBSERVED",
            "evidence": {
                "method": "GET",
                "path": "/api/orders/1",
                "request_body_fingerprint": same_fp,
            },
        },
        {
            "kind": "treatment",
            "status": "OBSERVED",
            "evidence": {
                "method": "GET",
                "path": "/api/orders/1",
                "request_body_fingerprint": same_fp,
            },
        },
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["oracle_verdict"]["status"] == "INDETERMINATE"
    assert "VACUOUS_CONTRAST" in out["oracle_validity_receipt"]["reason_codes"]


def test_vacuous_contrast_demotes_violation() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "risk_family": "authorization",
        "control_plan": [
            {
                "actor_ref": "actor_a",
                "method": "GET",
                "path": "/orders/1",
                "operation_ref": "op_get",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "actor_a",
                "method": "GET",
                "path": "/orders/1",
                "operation_ref": "op_get",
            }
        ],
        "observers": [{"observer_id": "authorization_comparison"}],
        "property": {
            "control_actor_ref": "actor_a",
            "treatment_actor_ref": "actor_a",
        },
    }
    result = _base_result()
    result["observer_receipts"] = [
        {
            "observer_id": "authorization_comparison",
            "status": "OBSERVED",
            "evidence": {"same_resource_proven": True},
        }
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["oracle_verdict"]["status"] == "INDETERMINATE"
    assert "VACUOUS_CONTRAST" in out["oracle_validity_receipt"]["reason_codes"]
    assert out["finding"] is None
    assert out["oracle_validity_receipt"]["schema_version"] == OVG_SCHEMA


def test_same_credential_demotes_authorization_candidate() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "authorization",
        "control_plan": [
            {
                "actor_ref": "owner",
                "method": "GET",
                "path": "/orders/1",
                "credential_fingerprint": "cred_same",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "other",
                "method": "GET",
                "path": "/orders/1",
                "credential_fingerprint": "cred_same",
            }
        ],
        "observers": [{"observer_id": "authorization_comparison"}],
        "property": {
            "control_actor_ref": "owner",
            "treatment_actor_ref": "other",
        },
    }
    result = _base_result()
    result["observer_receipts"] = [
        {
            "observer_id": "authorization_comparison",
            "status": "OBSERVED",
            "evidence": {"same_resource_proven": True},
        }
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["oracle_verdict"]["status"] == "INDETERMINATE"
    assert "SAME_CREDENTIAL_NO_CONTRAST" in out["oracle_validity_receipt"]["reason_codes"]


def test_write_response_only_demotes_state_violation() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "state",
        "control_plan": [],
        "treatment_plan": [
            {
                "actor_ref": "actor_a",
                "method": "POST",
                "path": "/orders",
                "operation_ref": "op_create",
            }
        ],
        "observers": [{"observer_id": "http_response"}],
        "property": {"assertion_kind": "state_transition"},
    }
    assert requires_independent_readback(experiment) is True
    result = _base_result()
    result["finding"]["risk_family"] = "state"
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["effect_observation_graph"]["schema_version"] == EOG_SCHEMA
    assert out["effect_observation_graph"]["status"] == "WRITE_RESPONSE_ONLY"
    assert out["oracle_verdict"]["status"] == "INDETERMINATE"
    codes = out["oracle_validity_receipt"]["reason_codes"]
    assert "WRITE_RESPONSE_ONLY_EVIDENCE" in codes
    assert "MISSING_BEFORE_STATE" in codes


def test_missing_before_state_for_state_transition() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "state",
        "treatment_plan": [
            {
                "actor_ref": "actor_a",
                "method": "PATCH",
                "path": "/orders/1",
                "operation_ref": "op_patch",
                "observation_path": "/orders/1",
            }
        ],
        "observers": [
            {"observer_id": "before_state"},
            {"observer_id": "after_state"},
        ],
        "property": {"assertion_kind": "state_transition"},
    }
    result = _base_result()
    result["finding"]["risk_family"] = "state"
    result["observer_receipts"] = [
        {
            "observer_id": "after_state",
            "status": "OBSERVED",
            "evidence": {
                "observation_path": "/orders/1",
                "after_fingerprint": "aft",
            },
        }
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert "MISSING_BEFORE_STATE" in out["oracle_validity_receipt"]["reason_codes"]
    assert out["oracle_verdict"]["status"] == "INDETERMINATE"


def test_valid_authorization_contrast_passes() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "authorization",
        "control_plan": [
            {
                "actor_ref": "owner",
                "method": "GET",
                "path": "/orders/1",
                "credential_fingerprint": "cred_owner",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "other",
                "method": "GET",
                "path": "/orders/1",
                "credential_fingerprint": "cred_other",
            }
        ],
        "observers": [{"observer_id": "authorization_comparison"}],
        "property": {
            "control_actor_ref": "owner",
            "treatment_actor_ref": "other",
        },
    }
    result = _base_result()
    result["observer_receipts"] = [
        {
            "observer_id": "authorization_comparison",
            "status": "OBSERVED",
            "evidence": {"same_resource_proven": True},
        }
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["oracle_validity_receipt"]["status"] == "PASSED"
    assert out["oracle_verdict"]["status"] == "VIOLATION"
    assert out["oracle_verdict"]["oracle_validity_gate"] == "PASSED"
    assert out["finding"] is not None


def test_effect_graph_prefers_independent_readback() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "state",
        "treatment_plan": [
            {
                "method": "POST",
                "path": "/orders",
                "operation_ref": "op_create",
                "observation_path": "/orders/1",
            }
        ],
    }
    result = {
        "observer_receipts": [
            {
                "observer_id": "business_effect",
                "status": "OBSERVED",
                "evidence": {
                    "observation_path": "/orders/1",
                    "business_effect_observed": True,
                    "before_fingerprint": "b",
                    "after_fingerprint": "a",
                },
            }
        ]
    }
    graph = build_effect_observation_graph(experiment=experiment, result=result)
    assert graph["status"] == "COMPLETE"
    assert graph["independent_observed_count"] >= 1
    assert "WRITE_RESPONSE_ONLY_EVIDENCE" not in graph["reason_codes"]


def test_demoted_oracle_receipt_validates_with_validity_provenance() -> None:
    from ai_test_asset_center.contract_oracles import validate_contract_oracle_receipt

    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "authorization",
        "control_plan": [
            {
                "actor_ref": "actor_a",
                "method": "GET",
                "path": "/orders/1",
                "credential_fingerprint": "c1",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "actor_a",
                "method": "GET",
                "path": "/orders/1",
                "credential_fingerprint": "c1",
            }
        ],
        "observers": [{"observer_id": "authorization_comparison"}],
        "property": {
            "control_actor_ref": "actor_a",
            "treatment_actor_ref": "actor_a",
        },
    }
    result = _base_result()
    result["observer_receipts"] = [
        {
            "observer_id": "authorization_comparison",
            "status": "OBSERVED",
            "evidence": {"same_resource_proven": True},
        }
    ]
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    validated = validate_contract_oracle_receipt(out["oracle_verdict"])
    assert validated["status"] == "INDETERMINATE"
    assert validated["oracle_validity_gate"] == "INDETERMINATE"
    assert "VACUOUS_CONTRAST" in validated["oracle_validity_reason_codes"]


def test_indeterminate_oracle_is_not_applicable() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "risk_family": "validation",
        "treatment_plan": [{"method": "GET", "path": "/health"}],
    }
    result = _base_result(status="INDETERMINATE")
    result["finding"] = None
    out = enforce_oracle_validity_gates(result=result, experiment=experiment)
    assert out["oracle_validity_receipt"]["status"] == "NOT_APPLICABLE"
    assert out["oracle_verdict"]["status"] == "INDETERMINATE"
