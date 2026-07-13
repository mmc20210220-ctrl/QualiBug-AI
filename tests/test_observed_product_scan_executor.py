from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
    validate_mainline_run_contract,
)
from ai_test_asset_center.observed_product_scan_executor import (
    PRODUCT_SCAN_CONTEXT_SCHEMA,
    PRODUCT_SCAN_INPUT_SCHEMA,
    ObservedProductScanExecutor,
)
from ai_test_asset_center.discovery_policy_evaluation_runner import PolicyEvaluationRunnerError
from ai_test_asset_center.policy_registry import StrategyBundle
from ai_test_asset_center.policy_wiring import policy_strategy_override
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


def _artifacts(
    tmp_path: Path,
    *,
    private_context: bool = False,
    test_accounts: dict | None = None,
) -> dict:
    api = tmp_path / "openapi.json"
    prd = tmp_path / "PRD.md"
    fixture = tmp_path / "fixture.json"
    context = tmp_path / "context.json"
    inputs = tmp_path / "input.json"
    api.write_text(json.dumps({"openapi": "3.0.0", "paths": {}}), encoding="utf-8")
    prd.write_text("observable product behavior", encoding="utf-8")
    fixture.write_text(json.dumps({"fixture": "v1"}), encoding="utf-8")
    context_payload = {
        "schema_version": PRODUCT_SCAN_CONTEXT_SCHEMA,
        "campaign_context": {
            "scope_id": "evaluation-scope",
            "ground_truth_ref" if private_context else "source_id": "forbidden" if private_context else "source-1",
        },
    }
    if test_accounts is not None:
        context_payload["test_accounts"] = test_accounts
    context.write_text(json.dumps(context_payload), encoding="utf-8")
    inputs.write_text(json.dumps({
        "schema_version": PRODUCT_SCAN_INPUT_SCHEMA,
        "project_id": "project-1",
        "base_url": "http://127.0.0.1:8011",
        "api_doc_ref": str(api),
        "prd_ref": str(prd),
        "multi_layer": True,
    }), encoding="utf-8")
    return {
        "runtime_view": {
            "target": {
                "target_id": "target-1",
                "project_id": "project-1",
                "runtime_fingerprint": "runtime-fingerprint",
                "runtime": {
                    "environment_ref": "http://127.0.0.1:8011",
                    "environment_type": "sandbox",
                    "input_bundle_ref": str(inputs),
                    "fixture_snapshot_ref": str(fixture),
                    "context_artifact_ref": str(context),
                },
            },
        },
        "input": inputs,
        "fixture": fixture,
        "context": context,
    }


def _operational_metrics(**kwargs):
    return {
        "wall_clock_seconds": kwargs["wall_clock_seconds"],
        "estimated_cost_usd": 0.5,
        "request_count": 3,
        "production_http_requests": 0,
        "cleanup_failures": 0,
        "safety_incidents": 0,
        "dirty_test_environments": 0,
        "execution_success_rate": 1,
        "engine_success_rate": 1,
        "duplicate_rate": 0,
    }


def test_executor_rejects_in_process_scan_without_explicit_test_flag(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="test-only"):
        ObservedProductScanExecutor(
            workspace_root=tmp_path,
            operational_metrics_collector=_operational_metrics,
            scan_callable=lambda **_: {},
        )


