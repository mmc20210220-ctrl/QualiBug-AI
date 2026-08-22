from __future__ import annotations

"""External CLI for private discovery-harness evaluation.

The command runs outside the discovery pipeline. It is the only layer that
loads evaluator-private ground truth, and it never includes ground-truth paths
in runtime views, receipts, or aggregate reports.
"""

import argparse
import json
import os
import re
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
    EVALUATION_RUN_ENVELOPE_SCHEMA,
    RECEIPT_SCHEMA,
    REPORT_SCHEMA,
    aggregate_evaluation_receipts,
    assess_commercial_dataset_shape,
    assess_discovery_goal_status,
    build_runtime_view,
    evaluate_completed_scan,
    load_evaluation_manifest,
    persist_evaluation_receipt,
    persist_evaluation_report,
)
from ai_test_asset_center.evaluator_receipt_auth import (
    resolve_evaluator_hmac_key,
    resolve_evaluator_hmac_keyring,
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


def _resolve_signing_key(args: argparse.Namespace) -> bytes | None:
    """Resolve evaluator trust without copying secrets into product state."""

    key_file = getattr(args, "hmac_key_file", None)
    if key_file is None:
        resolve_evaluator_hmac_keyring()
        return None
    path = Path(key_file).resolve()
    if path == REPOSITORY_ROOT or REPOSITORY_ROOT in path.parents:
        raise ValueError("evaluator HMAC key file must be outside product workspace")
    if not path.is_file():
        raise FileNotFoundError(f"evaluator HMAC key file not found: {path}")
    return resolve_evaluator_hmac_key(path.read_bytes())


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
    signing_key = _resolve_signing_key(args)
    manifest = load_evaluation_manifest(args.manifest)
    envelope = _load_object(Path(args.run_envelope), "run envelope")
    if envelope.get("schema_version") != EVALUATION_RUN_ENVELOPE_SCHEMA:
        raise ValueError(
            "run envelope must be normalized with the current schema before evaluation"
        )
    envelope_policy_id = str(envelope.get("policy_id") or "").strip()
    if envelope_policy_id and envelope_policy_id != args.policy_id:
        raise ValueError(
            "run envelope policy_id does not match evaluator-owned --policy-id"
        )
    scan_result = envelope.get("scan_result")
    if not isinstance(scan_result, dict):
        raise ValueError("run envelope.scan_result must be an object")
    findings = scan_result.get("findings")
    delivery_occurrences = scan_result.get("delivery_occurrences")
    candidates = scan_result.get("candidate_findings")
    if not isinstance(findings, list):
        raise ValueError("run envelope.scan_result.findings must be a list")
    if not isinstance(delivery_occurrences, list):
        raise ValueError(
            "run envelope.scan_result.delivery_occurrences must be a list"
        )
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
    obligation_attempt_ledger = (
        scan_result.get("obligation_attempt_ledger")
        if "obligation_attempt_ledger" in scan_result
        else envelope.get("obligation_attempt_ledger")
    )
    if obligation_attempt_ledger is not None and not isinstance(
        obligation_attempt_ledger,
        dict,
    ):
        raise ValueError(
            "obligation_attempt_ledger must be an object when embedded"
        )
    mainline_run = scan_result.get("mainline_run") or envelope.get(
        "mainline_run"
    )
    if not isinstance(mainline_run, dict):
        raise ValueError("mainline_run must be an object in the run envelope")
    if str(mainline_run.get("policy_version") or "").strip() != (
        args.policy_version
    ):
        raise ValueError(
            "mainline_run policy_version does not match evaluator-owned "
            "--policy-version"
        )
    strategy_fingerprint = str(args.strategy_fingerprint or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", strategy_fingerprint):
        raise ValueError("--strategy-fingerprint must be a SHA-256 hex digest")
    process_boundary = scan_result.get("process_boundary") or envelope.get(
        "process_boundary"
    )
    if process_boundary is not None and not isinstance(process_boundary, dict):
        raise ValueError("process_boundary must be an object when supplied")
    execution_attestation = envelope.get("execution_attestation")
    if execution_attestation is None:
        execution_attestation = scan_result.get("execution_attestation")
    if execution_attestation is not None and not isinstance(
        execution_attestation, dict
    ):
        raise ValueError(
            "execution_attestation must be an object when supplied"
        )
    authority_fields: dict[str, dict[str, Any]] = {}
    for field in (
        "formal_count_projection",
        "formal_delivery_authority",
        "canonical_defect_registry",
        "defect_identity_consistency",
    ):
        value = scan_result.get(field) or envelope.get(field)
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be an object in the run envelope")
        outer = envelope.get(field)
        if outer is not None and outer != value:
            raise ValueError(f"scan_result.{field} does not match envelope")
        authority_fields[field] = value
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
        policy_id=str(args.policy_id or ""),
        evaluation_mode=str(envelope.get("evaluation_mode") or ""),
        findings=findings,
        delivery_occurrences=delivery_occurrences,
        candidates=candidates or [],
        pipeline_health=pipeline_health,
        operational_metrics=operational_metrics,
        fixture_governance=fixture_governance or {},
        trace_ledger=trace_ledger,
        obligation_attempt_ledger=obligation_attempt_ledger,
        mainline_run=mainline_run,
        formal_count_projection=authority_fields["formal_count_projection"],
        formal_delivery_authority=authority_fields[
            "formal_delivery_authority"
        ],
        canonical_defect_registry=authority_fields[
            "canonical_defect_registry"
        ],
        defect_identity_consistency=authority_fields[
            "defect_identity_consistency"
        ],
        evaluator_policy_identity={
            "policy_id": args.policy_id,
            "policy_version": args.policy_version,
            "strategy_fingerprint": strategy_fingerprint,
        },
        process_boundary=process_boundary,
        execution_attestation=execution_attestation,
        receipt_signing_key=signing_key,
    )
    persisted = persist_evaluation_receipt(
        receipt,
        args.output_root,
        receipt_signing_key=signing_key,
    )
    return {"receipt_path": str(persisted), "receipt": receipt}


def _command_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    signing_key = _resolve_signing_key(args)
    manifest = load_evaluation_manifest(args.manifest)
    receipt_paths = [Path(item) for item in (args.receipt or [])]
    if args.receipt_dir:
        receipt_paths.extend(sorted(Path(args.receipt_dir).rglob("*.json")))
    if not receipt_paths:
        raise ValueError("aggregate requires --receipt or --receipt-dir")
    report = aggregate_evaluation_receipts(
        manifest,
        _load_receipts(receipt_paths),
        receipt_signing_key=signing_key,
    )
    persisted = persist_evaluation_report(
        report,
        args.output,
        receipt_signing_key=signing_key,
    )
    return {"report_path": str(persisted), "report": report}


def _command_goal_status(args: argparse.Namespace) -> dict[str, Any]:
    report = None
    signing_key = None
    if args.report:
        signing_key = _resolve_signing_key(args)
        report = _load_object(Path(args.report), "evaluation report")
        if report.get("schema_version") != REPORT_SCHEMA:
            raise ValueError(f"not an evaluation report: {args.report}")
    baseline = args.baseline_cost_per_true_positive_usd
    windows = args.consecutive_non_regressive_windows
    status = assess_discovery_goal_status(
        evaluation_report=report,
        baseline_cost_per_true_positive_usd=baseline,
        consecutive_non_regressive_windows=windows,
        receipt_signing_key=signing_key,
    )
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status_path": str(destination), "status": status}
    return {"status": status}


def _command_bundle_build(args: argparse.Namespace) -> dict[str, Any]:
    from ai_test_asset_center.self_proving_evidence_bundle import (
        BundleError,
        build_self_proving_bundle,
    )

    source = _load_object(Path(args.receipt_json), "receipt-json")
    descriptor = _load_object(Path(args.target_descriptor), "target-descriptor")
    hmac_key = None
    if args.hmac_key_env:
        raw = os.environ.get(args.hmac_key_env, "")
        if not raw:
            return {"ok": False, "reason_code": "bundle_hmac_key_env_missing", "exit_code": 3}
        hmac_key = raw.encode("utf-8")
    try:
        bundle = build_self_proving_bundle(
            reproduction_receipt=source.get("reproduction_receipt") or {},
            hydrated_steps=source.get("hydrated_steps") or [],
            target_descriptor=descriptor,
            hmac_key=hmac_key,
        )
    except BundleError as exc:
        return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail, "exit_code": 3}
    Path(args.out).write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "bundle_id": bundle.get("bundle_id"),
        "out": str(args.out),
        "exit_code": 0,
    }


