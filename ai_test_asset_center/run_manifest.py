# -*- coding: utf-8 -*-
"""Run Manifest — the Run entry that references Artifacts (SPEC P0-4, Phase 2).

A Run no longer owns files: after execution the mainline generates artifacts,
stores them in the content-addressed ``ArtifactStore``, verifies every
reference exists, and only then atomically commits a lightweight manifest
(SPEC §14/§15). Run history is described by manifests; artifacts are deleted
only by reference GC after manifests stop referencing them.

Commit ordering (SPEC §15) is enforced by this module::

    execute scan
      -> generate artifacts
      -> artifact_store.put
      -> verify every artifact exists      <- commit_success refuses otherwise
      -> write manifest.tmp
      -> atomic rename manifest
      -> Run = SUCCESS
      -> GC / retention                    <- later phases

Failed runs (SPEC §16) keep only metadata + error summary + an optional
debug-trace ref — never large evidence payloads.

Pinning (SPEC §21): a pinned Run is exempt from count retention and its
references stay live for GC.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .artifact_store import (
    ArtifactStore,
    ArtifactStoreError,
    canonical_json_bytes,
    parse_artifact_id,
)

RUN_MANIFEST_SCHEMA = "qualibug.run-manifest.v1"

RUN_STATUS_SUCCESS = "SUCCESS"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_ABORTED = "ABORTED"
RUN_STATUS_CRASHED = "CRASHED"

_RUN_ID_RE = re.compile(r"[^A-Za-z0-9_.\-]+")
_RUN_RETAIN_DEFAULT = 5
_FAILED_RUN_RETAIN_DEFAULT = 3


class RunManifestError(RuntimeError):
    """Run manifest contract violation."""


def sanitize_run_id(run_id: str) -> str:
    """Neutralize a run id for filesystem use (no path traversal)."""
    cleaned = _RUN_ID_RE.sub("_", str(run_id or "").strip())
    if not cleaned or cleaned in {".", ".."}:
        raise RunManifestError(f"invalid_run_id:{run_id!r}")
    return cleaned


def run_retain_count() -> int:
    """``QUALIBUG_RUN_RETAIN`` (default 5, SPEC §20/§31)."""
    return _env_int("QUALIBUG_RUN_RETAIN", _RUN_RETAIN_DEFAULT, minimum=1)


def failed_run_retain_count() -> int:
    """``QUALIBUG_FAILED_RUN_RETAIN`` (default 3, SPEC §16/§31)."""
    return _env_int("QUALIBUG_FAILED_RUN_RETAIN", _FAILED_RUN_RETAIN_DEFAULT, minimum=1)


@dataclass
class RunManifest:
    """Lightweight per-Run entry (SPEC §14)."""

    run_id: str
    created_at: str
    status: str
    scan_result_ref: str | None = None
    trace_refs: list[str] = field(default_factory=list)
    evidence_bundle_refs: list[str] = field(default_factory=list)
    delivery_package_refs: list[str] = field(default_factory=list)
    intelligence_report_ref: str | None = None
    pinned: bool = False
    error_summary: str | None = None
    debug_trace_ref: str | None = None
    lifecycle: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunManifest":
        if not isinstance(raw, dict):
            raise RunManifestError("run_manifest_invalid")
        manifest = cls(
            run_id=str(raw.get("run_id") or ""),
            created_at=str(raw.get("created_at") or ""),
            status=str(raw.get("status") or "").upper(),
            scan_result_ref=raw.get("scan_result_ref"),
            trace_refs=[str(item) for item in (raw.get("trace_refs") or []) if item],
            evidence_bundle_refs=[
                str(item) for item in (raw.get("evidence_bundle_refs") or []) if item
            ],
            delivery_package_refs=[
                str(item) for item in (raw.get("delivery_package_refs") or []) if item
            ],
            intelligence_report_ref=raw.get("intelligence_report_ref"),
            pinned=bool(raw.get("pinned")),
            error_summary=raw.get("error_summary"),
            debug_trace_ref=raw.get("debug_trace_ref"),
            lifecycle=raw.get("lifecycle") if isinstance(raw.get("lifecycle"), dict) else None,
        )
        if not manifest.run_id or manifest.run_id != sanitize_run_id(manifest.run_id):
            raise RunManifestError("run_manifest_invalid_run_id")
        if manifest.status not in {
            RUN_STATUS_SUCCESS,
            RUN_STATUS_FAILED,
            RUN_STATUS_ABORTED,
            RUN_STATUS_CRASHED,
        }:
            raise RunManifestError(f"run_manifest_invalid_status:{manifest.status}")
        return manifest


class RunManifestStore:
    """Persists Run manifests under ``<root>/runs/<run_id>/manifest.json``.

    Every commit follows the SPEC §15 ordering: references are verified to
    exist in the artifact store before the manifest is atomically renamed into
    place. A run is only ever ``SUCCESS`` after its manifest is committed.
    """

    def __init__(self, artifact_store: ArtifactStore, root: Path | str) -> None:
        self._artifact_store = artifact_store
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def artifact_store(self) -> ArtifactStore:
        return self._artifact_store

    # ------------------------------------------------------------------ #
    # Commit
    # ------------------------------------------------------------------ #
    def commit_success(
        self,
        run_id: str,
        *,
        scan_result_ref: str | None = None,
        trace_refs: list[str] | None = None,
        evidence_bundle_refs: list[str] | None = None,
        delivery_package_refs: list[str] | None = None,
        intelligence_report_ref: str | None = None,
        pinned: bool = False,
        lifecycle: dict[str, Any] | None = None,
    ) -> RunManifest:
        """Commit a SUCCESS manifest after verifying every reference (SPEC §15).

        Raises ``RunManifestError`` listing the missing references — the
        manifest is never written with dangling refs.
        """
        run_id = sanitize_run_id(run_id)
        refs = {
            "scan_result_ref": scan_result_ref,
            "intelligence_report_ref": intelligence_report_ref,
            "trace_refs": list(trace_refs or []),
            "evidence_bundle_refs": list(evidence_bundle_refs or []),
            "delivery_package_refs": list(delivery_package_refs or []),
        }
        missing = self._missing_refs(refs)
        if missing:
            raise RunManifestError(
                "run_manifest_refs_missing:" + ",".join(sorted(missing))
            )
        manifest = RunManifest(
            run_id=run_id,
            created_at=_now_utc(),
            status=RUN_STATUS_SUCCESS,
            scan_result_ref=scan_result_ref,
            trace_refs=list(trace_refs or []),
            evidence_bundle_refs=list(evidence_bundle_refs or []),
            delivery_package_refs=list(delivery_package_refs or []),
            intelligence_report_ref=intelligence_report_ref,
            pinned=bool(pinned),
            lifecycle=lifecycle if isinstance(lifecycle, dict) else None,
        )
        self._write_manifest(manifest)
        return manifest

    def commit_failed(
        self,
        run_id: str,
        *,
        status: str = RUN_STATUS_FAILED,
        error_summary: str | None = None,
        debug_trace_ref: str | None = None,
        scan_result_ref: str | None = None,
    ) -> RunManifest:
        """Commit a FAILED/ABORTED/CRASHED manifest (SPEC §16).

        Failed runs keep only metadata + error summary + an optional debug
        trace ref — never large evidence payloads. References that cannot be
        verified are dropped from the manifest rather than blocking the
        failure record (a failed run must stay observable).
        """
        run_id = sanitize_run_id(run_id)
        if status not in (RUN_STATUS_FAILED, RUN_STATUS_ABORTED, RUN_STATUS_CRASHED):
            raise RunManifestError(f"commit_failed_status:{status}")
        if scan_result_ref is not None and not self._artifact_store.exists(scan_result_ref):
            scan_result_ref = None
        if debug_trace_ref is not None and not self._artifact_store.exists(debug_trace_ref):
            debug_trace_ref = None
        manifest = RunManifest(
            run_id=run_id,
            created_at=_now_utc(),
            status=status,
            scan_result_ref=scan_result_ref,
            error_summary=(error_summary or "")[:2000] or None,
            debug_trace_ref=debug_trace_ref,
        )
        self._write_manifest(manifest)
        return manifest

    # ------------------------------------------------------------------ #
    # Read / write lifecycle
    # ------------------------------------------------------------------ #
    def load(self, run_id: str) -> RunManifest:
        run_id = sanitize_run_id(run_id)
        path = self._manifest_path(run_id)
        if not path.is_file():
            raise RunManifestError(f"run_manifest_missing:{run_id}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "null")
        except (OSError, json.JSONDecodeError) as exc:
            raise RunManifestError(f"run_manifest_unreadable:{run_id}") from exc
        return RunManifest.from_dict(raw)

    def list_runs(self) -> list[RunManifest]:
        runs_dir = self._root / "runs"
        manifests: list[RunManifest] = []
        if not runs_dir.is_dir():
            return manifests
        for child in sorted(runs_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "manifest.json"
            if not manifest_file.is_file():
                continue
            try:
                manifests.append(self.load(child.name))
            except RunManifestError:
                continue
        return sorted(manifests, key=lambda m: m.created_at)

    def verify(self, run_id: str) -> dict[str, Any]:
        """Verify every referenced artifact still exists (SPEC §15 validation)."""
        manifest = self.load(run_id)
        refs = self._collect_refs(manifest)
        missing = sorted(ref for ref in refs if not self._artifact_store.exists(ref))
        return {
            "run_id": manifest.run_id,
            "valid": not missing,
            "checked": len(refs),
            "missing_refs": missing,
            "status": manifest.status,
        }

    def delete(self, run_id: str) -> dict[str, Any]:
        """Delete a manifest. Pinned runs are never deleted (SPEC §21)."""
        manifest = self.load(run_id)
        if manifest.pinned:
            return {"deleted": False, "reason": "pinned", "run_id": run_id}
        path = self._manifest_path(run_id)
        try:
            if path.is_file():
                path.unlink()
            parent = path.parent
            try:
                parent.rmdir()
            except OSError:
                pass
        except OSError as exc:
            raise RunManifestError(f"run_manifest_delete_failed:{run_id}") from exc
        return {"deleted": True, "reason": "retention", "run_id": run_id}

    def pin(self, run_id: str, pinned: bool = True) -> RunManifest:
        """Toggle the pinned flag (SPEC §21) with an atomic rewrite."""
        manifest = self.load(run_id)
        manifest.pinned = bool(pinned)
        self._write_manifest(manifest)
        return manifest

    def collect_live_refs(self) -> set[str]:
        """All references of every valid manifest — the GC mark set (SPEC §17)."""
        live: set[str] = set()
        for manifest in self.list_runs():
            live.update(self._collect_refs(manifest))
        return live

    def retain_runs(self, *, run_retain: int | None = None, failed_run_retain: int | None = None) -> dict[str, Any]:
        """Phase-8 skeleton: delete the oldest non-pinned manifests beyond the
        count limits (SPEC §20). Artifacts are NOT touched here — reference GC
        runs afterwards and removes only truly unreferenced payloads.
        """
        run_retain = run_retain if run_retain is not None else run_retain_count()
        failed_run_retain = (
            failed_run_retain if failed_run_retain is not None else failed_run_retain_count()
        )
        success_runs = [
            m for m in self.list_runs()
            if m.status == RUN_STATUS_SUCCESS and not m.pinned
        ]
        failed_runs = [
            m for m in self.list_runs()
            if m.status != RUN_STATUS_SUCCESS and not m.pinned
        ]
        removed: list[str] = []
        for group, limit in (
            (success_runs, run_retain),
            (failed_runs, failed_run_retain),
        ):
            # Keep the newest `limit`; remove the oldest overflow. `group[:-limit]`
            # is a Python trap for limit=0 (`-0 == 0` keeps everything), so the
            # overflow slice is computed explicitly.
            overflow = max(0, len(group) - limit)
            for manifest in group[:overflow]:
                outcome = self.delete(manifest.run_id)
                if outcome.get("deleted"):
                    removed.append(manifest.run_id)
        return {
            "schema_version": "qualibug.run-retention.v1",
            "run_retain": run_retain,
            "failed_run_retain": failed_run_retain,
            "removed_run_ids": removed,
            "removed_count": len(removed),
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _manifest_path(self, run_id: str) -> Path:
        return self._root / "runs" / run_id / "manifest.json"

    def _write_manifest(self, manifest: RunManifest) -> None:
        run_dir = self._manifest_path(manifest.run_id).parent
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": RUN_MANIFEST_SCHEMA,
            **manifest.to_dict(),
        }
        path = run_dir / "manifest.json"
        temporary = run_dir / f".manifest.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        try:
            with temporary.open("wb") as out:
                out.write(canonical_json_bytes(payload))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    @staticmethod
    def _collect_refs(manifest: RunManifest) -> set[str]:
        refs = {manifest.scan_result_ref, manifest.intelligence_report_ref}
        refs.update(manifest.trace_refs)
        refs.update(manifest.evidence_bundle_refs)
        refs.update(manifest.delivery_package_refs)
        refs.discard(None)
        return {ref for ref in refs if ref}

    def _missing_refs(self, refs: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for field_name, value in refs.items():
            if isinstance(value, str) and value:
                self._check_ref(value, field_name, missing)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if item:
                        self._check_ref(str(item), f"{field_name}[{index}]", missing)
        return missing

    def _check_ref(self, artifact_id: str, field_name: str, missing: list[str]) -> None:
        try:
            parse_artifact_id(artifact_id)
        except ArtifactStoreError:
            missing.append(f"{field_name}={artifact_id}")
            return
        if not self._artifact_store.exists(artifact_id):
            missing.append(f"{field_name}={artifact_id}")


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default
