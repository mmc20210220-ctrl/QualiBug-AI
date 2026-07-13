from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center.discovery_evaluation_contract import (
    EvaluationContractError,
    MANIFEST_SCHEMA,
    load_evaluation_manifest,
    validate_authenticated_policy_comparison,
)
from ai_test_asset_center.discovery_policy_evaluation_runner import (
    COMPARISON_SCHEMA,
    FIXTURE_CLEANUP_SCHEMA,
    FIXTURE_PREPARE_SCHEMA,
    SCAN_RESULT_SCHEMA,
    DiscoveryPolicyEvaluationRunner,
    PolicyEvaluationRunnerError,
    strategy_fingerprint,
)
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.autonomous_evolution_orchestrator import EvolutionOrchestrator
from ai_test_asset_center.evaluator_receipt_auth import (
    EVALUATOR_HMAC_KEY_ENV,
    EVALUATOR_HMAC_KEYRING_ENV,
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)
from ai_test_asset_center.evaluator_execution_attestation import (
    PROCESS_BOUNDARY_SCHEMA,
)
from ai_test_asset_center.policy_registry import PolicyRecord, PolicyRegistry, StrategyBundle
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


TEST_EVALUATOR_HMAC_KEY = "policy-runner-test-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _evaluator_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY",
        TEST_EVALUATOR_HMAC_KEY,
    )


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _target(root: Path, target_id: str, industry: str, split: str, expectation: str) -> dict[str, Any]:
    input_path = root / "inputs" / f"{target_id}.json"
    fixture_path = root / "fixtures" / f"{target_id}.json"
    context_path = root / "contexts" / f"{target_id}.json"
    _write(input_path, {"target_id": target_id})
    _write(fixture_path, {"fixture": target_id})
    _write(context_path, {"context": target_id})
    evaluator: dict[str, str] = {}
    if expectation == "seeded_defects":
        truth_path = root / "private" / f"{target_id}.json"
        _write(
            truth_path,
            [{
                "bug_id": f"BUG-{target_id}",
                "title": f"{target_id} defect",
                "severity": "P1",
                "type": "authorization_access_control",
                "match_keywords": ["alpha", "beta", "gamma", "delta"],
            }],
        )
        evaluator["ground_truth_ref"] = str(truth_path.relative_to(root))
    return {
        "target_id": target_id,
        "project_id": f"project-{target_id}",
        "industry": industry,
        "split": split,
        "expectation": expectation,
        "runtime": {
            "environment_ref": f"env-{target_id}",
            "environment_type": "test",
            "input_bundle_ref": str(input_path.relative_to(root)),
            "fixture_snapshot_ref": str(fixture_path.relative_to(root)),
            "context_artifact_ref": str(context_path.relative_to(root)),
        },
        "evaluator": evaluator,
    }


def _manifest(root: Path) -> Path:
    path = root / "manifest.json"
    _write(
        path,
        {
            "schema_version": MANIFEST_SCHEMA,
            "dataset_id": "observed-runner-contract",
            "dataset_version": "v1",
            "targets": [
                _target(root, "held-in", "commerce", "held_in", "seeded_defects"),
                _target(root, "finance", "finance", "held_out", "seeded_defects"),
                _target(root, "health", "healthcare", "held_out", "seeded_defects"),
                _target(root, "saas", "enterprise-saas", "held_out", "seeded_defects"),
                _target(root, "clean", "commerce", "held_out", "clean"),
            ],
        },
    )
    return path


def _finding(target_id: str) -> dict[str, Any]:
    return {
        "candidate_id": f"candidate-{target_id}",
        "slice_id": f"slice-{target_id}",
        "obligation_id": f"obligation-{target_id}",
        "experiment_id": f"experiment-{target_id}",
        "execution_id": f"execution-{target_id}",
        "evidence_id": f"evidence-{target_id}",
        "finding_id": f"finding-{target_id}",
        "title": f"alpha beta gamma delta on {target_id}",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "evidence_level": "runtime",
        "execution_source": "live-test-target",
        "expected": "denied",
        "actual": "allowed",
        "timestamp": "2026-07-10T00:00:00Z",
        "evidence_consistency": {"verdict": "confirmed"},
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "reproduction": {
            "method": "GET",
            "path": f"/api/{target_id}",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"allowed": True}},
        },
        "raw_evidence": {
            "request_raw": {"method": "GET", "path": f"/api/{target_id}"},
            "response_raw": {"status_code": 200, "body": {"allowed": True}},
            "timestamp": "2026-07-10T00:00:00Z",
            "has_real_evidence": True,
        },
    }


