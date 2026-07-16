from __future__ import annotations

"""Build one immutable evaluator-private target manifest from explicit inputs."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark_evaluator.windows_fixture_controller import (  # noqa: E402
    WINDOWS_BENCHMARK_FIXTURE_SCHEMA,
)


def _write_immutable(path: Path, value: dict[str, Any]) -> None:
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != value:
            raise RuntimeError(f"immutable diagnostic artifact conflict: {path}")
        return
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(serialized, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--product-workspace", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--industry", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--environment-type", default="test")
    parser.add_argument("--api-doc", required=True)
    parser.add_argument("--prd", required=True)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--target-root", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    workspace = Path(args.product_workspace).resolve()
    if output_dir == workspace or workspace in output_dir.parents:
        raise RuntimeError("diagnostic manifest must be outside product workspace")
    api_doc = Path(args.api_doc).resolve()
    prd = Path(args.prd).resolve()
    ground_truth = Path(args.ground_truth).resolve()
    target_root = Path(args.target_root).resolve()
    for field, path in (
        ("api_doc", api_doc),
        ("prd", prd),
        ("ground_truth", ground_truth),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{field} not found: {path}")
    if not target_root.is_dir():
        raise FileNotFoundError(f"target_root not found: {target_root}")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "input.json"
    fixture_path = output_dir / "fixture.json"
    context_path = output_dir / "context.json"
    manifest_path = output_dir / "evaluation_manifest.json"
    base_url = str(args.base_url).strip().rstrip("/")
    _write_immutable(input_path, {
        "schema_version": "qualibug.discovery-evaluation-input.v1",
        "project_id": args.project_id,
        "base_url": base_url,
        "api_doc_ref": str(api_doc),
        "prd_ref": str(prd),
        "multi_layer": True,
    })
    _write_immutable(fixture_path, {
        "schema_version": WINDOWS_BENCHMARK_FIXTURE_SCHEMA,
        "project": args.project_id,
        "base_url": base_url,
        "target_root": str(target_root),
    })
    _write_immutable(context_path, {
        "schema_version": "qualibug.discovery-evaluation-context.v1",
        "campaign_context": {
            "scope_id": args.target_id,
            "environment_ref": base_url,
            "target_environment": args.environment_type,
            "execution_mode": "approved_sandbox_write",
        },
    })
    _write_immutable(manifest_path, {
        "schema_version": "qualibug.discovery-evaluation-dataset.v1",
        "dataset_id": args.dataset_id,
        "dataset_version": args.dataset_version,
        "targets": [{
            "target_id": args.target_id,
            "project_id": args.project_id,
            "industry": args.industry,
            "split": "held_in",
            "expectation": "seeded_defects",
            "runtime": {
                "environment_ref": base_url,
                "environment_type": args.environment_type,
                "input_bundle_ref": str(input_path),
                "fixture_snapshot_ref": str(fixture_path),
                "context_artifact_ref": str(context_path),
            },
            "evaluator": {
                "ground_truth_ref": str(ground_truth),
            },
        }],
    })
    print(json.dumps({
        "manifest": str(manifest_path),
        "target_id": args.target_id,
        "project_id": args.project_id,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
