from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_test_asset_center.discovery_policy_evaluation_runner import (
    FIXTURE_CLEANUP_SCHEMA,
    FIXTURE_PREPARE_SCHEMA,
    SCAN_RESULT_SCHEMA,
    DiscoveryPolicyEvaluationRunner,
    PolicyEvaluationRunnerError,
    TrustedObservationStore,
    strategy_fingerprint,
)
from ai_test_asset_center.evaluator_receipt_auth import seal_evaluator_artifact
from ai_test_asset_center.observed_product_scan_executor import (
    ObservedProductScanExecutor,
    _run_isolated_product_worker,
    _merge_context_test_accounts_with_existing_credentials,
    _sanitized_worker_environment,
)
from ai_test_asset_center.policy_registry import PolicyRecord, StrategyBundle


_SIGNING_KEY = b"evaluator-owned-test-key-material-32-bytes-minimum"


def test_trusted_observation_store_loads_only_exact_run_identity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "product-workspace"
    trusted_root = tmp_path / "evaluator-observations"
    workspace.mkdir()
    trusted_root.mkdir()
    observation = {
        "obligation_id": "OBL-1",
        "execution_id": "EXEC-1",
        "source_kind": "evaluator_http_proxy",
        "source_receipt_id": "proxy-receipt-1",
        "source_fingerprint": "a" * 64,
        "target_request_count": 1,
        "write_count": 0,
        "production_request_count": 0,
        "audit_receipt_ids": [],
    }
    pack = seal_evaluator_artifact(
        {
            "schema_version": "qualibug.evaluator-trusted-observation-pack.v1",
            "created_at_utc": "2026-07-16T00:00:00Z",
            "run_id": "RUN-1",
            "campaign_id": "CMP-1",
            "target_id": "TARGET-1",
            "observations": [observation],
        },
        signing_key=_SIGNING_KEY,
        domain="qualibug.evaluator-trusted-observation-pack.v1",
        fingerprint_field="observation_pack_fingerprint",
        authentication_field="observation_pack_authentication",
    )
    (trusted_root / "RUN-1.json").write_text(
        json.dumps(pack),
        encoding="utf-8",
    )

    store = TrustedObservationStore(
        trusted_root,
        product_workspace_root=workspace,
        verification_key=_SIGNING_KEY,
    )

    assert store.load(
        run_id="RUN-1",
        campaign_id="CMP-1",
        target_id="TARGET-1",
    ) == [observation]
    with pytest.raises(PolicyEvaluationRunnerError, match="campaign_id mismatch"):
        store.load(
            run_id="RUN-1",
            campaign_id="CMP-OTHER",
            target_id="TARGET-1",
        )


def test_trusted_observation_store_must_be_outside_product_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "product-workspace"
    trusted_root = workspace / "trusted-observations"
    trusted_root.mkdir(parents=True)

    with pytest.raises(PolicyEvaluationRunnerError, match="outside product workspace"):
        TrustedObservationStore(
            trusted_root,
            product_workspace_root=workspace,
            verification_key=_SIGNING_KEY,
        )


def test_worker_environment_removes_every_evaluator_secret() -> None:
    sanitized = _sanitized_worker_environment({
        "PATH": "runtime-path",
        "QUALIBUG_EVALUATOR_HMAC_KEY": "secret",
        "QUALIBUG_TRUSTED_OBSERVATION_ROOT": "private-root",
        "BENCHMARK_GROUND_TRUTH_PATH": "private-ground-truth",
        "LLM_API_KEY": "product-provider-key",
    })

    assert sanitized["PATH"] == "runtime-path"
    assert sanitized["LLM_API_KEY"] == "product-provider-key"
    assert "QUALIBUG_EVALUATOR_HMAC_KEY" not in sanitized
    assert "QUALIBUG_TRUSTED_OBSERVATION_ROOT" not in sanitized
    assert "BENCHMARK_GROUND_TRUTH_PATH" not in sanitized


