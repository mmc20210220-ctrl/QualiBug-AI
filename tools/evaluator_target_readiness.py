"""Emit redacted evaluator target receipts and enforce serial admission."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_test_asset_center.artifact_redactor import write_json_redacted  # noqa: E402
from ai_test_asset_center.evaluator_target_readiness import (  # noqa: E402
    READINESS_SCHEMA,
    EvaluatorTargetReadinessError,
    assess_serial_target_admission,
    build_target_readiness_receipt,
)


PAIR_RE = re.compile(r"^[^=\r\n]+=[^\r\n]+$")


def _pairs(values: list[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if not PAIR_RE.fullmatch(value):
            raise EvaluatorTargetReadinessError(
                f"{label} must use non-empty NAME=VALUE syntax"
            )
        name, item = value.split("=", 1)
        name = name.strip()
        item = item.strip()
        if not name or not item or name in parsed:
            raise EvaluatorTargetReadinessError(f"invalid or duplicate {label}: {name!r}")
        parsed[name] = item
    return parsed


def _receipt_sequence(path: Path) -> int:
    prefix = path.name.split("-", 1)[0]
    if not prefix.isdigit():
        raise EvaluatorTargetReadinessError(
            f"readiness receipt filename lacks numeric prefix: {path.name}"
        )
    return int(prefix)


def _load_readiness_receipts(root: Path) -> list[dict[str, Any]]:
    receipts: list[tuple[int, dict[str, Any]]] = []
    if not root.exists():
        return []
    for path in root.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise EvaluatorTargetReadinessError(f"receipt must be an object: {path}")
        if payload.get("schema_version") != READINESS_SCHEMA:
            continue
        receipts.append((_receipt_sequence(path), payload))
    receipts.sort(key=lambda item: item[0])
    sequences = [sequence for sequence, _ in receipts]
    if len(sequences) != len(set(sequences)):
        raise EvaluatorTargetReadinessError("duplicate readiness receipt sequence")
    return [payload for _, payload in receipts]


def _safe_target_id(value: str) -> str:
    target = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", target):
        raise EvaluatorTargetReadinessError("target id contains unsupported characters")
    return target


def _emit(args: argparse.Namespace) -> int:
    root = Path(args.receipts_root)
    target = _safe_target_id(args.target_id)
    receipt = build_target_readiness_receipt(
        target_id=target,
        target_role=args.target_role,
        state=args.state,
        previous_state=args.previous_state,
        environment_type=args.environment_type,
        environment_ref=args.environment_ref,
        requested_base_url=args.target_url,
        approved_base_url=args.approved_url or args.target_url,
        checks=_pairs(args.check, "check"),
        fingerprints=_pairs(args.fingerprint, "fingerprint"),
        blocking_codes=args.blocker,
        operator_action=args.operator_action,
    )
    filename = f"{args.sequence:04d}-{target}-{receipt['state'].lower()}.json"
    path = root / filename
    if path.exists():
        raise EvaluatorTargetReadinessError(f"receipt already exists: {path}")
    write_json_redacted(path, receipt)
    print(json.dumps({"status": "written", "path": str(path), "receipt": receipt}, ensure_ascii=False))
    return 0


def _admit(args: argparse.Namespace) -> int:
    receipts = _load_readiness_receipts(Path(args.receipts_root))
    decision = assess_serial_target_admission(receipts, args.requested_target_id)
    print(json.dumps(decision, ensure_ascii=False))
    return 0 if decision["allowed"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit")
    emit.add_argument("--receipts-root", required=True)
    emit.add_argument("--sequence", required=True, type=int)
    emit.add_argument("--target-id", required=True)
    emit.add_argument("--target-role", required=True)
    emit.add_argument("--state", required=True)
    emit.add_argument("--previous-state", required=True)
    emit.add_argument("--environment-type", required=True)
    emit.add_argument("--environment-ref", required=True)
    emit.add_argument("--target-url", required=True)
    emit.add_argument("--approved-url")
    emit.add_argument("--check", action="append", default=[])
    emit.add_argument("--fingerprint", action="append", default=[])
    emit.add_argument("--blocker", action="append", default=[])
    emit.add_argument("--operator-action", default="")
    emit.set_defaults(handler=_emit)

    admit = sub.add_parser("admit")
    admit.add_argument("--receipts-root", required=True)
    admit.add_argument("--requested-target-id", required=True)
    admit.set_defaults(handler=_admit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
