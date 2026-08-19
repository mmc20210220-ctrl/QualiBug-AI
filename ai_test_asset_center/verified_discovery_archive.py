# -*- coding: utf-8 -*-
"""Verified Discovery Archive — 已验证发现跨 run 累积（发现单调性）。

产品原则：只要目标系统代码未修复，已验证发现的 bug 不得因单次扫描的
覆盖波动（执行排序、预算、轮次随机性）而丢失。每个 run 正式交付的
defect finding（gate_passed + 复现成功）按稳定身份写入档案；后续 run 的
输出 findings = 本 run 新交付 ∪ 档案中未退休的历史发现（带
``archive_entry`` 标记与原始证据）。只有「目标已修复」信号（同一身份的
操作被正常执行且不再产生 violation，连续多 run 确认）才退休该条目——
此时"没发现"才是正常的。

角色变体聚合（distribution balance）：档案身份 = 角色无关聚合键
（风险族 + 断言类别 + 归一化操作 + 违反形态），角色是证据不是身份——
同一缺陷面的 buyer/seller/finance/auditor 变体共享一个档案条目，与
canonical registry 的聚合语义一致。旧档案（按角色相关 canonical id
归档）在加载时做一次幂等兼容迁移（``migrate_archive_for_aggregation``），
旧条目按自身 finding 快照重算聚合键，全部角色变体折叠为一条，与新 run
的聚合交付对齐而不是重复。

通用性：身份归一全部基于 finding 自身的结构字段（操作+路径形态 /
断言类别 / 违反形态 / 风险族），不含任何行业或基准特定词汇，
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

# 档案身份前缀：角色变体聚合身份（canonical identity 的跨 run 稳定聚合键）。
_AGG_IDENTITY_PREFIX = "cdef_agg_"

# 标题中的操作捕获：`[ContractOracle] <kind>: [role] METHOD /path`。
_TITLE_OPERATION_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _normalize_operation_path(path: str) -> str:
    """Normalize an operation path for aggregation (industry-agnostic).

    Mirrors the canonical identity locator normalization: query strings are
    dropped, UUID / nonce / numeric / test-id segments collapse to ``{id}`` so
    the same interface yields the same path across runs and role variants.
    """
    text = _text(path).split("?", 1)[0].rstrip("/")
    if not text:
        return ""
    text = _UUID_RE.sub("{id}", text)
    text = _NONCE_RE.sub("", text)
    segments: list[str] = []
    for segment in text.split("/"):
        if not segment:
            continue
        if segment.isdigit():
            segment = "{id}"
        elif re.fullmatch(r"(?i)qb[_-]test[_-].+", segment):
            segment = "{id}"
        elif re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9]*", segment) and len(segment) >= 3:
            segment = "{id}"
        segments.append(segment)
    return "/".join(segments)


def _aggregation_value_digest(value: Any, depth: int = 0) -> Any:
    """Stable semantic digest of an assertion expected/actual value.

    Role-variant aggregation semantics: the violation shape is what the
    assertion compared (booleans as-is, numbers by class, strings by
    normalized digest, nested containers recursively), never run-specific
    instance values. Generic — no industry or benchmark vocabulary.
    """
    if depth > 4:
        return {"type": "truncated"}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 0:
            return {"type": "number", "class": "zero"}
        return {"type": "number", "class": "positive" if value > 0 else "negative"}
    if isinstance(value, str):
        return {"type": "string", "digest": _sha256(_normalize_identity_text(value))}
    if isinstance(value, list):
        return {
            "type": "list",
            "items": [
                _aggregation_value_digest(item, depth + 1)
                for item in value[:12]
            ],
        }
    if isinstance(value, dict):
        entries = [
            {
                "key": _normalize_identity_text(key),
                "value": _aggregation_value_digest(item, depth + 1),
            }
            for key, item in sorted(
                value.items(),
                key=lambda pair: _normalize_identity_text(pair[0]),
            )[:24]
        ]
        return {"type": "object", "entries": entries}
    return {"type": type(value).__name__}


def _finding_assertion_kind(finding: dict[str, Any]) -> str:
    row = dict(finding)
    evidence = _dict(row.get("evidence"))
    assertion = _dict(evidence.get("assertion"))
    kind = _normalize_identity_text(assertion.get("kind"))
    if not kind:
        for failed in _list(row.get("failed_assertions")):
            if isinstance(failed, dict):
                kind = _normalize_identity_text(failed.get("kind"))
                if kind:
                    break
    if not kind:
        kind = _normalize_identity_text(row.get("category"))
    if not kind:
        kind = _normalize_identity_text(row.get("risk_family"))
    return kind


def _finding_operation(finding: dict[str, Any]) -> tuple[str, str]:
    """Return (verb, normalized path) from the finding's own structure."""
    row = dict(finding)
    reproduction = _dict(row.get("reproduction"))
    verb = _text(reproduction.get("method")).upper()
    path = _text(reproduction.get("path"))
    if not verb or not path:
        match = _TITLE_OPERATION_RE.search(_text(row.get("title")))
        if match:
            verb = match.group(1).upper()
            path = match.group(2)
    return verb, _normalize_operation_path(path)


