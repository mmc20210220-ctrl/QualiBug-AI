# -*- coding: utf-8 -*-
"""Verified Discovery Archive — 已验证发现跨 run 累积（发现单调性）。

产品原则：只要目标系统代码未修复，已验证发现的 bug 不得因单次扫描的
覆盖波动（执行排序、预算、轮次随机性）而丢失。每个 run 正式交付的
defect finding（gate_passed + 复现成功）按稳定身份写入档案；后续 run 的
输出 findings = 本 run 新交付 ∪ 档案中未退休的历史发现（带
``archive_entry`` 标记与原始证据）。只有「目标已修复」信号（同一身份的
操作被正常执行且不再产生 violation，连续多 run 确认）才退休该条目——
此时"没发现"才是正常的。

通用性：身份归一全部基于 finding 自身的结构字段（canonical 身份 /
title 的操作+路径形态 / 风险族 / 断言类别），不含任何行业或基准特定词汇，
换任何目标系统同样适用。档案存放于
``platform_workspace/<project>/defect_discovery/verified_discovery_archive.json``
（git-ignored 工作区，非仓库产物）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

_ARCHIVE_SCHEMA = "qualibug.verified-discovery-archive.v1"
_RETIRE_THRESHOLD = 3  # 连续 N 个 run 检测到「目标可能已修复」信号才退休（可配置）

# 运行时实例值（UUID、时间戳、nonce 后缀）在身份归一中被剥离。
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NONCE_RE = re.compile(r"-[0-9a-f]{8,}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_identity_text(value: str) -> str:
    """Strip runtime instance values from an identity-bearing string."""
    text = _text(value).lower()
    text = _UUID_RE.sub("{id}", text)
    text = _NONCE_RE.sub("", text)
    return re.sub(r"\s+", " ", text)


def finding_stable_identity(finding: dict[str, Any]) -> str:
    """Stable cross-run identity of a delivered defect finding.

    Priority: canonical_defect_id (canonical registry's receipt-derived
    identity) > canonical_identity_fingerprint > fallback fingerprint over
    the finding's own structure (normalized title / risk family / category /
    obligation identity). Runtime instance values (UUIDs, nonces) never
    participate, so the same target defect yields the same identity across
    runs even when the explored rows differ.
    """
    row = dict(finding)
    canonical = _text(row.get("canonical_defect_id"))
    if canonical:
        return canonical
    fingerprint = _text(row.get("canonical_identity_fingerprint"))
    if fingerprint:
        return "cdef_fp_" + fingerprint[:32]
    title = _normalize_identity_text(_text(row.get("title")))
    fallback = {
        "title": title,
        "risk_family": _text(row.get("risk_family")).lower(),
        "category": _text(row.get("category")).lower(),
        "obligation": _normalize_identity_text(
            _text(row.get("selected_obligation_id") or row.get("obligation_id"))
        ),
    }
    return "cdef_fb_" + _sha256(fallback)[:32]


def archive_path(project: str, root: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", _text(project)) or "default"
    return root / "platform_workspace" / safe / "defect_discovery" / (
        "verified_discovery_archive.json"
    )


def load_verified_discovery_archive(
    project: str,
    root: Path,
) -> dict[str, Any]:
    path = archive_path(project, root)
    if not path.exists():
        return {
            "schema_version": _ARCHIVE_SCHEMA,
            "project": project,
            "entries": {},
            "retired": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        # Fail open to a fresh archive with a visible receipt note rather
        # than blocking the whole scan on archive corruption.
        return {
            "schema_version": _ARCHIVE_SCHEMA,
            "project": project,
            "entries": {},
            "retired": {},
            "load_failure": f"{type(exc).__name__}:{str(exc)[:120]}",
        }
    if not isinstance(payload, dict):
        return {"schema_version": _ARCHIVE_SCHEMA, "project": project,
                "entries": {}, "retired": {}, "load_failure": "not_object"}
    payload.setdefault("schema_version", _ARCHIVE_SCHEMA)
    payload.setdefault("entries", {})
    payload.setdefault("retired", {})
    return payload


def save_verified_discovery_archive(
    project: str,
    root: Path,
    archive: dict[str, Any],
) -> Path:
    path = archive_path(project, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def _is_delivered_defect(finding: dict[str, Any]) -> bool:
    return bool(
        finding.get("gate_passed") is True
        and _text(finding.get("customer_delivery_status")) == "defect"
        and _text(finding.get("bug_status")) != "not_reproduced"
    )


def merge_run_deliveries(
    archive: dict[str, Any],
    *,
    run_id: str,
    campaign_id: str,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge this run's delivered defect findings into the archive.

    New identities are appended (``first_verified_run`` = this run); known
    identities refresh ``last_verified_run`` and carry the newest finding
    snapshot (evidence / reproduction / delivery receipts) forward. Nothing
    already verified is ever dropped here — dropping is only legal through
    the target-fix retirement path.
    """
    entries = archive.setdefault("entries", {})
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for finding in findings:
        if not _is_delivered_defect(finding):
            continue
        identity = finding_stable_identity(finding)
        existing = entries.get(identity)
        if existing is None:
            entries[identity] = {
                "identity": identity,
                "first_verified_run": run_id,
                "first_verified_at": now,
                "last_verified_run": run_id,
                "last_verified_at": now,
                "campaign_id": campaign_id,
                "fix_signal_count": 0,
                "finding": finding,
            }
        else:
            existing["last_verified_run"] = run_id
            existing["last_verified_at"] = now
            existing["campaign_id"] = campaign_id
            existing["finding"] = finding
    return archive


