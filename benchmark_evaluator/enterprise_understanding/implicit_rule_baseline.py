"""Run the frozen implicit-rule corpus through two real product builds.

Both product phases run in one isolated subprocess and receive only public source
manifests. Evaluator Ground Truth is loaded only after v1 ingestion/build, v2
supersession/build, source identity validation and final asset capture complete.
No threshold is claimed from this small contract corpus; it establishes measurable
candidate, promotion, lifecycle and execution-bridge metrics.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from benchmark_evaluator.scored_run_comparison import _fingerprint, _read_artifact

from .build_product_snapshot import SOURCE_MANIFEST_SCHEMA, _git_blob_sha
from .implicit_rules import evaluate_implicit_rules, load_implicit_rule_ground_truth
from .run_source_backed_workflow import _product_environment

PROJECT_ID = "implicit_rules_v1"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / PROJECT_ID
SOURCE_ONE_RELATIVE_PATH = (
    "benchmark_evaluator/enterprise_understanding/fixtures/"
    f"{PROJECT_ID}/source_v1.md"
)
SOURCE_TWO_RELATIVE_PATH = (
    "benchmark_evaluator/enterprise_understanding/fixtures/"
    f"{PROJECT_ID}/source_v2.md"
)
GROUND_TRUTH_RELATIVE_PATH = (
    "benchmark_evaluator/enterprise_understanding/fixtures/"
    f"{PROJECT_ID}/ground_truth.json"
)
STABLE_SOURCE_REF = "online-docs/implicit-rules/business-rules.md"
BASELINE_SCHEMA = "qualibug.enterprise-understanding-implicit-rule-baseline.v1"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _source_manifest(
    *,
    product_root: Path,
    source_relative_path: str,
) -> dict[str, Any]:
    source = product_root / source_relative_path
    if not source.is_file():
        raise FileNotFoundError(f"frozen implicit-rule source missing: {source}")
    return {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "project_id": PROJECT_ID,
        "sources": [
            {
                "path": source_relative_path,
                "source_ref": STABLE_SOURCE_REF,
                "source_type": "business_rules",
                "blob_sha": _git_blob_sha(source.read_bytes()),
            }
        ],
        "excluded_from_product_phase": [GROUND_TRUTH_RELATIVE_PATH],
        "product_phase_may_load_ground_truth": False,
        "fixture_source_is_frozen": True,
        "manifest_generated_from_frozen_source_bytes": True,
        "stable_source_ref_separate_from_fixture_path": True,
    }


def _blocked(
    output: Path,
    *,
    reason_code: str,
    product_exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    summary = {
        "schema": BASELINE_SCHEMA,
        "project_id": PROJECT_ID,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "measurement_status": "NOT_MEASURED",
        "product_phase_exit_code": product_exit_code,
        "product_phase_stdout_tail": stdout[-1000:],
        "product_phase_stderr_tail": stderr[-1000:],
        "ground_truth_loaded_after_product_phase": False,
        "ground_truth_entered_product_runtime": False,
        "product_model_can_self_label_true_or_false": False,
    }
    _write_json(output / "implicit_rule_baseline_summary.json", summary)
    return summary


def run_implicit_rule_baseline(
    *,
    product_root: str | Path,
    workspace_root: str | Path,
    output_dir: str | Path,
    process_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    product = Path(product_root).resolve()
    workspace = Path(workspace_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_one = output / "source_manifest_v1.json"
    manifest_two = output / "source_manifest_v2.json"
    product_receipt_path = output / "versioned_product_phase_receipt.json"
    phase_one_asset_path = output / "phase_one_enterprise_understanding_asset.json"
    final_asset_path = output / "final_enterprise_understanding_asset.json"
    evaluation_dir = output / "evaluation"

    _write_json(
        manifest_one,
        _source_manifest(
            product_root=product,
            source_relative_path=SOURCE_ONE_RELATIVE_PATH,
        ),
    )
    _write_json(
        manifest_two,
        _source_manifest(
            product_root=product,
            source_relative_path=SOURCE_TWO_RELATIVE_PATH,
        ),
    )

    command = [
        sys.executable,
        "-m",
        "benchmark_evaluator.enterprise_understanding.build_versioned_product_snapshot",
        "--project",
        PROJECT_ID,
        "--product-root",
        str(product),
        "--workspace-root",
        str(workspace),
        "--phase-one-manifest",
        str(manifest_one),
        "--phase-two-manifest",
        str(manifest_two),
        "--phase-one-asset-output",
        str(phase_one_asset_path),
        "--final-asset-output",
        str(final_asset_path),
        "--receipt-output",
        str(product_receipt_path),
    ]
    command_text = "\n".join(command).lower()
    if "ground_truth" in command_text:
        raise RuntimeError("ground_truth_path_leaked_into_versioned_product_command")
    product_env, removed_env_keys = _product_environment(environment)
    completed = process_runner(
        command,
        cwd=str(product),
        env=product_env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return _blocked(
            output,
            reason_code="VERSIONED_PRODUCT_PHASE_FAILED_BEFORE_GROUND_TRUTH_LOAD",
            product_exit_code=int(completed.returncode),
            stdout=str(completed.stdout or ""),
            stderr=str(completed.stderr or ""),
        )
    if not product_receipt_path.is_file() or not final_asset_path.is_file():
        return _blocked(
            output,
            reason_code="VERSIONED_PRODUCT_PHASE_ARTIFACT_MISSING",
            product_exit_code=int(completed.returncode),
            stdout=str(completed.stdout or ""),
            stderr=str(completed.stderr or ""),
        )

    product_receipt = _read_artifact(product_receipt_path)
    product_asset = _read_artifact(final_asset_path)
    if not isinstance(product_receipt, dict) or product_receipt.get("status") != "PASS":
        return _blocked(
            output,
            reason_code="VERSIONED_PRODUCT_PHASE_RECEIPT_NOT_PASS",
            product_exit_code=int(completed.returncode),
        )
    if not isinstance(product_asset, dict):
        return _blocked(
            output,
            reason_code="VERSIONED_PRODUCT_ASSET_NOT_OBJECT",
            product_exit_code=int(completed.returncode),
        )
    if product_receipt.get("ground_truth_loaded") is not False:
        return _blocked(
            output,
            reason_code="PRODUCT_PHASE_GROUND_TRUTH_BOUNDARY_VIOLATED",
            product_exit_code=int(completed.returncode),
        )
    transitions = product_receipt.get("source_version_transitions")
    if not isinstance(transitions, list) or not transitions:
        return _blocked(
            output,
            reason_code="SOURCE_VERSION_TRANSITION_RECEIPT_MISSING",
            product_exit_code=int(completed.returncode),
        )

    ground_truth_path = product / GROUND_TRUTH_RELATIVE_PATH
    ground_truth = load_implicit_rule_ground_truth(ground_truth_path)
    measurement = evaluate_implicit_rules(ground_truth, product_asset)
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    _write_json(evaluation_dir / "implicit_rule_measurement.json", measurement)
    _write_json(
        evaluation_dir / "implicit_rule_metrics.json",
        measurement.get("metrics") or {},
    )
    _write_json(
        evaluation_dir / "implicit_rule_alignments.json",
        measurement.get("alignments") or [],
    )
    _write_json(
        evaluation_dir / "implicit_rule_false_promotions.json",
        measurement.get("false_promotions") or [],
    )
    _write_json(
        evaluation_dir / "implicit_rule_missed_rules.json",
        measurement.get("missed_rules") or [],
    )
    _write_json(
        evaluation_dir / "implicit_rule_lifecycle_errors.json",
        measurement.get("lifecycle_errors") or [],
    )
    _write_json(
        evaluation_dir / "implicit_rule_execution_bridge_gaps.json",
        measurement.get("execution_bridge_gaps") or [],
    )

    metrics = measurement.get("metrics") if isinstance(measurement.get("metrics"), dict) else {}
    summary = {
        "schema": BASELINE_SCHEMA,
        "project_id": PROJECT_ID,
        "status": (
            "MEASURED" if measurement.get("status") == "MEASURED" else "NOT_MEASURED"
        ),
        "measurement_status": measurement.get("status"),
        "measurement_reason_code": measurement.get("reason_code"),
        "metrics": metrics,
        "next_repair_target": measurement.get("next_repair_target") or "",
        "source_version_transitions": transitions,
        "phase_two_governance_carry_forward_receipt": product_receipt.get(
            "phase_two_governance_carry_forward_receipt"
        )
        or {},
        "product_phase_exit_code": int(completed.returncode),
        "product_phase_environment_removed_sensitive_keys": removed_env_keys,
        "product_phase_command_contains_ground_truth": False,
        "ground_truth_loaded_after_product_phase": True,
        "ground_truth_fingerprint": _fingerprint(ground_truth),
        "product_asset_fingerprint": _fingerprint(product_asset),
        "versioned_product_receipt_fingerprint": product_receipt.get(
            "receipt_fingerprint"
        ),
        "ground_truth_entered_product_runtime": False,
        "product_model_can_self_label_true_or_false": False,
        "fuzzy_or_llm_alignment_used": False,
        "quality_scope": (
            "FROZEN_CONTRACT_CORPUS_ONLY_NOT_131_BUG_RECALL_OR_INDUSTRY_GENERALIZATION"
        ),
        "threshold_gate_applied": False,
        "output_paths": {
            "phase_one_asset": str(phase_one_asset_path),
            "final_asset": str(final_asset_path),
            "product_receipt": str(product_receipt_path),
            "measurement": str(evaluation_dir / "implicit_rule_measurement.json"),
        },
    }
    summary["summary_fingerprint"] = _fingerprint(summary)
    _write_json(output / "implicit_rule_baseline_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen two-version implicit-rule baseline."
    )
    default_product_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--product-root", default=str(default_product_root))
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = run_implicit_rule_baseline(
        product_root=args.product_root,
        workspace_root=args.workspace_root,
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASELINE_SCHEMA",
    "PROJECT_ID",
    "STABLE_SOURCE_REF",
    "run_implicit_rule_baseline",
]