def test_production_scan_runs_in_secret_free_subprocess_with_preallocated_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    observed: dict = {}
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY",
        "must-never-enter-product-runtime-0123456789",
    )
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEYRING",
        "must-also-be-removed",
    )
    monkeypatch.setenv(
        "QUALIBUG_TRUSTED_OBSERVATION_ROOT",
        str(tmp_path / "must-never-enter-product-runtime"),
    )
    monkeypatch.setenv(
        "QUALIBUG_TRUSTED_OBSERVATION_HMAC_KEY",
        "must-never-enter-product-runtime-0123456789",
    )
    monkeypatch.setenv(
        "QUALIBUG_BENCHMARK_GROUND_TRUTH",
        str(tmp_path / "private-ground-truth.json"),
    )

    def run_worker(command, **options):
        request_path = Path(command[-2])
        result_path = Path(command[-1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        context = request["scan_kwargs"]["campaign_context"]
        contract = validate_mainline_run_contract(context["mainline_run"])
        _, attempt_ledger = build_formal_evaluation_scope(
            [],
            run_id=contract["run_id"],
            campaign_id=contract["campaign_id"],
            target_id=contract["target_id"],
            environment_id=contract["environment_id"],
            policy_version=contract["policy_version"],
            evaluation_mode=contract["evaluation_mode"],
        )
        formal_scope = build_formal_scope_contract(
            mainline_run=contract,
            findings=[],
            obligation_attempt_ledger=attempt_ledger,
        )
        result_path.write_text(
            json.dumps(
                {
                    "success": True,
                    "scan_id": contract["run_id"],
                    "execution_status": "completed",
                    "mainline_run": contract,
                    "findings": [],
                    "candidate_findings": [],
                    "shadow_findings": [],
                    "obligation_attempt_ledger": attempt_ledger,
                    "evaluator_canonical_findings": [],
                    **formal_scope,
                    "pipeline_health": {"status": "OK"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        observed.update({"command": command, "options": options, "request": request})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "ai_test_asset_center.observed_product_scan_executor.subprocess.run",
        run_worker,
    )
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        subprocess_timeout_seconds=17.5,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        result = executor(
            runtime_view=artifacts["runtime_view"],
            campaign_id="campaign-subprocess",
            policy_id="policy-1",
            policy_version="v1",
            mainline_authority="experiment_candidate",
            evaluation_mode="shadow",
            fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
        )

    child_environment = observed["options"]["env"]
    assert "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY" not in child_environment
    assert "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEYRING" not in child_environment
    assert "QUALIBUG_TRUSTED_OBSERVATION_ROOT" not in child_environment
    assert "QUALIBUG_TRUSTED_OBSERVATION_HMAC_KEY" not in child_environment
    assert "QUALIBUG_BENCHMARK_GROUND_TRUTH" not in child_environment
    assert observed["options"]["timeout"] == 17.5
    assert observed["command"][1:3] == [
        "-m",
        "ai_test_asset_center.observed_product_scan_worker",
    ]
    context = observed["request"]["scan_kwargs"]["campaign_context"]
    contract = validate_mainline_run_contract(context["mainline_run"])
    assert context["run_id"] == contract["run_id"]
    assert context["target_id"] == contract["target_id"]
    assert context["environment_id"] == contract["environment_id"]
    assert result["run_id"] == contract["run_id"]
    assert result["mainline_run"] == contract
    assert result["process_boundary"]["isolation"] == "isolated_subprocess"
    assert result["process_boundary"]["evaluator_secrets_removed"] is True


def test_production_scan_timeout_fails_explicitly(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    observed: dict = {}

    def timeout_worker(command, **options):
        observed.update({"command": command, "options": options})
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(
        "ai_test_asset_center.observed_product_scan_executor.subprocess.run",
        timeout_worker,
    )
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        subprocess_timeout_seconds=0.25,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        with pytest.raises(
            PolicyEvaluationRunnerError,
            match="timed out after 0.25 seconds",
        ):
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-timeout",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={
                    "audit_receipt_id": "fixture-audit-1"
                },
            )

    assert observed["options"]["timeout"] == 0.25


@pytest.mark.parametrize("preexisting", [False, True])
def test_inline_evaluator_test_accounts_are_rejected_without_shared_write(
    tmp_path: Path,
    preexisting: bool,
) -> None:
    injected = {"admin": {"password": "ephemeral-evaluator-password"}}
    artifacts = _artifacts(tmp_path, test_accounts=injected)
    accounts_path = tmp_path / "platform_inputs" / "project-1" / "test_accounts.json"
    original = b'{"operator":"preserve-me"}'
    if preexisting:
        accounts_path.parent.mkdir(parents=True, exist_ok=True)
        accounts_path.write_bytes(original)

    called = False

    def scan_must_not_run(**_kwargs):
        nonlocal called
        called = True
        return {}

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan_must_not_run,
        allow_in_process_test_scan=True,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        with pytest.raises(
            PolicyEvaluationRunnerError,
            match="inline evaluator test_accounts forbidden",
        ):
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-accounts",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={"audit_receipt_id": "fixture-1"},
            )

    assert called is False
    if preexisting:
        assert accounts_path.read_bytes() == original
    else:
        assert not accounts_path.exists()


def test_subprocess_failure_redacts_secrets_before_embedding_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    secret = "sk-1234567890abcdefghijkl"

    def failed_worker(command, **_options):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=f"Authorization: Bearer {secret}; password=do-not-persist",
        )

    monkeypatch.setattr(
        "ai_test_asset_center.observed_product_scan_executor.subprocess.run",
        failed_worker,
    )
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        with pytest.raises(PolicyEvaluationRunnerError) as caught:
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-redaction",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={"audit_receipt_id": "fixture-1"},
            )

    diagnostic = str(caught.value)
    assert secret not in diagnostic
    assert "do-not-persist" not in diagnostic
    assert "REDACTED" in diagnostic


def test_executor_calls_real_scan_entrypoint_with_runtime_only_artifacts(monkeypatch, tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    calls = []

    def scan(**kwargs):
        calls.append(kwargs)
        contract = dict(kwargs["campaign_context"]["mainline_run"])
        _, attempt_ledger = build_formal_evaluation_scope(
            [],
            run_id=contract["run_id"],
            campaign_id=kwargs["campaign_context"]["campaign_id"],
            target_id="target-1",
            environment_id="http://127.0.0.1:8011",
            policy_version="v1",
            evaluation_mode="shadow",
        )
        formal_scope = build_formal_scope_contract(
            mainline_run=contract,
            findings=[],
            obligation_attempt_ledger=attempt_ledger,
        )
        return {
            "success": True,
            "scan_id": contract["run_id"],
            "execution_status": "completed",
            "mainline_run": contract,
            "findings": [],
            "candidate_findings": [],
            "shadow_findings": [],
            "obligation_attempt_ledger": attempt_ledger,
            "evaluator_canonical_findings": list(
                formal_scope["formal_count_projection"][
                    "canonical_representative_findings"
                ]
            ),
            **formal_scope,
            "pipeline_health": {"status": "OK"},
        }

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        result = executor(
            runtime_view=artifacts["runtime_view"],
            campaign_id="campaign-1",
            policy_id="policy-1",
            policy_version="v1",
            mainline_authority="experiment_candidate",
            evaluation_mode="shadow",
            fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
        )

    assert len(calls) == 1
    assert calls[0]["save_report"] is False
    assert calls[0]["base_url"] == "http://127.0.0.1:8011"
    assert calls[0]["campaign_context"]["campaign_id"] == "campaign-1"
    assert result["run_id"] == calls[0]["campaign_context"]["run_id"]
    assert result["execution_kind"] == "observed"
    assert result["estimated_metrics_used"] is False
    assert result["customer_outputs_published"] is False
    assert result["input_fingerprint"] == hashlib.sha256(artifacts["input"].read_bytes()).hexdigest()
    mainline_run = validate_mainline_run_contract(result["mainline_run"])
    assert mainline_run["mainline_authority"] == "experiment_candidate"
    assert mainline_run["run_id"] == calls[0]["campaign_context"]["run_id"]
    assert mainline_run["environment_id"] == "http://127.0.0.1:8011"
    assert calls[0]["campaign_context"]["mainline_authority"] == "experiment_candidate"
    assert calls[0]["campaign_context"]["mainline_run"] == mainline_run
    assert result["process_boundary"]["isolation"] == "in_process_test"
    assert result["process_boundary"]["evaluator_secrets_removed"] is False


def test_executor_uses_authority_scoped_shadow_projection_for_private_evaluator(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)

    def scan(**kwargs):
        contract = dict(kwargs["campaign_context"]["mainline_run"])
        formal, attempt_ledger = build_formal_evaluation_scope(
            [{"finding_id": "shadow-deliverable"}],
            run_id=contract["run_id"],
            campaign_id=kwargs["campaign_context"]["campaign_id"],
            target_id="target-1",
            environment_id="http://127.0.0.1:8011",
            policy_version="v1",
            evaluation_mode="shadow",
        )
        formal_scope = build_formal_scope_contract(
            mainline_run=contract,
            findings=formal,
            obligation_attempt_ledger=attempt_ledger,
        )
        return {
            "success": True,
            "scan_id": contract["run_id"],
            "execution_status": "completed",
            "mainline_run": contract,
            "findings": [],
            "candidate_findings": [],
            "shadow_findings": [
                formal[0],
                {
                    "finding_id": "shadow-rejected",
                    "mainline_run": {
                        "contract_fingerprint": contract["contract_fingerprint"],
                    },
                },
            ],
            "obligation_attempt_ledger": attempt_ledger,
            "evaluator_canonical_findings": list(
                formal_scope["formal_count_projection"][
                    "canonical_representative_findings"
                ]
            ),
            **formal_scope,
            "pipeline_health": {"status": "OK"},
        }

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        result = executor(
            runtime_view=artifacts["runtime_view"],
            campaign_id="campaign-shadow",
            policy_id="policy-1",
            policy_version="v1",
            mainline_authority="experiment_candidate",
            evaluation_mode="shadow",
            fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
        )

    assert [row["finding_id"] for row in result["findings"]] == [
        "shadow-deliverable"
    ]
    assert [row["finding_id"] for row in result["candidates"]] == [
        "shadow-rejected"
    ]
    assert result["evaluator_projection"]["authority_scope"] == "private_evaluator"


def test_executor_rejects_scan_shadow_contract_from_another_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)

    def scan(**kwargs):
        expected = kwargs["campaign_context"]["mainline_run"]
        return {
            "success": True,
            "scan_id": expected["run_id"],
            "execution_status": "completed",
            "mainline_run": build_mainline_run_contract(
                mainline_authority="experiment_candidate",
                run_id="different-run",
                campaign_id=kwargs["campaign_context"]["campaign_id"],
                target_id="target-1",
                environment_id="http://127.0.0.1:8011",
                policy_version="v1",
                evaluation_mode="shadow",
            ),
            "findings": [],
            "candidate_findings": [],
            "shadow_findings": [],
            "pipeline_health": {"status": "OK"},
        }

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        with pytest.raises(
            PolicyEvaluationRunnerError,
            match="scan mainline contract does not match evaluator run",
        ):
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-shadow",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
            )