def _policies() -> tuple[PolicyRecord, PolicyRecord]:
    champion_strategy = StrategyBundle()
    challenger_strategy = StrategyBundle()
    challenger_strategy.execution.cleanup_retry_count = 2
    challenger_strategy.execution.mainline_authority = "experiment_candidate"
    champion = PolicyRecord(
        policy_id="champion",
        policy_version="v1",
        parent_policy_version="",
        project_scope="global",
        status="active",
        created_reason="test contract",
        strategy=champion_strategy,
    )
    challenger = PolicyRecord(
        policy_id="challenger",
        policy_version="v1+candidate",
        parent_policy_version="v1",
        project_scope="global",
        status="candidate",
        created_reason="test contract",
        strategy=challenger_strategy,
    )
    return champion, challenger


class RecordingFixtureController:
    def __init__(self, manifest_path: Path, *, fail_cleanup_target: str = "") -> None:
        self.manifest = load_evaluation_manifest(manifest_path)
        self.fail_cleanup_target = fail_cleanup_target
        self.prepare_calls: list[str] = []
        self.cleanup_calls: list[str] = []

    def prepare(self, **kwargs: Any) -> dict[str, Any]:
        target = kwargs["runtime_view"]["target"]
        target_id = target["target_id"]
        self.prepare_calls.append(kwargs["campaign_id"])
        return {
            "schema_version": FIXTURE_PREPARE_SCHEMA,
            "target_id": target_id,
            "campaign_id": kwargs["campaign_id"],
            "environment_ref": target["runtime"]["environment_ref"],
            "environment_type": target["runtime"]["environment_type"],
            "fixture_fingerprint": kwargs["expected_fixture_fingerprint"],
            "status": "READY",
            "governed_sandbox_executor": True,
            "audit_receipt_id": f"prepare-{hashlib.sha256(kwargs['campaign_id'].encode()).hexdigest()[:12]}",
            "before_observation_ref": f"before:{target_id}",
            "after_observation_ref": f"after:{target_id}",
            "production_http_requests": 0,
        }

    def cleanup(self, **kwargs: Any) -> dict[str, Any]:
        target = kwargs["runtime_view"]["target"]
        target_id = target["target_id"]
        self.cleanup_calls.append(kwargs["campaign_id"])
        expected = self.manifest.target_fingerprints[target_id]["fixture_fingerprint"]
        failed = target_id == self.fail_cleanup_target
        return {
            "schema_version": FIXTURE_CLEANUP_SCHEMA,
            "target_id": target_id,
            "campaign_id": kwargs["campaign_id"],
            "environment_ref": target["runtime"]["environment_ref"],
            "environment_type": target["runtime"]["environment_type"],
            "fixture_fingerprint": expected,
            "status": "FAILED" if failed else "SUCCEEDED",
            "dirty_environment": failed,
            "audit_receipt_id": f"cleanup-{hashlib.sha256(kwargs['campaign_id'].encode()).hexdigest()[:12]}",
            "after_cleanup_observation_ref": f"cleanup:{target_id}",
            "production_http_requests": 0,
        }


