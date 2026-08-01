"""Read-only real-tenant acceptance for registry-managed connectors.

The authority reuses the fenced managed sync path, records only bounded metrics and hashes,
and fails closed whenever a required safety or completeness claim is absent. Acceptance always
uses the internal RETAIN lifecycle policy and never reads source bytes from the knowledge store.
The historical Feishu entrypoint remains a compatibility wrapper around the generic contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_auto_sync import (
    run_managed_connector_sync,
    run_managed_feishu_sync,
    test_managed_connector_connection,
    test_managed_feishu_connection,
)
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import _now, _write_json
from .real_project_onboarding import _safe_project_id

FEISHU_TENANT_ACCEPTANCE_SCHEMA = "qualibug.feishu-tenant-acceptance.v1"
CONNECTOR_TENANT_ACCEPTANCE_SCHEMA = "qualibug.connector-tenant-acceptance.v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_READ_ONLY_NETWORK_SIDE_EFFECTS = frozenset({"READ_ONLY", "READ_ONLY_GET"})

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
    """The acceptance request could not be evaluated safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _connector_id(value: Any) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise FeishuTenantAcceptanceError("connector_instance_id_invalid")
    return result


def _bounded_int(value: Any, minimum: int, maximum: int, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise FeishuTenantAcceptanceError(f"{field}_invalid") from exc
    if not minimum <= parsed <= maximum:
        raise FeishuTenantAcceptanceError(f"{field}_out_of_range")
    return parsed


def _bounded_float(value: Any, minimum: float, maximum: float, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FeishuTenantAcceptanceError(f"{field}_invalid") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
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
        result["runs"] = _bounded_int(runs, 2, 10, "acceptance_runs")
    if min_discovered_resources is not None:
        result["min_discovered_resources"] = _bounded_int(
            min_discovered_resources, 0, 1_000_000, "min_discovered_resources"
        )
    if min_coverage_ratio is not None:
        result["min_coverage_ratio"] = _bounded_float(
            min_coverage_ratio, 0.0, 1.0, "min_coverage_ratio"
        )
    if max_unsupported_ratio is not None:
        result["max_unsupported_ratio"] = _bounded_float(
            max_unsupported_ratio, 0.0, 1.0, "max_unsupported_ratio"
        )
    if max_run_duration_seconds is not None:
        result["max_run_duration_seconds"] = _bounded_float(
            max_run_duration_seconds,
            1.0,
            24 * 60 * 60,
            "max_run_duration_seconds",
        )
    return result


def _hash(value: Any) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _explicit_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _project_run(run: Mapping[str, Any], duration_seconds: float) -> dict[str, Any]:
    discovered = _integer(run.get("discovered_resource_count"))
    materialized = _integer(run.get("materialized_resource_count"))
    unchanged = _integer(run.get("unchanged_resource_count"))
    unsupported = _integer(run.get("unsupported_resource_count"))
    covered = _integer(run.get("covered_resource_count"), materialized + unchanged)
    default_ratio = covered / discovered if discovered else 1.0
    return {
        "sync_epoch_fingerprint": _hash(run.get("sync_epoch_id")),
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
        "knowledge_coverage_ratio": _number(
            run.get("knowledge_coverage_ratio"), default_ratio
        ),
        "knowledge_coverage_status": _text(
            run.get("knowledge_coverage_status"), 80
        ),
        "remote_discovery_complete": _explicit_bool(
            run.get("remote_discovery_complete")
        ),
        "supported_materialization_complete": _explicit_bool(
            run.get("supported_materialization_complete")
        ),
        "cursor_checkpoint_committed": _explicit_bool(
            run.get("cursor_checkpoint_committed")
        ),
        "checkpoint_commit_protocol": _text(
            run.get("checkpoint_commit_protocol"), 80
        ),
        "customer_material_mutation_executed": _explicit_bool(
            run.get("customer_material_mutation_executed")
        ),
        "source_content_persisted_in_adapter_receipt": _explicit_bool(
            run.get("source_content_persisted_in_adapter_receipt")
        ),
        "next_cursor_fingerprint": _hash(run.get("next_cursor")),
        "run_receipt_fingerprint": _hash(run.get("run_receipt_path")),
    }


def _check(
    check_id: str,
    passed: bool,
    observed: Any,
    expected: Any,
    *,
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
    checks = [
        _check(
            "CONNECTION_AVAILABLE",
            connection.get("status") == "AVAILABLE",
            _text(connection.get("status"), 80),
            "AVAILABLE",
        ),
        _check(
            "REMOTE_ACCESS_READ_ONLY",
            connection.get("network_side_effect") in _READ_ONLY_NETWORK_SIDE_EFFECTS,
            _text(connection.get("network_side_effect"), 80),
            sorted(_READ_ONLY_NETWORK_SIDE_EFFECTS),
        ),
        _check(
            "CONNECTION_CREDENTIALS_NOT_PERSISTED",
            connection.get("credentials_persisted") is False,
            _explicit_bool(connection.get("credentials_persisted")),
            False,
        ),
        _check(
            "ACCESS_TOKEN_NOT_PERSISTED",
            connection.get("access_token_persisted") is False,
            _explicit_bool(connection.get("access_token_persisted")),
            False,
        ),
    ]

    for index, run in enumerate(runs, start=1):
        prefix = f"RUN_{index}"
        accounting = (
            run["covered_resource_count"]
            + run["unsupported_resource_count"]
            + run["unknown_gap_count"]
        )
        checks.extend(
            [
                _check(f"{prefix}_COMPLETE", run["status"] == "COMPLETE", run["status"], "COMPLETE"),
                _check(
                    f"{prefix}_DISCOVERY_COMPLETE",
                    run["remote_discovery_complete"] is True,
                    run["remote_discovery_complete"],
                    True,
                ),
                _check(
                    f"{prefix}_SUPPORTED_MATERIALIZATION_COMPLETE",
                    run["supported_materialization_complete"] is True,
                    run["supported_materialization_complete"],
                    True,
                ),
                _check(
                    f"{prefix}_RESOURCE_ACCOUNTING_BALANCED",
                    run["discovered_resource_count"] == accounting,
                    {
                        "discovered": run["discovered_resource_count"],
                        "accounted": accounting,
                    },
                    "discovered = covered + unsupported + unknown_gap",
                ),
                _check(f"{prefix}_NO_UNKNOWN_GAPS", run["unknown_gap_count"] == 0, run["unknown_gap_count"], 0),
                _check(f"{prefix}_NO_FAILURES", run["failure_count"] == 0, run["failure_count"], 0),
                _check(
                    f"{prefix}_CHECKPOINT_COMMITTED",
                    run["cursor_checkpoint_committed"] is True,
                    run["cursor_checkpoint_committed"],
                    True,
                ),
                _check(
                    f"{prefix}_RECOVERABLE_CHECKPOINT_PROTOCOL",
                    run["checkpoint_commit_protocol"] == "RECOVERABLE_TWO_STAGE",
                    run["checkpoint_commit_protocol"],
                    "RECOVERABLE_TWO_STAGE",
                ),
                _check(
                    f"{prefix}_CURSOR_FINGERPRINT_RECORDED",
                    bool(run["next_cursor_fingerprint"]),
                    bool(run["next_cursor_fingerprint"]),
                    True,
                ),
                _check(
                    f"{prefix}_SYNC_RECEIPT_LINKED",
                    bool(run["run_receipt_fingerprint"]),
                    bool(run["run_receipt_fingerprint"]),
                    True,
                ),
                _check(
                    f"{prefix}_NO_CUSTOMER_MUTATION",
                    run["customer_material_mutation_executed"] is False,
                    run["customer_material_mutation_executed"],
                    False,
                ),
                _check(
                    f"{prefix}_NO_SOURCE_CONTENT_IN_SYNC_RECEIPT",
                    run["source_content_persisted_in_adapter_receipt"] is False,
                    run["source_content_persisted_in_adapter_receipt"],
                    False,
                ),
                _check(
                    f"{prefix}_DURATION_WITHIN_LIMIT",
                    run["duration_seconds"] <= float(thresholds["max_run_duration_seconds"]),
                    run["duration_seconds"],
                    f"<= {thresholds['max_run_duration_seconds']}",
                ),
            ]
        )

    if runs:
        minimum_discovered = min(row["discovered_resource_count"] for row in runs)
        minimum_coverage = min(row["knowledge_coverage_ratio"] for row in runs)
        maximum_unsupported_ratio = max(
            (
                row["unsupported_resource_count"] / row["discovered_resource_count"]
                if row["discovered_resource_count"]
                else 0.0
            )
            for row in runs
        )
        checks.extend(
            [
                _check(
                    "TENANT_SCALE_MEETS_PROFILE",
                    minimum_discovered >= int(thresholds["min_discovered_resources"]),
                    minimum_discovered,
                    f">= {thresholds['min_discovered_resources']}",
                ),
                _check(
                    "KNOWLEDGE_COVERAGE_MEETS_PROFILE",
                    minimum_coverage >= float(thresholds["min_coverage_ratio"]),
                    minimum_coverage,
                    f">= {thresholds['min_coverage_ratio']}",
                ),
                _check(
                    "UNSUPPORTED_RATIO_WITHIN_PROFILE",
                    maximum_unsupported_ratio <= float(thresholds["max_unsupported_ratio"]),
                    maximum_unsupported_ratio,
                    f"<= {thresholds['max_unsupported_ratio']}",
                ),
            ]
        )

    for index in range(1, len(runs)):
        previous, current = runs[index - 1], runs[index]
        stable = bool(previous["next_cursor_fingerprint"]) and (
            previous["next_cursor_fingerprint"] == current["next_cursor_fingerprint"]
        )
        if stable:
            checks.append(
                _check(
                    f"RUN_{index + 1}_STABLE_SNAPSHOT_NOT_REEXPORTED",
                    current["materialized_resource_count"] == 0
                    and current["export_avoided_count"] >= current["covered_resource_count"],
                    {
                        "materialized": current["materialized_resource_count"],
                        "export_avoided": current["export_avoided_count"],
                        "covered": current["covered_resource_count"],
                    },
                    {"materialized": 0, "export_avoided": ">= covered"},
                )
            )
        else:
            checks.append(
                _check(
                    f"RUN_{index + 1}_REMOTE_CHANGE_OBSERVED",
                    True,
                    "REMOTE_SNAPSHOT_CHANGED",
                    "REPEATABLE_OR_EXPLAINED",
                    severity="INFO",
                )
            )
    return checks


def _report_path(project: str, connector: str, root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_acceptance_reports"
        / connector
        / f"{stamp}_{uuid.uuid4().hex[:12]}.json"
    )


def _combined_evidence(values: list[bool | None]) -> bool | None:
    if any(value is True for value in values):
        return True
    if values and all(value is False for value in values):
        return False
    return None


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
    report_schema: str = FEISHU_TENANT_ACCEPTANCE_SCHEMA,
    acceptance_id_prefix: str = "fta",
) -> dict[str, Any]:
    """Execute repeated managed syncs and persist a fail-closed admission report."""
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
    options = {
        "max_nodes": _bounded_int(max_nodes, 1, 1_000_000, "max_nodes"),
        "max_export_polls": _bounded_int(max_export_polls, 1, 500, "max_export_polls"),
        "export_poll_interval": _bounded_float(export_poll_interval, 0.0, 10.0, "export_poll_interval"),
        "timeout": _bounded_float(timeout, 1.0, 300.0, "timeout"),
    }
    clean_actor = dict(actor or {"name": "qualibug_tenant_acceptance", "role": "knowledge_admin"})

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
                timeout=options["timeout"],
                sleeper=sleeper,
            )
        )
        for _ in range(int(limits["runs"])):
            started = clock()
            raw = dict(
                sync_runner(
                    project,
                    connector,
                    root=resolved_root,
                    actor=clean_actor,
                    deletion_policy="RETAIN",
                    max_nodes=options["max_nodes"],
                    max_export_polls=options["max_export_polls"],
                    export_poll_interval=options["export_poll_interval"],
                    allow_raw_text_fallback=allow_raw_text_fallback,
                    timeout=options["timeout"],
                    sleeper=sleeper,
                )
            )
            projected_runs.append(_project_run(raw, clock() - started))
            if raw.get("status") != "COMPLETE":
                break
    except Exception as exc:
        execution_error = {
            "type": type(exc).__name__,
            "code": _text(str(exc).split(":", 1)[0], 160),
            "detail_fingerprint": _hash(str(exc)),
        }

    checks = _evaluate(connection, projected_runs, limits)
    required_runs = int(limits["runs"])
    if execution_error is not None:
        checks.append(
            _check(
                "ACCEPTANCE_EXECUTION_COMPLETED",
                False,
                execution_error["type"],
                "NO_EXCEPTION",
                detail=execution_error["code"],
            )
        )
    else:
        checks.append(
            _check(
                "ACCEPTANCE_REQUIRED_RUNS_COMPLETED",
                len(projected_runs) == required_runs,
                len(projected_runs),
                required_runs,
            )
        )

    blocker_failures = [
        row for row in checks if row["severity"] == "BLOCKER" and row["status"] == "FAIL"
    ]
    verdict = "PASS" if not blocker_failures else "FAIL"
    durations = [float(row["duration_seconds"]) for row in projected_runs]
    path = _report_path(project, connector, resolved_root)
    relative_path = str(path.relative_to(resolved_root)).replace("\\", "/")
    mutation_evidence = _combined_evidence(
        [row["customer_material_mutation_executed"] for row in projected_runs]
    )
    content_evidence = _combined_evidence(
        [row["source_content_persisted_in_adapter_receipt"] for row in projected_runs]
    )
    report = {
        "schema": _text(report_schema, 120),
        "acceptance_id": _text(acceptance_id_prefix, 40) + "_" + uuid.uuid4().hex[:24],
        "project_id": project,
        "connector_instance_id": connector,
        "profile": profile_name,
        "verdict": verdict,
        "acceptance_ready": verdict == "PASS",
        "started_at_utc": started_at,
        "completed_at_utc": _now(),
        "report_path": relative_path,
        "thresholds": limits,
        "connection": {
            "status": _text(connection.get("status"), 80),
            "connector_type": _text(connection.get("connector_type"), 80),
            "auth_mode": _text(connection.get("auth_mode"), 80),
            "space_count": _integer(connection.get("space_count")),
            "network_side_effect": _text(connection.get("network_side_effect"), 80),
            "credentials_persisted": _explicit_bool(connection.get("credentials_persisted")),
            "access_token_persisted": _explicit_bool(connection.get("access_token_persisted")),
        },
        "runs": projected_runs,
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "blocker_failure_count": len(blocker_failures),
            "executed_run_count": len(projected_runs),
            "required_run_count": required_runs,
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
            "customer_material_mutation_executed": mutation_evidence,
            "source_content_persisted_in_sync_receipt": content_evidence,
            "deletion_policy": "RETAIN",
            "customer_source_content_in_acceptance_report": False,
            "raw_cursor_values_in_acceptance_report": False,
            "credential_values_in_acceptance_report": False,
            "source_occurrence_content_loaded_by_acceptance": False,
            "existing_managed_sync_authority_reused": True,
            "missing_safety_evidence_fails_acceptance": True,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, report)
    return report