def test_context_account_snapshot_cannot_replace_fresh_fixture_credentials(
    tmp_path: Path,
) -> None:
    accounts_path = tmp_path / "platform_inputs" / "project" / "test_accounts.json"
    accounts_path.parent.mkdir(parents=True)
    accounts_path.write_text(
        json.dumps({
            "account-a@example.test": {
                "account_ref": "account-a",
                "email": "account-a@example.test",
                "token": "fresh-token",
                "password": "fresh-password",
            },
        }),
        encoding="utf-8",
    )

    merged = _merge_context_test_accounts_with_existing_credentials(
        accounts_path,
        {
            "accounts": [{
                "account_ref": "account-a",
                "email": "account-a@example.test",
                "role": "operator",
                "token": "stale-context-token",
                "password": "stale-context-password",
            }],
        },
    )

    row = merged["accounts"][0]
    assert row["role"] == "operator"
    assert row["token"] == "fresh-token"
    assert row["password"] == "fresh-password"


def test_isolated_worker_emits_a_fingerprinted_process_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("QUALIBUG_EVALUATOR_HMAC_KEY", "parent-only-secret")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert "QUALIBUG_EVALUATOR_HMAC_KEY" not in environment
        request_path = Path(command[command.index("--request") + 1])
        output_path = Path(command[command.index("--output") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert request["schema_version"] == (
            "qualibug.observed-product-scan-worker-request.v1"
        )
        output_path.write_text(
            json.dumps({
                "schema_version": "qualibug.discovery-evaluation-scan-result.v1",
                "run_id": "RUN-1",
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="worker log", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result, boundary = _run_isolated_product_worker(
        {
            "schema_version": "qualibug.observed-product-scan-worker-request.v1",
            "runtime_view": {"target": {"target_id": "TARGET-1"}},
        },
        workspace_root=workspace,
        timeout_seconds=10,
    )

    assert result["run_id"] == "RUN-1"
    assert boundary["isolation"] == "isolated_subprocess"
    assert boundary["evaluator_secrets_removed"] is True
    assert boundary["exit_code"] == 0
    assert len(boundary["request_fingerprint"]) == 64
    assert len(boundary["result_fingerprint"]) == 64


def test_product_worker_preserves_the_full_formal_evaluation_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "generic-project"
    base_url = "http://127.0.0.1:8080"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    api_path = runtime_root / "api.md"
    prd_path = runtime_root / "prd.md"
    fixture_path = runtime_root / "fixture.json"
    input_path = runtime_root / "input.json"
    context_path = runtime_root / "context.json"
    api_path.write_text("GET /resources", encoding="utf-8")
    prd_path.write_text("Resources remain visible to their owner.", encoding="utf-8")
    fixture_path.write_text("{}", encoding="utf-8")
    input_path.write_text(
        json.dumps({
            "schema_version": "qualibug.discovery-evaluation-input.v1",
            "project_id": project_id,
            "base_url": base_url,
            "api_doc_ref": str(api_path),
            "prd_ref": str(prd_path),
            "multi_layer": True,
        }),
        encoding="utf-8",
    )
    context_path.write_text(
        json.dumps({
            "schema_version": "qualibug.discovery-evaluation-context.v1",
            "campaign_context": {
                "runtime_interface_discovery_enabled": True,
            },
        }),
        encoding="utf-8",
    )
    mainline = {
        "run_id": "RUN-FORMAL",
        "campaign_id": "CMP-PRODUCT",
        "target_id": "TARGET-FORMAL",
        "environment_id": base_url,
        "policy_version": "policy-v1",
    }
    canonical = {"canonical_defect_id": "DEFECT-1", "title": "Observed defect"}
    captured_context: dict[str, object] = {}
    captured_base_url: list[str] = []

    def fake_scan(**kwargs: object) -> dict[str, object]:
        captured_context.update(dict(kwargs["campaign_context"]))
        captured_base_url.append(str(kwargs["base_url"]))
        return {
            "success": True,
            "scan_id": "product-display-scan-id",
            "pipeline_health": {"status": "MEASURED"},
            "findings": [],
            "candidate_findings": [],
            "mainline_run": mainline,
            "obligation_attempt_ledger": {"schema_version": "ledger"},
            "canonical_defect_registry": {"schema_version": "registry"},
            "formal_delivery_authority": {"schema_version": "authority"},
            "formal_count_projection": {"schema_version": "projection"},
            "defect_identity_consistency": {"schema_version": "consistency"},
            "delivery_occurrences": [{"finding_id": "FINDING-1"}],
            "v12": {
                "evaluator_canonical_findings": [canonical],
                "runtime_interface_discovery": {
                    "status": "EXECUTED",
                    "execution": {
                        "selected_count": 1,
                        "execution_results": {
                            "surfobl-1": {
                                "execution_id": "surfexec-1",
                                "status": "EXECUTED",
                                "operational_receipt": {
                                    "http_request_attempt_count": 1,
                                    "write_request_attempt_count": 0,
                                    "production_http_request_count": 0,
                                },
                            },
                        },
                    },
                },
            },
        }

    from ai_test_asset_center import __main__ as scan_module

    monkeypatch.setattr(scan_module, "scan", fake_scan)
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=lambda **_: {"complete": True},
    )
    output = executor._execute_in_process(
        runtime_view={
            "target": {
                "target_id": "TARGET-FORMAL",
                "project_id": project_id,
                "runtime_fingerprint": "runtime-fingerprint",
                "runtime": {
                    "environment_ref": base_url,
                    "environment_type": "test",
                    "input_bundle_ref": str(input_path),
                    "context_artifact_ref": str(context_path),
                    "fixture_snapshot_ref": str(fixture_path),
                },
            },
        },
        campaign_id="CMP-FORMAL",
        policy_id="policy-id",
        policy_version="policy-v1",
        evaluation_mode="replay",
        fixture_preparation_receipt={"audit_receipt_id": "PREP-1"},
        observation_proxy_base_url="http://127.0.0.1:19090",
        agent_semantic_linking_enabled=True,
    )

    assert output["run_id"] == "RUN-FORMAL"
    assert output["findings"] == [canonical]
    assert output["runtime_interface_discovery"]["status"] == "EXECUTED"
    assert output["runtime_interface_discovery"]["execution"]["selected_count"] == 1
    for field in (
        "mainline_run",
        "obligation_attempt_ledger",
        "canonical_defect_registry",
        "formal_delivery_authority",
        "formal_count_projection",
        "defect_identity_consistency",
        "delivery_occurrences",
    ):
        assert field in output
    assert captured_context["target_id"] == "TARGET-FORMAL"
    assert captured_context["environment_id"] == base_url
    assert captured_context["policy_version"] == "policy-v1"
    assert captured_context["agent_semantic_linking_enabled"] is True
    assert captured_context["evaluation_campaign_id"] == "CMP-FORMAL"
    assert "campaign_id" not in captured_context
    assert output["campaign_id"] == "CMP-FORMAL"
    assert output["mainline_run"]["campaign_id"] == "CMP-PRODUCT"
    assert captured_base_url == ["http://127.0.0.1:19090"]


def test_product_worker_surfaces_the_product_failure_before_metric_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "generic-project"
    base_url = "http://127.0.0.1:8080"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    api_path = runtime_root / "api.md"
    prd_path = runtime_root / "prd.md"
    fixture_path = runtime_root / "fixture.json"
    input_path = runtime_root / "input.json"
    context_path = runtime_root / "context.json"
    api_path.write_text("GET /resources", encoding="utf-8")
    prd_path.write_text("Resources remain visible to their owner.", encoding="utf-8")
    fixture_path.write_text("{}", encoding="utf-8")
    input_path.write_text(
        json.dumps({
            "schema_version": "qualibug.discovery-evaluation-input.v1",
            "project_id": project_id,
            "base_url": base_url,
            "api_doc_ref": str(api_path),
            "prd_ref": str(prd_path),
            "multi_layer": True,
        }),
        encoding="utf-8",
    )
    context_path.write_text(
        json.dumps({
            "schema_version": "qualibug.discovery-evaluation-context.v1",
            "campaign_context": {},
        }),
        encoding="utf-8",
    )

    from ai_test_asset_center import __main__ as scan_module

    monkeypatch.setattr(
        scan_module,
        "scan",
        lambda **_: {
            "success": False,
            "error": "v12_pipeline_failed:MainlineContractError:source_identity_invalid",
            "failure_stage": "v12",
        },
    )
    metric_calls: list[dict[str, object]] = []
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=lambda **kwargs: metric_calls.append(kwargs) or {},
    )

    with pytest.raises(
        PolicyEvaluationRunnerError,
        match="product scan failed before evaluator projection.*source_identity_invalid",
    ):
        executor._execute_in_process(
            runtime_view={
                "target": {
                    "target_id": "TARGET-FAILED",
                    "project_id": project_id,
                    "runtime_fingerprint": "runtime-fingerprint",
                    "runtime": {
                        "environment_ref": base_url,
                        "environment_type": "test",
                        "input_bundle_ref": str(input_path),
                        "context_artifact_ref": str(context_path),
                        "fixture_snapshot_ref": str(fixture_path),
                    },
                },
            },
            campaign_id="CMP-FAILED",
            policy_id="policy-id",
            policy_version="policy-v1",
            evaluation_mode="replay",
            fixture_preparation_receipt={"audit_receipt_id": "PREP-FAILED"},
            observation_proxy_base_url="http://127.0.0.1:19090",
        )

    assert metric_calls == []


def test_campaign_cleanup_validates_occurrences_without_rewriting_canonical_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {"canonical_defect_id": "DEFECT-1", "title": "canonical"}
    occurrence = {"finding_id": "FINDING-1", "title": "occurrence"}
    observed_items: list[dict[str, object]] = []

    def fake_cleanup(
        items: list[dict[str, object]],
        receipt: dict[str, object],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        observed_items.extend(items)
        assert receipt["status"] == "SUCCEEDED"
        return list(items), []

    import ai_test_asset_center.customer_delivery_gate as gate_module

    monkeypatch.setattr(gate_module, "apply_governed_campaign_cleanup", fake_cleanup)
    executor = ObservedProductScanExecutor(
        workspace_root=tmp_path,
        operational_metrics_collector=lambda **_: {},
    )
    scan_output = {
        "findings": [canonical],
        "delivery_occurrences": [occurrence],
        "candidates": [],
        "operational_metrics": {"cleanup_failures": 0},
        "pipeline_health": {"status": "MEASURED"},
        "formal_delivery_authority": {"authority": "immutable"},
        "formal_count_projection": {"count": 1},
        "canonical_defect_registry": {"ids": ["DEFECT-1"]},
        "defect_identity_consistency": {"status": "VERIFIED"},
    }

    finalized = executor.finalize_after_cleanup(
        scan_output=scan_output,
        cleanup_receipt={
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "audit_receipt_id": "CLEANUP-1",
            "after_cleanup_observation_ref": "OBS-1",
        },
    )

    assert observed_items == [occurrence]
    assert finalized["findings"] == [canonical]
    assert finalized["formal_delivery_authority"] == {"authority": "immutable"}
    assert finalized["formal_count_projection"] == {"count": 1}


def test_runner_passes_full_authority_and_independent_attestation_to_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center import discovery_policy_evaluation_runner as runner_module

    target = SimpleNamespace(
        target_id="TARGET-1",
        environment_ref="http://127.0.0.1:8080",
        environment_type="test",
    )
    expected_fingerprints = {
        "runtime_fingerprint": "runtime-fingerprint",
        "input_fingerprint": "input-fingerprint",
        "fixture_fingerprint": "fixture-fingerprint",
        "context_fingerprint": "context-fingerprint",
    }
    manifest = SimpleNamespace(
        targets=[target],
        target_fingerprints={target.target_id: expected_fingerprints},
    )
    policy = PolicyRecord(
        policy_id="policy-1",
        policy_version="policy-v1",
        parent_policy_version="",
        project_scope="global",
        status="active",
        created_reason="test",
        strategy=StrategyBundle(),
    )
    campaign_id = "EVAL-1:replay:champion:TARGET-1"
    product_campaign_id = "CMP-PRODUCT"
    mainline = {
        "run_id": "RUN-1",
        "campaign_id": product_campaign_id,
        "target_id": target.target_id,
        "environment_id": target.environment_ref,
        "policy_version": policy.policy_version,
        "evaluation_mode": "replay",
    }
    preparation = {
        "schema_version": FIXTURE_PREPARE_SCHEMA,
        "target_id": target.target_id,
        "campaign_id": campaign_id,
        "environment_ref": target.environment_ref,
        "environment_type": target.environment_type,
        "fixture_fingerprint": "fixture-fingerprint",
        "status": "READY",
        "governed_sandbox_executor": True,
        "audit_receipt_id": "PREP-1",
        "before_observation_ref": "BEFORE-1",
        "after_observation_ref": "AFTER-1",
        "production_http_requests": 0,
    }
    cleanup = {
        "schema_version": FIXTURE_CLEANUP_SCHEMA,
        "target_id": target.target_id,
        "campaign_id": campaign_id,
        "environment_ref": target.environment_ref,
        "environment_type": target.environment_type,
        "fixture_fingerprint": "fixture-fingerprint",
        "status": "SUCCEEDED",
        "dirty_environment": False,
        "audit_receipt_id": "CLEANUP-1",
        "after_cleanup_observation_ref": "CLEAN-AFTER-1",
        "production_http_requests": 0,
    }

    class FixtureController:
        def prepare(self, **_: object) -> dict[str, object]:
            return preparation

        def cleanup(self, **_: object) -> dict[str, object]:
            return cleanup

    class ScanExecutor:
        def __call__(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["observation_proxy_base_url"] == (
                "http://127.0.0.1:19090"
            )
            assert kwargs["agent_semantic_linking_enabled"] is True
            return {
                "schema_version": SCAN_RESULT_SCHEMA,
                "run_id": "RUN-1",
                "target_id": target.target_id,
                "campaign_id": campaign_id,
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "evaluation_mode": "replay",
                "execution_kind": "observed",
                "estimated_metrics_used": False,
                "customer_outputs_published": False,
                "effective_strategy_fingerprint": strategy_fingerprint(policy.strategy),
                "fixture_audit_receipt_id": "PREP-1",
                **expected_fingerprints,
                "findings": [],
                "candidates": [],
                "delivery_occurrences": [],
                "pipeline_health": {"status": "MEASURED"},
                "operational_metrics": {"complete": True},
                "mainline_run": mainline,
                "obligation_attempt_ledger": {"schema_version": "ledger"},
                "canonical_defect_registry": {"schema_version": "registry"},
                "formal_delivery_authority": {"schema_version": "authority"},
                "formal_count_projection": {"schema_version": "projection"},
                "defect_identity_consistency": {"schema_version": "consistency"},
                "process_boundary": {"schema_version": "boundary"},
                "trace_ledger": None,
            }

    class ObservationGateway:
        @contextmanager
        def observe(self, **kwargs: object):
            assert kwargs == {
                "upstream_base_url": target.environment_ref,
                "campaign_id": campaign_id,
                "target_id": target.target_id,
                "environment_type": target.environment_type,
            }
            yield "http://127.0.0.1:19090"

    class ObservationStore:
        def load(self, **identity: str) -> list[dict[str, object]]:
            assert identity == {
                "run_id": "RUN-1",
                "campaign_id": product_campaign_id,
                "target_id": target.target_id,
            }
            return [{"obligation_id": "OBL-1"}]

    captured: dict[str, object] = {}

    def fake_attestation(**kwargs: object) -> dict[str, object]:
        captured["attestation_inputs"] = kwargs
        return {"schema_version": "attestation", "status": "VERIFIED"}

    def fake_evaluate(*_: object, **kwargs: object) -> dict[str, object]:
        captured["evaluation_inputs"] = kwargs
        return {"schema_version": "receipt", "policy_id": policy.policy_id}

    monkeypatch.setattr(runner_module, "build_runtime_view", lambda *_: {"target": {}})
    monkeypatch.setattr(runner_module, "build_execution_attestation", fake_attestation)
    monkeypatch.setattr(runner_module, "evaluate_completed_scan", fake_evaluate)
    monkeypatch.setattr(runner_module, "persist_evaluation_receipt", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner_module,
        "aggregate_evaluation_receipts",
        lambda *_a, **_k: {"policy_id": policy.policy_id, "evaluation_mode": "replay"},
    )
    monkeypatch.setattr(runner_module, "persist_evaluation_report", lambda *_a, **_k: None)

    runner = DiscoveryPolicyEvaluationRunner.__new__(DiscoveryPolicyEvaluationRunner)
    runner.output_root = tmp_path
    runner.fixture_controller = FixtureController()
    runner.scan_executor = ScanExecutor()
    runner.trusted_observation_gateway = ObservationGateway()
    runner.manifest = manifest
    runner.trusted_observation_store = ObservationStore()
    runner.receipt_signing_key = b"signing-key"
    runner._assert_manifest_frozen = lambda: None

    runner._run_policy_mode(
        policy=policy,
        role="champion",
        evaluation_mode="replay",
        evaluation_id="EVAL-1",
    )

    evaluation_inputs = captured["evaluation_inputs"]
    assert isinstance(evaluation_inputs, dict)
    for field in (
        "delivery_occurrences",
        "obligation_attempt_ledger",
        "mainline_run",
        "formal_count_projection",
        "formal_delivery_authority",
        "canonical_defect_registry",
        "defect_identity_consistency",
        "evaluator_policy_identity",
        "process_boundary",
        "execution_attestation",
        "receipt_signing_key",
    ):
        assert field in evaluation_inputs


def test_diagnostic_shape_bypass_cannot_be_used_for_promotion() -> None:
    policy = PolicyRecord(
        policy_id="policy-1",
        policy_version="policy-v1",
        parent_policy_version="",
        project_scope="global",
        status="active",
        created_reason="test",
        strategy=StrategyBundle(),
    )
    runner = DiscoveryPolicyEvaluationRunner.__new__(DiscoveryPolicyEvaluationRunner)
    runner._commercial_shape = {
        "commercial_shape_ready": False,
        "checks": [{"name": "held_out_industry_count", "passed": False}],
    }

    with pytest.raises(
        PolicyEvaluationRunnerError,
        match="promotion evaluation requires a commercial-shape dataset",
    ):
        runner.run(champion=policy, challenger=policy)


def test_target_diagnostic_runs_one_manifest_target_without_promotion_role() -> None:
    policy = PolicyRecord(
        policy_id="policy-1",
        policy_version="policy-v1",
        parent_policy_version="",
        project_scope="global",
        status="active",
        created_reason="test",
        strategy=StrategyBundle(),
    )
    runner = DiscoveryPolicyEvaluationRunner.__new__(DiscoveryPolicyEvaluationRunner)
    runner.manifest = SimpleNamespace(
        targets=[
            SimpleNamespace(target_id="TARGET-1"),
            SimpleNamespace(target_id="TARGET-2"),
        ]
    )
    captured: dict[str, object] = {}

    def fake_run_policy_mode(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"schema_version": "report"}

    runner._run_policy_mode = fake_run_policy_mode

    assert runner.run_target_diagnostic(
        policy=policy,
        target_id="TARGET-2",
        evaluation_mode="shadow",
        evaluation_id="diag-1",
    ) == {"schema_version": "report"}
    assert captured["policy"] == policy
    assert captured["role"] == "diagnostic"
    assert captured["evaluation_mode"] == "shadow"
    assert captured["evaluation_id"] == "diag-1"
    assert captured["target_ids"] == {"TARGET-2"}

    with pytest.raises(
        PolicyEvaluationRunnerError,
        match="diagnostic target is not in the frozen manifest",
    ):
        runner.run_target_diagnostic(policy=policy, target_id="TARGET-3")
