"""Controlled remediation planning for quarantined historical authorization findings.

The planner consumes the existing historical authorization inventory and produces
content-addressed recompile/re-execution requests.  It never replays an old compiled
experiment, issues an approval, sends traffic, or mutates source artifacts.  Historical
IDs are predecessor lineage only; current source/runtime/approval bindings must be
resolved again before the normal compiler and executor may consume a request.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .execution_approvals import resolve_execution_approval_for_campaign
from .historical_authorization_inventory import (
    DEFAULT_REPORT_RELATIVE_PATH as DEFAULT_INVENTORY_RELATIVE_PATH,
    build_historical_authorization_inventory,
    validate_historical_authorization_inventory,
)
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .private_pilot_scan_prep import _resolve_scan_runtime_defaults


RERUN_PLAN_SCHEMA = "qualibug.historical-authorization-rerun-plan.v1"
RERUN_REQUEST_SCHEMA = "qualibug.historical-authorization-rerun-request.v1"
DEFAULT_PLAN_RELATIVE_PATH = (
    Path("platform_outputs") / "historical_authorization_rerun_plan.json"
)
_REQUEST_STATUSES = {
    "READY_FOR_CONTROLLED_RECOMPILE",
    "READY_FOR_APPROVAL",
    "MANUAL_RECOMPILE_REQUIRED",
    "BLOCKED_RUNTIME_BINDING",
    "BLOCKED_SOURCE_BINDING",
}


class HistoricalAuthorizationRerunPlanError(ValueError):
    """A rerun plan is malformed or weakens the current execution boundary."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _target_base_url(project_id: str, root: Path, defaults: dict[str, Any]) -> str:
    """Resolve an existing configured target without inventing a new endpoint."""
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project_id, root)
    except Exception:
        registry = {}
    profile = _dict(_dict(registry).get("test_profile"))
    for candidate in (
        profile.get("base_url"),
        profile.get("target_base_url"),
        profile.get("api_base_url"),
        profile.get("service_url"),
        defaults.get("ui_base_url"),
    ):
        text = _text(candidate)
        if text.startswith(("http://", "https://")):
            return text[:500]
    return ""


def _current_source_binding(project_id: str, root: Path) -> dict[str, Any]:
    """Resolve exactly one active source hash or report ambiguity explicitly."""
    try:
        from .enterprise_source_registry import _read_registry

        registry = _read_registry(root, project_id)
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "source_hash": "",
            "source_id": "",
            "candidate_count": 0,
            "reason": f"SOURCE_REGISTRY_UNAVAILABLE:{type(exc).__name__}:{exc}",
        }
    candidates: list[dict[str, str]] = []
    for source_id, raw in sorted(_dict(registry.get("assets")).items()):
        row = _dict(raw)
        source_hash = _text(row.get("latest_source_hash")).lower()
        if source_hash:
            candidates.append({"source_id": _text(source_id), "source_hash": source_hash})
    if len(candidates) == 1:
        return {
            "status": "RESOLVED",
            "source_hash": candidates[0]["source_hash"],
            "source_id": candidates[0]["source_id"],
            "candidate_count": 1,
            "reason": "",
        }
    return {
        "status": "MISSING" if not candidates else "AMBIGUOUS",
        "source_hash": "",
        "source_id": "",
        "candidate_count": len(candidates),
        "reason": (
            "ACTIVE_SOURCE_BINDING_MISSING"
            if not candidates
            else "ACTIVE_SOURCE_BINDING_AMBIGUOUS"
        ),
    }


def _runtime_binding(project_id: str, root: Path) -> dict[str, Any]:
    try:
        defaults = _resolve_scan_runtime_defaults(project_id, root, {})
    except Exception as exc:
        defaults = {}
        runtime_reason = f"RUNTIME_DEFAULTS_UNAVAILABLE:{type(exc).__name__}:{exc}"
    else:
        runtime_reason = ""
    source = _current_source_binding(project_id, root)
    scope_id = _text(defaults.get("scope_id"))
    environment_ref = _text(defaults.get("environment_ref"))
    environment_type = _text(defaults.get("environment_type")).lower()
    target_base_url = _target_base_url(project_id, root, defaults)
    missing = [
        name
        for name, value in (
            ("scope_id", scope_id),
            ("environment_ref", environment_ref),
            ("target_base_url", target_base_url),
        )
        if not value
    ]
    return {
        "scope_id": scope_id,
        "environment_ref": environment_ref,
        "environment_type": environment_type,
        "target_base_url": target_base_url,
        "execution_mode": "safe_read_only",
        "write_execution_allowed": False,
        "source_binding_status": source["status"],
        "source_id": source["source_id"],
        "source_hash": source["source_hash"],
        "source_candidate_count": int(source["candidate_count"]),
        "runtime_status": "RESOLVED" if not missing and not runtime_reason else "INCOMPLETE",
        "missing_runtime_bindings": missing,
        "reason": runtime_reason or _text(source.get("reason")),
    }