def apply_archive_to_run(
    archive: dict[str, Any],
    *,
    run_id: str,
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the run's output findings = this run's deliveries ∪ archive.

    This run's own findings win on identity collision (they carry the newest
    evidence); archive entries not re-delivered this run are appended with
    ``archive_entry=true`` plus provenance (first/last verified run) so the
    consumer can distinguish freshly discovered findings from held-over
    verified ones. Retired entries are excluded — for those, not finding the
    bug is the expected outcome (target fixed).
    """
    merged: dict[str, dict[str, Any]] = {}
    for finding in findings:
        merged[finding_stable_identity(finding)] = finding
    held = 0
    for identity, entry in (archive.get("entries") or {}).items():
        if identity in merged:
            continue
        if entry.get("retired") or identity in (archive.get("retired") or {}):
            continue
        finding = dict(entry.get("finding") or {})
        finding["archive_entry"] = True
        finding["first_verified_run"] = entry.get("first_verified_run")
        finding["last_verified_run"] = entry.get("last_verified_run")
        merged[identity] = finding
        held += 1
    output = list(merged.values())
    receipt = {
        "schema_version": _ARCHIVE_SCHEMA,
        "run_id": run_id,
        "run_delivered": len(findings),
        "archive_held": held,
        "total_output": len(output),
        "retired_count": len(archive.get("retired") or {}),
    }
    return output, receipt


def record_target_fix_signals(
    archive: dict[str, Any],
    *,
    run_id: str,
    fix_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Record target-fix signals for archive identities (phase-2 interface).

    A fix signal is an observed non-violation for an identity whose operation
    was actually exercised this run (control + treatment both executed, no
    violation, interface still reachable) — NOT merely "the finding was not
    regenerated" (coverage fluctuation must never count as a fix signal).
    When ``fix_signal_count`` reaches ``_RETIRE_THRESHOLD`` consecutive runs
    the entry retires: the bug is no longer present, so its absence from the
    findings is the expected outcome. Retired entries stay in ``retired``
    with their history for auditability.
    """
    entries = archive.setdefault("entries", {})
    retired = archive.setdefault("retired", {})
    moved = 0
    for identity, entry in (fix_evidence.get("identities") or {}).items():
        if identity not in entries or entries[identity].get("retired"):
            continue
        count = int(entries[identity].get("fix_signal_count") or 0) + 1
        entries[identity]["fix_signal_count"] = count
        entries[identity]["last_fix_signal_run"] = run_id
        entries[identity]["last_fix_signal_evidence"] = fix_evidence.get(
            "evidence", {}
        ).get(identity)
        if count >= _RETIRE_THRESHOLD:
            retired[identity] = dict(entries[identity])
            retired[identity]["retired_run"] = run_id
            entries[identity]["retired"] = True
            moved += 1
    return {
        "schema_version": _ARCHIVE_SCHEMA,
        "run_id": run_id,
        "signals_recorded": len(fix_evidence.get("identities") or {}),
        "retired_now": moved,
        "retire_threshold": _RETIRE_THRESHOLD,
    }