def _finding_violation_shape(finding: dict[str, Any]) -> str:
    """Stable digest of the assertion's expected/actual violation shape."""
    row = dict(finding)
    evidence = _dict(row.get("evidence"))
    assertion = _dict(evidence.get("assertion"))
    expected = assertion.get("expected")
    actual = assertion.get("actual")
    if expected is None and actual is None:
        for failed in _list(row.get("failed_assertions")):
            if not isinstance(failed, dict):
                continue
            expected = expected if expected is not None else failed.get("expected")
            actual = actual if actual is not None else failed.get("actual")
    return _sha256({
        "expected": _aggregation_value_digest(expected),
        "actual": _aggregation_value_digest(actual),
    })


def derive_aggregation_key(finding: dict[str, Any]) -> str:
    """Cross-run stable, role-invariant aggregation key of a finding.

    Components: assertion kind, normalized verb+path, and the violation shape
    (expected/actual semantic digest) — exactly the canonical registry's
    aggregation dimensions (interface + assertion kind + violation shape).
    The actor role is deliberately absent, and so is risk_family: the same
    defect surface can be compiled under different risk families across
    role variants (authorization vs visibility vs isolation obligations all
    probe the same owner/tenant visibility leak), so the family would split
    one defect surface into several archive identities. Returns "" when the
    finding carries none of the structural evidence needed.
    """
    row = dict(finding)
    kind = _finding_assertion_kind(row)
    verb, path = _finding_operation(row)
    if not kind or not verb or not path:
        return ""
    return "|".join((
        kind,
        f"{verb} {path}",
        _finding_violation_shape(row),
    ))


def finding_stable_identity(finding: dict[str, Any]) -> str:
    """Stable cross-run identity of a delivered defect finding.

    Priority: role-variant aggregation identity (derived from the finding's
    own structure — operation + assertion kind + violation shape, actor
    role-free) > canonical_defect_id > fallback fingerprint over the
    finding's own structure. Runtime instance values (UUIDs, nonces) never
    participate, and the actor role never participates, so every role variant
    of the same defect surface yields the same identity across runs — the
    aggregation-key semantics that the canonical registry now applies.
    """
    row = dict(finding)
    key = derive_aggregation_key(finding)
    if key:
        return _AGG_IDENTITY_PREFIX + _sha256(key)[:32]
    canonical = _text(row.get("canonical_defect_id"))
    if canonical:
        return canonical
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


