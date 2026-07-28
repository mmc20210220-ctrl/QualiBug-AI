"""Fail-closed integrity, audit and concurrency guard for visual baselines.

The lifecycle module intentionally keeps a small persistence implementation. This
installer hardens its dynamic helpers without creating a second registry:

* cleaned knowledge-center actors are recorded as ``name:role``;
* an existing malformed or wrong-schema registry is never replaced with an empty
  registry;
* registered PNGs are fully verified under decompression-bomb and dimension
  limits before they can become executable authority;
* one immutable ref cannot acquire conflicting active viewport or screenshot-mode
  identities;
* every lifecycle version receives a unique baseline id, including re-registration
  after revocation;
* register/approve/revoke mutations are serialized by a short-lived project lock;
* revoking source authority atomically revokes every active approved copy derived
  from it, so invalidated pixels cannot remain executable through another ref.
"""
from __future__ import annotations

import contextvars
import hashlib
import io
import json
import os
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import _visual_baselines as _registry

_INSTALL_MARKER = "_qualibug_visual_baseline_registry_guard_installed"
_ORIGINAL_ACTOR = "_qualibug_visual_registry_actor_before_guard"
_ORIGINAL_LOAD = "_qualibug_visual_registry_load_before_guard"
_ORIGINAL_PNG = "_qualibug_visual_registry_png_before_guard"
_ORIGINAL_RECORD_ID = "_qualibug_visual_registry_record_id_before_guard"
_ORIGINAL_REGISTER = "_qualibug_visual_registry_register_before_guard"
_ORIGINAL_APPROVE = "_qualibug_visual_registry_approve_before_guard"
_ORIGINAL_REVOKE = "_qualibug_visual_registry_revoke_before_guard"
_LOCK_TIMEOUT_SECONDS = 5.0
_STALE_LOCK_SECONDS = 120.0
_RECORD_GENERATION: contextvars.ContextVar[int] = contextvars.ContextVar(
    "qualibug_visual_baseline_record_generation",
    default=0,
)


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


