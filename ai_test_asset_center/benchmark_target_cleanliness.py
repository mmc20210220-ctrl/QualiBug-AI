from __future__ import annotations

"""Fail-fast guard against benchmarking a dirty non-production target."""

import json
import shutil
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_utc(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp_missing")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _archive_audit(
    root: Path,
    project: str,
    audit_path: Path,
    stamp: datetime,
    *,
    expected_content: str,
) -> tuple[Path, str]:
    """Atomically retire the active audit after its contents were evaluated.

    Keeping a copied audit in the active location makes the next campaign treat
    already-reset writes as current dirty state.  The evaluated audit therefore
    has exactly one terminal location: the immutable archive.  A content change
    between evaluation and retirement fails closed instead of silently archiving
    unverified concurrent writes.
    """
    archive_dir = root / "_funnel_runs" / "benchmark_audit_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / f"{project}_{stamp.strftime('%Y%m%dT%H%M%SZ')}_sandbox_write_audit.jsonl"
    current_content = audit_path.read_text(encoding="utf-8")
    if current_content != expected_content:
        raise RuntimeError(f"benchmark_write_audit_changed_during_cleanliness_check:{audit_path}")
    if archived.exists():
        archived_content = archived.read_text(encoding="utf-8")
        if archived_content != expected_content:
            raise RuntimeError(f"benchmark_write_audit_archive_collision:{archived}")
        audit_path.unlink()
    else:
        audit_path.replace(archived)
    if audit_path.exists():
        raise RuntimeError(f"benchmark_write_audit_retirement_failed:{audit_path}")
    archived_content = archived.read_text(encoding="utf-8")
    if archived_content != expected_content:
        raise RuntimeError(f"benchmark_write_audit_archive_verification_failed:{archived}")
    return archived, sha256(archived.read_bytes()).hexdigest()


def assert_benchmark_target_clean(
    *,
    root: Path,
    project: str,
    target_base_url: str,
    reset_receipt_path: str = "",
) -> dict[str, Any]:
    """Require a post-audit target reset receipt after any incomplete cleanup."""
    root = Path(root)
    audit_path = root / "platform_workspace" / project / "defect_discovery" / "sandbox_write_audit.jsonl"
    if not audit_path.is_file():
        return {"status": "clean_no_prior_write_audit", "audit_path": str(audit_path)}

    audit_content = audit_path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(audit_content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"benchmark_write_audit_invalid:{audit_path}:{line_number}:{exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"benchmark_write_audit_invalid:{audit_path}:{line_number}:expected_object")
        rows.append(value)
    incomplete = []
    for row in rows:
        status = str(row.get("cleanup_status") or "").strip().lower()
        strategy = str(row.get("cleanup_strategy") or "").strip().lower()
        # completed/verified = compensated. Observer-proven no-mutation writes
        # leave the target unchanged and do not require a reset receipt.
        if status in {"completed", "verified"}:
            continue
        if status == "not_required" and strategy in {
            "rejected_write_observer_unchanged",
            "setup_rejected_observer_unchanged",
        }:
            continue
        incomplete.append(row)
    if not incomplete:
        latest_audit = max((_parse_utc(row.get("timestamp")) for row in rows), default=datetime.now(timezone.utc))
        archived_audit, archived_sha256 = _archive_audit(
            root,
            project,
            audit_path,
            latest_audit,
            expected_content=audit_content,
        )
        return {
            "status": "clean_all_prior_writes_cleaned",
            "audit_path": str(audit_path),
            "write_count": len(rows),
            "archived_audit": str(archived_audit),
            "archived_audit_sha256": archived_sha256,
        }

    receipt_path = Path(str(reset_receipt_path or "").strip()) if str(reset_receipt_path or "").strip() else None
    if receipt_path is None or not receipt_path.is_file():
        raise RuntimeError(
            "benchmark_target_dirty_reset_receipt_required:"
            f"project={project}:incomplete_cleanup_count={len(incomplete)}:audit={audit_path}"
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"benchmark_reset_receipt_invalid:{receipt_path}:{type(exc).__name__}:{exc}") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError(f"benchmark_reset_receipt_invalid:{receipt_path}:expected_object")
    expected = {
        "schema_version": "benchmark_target_reset.v1",
        "project": project,
        "target_base_url": str(target_base_url or "").rstrip("/"),
        "status": "completed",
    }
    mismatches = [
        key
        for key, expected_value in expected.items()
        if str(receipt.get(key) or "").rstrip("/") != str(expected_value).rstrip("/")
    ]
    if mismatches or not str(receipt.get("receipt_id") or "").strip():
        raise RuntimeError(
            f"benchmark_reset_receipt_invalid:{receipt_path}:mismatches={','.join(mismatches) or 'receipt_id'}"
        )
    latest_audit = max(_parse_utc(row.get("timestamp")) for row in rows)
    reset_at = _parse_utc(receipt.get("reset_at_utc"))
    if reset_at < latest_audit:
        raise RuntimeError(
            f"benchmark_reset_receipt_stale:{receipt_path}:reset_at={reset_at.isoformat()}:latest_audit={latest_audit.isoformat()}"
        )

    stamp = reset_at.strftime("%Y%m%dT%H%M%SZ")
    archive_dir = root / "_funnel_runs" / "benchmark_audit_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_receipt = archive_dir / f"{project}_{stamp}_target_reset_receipt.json"
    shutil.copy2(receipt_path, archived_receipt)
    archived_audit, archived_sha256 = _archive_audit(
        root,
        project,
        audit_path,
        reset_at,
        expected_content=audit_content,
    )
    return {
        "status": "clean_reset_receipt_verified",
        "write_count": len(rows),
        "incomplete_cleanup_count": len(incomplete),
        "reset_receipt_id": str(receipt.get("receipt_id")),
        "archived_audit": str(archived_audit),
        "archived_audit_sha256": archived_sha256,
        "archived_receipt": str(archived_receipt),
    }