def run_connector_tenant_acceptance(
    project_id: str,
    connector_instance_id: str,
    *,
    connection_tester: ConnectionTester = test_managed_connector_connection,
    sync_runner: SyncRunner = run_managed_connector_sync,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the generic acceptance contract through the registry-selected sync authority."""
    return run_feishu_tenant_acceptance(
        project_id,
        connector_instance_id,
        connection_tester=connection_tester,
        sync_runner=sync_runner,
        report_schema=CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
        acceptance_id_prefix="cta",
        **kwargs,
    )


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--connector", required=True)
    parser.add_argument("--profile", choices=sorted(ACCEPTANCE_PROFILES), default="pilot")
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


def _cli_main(
    argv: list[str] | None,
    *,
    runner: Callable[..., Mapping[str, Any]],
    description: str,
) -> int:
    args = _parser(description).parse_args(argv)
    report = runner(
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


def main(argv: list[str] | None = None) -> int:
    return _cli_main(
        argv,
        runner=run_feishu_tenant_acceptance,
        description="Run read-only real-tenant acceptance for a configured Feishu connector.",
    )


def connector_main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the registry-selected generic connector acceptance contract."""
    return _cli_main(
        argv,
        runner=run_connector_tenant_acceptance,
        description="Run read-only real-tenant acceptance for a configured connector.",
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCEPTANCE_PROFILES",
    "CONNECTOR_TENANT_ACCEPTANCE_SCHEMA",
    "FEISHU_TENANT_ACCEPTANCE_SCHEMA",
    "FeishuTenantAcceptanceError",
    "connector_main",
    "run_connector_tenant_acceptance",
    "run_feishu_tenant_acceptance",
]
