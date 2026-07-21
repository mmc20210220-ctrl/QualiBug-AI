"""Runtime evidence delivery: carry-forward, progress delta, promotion gate, manifest."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403
from ._common import _dedupe, _read_json  # noqa: F401

def _runtime_rerun_manifest_from_value(value: Any) -> tuple[dict[str, Any] | None, str]:
    """Load an optional remediation rerun manifest from config.

    The remediation plan created by ``_build_runtime_evidence_remediation_plan``
    already contains a deterministic ``rerun_manifest``.  This loader lets a
    follow-up run consume either that full plan, the manifest object itself, or
    a JSON file path without silently falling back to a full probe run.
    """
    if not value:
        return None, ""
    if isinstance(value, dict):
        if isinstance(value.get("rerun_manifest"), dict):
            return dict(value.get("rerun_manifest") or {}), "inline_remediation_plan"
        return dict(value), "inline_rerun_manifest"
    try:
        path = Path(str(value)).expanduser()
        if path.exists() and path.is_file():
            loaded = _read_json(path)
            if isinstance(loaded, dict):
                if isinstance(loaded.get("rerun_manifest"), dict):
                    return dict(loaded.get("rerun_manifest") or {}), str(path)
                return loaded, str(path)
    except Exception:
        return None, ""
    return None, ""


def _runtime_rerun_selection_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve an optional candidate-id allowlist for remediation reruns.

    Supported config keys intentionally accept both a full remediation plan and
    a bare rerun manifest so the previous run can feed the next run directly:

    - ``runtime_evidence_rerun_manifest``
    - ``runtime_evidence_rerun_manifest_path``
    - ``runtime_evidence_remediation_plan``
    - ``runtime_evidence_remediation_plan_path``
    - ``runtime_rerun_candidate_ids``
    """
    sources: list[str] = []
    candidate_ids: list[str] = []
    excluded_ready: list[str] = []

    for key in (
        "runtime_evidence_rerun_manifest",
        "runtime_evidence_rerun_manifest_path",
        "runtime_evidence_remediation_plan",
        "runtime_evidence_remediation_plan_path",
        "runtime_remediation_plan_path",
    ):
        manifest, source = _runtime_rerun_manifest_from_value(config.get(key))
        if not manifest:
            continue
        sources.append(f"{key}:{source or 'inline'}")
        candidate_ids.extend(str(v) for v in (manifest.get("candidate_ids") or []) if str(v).strip())
        excluded_ready.extend(str(v) for v in (manifest.get("customer_ready_candidate_ids_excluded") or []) if str(v).strip())

    direct = config.get("runtime_rerun_candidate_ids") or config.get("rerun_candidate_ids")
    if isinstance(direct, str):
        direct_values = [part.strip() for part in re.split(r"[,\s]+", direct) if part.strip()]
    elif isinstance(direct, list):
        direct_values = [str(v).strip() for v in direct if str(v).strip()]
    else:
        direct_values = []
    if direct_values:
        sources.append("runtime_rerun_candidate_ids")
        candidate_ids.extend(direct_values)

    enabled = bool(sources) or bool(config.get("enable_runtime_rerun_manifest"))
    return {
        "enabled": enabled,
        "sources": sources,
        "candidate_ids": _dedupe(candidate_ids),
        "customer_ready_candidate_ids_excluded": _dedupe(excluded_ready),
    }


