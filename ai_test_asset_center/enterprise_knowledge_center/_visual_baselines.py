"""Governed lifecycle for project-scoped formal UI visual baselines.

Visual baselines are executable test authority, not ordinary uploaded images.
This registry provides one auditable lifecycle:

``register``
    Copy a source-authored PNG into the immutable project input namespace.
``approve``
    Copy an active registered baseline into the approved workspace namespace.
``list``
    Return metadata only; image bytes are never embedded in registry responses.
``revoke``
    Mark authority inactive without deleting historical bytes or evidence.

Every active record binds content SHA-256, screenshot mode, CSS viewport and the
current deterministic renderer profile. Runtime comparison must match all of
those fields exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ._common import ROOT, _safe_project_id
from ._utils import _now, _require_manage_actor, _safe_slug

SCHEMA_VERSION = "qualibug.visual-baseline-registry.v1"
RENDERER_PROFILE = "chromium_css_scale_v1"
SCROLL_ORIGIN = "document_start"
FONT_READINESS = "document_fonts_ready"
INPUT_PREFIX = "visual_baselines"
APPROVED_PREFIX = "approved_visual_baselines"
MAX_BASELINE_BYTES = 25_000_000
MAX_IMAGE_WIDTH = 20_000
MAX_IMAGE_HEIGHT = 20_000
MAX_IMAGE_PIXELS = 40_000_000


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _dimension(value: Any, *, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}_invalid")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{field}_invalid")
    return number


def _paths(project: str, root: Path) -> dict[str, Path]:
    workspace = Path(root) / "platform_workspace" / project
    return {
        "registry": workspace / "visual_baseline_registry.json",
        "input": Path(root) / "platform_inputs" / project / INPUT_PREFIX,
        "approved": workspace / APPROVED_PREFIX,
    }


def _default_registry(project: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "baselines": [],
        "audit_events": [],
        "updated_at_utc": "",
    }


def _load(project: str, root: Path) -> dict[str, Any]:
    path = _paths(project, root)["registry"]
    if not path.is_file():
        return _default_registry(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _default_registry(project)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return _default_registry(project)
    payload.setdefault("baselines", [])
    payload.setdefault("audit_events", [])
    return payload


def _save(project: str, root: Path, registry: dict[str, Any]) -> None:
    path = _paths(project, root)["registry"]
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["schema_version"] = SCHEMA_VERSION
    registry["project_id"] = project
    registry["updated_at_utc"] = _now()
    serialized = json.dumps(registry, ensure_ascii=False, indent=2, default=str)
    fd, temporary = tempfile.mkstemp(
        prefix=".visual-baselines-",
        suffix=".json.tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _png_metadata(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("visual_baseline_png_required")
    if not 1 <= len(data) <= MAX_BASELINE_BYTES:
        raise ValueError("visual_baseline_size_invalid")
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("visual_baseline_pillow_unavailable") from exc
    try:
        image = Image.open(__import__("io").BytesIO(data))
        if str(image.format or "").upper() != "PNG":
            raise ValueError("visual_baseline_png_required")
        width, height = int(image.size[0]), int(image.size[1])
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("visual_baseline_png_decode_failed") from exc
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_WIDTH
        or height > MAX_IMAGE_HEIGHT
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValueError("visual_baseline_decode_dimension_limit_exceeded")
    return width, height


def _actor_ref(actor: dict[str, Any]) -> str:
    return _text(
        actor.get("actor_ref")
        or actor.get("subject")
        or actor.get("sub")
        or actor.get("id")
        or actor.get("username"),
        limit=200,
    )


def _atomic_copy(data: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = destination.read_bytes()
        if hashlib.sha256(existing).hexdigest() != hashlib.sha256(data).hexdigest():
            raise RuntimeError("visual_baseline_immutable_path_conflict")
        return
    fd, temporary = tempfile.mkstemp(
        prefix=".visual-baseline-",
        suffix=".png.tmp",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _record_id(project: str, ref: str, digest: str) -> str:
    raw = f"{project}|{ref}|{digest}".encode("utf-8")
    return "vbl_" + hashlib.sha256(raw).hexdigest()[:20]


def register_visual_baseline(
    project_id: str,
    *,
    file_path: str | Path,
    baseline_name: str,
    viewport_width: int,
    viewport_height: int,
    full_page: bool,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    source = Path(file_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("visual_baseline_source_file_not_found")
    data = source.read_bytes()
    image_width, image_height = _png_metadata(data)
    width = _dimension(
        viewport_width,
        minimum=240,
        maximum=7680,
        field="viewport_width",
    )
    height = _dimension(
        viewport_height,
        minimum=240,
        maximum=4320,
        field="viewport_height",
    )
    if not isinstance(full_page, bool):
        raise ValueError("full_page_boolean_required")
    name = _safe_slug(_text(baseline_name, limit=180) or source.stem, 120)
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{name}__{digest[:12]}.png"
    ref = f"{INPUT_PREFIX}/{filename}"
    destination = _paths(project, root)["input"] / filename

    registry = _load(project, root)
    existing = next(
        (
            row
            for row in registry["baselines"]
            if row.get("status") == "active"
            and row.get("ref") == ref
            and row.get("sha256") == digest
            and int(row.get("viewport_width") or 0) == width
            and int(row.get("viewport_height") or 0) == height
            and row.get("full_page") is full_page
        ),
        None,
    )
    if existing:
        return {
            "ok": True,
            "status": "DUPLICATE_ACTIVE",
            "baseline": dict(existing),
        }

    _atomic_copy(data, destination)
    now = _now()
    record = {
        "baseline_id": _record_id(project, ref, digest),
        "ref": ref,
        "namespace": INPUT_PREFIX,
        "status": "active",
        "authority": "source_registered",
        "sha256": digest,
        "size_bytes": len(data),
        "image_width": image_width,
        "image_height": image_height,
        "viewport_width": width,
        "viewport_height": height,
        "full_page": full_page,
        "renderer_profile": RENDERER_PROFILE,
        "scroll_origin": SCROLL_ORIGIN,
        "font_readiness": FONT_READINESS,
        "created_at_utc": now,
        "created_by": _actor_ref(clean_actor),
        "source_filename": source.name,
        "raw_pixels_embedded_in_registry": False,
    }
    registry["baselines"].append(record)
    registry["audit_events"].append({
        "event": "register",
        "at_utc": now,
        "actor_ref": _actor_ref(clean_actor),
        "baseline_id": record["baseline_id"],
        "ref": ref,
        "sha256": digest,
    })
    _save(project, root, registry)
    return {"ok": True, "status": "REGISTERED", "baseline": record}


def approve_visual_baseline(
    project_id: str,
    *,
    baseline_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load(project, root)
    source = next(
        (
            row
            for row in registry["baselines"]
            if row.get("baseline_id") == baseline_id
            and row.get("status") == "active"
        ),
        None,
    )
    if not source:
        raise KeyError("active_visual_baseline_not_found")
    source_ref = _text(source.get("ref"))
    if not source_ref.startswith(INPUT_PREFIX + "/"):
        raise ValueError("only_source_registered_baseline_can_be_approved")
    source_path = _paths(project, root)["input"] / Path(source_ref).name
    if not source_path.is_file():
        raise FileNotFoundError("registered_visual_baseline_bytes_missing")
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != source.get("sha256"):
        raise RuntimeError("registered_visual_baseline_hash_drift")
    filename = Path(source_ref).name
    ref = f"{APPROVED_PREFIX}/{filename}"
    destination = _paths(project, root)["approved"] / filename
    existing = next(
        (
            row
            for row in registry["baselines"]
            if row.get("status") == "active"
            and row.get("ref") == ref
            and row.get("sha256") == digest
        ),
        None,
    )
    if existing:
        return {
            "ok": True,
            "status": "DUPLICATE_ACTIVE",
            "baseline": dict(existing),
        }
    _atomic_copy(data, destination)
    now = _now()
    approved = {
        **{
            key: value
            for key, value in source.items()
            if key not in {"baseline_id", "ref", "namespace", "authority", "created_at_utc", "created_by"}
        },
        "baseline_id": _record_id(project, ref, digest),
        "ref": ref,
        "namespace": APPROVED_PREFIX,
        "authority": "approved_copy",
        "status": "active",
        "approved_from_baseline_id": baseline_id,
        "created_at_utc": now,
        "created_by": _actor_ref(clean_actor),
    }
    registry["baselines"].append(approved)
    registry["audit_events"].append({
        "event": "approve",
        "at_utc": now,
        "actor_ref": _actor_ref(clean_actor),
        "baseline_id": approved["baseline_id"],
        "approved_from_baseline_id": baseline_id,
        "ref": ref,
        "sha256": digest,
    })
    _save(project, root, registry)
    return {"ok": True, "status": "APPROVED", "baseline": approved}


def revoke_visual_baseline(
    project_id: str,
    *,
    baseline_id: str,
    reason: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    explanation = _text(reason, limit=500)
    if not explanation:
        raise ValueError("visual_baseline_revocation_reason_required")
    registry = _load(project, root)
    record = next(
        (
            row
            for row in registry["baselines"]
            if row.get("baseline_id") == baseline_id
            and row.get("status") == "active"
        ),
        None,
    )
    if not record:
        raise KeyError("active_visual_baseline_not_found")
    now = _now()
    record["status"] = "revoked"
    record["revoked_at_utc"] = now
    record["revoked_by"] = _actor_ref(clean_actor)
    record["revocation_reason"] = explanation
    registry["audit_events"].append({
        "event": "revoke",
        "at_utc": now,
        "actor_ref": _actor_ref(clean_actor),
        "baseline_id": baseline_id,
        "ref": record.get("ref"),
        "reason": explanation,
        "bytes_retained_for_audit": True,
    })
    _save(project, root, registry)
    return {"ok": True, "status": "REVOKED", "baseline": dict(record)}


def list_visual_baselines(
    project_id: str,
    *,
    root: Path | None = None,
    include_revoked: bool = False,
) -> dict[str, Any]:
    root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    registry = _load(project, root)
    rows = [
        dict(row)
        for row in registry["baselines"]
        if include_revoked or row.get("status") == "active"
    ]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "baselines": rows,
        "summary": {
            "active_count": sum(1 for row in registry["baselines"] if row.get("status") == "active"),
            "revoked_count": sum(1 for row in registry["baselines"] if row.get("status") == "revoked"),
            "source_registered_count": sum(1 for row in rows if row.get("authority") == "source_registered"),
            "approved_copy_count": sum(1 for row in rows if row.get("authority") == "approved_copy"),
        },
        "raw_pixels_embedded": False,
    }


def active_visual_baseline_record(
    project_id: str,
    ref: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    registry = _load(project, root)
    matches = [
        row
        for row in registry["baselines"]
        if row.get("status") == "active" and row.get("ref") == ref
    ]
    if len(matches) != 1:
        return None
    return dict(matches[0])


def operate_visual_baseline_registry(
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _dict(payload)
    operation = _text(action).lower() or "list"
    if operation in {"list", "view"}:
        return list_visual_baselines(
            project_id,
            root=root,
            include_revoked=bool(payload.get("include_revoked")),
        )
    if operation == "register":
        return register_visual_baseline(
            project_id,
            file_path=_text(payload.get("file_path")),
            baseline_name=_text(payload.get("baseline_name")),
            viewport_width=payload.get("viewport_width"),
            viewport_height=payload.get("viewport_height"),
            full_page=payload.get("full_page"),
            root=root,
            actor=actor,
        )
    if operation == "approve":
        return approve_visual_baseline(
            project_id,
            baseline_id=_text(payload.get("baseline_id")),
            root=root,
            actor=actor,
        )
    if operation == "revoke":
        return revoke_visual_baseline(
            project_id,
            baseline_id=_text(payload.get("baseline_id")),
            reason=_text(payload.get("reason")),
            root=root,
            actor=actor,
        )
    raise ValueError("unsupported visual baseline action; use list, register, approve or revoke")


__all__ = [
    "APPROVED_PREFIX",
    "FONT_READINESS",
    "INPUT_PREFIX",
    "RENDERER_PROFILE",
    "SCHEMA_VERSION",
    "SCROLL_ORIGIN",
    "active_visual_baseline_record",
    "approve_visual_baseline",
    "list_visual_baselines",
    "operate_visual_baseline_registry",
    "register_visual_baseline",
    "revoke_visual_baseline",
]
