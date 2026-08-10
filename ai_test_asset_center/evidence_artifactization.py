# -*- coding: utf-8 -*-
"""Fine-grained evidence artifactization (SPEC P0-4, Phase 3).

The 88 MB/run evidence bundle is the largest growth source. Instead of
persisting the whole bundle as one giant blob per run, this module splits it
into content-addressed pieces (SPEC §12/§13) so unchanged evidence across
runs physically stores once:

- ``METADATA`` artifact — runtime_contract + campaign + source manifest.
- ``EXECUTION_OUTPUT`` artifact — findings / candidate_findings /
  evidence_graphs / canonical_defect_registry / delivery_occurrences /
  ui_execution.
- ``DB_SNAPSHOT`` artifact — per-finding before/after database snapshots.
- ``HTTP_REQUEST`` / ``HTTP_RESPONSE`` artifacts — one pair per HAR entry,
  plus a small ``HAR_CAPTURE`` index (URLs/statuses/refs only, no bodies).
- ``EVIDENCE_BUNDLE_MANIFEST`` artifact — the bundle entry: every part ref.

Dual Read / Single Write (SPEC §33): new writes go exclusively through the
artifact store; old bundles stay readable through the legacy layout. A tiny
``manifest.pointer.json`` in the legacy bundle directory lets existing
consumers (release gate verification, delivery packaging, report loading)
find and hydrate the artifactized bundle without copying evidence back to
disk.

Redaction parity (SPEC §46): every piece passes through the same recursive
redaction as the legacy bundle writer — no new secret surface, and this
module never logs credentials, tokens or content.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .artifact_store import (
    DB_SNAPSHOT,
    EVIDENCE_BUNDLE_MANIFEST,
    EXECUTION_OUTPUT,
    HAR_CAPTURE,
    HTTP_REQUEST,
    HTTP_RESPONSE,
    METADATA,
    SCREENSHOT,
    ArtifactMetadata,
    ArtifactStore,
    ArtifactStoreError,
    LocalArtifactStore,
    canonical_json_bytes,
    default_artifact_store,
    parse_artifact_id,
)
from .canonical_defect_registry import CANONICAL_DEFECT_REGISTRY_SCHEMA
from .evidence_artifact_store import (
    EvidenceArtifactError,
    _bundle_dir,
    _has_runtime_evidence,
    _hash_json,
    _now,
    _redact,
    _safe_project,
)

EVIDENCE_BUNDLE_MANIFEST_SCHEMA = "qualibug.evidence-bundle-manifest.v1"
EVIDENCE_BUNDLE_POINTER_SCHEMA = "qualibug.evidence-bundle-pointer.v1"
_LEGACY_BUNDLE_SCHEMA = "qualibug-evidence-bundle-v2"


def _pointer_path(root: Path, project: str, bundle_id: str) -> Path:
    return _bundle_dir(Path(root), project, bundle_id) / "manifest.pointer.json"


def _load_pointer(root: Path, project: str, bundle_id: str) -> dict[str, Any]:
    path = _pointer_path(Path(root), project, bundle_id)
    if not path.is_file():
        raise EvidenceArtifactError("evidence_bundle_pointer_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceArtifactError("evidence_bundle_pointer_unreadable") from exc
    if not isinstance(raw, dict) or not raw.get("manifest_ref"):
        raise EvidenceArtifactError("evidence_bundle_pointer_invalid")
    return raw


def _write_pointer(root: Path, project: str, bundle_id: str, manifest_ref: str) -> None:
    payload = {
        "schema_version": EVIDENCE_BUNDLE_POINTER_SCHEMA,
        "bundle_id": bundle_id,
        "manifest_ref": manifest_ref,
        "created_at_utc": _now(),
    }
    directory = _bundle_dir(Path(root), project, bundle_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.pointer.json"
    temporary = directory / f".manifest.pointer.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
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


# --------------------------------------------------------------------------
# Persistence (Single Write — new evidence goes through the artifact store)
# --------------------------------------------------------------------------
def persist_evidence_bundle_artifactized(
    project_id: str,
    *,
    root: Path,
    run_id: str,
    campaign: dict[str, Any] | None,
    runtime_contract: dict[str, Any] | None,
    execution_status: str,
    auto_har: dict[str, Any] | None,
    evidence_graphs: list[dict[str, Any]] | None,
    findings: list[dict[str, Any]] | None,
    candidate_findings: list[dict[str, Any]] | None = None,
    canonical_defect_registry: dict[str, Any] | None = None,
    delivery_occurrences: list[dict[str, Any]] | None = None,
    ui_execution: dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Persist an evidence bundle as fine-grained content-addressed artifacts.

    Mirrors ``evidence_artifact_store.persist_evidence_bundle``'s identity
    contract and return shape; the bundle payload itself lives in the artifact
    store and only a small pointer file lands in the legacy bundle directory.
    """
    project = _safe_project(project_id)
    store = artifact_store if artifact_store is not None else default_artifact_store(root)
    stats_before = store.snapshot_stats()
    campaign_record = campaign if isinstance(campaign, dict) else {}
    contract = runtime_contract if isinstance(runtime_contract, dict) else {}
    status = str(execution_status or "not_executed")
    canonical_findings = findings if isinstance(findings, list) else []
    candidates = candidate_findings if isinstance(candidate_findings, list) else []
    occurrences = delivery_occurrences if isinstance(delivery_occurrences, list) else []
    registry = (
        canonical_defect_registry
        if isinstance(canonical_defect_registry, dict)
        else {}
    )

    # ── Identity contract (same authority as the legacy writer) ──
    identity_authority_status = "UNVERIFIED_LEGACY"
    canonical_ids: list[str] = []
    occurrence_ids: list[str] = []
    if registry:
        if (
            registry.get("schema_version") != CANONICAL_DEFECT_REGISTRY_SCHEMA
            or registry.get("status") != "VERIFIED"
        ):
            raise EvidenceArtifactError("canonical_defect_registry_invalid")
        canonical_ids = [
            str(value or "").strip()
            for value in registry.get("canonical_defect_ids", [])
        ] if isinstance(registry.get("canonical_defect_ids"), list) else []
        occurrence_ids = [
            str(value or "").strip()
            for value in registry.get("delivery_occurrence_finding_ids", [])
        ] if isinstance(registry.get("delivery_occurrence_finding_ids"), list) else []
        finding_ids = [
            str(item.get("canonical_defect_id") or "").strip()
            for item in canonical_findings
            if isinstance(item, dict)
        ]
        persisted_occurrence_ids = sorted(
            str(item.get("finding_id") or item.get("id") or "").strip()
            for item in occurrences
            if isinstance(item, dict)
        )
        if (
            not all(canonical_ids)
            or len(canonical_ids) != len(set(canonical_ids))
            or finding_ids != canonical_ids
            or persisted_occurrence_ids != occurrence_ids
            or int(registry.get("canonical_defect_count") or 0)
            != len(canonical_ids)
            or int(registry.get("delivery_occurrence_count") or 0)
            != len(occurrence_ids)
        ):
            _diag = {
                "canonical_ids": canonical_ids[:5],
                "finding_ids": finding_ids[:5],
                "occurrence_ids": occurrence_ids[:5],
                "persisted_occurrence_ids": persisted_occurrence_ids[:5],
                "registry_canonical_count": registry.get("canonical_defect_count"),
                "registry_occurrence_count": registry.get("delivery_occurrence_count"),
            }
            raise EvidenceArtifactError(
                f"canonical_evidence_scope_mismatch:{_diag}"
            )
        identity_authority_status = "VERIFIED"

    # ── Stable bundle id (identical inputs → identical bundle id) ──
    fingerprint = _hash_json({
        "project": project,
        "run": str(run_id or ""),
        "campaign": campaign_record.get("campaign_id"),
        "source": contract.get("source_manifest"),
        "status": status,
        "har": auto_har or {},
        "graphs": evidence_graphs or [],
        "canonical_findings": canonical_findings,
        "candidate_findings": candidates,
        "canonical_defect_registry": registry,
        "delivery_occurrences": occurrences,
        "ui_execution": ui_execution or {},
    })
    bundle_id = f"evb_{fingerprint[:24]}"

    # ── Fine-grained parts (SPEC §12/§13) ──
    parts: dict[str, Any] = {
        "metadata_ref": None,
        "execution_output_ref": None,
        "db_snapshot_ref": None,
        "request_ref": None,
        "response_ref": None,
        "screenshot_ref": None,
        "dom_ref": None,
        "ui_state_ref": None,
        "logs_refs": [],
        "har_entries_refs": [],
    }
    artifacts: list[dict[str, Any]] = []

    metadata_ref = store.put(
        _redact({
            "runtime_contract": contract,
            "campaign": campaign_record,
            "source_manifest": _redact(
                contract.get("source_manifest")
                if isinstance(contract.get("source_manifest"), dict)
                else {}
            ),
        }),
        METADATA,
    )
    parts["metadata_ref"] = metadata_ref.artifact_id
    artifacts.append(_part_record(store, metadata_ref, "runtime_contract"))

    execution_output = {
        "findings": canonical_findings,
        "candidate_findings": candidates,
        "evidence_graphs": evidence_graphs if isinstance(evidence_graphs, list) else [],
        "canonical_defect_registry": registry,
        "delivery_occurrences": occurrences,
        "ui_execution": ui_execution if isinstance(ui_execution, dict) else {},
    }
    execution_ref = store.put(_redact(execution_output), EXECUTION_OUTPUT)
    parts["execution_output_ref"] = execution_ref.artifact_id
    artifacts.append(_part_record(store, execution_ref, "execution_output"))

    # Per-finding DB snapshots (before/after) — the piece most often repeated
    # across runs of the same campaign.
    db_snapshots: dict[str, Any] = {}
    for finding in canonical_findings:
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("canonical_defect_id") or finding.get("id") or "")
        if not finding_id:
            continue
        raw_evidence = (
            finding.get("raw_evidence")
            if isinstance(finding.get("raw_evidence"), dict)
            else {}
        )
        snapshot = raw_evidence.get("db_snapshot")
        if isinstance(snapshot, dict) and snapshot:
            db_snapshots[finding_id] = snapshot
    if db_snapshots:
        db_ref = store.put(_redact(db_snapshots), DB_SNAPSHOT)
        parts["db_snapshot_ref"] = db_ref.artifact_id
        artifacts.append(_part_record(store, db_ref, "db_snapshot"))

    # HAR entries — one HTTP_REQUEST / HTTP_RESPONSE artifact pair per entry
    # plus a fingerprint-safe index (urls/statuses/refs, never bodies).
    har_capture = auto_har if isinstance(auto_har, dict) else {"status": "no_traffic"}
    entries = har_capture.get("entries", []) if isinstance(har_capture.get("entries"), list) else []
    har_index: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        response = entry.get("response")
        request_ref = response_ref = None
        if isinstance(request, dict):
            request_ref = store.put(_redact(request), HTTP_REQUEST).artifact_id
            artifacts.append(
                _part_record(store, store.stat(request_ref), f"har_{index}_request")
            )
        if isinstance(response, dict):
            response_ref = store.put(_redact(response), HTTP_RESPONSE).artifact_id
            artifacts.append(
                _part_record(store, store.stat(response_ref), f"har_{index}_response")
            )
        if request_ref or response_ref:
            har_index.append({
                "entry_index": index,
                "method": str(request.get("method") or "")[:32] if isinstance(request, dict) else "",
                "url": str(request.get("url") or "")[:400] if isinstance(request, dict) else "",
                "status": int(response.get("status") or 0) if isinstance(response, dict) else 0,
                "request_ref": request_ref,
                "response_ref": response_ref,
            })
    if har_index:
        parts["har_entries_refs"] = har_index
        if not parts["request_ref"]:
            parts["request_ref"] = har_index[0].get("request_ref")
        if not parts["response_ref"]:
            parts["response_ref"] = har_index[0].get("response_ref")

    runtime_captured = _has_runtime_evidence(
        [*canonical_findings, *occurrences], auto_har
    )
    source_manifest = _redact(
        contract.get("source_manifest")
        if isinstance(contract.get("source_manifest"), dict)
        else {}
    )
    bundle_manifest = {
        "schema_version": EVIDENCE_BUNDLE_MANIFEST_SCHEMA,
        "bundle_id": bundle_id,
        "project_id": project,
        "run_id": str(run_id or "")[:160],
        "campaign_id": str(campaign_record.get("campaign_id") or "")[:160],
        "execution_status": status,
        "identity_authority_status": identity_authority_status,
        "canonical_defect_count": len(canonical_ids),
        "delivery_occurrence_count": len(occurrence_ids),
        "evidence_level": (
            "runtime_captured" if runtime_captured else "plan_or_no_traffic"
        ),
        "source_manifest": source_manifest,
        "created_at_utc": _now(),
        "parts": parts,
        "artifact_count": len(artifacts) + 1,  # parts + the manifest itself
    }
    manifest_ref = store.put(bundle_manifest, EVIDENCE_BUNDLE_MANIFEST)
    _write_pointer(root, project, bundle_id, manifest_ref.artifact_id)

    from .artifact_store import snapshot_lifecycle_delta

    return {
        "status": "persisted",
        "bundle_id": bundle_id,
        "manifest_ref": str(
            (_pointer_path(root, project, bundle_id)).relative_to(Path(root))
        ),
        "artifact_manifest_ref": manifest_ref.artifact_id,
        "bundle_sha256": manifest_ref.content_hash,
        "evidence_level": bundle_manifest["evidence_level"],
        "artifact_count": bundle_manifest["artifact_count"],
        "artifactized": True,
        "lifecycle": snapshot_lifecycle_delta(stats_before, store.snapshot_stats()),
    }