def _approval_projection(
    project_id: str,
    *,
    root: Path,
    binding: dict[str, Any],
) -> dict[str, Any]:
    required = all(
        _text(binding.get(field))
        for field in ("scope_id", "environment_ref", "source_hash", "target_base_url")
    )
    if not required:
        return {
            "status": "NOT_RESOLVABLE",
            "approval_id": "",
            "code": "CURRENT_BINDING_INCOMPLETE",
        }
    result = resolve_execution_approval_for_campaign(
        project_id,
        root=root,
        scope_id=_text(binding.get("scope_id")),
        environment_ref=_text(binding.get("environment_ref")),
        source_hash=_text(binding.get("source_hash")),
        target_base_url=_text(binding.get("target_base_url")),
        execution_mode="safe_read_only",
    )
    approval = _dict(result.get("approval"))
    if result.get("found") is True and _text(approval.get("approval_id")):
        return {
            "status": "CURRENT_APPROVAL_FOUND",
            "approval_id": _text(approval.get("approval_id")),
            "code": "",
        }
    return {
        "status": "APPROVAL_REQUIRED",
        "approval_id": "",
        "code": _text(result.get("code")) or "EXECUTION_APPROVAL_NOT_FOUND",
    }


def _request_status(
    queue_item: dict[str, Any],
    binding: dict[str, Any],
    approval: dict[str, Any],
) -> str:
    action = _text(queue_item.get("action"))
    if (
        action == "MANUAL_RECOMPILE_REQUIRED"
        or not _text(queue_item.get("obligation_id"))
        or not _text(queue_item.get("experiment_id"))
    ):
        return "MANUAL_RECOMPILE_REQUIRED"
    if _text(binding.get("runtime_status")) != "RESOLVED":
        return "BLOCKED_RUNTIME_BINDING"
    if _text(binding.get("source_binding_status")) != "RESOLVED":
        return "BLOCKED_SOURCE_BINDING"
    if _text(approval.get("status")) != "CURRENT_APPROVAL_FOUND":
        return "READY_FOR_APPROVAL"
    return "READY_FOR_CONTROLLED_RECOMPILE"


def _rerun_request(
    project_id: str,
    queue_item: dict[str, Any],
    *,
    root: Path,
    binding: dict[str, Any],
) -> dict[str, Any]:
    predecessor = {
        "authority_scope_id": _text(queue_item.get("authority_scope_id")),
        "run_id": _text(queue_item.get("run_id")),
        "campaign_id": _text(queue_item.get("campaign_id")),
        "finding_id": _text(queue_item.get("finding_id")),
        "obligation_id": _text(queue_item.get("obligation_id")),
        "experiment_id": _text(queue_item.get("experiment_id")),
        "quarantine_receipt_id": _text(queue_item.get("quarantine_receipt_id")),
    }
    approval = _approval_projection(project_id, root=root, binding=binding)
    status = _request_status(queue_item, binding, approval)
    payload = {
        "schema_version": RERUN_REQUEST_SCHEMA,
        "status": status,
        "action": "RECOMPILE_AND_REEXECUTE_AUTHORIZATION_EXPERIMENT",
        "project_id": project_id,
        "predecessor": predecessor,
        "requirements": sorted({_text(value) for value in _list(queue_item.get("requirements")) if _text(value)}),
        "current_runtime_binding": dict(binding),
        "approval": approval,
        "execution_policy": {
            "auto_execute": False,
            "old_compiled_experiment_replay_allowed": False,
            "new_run_id_required": True,
            "new_campaign_id_required": True,
            "new_execution_id_required": True,
            "current_source_revalidation_required": True,
            "current_authorization_comparison_contract_required": True,
            "current_causality_receipt_required": True,
            "current_binding_identity_receipts_required": True,
            "customer_delivery_gate_v2_required": True,
            "execution_mode": "safe_read_only",
            "write_execution_allowed": False,
        },
    }
    fingerprint = _fingerprint(payload)
    return {
        **payload,
        "request_id": "auth_rerun_" + fingerprint[:24],
        "request_fingerprint": fingerprint,
    }


