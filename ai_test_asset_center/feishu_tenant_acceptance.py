"""Repeatable real-tenant acceptance for the managed Feishu connector.

This module is an operator-facing acceptance authority. It invokes the existing managed,
fenced and read-only synchronization path, projects only bounded metrics from its receipts,
and persists a credential-free admission report. It never reads source bytes from the
knowledge registry and always uses the RETAIN internal lifecycle policy during acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_auto_sync import (
    run_managed_feishu_sync,
    test_managed_feishu_connection,
)
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import _now, _redact_text, _write_json
from .real_project_onboarding import _safe_project_id

FEISHU_TENANT_ACCEPTANCE_SCHEMA = "qualibug.feishu-tenant-acceptance.v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")

ACCEPTANCE_PROFILES: dict[str, dict[str, float | int]] = {
    "smoke": {
        "runs": 2,
        "min_discovered_resources": 1,
        "min_coverage_ratio": 0.80,
        "max_unsupported_ratio": 0.20,
        "max_run_duration_seconds": 300.0,
    },
    "pilot": {
        "runs": 2,
        "min_discovered_resources": 20,
        "min_coverage_ratio": 0.95,
        "max_unsupported_ratio": 0.05,
        "max_run_duration_seconds": 900.0,
    },
    "enterprise": {
        "runs": 3,
        "min_discovered_resources": 200,
        "min_coverage_ratio": 0.98,
        "max_unsupported_ratio": 0.02,
        "max_run_duration_seconds": 1800.0,
    },
}

ConnectionTester = Callable[..., Mapping[str, Any]]
SyncRunner = Callable[..., Mapping[str, Any]]


class FeishuTenantAcceptanceError(RuntimeError):
    """The acceptance request or report could not be produced safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _connector_id(value: Any) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise FeishuTenantAcceptanceError("connector_instance_id_invalid")
    return result


