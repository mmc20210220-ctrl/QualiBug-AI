"""Immutable, redacted evidence bundles for enterprise execution runs.

Bundles are intentionally separate from a finding's status: persisting a bundle
never confirms a defect. Confirmation remains governed by the complete runtime
receipt contract in ``enterprise_campaign.has_real_confirmation_receipt``.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .canonical_defect_registry import CANONICAL_DEFECT_REGISTRY_SCHEMA


_SENSITIVE_KEYS = {
    "authorization", "cookie", "set-cookie", "password", "passwd", "secret",
    "token", "access_token", "refresh_token", "api_key", "apikey", "client_secret",
}
_MAX_TEXT = 100_000


class EvidenceArtifactError(RuntimeError):
    """Evidence cannot be safely persisted or verified."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_project(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("_", "-")
            result[str(key)] = "<REDACTED>" if normalized in _SENSITIVE_KEYS else _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value[:500]]
    if isinstance(value, str):
        return value[:_MAX_TEXT] + "…" if len(value) > _MAX_TEXT else value
    return value


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _bundle_dir(root: Path, project_id: str, bundle_id: str) -> Path:
    return Path(root) / "platform_workspace" / _safe_project(project_id) / "evidence_bundles" / bundle_id


def _write_artifact(directory: Path, name: str, value: Any) -> dict[str, Any]:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "artifact"
    path = directory / f"{safe_name}.json"
    payload = _redact(value)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode("utf-8")
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return {"name": safe_name, "path": path.name, "sha256": _hash_bytes(raw), "byte_count": len(raw)}


def _has_runtime_evidence(findings: list[dict[str, Any]] | None, auto_har: dict[str, Any] | None) -> bool:
    har_status = str((auto_har or {}).get("status") or "no_traffic") if isinstance(auto_har, dict) else "no_traffic"
    if har_status == "captured":
        return True
    for finding in findings if isinstance(findings, list) else []:
        if not isinstance(finding, dict):
            continue
        raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        db_snapshot = raw.get("db_snapshot") if isinstance(raw.get("db_snapshot"), dict) else {}
        db_evidence = finding.get("db_evidence") if isinstance(finding.get("db_evidence"), dict) else {}
        response_raw = raw.get("response_raw") if isinstance(raw.get("response_raw"), dict) else {}
        request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
        if response_raw.get("status_code") or response_raw.get("body"):
            return True
        if evidence.get("status_code") or evidence.get("payload_summary"):
            return True
        if db_snapshot.get("assertion") or (db_snapshot.get("before") and db_snapshot.get("after")):
            return True
        if db_evidence.get("db_assertion") or db_evidence.get("assertion"):
            return True
        if (
            request_raw.get("method")
            and request_raw.get("path")
            and (finding.get("execution_status") == "executed" or raw.get("has_real_evidence"))
        ):
            return True
    return False