class RecordingScanExecutor:
    def __init__(
        self,
        manifest_path: Path,
        policy_strategies: dict[str, StrategyBundle],
        *,
        publish_shadow: bool = False,
        returned_authority_override: str = "",
    ) -> None:
        self.manifest = load_evaluation_manifest(manifest_path)
        self.policy_strategies = policy_strategies
        self.champion_policy_id, self.challenger_policy_id = tuple(policy_strategies)
        self.publish_shadow = publish_shadow
        self.returned_authority_override = returned_authority_override
        self.calls: list[tuple[str, str, str]] = []
        self.authority_calls: list[tuple[str, str, str]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        target_id = kwargs["runtime_view"]["target"]["target_id"]
        policy_id = kwargs["policy_id"]
        mode = kwargs["evaluation_mode"]
        authority = kwargs["mainline_authority"]
        self.calls.append((mode, policy_id, target_id))
        self.authority_calls.append((mode, policy_id, authority))
        fingerprints = self.manifest.target_fingerprints[target_id]
        seeded = target_id != "clean"
        champion_finds = policy_id == self.champion_policy_id and target_id == "held-in"
        challenger_finds = policy_id == self.challenger_policy_id
        raw_findings = [_finding(target_id)] if seeded and (challenger_finds or champion_finds) else []
        run_id = f"run-{len(self.calls)}-{mode}-{policy_id}-{target_id}"
        mainline_run = build_mainline_run_contract(
            mainline_authority=self.returned_authority_override or authority,
            run_id=run_id,
            campaign_id=kwargs["campaign_id"],
            target_id=target_id,
            environment_id=kwargs["runtime_view"]["target"]["runtime"]["environment_ref"],
            policy_version=kwargs["policy_version"],
            evaluation_mode=mode,
        )
        findings, attempt_ledger = build_formal_evaluation_scope(
            raw_findings,
            run_id=run_id,
            campaign_id=kwargs["campaign_id"],
            target_id=target_id,
            environment_id=kwargs["runtime_view"]["target"]["runtime"][
                "environment_ref"
            ],
            policy_version=kwargs["policy_version"],
            evaluation_mode=mode,
            mainline_authority=self.returned_authority_override or authority,
        )
        formal_scope = build_formal_scope_contract(
            mainline_run=mainline_run,
            findings=findings,
            obligation_attempt_ledger=attempt_ledger,
        )
        return {
            "schema_version": SCAN_RESULT_SCHEMA,
            "run_id": run_id,
            "target_id": target_id,
            "campaign_id": kwargs["campaign_id"],
            "policy_id": policy_id,
            "policy_version": kwargs["policy_version"],
            "evaluation_mode": mode,
            "execution_kind": "observed",
            "estimated_metrics_used": False,
            "customer_outputs_published": self.publish_shadow if mode == "shadow" else False,
            "mainline_run": mainline_run,
            "effective_strategy_fingerprint": strategy_fingerprint(self.policy_strategies[policy_id]),
            "fixture_audit_receipt_id": kwargs["fixture_preparation_receipt"]["audit_receipt_id"],
            **{field: fingerprints[field] for field in (
                "runtime_fingerprint",
                "input_fingerprint",
                "fixture_fingerprint",
                "context_fingerprint",
            )},
            "findings": list(
                formal_scope["formal_count_projection"][
                    "canonical_representative_findings"
                ]
            ),
            "candidates": [],
            "obligation_attempt_ledger": attempt_ledger,
            **formal_scope,
            "pipeline_health": {"status": "OK"},
            "operational_metrics": {
                "wall_clock_seconds": 1,
                "estimated_cost_usd": 1,
                "request_count": 2,
                "production_http_requests": 0,
                "cleanup_failures": 0,
                "safety_incidents": 0,
                "dirty_test_environments": 0,
                "execution_success_rate": 1,
                "engine_success_rate": 1,
                "duplicate_rate": 0,
            },
            "process_boundary": {
                "schema_version": PROCESS_BOUNDARY_SCHEMA,
                "isolation": "isolated_subprocess",
                "worker_protocol_schema": (
                    "qualibug.observed-product-scan-worker-request.v1"
                ),
                "evaluator_secrets_removed": True,
                "request_fingerprint": "a" * 64,
                "result_fingerprint": "b" * 64,
                "exit_code": 0,
            },
        }


def _trusted_observations(**kwargs: Any) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for attempt in kwargs["scan_output"]["obligation_attempt_ledger"][
        "attempts"
    ]:
        operational = attempt.get("operational_receipt") or {}
        request_count = int(
            operational.get("http_request_attempt_count") or 0
        )
        if request_count == 0:
            continue
        observations.append({
            "obligation_id": attempt["obligation_id"],
            "execution_id": attempt["execution_id"],
            "source_kind": "evaluator_http_proxy",
            "source_receipt_id": (
                f"proxy:{kwargs['campaign_id']}:{attempt['obligation_id']}"
            ),
            "source_fingerprint": hashlib.sha256(
                f"{kwargs['campaign_id']}:{attempt['obligation_id']}".encode(
                    "utf-8"
                )
            ).hexdigest(),
            "target_request_count": request_count,
            "write_count": int(
                operational.get("accepted_write_count") or 0
            ),
            "production_request_count": int(
                operational.get("production_http_request_count") or 0
            ),
            "audit_receipt_ids": [],
        })
    return observations


def _runner(
    tmp_path: Path,
    *,
    fail_cleanup_target: str = "",
    publish_shadow: bool = False,
    returned_authority_override: str = "",
) -> tuple[DiscoveryPolicyEvaluationRunner, RecordingFixtureController, RecordingScanExecutor, PolicyRecord, PolicyRecord]:
    manifest_path = _manifest(tmp_path)
    champion, challenger = _policies()
    controller = RecordingFixtureController(manifest_path, fail_cleanup_target=fail_cleanup_target)
    executor = RecordingScanExecutor(
        manifest_path,
        {champion.policy_id: champion.strategy, challenger.policy_id: challenger.strategy},
        publish_shadow=publish_shadow,
        returned_authority_override=returned_authority_override,
    )
    runner = DiscoveryPolicyEvaluationRunner(
        manifest_path,
        output_root=tmp_path / "evaluations",
        fixture_controller=controller,
        scan_executor=executor,
        trusted_observation_provider=_trusted_observations,
    )
    return runner, controller, executor, champion, challenger


def test_runner_preserves_retired_key_verification_after_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_key = "old-policy-runner-key-0123456789abcdef"
    new_key = "new-policy-runner-key-0123456789abcdef"
    old_key_id = hashlib.sha256(old_key.encode("utf-8")).hexdigest()[:24]
    new_key_id = hashlib.sha256(new_key.encode("utf-8")).hexdigest()[:24]
    monkeypatch.delenv(EVALUATOR_HMAC_KEY_ENV, raising=False)
    monkeypatch.setenv(
        EVALUATOR_HMAC_KEYRING_ENV,
        json.dumps({
            "active_key_id": new_key_id,
            "keys": {
                old_key_id: old_key,
                new_key_id: new_key,
            },
        }),
    )
    runner, *_ = _runner(tmp_path)
    domain = "qualibug.runner-keyring-test.v1"
    retired = seal_evaluator_artifact(
        {"schema_version": domain, "value": 1},
        signing_key=old_key,
        domain=domain,
        fingerprint_field="artifact_fingerprint",
        authentication_field="artifact_authentication",
    )

    verified = verify_evaluator_artifact(
        retired,
        signing_key=runner.receipt_signing_key,
        domain=domain,
        fingerprint_field="artifact_fingerprint",
        authentication_field="artifact_authentication",
    )

    assert runner.receipt_signing_key is None
    assert verified == retired


def test_runner_executes_every_target_four_times_and_never_activates_candidate(tmp_path: Path) -> None:
    runner, controller, executor, champion, challenger = _runner(tmp_path)

    result = runner.run(champion=champion, challenger=challenger, evaluation_id="observed-eval")

    assert result["schema_version"] == COMPARISON_SCHEMA
    assert result["observed_execution"] is True
    assert result["estimated_metrics_used"] is False
    assert result["activation_performed"] is False
    assert len(executor.calls) == 20
    assert len(set(executor.calls)) == 20
    assert len(controller.prepare_calls) == 20
    assert len(controller.cleanup_calls) == 20
    assert result["promotion_decision"]["promote"] is True
    assert challenger.status == "candidate"
    assert Path(result["comparison_ref"]).is_file()
    for report_name, report_ref in result["report_refs"].items():
        report = json.loads(Path(report_ref).read_text(encoding="utf-8"))
        expected_policy = (
            champion if report_name.startswith("champion_") else challenger
        )
        assert report["policy_identity"] == {
            "policy_id": expected_policy.policy_id,
            "policy_version": expected_policy.policy_version,
            "strategy_fingerprint": strategy_fingerprint(
                expected_policy.strategy
            ),
            "target_mainline_contract_fingerprints": report[
                "policy_identity"
            ]["target_mainline_contract_fingerprints"],
        }


def _reseal_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
    return seal_evaluator_artifact(
        comparison,
        signing_key=TEST_EVALUATOR_HMAC_KEY,
        domain=COMPARISON_SCHEMA,
        fingerprint_field="comparison_fingerprint",
        authentication_field="comparison_authentication",
    )


def test_strict_comparison_validator_requires_all_four_target_reports(
    tmp_path: Path,
) -> None:
    runner, _, _, champion, challenger = _runner(tmp_path)
    result = runner.run(
        champion=champion,
        challenger=challenger,
        evaluation_id="strict-four-reports",
    )
    persisted = json.loads(
        Path(result["comparison_ref"]).read_text(encoding="utf-8")
    )
    del persisted["report_refs"]["challenger_shadow"]
    del persisted["report_fingerprints"]["challenger_shadow"]
    forged = _reseal_comparison(persisted)

    with pytest.raises(EvaluationContractError, match="four authenticated reports"):
        validate_authenticated_policy_comparison(
            forged,
            receipt_signing_key=TEST_EVALUATOR_HMAC_KEY,
        )


def test_strict_comparison_validator_recomputes_metrics_and_gate_decision(
    tmp_path: Path,
) -> None:
    runner, _, _, champion, challenger = _runner(tmp_path)
    result = runner.run(
        champion=champion,
        challenger=challenger,
        evaluation_id="strict-recompute",
    )
    persisted = json.loads(
        Path(result["comparison_ref"]).read_text(encoding="utf-8")
    )
    persisted["challenger_metrics"]["true_positives"] += 100
    persisted["promotion_decision"]["reason"] = "FORGED_BUT_RESEALED"
    forged = _reseal_comparison(persisted)

    with pytest.raises(EvaluationContractError, match="rebuild mismatch"):
        validate_authenticated_policy_comparison(
            forged,
            receipt_signing_key=TEST_EVALUATOR_HMAC_KEY,
        )


def test_strict_comparison_validator_binds_policy_version_to_reports(
    tmp_path: Path,
) -> None:
    runner, _, _, champion, challenger = _runner(tmp_path)
    result = runner.run(
        champion=champion,
        challenger=challenger,
        evaluation_id="strict-policy-version",
    )
    persisted = json.loads(
        Path(result["comparison_ref"]).read_text(encoding="utf-8")
    )
    persisted["challenger"]["policy_version"] = "forged-version"
    forged = _reseal_comparison(persisted)

    with pytest.raises(EvaluationContractError, match="policy version mismatch"):
        validate_authenticated_policy_comparison(
            forged,
            receipt_signing_key=TEST_EVALUATOR_HMAC_KEY,
        )


def test_strict_comparison_validator_accepts_retained_historical_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _, _, champion, challenger = _runner(tmp_path)
    result = runner.run(
        champion=champion,
        challenger=challenger,
        evaluation_id="strict-key-rotation",
    )
    persisted = json.loads(
        Path(result["comparison_ref"]).read_text(encoding="utf-8")
    )
    next_key = "policy-runner-next-key-0123456789abcdef"
    old_key_id = hashlib.sha256(
        TEST_EVALUATOR_HMAC_KEY.encode("utf-8")
    ).hexdigest()[:24]
    next_key_id = hashlib.sha256(next_key.encode("utf-8")).hexdigest()[:24]
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEYRING",
        json.dumps(
            {
                "active_key_id": next_key_id,
                "keys": {
                    old_key_id: TEST_EVALUATOR_HMAC_KEY,
                    next_key_id: next_key,
                },
            }
        ),
    )

    validated = validate_authenticated_policy_comparison(persisted)

    assert validated["comparison_fingerprint"] == persisted[
        "comparison_fingerprint"
    ]