def test_executor_rejects_scan_without_runtime_emitted_mainline_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)

    def scan(**kwargs):
        return {
            "success": True,
            "scan_id": kwargs["campaign_context"]["run_id"],
            "execution_status": "completed",
            "findings": [],
            "candidate_findings": [],
            "shadow_findings": [],
            "pipeline_health": {"status": "OK"},
        }

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        with pytest.raises(
            PolicyEvaluationRunnerError,
            match="must emit its frozen mainline_run",
        ):
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-shadow",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={
                    "audit_receipt_id": "fixture-audit-1"
                },
            )


def test_executor_rejects_scan_without_explicit_scan_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)

    def scan(**kwargs):
        return {
            "success": True,
            "execution_status": "completed",
            "findings": [],
            "candidate_findings": [],
            "shadow_findings": [],
            "pipeline_health": {"status": "OK"},
        }

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )
    strategy = StrategyBundle()
    strategy.execution.mainline_authority = "experiment_candidate"

    with policy_strategy_override(strategy):
        with pytest.raises(
            PolicyEvaluationRunnerError,
            match="explicit scan_id",
        ):
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-shadow",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={
                    "audit_receipt_id": "fixture-audit-1"
                },
            )


def test_executor_rejects_missing_mainline_authority_before_scan(monkeypatch, tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    called = False

    def scan(**kwargs):
        nonlocal called
        called = True
        return {}

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )

    with pytest.raises(PolicyEvaluationRunnerError, match="mainline_authority"):
        executor(
            runtime_view=artifacts["runtime_view"],
            campaign_id="campaign-1",
            policy_id="policy-1",
            policy_version="v1",
            evaluation_mode="shadow",
            fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
        )

    assert called is False


