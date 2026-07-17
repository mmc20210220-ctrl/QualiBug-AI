#!/usr/bin/env python3
"""Collect one evaluator-owned import-trace root from a real command."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmark_evaluator.architecture_import_trace_collector import (
    ArchitectureImportTraceCollectionError,
    collect_architecture_import_trace,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--root-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--product-workspace", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        summary = collect_architecture_import_trace(
            inventory_path=args.inventory,
            root_id=str(args.root_id).strip(),
            output_path=args.output,
            product_workspace=args.product_workspace,
            command=command,
        )
    except (ArchitectureImportTraceCollectionError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "qualibug.import-trace-collection-error.v1",
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
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
