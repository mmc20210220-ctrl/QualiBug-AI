#!/usr/bin/env python3
"""Create immutable Phase-1 timing receipts and compare like-for-like runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


TIMING_SCHEMA_VERSION = "qualibug.discovery-phase1-timing.v1"
COMPARISON_SCHEMA_VERSION = "qualibug.discovery-phase1-timing-comparison.v1"
REQUIRED_WARM_RUNS = 5
MINIMUM_P50_IMPROVEMENT_RATIO = 0.60


class TimingReceiptError(ValueError):
    """The timing evidence is incomplete, mutable, or not comparable."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TimingReceiptError(f"{field}_missing")
    return text


def runtime_identity() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": str(Path(sys.executable).resolve()),
    }


def system_identity() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def build_environment_fingerprint(
    runtime: dict[str, Any],
    system: dict[str, Any],
    *,
    tag: str = "",
) -> str:
    if not isinstance(runtime, dict) or not runtime:
        raise TimingReceiptError("runtime_identity_missing")
    if not isinstance(system, dict) or not system:
        raise TimingReceiptError("system_identity_missing")
    return _fingerprint({
        "runtime": runtime,
        "system": system,
        "tag": str(tag or "").strip(),
    })


def _validated_command(command: Sequence[str]) -> list[str]:
    if isinstance(command, (str, bytes)):
        raise TimingReceiptError("timing_command_invalid")
    normalized = [str(item).strip() for item in command]
    if not normalized or any(not item for item in normalized):
        raise TimingReceiptError("timing_command_invalid")
    return normalized


def _validated_samples(samples_ms: Sequence[int]) -> list[int]:
    if len(samples_ms) != REQUIRED_WARM_RUNS:
        raise TimingReceiptError("five_warm_runs_required")
    normalized: list[int] = []
    for value in samples_ms:
        if isinstance(value, bool):
            raise TimingReceiptError("sample_duration_invalid")
        try:
            duration = int(value)
        except (TypeError, ValueError) as exc:
            raise TimingReceiptError("sample_duration_invalid") from exc
        if duration <= 0:
            raise TimingReceiptError("sample_duration_invalid")
        normalized.append(duration)
    return sorted(normalized)