def test_executor_rejects_authority_that_differs_from_effective_policy(monkeypatch, tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    called = False

    def scan(**kwargs):
        nonlocal called
        called = True
        return {}

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )

    with policy_strategy_override(StrategyBundle()):
        with pytest.raises(PolicyEvaluationRunnerError, match="effective policy"):
            executor(
                runtime_view=artifacts["runtime_view"],
                campaign_id="campaign-1",
                policy_id="policy-1",
                policy_version="v1",
                mainline_authority="experiment_candidate",
                evaluation_mode="shadow",
                fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
            )

    assert called is False


def test_executor_rejects_context_artifact_with_ground_truth_key_before_scan(monkeypatch, tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path, private_context=True)
    called = False

    def scan(**kwargs):
        nonlocal called
        called = True
        return {}

    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
        scan_callable=scan,
        allow_in_process_test_scan=True,
    )

    with pytest.raises(PolicyEvaluationRunnerError, match="evaluator-private"):
        executor(
            runtime_view=artifacts["runtime_view"],
            campaign_id="campaign-1",
            policy_id="policy-1",
            policy_version="v1",
            mainline_authority="legacy_champion",
            evaluation_mode="replay",
            fixture_preparation_receipt={"audit_receipt_id": "fixture-audit-1"},
        )

    assert called is False