def _part_record(
    store: ArtifactStore, ref: Any, name: str
) -> dict[str, Any]:
    """Legacy-compatible artifact record for one stored part."""
    return {
        "name": name,
        "artifact_type": ref.artifact_type,
        "artifact_id": ref.artifact_id,
        "path": ref.artifact_id,  # runtime resolution via the store, never a machine path
        "sha256": ref.content_hash,
        "byte_count": ref.size_bytes,
    }


# --------------------------------------------------------------------------
# Read back (Dual Read — artifactized bundles hydrate the legacy bundle view)
# --------------------------------------------------------------------------
def load_evidence_bundle_v2(
    project_id: str,
    bundle_id: str,
    *,
    root: Path,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Hydrate the legacy bundle dict from the artifactized bundle.

    The pointer file is the only per-run physical residue; every payload is
    read from the content-addressed store (SPEC §33 Dual Read).
    """
    project = _safe_project(project_id)
    pointer = _load_pointer(Path(root), project, bundle_id)
    manifest_ref = str(pointer.get("manifest_ref") or "")
    store = artifact_store if artifact_store is not None else default_artifact_store(root)
    try:
        parse_artifact_id(manifest_ref)
    except ArtifactStoreError as exc:
        raise EvidenceArtifactError("evidence_bundle_pointer_invalid") from exc
    if not store.exists(manifest_ref):
        raise EvidenceArtifactError("evidence_bundle_manifest_missing")
    manifest = store.get_json(manifest_ref)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != EVIDENCE_BUNDLE_MANIFEST_SCHEMA:
        raise EvidenceArtifactError("evidence_bundle_manifest_invalid")
    parts = manifest.get("parts") if isinstance(manifest.get("parts"), dict) else {}

    artifacts: list[dict[str, Any]] = []
    for name, ref in (
        ("metadata", parts.get("metadata_ref")),
        ("execution_output", parts.get("execution_output_ref")),
        ("db_snapshot", parts.get("db_snapshot_ref")),
        ("request", parts.get("request_ref")),
        ("response", parts.get("response_ref")),
        ("screenshot", parts.get("screenshot_ref")),
        ("dom", parts.get("dom_ref")),
        ("ui_state", parts.get("ui_state_ref")),
    ):
        if not ref:
            continue
        try:
            record = _part_record(store, store.stat(str(ref)), name)
        except ArtifactStoreError as exc:
            raise EvidenceArtifactError("evidence_bundle_part_missing") from exc
        artifacts.append(record)
    for index, entry in enumerate(parts.get("logs_refs") or []):
        if not entry:
            continue
        artifacts.append(
            _part_record(store, store.stat(str(entry)), f"logs_{index}")
        )
    for entry in parts.get("har_entries_refs") or []:
        if not isinstance(entry, dict):
            continue
        for key, name in (("request_ref", "har_request"), ("response_ref", "har_response")):
            ref = entry.get(key)
            if ref:
                artifacts.append(_part_record(store, store.stat(str(ref)), name))

    return {
        "schema_version": _LEGACY_BUNDLE_SCHEMA,
        "bundle_id": str(manifest.get("bundle_id") or bundle_id),
        "project_id": str(manifest.get("project_id") or project),
        "run_id": str(manifest.get("run_id") or ""),
        "campaign_id": str(manifest.get("campaign_id") or ""),
        "execution_status": str(manifest.get("execution_status") or ""),
        "identity_authority_status": str(manifest.get("identity_authority_status") or "UNVERIFIED_LEGACY"),
        "canonical_defect_count": int(manifest.get("canonical_defect_count") or 0),
        "delivery_occurrence_count": int(manifest.get("delivery_occurrence_count") or 0),
        "evidence_level": str(manifest.get("evidence_level") or "plan_or_no_traffic"),
        "source_manifest": (
            manifest.get("source_manifest")
            if isinstance(manifest.get("source_manifest"), dict)
            else {}
        ),
        "created_at_utc": str(manifest.get("created_at_utc") or ""),
        "artifacts": artifacts,
        "bundle_sha256": manifest_ref.split(":", 1)[-1],
        "artifactized": True,
        "parts": parts,
    }


def verify_evidence_bundle_v2(
    project_id: str,
    bundle_id: str,
    *,
    root: Path,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Verify an artifactized bundle: manifest artifact present + every part
    ref present in the store (content-addressed ids ARE the hash check)."""
    project = _safe_project(project_id)
    pointer = _load_pointer(Path(root), project, bundle_id)
    manifest_ref = str(pointer.get("manifest_ref") or "")
    store = artifact_store if artifact_store is not None else default_artifact_store(root)
    try:
        parse_artifact_id(manifest_ref)
    except ArtifactStoreError as exc:
        raise EvidenceArtifactError("evidence_bundle_pointer_invalid") from exc
    if not store.exists(manifest_ref):
        return {"valid": False, "code": "EVIDENCE_BUNDLE_MANIFEST_MISSING", "checked": 0}
    manifest = store.get_json(manifest_ref)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != EVIDENCE_BUNDLE_MANIFEST_SCHEMA:
        return {"valid": False, "code": "EVIDENCE_BUNDLE_MANIFEST_INVALID", "checked": 0}
    parts = manifest.get("parts") if isinstance(manifest.get("parts"), dict) else {}
    refs: list[str] = []
    for key in (
        "metadata_ref", "execution_output_ref", "db_snapshot_ref",
        "request_ref", "response_ref", "screenshot_ref", "dom_ref", "ui_state_ref",
    ):
        value = parts.get(key)
        if value:
            refs.append(str(value))
    refs.extend(str(item) for item in (parts.get("logs_refs") or []) if item)
    for entry in parts.get("har_entries_refs") or []:
        if isinstance(entry, dict):
            for key in ("request_ref", "response_ref"):
                if entry.get(key):
                    refs.append(str(entry[key]))
    checked = 0
    for ref in refs:
        if not store.exists(ref):
            return {"valid": False, "code": "EVIDENCE_ARTIFACT_MISSING", "checked": checked}
        checked += 1
    return {
        "valid": True,
        "checked": checked,
        "bundle_sha256": manifest_ref.split(":", 1)[-1],
    }


def load_evidence_bundle_report_view(
    project_id: str,
    bundle_id: str,
    *,
    root: Path,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Read surface for report loading: hydrated manifest + campaign +
    findings, without materializing evidence back to disk.

    The campaign comes from the small METADATA artifact; findings come from
    the EXECUTION_OUTPUT artifact (both canonical JSON in the store).
    """
    manifest = load_evidence_bundle_v2(
        project_id, bundle_id, root=root, artifact_store=artifact_store
    )
    store = artifact_store if artifact_store is not None else default_artifact_store(root)
    parts = manifest.get("parts") if isinstance(manifest.get("parts"), dict) else {}
    campaign: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    metadata_ref = parts.get("metadata_ref")
    if metadata_ref:
        try:
            meta = store.get_json(str(metadata_ref))
        except ArtifactStoreError:
            meta = None
        if isinstance(meta, dict) and isinstance(meta.get("campaign"), dict):
            campaign = dict(meta["campaign"])
    execution_ref = parts.get("execution_output_ref")
    if execution_ref:
        try:
            payload = store.get_json(str(execution_ref))
        except ArtifactStoreError:
            payload = None
        if isinstance(payload, dict):
            raw_findings = payload.get("findings")
            if isinstance(raw_findings, list):
                findings = [item for item in raw_findings if isinstance(item, dict)]
    return {"manifest": manifest, "campaign": campaign, "findings": findings}
