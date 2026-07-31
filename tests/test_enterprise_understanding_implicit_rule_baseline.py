from __future__ import annotations

import json
import subprocess
from pathlib import Path

from benchmark_evaluator.enterprise_understanding.implicit_rule_baseline import (
    run_implicit_rule_baseline,
)


ROOT = Path(__file__).resolve().parents[1]


def _argument(command: list[str], name: str) -> Path:
    return Path(command[command.index(name) + 1])


def test_baseline_loads_ground_truth_only_after_versioned_product_phase(tmp_path):
    seen = {}

    def product_runner(command, *, cwd, env, check, capture_output, text):
        seen["command"] = list(command)
        seen["cwd"] = cwd
        seen["env"] = dict(env)
        assert check is False
        assert capture_output is True
        assert text is True
        assert "ground_truth" not in "\n".join(command).lower()

        receipt_path = _argument(command, "--receipt-output")
        final_asset_path = _argument(command, "--final-asset-output")
        phase_one_asset_path = _argument(command, "--phase-one-asset-output")
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        phase_one_asset_path.write_text(
            json.dumps({"asset_id": "asset:v1"}), encoding="utf-8"
        )
        final_asset_path.write_text(
            json.dumps(
                {
                    "asset_id": "asset:v2",
                    "implicit_rule_candidates": [],
                    "rule_library": [],
                    "implicit_rule_lifecycle_ledger": {"items": []},
                    "relationships": [],
                    "oracle_library": [],
                }
            ),
            encoding="utf-8",
        )
        receipt_path.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "ground_truth_loaded": False,
                    "receipt_fingerprint": "versioned-product-receipt",
                    "source_version_transitions": [
                        {
                            "source_ref": "online-docs/implicit-rules/business-rules.md",
                            "phase_one_version": 1,
                            "phase_two_version": 2,
                        }
                    ],
                    "phase_two_governance_carry_forward_receipt": {
                        "captured_before_base_rebuild": True,
                        "prior_rule_library_reused": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="PASS", stderr="")

    output = tmp_path / "output"
    result = run_implicit_rule_baseline(
        product_root=ROOT,
        workspace_root=tmp_path / "workspace",
        output_dir=output,
        process_runner=product_runner,
        environment={
            "PATH": "/usr/bin",
            "HIDDEN_BUG_ANSWER_KEY": "must-not-enter-product",
            "GROUND_TRUTH_TOKEN": "must-not-enter-product",
        },
    )

    assert result["status"] == "MEASURED"
    assert result["measurement_status"] == "MEASURED"
    assert result["product_phase_command_contains_ground_truth"] is False
    assert result["ground_truth_loaded_after_product_phase"] is True
    assert result["ground_truth_entered_product_runtime"] is False
    assert result["product_model_can_self_label_true_or_false"] is False
    assert result["threshold_gate_applied"] is False
    assert result["quality_scope"] == (
        "FROZEN_CONTRACT_CORPUS_ONLY_NOT_131_BUG_RECALL_OR_INDUSTRY_GENERALIZATION"
    )
    assert set(result["product_phase_environment_removed_sensitive_keys"]) == {
        "GROUND_TRUTH_TOKEN",
        "HIDDEN_BUG_ANSWER_KEY",
    }
    assert "GROUND_TRUTH_TOKEN" not in seen["env"]
    assert "HIDDEN_BUG_ANSWER_KEY" not in seen["env"]
    assert (output / "source_manifest_v1.json").is_file()
    assert (output / "source_manifest_v2.json").is_file()
    assert (output / "evaluation" / "implicit_rule_measurement.json").is_file()
    assert (output / "implicit_rule_baseline_summary.json").is_file()


def test_product_failure_blocks_before_ground_truth_load(tmp_path):
    def product_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="failed")

    output = tmp_path / "output"
    result = run_implicit_rule_baseline(
        product_root=ROOT,
        workspace_root=tmp_path / "workspace",
        output_dir=output,
        process_runner=product_runner,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == (
        "VERSIONED_PRODUCT_PHASE_FAILED_BEFORE_GROUND_TRUTH_LOAD"
    )
    assert result["measurement_status"] == "NOT_MEASURED"
    assert result["ground_truth_loaded_after_product_phase"] is False
    assert result["ground_truth_entered_product_runtime"] is False
    assert not (output / "evaluation" / "implicit_rule_measurement.json").exists()
