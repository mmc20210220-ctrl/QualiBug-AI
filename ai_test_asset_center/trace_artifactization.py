# -*- coding: utf-8 -*-
"""Trace ledger artifactization (SPEC P0-4, Phase 4).

The per-run discovery trace ledger is ~9 MB of redacted, obligation-attempt
rows. Instead of accumulating one immutable file per run under
``trace_ledgers/`` (SPEC §2 growth model), new runs store the ledger in the
content-addressed ArtifactStore:

- one ``TRACE_EVENT`` artifact per attempt row (the payload pieces that repeat
  most across runs of the same campaign), and
- one ``TRACE_LEDGER`` metadata artifact — every non-attempt field plus
  ``attempt_refs`` (SPEC §26: trace metadata + event payload refs).

Run Manifests reference the metadata artifact (``trace_refs``); the reference
GC transitively keeps the ``attempt_refs`` alive by expanding TRACE_LEDGER
containers (SPEC §17/§18).

Dual Read / Single Write (SPEC §33): new runs write only through the store; a
``hydrate_trace_ledger`` reconstruction (metadata + event payloads) reproduces
the exact validated ledger dict — the fingerprint contract of
``validate_trace_ledger`` still holds. Legacy ``*.trace-ledger.json`` files
stay readable for old runs, and ``load_round_trace_ledgers`` serves both
surfaces (store-first, legacy fallback).

Redaction parity (SPEC §46): the ledger must already be redacted before it is
artifactized — the same ``redact_and_validate`` guard the legacy writer uses.
This module never logs content, tokens or credentials.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifact_store import (
    TRACE_EVENT,
    TRACE_LEDGER,
    ArtifactStore,
    ArtifactStoreError,
    artifact_store_enabled,
    default_artifact_store,
    parse_artifact_id,
)
from .artifact_redactor import redact_and_validate
from .discovery_trace_ledger import (
    DiscoveryTraceError,
    TRACE_LEDGER_SCHEMA,
    persist_trace_ledger,
    validate_trace_ledger,
)
from .run_manifest import RunManifestStore

TRACE_ARTIFACTIZATION_SCHEMA = "qualibug.trace-ledger-artifactization.v1"

_LEDGER_DIR_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class TraceArtifactizationError(ValueError):
    """Trace artifactization contract violation."""


def artifactize_trace_ledger(
    store: ArtifactStore,
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Split a validated, redacted trace ledger into store artifacts (Phase 4).

    - every attempt row becomes a ``TRACE_EVENT`` artifact (content-addressed,
      so identical rows across runs physically store once);
    - the remaining ledger fields plus ``attempt_refs`` become one
      ``TRACE_LEDGER`` metadata artifact.

    Returns ``{metadata_ref, attempt_refs, attempt_count, lifecycle}``.
    Raises ``TraceArtifactizationError`` for an invalid or unredacted ledger —
    the caller must never silently drop a trace (the failure surfaces in the
    run receipt).
    """
    if not isinstance(ledger, dict):
        raise TraceArtifactizationError("trace_ledger_object_required")
    try:
        value = validate_trace_ledger(ledger)
    except DiscoveryTraceError as exc:
        raise TraceArtifactizationError(f"trace_ledger_invalid:{exc}") from exc
    redacted, _receipt = redact_and_validate(value)
    if redacted != value:
        raise TraceArtifactizationError(
            "trace ledger still contained redactable material before artifactization"
        )

    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        raise TraceArtifactizationError("trace_ledger_attempts_missing")

    stats_before = store.snapshot_stats()
    attempt_refs: list[str] = []
    for index, row in enumerate(attempts):
        if not isinstance(row, dict):
            raise TraceArtifactizationError(f"trace_attempt_invalid:{index}")
        ref = store.put(row, TRACE_EVENT)
        attempt_refs.append(ref.artifact_id)

    metadata = {key: item for key, item in value.items() if key != "attempts"}
    metadata["attempt_refs"] = attempt_refs
    metadata["attempt_count"] = len(attempt_refs)
    metadata["artifactization"] = {
        "schema_version": TRACE_ARTIFACTIZATION_SCHEMA,
        "mode": "metadata_plus_event_refs",
    }
    metadata_ref = store.put(metadata, TRACE_LEDGER)

    from .artifact_store import snapshot_lifecycle_delta

    return {
        "schema_version": TRACE_ARTIFACTIZATION_SCHEMA,
        "metadata_ref": metadata_ref.artifact_id,
        "attempt_refs": attempt_refs,
        "attempt_count": len(attempt_refs),
        "lifecycle": snapshot_lifecycle_delta(
            stats_before, store.snapshot_stats()
        ),
    }