def persist_evidence_bundle(
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
) -> dict[str, Any]:
    """Persist a redacted evidence bundle and return its integrity manifest."""
    project = _safe_project(project_id)
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
    identity_authority_status = "UNVERIFIED_LEGACY"
    canonical_ids: list[str] = []
    occurrence_ids: list[str] = []
    if registry:
        if (
            registry.get("schema_version")
            != CANONICAL_DEFECT_REGISTRY_SCHEMA
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
                "occurrences_len": len(occurrences),
                "canonical_findings_len": len(canonical_findings),
            }
            raise EvidenceArtifactError(f"canonical_evidence_scope_mismatch:{_diag}")
        identity_authority_status = "VERIFIED"
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
    directory = _bundle_dir(Path(root), project, bundle_id)
    directory.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, Any]] = []
    artifacts.append(_write_artifact(directory, "runtime_contract", contract))
    artifacts.append(_write_artifact(directory, "campaign", campaign_record))
    artifacts.append(_write_artifact(directory, "auto_har", auto_har if isinstance(auto_har, dict) else {"status": "no_traffic"}))
    artifacts.append(_write_artifact(directory, "evidence_graphs", evidence_graphs if isinstance(evidence_graphs, list) else []))
    artifacts.append(_write_artifact(directory, "findings", canonical_findings))
    artifacts.append(_write_artifact(directory, "candidate_findings", candidates))
    if registry:
        artifacts.append(
            _write_artifact(directory, "canonical_defect_registry", registry)
        )
        artifacts.append(
            _write_artifact(directory, "delivery_occurrences", occurrences)
        )
    artifacts.append(_write_artifact(directory, "ui_execution", ui_execution if isinstance(ui_execution, dict) else {"status": "not_requested"}))

    runtime_captured = _has_runtime_evidence(
        [*canonical_findings, *occurrences], auto_har
    )
    bundle = {
        "schema_version": "qualibug-evidence-bundle-v2",
        "bundle_id": bundle_id,
        "project_id": project,
        "run_id": str(run_id or "")[:160],
        "campaign_id": str(campaign_record.get("campaign_id") or "")[:160],
        "execution_status": status,
        "identity_authority_status": identity_authority_status,
        "canonical_defect_count": len(canonical_ids),
        "delivery_occurrence_count": len(occurrence_ids),
        "evidence_level": "runtime_captured" if runtime_captured else "plan_or_no_traffic",
        "source_manifest": _redact(contract.get("source_manifest") if isinstance(contract.get("source_manifest"), dict) else {}),
        "created_at_utc": _now(),
        "artifacts": artifacts,
        "bundle_sha256": "",
    }
    bundle["bundle_sha256"] = _hash_json({key: value for key, value in bundle.items() if key != "bundle_sha256"})
    _atomic_json(directory / "manifest.json", bundle)
    return {
        "status": "persisted",
        "bundle_id": bundle_id,
        "manifest_ref": str((directory / "manifest.json").relative_to(Path(root))),
        "bundle_sha256": bundle["bundle_sha256"],
        "evidence_level": bundle["evidence_level"],
        "artifact_count": len(artifacts),
    }


def load_evidence_bundle(project_id: str, bundle_id: str, *, root: Path) -> dict[str, Any]:
    directory = _bundle_dir(Path(root), project_id, str(bundle_id))
    if (directory / "manifest.pointer.json").is_file():
        # ── P0-4 Dual Read: artifactized bundles hydrate the legacy bundle
        # view from the content-addressed store (SPEC §33); no copy of the
        # evidence is materialized back to disk. ──
        from .evidence_artifactization import load_evidence_bundle_v2

        return load_evidence_bundle_v2(project_id, str(bundle_id), root=Path(root))
    path = directory / "manifest.json"
    if not path.exists():
        raise EvidenceArtifactError("evidence_bundle_missing")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceArtifactError("evidence_bundle_unreadable") from exc
    if not isinstance(manifest, dict):
        raise EvidenceArtifactError("evidence_bundle_invalid")
    return manifest


def verify_evidence_bundle(project_id: str, bundle_id: str, *, root: Path) -> dict[str, Any]:
    directory = _bundle_dir(Path(root), project_id, bundle_id)
    if (directory / "manifest.pointer.json").is_file():
        from .evidence_artifactization import verify_evidence_bundle_v2

        return verify_evidence_bundle_v2(project_id, str(bundle_id), root=Path(root))
    manifest = load_evidence_bundle(project_id, bundle_id, root=root)
    directory = _bundle_dir(Path(root), project_id, bundle_id)
    checked = 0
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            return {"valid": False, "code": "EVIDENCE_ARTIFACT_INVALID", "checked": checked}
        path = directory / str(artifact.get("path") or "")
        if not path.exists():
            return {"valid": False, "code": "EVIDENCE_ARTIFACT_MISSING", "checked": checked}
        if _hash_bytes(path.read_bytes()) != str(artifact.get("sha256") or ""):
            return {"valid": False, "code": "EVIDENCE_ARTIFACT_HASH_MISMATCH", "checked": checked}
        checked += 1
    expected = str(manifest.get("bundle_sha256") or "")
    actual = _hash_json({key: value for key, value in manifest.items() if key != "bundle_sha256"})
    if expected != actual:
        return {"valid": False, "code": "EVIDENCE_BUNDLE_HASH_MISMATCH", "checked": checked}
    return {"valid": True, "checked": checked, "bundle_sha256": expected}
