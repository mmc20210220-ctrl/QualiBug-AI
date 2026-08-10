# -*- coding: utf-8 -*-
"""Intelligence report deduplication (SPEC P0-4, Phase 6, §25/§43).

The report is a *logical* read model: "report logical information != copy all
raw evidence" (SPEC §43). The measured 133 MB report is dominated by payloads
that duplicate the artifact store — ``obligation_attempt_ledger`` (~59 MB),
duplicated finding lists with embedded ``raw_evidence`` (~8 MB),
``delivery_occurrences`` (~6 MB) — plus projections that are re-derivable
from scan_result/evidence artifacts.

This module rewrites the report as **finding summaries + artifact refs**:

- every top-level payload over a size threshold is stored once in the
  content-addressed store and replaced by ``<key>_ref`` + ``<key>_summary``
  (a compact, generic projection — never a re-embedding);
- finding lists keep every summary/status field but drop ``raw_evidence`` and
  carry ``evidence_refs`` pointing at the evidence-bundle execution-output
  artifact (the same evidence, stored once);
- every reference embedded anywhere is also collected into the top-level
  ``artifact_refs`` list so the reference GC can keep them live without
  parsing report internals.

Dual Read / Single Write (SPEC §33): when the artifact store is disabled the
legacy full report is written unchanged; when enabled only the compact report
is written. Consumers of the report surface (frontend loading via
``private_pilot_report_loading``) keep reading the same summary keys —
``real_findings`` / ``findings`` / ``campaign`` / ``total_findings`` —
unchanged in shape, smaller in bytes.

Redaction parity (SPEC §46): the compact report still passes through
``write_json_redacted`` (the same fail-closed secret scan the legacy writer
uses). Artifact metadata never carries credentials.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .artifact_store import (
    ArtifactStore,
    ArtifactStoreError,
    artifact_store_enabled,
    default_artifact_store,
)
from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

REPORT_ARTIFACTIZATION_SCHEMA = "qualibug.intelligence-report-artifactization.v1"

# Product report-schema fields that are stored as artifacts when they exceed
# the embed threshold (open list — it is the product's own report contract,
# not industry/business data; any of these keys may simply be absent).
REPORT_HEAVY_PAYLOAD_KEYS = (
    "obligation_attempt_ledger",
    "behavior_slice_ledger",
    "delivery_occurrences",
    "discovery_funnel",
    "formal_count_projection",
    "canonical_defect_registry",
    "test_data_bootstrap",
)

REPORT_EMBED_MAX_BYTES_DEFAULT = 256 * 1024
_FINDING_LIST_KEYS = ("real_findings", "findings", "bug_scores")


def report_embed_max_bytes() -> int:
    """``QUALIBUG_REPORT_EMBED_MAX_BYTES`` (default 256 KiB, SPEC §43)."""
    raw = str(os.getenv("QUALIBUG_REPORT_EMBED_MAX_BYTES", "") or "").strip()
    if not raw:
        return REPORT_EMBED_MAX_BYTES_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return REPORT_EMBED_MAX_BYTES_DEFAULT
    return value if value >= 0 else REPORT_EMBED_MAX_BYTES_DEFAULT


def _serialized_size(value: Any) -> int:
    try:
        import json

        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def _compact_summary(key: str, value: Any, ref: str) -> dict[str, Any]:
    """Generic summary for an artifactized payload — counts and identity
    scalars only, never raw content (SPEC §25: finding summary + refs)."""
    summary: dict[str, Any] = {"artifactized": True, "ref": ref}
    if isinstance(value, dict):
        summary["kind"] = "object"
        summary["field_count"] = len(value)
        for name in (
            "schema_version",
            "run_id",
            "campaign_id",
            "created_at_utc",
            "ledger_fingerprint",
            "complete",
        ):
            item = value.get(name)
            if item is not None and not isinstance(item, (list, dict)):
                # The redactor's reseal walker matches sealed schemas by the
                # literal `schema_version` value — the summary must never
                # carry that key (it is not a ledger).
                summary["source_" + name] = item
    elif isinstance(value, list):
        summary["kind"] = "list"
        summary["element_count"] = len(value)
    else:
        summary["kind"] = type(value).__name__
    return summary


def _compact_findings(
    findings: list[dict[str, Any]],
    execution_output_ref: str | None,
) -> list[dict[str, Any]]:
    """Strip embedded raw evidence from finding summaries (SPEC §25/§43).

    Every summary/status field is preserved; the raw evidence payload is
    replaced by ``evidence_refs`` pointing at the evidence-bundle
    execution-output artifact that already holds the full findings.
    """
    compact: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        row = dict(finding)
        if isinstance(row.get("raw_evidence"), dict) and row["raw_evidence"]:
            row.pop("raw_evidence", None)
        if execution_output_ref:
            row["evidence_refs"] = [execution_output_ref]
        compact.append(row)
    return compact


def _extract_execution_output_ref(
    store: ArtifactStore, bundle_manifest_ref: str | None
) -> str | None:
    """The evidence-bundle EXECUTION_OUTPUT artifact id, when available."""
    if not bundle_manifest_ref:
        return None
    try:
        manifest = store.get_json(bundle_manifest_ref)
    except ArtifactStoreError:
        return None
    if not isinstance(manifest, dict):
        return None
    parts = manifest.get("parts")
    if not isinstance(parts, dict):
        return None
    ref = parts.get("execution_output_ref")
    return str(ref) if ref else None


def compact_intelligence_report(
    payload: dict[str, Any],
    store: ArtifactStore,
    *,
    bundle_manifest_ref: str | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Rewrite the report payload as summary + artifact refs.

    Returns ``(compact_payload, refs, stats)``. Never mutates ``payload``.
    Fails fast (raises) on store errors — a report that cannot reference its
    evidence must not silently pretend the evidence is embedded.
    """
    if not isinstance(payload, dict):
        raise ValueError("intelligence_report_payload_object_required")
    compact = dict(payload)
    refs: list[str] = []
    artifactized_keys: list[str] = []
    removed_bytes = 0
    threshold = report_embed_max_bytes()

    # ── 1. Heavy top-level payloads → artifacts (summary + ref) ──
    for key in REPORT_HEAVY_PAYLOAD_KEYS:
        value = compact.get(key)
        if value is None:
            continue
        size = _serialized_size(value)
        if size <= threshold:
            continue
        # Redaction parity with the legacy writer (SPEC §46): the payload is
        # redacted+validated before it enters the store, and unredacted
        # material fails closed instead of being persisted.
        from .artifact_redactor import redact_and_validate

        redacted, _receipt = redact_and_validate(value)
        if redacted != value:
            raise ValueError(f"report_payload_unredacted:{key}")
        ref = store.put(redacted, _artifact_type_for_report_key(key)).artifact_id
        refs.append(ref)
        removed_bytes += size
        artifactized_keys.append(key)
        compact[key + "_ref"] = ref
        compact[key + "_summary"] = _compact_summary(key, value, ref)
        del compact[key]

    # ── 2. Finding lists: summaries + evidence_refs (no raw evidence) ──
    execution_output_ref = _extract_execution_output_ref(store, bundle_manifest_ref)
    for key in _FINDING_LIST_KEYS:
        findings = compact.get(key)
        if not isinstance(findings, list):
            continue
        compact_findings = _compact_findings(findings, execution_output_ref)
        removed_bytes += max(0, _serialized_size(findings) - _serialized_size(compact_findings))
        compact[key] = compact_findings
    if execution_output_ref and execution_output_ref not in refs:
        refs.append(execution_output_ref)

    # ── 3. Every embedded ref is declared once for the reference GC ──
    compact["artifact_refs"] = sorted(set(refs))
    compact["report_artifactization"] = {
        "schema_version": REPORT_ARTIFACTIZATION_SCHEMA,
        "artifactized_keys": artifactized_keys,
        "ref_count": len(refs),
        "removed_embedded_bytes": removed_bytes,
    }
    stats = {
        "schema_version": REPORT_ARTIFACTIZATION_SCHEMA,
        "artifactized_keys": artifactized_keys,
        "removed_embedded_bytes": removed_bytes,
        "ref_count": len(refs),
    }
    return compact, refs, stats