def build_timing_receipt(
    *,
    command: list[str],
    samples_ms: list[int],
    code_fingerprint: str,
    input_fingerprint: str,
    environment_fingerprint: str,
    created_at_utc: str | None = None,
    runtime_identity: dict[str, Any] | None = None,
    system_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one five-sample timing receipt without performing execution."""

    normalized_command = _validated_command(command)
    ordered = _validated_samples(samples_ms)
    code = _required_text(code_fingerprint, field="code_fingerprint")
    input_id = _required_text(input_fingerprint, field="input_fingerprint")
    environment_id = _required_text(
        environment_fingerprint,
        field="environment_fingerprint",
    )
    runtime = dict(runtime_identity or globals()["runtime_identity"]())
    system = dict(system_identity or globals()["system_identity"]())
    if not runtime:
        raise TimingReceiptError("runtime_identity_missing")
    if not system:
        raise TimingReceiptError("system_identity_missing")
    created = str(
        created_at_utc
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ).strip()
    if not created:
        raise TimingReceiptError("created_at_utc_missing")

    body = {
        "schema_version": TIMING_SCHEMA_VERSION,
        "created_at_utc": created,
        "command": normalized_command,
        "command_fingerprint": _fingerprint(normalized_command),
        "code_commit": code,
        "code_fingerprint": code,
        "input_fingerprint": input_id,
        "environment_fingerprint": environment_id,
        "runtime_identity": runtime,
        "runtime_fingerprint": _fingerprint(runtime),
        "system_identity": system,
        "system_fingerprint": _fingerprint(system),
        "warm_run_count": REQUIRED_WARM_RUNS,
        "samples_ms": ordered,
        "p50_ms": ordered[2],
        "p95_ms": ordered[4],
    }
    receipt_fingerprint = _fingerprint(body)
    return {
        **body,
        "receipt_id": f"phase1_timing_{receipt_fingerprint[:24]}",
        "receipt_fingerprint": receipt_fingerprint,
    }


def _validated_receipt(value: dict[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TimingReceiptError(f"{label}_receipt_invalid")
    if value.get("schema_version") != TIMING_SCHEMA_VERSION:
        raise TimingReceiptError(f"{label}_receipt_schema_invalid")
    samples = _validated_samples(value.get("samples_ms") or [])
    if int(value.get("p50_ms") or 0) != samples[2]:
        raise TimingReceiptError(f"{label}_receipt_p50_invalid")
    if int(value.get("p95_ms") or 0) != samples[4]:
        raise TimingReceiptError(f"{label}_receipt_p95_invalid")
    command = _validated_command(value.get("command") or [])
    runtime = value.get("runtime_identity")
    system = value.get("system_identity")
    integrity_valid = bool(
        value.get("warm_run_count") == REQUIRED_WARM_RUNS
        and value.get("command_fingerprint") == _fingerprint(command)
        and isinstance(runtime, dict)
        and value.get("runtime_fingerprint") == _fingerprint(runtime)
        and isinstance(system, dict)
        and value.get("system_fingerprint") == _fingerprint(system)
        and value.get("code_commit") == value.get("code_fingerprint")
    )
    receipt_body = {
        key: item
        for key, item in value.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    expected_fingerprint = _fingerprint(receipt_body)
    integrity_valid = bool(
        integrity_valid
        and value.get("receipt_fingerprint") == expected_fingerprint
        and value.get("receipt_id")
        == f"phase1_timing_{expected_fingerprint[:24]}"
    )
    if not integrity_valid:
        raise TimingReceiptError(f"{label}_receipt_integrity_invalid")
    return value


def compare_timing_receipts(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Compare only receipts produced for the same command, input, and host."""

    baseline_value = _validated_receipt(baseline, label="baseline")
    candidate_value = _validated_receipt(candidate, label="candidate")
    identity_keys = (
        "command_fingerprint",
        "input_fingerprint",
        "environment_fingerprint",
        "runtime_fingerprint",
        "system_fingerprint",
    )
    mismatches = [
        key
        for key in identity_keys
        if baseline_value.get(key) != candidate_value.get(key)
    ]
    if mismatches:
        raise TimingReceiptError(
            "timing_identity_mismatch:" + ",".join(mismatches)
        )
    baseline_p50 = int(baseline_value["p50_ms"])
    candidate_p50 = int(candidate_value["p50_ms"])
    if baseline_p50 <= 0:
        raise TimingReceiptError("baseline_p50_invalid")
    improvement = (baseline_p50 - candidate_p50) / baseline_p50
    body = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "baseline_receipt_id": str(baseline_value.get("receipt_id") or ""),
        "candidate_receipt_id": str(candidate_value.get("receipt_id") or ""),
        "baseline_code_fingerprint": str(
            baseline_value.get("code_fingerprint") or ""
        ),
        "candidate_code_fingerprint": str(
            candidate_value.get("code_fingerprint") or ""
        ),
        "baseline_p50_ms": baseline_p50,
        "candidate_p50_ms": candidate_p50,
        "improvement_ratio": improvement,
        "required_improvement_ratio": MINIMUM_P50_IMPROVEMENT_RATIO,
        "passed": improvement >= MINIMUM_P50_IMPROVEMENT_RATIO,
        "identity": {
            key: baseline_value.get(key) for key in identity_keys
        },
    }
    fingerprint = _fingerprint(body)
    return {
        **body,
        "comparison_id": f"phase1_timing_comparison_{fingerprint[:24]}",
        "comparison_fingerprint": fingerprint,
    }