def test_finalize_after_cleanup_preserves_recovered_failure_count(monkeypatch, tmp_path: Path) -> None:
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.customer_delivery_gate.apply_governed_campaign_cleanup",
        lambda items, receipt: ([{"title": "readjudicated", "evidence": {"cleanup": {"status": "completed", "receipt_ref": "cleanup-1"}}}], []),
    )

    result = executor.finalize_after_cleanup(
        scan_output={
            "findings": [{"title": "cleanup-only"}],
            "candidates": [],
            "operational_metrics": {"cleanup_failures": 4, "dirty_test_environments": 1},
            "pipeline_health": {
                "status": "DEGRADED",
                "cleanup_failure_count": 4,
                "empty_findings_means_no_bugs": False,
                "unexecuted_candidate_signal": {
                    "stop_reason": "",
                    "pending_slice_count": 0,
                    "binding_or_precondition_blocks": 0,
                },
                "usage_cost_unknown": False,
                "operator_note": "4 条 finding cleanup 未成功",
            },
        },
        cleanup_receipt={
            "status": "SUCCEEDED",
            "audit_receipt_id": "cleanup-1",
            "after_cleanup_observation_ref": "state:clean",
            "dirty_environment": False,
        },
    )

    assert result["findings"][0]["title"] == "cleanup-only"
    assert result["operational_metrics"]["cleanup_failures"] == 4
    assert result["operational_metrics"]["environment_restored"] is True
    assert result["operational_metrics"]["scenario_cleanup_failures_before_campaign_reset"] == 4
    assert result["campaign_cleanup_finalization"]["cleanup_failures_preserved"] is True
    assert result["pipeline_health"]["cleanup_failure_count"] == 4
    assert result["pipeline_health"]["environment_restored"] is True
    assert result["pipeline_health"].get("campaign_cleanup_recovered") is False


def test_finalize_after_cleanup_keeps_degraded_when_other_signals_remain(monkeypatch, tmp_path: Path) -> None:
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=_operational_metrics,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.customer_delivery_gate.apply_governed_campaign_cleanup",
        lambda items, receipt: ([{"title": "ok", "evidence": {"cleanup": {"status": "completed", "receipt_ref": "c1"}}}], []),
    )

    result = executor.finalize_after_cleanup(
        scan_output={
            "findings": [{"title": "x"}],
            "candidates": [],
            "operational_metrics": {"cleanup_failures": 2, "dirty_test_environments": 1},
            "pipeline_health": {
                "status": "DEGRADED",
                "cleanup_failure_count": 2,
                "execution_reason": "preflight_health_failed",
                "unexecuted_candidate_signal": {
                    "stop_reason": "",
                    "pending_slice_count": 0,
                    "binding_or_precondition_blocks": 0,
                },
                "preflight": {"all_checks_passed": False},
                "operator_note": "preflight failed",
            },
        },
        cleanup_receipt={
            "status": "SUCCEEDED",
            "audit_receipt_id": "cleanup-1",
            "after_cleanup_observation_ref": "state:clean",
            "dirty_environment": False,
        },
    )

    assert result["pipeline_health"]["status"] == "DEGRADED"
    assert result["pipeline_health"]["cleanup_failure_count"] == 2
    assert result["operational_metrics"]["cleanup_failures"] == 2
    assert result["operational_metrics"]["environment_restored"] is True