def _artifact_type_for_report_key(key: str) -> str:
    """Map a report field to a stable artifact type (product contract)."""
    return "OBLIGATION_ATTEMPT_LEDGER" if key == "obligation_attempt_ledger" else (
        "BEHAVIOR_SLICE_LEDGER" if key == "behavior_slice_ledger" else (
            "DELIVERY_OCCURRENCES" if key == "delivery_occurrences" else (
                "DISCOVERY_FUNNEL" if key == "discovery_funnel" else (
                    "FORMAL_COUNT_PROJECTION" if key == "formal_count_projection" else (
                        "CANONICAL_DEFECT_REGISTRY" if key == "canonical_defect_registry" else
                        "TEST_DATA_BOOTSTRAP"
                    )
                )
            )
        )
    )


def write_intelligence_report(
    path: Path | str,
    payload: dict[str, Any],
    *,
    root: Path | str,
    bundle_manifest_ref: str | None = None,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Write the report through the Single-Write surface (SPEC §33/§43).

    Store enabled: compact summary + refs are written to ``path`` (still via
    the fail-closed ``write_json_redacted`` secret scan); store disabled:
    the legacy full report is written unchanged. Returns a receipt.
    """
    target = Path(path)
    if not artifact_store_enabled():
        write_json_redacted(target, payload)
        return {"schema_version": REPORT_ARTIFACTIZATION_SCHEMA, "mode": "legacy"}
    active_store = store if store is not None else default_artifact_store(root)
    compact, refs, stats = compact_intelligence_report(
        payload, active_store, bundle_manifest_ref=bundle_manifest_ref
    )
    try:
        write_json_redacted(target, compact)
    except ArtifactSecretLeakError:
        # Fail closed: never leave a secret-bearing report on disk.
        raise
    return {"schema_version": REPORT_ARTIFACTIZATION_SCHEMA, "mode": "artifact_store", **stats}


def verify_report_refs(
    store: ArtifactStore,
    report_artifact_id: str,
) -> dict[str, Any]:
    """Verify a stored INTELLIGENCE_REPORT artifact's declared refs exist.

    Returns ``{valid, checked, missing_refs}`` — the machine check behind
    "report refs are live" used by tests and the run-manifest verify path.
    """
    manifest = store.get_json(report_artifact_id)
    if not isinstance(manifest, dict):
        raise ArtifactStoreError(f"report_artifact_invalid:{report_artifact_id}")
    refs: list[str] = []
    declared = manifest.get("artifact_refs")
    if isinstance(declared, list):
        refs.extend(str(item) for item in declared if item)
    missing = [ref for ref in refs if not store.exists(ref)]
    return {
        "valid": not missing,
        "checked": len(refs),
        "missing_refs": missing,
    }