def write_immutable_json(path: Path | str, payload: dict[str, Any]) -> None:
    """Persist a redacted JSON receipt exactly once; existing paths are errors."""

    from ai_test_asset_center.artifact_redactor import redact_and_validate

    target = Path(path)
    redacted, _ = redact_and_validate(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(redacted, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise TimingReceiptError(f"receipt_path_exists:{target}") from exc
    except Exception as write_error:
        cleanup_error: Exception | None = None
        if target.exists():
            try:
                target.unlink()
            except Exception as exc:  # cleanup failure must remain visible
                cleanup_error = exc
        if cleanup_error is not None:
            raise ExceptionGroup(
                "timing_receipt_write_and_cleanup_failed",
                [write_error, cleanup_error],
            )
        raise


def _git_output(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise TimingReceiptError(
            f"git_identity_failed:{' '.join(args)}:{completed.stderr.strip()[:500]}"
        )
    return completed.stdout.strip()


def clean_code_fingerprint(cwd: Path | str) -> str:
    root = Path(cwd).resolve()
    status = _git_output(root, "status", "--porcelain")
    if status:
        raise TimingReceiptError("working_tree_not_clean")
    return _required_text(
        _git_output(root, "rev-parse", "HEAD"),
        field="code_fingerprint",
    )


def measure_command(
    command: list[str],
    *,
    cwd: Path | str,
    warmup_runs: int = 1,
) -> list[int]:
    """Execute one command without a shell and return five warm durations."""

    normalized_command = _validated_command(command)
    if isinstance(warmup_runs, bool) or int(warmup_runs) < 0:
        raise TimingReceiptError("warmup_runs_invalid")
    root = Path(cwd).resolve()
    samples: list[int] = []
    total_runs = int(warmup_runs) + REQUIRED_WARM_RUNS
    for index in range(total_runs):
        started = time.perf_counter_ns()
        completed = subprocess.run(
            normalized_command,
            cwd=str(root),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        elapsed_ms = max(
            1,
            int(round((time.perf_counter_ns() - started) / 1_000_000)),
        )
        if completed.returncode != 0:
            phase = "warmup" if index < int(warmup_runs) else "sample"
            raise TimingReceiptError(
                f"timed_command_failed:{phase}:{index + 1}:"
                f"exit_{completed.returncode}:{completed.stderr.strip()[-1000:]}"
            )
        if index >= int(warmup_runs):
            samples.append(elapsed_ms)
    return samples


def _read_json(path: Path | str) -> dict[str, Any]:
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TimingReceiptError(f"receipt_read_failed:{target}:{exc}") from exc
    if not isinstance(value, dict):
        raise TimingReceiptError(f"receipt_not_object:{target}")
    return value


def _measure_command(args: argparse.Namespace) -> dict[str, Any]:
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    command = _validated_command(command)
    root = Path(args.cwd or ".").resolve()
    runtime = runtime_identity()
    system = system_identity()
    environment = build_environment_fingerprint(
        runtime,
        system,
        tag=args.environment_tag,
    )
    code_fingerprint = clean_code_fingerprint(root)
    samples = measure_command(
        command,
        cwd=root,
        warmup_runs=args.warmup_runs,
    )
    if clean_code_fingerprint(root) != code_fingerprint:
        raise TimingReceiptError("code_fingerprint_changed_during_measurement")
    receipt = build_timing_receipt(
        command=command,
        samples_ms=samples,
        code_fingerprint=code_fingerprint,
        input_fingerprint=args.input_fingerprint,
        environment_fingerprint=environment,
        runtime_identity=runtime,
        system_identity=system,
    )
    write_immutable_json(args.output, receipt)
    return receipt


def _compare_command(args: argparse.Namespace) -> dict[str, Any]:
    comparison = compare_timing_receipts(
        _read_json(args.baseline),
        _read_json(args.candidate),
    )
    if args.output:
        write_immutable_json(args.output, comparison)
    return comparison


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and compare immutable discovery Phase-1 timing receipts."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    measure = subparsers.add_parser(
        "measure",
        help="run an exact command once for warmup and five times for evidence",
    )
    measure.add_argument("--output", required=True)
    measure.add_argument("--input-fingerprint", required=True)
    measure.add_argument("--environment-tag", default="")
    measure.add_argument("--warmup-runs", type=int, default=1)
    measure.add_argument("--cwd", default=".")
    measure.add_argument("command", nargs=argparse.REMAINDER)
    measure.set_defaults(handler=_measure_command)

    compare = subparsers.add_parser(
        "compare",
        help="compare like-for-like baseline and candidate receipts",
    )
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    compare.add_argument("--output")
    compare.set_defaults(handler=_compare_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = args.handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
