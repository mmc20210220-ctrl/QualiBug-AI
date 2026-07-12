from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import inspect

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    MainlineContractError,
    build_mainline_run_contract,
)


API_SPEC = """{
  "openapi": "3.0.0",
  "paths": {
    "/resources": {
      "get": {"operationId": "listResources"}
    }
  }
}"""


def _inputs(authority: str):
    from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs

    return DiscoveryMainlineInputs(
        project="PROJECT-1",
        root=Path("."),
        prd_text="requirement",
        api_spec_text="GET /resources",
        db_schema_text="",
        approved_base_url="http://127.0.0.1:8080",
        campaign_context={"mainline_authority": authority},
    )


def _contract(authority: str, *, campaign_id: str = "CMP-1") -> dict:
    return build_mainline_run_contract(
        mainline_authority=authority,
        run_id="RUN-1",
        campaign_id=campaign_id,
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v1" if authority == "legacy_champion" else "v2",
        evaluation_mode="replay",
    )


def test_v12_wrapper_delegates_once_and_has_no_runtime_fallback() -> None:
    source = Path("ai_test_asset_center/v12_pipeline.py").read_text(encoding="utf-8")

    assert "effective_execution_status" not in source
    assert "fallback_to_legacy" not in source
    assert source.count("run_discovery_mainline(") == 1


def test_v12_rejects_missing_immutable_run_identity(tmp_path: Path) -> None:
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    with pytest.raises(MainlineContractError, match="mainline_authority_missing"):
        run_v12_pipeline(
            "project-missing-identity",
            tmp_path,
            api_spec_text=API_SPEC,
            campaign_context={},
        )


def test_experiment_candidate_returns_attempt_authoritative_result(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-candidate",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    result = run_v12_pipeline(
        "project-candidate",
        tmp_path,
        api_spec_text=API_SPEC,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-CANDIDATE",
            "target_id": "TARGET-CANDIDATE",
            "environment_id": "ENV-CANDIDATE",
            "environment_ref": "ENV-CANDIDATE",
            "environment_type": "test",
            "scope_id": "scope-candidate",
            "policy_version": "policy-candidate",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
        },
    )

    assert result["mainline_run"]["mainline_authority"] == "experiment_candidate"
    assert result["obligation_attempt_ledger"]["complete"] is True
    assert result["discovery_funnel"]["receipt_authority"] == "obligation_attempt_ledger"
    assert result["formal_count_projection"]["formal_finding_ids"] == []


def test_candidate_accounts_for_compiled_obligation_when_runtime_is_plan_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-plan-only",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.build_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: {
            "source_inventory": [
                {
                    "source_id": "api-contract",
                    "source_type": "openapi",
                    "original_name": "api.json",
                    "content_hash": manifest["source_hash"],
                }
            ],
            "permission_matrix": [
                {
                    "role": "reader",
                    "resource": "/resources",
                    "actions": ["read"],
                    "scope": "own",
                    "source_id": "permission-source",
                },
                {
                    "role": "restricted",
                    "resource": "/resources",
                    "actions": [],
                    "scope": "own",
                    "source_id": "permission-source",
                },
            ],
        },
    )
    result = run_v12_pipeline(
        "project-plan-only",
        tmp_path,
        api_spec_text=API_SPEC,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-PLAN-ONLY",
            "target_id": "TARGET-PLAN-ONLY",
            "environment_id": "ENV-PLAN-ONLY",
            "environment_ref": "ENV-PLAN-ONLY",
            "environment_type": "test",
            "scope_id": "scope-plan-only",
            "policy_version": "policy-plan-only",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
        },
    )

    attempts = result["obligation_attempt_ledger"]["attempts"]
    assert attempts
    assert all(row["terminal_status"] == "BLOCKED" for row in attempts)
    assert {row["reason_code"] for row in attempts} == {"BLOCKED_RUNTIME_TARGET"}
    assert result["discovery_funnel"]["pipeline_health"]["status"] == "BLOCKED"


