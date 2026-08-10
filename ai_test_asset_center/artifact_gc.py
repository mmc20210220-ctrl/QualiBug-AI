# -*- coding: utf-8 -*-
"""Reference garbage collector for the artifact store (SPEC P0-4, Phase 7).

Deleting an artifact is safe in exactly one case (SPEC §17): no valid Run
Manifest references it AND it is not pinned AND it is older than the grace
period (SPEC §19). Mark-and-sweep over manifests is the final truth
(SPEC §18) — no ref_count column, so abnormal exits can never desync counts
into wrong deletions.

Phase 7 completes the skeleton into a real collector:

- **Transitive mark through reference containers** (SPEC §12/§14/§26): run
  manifests reference entry artifacts — ``EVIDENCE_BUNDLE_MANIFEST`` (parts
  refs), ``TRACE_LEDGER`` (``attempt_refs``) and ``INTELLIGENCE_REPORT``
  (``artifact_refs``) — whose payloads are themselves referenced by the run.
  The live set is expanded through those containers, so deleting a run can
  never orphan the evidence of a run that still references it.
- **Dry-run by default** (SPEC §36): ``plan()`` emits live / garbage /
  reclaimable / protected / pinned without deleting anything;
  ``run()`` deletes only when explicitly requested or when
  ``QUALIBUG_ARTIFACT_GC_ENABLE=true`` is set.
- **Crash safety** (SPEC §40-C): the garbage set is fully classified before
  the first deletion; every deletion re-checks liveness against a fresh mark
  taken just before the sweep, so a concurrent manifest commit or a crash
  mid-sweep can never delete an artifact a live run still references.
- **Fail closed**: a live reference container whose payload cannot be parsed
  aborts the whole GC run (deletes nothing) instead of guessing which refs
  it holds.

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

from .artifact_store import (
    EVIDENCE_BUNDLE_MANIFEST,
    INTELLIGENCE_REPORT,
    TRACE_LEDGER,
    ArtifactStore,
    ArtifactStoreError,
    parse_artifact_id,
)
from .run_manifest import RunManifestStore

GC_GRACE_HOURS_DEFAULT = 24
GC_MAX_GB_DEFAULT = 0  # 0 = capacity quota disabled (SPEC §22/§31)

# Reference-container artifact types: their payloads declare refs that the
# run graph keeps alive (SPEC §12/§14/§26). The set is open — new containers
# register here with their declared-ref extractor.
_REF_CONTAINER_TYPES = frozenset(
    {EVIDENCE_BUNDLE_MANIFEST, TRACE_LEDGER, INTELLIGENCE_REPORT}
)


def gc_grace_hours() -> float:
    """``QUALIBUG_ARTIFACT_GC_GRACE_HOURS`` (default 24, SPEC §19/§31)."""
    return _env_float("QUALIBUG_ARTIFACT_GC_GRACE_HOURS", GC_GRACE_HOURS_DEFAULT, minimum=0.0)


def artifact_max_gb() -> float:
    """``QUALIBUG_ARTIFACT_MAX_GB`` (default 0 = disabled, SPEC §22/§31)."""
    return _env_float("QUALIBUG_ARTIFACT_MAX_GB", GC_MAX_GB_DEFAULT, minimum=0.0)


def gc_enabled() -> bool:
    """``QUALIBUG_ARTIFACT_GC_ENABLE`` — real deletion is opt-in.

    Default remains dry-run: GC must be explicitly enabled
    (``QUALIBUG_ARTIFACT_GC_ENABLE=true``) before it deletes anything
    (SPEC §36, "上线初期默认可先 dry-run 验证").
    """
    raw = str(os.getenv("QUALIBUG_ARTIFACT_GC_ENABLE", "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "enabled")


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

    # ------------------------------------------------------------------ #
    # Mark phase
    # ------------------------------------------------------------------ #
    def collect_live_refs(self) -> set[str] | None:
        """Mark-and-sweep live set: every manifest ref, transitively expanded
        through reference containers.

        Returns ``None`` when a live container cannot be parsed — the caller
        must abort the GC (fail closed) rather than sweep blind.
        """
        live: set[str] = set()
        if self._manifest_store is not None:
            for manifest in self._manifest_store.list_runs():
                live.update(self._manifest_store._collect_refs(manifest))
        queue = list(live)
        seen: set[str] = set()
        while queue:
            artifact_id = queue.pop()
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            nested = self._expand_container(artifact_id)
            if nested is None:
                # A live reference container is unreadable — abort the mark.
                return None
            for ref in nested:
                if ref not in live:
                    live.add(ref)
                    queue.append(ref)
        return live

    def collect_pinned_refs(self) -> set[str]:
        """Refs of pinned runs — protected from retention AND GC (SPEC §21)."""
        pinned: set[str] = set()
        if self._manifest_store is None:
            return pinned
        for manifest in self._manifest_store.list_runs():
            if manifest.pinned:
                pinned.update(self._manifest_store._collect_refs(manifest))
        return pinned

    def _expand_container(self, artifact_id: str) -> set[str] | None:
        """Refs declared inside a reference-container artifact.

        Returns an empty set for non-containers, the declared refs for
        containers, and ``None`` when a container payload cannot be parsed
        (fail closed). Only well-formed artifact ids are returned.
        """
        try:
            meta = self._artifact_store.metadata(artifact_id)
        except ArtifactStoreError:
            return set()
        if meta.artifact_type not in _REF_CONTAINER_TYPES:
            return set()
        try:
            payload = self._artifact_store.get_json(artifact_id)
        except Exception:
            # Any read/parse failure of a live container's payload (corrupt
            # zstd frame, invalid JSON, …) fails closed: the caller aborts the
            # GC instead of sweeping refs it cannot confirm.
            return None
        if not isinstance(payload, dict):
            return None
        if meta.artifact_type == EVIDENCE_BUNDLE_MANIFEST:
            refs = self._bundle_manifest_refs(payload)
        elif meta.artifact_type == TRACE_LEDGER:
            refs = _list_refs(payload.get("attempt_refs"))
        elif meta.artifact_type == INTELLIGENCE_REPORT:
            refs = _list_refs(payload.get("artifact_refs"))
        else:  # pragma: no cover - guarded by _REF_CONTAINER_TYPES
            refs = []
        return {ref for ref in refs if _valid_ref(ref)}

    @staticmethod
    def _bundle_manifest_refs(payload: dict[str, Any]) -> list[str]:
        """Declared refs of an EVIDENCE_BUNDLE_MANIFEST (SPEC §12)."""
        parts = payload.get("parts")
        if not isinstance(parts, dict):
            return []
        refs: list[str] = []
        for key in (
            "metadata_ref",
            "execution_output_ref",
            "db_snapshot_ref",
            "request_ref",
            "response_ref",
            "screenshot_ref",
            "dom_ref",
            "ui_state_ref",
        ):
            value = parts.get(key)
            if value:
                refs.append(str(value))
        logs = parts.get("logs_refs")
        if isinstance(logs, list):
            refs.extend(str(item) for item in logs if item)
        for entry in parts.get("har_entries_refs") or []:
            if not isinstance(entry, dict):
                continue
            for key in ("request_ref", "response_ref"):
                if entry.get(key):
                    refs.append(str(entry[key]))
        return refs

    # ------------------------------------------------------------------ #
    # Plan / sweep
    # ------------------------------------------------------------------ #
    def plan(self, *, now: float | None = None) -> dict[str, Any]:
        """Compute the GC plan without deleting anything (SPEC §36).

        Returns live/garbage/protected/pinned counts and reclaimable bytes
        (physical stored bytes + logical original bytes). When the mark aborts
        (unreadable live container) the plan reports ``status: ABORTED`` and
        deletes nothing.
        """
        now = time.time() if now is None else float(now)
        grace_seconds = self._grace_hours * 3600.0

        live_refs = self.collect_live_refs()
        if live_refs is None:
            return {
                "dry_run": True,
                "status": "ABORTED",
                "abort_reason": "live_reference_container_unparseable",
                "deleted_count": 0,
                "reclaimed_bytes": 0,
            }
        pinned_refs = self.collect_pinned_refs()

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
            "status": "completed",
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

    def run(self, *, dry_run: bool | None = None, now: float | None = None) -> dict[str, Any]:
        """Execute (or preview) the GC.

        ``dry_run=None`` (default) follows ``QUALIBUG_ARTIFACT_GC_ENABLE``:
        unset/false → preview only; ``true`` → real deletion. An explicit
        ``dry_run=True/False`` overrides the environment (SPEC §36).

        Crash safety (SPEC §40-C): the garbage set is classified before any
        deletion, and every deletion re-checks a fresh live mark — a run
        committed concurrently, or a crash mid-sweep, can never lose an
        artifact a live run still references.
        """
        if dry_run is None:
            dry_run = not gc_enabled()
        plan = self.plan(now=now)
        if dry_run or plan.get("status") == "ABORTED":
            return plan
        live_now = self.collect_live_refs()
        if live_now is None:
            return {**plan, "status": "ABORTED", "deleted_count": 0, "reclaimed_bytes": 0}
        deleted = 0
        reclaimed = 0
        for artifact_id in plan["garbage"]:
            if artifact_id in live_now:
                # Became live since the plan (concurrent manifest commit) —
                # never delete it.
                continue
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


def _list_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _valid_ref(artifact_id: str) -> bool:
    try:
        parse_artifact_id(artifact_id)
        return True
    except ArtifactStoreError:
        return False


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
