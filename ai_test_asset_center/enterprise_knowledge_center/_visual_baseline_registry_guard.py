"""Fail-closed integrity and audit guard for the visual baseline registry.

The lifecycle module intentionally keeps a small persistence implementation. This
installer hardens its dynamic helpers without creating a second registry:

* cleaned knowledge-center actors are recorded as ``name:role``;
* an existing malformed or wrong-schema registry is never replaced with an empty
  registry;
* registered PNGs are fully verified under decompression-bomb and dimension
  limits before they can become executable authority;
* one immutable ref cannot acquire conflicting active viewport or screenshot-mode
  identities.
"""
from __future__ import annotations

import hashlib
import io
import json
import warnings
from pathlib import Path
from typing import Any

from . import _visual_baselines as _registry

_INSTALL_MARKER = "_qualibug_visual_baseline_registry_guard_installed"
_ORIGINAL_ACTOR = "_qualibug_visual_registry_actor_before_guard"
_ORIGINAL_LOAD = "_qualibug_visual_registry_load_before_guard"
_ORIGINAL_PNG = "_qualibug_visual_registry_png_before_guard"
_ORIGINAL_REGISTER = "_qualibug_visual_registry_register_before_guard"


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def install_visual_baseline_registry_guard() -> None:
    if getattr(_registry, _INSTALL_MARKER, False):
        return
    original_register = _registry.register_visual_baseline
    setattr(_registry, _ORIGINAL_ACTOR, _registry._actor_ref)
    setattr(_registry, _ORIGINAL_LOAD, _registry._load)
    setattr(_registry, _ORIGINAL_PNG, _registry._png_metadata)
    setattr(_registry, _ORIGINAL_REGISTER, original_register)

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
        return original_register(
            project_id,
            file_path=file_path,
            baseline_name=baseline_name,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            full_page=full_page,
            root=root,
            actor=actor,
        )

    _registry._actor_ref = actor_ref_with_role
    _registry._load = load_fail_closed
    _registry._png_metadata = verified_png_metadata
    _registry.register_visual_baseline = register_without_identity_conflict
    setattr(_registry, _INSTALL_MARKER, True)


__all__ = ["install_visual_baseline_registry_guard"]
