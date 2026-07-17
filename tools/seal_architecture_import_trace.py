#!/usr/bin/env python3
"""Seal evaluator-owned import observations outside the product workspace."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmark_evaluator.architecture_import_trace import (
    ArchitectureImportTraceError,
    seal_architecture_import_trace,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--product-workspace", type=Path, required=True)
    return parser


def _outside(path: Path, workspace: Path, *, field: str) -> Path:
    resolved = path.resolve()
    if resolved == workspace or workspace in resolved.parents:
        raise ArchitectureImportTraceError(
            f"{field}_must_be_outside_product_workspace"
        )
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchitectureImportTraceError(
            f"import_trace_input_invalid:{type(exc).__name__}"
        ) from exc
    if not isinstance(value, dict):
        raise ArchitectureImportTraceError("import_trace_input_not_object")
    return value


def _persist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.product_workspace.resolve()
    try:
        input_path = _outside(args.input, workspace, field="input")
        output_path = _outside(args.output, workspace, field="output")
        key_path = _outside(args.key_file, workspace, field="key_file")
        key = key_path.read_bytes()
        if len(key) < 32:
            raise ArchitectureImportTraceError("key_file_invalid")
        sealed = seal_architecture_import_trace(
            _read_json(input_path),
            signing_key=key,
        )
        _persist(output_path, sealed)
    except (ArchitectureImportTraceError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "qualibug.import-trace-seal-error.v1",
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
    print(
        json.dumps(
            {
                "schema_version": sealed["schema_version"],
                "status": "SEALED",
                "output": str(output_path),
                "trace_fingerprint": sealed["trace_fingerprint"],
                "key_id": sealed["trace_authentication"]["key_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
