#!/usr/bin/env python3
"""Build the non-destructive Python module strangler inventory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ai_test_asset_center.architecture_inventory import (
    ArchitectureInventoryError,
    build_architecture_inventory,
    persist_architecture_inventory,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory Python module reachability and guarded retirement "
            "candidates. This command never deletes source files."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="repository root (default: current QualiBug checkout)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPOSITORY_ROOT / "ai_test_asset_center" / "architecture_roots.json",
        help="versioned architecture-root declaration",
    )
    parser.add_argument(
        "--runtime-trace",
        type=Path,
        help="optional complete/partial Python import trace receipt",
    )
    parser.add_argument(
        "--evaluator-key-file",
        type=Path,
        help=(
            "evaluator HMAC key outside the product workspace; required "
            "to trust a signed runtime trace"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "platform_outputs"
            / "architecture"
            / "discovery_module_inventory.json"
        ),
        help="deterministic JSON inventory output",
    )
    return parser


def _load_external_evaluator_key(
    path: Path | None,
    *,
    product_workspace: Path,
) -> bytes | None:
    if path is None:
        return None
    resolved = path.resolve()
    if resolved == product_workspace or product_workspace in resolved.parents:
        raise ArchitectureInventoryError(
            "evaluator_key_file_must_be_outside_product_workspace"
        )
    try:
        key = resolved.read_bytes()
    except OSError as exc:
        raise ArchitectureInventoryError(
            f"evaluator_key_file_unreadable:{type(exc).__name__}"
        ) from exc
    if len(key) < 32:
        raise ArchitectureInventoryError("evaluator_key_file_invalid")
    return key


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        evaluator_key = _load_external_evaluator_key(
            args.evaluator_key_file,
            product_workspace=root,
        )
        inventory = build_architecture_inventory(
            repo_root=root,
            config_path=args.config.resolve(),
            runtime_trace_path=(
                args.runtime_trace.resolve()
                if args.runtime_trace is not None
                else None
            ),
            runtime_trace_signing_key=evaluator_key,
        )
        output = persist_architecture_inventory(
            inventory,
            args.output.resolve(),
        ).resolve()
    except (ArchitectureInventoryError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "qualibug.architecture-inventory-error.v1",
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    runtime_trace = inventory["runtime_trace"]
    dynamic_uncertainty = inventory["dynamic_import_uncertainty"]
    print(
        json.dumps(
            {
                "schema_version": inventory["schema_version"],
                "quality_claim_status": inventory["quality_claim_status"],
                "external_discovery_quality": inventory[
                    "external_discovery_quality"
                ],
                "auto_delete_performed": inventory["auto_delete_performed"],
                "output": str(output),
                "diagnostics": inventory["diagnostics"],
                "runtime_trace": {
                    "status": runtime_trace["status"],
                    "coverage_status": runtime_trace["coverage_status"],
                    "trusted_for_deletion": runtime_trace["trusted_for_deletion"],
                    "authentication": runtime_trace["authentication"],
                    "covered_root_count": len(runtime_trace["covered_roots"]),
                    "missing_required_root_count": len(
                        runtime_trace["missing_required_roots"]
                    ),
                    "observed_module_count": len(runtime_trace["modules"]),
                    "collector": runtime_trace["collector"],
                },
                "dynamic_import_uncertainty": {
                    "present": dynamic_uncertainty["present"],
                    "reachable_module_count": len(
                        dynamic_uncertainty["reachable_modules"]
                    ),
                    "effect": dynamic_uncertainty["effect"],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