def test_runner_binds_each_policy_mainline_authority_before_scan(tmp_path: Path) -> None:
    runner, _, executor, champion, challenger = _runner(tmp_path)

    runner.run(champion=champion, challenger=challenger, evaluation_id="authority-binding")

    assert len(executor.authority_calls) == 20
    assert {
        authority
        for _, policy_id, authority in executor.authority_calls
        if policy_id == champion.policy_id
    } == {"legacy_champion"}
    assert {
        authority
        for _, policy_id, authority in executor.authority_calls
        if policy_id == challenger.policy_id
    } == {"experiment_candidate"}


def test_runner_rejects_scan_contract_from_different_mainline_authority(tmp_path: Path) -> None:
    runner, controller, _, champion, challenger = _runner(
        tmp_path,
        returned_authority_override="legacy_champion",
    )

    with pytest.raises(PolicyEvaluationRunnerError, match="mainline authority mismatch"):
        runner.run(champion=champion, challenger=challenger, evaluation_id="authority-mismatch")

    assert len(controller.cleanup_calls) == 6


def test_runner_fails_closed_when_governed_cleanup_does_not_restore_target(tmp_path: Path) -> None:
    runner, controller, _, champion, challenger = _runner(tmp_path, fail_cleanup_target="held-in")

    with pytest.raises(PolicyEvaluationRunnerError, match="cleanup failed"):
        runner.run(champion=champion, challenger=challenger, evaluation_id="cleanup-failure")

    assert len(controller.cleanup_calls) == 1
    assert challenger.status == "candidate"
    assert len(list((tmp_path / "evaluations" / "cleanup-failure" / "failures").glob("*.json"))) == 1


