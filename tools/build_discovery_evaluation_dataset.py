from __future__ import annotations

"""Build a frozen evaluator-private manifest from real benchmark projects.

Two modes:

1. Suite mode (``--suite-root``): projects live under
   ``<suite>/projects/<name>/{input,oracle}`` with OpenAPI, PRD, and
   ``BUG_GROUND_TRUTH.json``.

2. External-path scaffolding (``--external-target``): each target supplies its
   own OpenAPI/PRD/GT paths. Ground-truth paths are written only into the
   evaluator-private ``evaluator.ground_truth_ref`` field and never into
   runtime input/fixture/context artifacts.

Neither mode embeds ground-truth answers into discovery runtime prompts or
context. Production / unknown environment types are rejected by
``load_evaluation_manifest``.
"""

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


def _parse_external_target(value: str) -> dict[str, str]:
    """Parse TARGET_ID|INDUSTRY|SPLIT|EXPECTATION|OPENAPI|PRD|GT_OR_EMPTY|BASE_URL."""
    parts = [item.strip() for item in str(value or "").split("|")]
    if len(parts) != 8:
        raise argparse.ArgumentTypeError(
            "--external-target must be "
            "TARGET_ID|INDUSTRY|SPLIT|EXPECTATION|OPENAPI|PRD|GT_OR_EMPTY|BASE_URL"
        )
    target_id, industry, split, expectation, openapi, prd, gt, base_url = parts
    if not all([target_id, industry, split, expectation, openapi, prd, base_url]):
        raise argparse.ArgumentTypeError("--external-target fields must be non-empty except GT for clean")
    if expectation not in {"seeded_defects", "clean"}:
        raise argparse.ArgumentTypeError("EXPECTATION must be seeded_defects or clean")
    if split not in {"held_in", "held_out"}:
        raise argparse.ArgumentTypeError("SPLIT must be held_in or held_out")
    if expectation == "seeded_defects" and not gt:
        raise argparse.ArgumentTypeError("seeded_defects targets require a ground-truth path")
    if expectation == "clean" and gt:
        raise argparse.ArgumentTypeError("clean targets must leave GT empty")
    return {
        "target_id": target_id,
        "industry": industry,
        "split": split,
        "expectation": expectation,
        "openapi": openapi,
        "prd": prd,
        "ground_truth": gt,
        "base_url": base_url,
    }


def _project_files(suite_root: Path, project_name: str, *, require_ground_truth: bool = True) -> dict[str, Path]:
    project = (suite_root / "projects" / project_name).resolve()
    files = {
        "openapi": project / "input" / "openapi.yaml",
        "prd": project / "input" / "PRD.md",
        "ground_truth": project / "oracle" / "BUG_GROUND_TRUTH.json",
    }
    required = ["openapi", "prd"] + (["ground_truth"] if require_ground_truth else [])
    missing = [f"{key}:{files[key]}" for key in required if not files[key].is_file()]
    if missing:
        raise FileNotFoundError(f"benchmark project {project_name!r} is incomplete: {missing}")
    return files