def hydrate_trace_ledger(
    store: ArtifactStore,
    metadata_ref: str,
) -> dict[str, Any]:
    """Reconstruct the exact validated trace ledger dict from its artifacts.

    Reassembly is exact (events were stored as canonical JSON of the same
    rows), so ``validate_trace_ledger``'s fingerprint contract still holds —
    a corrupted projection fails loudly instead of returning partial data.
    """
    parse_artifact_id(metadata_ref)
    if not store.exists(metadata_ref):
        raise TraceArtifactizationError(f"trace_metadata_missing:{metadata_ref}")
    try:
        metadata = store.get_json(metadata_ref)
    except ArtifactStoreError as exc:
        raise TraceArtifactizationError(
            f"trace_metadata_unreadable:{metadata_ref}"
        ) from exc
    if not isinstance(metadata, dict):
        raise TraceArtifactizationError("trace_metadata_invalid")
    attempt_refs = metadata.get("attempt_refs")
    if not isinstance(attempt_refs, list):
        raise TraceArtifactizationError("trace_attempt_refs_missing")
    attempts: list[dict[str, Any]] = []
    for index, ref in enumerate(attempt_refs):
        if not store.exists(str(ref)):
            raise TraceArtifactizationError(
                f"trace_event_missing:{metadata_ref}:{index}"
            )
        row = store.get_json(str(ref))
        if not isinstance(row, dict):
            raise TraceArtifactizationError(
                f"trace_event_invalid:{metadata_ref}:{index}"
            )
        attempts.append(row)
    ledger = {
        key: item
        for key, item in metadata.items()
        if key not in ("attempt_refs", "artifactization")
    }
    ledger["attempts"] = attempts
    try:
        return validate_trace_ledger(ledger)
    except DiscoveryTraceError as exc:
        raise TraceArtifactizationError(f"trace_hydration_invalid:{exc}") from exc


def persist_trace_ledger_output(
    ledger: dict[str, Any],
    evolution_root: Path | str,
    *,
    root: Path | str,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Single-write trace persistence (SPEC §33).

    New runs (artifact store enabled) write only through the ArtifactStore;
    the legacy ``*.trace-ledger.json`` file path stays for the store-disabled
    fallback mode. Returns ``{mode, ref, artifactized}`` where ``ref`` is the
    artifact id (``sha256:...``) or the workspace-relative legacy path.
    """
    if not artifact_store_enabled():
        path = persist_trace_ledger(ledger, Path(evolution_root) / "trace_ledgers")
        ref = str(Path(path).relative_to(Path(root))).replace("\\", "/")
        return {"mode": "legacy", "ref": ref, "artifactized": False}
    active_store = store if store is not None else default_artifact_store(root)
    outcome = artifactize_trace_ledger(active_store, ledger)
    return {
        "mode": "artifact_store",
        "ref": outcome["metadata_ref"],
        "artifactized": True,
        "attempt_count": outcome["attempt_count"],
    }


def load_round_trace_ledgers(
    project: str,
    root: Path | str,
    *,
    store: ArtifactStore | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Dual Read: all round trace ledgers for a project (newest last).

    Artifactized ledgers (referenced by Run Manifests) are read from the store
    first; legacy ``*.trace-ledger.json`` files are the fallback for runs that
    predate the artifact architecture (SPEC §33). Deduplicated by
    ``(run_id, created_at_utc)`` and sorted by ``created_at_utc``.
    """
    root_path = Path(root)
    ledgers: dict[tuple[str, str], dict[str, Any]] = {}

    # ── Store surface: manifests reference TRACE_LEDGER metadata artifacts ──
    if artifact_store_enabled():
        active_store = store if store is not None else default_artifact_store(root_path)
        try:
            manifest_store = RunManifestStore(active_store, root_path)
            for manifest in manifest_store.list_runs():
                for ref in manifest.trace_refs:
                    try:
                        metadata = active_store.metadata(str(ref))
                    except ArtifactStoreError:
                        continue
                    if metadata.artifact_type != TRACE_LEDGER:
                        continue
                    try:
                        ledger = hydrate_trace_ledger(active_store, str(ref))
                    except TraceArtifactizationError:
                        continue
                    key = (str(ledger.get("run_id") or ""), str(ledger.get("created_at_utc") or ""))
                    ledgers[key] = ledger
        except Exception:
            # A manifest/index read failure must never break the loader.
            pass

    # ── Legacy surface: immutable per-round files from old runs ──
    base = root_path / "platform_outputs" / project / "discovery_evolution" / "trace_ledgers"
    if base.is_dir():
        for path in sorted(base.glob("*/*.trace-ledger.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8") or "null")
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            key = (str(data.get("run_id") or ""), str(data.get("created_at_utc") or ""))
            ledgers.setdefault(key, data)

    ordered = sorted(ledgers.values(), key=lambda item: str(item.get("created_at_utc") or ""))
    if limit is not None and limit > 0:
        ordered = ordered[-int(limit):]
    return ordered
