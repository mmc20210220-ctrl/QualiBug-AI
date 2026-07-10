from __future__ import annotations

"""Build a frozen evaluator-private manifest from real benchmark projects."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_test_asset_center.discovery_evaluation_contract import (  # noqa: E402
    MANIFEST_SCHEMA,
    assess_commercial_dataset_shape,
    load_evaluation_manifest,
)
from ai_test_asset_center.evaluation_fixture_controller import HTTP_FIXTURE_SCHEMA  # noqa: E402
from ai_test_asset_center.observed_product_scan_executor import (  # noqa: E402
    PRODUCT_SCAN_CONTEXT_SCHEMA,
    PRODUCT_SCAN_INPUT_SCHEMA,
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mapping(value: str, field: str) -> tuple[str, str]:
    industry, separator, project = str(value or "").partition("=")
    industry, project = industry.strip(), project.strip()
    if separator != "=" or not industry or not project:
        raise argparse.ArgumentTypeError(f"{field} must use INDUSTRY=PROJECT_DIRECTORY")
    return industry, project


def _project_files(suite_root: Path, project_name: str) -> dict[str, Path]:
    project = (suite_root / "projects" / project_name).resolve()
    files = {
        "openapi": project / "input" / "openapi.yaml",
        "prd": project / "input" / "PRD.md",
        "ground_truth": project / "oracle" / "BUG_GROUND_TRUTH.json",
    }
    missing = [f"{key}:{path}" for key, path in files.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"benchmark project {project_name!r} is incomplete: {missing}")
    return files


def _target(
    *,
    output_root: Path,
    suite_root: Path,
    target_id: str,
    project_name: str,
    industry: str,
    split: str,
    expectation: str,
    base_url: str,
    environment_type: str,
    reset_method: str,
    reset_path: str,
    observation_path: str,
    test_accounts: dict[str, Any],
) -> dict[str, Any]:
    files = _project_files(suite_root, project_name)
    artifact_dir = output_root / "runtime" / target_id
    input_path = artifact_dir / "input.json"
    fixture_path = artifact_dir / "fixture.json"
    context_path = artifact_dir / "context.json"
    _write_json(input_path, {
        "schema_version": PRODUCT_SCAN_INPUT_SCHEMA,
        "project_id": f"evaluation-{target_id}",
        "base_url": base_url,
        "api_doc_ref": str(files["openapi"]),
        "prd_ref": str(files["prd"]),
        "multi_layer": True,
    })
    _write_json(fixture_path, {
        "schema_version": HTTP_FIXTURE_SCHEMA,
        "base_url": base_url,
        "reset": {"method": reset_method, "path": reset_path, "body": {}},
        "observation_path": observation_path,
        "clean_state_assertions": {"record_count": 0},
        "actor_identity": "evaluation-fixture-controller",
    })
    _write_json(context_path, {
        "schema_version": PRODUCT_SCAN_CONTEXT_SCHEMA,
        "campaign_context": {
            "scope_id": f"evaluation-{target_id}",
            "environment_ref": base_url,
            "target_environment": environment_type,
            "execution_mode": "approved_sandbox_write",
        },
        "test_accounts": test_accounts,
    })
    evaluator = {"ground_truth_ref": str(files["ground_truth"])} if expectation == "seeded_defects" else {}
    return {
        "target_id": target_id,
        "project_id": f"evaluation-{target_id}",
        "industry": industry,
        "split": split,
        "expectation": expectation,
        "runtime": {
            "environment_ref": base_url,
            "environment_type": environment_type,
            "input_bundle_ref": str(input_path),
            "fixture_snapshot_ref": str(fixture_path),
            "context_artifact_ref": str(context_path),
        },
        "evaluator": evaluator,
    }


def build_dataset(args: argparse.Namespace) -> Path:
    suite_root = Path(args.suite_root).resolve()
    output_root = Path(args.output_root).resolve()
    held_in = _mapping(args.held_in, "--held-in")
    held_out = [_mapping(item, "--held-out") for item in args.held_out]
    clean = _mapping(args.clean, "--clean")
    if len({industry for industry, _ in held_out}) < 3:
        raise ValueError("at least three distinct --held-out industries are required")
    accounts: dict[str, Any] = {}
    if args.test_accounts_ref:
        accounts_path = Path(args.test_accounts_ref).resolve()
        accounts_raw = json.loads(accounts_path.read_text(encoding="utf-8"))
        if not isinstance(accounts_raw, dict):
            raise ValueError("--test-accounts-ref must contain an object")
        accounts = accounts_raw

    targets = [
        _target(
            output_root=output_root,
            suite_root=suite_root,
            target_id="held-in-1",
            project_name=held_in[1],
            industry=held_in[0],
            split="held_in",
            expectation="seeded_defects",
            base_url=args.seeded_base_url.rstrip("/"),
            environment_type=args.environment_type,
            reset_method=args.reset_method,
            reset_path=args.reset_path,
            observation_path=args.observation_path,
            test_accounts=accounts,
        )
    ]
    for index, (industry, project_name) in enumerate(held_out, 1):
        targets.append(_target(
            output_root=output_root,
            suite_root=suite_root,
            target_id=f"held-out-{index}",
            project_name=project_name,
            industry=industry,
            split="held_out",
            expectation="seeded_defects",
            base_url=args.seeded_base_url.rstrip("/"),
            environment_type=args.environment_type,
            reset_method=args.reset_method,
            reset_path=args.reset_path,
            observation_path=args.observation_path,
            test_accounts=accounts,
        ))
    targets.append(_target(
        output_root=output_root,
        suite_root=suite_root,
        target_id="clean-1",
        project_name=clean[1],
        industry=clean[0],
        split="held_out",
        expectation="clean",
        base_url=args.clean_base_url.rstrip("/"),
        environment_type=args.environment_type,
        reset_method=args.reset_method,
        reset_path=args.reset_path,
        observation_path=args.observation_path,
        test_accounts=accounts,
    ))
    manifest_path = output_root / "evaluation_manifest.json"
    _write_json(manifest_path, {
        "schema_version": MANIFEST_SCHEMA,
        "dataset_id": args.dataset_id,
        "dataset_version": args.dataset_version,
        "targets": targets,
    })
    manifest = load_evaluation_manifest(manifest_path)
    shape = assess_commercial_dataset_shape(manifest)
    if shape.get("commercial_shape_ready") is not True:
        raise RuntimeError(f"generated dataset failed commercial shape gate: {shape}")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--held-in", required=True, help="INDUSTRY=PROJECT_DIRECTORY")
    parser.add_argument("--held-out", action="append", required=True, help="Repeat INDUSTRY=PROJECT_DIRECTORY")
    parser.add_argument("--clean", required=True, help="INDUSTRY=PROJECT_DIRECTORY used against clean target")
    parser.add_argument("--seeded-base-url", required=True)
    parser.add_argument("--clean-base-url", required=True)
    parser.add_argument("--environment-type", default="sandbox")
    parser.add_argument("--reset-method", required=True)
    parser.add_argument("--reset-path", required=True)
    parser.add_argument("--observation-path", required=True)
    parser.add_argument("--test-accounts-ref")
    args = parser.parse_args()
    path = build_dataset(args)
    print(json.dumps({"manifest": str(path), "status": "READY"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