def _require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def _target(
    *,
    output_root: Path,
    target_id: str,
    project_id: str,
    industry: str,
    split: str,
    expectation: str,
    base_url: str,
    environment_type: str,
    reset_method: str,
    reset_path: str,
    observation_path: str,
    test_accounts: dict[str, Any],
    openapi: Path,
    prd: Path,
    ground_truth: Path | None,
) -> dict[str, Any]:
    artifact_dir = output_root / "runtime" / target_id
    input_path = artifact_dir / "input.json"
    fixture_path = artifact_dir / "fixture.json"
    context_path = artifact_dir / "context.json"
    _write_json(input_path, {
        "schema_version": PRODUCT_SCAN_INPUT_SCHEMA,
        "project_id": project_id,
        "base_url": base_url,
        "api_doc_ref": str(openapi),
        "prd_ref": str(prd),
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
            "scope_id": project_id,
            "environment_ref": base_url,
            "target_environment": environment_type,
            "execution_mode": "approved_sandbox_write",
        },
        "test_accounts": test_accounts,
    })
    evaluator: dict[str, Any] = {}
    if expectation == "seeded_defects":
        if ground_truth is None:
            raise ValueError(f"seeded target {target_id} requires ground_truth")
        evaluator = {"ground_truth_ref": str(ground_truth)}
    return {
        "target_id": target_id,
        "project_id": project_id,
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


def build_dataset_from_suite(args: argparse.Namespace) -> Path:
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

    def suite_target(
        *,
        target_id: str,
        industry: str,
        project_name: str,
        split: str,
        expectation: str,
        base_url: str,
    ) -> dict[str, Any]:
        files = _project_files(
            suite_root,
            project_name,
            require_ground_truth=(expectation == "seeded_defects"),
        )
        return _target(
            output_root=output_root,
            target_id=target_id,
            project_id=f"evaluation-{target_id}",
            industry=industry,
            split=split,
            expectation=expectation,
            base_url=base_url.rstrip("/"),
            environment_type=args.environment_type,
            reset_method=args.reset_method,
            reset_path=args.reset_path,
            observation_path=args.observation_path,
            test_accounts=accounts,
            openapi=files["openapi"],
            prd=files["prd"],
            ground_truth=files["ground_truth"] if expectation == "seeded_defects" else None,
        )

    targets = [
        suite_target(
            target_id="held-in-1",
            industry=held_in[0],
            project_name=held_in[1],
            split="held_in",
            expectation="seeded_defects",
            base_url=args.seeded_base_url,
        )
    ]
    for index, (industry, project_name) in enumerate(held_out, 1):
        targets.append(suite_target(
            target_id=f"held-out-{index}",
            industry=industry,
            project_name=project_name,
            split="held_out",
            expectation="seeded_defects",
            base_url=args.seeded_base_url,
        ))
    targets.append(suite_target(
        target_id="clean-1",
        industry=clean[0],
        project_name=clean[1],
        split="held_out",
        expectation="clean",
        base_url=args.clean_base_url,
    ))
    return _finalize_manifest(args, output_root, targets)


def build_dataset_from_external(args: argparse.Namespace) -> Path:
    output_root = Path(args.output_root).resolve()
    parsed = [_parse_external_target(item) for item in args.external_target]
    industries_held_out = {item["industry"] for item in parsed if item["split"] == "held_out" and item["expectation"] == "seeded_defects"}
    if not any(item["split"] == "held_in" and item["expectation"] == "seeded_defects" for item in parsed):
        raise ValueError("external scaffolding requires at least one held_in seeded_defects target")
    if len(industries_held_out) < 3:
        raise ValueError("external scaffolding requires at least three distinct held_out seeded industries")
    if not any(item["expectation"] == "clean" for item in parsed):
        raise ValueError("external scaffolding requires at least one clean target")

    accounts: dict[str, Any] = {}
    if args.test_accounts_ref:
        accounts_path = Path(args.test_accounts_ref).resolve()
        accounts_raw = json.loads(accounts_path.read_text(encoding="utf-8"))
        if not isinstance(accounts_raw, dict):
            raise ValueError("--test-accounts-ref must contain an object")
        accounts = accounts_raw

    targets: list[dict[str, Any]] = []
    for item in parsed:
        openapi = _require_file(Path(item["openapi"]), f"{item['target_id']} openapi")
        prd = _require_file(Path(item["prd"]), f"{item['target_id']} prd")
        ground_truth = (
            _require_file(Path(item["ground_truth"]), f"{item['target_id']} ground_truth")
            if item["expectation"] == "seeded_defects"
            else None
        )
        targets.append(_target(
            output_root=output_root,
            target_id=item["target_id"],
            project_id=f"evaluation-{item['target_id']}",
            industry=item["industry"],
            split=item["split"],
            expectation=item["expectation"],
            base_url=item["base_url"].rstrip("/"),
            environment_type=args.environment_type,
            reset_method=args.reset_method,
            reset_path=args.reset_path,
            observation_path=args.observation_path,
            test_accounts=accounts,
            openapi=openapi,
            prd=prd,
            ground_truth=ground_truth,
        ))
    return _finalize_manifest(args, output_root, targets)


def _finalize_manifest(args: argparse.Namespace, output_root: Path, targets: list[dict[str, Any]]) -> Path:
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
    # Prove runtime views never carry evaluator GT paths.
    from ai_test_asset_center.discovery_evaluation_contract import build_runtime_view

    for target in manifest.targets:
        runtime_view = build_runtime_view(manifest, target.target_id)
        dumped = json.dumps(runtime_view, ensure_ascii=False)
        if "ground_truth" in dumped.lower() or "evaluator" in dumped.lower():
            raise RuntimeError(f"runtime view leaked evaluator fields for {target.target_id}")
    return manifest_path


def build_dataset(args: argparse.Namespace) -> Path:
    if args.external_target:
        if args.suite_root or args.held_in or args.held_out or args.clean:
            raise ValueError("use either --external-target scaffolding or --suite-root mode, not both")
        return build_dataset_from_external(args)
    if not args.suite_root:
        raise ValueError("--suite-root is required unless --external-target is supplied")
    return build_dataset_from_suite(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", help="benchmark suite root with projects/<name>/{input,oracle}")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--held-in", help="INDUSTRY=PROJECT_DIRECTORY")
    parser.add_argument("--held-out", action="append", help="Repeat INDUSTRY=PROJECT_DIRECTORY")
    parser.add_argument("--clean", help="INDUSTRY=PROJECT_DIRECTORY used against clean target")
    parser.add_argument(
        "--external-target",
        action="append",
        help=(
            "TARGET_ID|INDUSTRY|SPLIT|EXPECTATION|OPENAPI|PRD|GT_OR_EMPTY|BASE_URL "
            "(repeat; GT empty for clean targets)"
        ),
    )
    parser.add_argument("--seeded-base-url", help="required in suite mode")
    parser.add_argument("--clean-base-url", help="required in suite mode")
    parser.add_argument("--environment-type", default="sandbox")
    parser.add_argument("--reset-method", required=True)
    parser.add_argument("--reset-path", required=True)
    parser.add_argument("--observation-path", required=True)
    parser.add_argument("--test-accounts-ref")
    args = parser.parse_args()
    if not args.external_target:
        missing = [
            name for name, value in (
                ("--suite-root", args.suite_root),
                ("--held-in", args.held_in),
                ("--held-out", args.held_out),
                ("--clean", args.clean),
                ("--seeded-base-url", args.seeded_base_url),
                ("--clean-base-url", args.clean_base_url),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"suite mode missing required args: {', '.join(missing)}")
    path = build_dataset(args)
    print(json.dumps({"manifest": str(path), "status": "READY"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
