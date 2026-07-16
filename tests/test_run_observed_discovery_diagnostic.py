from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center.evaluator_receipt_auth import (
    EVALUATOR_HMAC_KEY_ENV,
    EvaluatorReceiptAuthError,
)
from ai_test_asset_center.policy_registry import PolicyRegistry
from tools import run_observed_discovery_diagnostic as cli


def test_diagnostic_key_preflight_happens_before_registry_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "policy-registry.json"
    registry = PolicyRegistry(registry_path)
    original_policy_ids = set(registry._policies)
    monkeypatch.delenv(EVALUATOR_HMAC_KEY_ENV, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_observed_discovery_diagnostic.py",
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--target-id",
            "TARGET-1",
            "--output-root",
            str(tmp_path / "evaluations"),
            "--registry",
            str(registry_path),
            "--trusted-observation-root",
            str(tmp_path / "trusted-observations"),
        ],
    )

    with pytest.raises(EvaluatorReceiptAuthError, match="HMAC key missing"):
        cli.main()

    reloaded = PolicyRegistry(registry_path)
    assert set(reloaded._policies) == original_policy_ids


def test_diagnostic_cli_runs_active_policy_without_registry_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry_path = tmp_path / "policy-registry.json"
    registry = PolicyRegistry(registry_path)
    original_policy_ids = set(registry._policies)
    captured: dict[str, Any] = {}

    class Store:
        def __init__(self, root: Path, **kwargs: object) -> None:
            captured["store_root"] = root
            captured["store_kwargs"] = kwargs

    class Gateway:
        def __init__(self, **kwargs: object) -> None:
            captured["gateway_kwargs"] = kwargs

    class Runner:
        def __init__(self, manifest: Path, **kwargs: object) -> None:
            captured["manifest"] = manifest
            captured["runner_kwargs"] = kwargs

        def run_target_diagnostic(self, **kwargs: object) -> dict[str, object]:
            captured["diagnostic_kwargs"] = kwargs
            return {
                "schema_version": "qualibug.discovery-evaluation-report.v1",
                "dataset_id": "dataset-1",
                "policy_id": "policy-baseline-001",
                "evaluation_mode": "replay",
                "claim_status": "NOT_MEASURED",
                "commercial_promotion_evidence_ready": False,
                "evaluated_target_count": 1,
                "not_measured_targets": [{"target_id": "TARGET-1"}],
                "held_in": {"true_positives": 0},
                "held_out": {"true_positives": 0},
                "pipeline_degraded_target_count": 0,
            }

    monkeypatch.setattr(cli, "resolve_evaluator_hmac_key", lambda: b"test-key")
    monkeypatch.setattr(cli, "TrustedObservationStore", Store)
    monkeypatch.setattr(cli, "EvaluatorHttpObservationGateway", Gateway)
    monkeypatch.setattr(cli, "DiscoveryPolicyEvaluationRunner", Runner)
    monkeypatch.setattr(cli, "GovernedHttpResetFixtureController", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        cli,
        "WindowsBenchmarkFixtureController",
        lambda **kwargs: {"kind": "windows-benchmark", **kwargs},
    )
    monkeypatch.setattr(cli, "ObservedProductScanExecutor", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_observed_discovery_diagnostic.py",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--target-id",
            "TARGET-1",
            "--output-root",
            str(tmp_path / "evaluations"),
            "--registry",
            str(registry_path),
            "--trusted-observation-root",
            str(tmp_path / "trusted-observations"),
            "--evaluation-id",
            "diag-1",
            "--fixture-controller",
            "windows-benchmark",
        ],
    )

    assert cli.main() == 0

    reloaded = PolicyRegistry(registry_path)
    assert set(reloaded._policies) == original_policy_ids
    assert captured["runner_kwargs"]["require_commercial_shape"] is False
    assert captured["runner_kwargs"]["fixture_controller"]["kind"] == (
        "windows-benchmark"
    )
    assert captured["diagnostic_kwargs"]["target_id"] == "TARGET-1"
    assert captured["diagnostic_kwargs"]["evaluation_id"] == "diag-1"
    assert captured["diagnostic_kwargs"]["policy"].policy_id == "policy-baseline-001"
    summary = json.loads(capsys.readouterr().out)
    assert summary["diagnostic_only"] is True
    assert summary["commercial_promotion_evidence_ready"] is False