def _project_plan(project: dict[str, Any], *, root: Path) -> dict[str, Any]:
    project_id = _text(project.get("project_id"))
    binding = _runtime_binding(project_id, root)
    requests = [
        _rerun_request(project_id, _dict(item), root=root, binding=binding)
        for item in _list(project.get("rerun_queue"))
    ]
    requests.sort(key=lambda value: value["request_id"])
    counts = {
        status: sum(value["status"] == status for value in requests)
        for status in sorted(_REQUEST_STATUSES)
    }
    status = (
        "BLOCKED"
        if counts["BLOCKED_RUNTIME_BINDING"] or counts["BLOCKED_SOURCE_BINDING"]
        else "MANUAL_ACTION_REQUIRED"
        if counts["MANUAL_RECOMPILE_REQUIRED"] or counts["READY_FOR_APPROVAL"]
        else "READY"
        if requests
        else "CLEAR"
    )
    return {
        "project_id": project_id,
        "status": status,
        "request_count": len(requests),
        "status_counts": counts,
        "requests": requests,
    }


def build_historical_authorization_rerun_plan(
    inventory: dict[str, Any],
    *,
    root: str | Path,
    project_ids: Iterable[str] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a non-executing plan from a validated inventory report."""
    validated_inventory = validate_historical_authorization_inventory(inventory)
    resolved_root = Path(root).expanduser().resolve()
    requested = {_text(value) for value in (project_ids or []) if _text(value)}
    projects = [
        _project_plan(_dict(project), root=resolved_root)
        for project in _list(validated_inventory.get("projects"))
        if not requested or _text(_dict(project).get("project_id")) in requested
    ]
    projects.sort(key=lambda value: value["project_id"])
    requests = [request for project in projects for request in project["requests"]]
    status_counts = {
        status: sum(request["status"] == status for request in requests)
        for status in sorted(_REQUEST_STATUSES)
    }
    overall_status = (
        "BLOCKED"
        if status_counts["BLOCKED_RUNTIME_BINDING"] or status_counts["BLOCKED_SOURCE_BINDING"]
        else "MANUAL_ACTION_REQUIRED"
        if status_counts["MANUAL_RECOMPILE_REQUIRED"] or status_counts["READY_FOR_APPROVAL"]
        else "READY"
        if requests
        else "CLEAR"
    )
    payload = {
        "schema_version": RERUN_PLAN_SCHEMA,
        "generated_at_utc": _text(generated_at_utc) or _utc_now(),
        "root": str(resolved_root),
        "inventory_fingerprint": _text(validated_inventory.get("inventory_fingerprint")),
        "status": overall_status,
        "project_count": len(projects),
        "request_count": len(requests),
        "status_counts": status_counts,
        "auto_execute": False,
        "source_artifacts_modified": False,
        "projects": projects,
    }
    payload["plan_fingerprint"] = _fingerprint(payload)
    return validate_historical_authorization_rerun_plan(payload)


def validate_historical_authorization_rerun_plan(plan: dict[str, Any]) -> dict[str, Any]:
    row = _dict(plan)
    required = {
        "schema_version", "generated_at_utc", "root", "inventory_fingerprint",
        "status", "project_count", "request_count", "status_counts",
        "auto_execute", "source_artifacts_modified", "projects", "plan_fingerprint",
    }
    if set(row) != required or row.get("schema_version") != RERUN_PLAN_SCHEMA:
        raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_fields_invalid")
    if row.get("auto_execute") is not False or row.get("source_artifacts_modified") is not False:
        raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_mutation_forbidden")
    projects = [_dict(value) for value in _list(row.get("projects"))]
    if [_text(value.get("project_id")) for value in projects] != sorted({_text(value.get("project_id")) for value in projects}):
        raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_projects_invalid")
    requests: list[dict[str, Any]] = []
    for project in projects:
        project_requests = [_dict(value) for value in _list(project.get("requests"))]
        if int(project.get("request_count") or 0) != len(project_requests):
            raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_project_count_invalid")
        for request in project_requests:
            if request.get("schema_version") != RERUN_REQUEST_SCHEMA or request.get("status") not in _REQUEST_STATUSES:
                raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_request_status_invalid")
            policy = _dict(request.get("execution_policy"))
            if (
                policy.get("auto_execute") is not False
                or policy.get("old_compiled_experiment_replay_allowed") is not False
                or policy.get("new_run_id_required") is not True
                or policy.get("new_campaign_id_required") is not True
                or policy.get("new_execution_id_required") is not True
                or policy.get("write_execution_allowed") is not False
            ):
                raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_request_policy_invalid")
            unsigned = {key: value for key, value in request.items() if key not in {"request_id", "request_fingerprint"}}
            fingerprint = _fingerprint(unsigned)
            if (
                _text(request.get("request_id")) != "auth_rerun_" + fingerprint[:24]
                or _text(request.get("request_fingerprint")) != fingerprint
            ):
                raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_request_fingerprint_invalid")
        expected_counts = {
            status: sum(value.get("status") == status for value in project_requests)
            for status in sorted(_REQUEST_STATUSES)
        }
        if project.get("status_counts") != expected_counts:
            raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_project_summary_invalid")
        requests.extend(project_requests)
    expected_counts = {
        status: sum(value.get("status") == status for value in requests)
        for status in sorted(_REQUEST_STATUSES)
    }
    if (
        int(row.get("project_count") or 0) != len(projects)
        or int(row.get("request_count") or 0) != len(requests)
        or row.get("status_counts") != expected_counts
    ):
        raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_summary_invalid")
    observed = _text(row.get("plan_fingerprint"))
    expected = _fingerprint({key: value for key, value in row.items() if key != "plan_fingerprint"})
    if not observed or observed != expected:
        raise HistoricalAuthorizationRerunPlanError("historical_authorization_rerun_plan_fingerprint_invalid")
    return dict(row)


def resolve_rerun_plan_path(output: str | Path | None, *, root: str | Path) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    if output is None or _text(output) in {"", "default"}:
        return resolved_root / DEFAULT_PLAN_RELATIVE_PATH
    path = Path(output).expanduser()
    return path.resolve() if path.is_absolute() else (resolved_root / path).resolve()


def write_historical_authorization_rerun_plan(
    plan: dict[str, Any],
    *,
    output: str | Path | None,
    root: str | Path,
) -> Path:
    validated = validate_historical_authorization_rerun_plan(plan)
    destination = resolve_rerun_plan_path(output, root=root)
    _write_json_object_atomic(destination, validated)
    return destination


def _load_or_build_inventory(
    *,
    root: Path,
    inventory_path: str,
    project_ids: list[str],
) -> dict[str, Any]:
    if inventory_path:
        path = Path(inventory_path).expanduser()
        if not path.is_absolute():
            path = root / path
        return validate_historical_authorization_inventory(_read_json_object(path.resolve()))
    return build_historical_authorization_inventory(root, project_ids=project_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a non-executing authorization rerun plan from the historical inventory.")
    parser.add_argument("--root", default=".", help="QualiBug root directory.")
    parser.add_argument("--inventory", default="", help=("Existing inventory JSON. Omit to build a fresh in-memory inventory; default inventory location is " + str(DEFAULT_INVENTORY_RELATIVE_PATH)))
    parser.add_argument("--project", action="append", default=[], help="Plan only this project ID. Repeat for multiple projects.")
    parser.add_argument("--output", default="default", help="Rerun plan JSON path.")
    parser.add_argument("--stdout-only", action="store_true", help="Print without writing the rerun plan file.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON.")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        inventory = _load_or_build_inventory(root=root, inventory_path=args.inventory, project_ids=args.project)
        plan = build_historical_authorization_rerun_plan(inventory, root=root, project_ids=args.project)
        if not args.stdout_only:
            write_historical_authorization_rerun_plan(plan, output=args.output, root=root)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}", "auto_execute": False, "source_artifacts_modified": False}, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_PLAN_RELATIVE_PATH",
    "HistoricalAuthorizationRerunPlanError",
    "RERUN_PLAN_SCHEMA",
    "RERUN_REQUEST_SCHEMA",
    "build_historical_authorization_rerun_plan",
    "main",
    "resolve_rerun_plan_path",
    "validate_historical_authorization_rerun_plan",
    "write_historical_authorization_rerun_plan",
]
