# -*- coding: utf-8 -*-
"""Reference garbage collector for the artifact store (SPEC P0-4, Phase 7
skeleton — dry-run basics for this task; full GC lands in Phase 7).

Deleting an artifact is safe in exactly one case (SPEC §17): no valid Run
Manifest references it AND it is not pinned AND it is older than the grace
period (SPEC §19). Mark-and-sweep over manifests is the first-stage truth
(SPEC §18) — no ref_count column, so abnormal exits can never desync counts
into wrong deletions.

This phase provides the interface plus the dry-run baseline (SPEC §36):
``plan()`` emits live / garbage / reclaimable / protected / pinned counts and
bytes without deleting anything; ``run(dry_run=True)`` is the default safe
mode. Real deletion is behind an explicit non-dry-run call.

Guards:

- GC operates exclusively on artifact ids enumerated by
  ``ArtifactStore.list_all()`` — it can never touch files outside the store
  (knowledge.db lives outside the artifact root by construction and is never
  scanned; Test 10).
- Artifacts referenced by any manifest — including FAILED/ABORTED/CRASHED
  manifests and pinned runs — are live (SPEC §17/§21).
- Unreferenced artifacts younger than the grace period are protected, never
  deleted (SPEC §19/§40 crash-safety scenario A).
"""
from __future__ import annotations

import calendar
import os
import time
from typing import Any

from .artifact_store import ArtifactStore, ArtifactStoreError
from .run_manifest import RunManifestStore

GC_GRACE_HOURS_DEFAULT = 24
GC_MAX_GB_DEFAULT = 0  # 0 = capacity quota disabled (SPEC §22/§31)


def gc_grace_hours() -> float:
    """``QUALIBUG_ARTIFACT_GC_GRACE_HOURS`` (default 24, SPEC §19/§31)."""
    return _env_float("QUALIBUG_ARTIFACT_GC_GRACE_HOURS", GC_GRACE_HOURS_DEFAULT, minimum=0.0)


def artifact_max_gb() -> float:
    """``QUALIBUG_ARTIFACT_MAX_GB`` (default 0 = disabled, SPEC §22/§31)."""
    return _env_float("QUALIBUG_ARTIFACT_MAX_GB", GC_MAX_GB_DEFAULT, minimum=0.0)


class ArtifactGarbageCollector:
    """Mark-and-sweep GC over manifest references (SPEC §17/§18/§36)."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        run_manifest_store: RunManifestStore | None = None,
        *,
        grace_hours: float | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._manifest_store = run_manifest_store
        self._grace_hours = (
            grace_hours if grace_hours is not None else gc_grace_hours()
        )

    @property
    def grace_hours(self) -> float:
        return self._grace_hours

    def plan(self, *, now: float | None = None) -> dict[str, Any]:
        """Compute the GC plan without deleting anything (SPEC §36).

        Returns live/garbage/protected/pinned counts and reclaimable bytes
        (physical stored bytes + logical original bytes).
        """
        now = time.time() if now is None else float(now)
        grace_seconds = self._grace_hours * 3600.0

        live_refs: set[str] = set()
        pinned_refs: set[str] = set()
        if self._manifest_store is not None:
            for manifest in self._manifest_store.list_runs():
                live_refs.update(self._manifest_store._collect_refs(manifest))
                if manifest.pinned:
                    pinned_refs.update(self._manifest_store._collect_refs(manifest))

        live: list[str] = []
        garbage: list[str] = []
        protected: list[str] = []
        pinned: list[str] = []
        reclaimable_stored = 0
        reclaimable_logical = 0
        for artifact_id in self._artifact_store.list_all():
            if artifact_id in live_refs:
                live.append(artifact_id)
                if artifact_id in pinned_refs:
                    pinned.append(artifact_id)
                continue
            try:
                meta = self._artifact_store.metadata(artifact_id)
            except ArtifactStoreError:
                # A payload without readable metadata is not safe to classify;
                # it stays protected rather than being deleted blindly.
                protected.append(artifact_id)
                continue
            created_at = _parse_created_at(meta.created_at)
            if created_at is not None and (now - created_at) < grace_seconds:
                protected.append(artifact_id)
                continue
            garbage.append(artifact_id)
            reclaimable_stored += int(meta.stored_size)
            reclaimable_logical += int(meta.original_size)

        return {
            "dry_run": True,
            "live_count": len(live),
            "garbage_count": len(garbage),
            "protected_count": len(protected),
            "pinned_count": len(pinned),
            "reclaimable_bytes": reclaimable_stored,
            "reclaimable_logical_bytes": reclaimable_logical,
            "deleted_count": 0,
            "grace_hours": self._grace_hours,
            "live": live,
            "garbage": garbage,
            "protected": protected,
            "pinned": pinned,
        }

    def run(self, *, dry_run: bool = True, now: float | None = None) -> dict[str, Any]:
        """Execute (or preview) the GC.

        ``dry_run=True`` (default) deletes nothing and returns the plan
        (SPEC §36). Only an explicit non-dry-run call deletes, and only the
        artifacts the plan classified as garbage.
        """
        plan = self.plan(now=now)
        if dry_run:
            return plan
        deleted = 0
        reclaimed = 0
        for artifact_id in plan["garbage"]:
            try:
                meta = self._artifact_store.metadata(artifact_id)
                self._artifact_store.delete(artifact_id)
                deleted += 1
                reclaimed += int(meta.stored_size)
            except ArtifactStoreError:
                continue
        return {
            **plan,
            "dry_run": False,
            "deleted_count": deleted,
            "reclaimed_bytes": reclaimed,
        }


def _parse_created_at(created_at: str) -> float | None:
    """Parse ISO-UTC ``%Y-%m-%dT%H:%M:%SZ``; None when unparseable."""
    text = str(created_at or "").strip()
    if not text:
        return None
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return None


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default
