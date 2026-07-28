"""Stage browser-uploaded fixture bytes into project inputs before registration."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .enterprise_knowledge_center._utils import _safe_slug
from .ui_upload_fixture_registry import register_upload_fixture

MAX_HTTP_FIXTURE_BYTES = 10 * 1024 * 1024


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def stage_and_register_upload_fixture(
    project_id: str,
    *,
    data: bytes,
    filename: str,
    fixture_name: str,
    content_type: str = "application/octet-stream",
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise TypeError("ui_upload_fixture_binary_body_required")
    if not 1 <= len(data) <= MAX_HTTP_FIXTURE_BYTES:
        raise ValueError("ui_upload_fixture_http_size_invalid")
    effective_root = Path(root or ROOT).resolve()
    project = _safe_project_id(project_id)
    original = Path(_text(filename, limit=240)).name
    if not original or original in {".", ".."}:
        raise ValueError("ui_upload_fixture_filename_required")
    safe_stem = _safe_slug(Path(original).stem, 100)
    safe_suffix = Path(original).suffix.lower()[:20]
    digest = hashlib.sha256(data).hexdigest()
    inbox = effective_root / "platform_inputs" / project / "ui_upload_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{safe_stem}__{digest[:12]}{safe_suffix}"
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise RuntimeError("ui_upload_fixture_inbox_path_invalid")
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError("ui_upload_fixture_inbox_immutable_conflict")
    else:
        fd, temporary = tempfile.mkstemp(
            prefix=".ui-upload-inbox-",
            suffix=".tmp",
            dir=str(inbox),
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if hashlib.sha256(temporary_path.read_bytes()).hexdigest() != digest:
                raise RuntimeError("ui_upload_fixture_inbox_write_hash_mismatch")
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
    result = register_upload_fixture(
        project,
        file_path=destination,
        fixture_name=_text(fixture_name, limit=180) or safe_stem,
        root=effective_root,
        actor=actor,
    )
    fixture = result.get("fixture") if isinstance(result.get("fixture"), dict) else {}
    fixture["submitted_content_type"] = _text(content_type, limit=120)
    fixture["browser_binary_upload_used"] = True
    fixture["base64_transport_used"] = False
    result["fixture"] = fixture
    result["transport"] = {
        "mode": "application_octet_stream",
        "size_bytes": len(data),
        "sha256": digest,
        "base64_used": False,
        "global_request_limit_respected": True,
    }
    return result


__all__ = ["MAX_HTTP_FIXTURE_BYTES", "stage_and_register_upload_fixture"]