def _apply_runtime_rerun_selection(probes: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter probes for a targeted remediation rerun and return an audit receipt.

    If a previous remediation plan says only ``WRITE-BINDING-GAP`` needs a
    rerun, the executor should not waste time replaying already
    customer-ready probes.  The receipt is added to the runtime report so the
    run remains auditable.
    """
    selection = _runtime_rerun_selection_from_config(config)
    available_ids = [str(p.get("candidate_id") or "") for p in probes if str(p.get("candidate_id") or "").strip()]
    available_set = set(available_ids)
    candidate_ids = list(selection.get("candidate_ids") or [])
    if not selection.get("enabled"):
        return probes, {
            "enabled": False,
            "status": "disabled_full_probe_plan_selected",
            "available_candidate_count": len(available_set),
            "selected_probe_count": len(probes),
            "skipped_probe_count": 0,
            "candidate_ids": [],
            "missing_candidate_ids": [],
            "customer_ready_candidate_ids_excluded": [],
            "sources": [],
        }

    selected_set = set(candidate_ids)
    filtered = [p for p in probes if str(p.get("candidate_id") or "") in selected_set]
    missing = [cid for cid in candidate_ids if cid not in available_set]
    if not candidate_ids:
        status = "empty_rerun_queue_no_probes_selected"
    elif not filtered:
        status = "no_matching_rerun_candidates"
    else:
        status = "targeted_runtime_rerun_selection_applied"
    return filtered, {
        "enabled": True,
        "status": status,
        "available_candidate_count": len(available_set),
        "requested_candidate_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "selected_probe_count": len(filtered),
        "skipped_probe_count": max(0, len(probes) - len(filtered)),
        "missing_candidate_ids": missing,
        "customer_ready_candidate_ids_excluded": list(selection.get("customer_ready_candidate_ids_excluded") or []),
        "sources": list(selection.get("sources") or []),
    }


def _runtime_carry_forward_candidate_ids(config: dict[str, Any], runtime_rerun_selection: dict[str, Any]) -> list[str]:
    explicit = config.get("runtime_evidence_carry_forward_candidate_ids") or config.get("carry_forward_candidate_ids")
    if isinstance(explicit, str):
        values = [part.strip() for part in re.split(r"[,\s]+", explicit) if part.strip()]
    elif isinstance(explicit, list):
        values = [str(value).strip() for value in explicit if str(value).strip()]
    else:
        values = []
    values.extend(str(value) for value in (runtime_rerun_selection.get("customer_ready_candidate_ids_excluded") or []) if str(value).strip())
    return _dedupe(values)


def _runtime_carry_forward_artifact_paths(config: dict[str, Any], runtime_rerun_selection: dict[str, Any]) -> dict[str, list[Path]]:
    """Resolve previous-run artifacts that can be carried into a targeted rerun.

    A targeted rerun deliberately skips already customer-ready probes.  Without
    this carry-forward step, the follow-up run's reproduction pack would appear
    to have lost those customer-ready findings.  We only consume explicit paths
    or artifacts colocated with the remediation plan path that selected this
    rerun.
    """
    pack_paths: list[Path] = []
    ledger_paths: list[Path] = []

    def add_path(bucket: list[Path], value: Any) -> None:
        if not value:
            return
        try:
            path = Path(str(value)).expanduser()
        except Exception:
            return
        if path.exists() and path.is_file():
            bucket.append(path)

    for key in (
        "runtime_evidence_previous_reproduction_pack_path",
        "runtime_customer_reproduction_pack_path",
        "runtime_evidence_carry_forward_reproduction_pack_path",
    ):
        add_path(pack_paths, config.get(key))
    for key in (
        "runtime_evidence_previous_probe_ledger_path",
        "runtime_evidence_probe_ledger_path",
        "runtime_evidence_carry_forward_probe_ledger_path",
    ):
        add_path(ledger_paths, config.get(key))

    for source in runtime_rerun_selection.get("sources") or []:
        raw = str(source).split(":", 1)[1] if ":" in str(source) else str(source)
        if not raw or raw.startswith("inline"):
            continue
        try:
            path = Path(raw).expanduser()
        except Exception:
            continue
        if not path.exists():
            continue
        base_dir = path.parent if path.is_file() else path
        add_path(pack_paths, base_dir / "grounded_probe_runtime_customer_reproduction_pack.json")
        add_path(ledger_paths, base_dir / "grounded_probe_runtime_evidence_probe_ledger.json")

    return {"reproduction_pack_paths": _dedupe_paths(pack_paths), "probe_ledger_paths": _dedupe_paths(ledger_paths)}


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return value


def _runtime_carry_forward_package_ready(package: dict[str, Any]) -> bool:
    if package.get("customer_ready") is not True:
        return False
    gate = package.get("reproduction_readiness_gate") if isinstance(package.get("reproduction_readiness_gate"), dict) else {}
    if gate:
        if gate.get("customer_ready") is not True:
            return False
        if gate.get("blockers"):
            return False
    trace = package.get("reproduction_trace") if isinstance(package.get("reproduction_trace"), list) else []
    if not trace:
        return False
    return True


def _build_runtime_evidence_carry_forward(config: dict[str, Any], runtime_rerun_selection: dict[str, Any]) -> dict[str, Any]:
    """Carry previous customer-ready evidence into targeted reruns.

    The executor may run only remediation candidates while excluding known-good
    customer-ready candidates.  This artifact preserves those previous ready
    findings in the new report, but only when the prior reproduction package was
    itself customer-ready and blocker-free.
    """
    candidate_ids = _runtime_carry_forward_candidate_ids(config, runtime_rerun_selection)
    enabled = bool(candidate_ids) and bool(runtime_rerun_selection.get("enabled"))
    paths = _runtime_carry_forward_artifact_paths(config, runtime_rerun_selection)
    carried_packages: list[dict[str, Any]] = []
    carried_ledger_entries: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    candidate_set = set(candidate_ids)

    if enabled:
        for pack_path in paths["reproduction_pack_paths"]:
            try:
                pack = _read_json(pack_path)
            except Exception as exc:
                blocked.append({"source": str(pack_path), "reason": f"reproduction_pack_load_failed:{type(exc).__name__}"})
                continue
            for package in pack.get("packages") or []:
                if not isinstance(package, dict):
                    continue
                cid = str(package.get("candidate_id") or "")
                if cid not in candidate_set:
                    continue
                if not _runtime_carry_forward_package_ready(package):
                    blocked.append({
                        "candidate_id": cid,
                        "source": str(pack_path),
                        "reason": "previous_reproduction_package_not_customer_ready_or_missing_trace",
                    })
                    continue
                cloned = _json_clone(package)
                cloned["carried_forward"] = True
                cloned["carry_forward_source"] = str(pack_path)
                cloned["carry_forward_reason"] = "targeted_rerun_excluded_previously_customer_ready_candidate"
                carried_packages.append(cloned)

        for ledger_path in paths["probe_ledger_paths"]:
            try:
                ledger = _read_json(ledger_path)
            except Exception as exc:
                blocked.append({"source": str(ledger_path), "reason": f"probe_ledger_load_failed:{type(exc).__name__}"})
                continue
            for entry in ledger.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                cid = str(entry.get("candidate_id") or "")
                if cid not in candidate_set:
                    continue
                if entry.get("customer_ready") is not True:
                    blocked.append({"candidate_id": cid, "source": str(ledger_path), "reason": "previous_probe_ledger_entry_not_customer_ready"})
                    continue
                cloned = _json_clone(entry)
                cloned["carried_forward"] = True
                cloned["carry_forward_source"] = str(ledger_path)
                cloned["next_action"] = "Carried forward from previous customer-ready evidence; rerun only if auth, endpoint, fixture, or oracle semantics changed."
                carried_ledger_entries.append(cloned)

    carried_package_ids: list[str] = []
    for package in carried_packages:
        cid = str(package.get("candidate_id") or "")
        if cid and cid not in carried_package_ids:
            carried_package_ids.append(cid)
    carried_ledger_ids: list[str] = []
    for entry in carried_ledger_entries:
        cid = str(entry.get("candidate_id") or "")
        if cid and cid not in carried_ledger_ids:
            carried_ledger_ids.append(cid)

    return {
        "engine": "runtime_evidence_carry_forward_v1_phase95",
        "enabled": enabled,
        "status": (
            "customer_ready_evidence_carried_forward" if carried_package_ids or carried_ledger_ids else
            "enabled_but_no_customer_ready_evidence_found" if enabled else
            "disabled_no_targeted_rerun_or_no_excluded_candidates"
        ),
        "candidate_ids_requested": candidate_ids,
        "carried_forward_candidate_ids": _dedupe(carried_package_ids + carried_ledger_ids),
        "carried_forward_reproduction_count": len(carried_packages),
        "carried_forward_probe_ledger_count": len(carried_ledger_entries),
        "blocked_candidate_count": len(blocked),
        "blocked_candidates": blocked[:50],
        "source_paths": {
            "reproduction_packs": [str(path) for path in paths["reproduction_pack_paths"]],
            "probe_ledgers": [str(path) for path in paths["probe_ledger_paths"]],
        },
        "packages": carried_packages,
        "probe_ledger_entries": carried_ledger_entries,
    }


def _render_runtime_evidence_carry_forward_markdown(carry: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Carry Forward",
        "",
        f"- engine: `{carry.get('engine')}`",
        f"- status: `{carry.get('status')}`",
        f"- enabled: `{carry.get('enabled')}`",
        f"- requested candidates: `{json.dumps(carry.get('candidate_ids_requested') or [], ensure_ascii=False)}`",
        f"- carried candidates: `{json.dumps(carry.get('carried_forward_candidate_ids') or [], ensure_ascii=False)}`",
        f"- carried reproduction packages: {carry.get('carried_forward_reproduction_count')}",
        f"- carried probe ledger entries: {carry.get('carried_forward_probe_ledger_count')}",
        f"- blocked candidates: {carry.get('blocked_candidate_count')}",
        "",
    ]
    packages = [p for p in (carry.get("packages") or []) if isinstance(p, dict)]
    if packages:
        lines.extend(["## Carried customer-ready reproduction packages", "", "| Candidate | Finding | Readiness | Source |", "|---|---|---|---|"])
        for package in packages[:50]:
            lines.append(
                "| "
                + " | ".join([
                    str(package.get("candidate_id") or "-"),
                    str(package.get("finding_id") or "-"),
                    str(package.get("readiness_level") or "-"),
                    str(package.get("carry_forward_source") or "-").replace("|", "\\|"),
                ])
                + " |"
            )
        lines.append("")
    blocked = [b for b in (carry.get("blocked_candidates") or []) if isinstance(b, dict)]
    if blocked:
        lines.extend(["## Carry-forward blockers", ""])
        for item in blocked[:50]:
            lines.append(f"- `{item.get('candidate_id') or '-'}`: {item.get('reason')} ({item.get('source')})")
        lines.append("")
    return "\n".join(lines)


def _runtime_progress_delta_artifact_paths(config: dict[str, Any], report: dict[str, Any]) -> dict[str, list[Path]]:
    """Resolve previous-run artifacts used to compare targeted rerun progress.

    The remediation loop is useful only if each rerun can prove that evidence
    gaps are shrinking and customer-ready evidence is preserved.  This resolver
    consumes explicit previous-artifact paths, a baseline directory, or the
    parent directory of the remediation plan that triggered the rerun.
    """
    buckets: dict[str, list[Path]] = {
        "scoreboards": [],
        "remediation_plans": [],
        "reproduction_packs": [],
        "probe_ledgers": [],
    }

    def add_file(bucket: str, value: Any) -> None:
        if not value:
            return
        try:
            path = Path(str(value)).expanduser()
        except Exception:
            return
        if path.exists() and path.is_file():
            buckets[bucket].append(path)

    def add_dir(value: Any) -> None:
        if not value:
            return
        try:
            base_dir = Path(str(value)).expanduser()
        except Exception:
            return
        if base_dir.is_file():
            base_dir = base_dir.parent
        if not base_dir.exists() or not base_dir.is_dir():
            return
        add_file("scoreboards", base_dir / "grounded_probe_runtime_evidence_scoreboard.json")
        add_file("remediation_plans", base_dir / "grounded_probe_runtime_evidence_remediation_plan.json")
        add_file("reproduction_packs", base_dir / "grounded_probe_runtime_customer_reproduction_pack.json")
        add_file("probe_ledgers", base_dir / "grounded_probe_runtime_evidence_probe_ledger.json")

    explicit_keys = {
        "scoreboards": [
            "runtime_evidence_previous_scoreboard_path",
            "runtime_evidence_progress_previous_scoreboard_path",
        ],
        "remediation_plans": [
            "runtime_evidence_previous_remediation_plan_path",
            "runtime_evidence_progress_previous_remediation_plan_path",
        ],
        "reproduction_packs": [
            "runtime_evidence_previous_reproduction_pack_path",
            "runtime_customer_reproduction_pack_path",
            "runtime_evidence_carry_forward_reproduction_pack_path",
        ],
        "probe_ledgers": [
            "runtime_evidence_previous_probe_ledger_path",
            "runtime_evidence_probe_ledger_path",
            "runtime_evidence_carry_forward_probe_ledger_path",
        ],
    }
    for bucket, keys in explicit_keys.items():
        for key in keys:
            add_file(bucket, config.get(key))

    for key in (
        "runtime_evidence_progress_baseline_dir",
        "runtime_evidence_previous_output_dir",
        "runtime_evidence_baseline_output_dir",
    ):
        add_dir(config.get(key))

    runtime_rerun_selection = report.get("runtime_rerun_selection") if isinstance(report.get("runtime_rerun_selection"), dict) else {}
    for source in runtime_rerun_selection.get("sources") or []:
        raw = str(source).split(":", 1)[1] if ":" in str(source) else str(source)
        if not raw or raw.startswith("inline"):
            continue
        try:
            path = Path(raw).expanduser()
        except Exception:
            continue
        if path.exists():
            add_dir(path.parent if path.is_file() else path)

    return {key: _dedupe_paths(value) for key, value in buckets.items()}


def _read_first_runtime_artifact(paths: list[Path]) -> tuple[dict[str, Any], str | None, str | None]:
    for path in paths:
        try:
            payload = _read_json(path)
        except Exception as exc:
            return {}, str(path), f"load_failed:{type(exc).__name__}"
        if isinstance(payload, dict):
            return payload, str(path), None
    return {}, None, None


def _runtime_gap_types_from_remediation_plan(plan: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for group in plan.get("priority_groups") or []:
        if not isinstance(group, dict):
            continue
        gap = str(group.get("gap_type") or "").strip()
        if gap:
            gaps.append(gap)
    return _dedupe(gaps)


def _runtime_reproduction_ready_count(pack: dict[str, Any]) -> int:
    if isinstance(pack.get("customer_ready_reproduction_count"), int):
        return int(pack.get("customer_ready_reproduction_count") or 0)
    return sum(1 for package in (pack.get("packages") or []) if isinstance(package, dict) and package.get("customer_ready") is True)


def _runtime_delta_number(current: Any, previous: Any) -> float | int:
    try:
        current_num = float(current or 0)
        previous_num = float(previous or 0)
    except Exception:
        return 0
    delta = round(current_num - previous_num, 2)
    return int(delta) if float(delta).is_integer() else delta


def _build_runtime_evidence_progress_delta(config: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Compare the current run with previous evidence artifacts.

    Targeted remediation reruns must show measurable improvement or a clear
    regression.  This artifact prevents the loop from saying "rerun complete"
    when P0 gaps stayed the same, customer-ready evidence disappeared, or new
    blockers appeared.
    """
    paths = _runtime_progress_delta_artifact_paths(config, report)
    previous_scoreboard, scoreboard_source, scoreboard_error = _read_first_runtime_artifact(paths["scoreboards"])
    previous_plan, plan_source, plan_error = _read_first_runtime_artifact(paths["remediation_plans"])
    previous_pack, pack_source, pack_error = _read_first_runtime_artifact(paths["reproduction_packs"])
    previous_ledger, ledger_source, ledger_error = _read_first_runtime_artifact(paths["probe_ledgers"])

    current_scoreboard = report.get("runtime_evidence_scoreboard") if isinstance(report.get("runtime_evidence_scoreboard"), dict) else {}
    current_plan = report.get("runtime_evidence_remediation_plan") if isinstance(report.get("runtime_evidence_remediation_plan"), dict) else {}
    current_pack = report.get("runtime_customer_reproduction_pack") if isinstance(report.get("runtime_customer_reproduction_pack"), dict) else {}
    current_ledger = report.get("runtime_evidence_probe_ledger") if isinstance(report.get("runtime_evidence_probe_ledger"), dict) else {}

    previous_gap_types = _runtime_gap_types_from_remediation_plan(previous_plan)
    current_gap_types = _runtime_gap_types_from_remediation_plan(current_plan)
    resolved_gap_types = [gap for gap in previous_gap_types if gap not in set(current_gap_types)]
    new_gap_types = [gap for gap in current_gap_types if gap not in set(previous_gap_types)]
    persisting_gap_types = [gap for gap in current_gap_types if gap in set(previous_gap_types)]
    has_previous = bool(previous_scoreboard or previous_plan or previous_pack or previous_ledger)

    previous_maturity = previous_scoreboard.get("evidence_maturity") if isinstance(previous_scoreboard.get("evidence_maturity"), dict) else {}
    current_maturity = current_scoreboard.get("evidence_maturity") if isinstance(current_scoreboard.get("evidence_maturity"), dict) else {}

    previous_p0 = int(previous_plan.get("p0_group_count") or 0)
    current_p0 = int(current_plan.get("p0_group_count") or 0)
    previous_queue = int(previous_plan.get("queued_candidate_count") or 0)
    current_queue = int(current_plan.get("queued_candidate_count") or 0)
    previous_ready = _runtime_reproduction_ready_count(previous_pack)
    current_ready = _runtime_reproduction_ready_count(current_pack)
    previous_ledger_ready = int(previous_ledger.get("customer_ready_probe_count") or 0)
    current_ledger_ready = int(current_ledger.get("customer_ready_probe_count") or 0)

    regressions: list[str] = []
    if previous_scoreboard and current_scoreboard:
        if _runtime_delta_number(current_scoreboard.get("execution_integrity_score"), previous_scoreboard.get("execution_integrity_score")) < 0:
            regressions.append("execution_integrity_score_decreased")
    if previous_plan:
        if current_p0 > previous_p0:
            regressions.append("p0_gap_count_increased")
        if current_queue > previous_queue:
            regressions.append("queued_candidate_count_increased")
    if previous_pack and current_ready < previous_ready:
        regressions.append("customer_ready_reproduction_count_decreased")
    if previous_ledger and current_ledger_ready < previous_ledger_ready:
        regressions.append("customer_ready_probe_ledger_count_decreased")
    if has_previous and new_gap_types:
        regressions.append("new_runtime_gap_types_detected")

    if not has_previous:
        status = "no_previous_runtime_evidence_found"
        next_action = "Capture this run as the baseline, then rerun after fixing the remediation plan gaps."
    elif regressions:
        status = "runtime_evidence_regression_detected"
        next_action = "Stop customer handoff, inspect regression reasons, and rerun only after preserving previous customer-ready evidence."
    elif current_p0 == 0 and current_queue == 0 and bool(current_maturity.get("customer_ready")):
        status = "customer_ready_runtime_evidence_progress"
        next_action = "No P0 remediation remains; prepare customer handoff and retain this run receipt as the new baseline."
    elif resolved_gap_types or current_p0 < previous_p0 or current_queue < previous_queue or current_ready > previous_ready:
        status = "runtime_evidence_improving"
        next_action = "Continue the remediation loop with the remaining queued candidate_ids and preserve carried-forward evidence."
    else:
        status = "runtime_evidence_unchanged"
        next_action = "Review persisting gap types; current rerun did not reduce runtime evidence blockers."

    artifact_load_errors = [item for item in [scoreboard_error, plan_error, pack_error, ledger_error] if item]
    return {
        "engine": "runtime_evidence_progress_delta_v1_phase95",
        "status": status,
        "project_id": report.get("project_id"),
        "has_previous_evidence": has_previous,
        "previous_sources": {
            "scoreboard": scoreboard_source,
            "remediation_plan": plan_source,
            "reproduction_pack": pack_source,
            "probe_ledger": ledger_source,
        },
        "artifact_load_errors": artifact_load_errors,
        "targeted_rerun": {
            "enabled": bool((report.get("runtime_rerun_selection") or {}).get("enabled")) if isinstance(report.get("runtime_rerun_selection"), dict) else False,
            "selected_probe_count": ((report.get("runtime_rerun_selection") or {}).get("selected_probe_count") if isinstance(report.get("runtime_rerun_selection"), dict) else 0),
            "skipped_probe_count": ((report.get("runtime_rerun_selection") or {}).get("skipped_probe_count") if isinstance(report.get("runtime_rerun_selection"), dict) else 0),
        },
        "metrics": {
            "execution_integrity_score": {
                "previous": previous_scoreboard.get("execution_integrity_score", 0),
                "current": current_scoreboard.get("execution_integrity_score", 0),
                "delta": _runtime_delta_number(current_scoreboard.get("execution_integrity_score"), previous_scoreboard.get("execution_integrity_score")),
            },
            "execution_coverage_rate": {
                "previous": previous_scoreboard.get("execution_coverage_rate", 0),
                "current": current_scoreboard.get("execution_coverage_rate", 0),
                "delta": _runtime_delta_number(current_scoreboard.get("execution_coverage_rate"), previous_scoreboard.get("execution_coverage_rate")),
            },
            "runtime_binding_success_rate": {
                "previous": previous_scoreboard.get("runtime_binding_success_rate", 0),
                "current": current_scoreboard.get("runtime_binding_success_rate", 0),
                "delta": _runtime_delta_number(current_scoreboard.get("runtime_binding_success_rate"), previous_scoreboard.get("runtime_binding_success_rate")),
            },
            "p0_group_count": {"previous": previous_p0, "current": current_p0, "delta": current_p0 - previous_p0},
            "queued_candidate_count": {"previous": previous_queue, "current": current_queue, "delta": current_queue - previous_queue},
            "customer_ready_reproduction_count": {"previous": previous_ready, "current": current_ready, "delta": current_ready - previous_ready},
            "customer_ready_probe_ledger_count": {"previous": previous_ledger_ready, "current": current_ledger_ready, "delta": current_ledger_ready - previous_ledger_ready},
        },
        "maturity": {
            "previous_level": previous_maturity.get("level"),
            "current_level": current_maturity.get("level"),
            "previous_customer_ready": bool(previous_maturity.get("customer_ready")),
            "current_customer_ready": bool(current_maturity.get("customer_ready")),
        },
        "resolved_gap_types": resolved_gap_types,
        "new_gap_types": new_gap_types,
        "persisting_gap_types": persisting_gap_types,
        "regressions": regressions,
        "carry_forward": {
            "candidate_count": len((report.get("runtime_evidence_carry_forward") or {}).get("carried_forward_candidate_ids") or []) if isinstance(report.get("runtime_evidence_carry_forward"), dict) else 0,
            "reproduction_count": (report.get("runtime_evidence_carry_forward") or {}).get("carried_forward_reproduction_count", 0) if isinstance(report.get("runtime_evidence_carry_forward"), dict) else 0,
            "probe_ledger_count": (report.get("runtime_evidence_carry_forward") or {}).get("carried_forward_probe_ledger_count", 0) if isinstance(report.get("runtime_evidence_carry_forward"), dict) else 0,
        },
        "next_action": next_action,
    }


def _render_runtime_evidence_progress_delta_markdown(delta: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Progress Delta",
        "",
        f"- engine: `{delta.get('engine')}`",
        f"- status: `{delta.get('status')}`",
        f"- project: `{delta.get('project_id')}`",
        f"- has previous evidence: `{delta.get('has_previous_evidence')}`",
        f"- next action: {delta.get('next_action')}",
        "",
    ]
    maturity = delta.get("maturity") if isinstance(delta.get("maturity"), dict) else {}
    lines.extend([
        "## Evidence maturity",
        "",
        f"- previous: `{maturity.get('previous_level')}` / customer-ready `{maturity.get('previous_customer_ready')}`",
        f"- current: `{maturity.get('current_level')}` / customer-ready `{maturity.get('current_customer_ready')}`",
        "",
    ])
    metrics = delta.get("metrics") if isinstance(delta.get("metrics"), dict) else {}
    if metrics:
        lines.extend(["## Metric deltas", "", "| Metric | Previous | Current | Delta |", "|---|---:|---:|---:|"])
        for name, payload in metrics.items():
            if not isinstance(payload, dict):
                continue
            lines.append(f"| `{name}` | {payload.get('previous')} | {payload.get('current')} | {payload.get('delta')} |")
        lines.append("")
    lines.extend([
        "## Gap movement",
        "",
        f"- resolved gap types: `{json.dumps(delta.get('resolved_gap_types') or [], ensure_ascii=False)}`",
        f"- persisting gap types: `{json.dumps(delta.get('persisting_gap_types') or [], ensure_ascii=False)}`",
        f"- new gap types: `{json.dumps(delta.get('new_gap_types') or [], ensure_ascii=False)}`",
        f"- regressions: `{json.dumps(delta.get('regressions') or [], ensure_ascii=False)}`",
        "",
    ])
    carry = delta.get("carry_forward") if isinstance(delta.get("carry_forward"), dict) else {}
    lines.extend([
        "## Carry-forward preservation",
        "",
        f"- carried candidates: {carry.get('candidate_count', 0)}",
        f"- carried reproduction packages: {carry.get('reproduction_count', 0)}",
        f"- carried probe ledger entries: {carry.get('probe_ledger_count', 0)}",
        "",
    ])
    sources = delta.get("previous_sources") if isinstance(delta.get("previous_sources"), dict) else {}
    if any(sources.values()):
        lines.extend(["## Previous artifact sources", ""])
        for key, value in sources.items():
            lines.append(f"- {key}: `{value}`")
        lines.append("")
    return "\n".join(lines)



def _runtime_reproduction_gate_ready(package: dict[str, Any]) -> bool:
    gate = package.get("reproduction_readiness_gate") if isinstance(package.get("reproduction_readiness_gate"), dict) else {}
    if gate:
        return bool(gate.get("customer_ready")) and not bool(gate.get("blockers") or [])
    return bool(package.get("customer_ready")) and bool(package.get("reproduction_trace") or [])


def _runtime_package_candidate_ids(pack: dict[str, Any], *, ready_only: bool) -> list[str]:
    ids: list[str] = []
    for package in pack.get("packages") or []:
        if not isinstance(package, dict):
            continue
        if ready_only and not (package.get("customer_ready") is True and _runtime_reproduction_gate_ready(package)):
            continue
        if not ready_only and (package.get("customer_ready") is True and _runtime_reproduction_gate_ready(package)):
            continue
        candidate_id = str(package.get("candidate_id") or "").strip()
        if candidate_id:
            ids.append(candidate_id)
    return _dedupe(ids)


def _runtime_ledger_gap_candidate_ids(ledger: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for entry in ledger.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("customer_ready") is True and not (entry.get("gap_types") or []):
            continue
        candidate_id = str(entry.get("candidate_id") or "").strip()
        if candidate_id:
            ids.append(candidate_id)
    return _dedupe(ids)


def _build_runtime_evidence_promotion_gate(report: dict[str, Any]) -> dict[str, Any]:
    """Decide whether a runtime evidence run may be promoted to customer handoff.

    The remediation loop can now run targeted probes, carry forward proven
    findings, and compute progress deltas.  This gate converts those artifacts
    into a single go/no-go decision so the product does not accidentally market
    a run as customer-ready while P0 gaps, reproduction blockers, or regression
    signals still exist.
    """
    scoreboard = report.get("runtime_evidence_scoreboard") if isinstance(report.get("runtime_evidence_scoreboard"), dict) else {}
    maturity = scoreboard.get("evidence_maturity") if isinstance(scoreboard.get("evidence_maturity"), dict) else {}
    remediation = report.get("runtime_evidence_remediation_plan") if isinstance(report.get("runtime_evidence_remediation_plan"), dict) else {}
    ledger = report.get("runtime_evidence_probe_ledger") if isinstance(report.get("runtime_evidence_probe_ledger"), dict) else {}
    pack = report.get("runtime_customer_reproduction_pack") if isinstance(report.get("runtime_customer_reproduction_pack"), dict) else {}
    progress = report.get("runtime_evidence_progress_delta") if isinstance(report.get("runtime_evidence_progress_delta"), dict) else {}
    carry = report.get("runtime_evidence_carry_forward") if isinstance(report.get("runtime_evidence_carry_forward"), dict) else {}
    rerun = report.get("runtime_rerun_selection") if isinstance(report.get("runtime_rerun_selection"), dict) else {}

    checks = {
        "scoreboard_customer_ready": bool(maturity.get("customer_ready")),
        "scoreboard_maturity_level": maturity.get("level"),
        "execution_integrity_score": scoreboard.get("execution_integrity_score", 0),
        "p0_group_count": int(remediation.get("p0_group_count") or 0),
        "queued_candidate_count": int(remediation.get("queued_candidate_count") or 0),
        "customer_ready_reproduction_count": int(pack.get("customer_ready_reproduction_count") or 0),
        "blocked_reproduction_count": int(pack.get("blocked_reproduction_count") or 0),
        "probe_ledger_evidence_gap_count": int(ledger.get("evidence_gap_probe_count") or 0),
        "probe_ledger_customer_ready_count": int(ledger.get("customer_ready_probe_count") or 0),
        "progress_delta_status": progress.get("status"),
        "progress_regression_count": len(progress.get("regressions") or []),
        "carry_forward_blocked_candidate_count": int(carry.get("blocked_candidate_count") or 0),
        "rerun_missing_candidate_count": len(rerun.get("missing_candidate_ids") or []),
        "targeted_rerun_enabled": bool(rerun.get("enabled")),
    }

    blockers: list[str] = []
    warnings: list[str] = []
    if not checks["scoreboard_customer_ready"]:
        blockers.append("scoreboard_maturity_not_customer_ready")
    if checks["p0_group_count"] > 0:
        blockers.append("p0_runtime_remediation_groups_remaining")
    if checks["queued_candidate_count"] > 0:
        blockers.append("runtime_remediation_rerun_queue_not_empty")
    if checks["customer_ready_reproduction_count"] <= 0:
        blockers.append("no_customer_ready_reproduction_packages")
    if checks["blocked_reproduction_count"] > 0:
        blockers.append("blocked_reproduction_packages_remaining")
    if checks["probe_ledger_evidence_gap_count"] > 0:
        blockers.append("probe_ledger_evidence_gaps_remaining")
    if checks["progress_regression_count"] > 0:
        blockers.append("runtime_evidence_progress_regression_detected")
    if checks["carry_forward_blocked_candidate_count"] > 0:
        blockers.append("carry_forward_blocked_candidate_evidence")
    if checks["rerun_missing_candidate_count"] > 0:
        blockers.append("rerun_manifest_references_missing_candidates")

    if not progress:
        warnings.append("progress_delta_not_built")
    elif progress.get("status") == "no_previous_runtime_evidence_found":
        warnings.append("first_runtime_evidence_baseline_no_previous_delta")
    elif progress.get("status") == "runtime_evidence_unchanged":
        warnings.append("targeted_rerun_did_not_improve_evidence")

    ready_candidate_ids = _runtime_package_candidate_ids(pack, ready_only=True)
    blocked_candidate_ids = _dedupe(
        _runtime_package_candidate_ids(pack, ready_only=False)
        + _runtime_ledger_gap_candidate_ids(ledger)
        + [str(cid) for cid in (rerun.get("missing_candidate_ids") or []) if str(cid).strip()]
    )

    promotion_ready = not blockers and checks["customer_ready_reproduction_count"] > 0
    if promotion_ready:
        status = "customer_ready_runtime_evidence_promotion_approved"
        next_action = "Freeze this run as the customer-ready baseline and use the reproduction pack for customer/developer handoff."
    elif checks["progress_regression_count"] > 0:
        status = "runtime_evidence_promotion_blocked_by_regression"
        next_action = "Stop handoff, inspect the progress delta regressions, and rerun after restoring the previous customer-ready evidence."
    elif checks["p0_group_count"] > 0 or checks["queued_candidate_count"] > 0:
        status = "runtime_evidence_promotion_blocked_by_remediation_queue"
        next_action = "Run the remediation rerun manifest first, then regenerate scoreboard, ledger, reproduction pack, progress delta, and this promotion gate."
    elif checks["customer_ready_reproduction_count"] <= 0:
        status = "runtime_evidence_promotion_blocked_no_customer_ready_findings"
        next_action = "Continue runtime execution until at least one validated finding has a customer-ready reproduction package."
    else:
        status = "runtime_evidence_promotion_blocked"
        next_action = "Resolve the listed blockers before commercial/customer handoff."

    return {
        "engine": "runtime_evidence_promotion_gate_v1_phase95",
        "status": status,
        "project_id": report.get("project_id"),
        "promotion_ready": promotion_ready,
        "customer_ready": promotion_ready,
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
        "checks": checks,
        "approved_customer_ready_candidate_ids": ready_candidate_ids,
        "approved_customer_ready_candidate_count": len(ready_candidate_ids),
        "blocked_candidate_ids": blocked_candidate_ids,
        "blocked_candidate_count": len(blocked_candidate_ids),
        "next_action": next_action,
    }


def _render_runtime_evidence_promotion_gate_markdown(gate: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Promotion Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- status: `{gate.get('status')}`",
        f"- project: `{gate.get('project_id')}`",
        f"- promotion ready: `{gate.get('promotion_ready')}`",
        f"- next action: {gate.get('next_action')}",
        "",
        "## Gate blockers",
        "",
    ]
    blockers = gate.get("blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- none")
    lines.append("")
    warnings = gate.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- `{warning}`")
        lines.append("")
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    if checks:
        lines.extend(["## Checks", "", "| Check | Value |", "|---|---:|"])
        for name, value in checks.items():
            lines.append(f"| `{name}` | `{value}` |")
        lines.append("")
    approved = gate.get("approved_customer_ready_candidate_ids") or []
    blocked = gate.get("blocked_candidate_ids") or []
    lines.extend([
        "## Candidate summary",
        "",
        f"- approved customer-ready candidates: `{json.dumps(approved, ensure_ascii=False)}`",
        f"- blocked candidates: `{json.dumps(blocked, ensure_ascii=False)}`",
        "",
    ])
    return "\n".join(lines)



_RUNTIME_DELIVERY_REQUIRED_OUTPUT_KEYS = [
    "runtime_evidence_scoreboard_json",
    "runtime_evidence_probe_ledger_json",
    "runtime_customer_reproduction_pack_json",
    "runtime_evidence_remediation_plan_json",
    "runtime_evidence_carry_forward_json",
    "runtime_evidence_progress_delta_json",
    "runtime_evidence_promotion_gate_json",
]

_RUNTIME_DELIVERY_OPTIONAL_OUTPUT_KEYS = [
    "runtime_evidence_scoreboard_md",
    "runtime_evidence_probe_ledger_md",
    "runtime_customer_reproduction_pack_md",
    "runtime_evidence_remediation_plan_md",
    "runtime_evidence_carry_forward_md",
    "runtime_evidence_progress_delta_md",
    "runtime_evidence_promotion_gate_md",
]


def _runtime_file_sha256(path_text: str) -> tuple[bool, int, str | None, str | None]:
    if not path_text:
        return False, 0, None, "missing_path"
    try:
        path = Path(path_text)
    except Exception:
        return False, 0, None, "invalid_path"
    if not path.exists() or not path.is_file():
        return False, 0, None, "missing_or_not_file"
    h = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                h.update(chunk)
    except Exception as exc:  # pragma: no cover - defensive filesystem guard
        return False, size, None, f"hash_failed:{type(exc).__name__}"
    return True, size, h.hexdigest(), None


def _runtime_delivery_artifact_entry(outputs: dict[str, Any], key: str, *, required: bool) -> dict[str, Any]:
    path_text = str(outputs.get(key) or "")
    exists, byte_size, sha, error = _runtime_file_sha256(path_text)
    return {
        "artifact_key": key,
        "path": path_text,
        "required": required,
        "exists": exists,
        "byte_size": byte_size,
        "sha256": sha,
        "hash_status": "hashed" if sha else "missing_or_unhashable",
        "hash_error": None if sha else error,
    }


def _runtime_delivery_baseline_id(project_id: Any, artifact_entries: list[dict[str, Any]], approved_candidate_ids: list[str]) -> str:
    material = {
        "project_id": project_id,
        "approved_customer_ready_candidate_ids": sorted(approved_candidate_ids),
        "artifact_hashes": [
            {
                "artifact_key": entry.get("artifact_key"),
                "sha256": entry.get("sha256"),
                "byte_size": entry.get("byte_size"),
            }
            for entry in artifact_entries
            if entry.get("sha256")
        ],
    }
    digest = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return "qbruntime-" + digest[:20]


def _build_runtime_evidence_customer_delivery_manifest(report: dict[str, Any]) -> dict[str, Any]:
    """Freeze a promoted runtime evidence run into a customer delivery manifest.

    The promotion gate answers whether the current run is customer-ready.  This
    manifest gives that decision an auditable handoff surface: every runtime
    evidence artifact needed to defend the decision is hashed, required artifact
    gaps are explicit, and the approved customer-ready candidate set is fixed in
    one deterministic baseline id.
    """
    outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
    gate = report.get("runtime_evidence_promotion_gate") if isinstance(report.get("runtime_evidence_promotion_gate"), dict) else {}
    pack = report.get("runtime_customer_reproduction_pack") if isinstance(report.get("runtime_customer_reproduction_pack"), dict) else {}
    ledger = report.get("runtime_evidence_probe_ledger") if isinstance(report.get("runtime_evidence_probe_ledger"), dict) else {}
    progress = report.get("runtime_evidence_progress_delta") if isinstance(report.get("runtime_evidence_progress_delta"), dict) else {}

    artifact_entries: list[dict[str, Any]] = []
    for key in _RUNTIME_DELIVERY_REQUIRED_OUTPUT_KEYS:
        artifact_entries.append(_runtime_delivery_artifact_entry(outputs, key, required=True))
    for key in _RUNTIME_DELIVERY_OPTIONAL_OUTPUT_KEYS:
        if outputs.get(key):
            artifact_entries.append(_runtime_delivery_artifact_entry(outputs, key, required=False))

    missing_required = [entry for entry in artifact_entries if entry.get("required") and not entry.get("sha256")]
    approved_candidate_ids = [str(cid) for cid in (gate.get("approved_customer_ready_candidate_ids") or []) if str(cid).strip()]
    blocked_candidate_ids = [str(cid) for cid in (gate.get("blocked_candidate_ids") or []) if str(cid).strip()]
    promotion_ready = bool(gate.get("promotion_ready"))

    blockers: list[str] = []
    if not promotion_ready:
        blockers.append("runtime_promotion_gate_not_approved")
    if missing_required:
        blockers.append("required_runtime_delivery_artifacts_missing_or_unhashable")
    if not approved_candidate_ids:
        blockers.append("no_approved_customer_ready_candidates")

    if not blockers:
        status = "customer_ready_runtime_delivery_manifest_ready"
        next_action = "Freeze these artifact hashes with the customer handoff package and use the approved candidate list as the delivery baseline."
    elif "required_runtime_delivery_artifacts_missing_or_unhashable" in blockers:
        status = "runtime_delivery_manifest_missing_required_artifacts"
        next_action = "Regenerate runtime evidence artifacts before freezing a customer delivery baseline."
    else:
        status = "runtime_delivery_manifest_blocked_by_promotion_gate"
        next_action = "Resolve the promotion gate blockers before freezing a customer delivery baseline."

    baseline_id = _runtime_delivery_baseline_id(report.get("project_id"), artifact_entries, approved_candidate_ids)
    return {
        "engine": "runtime_evidence_customer_delivery_manifest_v1_phase95",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "delivery_baseline_id": baseline_id,
        "customer_ready": not blockers,
        "promotion_gate_status": gate.get("status"),
        "promotion_ready": promotion_ready,
        "blockers": _dedupe(blockers),
        "promotion_gate_blockers": gate.get("blockers") or [],
        "approved_customer_ready_candidate_ids": _dedupe(approved_candidate_ids),
        "approved_customer_ready_candidate_count": len(_dedupe(approved_candidate_ids)),
        "blocked_candidate_ids": _dedupe(blocked_candidate_ids),
        "blocked_candidate_count": len(_dedupe(blocked_candidate_ids)),
        "customer_ready_reproduction_count": int(pack.get("customer_ready_reproduction_count") or 0),
        "probe_ledger_customer_ready_count": int(ledger.get("customer_ready_probe_count") or 0),
        "progress_delta_status": progress.get("status"),
        "progress_regression_count": len(progress.get("regressions") or []),
        "required_artifact_count": len(_RUNTIME_DELIVERY_REQUIRED_OUTPUT_KEYS),
        "hashed_required_artifact_count": sum(1 for entry in artifact_entries if entry.get("required") and entry.get("sha256")),
        "missing_required_artifact_count": len(missing_required),
        "missing_required_artifacts": [
            {
                "artifact_key": entry.get("artifact_key"),
                "path": entry.get("path"),
                "reason": entry.get("hash_error") or entry.get("hash_status"),
            }
            for entry in missing_required
        ],
        "artifact_manifest": artifact_entries,
        "artifact_count": len(artifact_entries),
        "hashed_artifact_count": sum(1 for entry in artifact_entries if entry.get("sha256")),
        "next_action": next_action,
        "customer_safe_note": "This manifest stores artifact hashes, paths and candidate ids only; it does not include raw tokens, cookies or customer secrets.",
    }


def _render_runtime_evidence_customer_delivery_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Customer Delivery Manifest",
        "",
        f"- engine: `{manifest.get('engine')}`",
        f"- status: `{manifest.get('status')}`",
        f"- project: `{manifest.get('project_id')}`",
        f"- delivery baseline id: `{manifest.get('delivery_baseline_id')}`",
        f"- customer ready: `{manifest.get('customer_ready')}`",
        f"- promotion gate: `{manifest.get('promotion_gate_status')}` / ready `{manifest.get('promotion_ready')}`",
        f"- approved candidates: `{json.dumps(manifest.get('approved_customer_ready_candidate_ids') or [], ensure_ascii=False)}`",
        f"- blocked candidates: `{json.dumps(manifest.get('blocked_candidate_ids') or [], ensure_ascii=False)}`",
        f"- required artifacts hashed: {manifest.get('hashed_required_artifact_count')}/{manifest.get('required_artifact_count')}",
        f"- next action: {manifest.get('next_action')}",
        "",
        "## Blockers",
        "",
    ]
    blockers = manifest.get("blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- none")
    lines.append("")

    missing = [item for item in (manifest.get("missing_required_artifacts") or []) if isinstance(item, dict)]
    if missing:
        lines.extend(["## Missing required runtime artifacts", ""])
        for item in missing:
            lines.append(f"- `{item.get('artifact_key')}` — {item.get('reason')} — `{item.get('path')}`")
        lines.append("")

    lines.extend(["## Runtime evidence artifact hashes", "", "| Artifact | Required | Status | Bytes | SHA256 |", "|---|---:|---|---:|---|"])
    for entry in manifest.get("artifact_manifest") or []:
        if not isinstance(entry, dict):
            continue
        lines.append(
            "| "
            + " | ".join([
                f"`{entry.get('artifact_key')}`",
                "yes" if entry.get("required") else "no",
                str(entry.get("hash_status") or "-"),
                str(entry.get("byte_size") or 0),
                f"`{entry.get('sha256') or '-'}`",
            ])
            + " |"
        )
    lines.extend(["", f"> {manifest.get('customer_safe_note')}"])
    return "\n".join(lines)



def _delivery_manifest_verification_source(config: dict[str, Any], report: dict[str, Any]) -> tuple[dict[str, Any] | None, str, str | None, str | None]:
    """Resolve the delivery manifest to verify.

    By default we verify the manifest just produced in this report.  Targeted
    reruns can also pass a previously frozen manifest path; this lets the engine
    prove that carry-forward/customer-ready evidence still matches the hashed
    delivery baseline before trusting it in a later handoff.
    """
    configured_path = None
    for key in (
        "runtime_evidence_customer_delivery_manifest_path",
        "runtime_delivery_manifest_path",
        "previous_runtime_evidence_customer_delivery_manifest_path",
        "previous_runtime_delivery_manifest_path",
    ):
        value = config.get(key)
        if value:
            configured_path = str(value)
            break
    if configured_path:
        try:
            manifest = _read_json(configured_path)
        except Exception as exc:  # pragma: no cover - defensive file guard
            return None, "configured_manifest_path", configured_path, f"manifest_load_failed:{type(exc).__name__}"
        if not isinstance(manifest, dict):
            return None, "configured_manifest_path", configured_path, "manifest_not_object"
        return manifest, "configured_manifest_path", configured_path, None

    manifest = report.get("runtime_evidence_customer_delivery_manifest")
    if isinstance(manifest, dict):
        return manifest, "current_report_manifest", None, None
    return None, "current_report_manifest", None, "manifest_missing_from_report"


def _verify_delivery_artifact_entry(entry: dict[str, Any]) -> dict[str, Any]:
    key = str(entry.get("artifact_key") or "")
    path_text = str(entry.get("path") or "")
    expected_sha = str(entry.get("sha256") or "")
    expected_size_raw = entry.get("byte_size")
    try:
        expected_size = int(expected_size_raw) if expected_size_raw not in (None, "") else None
    except Exception:
        expected_size = None

    exists, actual_size, actual_sha, error = _runtime_file_sha256(path_text)
    issues: list[str] = []
    if not expected_sha:
        issues.append("expected_sha256_missing")
    if not actual_sha:
        issues.append(error or "artifact_missing_or_unhashable")
    elif expected_sha and actual_sha != expected_sha:
        issues.append("sha256_mismatch")
    if expected_size is not None and actual_sha and actual_size != expected_size:
        issues.append("byte_size_mismatch")

    if not issues:
        status = "verified"
    elif "sha256_mismatch" in issues or "byte_size_mismatch" in issues:
        status = "artifact_tamper_or_drift_detected"
    elif "expected_sha256_missing" in issues:
        status = "expected_hash_missing"
    else:
        status = "artifact_missing_or_unhashable"

    return {
        "artifact_key": key,
        "path": path_text,
        "required": bool(entry.get("required")),
        "expected_sha256": expected_sha or None,
        "actual_sha256": actual_sha,
        "expected_byte_size": expected_size,
        "actual_byte_size": actual_size,
        "exists": bool(exists),
        "status": status,
        "issues": issues,
    }


def _build_runtime_evidence_delivery_manifest_verification(config: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Verify that a frozen runtime delivery manifest still matches its artifacts.

    The customer delivery manifest freezes the artifact hashes.  This verifier is
    the tamper-evident read-back step: it re-hashes the referenced artifacts,
    checks the deterministic baseline id, and blocks promotion/carry-forward
    reuse when any required evidence file has drifted or disappeared.
    """
    manifest, source, manifest_path, load_error = _delivery_manifest_verification_source(config, report)
    if manifest is None:
        return {
            "engine": "runtime_evidence_delivery_manifest_verification_v1_phase95",
            "status": "runtime_delivery_manifest_verification_failed_to_load",
            "verified": False,
            "source": source,
            "manifest_path": manifest_path,
            "blockers": [load_error or "manifest_missing"],
            "warnings": [],
            "artifact_results": [],
            "artifact_verification_count": 0,
            "verified_artifact_count": 0,
            "failed_artifact_count": 0,
            "failed_required_artifact_count": 0,
            "baseline_id_expected": None,
            "baseline_id_recomputed": None,
            "tamper_evident": True,
            "next_action": "Provide a readable runtime evidence customer delivery manifest before verifying or reusing a delivery baseline.",
        }

    artifact_manifest = [entry for entry in (manifest.get("artifact_manifest") or []) if isinstance(entry, dict)]
    artifact_results = [_verify_delivery_artifact_entry(entry) for entry in artifact_manifest]
    failed_required = [item for item in artifact_results if item.get("required") and item.get("status") != "verified"]
    failed_optional = [item for item in artifact_results if not item.get("required") and item.get("status") != "verified"]

    approved_candidate_ids = [str(cid) for cid in (manifest.get("approved_customer_ready_candidate_ids") or []) if str(cid).strip()]
    expected_baseline_id = manifest.get("delivery_baseline_id")
    recomputed_baseline_id = _runtime_delivery_baseline_id(manifest.get("project_id"), artifact_manifest, approved_candidate_ids) if artifact_manifest else None

    current_gate = report.get("runtime_evidence_promotion_gate") if isinstance(report.get("runtime_evidence_promotion_gate"), dict) else {}
    current_gate_ids = [str(cid) for cid in (current_gate.get("approved_customer_ready_candidate_ids") or []) if str(cid).strip()]

    blockers: list[str] = []
    warnings: list[str] = []
    if not bool(manifest.get("customer_ready")):
        blockers.append("delivery_manifest_not_customer_ready")
    if not artifact_manifest:
        blockers.append("delivery_manifest_has_no_artifact_manifest")
    if failed_required:
        blockers.append("required_delivery_artifact_hash_verification_failed")
    if recomputed_baseline_id and expected_baseline_id and recomputed_baseline_id != expected_baseline_id:
        blockers.append("delivery_baseline_id_mismatch")
    if not expected_baseline_id:
        blockers.append("delivery_baseline_id_missing")
    if not approved_candidate_ids:
        blockers.append("approved_customer_ready_candidate_ids_missing")
    if source == "current_report_manifest" and current_gate_ids and sorted(current_gate_ids) != sorted(approved_candidate_ids):
        blockers.append("promotion_gate_candidate_set_mismatch")
    if failed_optional:
        warnings.append("optional_delivery_artifact_hash_verification_failed")

    tamper_evident = bool(failed_required or failed_optional or (recomputed_baseline_id and expected_baseline_id and recomputed_baseline_id != expected_baseline_id))
    if blockers:
        status = "runtime_delivery_manifest_verification_failed"
        next_action = "Regenerate or repair the runtime evidence delivery package before relying on this delivery baseline."
    elif warnings:
        status = "runtime_delivery_manifest_verified_with_warnings"
        next_action = "Required delivery evidence is intact; refresh optional artifacts before final customer handoff if they are included."
    else:
        status = "runtime_delivery_manifest_verified"
        next_action = "Delivery manifest and required artifact hashes are intact; this baseline can be reused or handed off."

    return {
        "engine": "runtime_evidence_delivery_manifest_verification_v1_phase95",
        "status": status,
        "verified": not blockers,
        "source": source,
        "manifest_path": manifest_path,
        "project_id": manifest.get("project_id"),
        "delivery_baseline_id": expected_baseline_id,
        "baseline_id_expected": expected_baseline_id,
        "baseline_id_recomputed": recomputed_baseline_id,
        "baseline_id_matches": bool(expected_baseline_id and recomputed_baseline_id == expected_baseline_id),
        "customer_ready": bool(manifest.get("customer_ready")),
        "approved_customer_ready_candidate_ids": _dedupe(approved_candidate_ids),
        "approved_customer_ready_candidate_count": len(_dedupe(approved_candidate_ids)),
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
        "artifact_results": artifact_results,
        "artifact_verification_count": len(artifact_results),
        "verified_artifact_count": sum(1 for item in artifact_results if item.get("status") == "verified"),
        "failed_artifact_count": sum(1 for item in artifact_results if item.get("status") != "verified"),
        "failed_required_artifact_count": len(failed_required),
        "failed_required_artifacts": [item for item in failed_required],
        "failed_optional_artifact_count": len(failed_optional),
        "tamper_evident": tamper_evident,
        "next_action": next_action,
        "customer_safe_note": "Verification re-hashes runtime evidence artifact files and compares hashes only; it does not expose secrets or raw authorization material.",
    }


def _render_runtime_evidence_delivery_manifest_verification_markdown(verification: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Delivery Manifest Verification",
        "",
        f"- engine: `{verification.get('engine')}`",
        f"- status: `{verification.get('status')}`",
        f"- verified: `{verification.get('verified')}`",
        f"- source: `{verification.get('source')}`",
        f"- manifest path: `{verification.get('manifest_path') or '-'}`",
        f"- delivery baseline id: `{verification.get('delivery_baseline_id')}`",
        f"- baseline id matches: `{verification.get('baseline_id_matches')}`",
        f"- approved candidates: `{json.dumps(verification.get('approved_customer_ready_candidate_ids') or [], ensure_ascii=False)}`",
        f"- artifacts verified: {verification.get('verified_artifact_count')}/{verification.get('artifact_verification_count')}",
        f"- required artifact failures: {verification.get('failed_required_artifact_count')}",
        f"- tamper evident: `{verification.get('tamper_evident')}`",
        f"- next action: {verification.get('next_action')}",
        "",
        "## Blockers",
        "",
    ]
    blockers = verification.get("blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- none")
    lines.append("")

    warnings = verification.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- `{warning}`")
        lines.append("")

    failed = [item for item in (verification.get("artifact_results") or []) if isinstance(item, dict) and item.get("status") != "verified"]
    if failed:
        lines.extend(["## Failed artifact checks", "", "| Artifact | Required | Status | Issues |", "|---|---:|---|---|"])
        for item in failed:
            lines.append(
                "| "
                + " | ".join([
                    f"`{item.get('artifact_key')}`",
                    "yes" if item.get("required") else "no",
                    f"`{item.get('status')}`",
                    f"`{json.dumps(item.get('issues') or [], ensure_ascii=False)}`",
                ])
                + " |"
            )
        lines.append("")

    lines.extend(["## Artifact verification results", "", "| Artifact | Required | Status | Expected SHA256 | Actual SHA256 |", "|---|---:|---|---|---|"])
    for item in verification.get("artifact_results") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            "| "
            + " | ".join([
                f"`{item.get('artifact_key')}`",
                "yes" if item.get("required") else "no",
                f"`{item.get('status')}`",
                f"`{item.get('expected_sha256') or '-'}`",
                f"`{item.get('actual_sha256') or '-'}`",
            ])
            + " |"
        )
    lines.extend(["", f"> {verification.get('customer_safe_note')}"])
    return "\n".join(lines)