def _command_verify(args: argparse.Namespace) -> dict[str, Any]:
    from ai_test_asset_center.self_proving_evidence_bundle import verify_self_proving_bundle

    bundle = _load_object(Path(args.bundle), "bundle")
    overrides: dict[str, str] = {}
    for item in args.base_url or []:
        name, separator, url = str(item).partition("=")
        if name.strip() and separator and url.strip():
            overrides[name.strip()] = url.strip()
    hmac_key = None
    if args.hmac_key_env:
        raw = os.environ.get(args.hmac_key_env, "")
        if raw:
            hmac_key = raw.encode("utf-8")
    return verify_self_proving_bundle(
        bundle,
        base_url_overrides=overrides,
        hmac_key=hmac_key,
        perturb_order=bool(args.perturb_order),
        timeout_seconds=float(args.timeout or 10.0),
    )


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
    evaluate_parser.add_argument(
        "--policy-id",
        required=True,
        help="evaluator-owned policy identity; any envelope copy must match",
    )
    evaluate_parser.add_argument(
        "--policy-version",
        required=True,
        help="evaluator-owned policy version; must match the mainline contract",
    )
    evaluate_parser.add_argument(
        "--strategy-fingerprint",
        required=True,
        help="evaluator-owned SHA-256 fingerprint of the full strategy bundle",
    )
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
    evaluate_parser.add_argument(
        "--hmac-key-file",
        type=Path,
        help="evaluator-owned binary HMAC key file outside the product workspace",
    )
    evaluate_parser.set_defaults(handler=_command_evaluate)

    aggregate_parser = subparsers.add_parser("aggregate", help="aggregate one policy/mode receipt set")
    aggregate_parser.add_argument("--manifest", required=True, type=Path)
    aggregate_parser.add_argument("--receipt", action="append", type=Path)
    aggregate_parser.add_argument("--receipt-dir", type=Path)
    aggregate_parser.add_argument("--output", required=True, type=Path)
    aggregate_parser.add_argument(
        "--hmac-key-file",
        type=Path,
        help="evaluator-owned binary HMAC key file outside the product workspace",
    )
    aggregate_parser.set_defaults(handler=_command_aggregate)

    goal_parser = subparsers.add_parser(
        "goal-status",
        help="assess Goal gates A/B/C readiness and absolute D/pilot/GA thresholds",
    )
    goal_parser.add_argument(
        "--report",
        type=Path,
        help="optional MEASURED aggregate evaluation report; omit to report NOT_MEASURED for D/pilot/GA",
    )
    goal_parser.add_argument(
        "--baseline-cost-per-true-positive-usd",
        type=float,
        default=None,
        help="frozen baseline unit cost required for Gate D cost-improvement check",
    )
    goal_parser.add_argument(
        "--consecutive-non-regressive-windows",
        type=int,
        default=None,
        help="count of consecutive frozen non-regressive evaluation windows for GA",
    )
    goal_parser.add_argument(
        "--hmac-key-file",
        type=Path,
        help="evaluator-owned binary HMAC key file outside the product workspace",
    )
    goal_parser.add_argument("--output", type=Path, help="optional path to persist the goal-status JSON")
    goal_parser.set_defaults(handler=_command_goal_status)

    bundle_build_parser = subparsers.add_parser(
        "bundle-build",
        help="compile a sealed reproduction receipt into a self-proving evidence bundle (EVIDENCE_CHAIN_VERIFICATION_SPEC P0)",
    )
    bundle_build_parser.add_argument(
        "--receipt-json",
        type=Path,
        required=True,
        help="JSON object with reproduction_receipt + hydrated_steps (concrete method/path/headers/body per step_id)",
    )
    bundle_build_parser.add_argument(
        "--target-descriptor",
        type=Path,
        required=True,
        help="JSON {environment_type, services:[{name, base_url}]}; non-production environments only",
    )
    bundle_build_parser.add_argument("--out", type=Path, required=True)
    bundle_build_parser.add_argument(
        "--hmac-key-env",
        default="",
        help="env var holding the HMAC key; omit to seal by content digest only",
    )
    bundle_build_parser.set_defaults(handler=_command_bundle_build)

    verify_parser = subparsers.add_parser(
        "verify",
        help="replay a self-proving evidence bundle against a live non-production target",
    )
    verify_parser.add_argument("--bundle", type=Path, required=True)
    verify_parser.add_argument(
        "--base-url",
        action="append",
        default=[],
        help="override a service base URL: --base-url name=http://host:port (repeatable; '*' matches any)",
    )
    verify_parser.add_argument(
        "--hmac-key-env",
        default="",
        help="env var holding the HMAC key; required only when the bundle carries an hmac_sha256 seal",
    )
    verify_parser.add_argument(
        "--perturb-order",
        action="store_true",
        help="deterministically shuffle steps within each phase (control stays before treatment)",
    )
    verify_parser.add_argument("--timeout", type=float, default=10.0)
    verify_parser.set_defaults(handler=_command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = args.handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict):
        return int(result.get("exit_code", 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
