"""Run the source-backed enterprise-understanding benchmark in two isolated phases.

Phase 1 is a child process that receives only public enterprise sources and builds through the
existing product composition root. Phase 2 starts only after Phase 1 exits successfully; it then
loads evaluator Ground Truth and scores the immutable product asset snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from benchmark_evaluator.scored_run_comparison import _fingerprint, _read_artifact

from .ground_truth import load_ground_truth
from .runner import run_benchmark

SOURCE_BACKED_WORKFLOW_SCHEMA = (
    "qualibug.enterprise-understanding-source-backed-workflow.v1"
)
_SENSITIVE_ENV_MARKERS = (
    "GROUND_TRUTH",
    "ANSWER_KEY",
    "HIDDEN_BUG",
    "EXPECTED_BUG",
)


class SourceBackedWorkflowError(RuntimeError):
    """The isolated product/evaluator workflow could not complete safely."""


def _product_environment(
    environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    source = os.environ if environment is None else environment
    removed: list[str] = []
    clean: dict[str, str] = {}
    for key, value in source.items():
        upper = str(key).upper()
        if any(marker in upper for marker in _SENSITIVE_ENV_MARKERS):
            removed.append(str(key))
            continue
        clean[str(key)] = str(value)
    clean["QUALIBUG_ENTERPRISE_UNDERSTANDING_PRODUCT_PHASE"] = "1"
    clean["QUALIBUG_EVALUATOR_PRIVATE_INPUT_ACCESS_ALLOWED"] = "0"
    return clean, sorted(removed)


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run_source_backed_understanding_workflow(
    *,
    project_id: str,
    product_root: str | Path,
    workspace_root: str | Path,
    source_manifest_path: str | Path,
    ground_truth_path: str | Path,
    output_dir: str | Path,
    process_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build from public sources in a child process, then score in the evaluator process."""
    project = str(project_id or "").strip()
    if not project:
        raise SourceBackedWorkflowError("project_id_required")
    product = Path(product_root).resolve()
    workspace = Path(workspace_root).resolve()
    manifest = Path(source_manifest_path).resolve()
    ground_truth_file = Path(ground_truth_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    workflow_receipt_path = output / "source_backed_workflow_receipt.json"
    asset_path = output / "final_enterprise_understanding_asset.json"
    product_receipt_path = output / "product_phase_receipt.json"
    evaluator_output = output / "evaluation"

    if not product.is_dir():
        raise SourceBackedWorkflowError(f"product_root_missing:{product}")
    if not manifest.is_file():
        raise SourceBackedWorkflowError(f"source_manifest_missing:{manifest}")
    if not ground_truth_file.is_file():
        raise SourceBackedWorkflowError(f"ground_truth_file_missing:{ground_truth_file}")

    command = [
        sys.executable,
        "-m",
        "benchmark_evaluator.enterprise_understanding.build_product_snapshot",
        "--project",
        project,
        "--product-root",
        str(product),
        "--workspace-root",
        str(workspace),
        "--manifest",
        str(manifest),
        "--asset-output",
        str(asset_path),
        "--receipt-output",
        str(product_receipt_path),
    ]
    command_text = "\n".join(command)
    if str(ground_truth_file) in command_text or "ground_truth" in manifest.name.lower():
        raise SourceBackedWorkflowError("ground_truth_path_leaked_into_product_command")
    product_env, removed_env_keys = _product_environment(environment)
    completed = process_runner(
        command,
        cwd=str(product),
        env=product_env,
        check=False,
        capture_output=True,
        text=True,
    )
    base_receipt: dict[str, Any] = {
        "schema_version": SOURCE_BACKED_WORKFLOW_SCHEMA,
        "project_id": project,
        "product_root": str(product),
        "workspace_root": str(workspace),
        "source_manifest_path": str(manifest),
        "source_manifest_fingerprint": _fingerprint(_read_artifact(manifest)),
        "product_phase_command": command,
        "product_phase_command_contains_ground_truth": False,
        "product_phase_environment_removed_sensitive_keys": removed_env_keys,
        "product_phase_ground_truth_access_allowed": False,
        "product_phase_exit_code": int(completed.returncode),
        "product_phase_stdout_tail": str(completed.stdout or "")[-1000:],
        "product_phase_stderr_tail": str(completed.stderr or "")[-1000:],
        "ground_truth_loaded_after_product_phase": False,
        "model_writeback_allowed_by_evaluator": False,
    }
    if completed.returncode != 0:
        base_receipt.update(
            {
                "status": "BLOCKED_PRODUCT_PHASE_FAILED",
                "reason_code": "PRODUCT_PHASE_FAILED_BEFORE_GROUND_TRUTH_LOAD",
                "hidden_ground_truth_entered_product_runtime": False,
            }
        )
        base_receipt["receipt_fingerprint"] = _fingerprint(base_receipt)
        _write_receipt(workflow_receipt_path, base_receipt)
        return base_receipt
    if not asset_path.is_file() or not product_receipt_path.is_file():
        base_receipt.update(
            {
                "status": "BLOCKED_PRODUCT_PHASE_ARTIFACT_MISSING",
                "reason_code": "PRODUCT_PHASE_SUCCEEDED_WITHOUT_REQUIRED_ARTIFACTS",
                "hidden_ground_truth_entered_product_runtime": False,
            }
        )
        base_receipt["receipt_fingerprint"] = _fingerprint(base_receipt)
        _write_receipt(workflow_receipt_path, base_receipt)
        return base_receipt

    ground_truth = load_ground_truth(ground_truth_file)
    product_asset = _read_artifact(asset_path)
    if not isinstance(product_asset, dict):
        raise SourceBackedWorkflowError("product_asset_snapshot_not_object")
    benchmark = run_benchmark(
        ground_truth,
        product_asset,
        output_dir=str(evaluator_output),
    )
    product_phase_receipt = _read_artifact(product_receipt_path)
    benchmark_workflow_receipt = (
        benchmark.get("workflow_receipt")
        if isinstance(benchmark.get("workflow_receipt"), dict)
        else {}
    )
    base_receipt.update(
        {
            "status": str(benchmark.get("status") or "UNKNOWN"),
            "reason_code": "",
            "ground_truth_path": str(ground_truth_file),
            "ground_truth_loaded_after_product_phase": True,
            "ground_truth_fingerprint": benchmark.get("ground_truth_fingerprint"),
            "product_asset_fingerprint": benchmark.get("product_asset_fingerprint"),
            "product_phase_receipt_fingerprint": (
                product_phase_receipt.get("receipt_fingerprint")
                if isinstance(product_phase_receipt, dict)
                else ""
            ),
            "benchmark_result_fingerprint": benchmark.get("result_fingerprint"),
            "next_repair_target": benchmark.get("next_repair_target") or "",
            "next_ingestion_repair_target": benchmark.get(
                "next_ingestion_repair_target"
            )
            or "",
            "document_ground_truth_measurement_status": benchmark_workflow_receipt.get(
                "document_ground_truth_measurement_status"
            ),
            "hidden_ground_truth_entered_product_runtime": False,
            "product_model_can_self_label_true_or_false": False,
            "output_paths": {
                "asset": str(asset_path),
                "product_phase_receipt": str(product_receipt_path),
                "evaluation": str(evaluator_output),
            },
        }
    )
    base_receipt["receipt_fingerprint"] = _fingerprint(base_receipt)
    _write_receipt(workflow_receipt_path, base_receipt)
    return base_receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build one public-source enterprise-understanding asset in an isolated child "
            "process, then evaluate it against Ground Truth."
        )
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = run_source_backed_understanding_workflow(
            project_id=args.project,
            product_root=args.product_root,
            workspace_root=args.workspace_root,
            source_manifest_path=args.manifest,
            ground_truth_path=args.ground_truth,
            output_dir=args.output,
        )
    except SourceBackedWorkflowError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if str(receipt.get("status") or "").startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SOURCE_BACKED_WORKFLOW_SCHEMA",
    "SourceBackedWorkflowError",
    "run_source_backed_understanding_workflow",
]
