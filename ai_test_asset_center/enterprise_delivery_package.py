"""Create a customer-safe delivery package from verified scan evidence.

The package includes only the authoritative scan result, release decision and
redacted evidence artifacts. It refuses to package a scan whose bundle fails
integrity verification.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any


class DeliveryPackageError(ValueError):
    """A customer delivery package cannot be produced safely."""


def _safe(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryPackageError(f"delivery_json_unreadable:{path.name}") from exc
    if not isinstance(payload, dict):
        raise DeliveryPackageError(f"delivery_json_invalid:{path.name}")
    return payload


def _read_scan_result(path: Path) -> dict[str, Any]:
    """Read scan_result via the sharded store loader（分片/旧单文件自动兼容）。"""
    from .scan_result_store import load_scan_result

    if not path.is_file():
        raise DeliveryPackageError(f"delivery_json_unreadable:{path.name}")
    try:
        payload = load_scan_result(path, keys=["findings", "candidate_findings", "delivery_occurrences", "evidence_bundle", "release_gate", "campaign", "runtime_contract", "total_findings"])
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        raise DeliveryPackageError(f"delivery_json_unreadable:{path.name}") from exc
    if not isinstance(payload, dict):
        raise DeliveryPackageError(f"delivery_json_invalid:{path.name}")
    return payload


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_delivery_package(
    project_id: str,
    *,
    root: Path,
    scan_result: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Create ZIP only when evidence bundle verification and release facts exist."""
    workspace = Path(root)
    project = _safe(project_id)
    result = scan_result or _read_scan_result(workspace / "platform_outputs" / project / "scan_result.json")
    evidence = result.get("evidence_bundle") if isinstance(result.get("evidence_bundle"), dict) else {}
    bundle_id = str(evidence.get("bundle_id") or "")
    if str(evidence.get("status") or "") != "persisted" or not bundle_id:
        raise DeliveryPackageError("delivery_evidence_bundle_missing")
    release_gate = result.get("release_gate") if isinstance(result.get("release_gate"), dict) else {}
    if not release_gate:
        raise DeliveryPackageError("delivery_release_gate_missing")
    try:
        from .evidence_artifact_store import load_evidence_bundle, verify_evidence_bundle
        verification = verify_evidence_bundle(project_id, bundle_id, root=workspace)
        if verification.get("valid") is not True:
            raise DeliveryPackageError("delivery_evidence_bundle_not_verified")
        manifest = load_evidence_bundle(project_id, bundle_id, root=workspace)
    except DeliveryPackageError:
        raise
    except Exception as exc:
        raise DeliveryPackageError("delivery_evidence_bundle_unreadable") from exc

    bundle_dir = workspace / "platform_workspace" / project / "evidence_bundles" / bundle_id
    if not bundle_dir.exists():
        raise DeliveryPackageError("delivery_evidence_bundle_directory_missing")
    release_verdict = str(release_gate.get("verdict") or "not_ready")
    if release_verdict == "pass" and int(result.get("total_findings") or 0) <= 0:
        release_verdict = "not_ready"
    destination = Path(output_dir) if output_dir else workspace / "platform_outputs" / project / "delivery_packages"
    destination.mkdir(parents=True, exist_ok=True)
    package_id = f"delivery_{project}_{bundle_id}"
    archive_path = destination / f"{package_id}.zip"
    manifest_payload = {
        "schema_version": "qualibug-delivery-package-v1",
        "project_id": project_id,
        "package_id": package_id,
        "bundle_id": bundle_id,
        "release_gate": release_gate,
        "campaign": result.get("campaign") if isinstance(result.get("campaign"), dict) else {},
        "source_manifest": (result.get("runtime_contract") or {}).get("source_manifest", {}) if isinstance(result.get("runtime_contract"), dict) else {},
        "artifact_count": len(manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []),
        "artifactized": bool(manifest.get("artifactized")),
    }
    temporary = archive_path.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("delivery_manifest.json", json.dumps(manifest_payload, ensure_ascii=False, indent=2, default=str))
            archive.writestr("scan_result.json", json.dumps(result, ensure_ascii=False, indent=2, default=str))
            archive.writestr("evidence_bundle_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
            if manifest.get("artifactized"):
                # ── P0-4 Dual Read: pull each fine-grained part from the
                # content-addressed store (streaming); nothing is copied back
                # to the bundle directory. ──
                from .artifact_store import default_artifact_store

                store = default_artifact_store(workspace)
                for index, artifact in enumerate(manifest.get("artifacts", [])):
                    if not isinstance(artifact, dict):
                        continue
                    artifact_id = str(artifact.get("artifact_id") or "")
                    if not artifact_id or not store.exists(artifact_id):
                        continue
                    safe_name = str(artifact.get("name") or "artifact")
                    with store.open(artifact_id) as source_stream:
                        archive.writestr(
                            f"evidence/{index:04d}_{safe_name}.json",
                            source_stream.read(),
                        )
            else:
                for artifact in manifest.get("artifacts", []):
                    if not isinstance(artifact, dict):
                        continue
                    relative = str(artifact.get("path") or "")
                    source = bundle_dir / relative
                    if source.is_file():
                        archive.write(source, arcname=f"evidence/{relative}")
        temporary.replace(archive_path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return {
        "status": "created",
        "package_id": package_id,
        "package_ref": str(archive_path.relative_to(workspace)),
        "sha256": _hash_file(archive_path),
        "release_verdict": release_verdict,
        "evidence_bundle_id": bundle_id,
    }