def test_candidate_invokes_only_experiment_executor_for_approved_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    manifest = register_source_asset(
        "project-execution",
        "api-contract",
        API_SPEC,
        source_type="openapi",
        root=tmp_path,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.build_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: {
            "source_inventory": [
                {
                    "source_id": "api-contract",
                    "source_type": "openapi",
                    "original_name": "api.json",
                    "content_hash": manifest["source_hash"],
                }
            ],
            "permission_matrix": [
                {
                    "role": "reader",
                    "resource": "/resources",
                    "actions": ["read"],
                    "scope": "own",
                    "source_id": "permission-source",
                },
                {
                    "role": "restricted",
                    "resource": "/resources",
                    "actions": [],
                    "scope": "own",
                    "source_id": "permission-source",
                },
            ],
        },
    )
    calls: list[str] = []

    def fake_execute(selected, **_kwargs):
        calls.append("experiment")
        obligation_id = selected[0]["obligation_id"]
        return {
            "selected_count": 1,
            "executed_count": 1,
            "blocked_count": 0,
            "harness_failure_count": 0,
            "cleanup_failures": 0,
            "findings": [],
            "results": [],
            "compile_results": {
                obligation_id: {
                    "status": "COMPILED",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "execution_results": {
                obligation_id: {
                    "status": "EXECUTED",
                    "observation_receipt_ids": ["obs-approved"],
                    "oracle_receipt_id": "oracle-approved",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "gate_results": {
                obligation_id: {
                    "status": "REJECTED",
                    "reason_code": "ORACLE_NOT_VIOLATED",
                    "gate_receipt_id": "gate-approved",
                    "cost_coverage_status": "MEASURED",
                }
            },
            "every_experiment_has_receipt": True,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.discovery_runtime.execute_selected_experiments",
        fake_execute,
    )
    result = run_v12_pipeline(
        "project-execution",
        tmp_path,
        api_spec_text=API_SPEC,
        base_url="http://127.0.0.1:8080",
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-EXECUTION",
            "target_id": "TARGET-EXECUTION",
            "environment_id": "ENV-EXECUTION",
            "environment_ref": "ENV-EXECUTION",
            "environment_type": "test",
            "scope_id": "scope-execution",
            "execution_mode": "safe_read_only",
            "policy_version": "policy-execution",
            "evaluation_mode": "operational",
            "source_manifest": manifest,
        },
    )

    assert calls == ["experiment"]
    assert result["obligation_attempt_ledger"]["attempts"][0]["terminal_status"] == "REJECTED"
    assert result["discovery_funnel"]["pipeline_health"]["status"] == "OK"


def test_campaign_identity_exists_before_planning_and_execution() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    events: list[str] = []
    contract = _contract("legacy_champion")

    result = run_discovery_mainline(
        _inputs("legacy_champion"),
        build_campaign=lambda _: events.append("campaign") or SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: events.append("plan") or SimpleNamespace(mainline_run=contract),
        legacy_runner=lambda *_: events.append("legacy") or {"mainline_run": contract},
        experiment_runner=lambda *_: events.append("experiment") or {"mainline_run": contract},
    )

    assert events == ["campaign", "plan", "legacy"]
    assert result["mainline_run"]["campaign_id"] == "CMP-1"


def test_one_run_never_invokes_both_runners() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    calls = {"legacy": 0, "experiment": 0}
    contract = _contract("experiment_candidate")

    run_discovery_mainline(
        _inputs("experiment_candidate"),
        build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
        legacy_runner=lambda *_: calls.__setitem__("legacy", calls["legacy"] + 1) or {"mainline_run": contract},
        experiment_runner=lambda *_: calls.__setitem__("experiment", calls["experiment"] + 1) or {"mainline_run": contract},
    )

    assert calls == {"legacy": 0, "experiment": 1}


def test_runner_failure_never_falls_back_to_other_authority() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    calls = {"legacy": 0, "experiment": 0}
    contract = _contract("experiment_candidate")

    def fail_experiment(*_):
        calls["experiment"] += 1
        raise RuntimeError("candidate failed")

    with pytest.raises(RuntimeError, match="candidate failed"):
        run_discovery_mainline(
            _inputs("experiment_candidate"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            legacy_runner=lambda *_: calls.__setitem__("legacy", calls["legacy"] + 1) or {"mainline_run": contract},
            experiment_runner=fail_experiment,
        )

    assert calls == {"legacy": 0, "experiment": 1}


def test_coordinator_rejects_campaign_or_result_identity_mismatch() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    contract = _contract("legacy_champion")
    with pytest.raises(MainlineContractError, match="mainline_campaign_identity_mismatch"):
        run_discovery_mainline(
            _inputs("legacy_champion"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-OTHER"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            legacy_runner=lambda *_: {"mainline_run": contract},
            experiment_runner=lambda *_: {"mainline_run": contract},
        )

    wrong_result = _contract("legacy_champion", campaign_id="CMP-OTHER")
    with pytest.raises(MainlineContractError, match="mainline_result_authority_mismatch"):
        run_discovery_mainline(
            _inputs("legacy_champion"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            legacy_runner=lambda *_: {"mainline_run": wrong_result},
            experiment_runner=lambda *_: {"mainline_run": contract},
        )


def test_v12_establishes_identity_and_runtime_contract_before_coordinator() -> None:
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    source = inspect.getsource(run_v12_pipeline)
    identity_index = source.index("_require_mainline_identity(")
    normalization_index = source.index("_normalize_executable_api_document(")
    runtime_index = source.index("_runtime_contract(")
    coordinator_index = source.index("run_discovery_mainline(")

    assert identity_index < normalization_index < runtime_index < coordinator_index
    assert "_run_legacy_champion_domain(" not in source
