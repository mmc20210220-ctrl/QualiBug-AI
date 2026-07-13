from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ai_test_asset_center.evaluator_receipt_auth import (
    EVALUATOR_HMAC_KEY_ENV,
    EvaluatorReceiptAuthError,
)
from ai_test_asset_center.policy_registry import PolicyRegistry
from tools import run_observed_discovery_policy_evaluation as cli


def test_key_preflight_happens_before_candidate_registry_mutation(
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
            "run_observed_discovery_policy_evaluation.py",
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--output-root",
            str(tmp_path / "evaluations"),
            "--registry",
            str(registry_path),
            "--trusted-observation-root",
            str(tmp_path / "trusted-observations"),
            "--edit-path",
            "execution.cleanup_retry_count",
            "--edit-operation",
            "set_integer",
            "--edit-value",
            "2",
        ],
    )

    with pytest.raises(EvaluatorReceiptAuthError, match="HMAC key missing"):
        cli.main()

    reloaded = PolicyRegistry(registry_path)
    assert set(reloaded._policies) == original_policy_ids