def test_runner_rejects_shadow_execution_that_publishes_customer_output(tmp_path: Path) -> None:
    runner, controller, executor, champion, challenger = _runner(tmp_path, publish_shadow=True)

    with pytest.raises(PolicyEvaluationRunnerError, match="shadow evaluation must not publish"):
        runner.run(champion=champion, challenger=challenger, evaluation_id="shadow-publish")

    assert any(mode == "shadow" for mode, _, _ in executor.calls)
    assert len(controller.cleanup_calls) == len(executor.calls)
    assert len(list((tmp_path / "evaluations" / "shadow-publish" / "failures").glob("*.json"))) == 1


def test_failure_artifact_redacts_diagnostics_before_persistence(
    tmp_path: Path,
) -> None:
    runner, _, _, champion, challenger = _runner(tmp_path)
    secret = "sk-1234567890abcdefghijkl"

    path = runner._persist_failure(
        evaluation_id="redacted-failure",
        champion=champion,
        challenger=challenger,
        error=RuntimeError(
            f"Authorization: Bearer {secret}; password=do-not-persist"
        ),
    )

    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert secret not in raw
    assert "do-not-persist" not in raw
    assert "REDACTED" in payload["error"]
    assert len(payload["diagnostic_fingerprint"]) == 64


def test_orchestrator_activates_only_the_runner_authenticated_candidate(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    registry_path = tmp_path / "policy-registry.json"
    registry = PolicyRegistry(registry_path)
    champion = registry.get_active()
    assert champion is not None
    challenger_strategy = StrategyBundle()
    challenger_strategy.execution.cleanup_retry_count = 2
    challenger = PolicyRecord(
        policy_id="observed-candidate",
        policy_version=f"{champion.policy_version}+candidate",
        parent_policy_version=champion.policy_version,
        project_scope="global",
        status="candidate",
        created_reason="bounded observed proposal",
        strategy=challenger_strategy,
    )
    registry.register(challenger)
    controller = RecordingFixtureController(manifest_path)
    executor = RecordingScanExecutor(
        manifest_path,
        {champion.policy_id: champion.strategy, challenger.policy_id: challenger.strategy},
    )
    orchestrator = EvolutionOrchestrator.__new__(EvolutionOrchestrator)
    orchestrator.registry = registry

    result = orchestrator.evaluate_and_promote_observed_candidate(
        candidate_policy_id=challenger.policy_id,
        manifest_path=str(manifest_path),
        output_root=str(tmp_path / "evaluations"),
        fixture_controller=controller,
        scan_executor=executor,
        trusted_observation_provider=_trusted_observations,
        evaluation_id="orchestrated-observed",
    )

    assert result["activation_performed"] is True
    assert result["active_policy_id"] == challenger.policy_id
    assert registry.get_active().policy_id == challenger.policy_id
    assert PolicyRegistry(registry_path).get_active().policy_id == challenger.policy_id
