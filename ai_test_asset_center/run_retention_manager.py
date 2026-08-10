# -*- coding: utf-8 -*-
"""RunRetentionManager — the unified Run-lifecycle owner (SPEC P0-4, Phase 8,
§20/§22/§23/§32/§47).

Retention's object is the **Run**, never the artifact (SPEC §20): when the
Run count exceeds the limit the *oldest non-pinned Run Manifests* are
deleted, and only then does reference GC remove the artifacts that truly
lost their last reference. This replaces the per-directory retain=N pattern
(SPEC §34 forbidden-1) and retires ``scan_result_retention.py`` from the
mainline (kept as deprecated legacy fallback for the store-disabled mode,
SPEC §32).

Responsibilities (run once per scan, after the Run Manifest commit):

1. **Run retention** — ``QUALIBUG_RUN_RETAIN`` (default 5) newest non-pinned
   SUCCESS manifests; ``QUALIBUG_FAILED_RUN_RETAIN`` (default 3) newest
   non-pinned FAILED/ABORTED/CRASHED manifests. Failed runs already keep only
   metadata + error summary (SPEC §16) — retention never re-adds payload.
2. **Reference GC** — mark-and-sweep over the remaining manifests
   (``ArtifactGarbageCollector``). Real deletion is opt-in via
   ``QUALIBUG_ARTIFACT_GC_ENABLE=true``; dry-run is the default (SPEC §36).
3. **Quota** — ``QUALIBUG_ARTIFACT_MAX_GB`` (default 0 = disabled): when the
   store's physical size exceeds the limit, the oldest non-pinned runs are
   removed (each followed by GC) until the store fits or nothing deletable
   remains (SPEC §22). Quota never deletes by artifact mtime.
4. **Scratch TTL** — ``QUALIBUG_SCRATCH_TTL_HOURS`` (default 24, SPEC §23):
   only *known QualiBug temp patterns* under the workspace ``.scratch/`` are
   removed when older than the TTL (SPEC §24: never ``rm *``, never delete
   unknown files).

Pinned runs are exempt from count retention AND quota (SPEC §21). Knowledge
assets (``knowledge.db``) are never touched — they live outside the artifact
store and have their own lifecycle (SPEC §4/§47, Test 10).

Every stage emits a receipt section; the manager never raises into the scan
(callers attach the receipt visibly instead).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .artifact_gc import ArtifactGarbageCollector, artifact_max_gb, gc_enabled
from .artifact_store import ArtifactStore, ArtifactStoreError
from .run_manifest import (
    RUN_STATUS_SUCCESS,
    RunManifestStore,
    failed_run_retain_count,
    run_retain_count,
)

RETENTION_MANAGER_SCHEMA = "qualibug.run-retention-manager.v1"
SCRATCH_TTL_HOURS_DEFAULT = 24
_SCRATCH_TMP_RE = re.compile(r"^\.q-.*\.tmp$|\.tmp$|\.legacy$")


def scratch_ttl_hours() -> float:
    """``QUALIBUG_SCRATCH_TTL_HOURS`` (default 24, SPEC §23/§31)."""
    raw = str(os.getenv("QUALIBUG_SCRATCH_TTL_HOURS", "") or "").strip()
    if not raw:
        return SCRATCH_TTL_HOURS_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return SCRATCH_TTL_HOURS_DEFAULT
    return value if value >= 0.0 else SCRATCH_TTL_HOURS_DEFAULT


def cleanup_stale_scratch(
    root: Path | str,
    *,
    ttl_hours: float | None = None,
) -> dict[str, Any]:
    """Remove known QualiBug temp patterns under ``<root>/.scratch/`` older
    than the TTL (SPEC §23/§24).

    Only files matching the explicit temp patterns (``.q-*.tmp``, ``*.tmp``,
    ``*.legacy``) are considered; directories and unknown files are never
    touched. Returns a receipt with removed count and freed bytes.
    """
    ttl = ttl_hours if ttl_hours is not None else scratch_ttl_hours()
    scratch = Path(root) / ".scratch"
    cutoff = time.time() - ttl * 3600.0
    removed = 0
    freed_bytes = 0
    if scratch.is_dir():
        for child in scratch.iterdir():
            if not child.is_file() or not _SCRATCH_TMP_RE.search(child.name):
                continue
            try:
                if child.stat().st_mtime >= cutoff:
                    continue
                freed_bytes += child.stat().st_size
                child.unlink()
                removed += 1
            except OSError:
                continue
    return {
        "schema_version": "qualibug.scratch-ttl.v1",
        "ttl_hours": ttl,
        "removed_count": removed,
        "freed_bytes": freed_bytes,
    }


class RunRetentionManager:
    """Unified owner of Run retention, reference GC and quota (SPEC §20/§22)."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        manifest_store: RunManifestStore,
        root: Path | str | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._manifest_store = manifest_store
        self._root = Path(root) if root is not None else manifest_store.root

    # ------------------------------------------------------------------ #
    # Public entry
    # ------------------------------------------------------------------ #
    def run(
        self,
        *,
        run_retain: int | None = None,
        failed_run_retain: int | None = None,
        gc_enable: bool | None = None,
        gc_grace_hours: float | None = None,
        max_gb: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Execute the full Run-lifecycle pass (retention → GC → quota).

        Every stage is a visible receipt section; a stage failure is reported
        in the receipt and never raises into the scan.
        """
        receipt: dict[str, Any] = {
            "schema_version": RETENTION_MANAGER_SCHEMA,
            "status": "completed",
        }
        # ── 1. Run retention: delete oldest non-pinned manifests beyond the
        # count limits (SPEC §20). Artifacts are NOT touched here — GC owns
        # payloads. ──
        try:
            receipt["run_retention"] = self._manifest_store.retain_runs(
                run_retain=(
                    run_retain if run_retain is not None else run_retain_count()
                ),
                failed_run_retain=(
                    failed_run_retain
                    if failed_run_retain is not None
                    else failed_run_retain_count()
                ),
            )
        except Exception as exc:
            receipt["run_retention"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
            receipt["status"] = "degraded"

        # ── 2. Reference GC (SPEC §17/§36): dry-run unless explicitly
        # enabled. ──
        effective_gc = gc_enable if gc_enable is not None else gc_enabled()
        try:
            gc = ArtifactGarbageCollector(
                self._artifact_store,
                self._manifest_store,
                grace_hours=gc_grace_hours,
            )
            receipt["gc"] = gc.run(dry_run=not effective_gc, now=now)
        except Exception as exc:
            receipt["gc"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
            receipt["status"] = "degraded"

        # ── 3. Quota (SPEC §22): bounded, run-level eviction + GC. ──
        try:
            receipt["quota"] = self._enforce_quota(
                max_gb if max_gb is not None else artifact_max_gb(),
                gc_enable=effective_gc,
                gc_grace_hours=gc_grace_hours,
            )
        except Exception as exc:
            receipt["quota"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
            receipt["status"] = "degraded"
        return receipt

    # ------------------------------------------------------------------ #
    # Quota
    # ------------------------------------------------------------------ #
    def physical_store_bytes(self) -> int:
        """Current physical footprint of the artifact store (stored bytes)."""
        total = 0
        for artifact_id in self._artifact_store.list_all():
            try:
                total += int(self._artifact_store.metadata(artifact_id).stored_size)
            except ArtifactStoreError:
                continue
        return total

    def _enforce_quota(
        self,
        max_gb: float,
        *,
        gc_enable: bool,
        gc_grace_hours: float | None = None,
    ) -> dict[str, Any]:
        """Quota enforcement — never by artifact mtime (SPEC §34 forbidden-2).

        When the store exceeds ``max_gb``, the oldest non-pinned Run Manifest
        is deleted and GC runs, repeatedly, until the store fits or no
        non-pinned run remains. ``max_gb <= 0`` disables the quota (§22).
        """
        if max_gb <= 0:
            return {"enabled": False, "max_gb": max_gb}
        limit = max_gb * 1024.0 ** 3
        removed_run_ids: list[str] = []
        rounds = 0
        used = self.physical_store_bytes()
        while used > limit and rounds < 100000:
            candidates = [
                manifest
                for manifest in self._manifest_store.list_runs()
                if not manifest.pinned
            ]
            if not candidates:
                break
            oldest = min(candidates, key=lambda m: str(m.created_at))
            outcome = self._manifest_store.delete(oldest.run_id)
            if not outcome.get("deleted"):
                break
            removed_run_ids.append(oldest.run_id)
            try:
                gc = ArtifactGarbageCollector(
                    self._artifact_store,
                    self._manifest_store,
                    grace_hours=gc_grace_hours,
                )
                gc.run(dry_run=not gc_enable)
            except Exception:
                pass
            used = self.physical_store_bytes()
            rounds += 1
        return {
            "enabled": True,
            "max_gb": max_gb,
            "limit_bytes": int(limit),
            "used_bytes": used,
            "removed_run_ids": removed_run_ids,
            "removed_count": len(removed_run_ids),
            "still_over": used > limit,
            "eviction_rounds": rounds,
        }

    # ------------------------------------------------------------------ #
    # Helpers used by callers
    # ------------------------------------------------------------------ #
    def manifest_count(self, status: str | None = None) -> int:
        manifests = self._manifest_store.list_runs()
        if status is not None:
            manifests = [m for m in manifests if m.status == status]
        return len(manifests)

    def success_manifest_count(self) -> int:
        return self.manifest_count(RUN_STATUS_SUCCESS)
