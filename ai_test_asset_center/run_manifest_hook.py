# -*- coding: utf-8 -*-
"""Run Manifest post-scan hook (SPEC P0-4, Phase 2 wiring).

Registered through the first-class ``scan_post_hooks`` convention (never by
replacing ``__main__.scan``): after the core scan body persists its products,
this hook artifactizes the run outputs and atomically commits the Run
Manifest with the SPEC §15 ordering — put artifacts -> verify every reference
exists -> write manifest.tmp -> atomic rename -> Run=SUCCESS.

- SUCCESS runs: scan_result index, evidence bundle manifest ref, intelligence
  report index are referenced; trace artifactization is Phase 4 (trace_refs
  stay empty here).
- FAILED/ABORTED runs (SPEC §16): only metadata + error summary + the small
  failed scan_result index — never large evidence.

The hook can never mask the original scan result: any failure is attached to
the result as a visible ``run_manifest_receipt`` (``status: failed``), never
raised into the scan.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .artifact_store import (
    INTELLIGENCE_REPORT,
    SCAN_RESULT,
    ArtifactStoreError,
    artifact_store_enabled,
    default_artifact_store,
    merge_lifecycle_deltas,
)
from .run_manifest import (
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    RunManifestStore,
)

HOOK_NAME = "run_manifest_commit"
_SAFE_PROJECT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def install_run_manifest_hook() -> None:
    """Register the manifest-commit post hook idempotently."""
    from .scan_post_hooks import register_scan_post_hook

    register_scan_post_hook(HOOK_NAME, _commit_run_manifest)


def _commit_run_manifest(
    result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    if not artifact_store_enabled():
        return result
    try:
        receipt = _commit(result, project=project, root=root)
    except Exception as exc:
        # Visible, non-masking failure receipt (SPEC §15 — no manifest is
        # committed when the run could not be finalized).
        if isinstance(result, dict):
            result["run_manifest_receipt"] = {
                "schema_version": "qualibug.run-manifest-receipt.v1",
                "status": "failed",
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            }
        return result
    if isinstance(result, dict):
        result["run_manifest_receipt"] = receipt
    return result


def _commit(result: dict[str, Any], *, project: str, root: Path) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"status": "skipped", "reason": "result_not_dict"}
    run_id = str(result.get("scan_id") or "")
    if not run_id:
        return {"status": "skipped", "reason": "scan_id_missing"}

    store = default_artifact_store(root)
    manifest_store = RunManifestStore(store, root)
    stats_before = store.snapshot_stats()

    project_safe = _SAFE_PROJECT_RE.sub("_", str(project or "").strip())
    output_root = Path(root) / "platform_outputs" / project_safe

    # ── Artifactize the run outputs (streaming put_file for files) ──
    scan_result_ref = None
    scan_result_path = output_root / "scan_result.json"
    if scan_result_path.is_file():
        try:
            scan_result_ref = store.put_file(
                scan_result_path, SCAN_RESULT
            ).artifact_id
        except ArtifactStoreError:
            scan_result_ref = None

    report_ref = None
    report_path = output_root / "intelligence_report.json"
    if report_path.is_file():
        try:
            report_ref = store.put_file(
                report_path, INTELLIGENCE_REPORT
            ).artifact_id
        except ArtifactStoreError:
            report_ref = None

    evidence_refs: list[str] = []
    evidence = result.get("evidence_bundle")
    if isinstance(evidence, dict) and evidence.get("status") == "persisted":
        manifest_ref = evidence.get("artifact_manifest_ref")
        if manifest_ref and store.exists(str(manifest_ref)):
            evidence_refs.append(str(manifest_ref))

    lifecycle = merge_lifecycle_deltas(
        _evidence_lifecycle(evidence),
        _lifecycle_delta(stats_before, store.snapshot_stats()),
    )

    # ── Status decision (SPEC §15/§16): a run that finalized its terminal
    # lifecycle (including contract-blocked scans) is SUCCESS; anything that
    # failed safe is FAILED and keeps only metadata + error summary. ──
    execution_status = str(result.get("execution_status") or "")
    if bool(result.get("success")):
        manifest = manifest_store.commit_success(
            run_id,
            scan_result_ref=scan_result_ref,
            evidence_bundle_refs=evidence_refs,
            intelligence_report_ref=report_ref,
            lifecycle=lifecycle,
        )
        return {
            "schema_version": "qualibug.run-manifest-receipt.v1",
            "status": "committed",
            "run_id": run_id,
            "manifest_status": manifest.status,
            "scan_result_ref": scan_result_ref,
            "evidence_bundle_refs": evidence_refs,
            "intelligence_report_ref": report_ref,
            "lifecycle": lifecycle,
        }

    error_summary = str(
        result.get("error")
        or result.get("reason")
        or result.get("grade")
        or execution_status
        or "scan_incomplete"
    )[:2000]
    manifest = manifest_store.commit_failed(
        run_id,
        status=RUN_STATUS_FAILED,
        error_summary=error_summary,
        scan_result_ref=scan_result_ref,
    )
    return {
        "schema_version": "qualibug.run-manifest-receipt.v1",
        "status": "committed",
        "run_id": run_id,
        "manifest_status": manifest.status,
        "scan_result_ref": scan_result_ref,
        "error_summary": error_summary[:160],
        "lifecycle": lifecycle,
    }


def _evidence_lifecycle(evidence: Any) -> dict[str, Any] | None:
    """Per-run evidence lifecycle recorded by the artifactized persist."""
    if not isinstance(evidence, dict):
        return None
    lifecycle = evidence.get("lifecycle")
    return lifecycle if isinstance(lifecycle, dict) else None


def _lifecycle_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, Any]:
    """Per-run lifecycle summary (SPEC §37): deltas over this run's puts."""
    from .artifact_store import snapshot_lifecycle_delta

    return snapshot_lifecycle_delta(before, after)