def _bounded_int(value: Any, *, minimum: int, maximum: int, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise FeishuTenantAcceptanceError(f"{field}_invalid") from exc
    if not minimum <= parsed <= maximum:
        raise FeishuTenantAcceptanceError(f"{field}_out_of_range")
    return parsed


def _bounded_float(value: Any, *, minimum: float, maximum: float, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FeishuTenantAcceptanceError(f"{field}_invalid") from exc
    if not minimum <= parsed <= maximum:
        raise FeishuTenantAcceptanceError(f"{field}_out_of_range")
    return parsed


def _thresholds(
    profile: str,
    *,
    runs: int | None,
    min_discovered_resources: int | None,
    min_coverage_ratio: float | None,
    max_unsupported_ratio: float | None,
    max_run_duration_seconds: float | None,
) -> dict[str, float | int]:
    name = _text(profile, 40).lower() or "pilot"
    if name not in ACCEPTANCE_PROFILES:
        raise FeishuTenantAcceptanceError("acceptance_profile_invalid")
    result = dict(ACCEPTANCE_PROFILES[name])
    if runs is not None:
        result["runs"] = _bounded_int(
            runs, minimum=2, maximum=10, field="acceptance_runs"
        )
    if min_discovered_resources is not None:
        result["min_discovered_resources"] = _bounded_int(
            min_discovered_resources,
            minimum=0,
            maximum=1_000_000,
            field="min_discovered_resources",
        )
    if min_coverage_ratio is not None:
        result["min_coverage_ratio"] = _bounded_float(
            min_coverage_ratio,
            minimum=0.0,
            maximum=1.0,
            field="min_coverage_ratio",
        )
    if max_unsupported_ratio is not None:
        result["max_unsupported_ratio"] = _bounded_float(
            max_unsupported_ratio,
            minimum=0.0,
            maximum=1.0,
            field="max_unsupported_ratio",
        )
    if max_run_duration_seconds is not None:
        result["max_run_duration_seconds"] = _bounded_float(
            max_run_duration_seconds,
            minimum=1.0,
            maximum=24 * 60 * 60,
            field="max_run_duration_seconds",
        )
    return result


def _hash(value: Any) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _project_run(run: Mapping[str, Any], duration_seconds: float) -> dict[str, Any]:
    discovered = _integer(run.get("discovered_resource_count"))
    materialized = _integer(run.get("materialized_resource_count"))
    unchanged = _integer(run.get("unchanged_resource_count"))
    unsupported = _integer(run.get("unsupported_resource_count"))
    covered = _integer(run.get("covered_resource_count"), materialized + unchanged)
    ratio = _number(
        run.get("knowledge_coverage_ratio"),
        covered / discovered if discovered else 1.0,
    )
    next_cursor = _text(run.get("next_cursor"), 2000)
    return {
        "sync_epoch_id": _text(run.get("sync_epoch_id"), 160),
        "status": _text(run.get("status"), 40),
        "duration_seconds": round(max(0.0, duration_seconds), 6),
        "discovered_resource_count": discovered,
        "covered_resource_count": covered,
        "materialized_resource_count": materialized,
        "unchanged_resource_count": unchanged,
        "unsupported_resource_count": unsupported,
        "unknown_gap_count": _integer(run.get("unknown_gap_count")),
        "failure_count": _integer(run.get("failure_count")),
        "degraded_resource_count": _integer(run.get("degraded_resource_count")),
        "export_avoided_count": _integer(run.get("export_avoided_count")),
        "knowledge_coverage_ratio": ratio,
        "knowledge_coverage_status": _text(
            run.get("knowledge_coverage_status"), 80
        ),
        "remote_discovery_complete": run.get("remote_discovery_complete") is True,
        "supported_materialization_complete": (
            run.get("supported_materialization_complete") is True
        ),
        "cursor_checkpoint_committed": run.get("cursor_checkpoint_committed") is True,
        "checkpoint_commit_protocol": _text(
            run.get("checkpoint_commit_protocol"), 80
        ),
        "customer_material_mutation_executed": (
            run.get("customer_material_mutation_executed") is True
        ),
        "source_content_persisted_in_adapter_receipt": (
            run.get("source_content_persisted_in_adapter_receipt") is True
        ),
        "next_cursor_fingerprint": _hash(next_cursor),
        "run_receipt_path": _text(run.get("run_receipt_path"), 1000),
    }


def _check(
    check_id: str,
    passed: bool,
    *,
    observed: Any,
    expected: Any,
    detail: str = "",
    severity: str = "BLOCKER",
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "PASS" if passed else "FAIL",
        "severity": severity,
        "observed": observed,
        "expected": expected,
        "detail": _text(detail, 500),
    }


def _evaluate(
    connection: Mapping[str, Any],
    runs: list[dict[str, Any]],
    thresholds: Mapping[str, float | int],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "CONNECTION_AVAILABLE",
            connection.get("status") == "AVAILABLE",
            observed=_text(connection.get("status"), 80),
            expected="AVAILABLE",
        )
    )
    checks.append(
        _check(
            "REMOTE_ACCESS_READ_ONLY",
            connection.get("network_side_effect") == "READ_ONLY",
            observed=_text(connection.get("network_side_effect"), 80),
            expected="READ_ONLY",
            detail="Authentication and scope validation must not mutate customer material.",
        )
    )

    for index, run in enumerate(runs, start=1):
        prefix = f"RUN_{index}"
        checks.extend(
            [
                _check(
                    f"{prefix}_COMPLETE",
                    run["status"] == "COMPLETE",
                    observed=run["status"],
                    expected="COMPLETE",
                ),
                _check(
                    f"{prefix}_DISCOVERY_COMPLETE",
                    run["remote_discovery_complete"] is True,
                    observed=run["remote_discovery_complete"],
                    expected=True,
                ),
                _check(
                    f"{prefix}_SUPPORTED_MATERIALIZATION_COMPLETE",
                    run["supported_materialization_complete"] is True,
                    observed=run["supported_materialization_complete"],
                    expected=True,
                ),
                _check(
                    f"{prefix}_NO_UNKNOWN_GAPS",
                    run["unknown_gap_count"] == 0,
                    observed=run["unknown_gap_count"],
                    expected=0,
                ),
                _check(
                    f"{prefix}_NO_FAILURES",
                    run["failure_count"] == 0,
                    observed=run["failure_count"],
                    expected=0,
                ),
                _check(
                    f"{prefix}_CHECKPOINT_COMMITTED",
                    run["cursor_checkpoint_committed"] is True,
                    observed=run["cursor_checkpoint_committed"],
                    expected=True,
                ),
                _check(
                    f"{prefix}_NO_CUSTOMER_MUTATION",
                    run["customer_material_mutation_executed"] is False,
                    observed=run["customer_material_mutation_executed"],
                    expected=False,
                ),
                _check(
                    f"{prefix}_NO_SOURCE_CONTENT_IN_ACCEPTANCE_RECEIPT",
                    run["source_content_persisted_in_adapter_receipt"] is False,
                    observed=run["source_content_persisted_in_adapter_receipt"],
                    expected=False,
                ),
                _check(
                    f"{prefix}_DURATION_WITHIN_LIMIT",
                    run["duration_seconds"]
                    <= float(thresholds["max_run_duration_seconds"]),
                    observed=run["duration_seconds"],
                    expected=f"<= {thresholds['max_run_duration_seconds']}",
                ),
            ]
        )

    if runs:
        baseline = runs[0]
        discovered = baseline["discovered_resource_count"]
        unsupported_ratio = (
            baseline["unsupported_resource_count"] / discovered if discovered else 0.0
        )
        checks.extend(
            [
                _check(
                    "TENANT_SCALE_MEETS_PROFILE",
                    discovered >= int(thresholds["min_discovered_resources"]),
                    observed=discovered,
                    expected=f">= {thresholds['min_discovered_resources']}",
                ),
                _check(
                    "KNOWLEDGE_COVERAGE_MEETS_PROFILE",
                    min(run["knowledge_coverage_ratio"] for run in runs)
                    >= float(thresholds["min_coverage_ratio"]),
                    observed=min(run["knowledge_coverage_ratio"] for run in runs),
                    expected=f">= {thresholds['min_coverage_ratio']}",
                ),
                _check(
                    "UNSUPPORTED_RATIO_WITHIN_PROFILE",
                    unsupported_ratio
                    <= float(thresholds["max_unsupported_ratio"]),
                    observed=unsupported_ratio,
                    expected=f"<= {thresholds['max_unsupported_ratio']}",
                ),
            ]
        )

    for index in range(1, len(runs)):
        previous = runs[index - 1]
        current = runs[index]
        stable_snapshot = bool(previous["next_cursor_fingerprint"]) and (
            previous["next_cursor_fingerprint"] == current["next_cursor_fingerprint"]
        )
        if stable_snapshot:
            checks.append(
                _check(
                    f"RUN_{index + 1}_STABLE_SNAPSHOT_NOT_REEXPORTED",
                    current["materialized_resource_count"] == 0
                    and current["export_avoided_count"]
                    >= current["covered_resource_count"],
                    observed={
                        "materialized_resource_count": current[
                            "materialized_resource_count"
                        ],
                        "export_avoided_count": current["export_avoided_count"],
                        "covered_resource_count": current["covered_resource_count"],
                    },
                    expected={
                        "materialized_resource_count": 0,
                        "export_avoided_count": ">= covered_resource_count",
                    },
                    detail="An unchanged remote snapshot must reuse existing occurrences.",
                )
            )
        else:
            checks.append(
                _check(
                    f"RUN_{index + 1}_REMOTE_CHANGE_OBSERVED",
                    True,
                    observed="REMOTE_SNAPSHOT_CHANGED",
                    expected="REPEATABLE_OR_EXPLAINED",
                    detail=(
                        "The remote cursor changed during acceptance, so no-change export "
                        "avoidance was not asserted."
                    ),
                    severity="INFO",
                )
            )
    return checks


def _report_path(project: str, connector: str, root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report_id = uuid.uuid4().hex[:12]
    return (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_acceptance_reports"
        / connector
        / f"{stamp}_{report_id}.json"
    )


def run_feishu_tenant_acceptance(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    profile: str = "pilot",
    runs: int | None = None,
    min_discovered_resources: int | None = None,
    min_coverage_ratio: float | None = None,
    max_unsupported_ratio: float | None = None,
    max_run_duration_seconds: float | None = None,
    max_nodes: int = 100_000,
    max_export_polls: int = 40,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    timeout: float = 30.0,
    actor: dict[str, Any] | None = None,
    connection_tester: ConnectionTester = test_managed_feishu_connection,
    sync_runner: SyncRunner = run_managed_feishu_sync,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run repeated real-tenant synchronization and persist a bounded admission report."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    profile_name = _text(profile, 40).lower() or "pilot"
    limits = _thresholds(
        profile_name,
        runs=runs,
        min_discovered_resources=min_discovered_resources,
        min_coverage_ratio=min_coverage_ratio,
        max_unsupported_ratio=max_unsupported_ratio,
        max_run_duration_seconds=max_run_duration_seconds,
    )
    max_nodes_value = _bounded_int(
        max_nodes, minimum=1, maximum=1_000_000, field="max_nodes"
    )
    max_polls_value = _bounded_int(
        max_export_polls, minimum=1, maximum=500, field="max_export_polls"
    )
    timeout_value = _bounded_float(
        timeout, minimum=1.0, maximum=300.0, field="timeout"
    )
    poll_interval = _bounded_float(
        export_poll_interval,
        minimum=0.0,
        maximum=10.0,
        field="export_poll_interval",
    )
    clean_actor = dict(
        actor or {"name": "qualibug_tenant_acceptance", "role": "knowledge_admin"}
    )

    started_at = _now()
    connection: dict[str, Any] = {}
    projected_runs: list[dict[str, Any]] = []
    execution_error: dict[str, str] | None = None
    try:
        connection = dict(
            connection_tester(
                project,
                connector,
                root=resolved_root,
                timeout=timeout_value,
                sleeper=sleeper,
            )
        )
        for _ in range(int(limits["runs"])):
            started = clock()
            raw_run = dict(
                sync_runner(
                    project,
                    connector,
                    root=resolved_root,
                    actor=clean_actor,
                    deletion_policy="RETAIN",
                    max_nodes=max_nodes_value,
                    max_export_polls=max_polls_value,
                    export_poll_interval=poll_interval,
                    allow_raw_text_fallback=allow_raw_text_fallback,
                    timeout=timeout_value,
                    sleeper=sleeper,
                )
            )
            projected_runs.append(_project_run(raw_run, clock() - started))
            if raw_run.get("status") != "COMPLETE":
                break
    except Exception as exc:
        execution_error = {
            "type": type(exc).__name__,
            "detail": _redact_text(str(exc), 500),
        }

    checks = _evaluate(connection, projected_runs, limits)
    if execution_error is not None:
        checks.append(
            _check(
                "ACCEPTANCE_EXECUTION_COMPLETED",
                False,
                observed=execution_error["type"],
                expected="NO_EXCEPTION",
                detail=execution_error["detail"],
            )
        )
    elif len(projected_runs) != int(limits["runs"]):
        checks.append(
            _check(
                "ACCEPTANCE_REQUIRED_RUNS_COMPLETED",
                False,
                observed=len(projected_runs),
                expected=int(limits["runs"]),
            )
        )
    else:
        checks.append(
            _check(
                "ACCEPTANCE_REQUIRED_RUNS_COMPLETED",
                True,
                observed=len(projected_runs),
                expected=int(limits["runs"]),
            )
        )

    blocker_failures = [
        row
        for row in checks
        if row["severity"] == "BLOCKER" and row["status"] == "FAIL"
    ]
    verdict = "PASS" if not blocker_failures else "FAIL"
    durations = [float(row["duration_seconds"]) for row in projected_runs]
    report = {
        "schema": FEISHU_TENANT_ACCEPTANCE_SCHEMA,
        "acceptance_id": "fta_" + uuid.uuid4().hex[:24],
        "project_id": project,
        "connector_instance_id": connector,
        "profile": profile_name,
        "verdict": verdict,
        "acceptance_ready": verdict == "PASS",
        "started_at_utc": started_at,
        "completed_at_utc": _now(),
        "thresholds": limits,
        "connection": {
            "status": _text(connection.get("status"), 80),
            "connector_type": _text(connection.get("connector_type"), 80),
            "auth_mode": _text(connection.get("auth_mode"), 80),
            "space_count": _integer(connection.get("space_count")),
            "network_side_effect": _text(
                connection.get("network_side_effect"), 80
            ),
            "credentials_persisted": connection.get("credentials_persisted") is True,
            "access_token_persisted": connection.get("access_token_persisted") is True,
        },
        "runs": projected_runs,
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "blocker_failure_count": len(blocker_failures),
            "executed_run_count": len(projected_runs),
            "required_run_count": int(limits["runs"]),
            "maximum_run_duration_seconds": max(durations, default=0.0),
            "minimum_coverage_ratio": min(
                (float(row["knowledge_coverage_ratio"]) for row in projected_runs),
                default=0.0,
            ),
            "maximum_discovered_resource_count": max(
                (int(row["discovered_resource_count"]) for row in projected_runs),
                default=0,
            ),
        },
        "execution_error": execution_error,
        "governance": {
            "customer_material_access": "NON_MUTATING_READ_ONLY",
            "customer_material_mutation_executed": False,
            "deletion_policy": "RETAIN",
            "customer_source_content_in_report": False,
            "raw_cursor_values_in_report": False,
            "credential_values_in_report": False,
            "source_occurrence_content_loaded_by_acceptance": False,
            "existing_managed_sync_authority_reused": True,
        },
    }
    path = _report_path(project, connector, resolved_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, report)
    report["report_path"] = str(path.relative_to(resolved_root)).replace("\\", "/")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run read-only real-tenant acceptance for a configured Feishu connector."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--connector", required=True)
    parser.add_argument(
        "--profile",
        choices=sorted(ACCEPTANCE_PROFILES),
        default="pilot",
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--runs", type=int)
    parser.add_argument("--min-discovered-resources", type=int)
    parser.add_argument("--min-coverage-ratio", type=float)
    parser.add_argument("--max-unsupported-ratio", type=float)
    parser.add_argument("--max-run-duration-seconds", type=float)
    parser.add_argument("--max-nodes", type=int, default=100_000)
    parser.add_argument("--max-export-polls", type=int, default=40)
    parser.add_argument("--export-poll-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--allow-raw-text-fallback", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_feishu_tenant_acceptance(
        args.project,
        args.connector,
        root=Path(args.root),
        profile=args.profile,
        runs=args.runs,
        min_discovered_resources=args.min_discovered_resources,
        min_coverage_ratio=args.min_coverage_ratio,
        max_unsupported_ratio=args.max_unsupported_ratio,
        max_run_duration_seconds=args.max_run_duration_seconds,
        max_nodes=args.max_nodes,
        max_export_polls=args.max_export_polls,
        export_poll_interval=args.export_poll_interval,
        allow_raw_text_fallback=args.allow_raw_text_fallback,
        timeout=args.timeout,
    )
    json.dump(
        {
            "verdict": report["verdict"],
            "acceptance_ready": report["acceptance_ready"],
            "report_path": report["report_path"],
            "summary": report["summary"],
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0 if report["acceptance_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCEPTANCE_PROFILES",
    "FEISHU_TENANT_ACCEPTANCE_SCHEMA",
    "FeishuTenantAcceptanceError",
    "run_feishu_tenant_acceptance",
]
