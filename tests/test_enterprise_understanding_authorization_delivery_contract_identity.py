"""Authorization delivery packaging must use the exact compiled comparison contract."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from ai_test_asset_center import experiment_executor
from ai_test_asset_center.authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _contract() -> dict:
    return {
        "schema_version": "qualibug.authorization-comparison-contract.v1",
        "status": "COMPILED_RUNTIME_VERIFICATION_REQUIRED",
        "comparison_dimension": "ROLE_PERMISSION",
        "control_actor_ref": "actor:control",
        "treatment_actor_ref": "actor:treatment",
        "resource_identity_binding_targets": ["order_id"],
        "shared_binding_graph_fingerprint": _sha({"target": "order_id"}),
    }


def _result(contract: dict) -> dict:
    return {
        "status": "EXECUTED",
        "finding": {"title": "authorization candidate"},
        "authorization_causality_receipt": {
            "status": "PASSED",
            "comparison_contract_fingerprint": _sha(contract),
            "compile_binding_graph_fingerprint": contract[
                "shared_binding_graph_fingerprint"
            ],
        },
    }


def _lineage_result(*, finding_execution_id: str = "") -> dict:
    finding = {
        "campaign_id": "campaign:1",
        "obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
    }
    if finding_execution_id:
        finding["execution_id"] = finding_execution_id
    return {
        "finding": finding,
        "authorization_causality_receipt": {
            "status": "PASSED",
            "campaign_id": "campaign:1",
            "obligation_id": "obl:auth",
            "experiment_id": "exp:auth",
            "execution_id": "execution:1",
        },
    }


def test_exact_compiled_contract_identity_is_accepted() -> None:
    contract = _contract()

    experiment_executor._verify_authorization_compile_identity(
        _result(contract),
        {"authorization_comparison_contract": contract},
    )


def test_compiled_contract_drift_is_rejected() -> None:
    contract = _contract()
    result = _result(contract)
    changed = deepcopy(contract)
    changed["comparison_dimension"] = "TENANT_SCOPE"

    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_delivery_comparison_contract_fingerprint_mismatch",
    ):
        experiment_executor._verify_authorization_compile_identity(
            result,
            {"authorization_comparison_contract": changed},
        )


def test_missing_finding_execution_lineage_is_sealed_from_causal_receipt() -> None:
    original = _lineage_result()
    snapshot = deepcopy(original)

    output = experiment_executor._seal_authorization_finding_lineage(original)

    assert original == snapshot
    assert output["finding"]["execution_id"] == "execution:1"


def test_foreign_finding_execution_lineage_is_rejected() -> None:
    with pytest.raises(
        AuthorizationDeliveryGateError,
        match="authorization_delivery_finding_lineage_mismatch:execution_id",
    ):
        experiment_executor._seal_authorization_finding_lineage(
            _lineage_result(finding_execution_id="execution:other")
        )


def test_public_executor_removes_finding_when_contract_identity_drifts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center import _experiment_executor_mainline_mechanics

    contract = _contract()
    changed = deepcopy(contract)
    changed["comparison_dimension"] = "TENANT_SCOPE"
    governed = _result(contract)
    governed["oracle_verdict"] = {
        "status": "VIOLATION",
        "verdict": "customer_deliverable_defect_candidate",
        "customer_deliverable_candidate": True,
    }

    monkeypatch.setattr(
        _experiment_executor_mainline_mechanics,
        "_execute_one_governed",
        lambda *args, **kwargs: {"status": "EXECUTED"},
    )
    monkeypatch.setattr(
        _experiment_executor_mainline_mechanics,
        "enforce_authorization_oracle_causality",
        lambda **kwargs: deepcopy(governed),
    )
    monkeypatch.setattr(
        experiment_executor._governance,
        "_test_account_rows",
        lambda root, project: [],
    )

    output = experiment_executor.execute_one_experiment(
        {"authorization_comparison_contract": changed},
        behavior_ir={},
        root=tmp_path,
        project="demo",
        base_url="https://test.invalid",
        runtime_contract={},
        campaign_id="campaign:1",
        execution_id="execution:1",
        actor_tokens={},
    )

    assert output["finding"] is None
    assert output["reason_code"] == "AUTHORIZATION_DELIVERY_EVIDENCE_INVALID"
    assert output["oracle_verdict"]["status"] == "INDETERMINATE"
    assert output["oracle_verdict"]["customer_deliverable_candidate"] is False