@contextmanager
def _mutation_lock(project: str, root: Path) -> Iterator[None]:
    registry_path = _registry._paths(project, root)["registry"]
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry_path.with_name(registry_path.name + ".lock")
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > _STALE_LOCK_SECONDS
            except OSError:
                stale = False
            if stale:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("visual_baseline_registry_busy")
            time.sleep(0.05)
    try:
        os.write(
            descriptor,
            f"pid={os.getpid()} acquired={time.time():.6f}\n".encode("ascii"),
        )
        os.fsync(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


def install_visual_baseline_registry_guard() -> None:
    if getattr(_registry, _INSTALL_MARKER, False):
        return
    original_register = _registry.register_visual_baseline
    original_approve = _registry.approve_visual_baseline
    original_revoke = _registry.revoke_visual_baseline
    original_record_id = _registry._record_id
    setattr(_registry, _ORIGINAL_ACTOR, _registry._actor_ref)
    setattr(_registry, _ORIGINAL_LOAD, _registry._load)
    setattr(_registry, _ORIGINAL_PNG, _registry._png_metadata)
    setattr(_registry, _ORIGINAL_RECORD_ID, original_record_id)
    setattr(_registry, _ORIGINAL_REGISTER, original_register)
    setattr(_registry, _ORIGINAL_APPROVE, original_approve)
    setattr(_registry, _ORIGINAL_REVOKE, original_revoke)

    def actor_ref_with_role(actor: dict[str, Any]) -> str:
        name = _text(
            actor.get("name")
            or actor.get("actor_ref")
            or actor.get("subject")
            or actor.get("sub")
            or actor.get("id")
            or actor.get("username")
            or "knowledge_operator",
            limit=160,
        )
        role = _text(actor.get("role"), limit=64)
        return f"{name}:{role}" if role else name

    def load_fail_closed(project: str, root: Path) -> dict[str, Any]:
        path = _registry._paths(project, root)["registry"]
        if not path.is_file():
            return _registry._default_registry(project)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("visual_baseline_registry_corrupt") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _registry.SCHEMA_VERSION
            or payload.get("project_id") not in {None, "", project}
            or not isinstance(payload.get("baselines", []), list)
            or not isinstance(payload.get("audit_events", []), list)
        ):
            raise RuntimeError("visual_baseline_registry_schema_invalid")
        payload.setdefault("project_id", project)
        payload.setdefault("baselines", [])
        payload.setdefault("audit_events", [])
        return payload

    def verified_png_metadata(data: bytes) -> tuple[int, int]:
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("visual_baseline_png_required")
        if not 1 <= len(data) <= _registry.MAX_BASELINE_BYTES:
            raise ValueError("visual_baseline_size_invalid")
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("visual_baseline_pillow_unavailable") from exc
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                header = Image.open(io.BytesIO(data))
                if str(header.format or "").upper() != "PNG":
                    raise ValueError("visual_baseline_png_required")
                width, height = int(header.size[0]), int(header.size[1])
                if (
                    width < 1
                    or height < 1
                    or width > _registry.MAX_IMAGE_WIDTH
                    or height > _registry.MAX_IMAGE_HEIGHT
                    or width * height > _registry.MAX_IMAGE_PIXELS
                ):
                    raise ValueError(
                        "visual_baseline_decode_dimension_limit_exceeded"
                    )
                header.verify()
        except ValueError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("visual_baseline_decompression_bomb_blocked") from exc
        except Exception as exc:
            raise ValueError("visual_baseline_png_decode_failed") from exc
        return width, height

    def record_id_with_generation(project: str, ref: str, digest: str) -> str:
        generation = int(_RECORD_GENERATION.get() or 0)
        if generation <= 0:
            raise RuntimeError("visual_baseline_record_generation_missing")
        raw = (
            f"{project}|{ref}|{digest}|generation:{generation}"
        ).encode("utf-8")
        return "vbl_" + hashlib.sha256(raw).hexdigest()[:20]

    def register_without_identity_conflict(
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
        clean_actor = _registry._require_manage_actor(actor)
        effective_root = Path(root or _registry.ROOT)
        project = _registry._safe_project_id(project_id)
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError("visual_baseline_source_file_not_found")
        data = source.read_bytes()
        _registry._png_metadata(data)
        width = _registry._dimension(
            viewport_width,
            minimum=240,
            maximum=7680,
            field="viewport_width",
        )
        height = _registry._dimension(
            viewport_height,
            minimum=240,
            maximum=4320,
            field="viewport_height",
        )
        if not isinstance(full_page, bool):
            raise ValueError("full_page_boolean_required")
        name = _registry._safe_slug(
            _text(baseline_name, limit=180) or source.stem,
            120,
        )
        digest = hashlib.sha256(data).hexdigest()
        ref = f"{_registry.INPUT_PREFIX}/{name}__{digest[:12]}.png"
        with _mutation_lock(project, effective_root):
            registry = _registry._load(project, effective_root)
            same_ref = [
                row
                for row in registry["baselines"]
                if row.get("status") == "active" and row.get("ref") == ref
            ]
            exact = [
                row
                for row in same_ref
                if row.get("sha256") == digest
                and int(row.get("viewport_width") or 0) == width
                and int(row.get("viewport_height") or 0) == height
                and row.get("full_page") is full_page
                and row.get("renderer_profile") == _registry.RENDERER_PROFILE
                and row.get("scroll_origin") == _registry.SCROLL_ORIGIN
                and row.get("font_readiness") == _registry.FONT_READINESS
            ]
            if same_ref and not exact:
                raise RuntimeError("visual_baseline_active_identity_conflict")
            if len(exact) > 1:
                raise RuntimeError("visual_baseline_active_identity_ambiguous")
            token = _RECORD_GENERATION.set(len(registry["baselines"]) + 1)
            try:
                return original_register(
                    project_id,
                    file_path=file_path,
                    baseline_name=baseline_name,
                    viewport_width=viewport_width,
                    viewport_height=viewport_height,
                    full_page=full_page,
                    root=effective_root,
                    actor=clean_actor,
                )
            finally:
                _RECORD_GENERATION.reset(token)

    def approve_with_lock(
        project_id: str,
        *,
        baseline_id: str,
        root: Path | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_actor = _registry._require_manage_actor(actor)
        effective_root = Path(root or _registry.ROOT)
        project = _registry._safe_project_id(project_id)
        with _mutation_lock(project, effective_root):
            registry = _registry._load(project, effective_root)
            token = _RECORD_GENERATION.set(len(registry["baselines"]) + 1)
            try:
                return original_approve(
                    project_id,
                    baseline_id=baseline_id,
                    root=effective_root,
                    actor=clean_actor,
                )
            finally:
                _RECORD_GENERATION.reset(token)

    def revoke_with_lock(
        project_id: str,
        *,
        baseline_id: str,
        reason: str,
        root: Path | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_actor = _registry._require_manage_actor(actor)
        effective_root = Path(root or _registry.ROOT)
        project = _registry._safe_project_id(project_id)
        explanation = _text(reason, limit=500)
        if not explanation:
            raise ValueError("visual_baseline_revocation_reason_required")
        with _mutation_lock(project, effective_root):
            registry = _registry._load(project, effective_root)
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
            now = _registry._now()
            actor_ref = _registry._actor_ref(clean_actor)
            record["status"] = "revoked"
            record["revoked_at_utc"] = now
            record["revoked_by"] = actor_ref
            record["revocation_reason"] = explanation
            cascade_ids: list[str] = []
            if record.get("authority") == "source_registered":
                for child in registry["baselines"]:
                    if (
                        child.get("status") == "active"
                        and child.get("authority") == "approved_copy"
                        and child.get("approved_from_baseline_id") == baseline_id
                    ):
                        child["status"] = "revoked"
                        child["revoked_at_utc"] = now
                        child["revoked_by"] = actor_ref
                        child["revocation_reason"] = explanation
                        child["cascade_source_baseline_id"] = baseline_id
                        child_id = _text(child.get("baseline_id"), limit=200)
                        if child_id:
                            cascade_ids.append(child_id)
            registry["audit_events"].append({
                "event": "revoke",
                "at_utc": now,
                "actor_ref": actor_ref,
                "baseline_id": baseline_id,
                "ref": record.get("ref"),
                "reason": explanation,
                "bytes_retained_for_audit": True,
                "cascade_revoked_baseline_ids": cascade_ids,
            })
            _registry._save(project, effective_root, registry)
            return {
                "ok": True,
                "status": "REVOKED",
                "baseline": dict(record),
                "cascade_revoked_baseline_ids": cascade_ids,
                "cascade_revoked_count": len(cascade_ids),
            }

    _registry._actor_ref = actor_ref_with_role
    _registry._load = load_fail_closed
    _registry._png_metadata = verified_png_metadata
    _registry._record_id = record_id_with_generation
    _registry.register_visual_baseline = register_without_identity_conflict
    _registry.approve_visual_baseline = approve_with_lock
    _registry.revoke_visual_baseline = revoke_with_lock
    setattr(_registry, _INSTALL_MARKER, True)


__all__ = ["install_visual_baseline_registry_guard"]
