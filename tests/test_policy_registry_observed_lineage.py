from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import ai_test_asset_center.policy_registry as policy_registry_module
from ai_test_asset_center.policy_registry import (
    OBSERVED_COMPARISON_AUTHENTICATION_FIELD,
    OBSERVED_COMPARISON_FINGERPRINT_FIELD,
    OBSERVED_COMPARISON_SCHEMA,
    PolicyRecord,
    PolicyRegistry,
    PolicyRegistryError,
    StrategyBundle,
    _full_strategy_signature,
)
from ai_test_asset_center.evaluator_receipt_auth import seal_evaluator_artifact


TEST_EVALUATOR_HMAC_KEY = "policy-registry-test-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _evaluator_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY",
        TEST_EVALUATOR_HMAC_KEY,
    )


def _candidate(parent: PolicyRecord) -> PolicyRecord:
    strategy = StrategyBundle()
    strategy.execution.cleanup_retry_count = 2
    return PolicyRecord(
        policy_id="candidate",
        policy_version="v1+candidate",
        parent_policy_version=parent.policy_version,
        project_scope="global",
        status="candidate",
        created_reason="bounded observed proposal",
        strategy=strategy,
    )


def _attach_observed_comparison(root: Path, policy: PolicyRecord) -> None:
    comparison = {
        "schema_version": OBSERVED_COMPARISON_SCHEMA,
        "dataset_manifest_fingerprint": "dataset-fingerprint",
        "challenger": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "strategy_fingerprint": _full_strategy_signature(policy.strategy),
        },
        "observed_execution": True,
        "estimated_metrics_used": False,
        "activation_performed": False,
        "promotion_decision": {
            "promote": True,
            "reason": "PROMOTE_MEASURED_NON_REGRESSIVE_IMPROVEMENT",
        },
    }
    comparison = seal_evaluator_artifact(
        comparison,
        signing_key=TEST_EVALUATOR_HMAC_KEY,
        domain=OBSERVED_COMPARISON_SCHEMA,
        fingerprint_field=OBSERVED_COMPARISON_FINGERPRINT_FIELD,
        authentication_field=OBSERVED_COMPARISON_AUTHENTICATION_FIELD,
    )
    path = root / "comparison.json"
    path.write_text(json.dumps(comparison), encoding="utf-8")
    policy.evaluation_summary = {
        "schema_version": OBSERVED_COMPARISON_SCHEMA,
        "comparison_ref": str(path),
        "comparison_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dataset_manifest_fingerprint": "dataset-fingerprint",
        "strategy_fingerprint": _full_strategy_signature(policy.strategy),
        "observed_execution": True,
        "estimated_metrics_used": False,
        "promote": True,
    }


def test_observed_promotion_persists_lineage_and_rollback_restores_parent_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry = PolicyRegistry(registry_path)
    parent = registry.get_active()
    assert parent is not None
    candidate = _candidate(parent)
    registry.register(candidate)
    _attach_observed_comparison(tmp_path, candidate)
    monkeypatch.setattr(
        policy_registry_module,
        "validate_authenticated_policy_comparison",
        lambda comparison, **_: comparison,
    )

    approved = registry.promote(candidate.policy_id, "observed gate passed")
    assert approved.status == "champion"
    assert registry.get_active() is parent

    activated = registry.promote(candidate.policy_id, "observed gate passed")
    assert activated.status == "active"
    assert registry.get_active() is activated
    assert parent.status == "retired"

    restored = PolicyRegistry(registry_path)
    assert restored.get_active().policy_id == candidate.policy_id
    rolled_back = restored.rollback(candidate.policy_id, "post-promotion regression")
    assert rolled_back.status == "rolled_back"
    assert restored.get_active().policy_id == parent.policy_id
    assert restored._active_policy_id == parent.policy_id

    reloaded = PolicyRegistry(registry_path)
    assert reloaded.get_active().policy_id == parent.policy_id
    event_types = [item["event_type"] for item in reloaded._events]
    assert event_types[-4:] == ["register", "approve_challenger", "activate", "rollback"]


def test_promotion_rejects_strategy_mutation_after_observed_evaluation(tmp_path: Path) -> None:
    registry = PolicyRegistry(tmp_path / "registry.json")
    parent = registry.get_active()
    assert parent is not None
    candidate = _candidate(parent)
    registry.register(candidate)
    _attach_observed_comparison(tmp_path, candidate)
    candidate.strategy.execution.cleanup_retry_count = 3

    with pytest.raises(PolicyRegistryError, match="changed after observed evaluation"):
        registry.promote(candidate.policy_id, "stale evaluation")


def test_promotion_rejects_rehashed_but_unauthenticated_comparison_tamper(
    tmp_path: Path,
) -> None:
    registry = PolicyRegistry(tmp_path / "registry.json")
    parent = registry.get_active()
    assert parent is not None
    candidate = _candidate(parent)
    registry.register(candidate)
    _attach_observed_comparison(tmp_path, candidate)
    comparison_path = Path(candidate.evaluation_summary["comparison_ref"])
    forged = json.loads(comparison_path.read_text(encoding="utf-8"))
    forged["promotion_decision"]["reason"] = "FORGED"
    comparison_path.write_text(json.dumps(forged), encoding="utf-8")
    candidate.evaluation_summary["comparison_file_sha256"] = hashlib.sha256(
        comparison_path.read_bytes()
    ).hexdigest()

    with pytest.raises(PolicyRegistryError, match="authentication"):
        registry.promote(candidate.policy_id, "forged evaluation")


def test_promotion_rejects_signed_minimal_comparison_without_four_reports(
    tmp_path: Path,
) -> None:
    registry = PolicyRegistry(tmp_path / "registry.json")
    parent = registry.get_active()
    assert parent is not None
    candidate = _candidate(parent)
    registry.register(candidate)
    _attach_observed_comparison(tmp_path, candidate)

    with pytest.raises(PolicyRegistryError, match="strict validation"):
        registry.promote(candidate.policy_id, "incomplete evaluation")


def test_corrupt_registry_fails_fast_instead_of_bootstrapping(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(PolicyRegistryError, match="corrupt"):
        PolicyRegistry(path)
