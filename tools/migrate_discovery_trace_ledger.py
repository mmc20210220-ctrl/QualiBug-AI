"""Explicit offline migration from Discovery Trace Ledger V1 to V2."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from ai_test_asset_center.artifact_redactor import redact_and_validate
from ai_test_asset_center.discovery_trace_ledger import (
    migrate_trace_ledger_v1_to_v2,
)


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate one immutable Discovery Trace Ledger V1 artifact to V2.",
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--obligation-map", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.input.resolve()
    mapping_path = args.obligation_map.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"migration output already exists: {output}")
    v1 = _read_object(source, label="V1 trace ledger")
    raw_mapping = _read_object(mapping_path, label="obligation map")
    obligation_map = {
        str(key).strip(): str(value).strip()
        for key, value in raw_mapping.items()
        if str(key).strip() and str(value).strip()
    }
    if len(obligation_map) != len(raw_mapping):
        raise ValueError("obligation map contains empty identities")
    migrated = migrate_trace_ledger_v1_to_v2(
        v1,
        obligation_map=obligation_map,
    )
    redacted, _redaction_receipt = redact_and_validate(migrated)
    if redacted != migrated:
        raise ValueError("migrated trace ledger still contains redactable material")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
