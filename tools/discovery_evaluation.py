from __future__ import annotations

"""External CLI for private discovery-harness evaluation.

The command runs outside the discovery pipeline. It is the only layer that
loads evaluator-private ground truth, and it never includes ground-truth paths
in runtime views, receipts, or aggregate reports.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ``python tools/discovery_evaluation.py`` sets sys.path to ``tools``. Resolve
# the repository root from this file so the command behaves the same in a
# source checkout and an installed environment.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ai_test_asset_center.discovery_evaluation_contract import (
    RECEIPT_SCHEMA,
    aggregate_evaluation_receipts,
    assess_commercial_dataset_shape,
    build_runtime_view,
    evaluate_completed_scan,
    load_evaluation_manifest,
    persist_evaluation_receipt,
    persist_evaluation_report,
)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object: {path}")
    return value


def _load_receipts(paths: list[Path]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in paths:
        value = _load_object(path, "evaluation receipt")
        if value.get("schema_version") != RECEIPT_SCHEMA:
            raise ValueError(f"not an evaluation receipt: {path}")
        receipts.append(value)
    return receipts


def _command_inspect(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_evaluation_manifest(args.manifest)
    return {
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "manifest_fingerprint": manifest.manifest_fingerprint,
        "commercial_shape": assess_commercial_dataset_shape(manifest),
        "runtime_views": [build_runtime_view(manifest, item.target_id) for item in manifest.targets],
    }


def _command_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_evaluation_manifest(args.manifest)
    envelope = _load_object(Path(args.run_envelope), "run envelope")
    scan_result = envelope.get("scan_result")
    if not isinstance(scan_result, dict):
        raise ValueError("run envelope.scan_result must be an object")
    findings = scan_result.get("findings")
    candidates = scan_result.get("candidate_findings")
    if not isinstance(findings, list):
        raise ValueError("run envelope.scan_result.findings must be a list")
    if candidates is not None and not isinstance(candidates, list):
        raise ValueError("run envelope.scan_result.candidate_findings must be a list when present")
    pipeline_health = envelope.get("pipeline_health")
    operational_metrics = envelope.get("operational_metrics")
    fixture_governance = envelope.get("fixture_governance")
    if not isinstance(pipeline_health, dict):
        raise ValueError("run envelope.pipeline_health must be an object")
    if not isinstance(operational_metrics, dict):
        raise ValueError("run envelope.operational_metrics must be an object")
    if fixture_governance is not None and not isinstance(fixture_governance, dict):
        raise ValueError("run envelope.fixture_governance must be an object when present")
    trace_ledger: dict[str, Any] | None = None
    if args.trace_ledger:
        trace_ledger = _load_object(Path(args.trace_ledger), "discovery trace ledger")
    else:
        embedded_trace = scan_result.get("trace_ledger") or envelope.get("trace_ledger")
        if embedded_trace is not None:
            if not isinstance(embedded_trace, dict):
                raise ValueError("trace_ledger must be an object when embedded")
            trace_ledger = embedded_trace

    receipt = evaluate_completed_scan(
        manifest,
        args.target_id,
        run_id=str(envelope.get("run_id") or ""),
        policy_id=str(envelope.get("policy_id") or ""),
        evaluation_mode=str(envelope.get("evaluation_mode") or ""),
        findings=findings,
        candidates=candidates or [],
        pipeline_health=pipeline_health,
        operational_metrics=operational_metrics,
        fixture_governance=fixture_governance or {},
        trace_ledger=trace_ledger,
    )
    persisted = persist_evaluation_receipt(receipt, args.output_root)
    return {"receipt_path": str(persisted), "receipt": receipt}


def _command_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_evaluation_manifest(args.manifest)
    receipt_paths = [Path(item) for item in (args.receipt or [])]
    if args.receipt_dir:
        receipt_paths.extend(sorted(Path(args.receipt_dir).rglob("*.json")))
    if not receipt_paths:
        raise ValueError("aggregate requires --receipt or --receipt-dir")
    report = aggregate_evaluation_receipts(manifest, _load_receipts(receipt_paths))
    persisted = persist_evaluation_report(report, args.output)
    return {"report_path": str(persisted), "report": report}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluator-private, hidden-ground-truth discovery quality measurement"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="validate and print discovery-safe runtime views")
    inspect_parser.add_argument("--manifest", required=True, type=Path)
    inspect_parser.set_defaults(handler=_command_inspect)

    evaluate_parser = subparsers.add_parser("evaluate", help="score one completed run and persist its receipt")
    evaluate_parser.add_argument("--manifest", required=True, type=Path)
    evaluate_parser.add_argument("--target-id", required=True)
    evaluate_parser.add_argument("--run-envelope", required=True, type=Path)
    evaluate_parser.add_argument(
        "--trace-ledger",
        type=Path,
        help=(
            "optional redacted discovery trace ledger for evaluator-private "
            "per-Bug first-loss diagnostics"
        ),
    )
    evaluate_parser.add_argument("--output-root", required=True, type=Path)
    evaluate_parser.set_defaults(handler=_command_evaluate)

    aggregate_parser = subparsers.add_parser("aggregate", help="aggregate one policy/mode receipt set")
    aggregate_parser.add_argument("--manifest", required=True, type=Path)
    aggregate_parser.add_argument("--receipt", action="append", type=Path)
    aggregate_parser.add_argument("--receipt-dir", type=Path)
    aggregate_parser.add_argument("--output", required=True, type=Path)
    aggregate_parser.set_defaults(handler=_command_aggregate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = args.handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