def _merge_archive_entries(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge entries that collapse onto one aggregation identity.

    The newest finding snapshot (by last_verified_at) wins while the earliest
    first_verified provenance is preserved, so a migrated multi-role entry
    keeps full history and the strongest evidence. Deterministic ordering.
    """
    ordered = sorted(
        candidates,
        key=lambda entry: (
            _text(entry.get("last_verified_at")),
            _text(entry.get("identity")),
        ),
    )
    merged = dict(ordered[-1])
    merged["identity"] = _text(ordered[-1].get("identity"))
    merged.pop("_source_identity", None)
    first = min(
        ordered,
        key=lambda entry: (
            _text(entry.get("first_verified_run")),
            _text(entry.get("first_verified_at")),
        ),
    )
    merged["first_verified_run"] = first.get("first_verified_run")
    merged["first_verified_at"] = first.get("first_verified_at")
    merged["fix_signal_count"] = max(
        int(entry.get("fix_signal_count") or 0) for entry in ordered
    )
    return merged


def migrate_archive_for_aggregation(
    archive: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One-time archive compatibility migration to aggregation identities.

    Pre-aggregation runs archived under per-role canonical defect ids
    (the concrete treatment actor class was identity-defining). Role-variant
    aggregation removed the actor role from identity, so every role variant
    of one defect surface now shares one aggregation identity — and old
    entries must be re-keyed to it or they would reappear as duplicates next
    to the aggregated delivery of the same defect. Entries and retired rows
    are re-keyed by deriving the aggregation identity from their stored
    finding snapshots; entries that collapse keep the newest finding while
    the earliest first_verified_run is preserved. Re-running is idempotent:
    an already migrated key re-derives to itself.
    """
    payload = dict(archive)
    entries = dict(_dict(payload.get("entries")))
    retired = dict(_dict(payload.get("retired")))
    if not entries and not retired:
        return payload, {
            "schema_version": _ARCHIVE_SCHEMA,
            "migrated_entries": 0,
            "collapsed_entries": 0,
            "unmigrated_entries": 0,
            "migrated_retired": 0,
            "collapsed_retired": 0,
        }
    rekeyed_entries: dict[str, list[dict[str, Any]]] = {}
    rekeyed_retired: dict[str, list[dict[str, Any]]] = {}
    unmigrated = 0
    for identity, entry in entries.items():
        new_id = finding_stable_identity(_dict(entry).get("finding") or {})
        if not new_id:
            unmigrated += 1
            new_id = _text(identity)
        row = dict(_dict(entry))
        row["identity"] = new_id
        row["_source_identity"] = _text(identity)
        rekeyed_entries.setdefault(_text(new_id), []).append(row)
    for identity, entry in retired.items():
        new_id = finding_stable_identity(_dict(entry).get("finding") or {})
        if not new_id:
            new_id = _text(identity)
        row = dict(_dict(entry))
        row["identity"] = new_id
        row["_source_identity"] = _text(identity)
        rekeyed_retired.setdefault(_text(new_id), []).append(row)

    def _migrated(rows: list[dict[str, Any]]) -> int:
        return sum(
            1
            for row in rows
            if _text(row.get("_source_identity")) != _text(row.get("identity"))
        )

    def _collapsed(rows: list[dict[str, Any]]) -> int:
        return max(0, len(rows) - 1)

    payload["entries"] = {
        new_id: _merge_archive_entries(rows)
        for new_id, rows in rekeyed_entries.items()
    }
    payload["retired"] = {
        new_id: _merge_archive_entries(rows)
        for new_id, rows in rekeyed_retired.items()
    }
    receipt = {
        "schema_version": _ARCHIVE_SCHEMA,
        "migrated_entries": sum(_migrated(rows) for rows in rekeyed_entries.values()),
        "migrated_retired": sum(_migrated(rows) for rows in rekeyed_retired.values()),
        "collapsed_entries": sum(_collapsed(rows) for rows in rekeyed_entries.values()),
        "collapsed_retired": sum(_collapsed(rows) for rows in rekeyed_retired.values()),
        "unmigrated_entries": unmigrated,
        "note": (
            "legacy per-role canonical ids re-keyed to the role-invariant "
            "aggregation identity; re-running is idempotent"
        ),
    }
    payload["aggregation_migration_receipt"] = receipt
    return payload, receipt


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
    # Role-variant aggregation compatibility: legacy archives keyed by
    # per-role canonical defect ids are re-keyed to the role-invariant
    # aggregation identity at load time, so old entries align with new-run
    # aggregated deliveries instead of duplicating them. Idempotent.
    migrated, _receipt = migrate_archive_for_aggregation(payload)
    return migrated


def save_verified_discovery_archive(
    project: str,
    root: Path,
    archive: dict[str, Any],
) -> Path:
    path = archive_path(project, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact serialization: the archive is a machine-read monotonic ledger,
    # not a human diagnostic file — indent=2 nearly doubles its size (18MB →
    # ~13MB) on every scan write.
    path.write_text(
        json.dumps(archive, ensure_ascii=False, separators=(",", ":"), default=str),
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


def _representative_rank(row: dict[str, Any]) -> tuple[float, str]:
    """Deterministic representative rank of a delivered finding.

    Mirrors the canonical registry's ``_representative_finding_id`` choice:
    highest confidence first, ties by smallest occurrence finding_id — so the
    archive's collision handling never depends on list order and never
    silently drops the strongest evidence of a defect surface.
    """
    try:
        confidence = float(row.get("confidence_score") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    occurrence_id = _text(
        row.get("delivery_occurrence_finding_id")
        or row.get("finding_id")
        or row.get("id")
    )
    return (-confidence, occurrence_id)


def _archived_finding_inadmissible_reason(finding: dict[str, Any]) -> str:
    """Return why an archived finding must no longer be re-emitted, or "".

    Oracle rules evolve: framework-generic 404 responses (route-not-found
    artifacts) and authorization-family 422 responses (harness request
    formation artifacts) are now adjudicated INDETERMINATE. Findings produced
    before those rules must never be held over as customer defects — the
    archive is monotone for REAL defects, not for artifacts of a corrected
    adjudication rule.
    """
    evidence = _dict(finding.get("evidence"))
    assertion = _dict(evidence.get("assertion"))
    if _text(assertion.get("kind")) != "http_status_class":
        return ""
    actual = assertion.get("actual")
    if actual == 404:
        raw = _dict(_dict(finding.get("raw_evidence")).get("response_raw"))
        body = raw.get("body")
        if body is None:
            return ""
        if isinstance(body, dict) and set(body) == {"detail"} and body.get(
            "detail"
        ) == "Not Found":
            return "framework_route_not_found_artifact"
        raw_text = str(body)
        if '"detail": "Not Found"' in raw_text or "'detail': 'Not Found'" in raw_text:
            return "framework_route_not_found_artifact"
        return ""
    if (
        actual == 422
        and _text(finding.get("risk_family")).lower() == "authorization"
    ):
        return "authorization_input_rejected_artifact"
    return ""


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
    bug is the expected outcome (target fixed). Archived findings whose
    evidence class has since been corrected (framework 404 / authorization
    422 artifacts) are quarantined into ``retired`` with a visible reason
    instead of being re-emitted as customer defects.
    """
    merged: dict[str, dict[str, Any]] = {}
    for finding in findings:
        identity = finding_stable_identity(finding)
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = finding
            continue
        # Collision among this run's own findings: the aggregation contract
        # delivers one representative per defect surface, so a collision
        # should not happen once the upstream canonical registry collapses
        # role/obligation variants. Defensively, keep the strongest evidence
        # (highest confidence, ties by smallest occurrence finding_id — the
        # same deterministic choice the canonical registry makes), so an
        # arbitrary last-wins dedup can never silently drop a verified true
        # positive.
        if _representative_rank(finding) < _representative_rank(existing):
            merged[identity] = finding
    held = 0
    quarantined = 0
    for identity, entry in (archive.get("entries") or {}).items():
        if identity in merged:
            continue
        if entry.get("retired") or identity in (archive.get("retired") or {}):
            continue
        finding = dict(entry.get("finding") or {})
        inadmissible_reason = _archived_finding_inadmissible_reason(finding)
        if inadmissible_reason:
            # The evidence class this finding was produced under is no longer
            # admissible (framework route-not-found / authorization input
            # rejection artifacts). Retire visibly — never re-emit a routing
            # artifact as a customer defect across future runs.
            archive.setdefault("retired", {})[identity] = dict(entry)
            archive["retired"][identity]["retired_run"] = run_id
            archive["retired"][identity]["retire_reason"] = inadmissible_reason
            entries = archive.setdefault("entries", {})
            if identity in entries:
                entries[identity]["retired"] = True
                entries[identity]["retire_reason"] = inadmissible_reason
            quarantined += 1
            continue
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
        "archive_quarantined": quarantined,
        "total_output": len(output),
        "retired_count": len(archive.get("retired") or {}),
    }
    return output, receipt


def apply_verified_discovery_archive_to_run(
    project: str,
    root: Path,
    *,
    run_id: str,
    campaign_id: str,
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan 主链收尾一站式入口：load → merge → apply → save（单点闭环）。

    这是已验证发现档案跨 run 单调保持的**唯一**主链入口：本 run 交付并入
    档案（新身份追加 / 已知身份刷新，绝不丢弃），输出 findings = 本 run
    新交付 ∪ 档案中未退休的历史发现（``archive_entry=true`` 标记），随后
    落盘。失败时直接抛出 —— 由调用方（scan 收尾）记录可见的 FAILED
    receipt；档案永不静默吞错，也永不阻塞扫描。
    """
    archive = load_verified_discovery_archive(project, root)
    archive = merge_run_deliveries(
        archive,
        run_id=run_id,
        campaign_id=campaign_id,
        findings=findings,
    )
    output, receipt = apply_archive_to_run(
        archive,
        run_id=run_id,
        findings=findings,
    )
    save_verified_discovery_archive(project, root, archive)
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
