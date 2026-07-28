"""File-system and hashing hardening for complex UI interactions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from . import professional_ui_complex_interactions as _complex
from . import professional_ui_interaction_cleanup as _interaction

_INSTALL_MARKER = "_qualibug_complex_interaction_hardening_installed"
_ORIGINAL_RESOLVER = "_qualibug_upload_resolver_before_hardening"
_ORIGINAL_DOWNLOAD_HASH = "_qualibug_download_hash_before_hardening"
_CHUNK_BYTES = 1024 * 1024


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _resolve_upload_files_hardened(
    refs: list[str],
    runtime_contract: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    if not refs:
        return [], []
    context = _dict(_complex._RUNTIME_CONTEXT.get())
    root_text = _text(context.get("root"), limit=2000)
    project = _text(context.get("project"), limit=160)
    if not root_text or not project:
        raise RuntimeError("UI_UPLOAD_RUNTIME_CONTEXT_MISSING")
    root = Path(root_text).resolve()
    bindings = _dict(runtime_contract.get("ui_file_bindings"))
    lexical_roots = [
        _lexical_path(root / "platform_inputs" / project),
        _lexical_path(root / "platform_workspace" / project / "ui_upload_fixtures"),
    ]
    resolved_roots = [path.resolve() for path in lexical_roots]
    paths: list[str] = []
    evidence: list[dict[str, Any]] = []
    total = 0
    for ref in refs:
        binding = _dict(bindings.get(ref))
        if not binding:
            raise RuntimeError("UI_UPLOAD_FILE_BINDING_MISSING")
        if binding.get("approved") is not True and _text(
            binding.get("status")
        ).lower() != "approved":
            raise RuntimeError("UI_UPLOAD_FILE_BINDING_NOT_APPROVED")
        raw_path = _text(binding.get("file_path") or binding.get("path"), limit=2000)
        expected_sha = _text(binding.get("sha256"), limit=64).lower()
        if not raw_path or not _complex._SHA256_RE.fullmatch(expected_sha):
            raise RuntimeError("UI_UPLOAD_FILE_BINDING_IDENTITY_INCOMPLETE")
        submitted = Path(raw_path)
        unresolved = submitted if submitted.is_absolute() else root / submitted
        lexical = _lexical_path(unresolved)
        lexical_root = next(
            (scope for scope in lexical_roots if _within(lexical, scope)),
            None,
        )
        if lexical_root is None or _has_symlink_component(lexical, lexical_root):
            raise RuntimeError("UI_UPLOAD_FILE_OUTSIDE_PROJECT_SCOPE")
        candidate = lexical.resolve()
        resolved_root = next(
            (scope for scope in resolved_roots if _within(candidate, scope)),
            None,
        )
        if resolved_root is None:
            raise RuntimeError("UI_UPLOAD_FILE_OUTSIDE_PROJECT_SCOPE")
        if not candidate.is_file():
            raise RuntimeError("UI_UPLOAD_FILE_NOT_FOUND")
        size = int(candidate.stat().st_size)
        if size < 1 or size > _complex.MAX_UPLOAD_FILE_BYTES:
            raise RuntimeError("UI_UPLOAD_FILE_SIZE_INVALID")
        total += size
        if total > _complex.MAX_UPLOAD_TOTAL_BYTES:
            raise RuntimeError("UI_UPLOAD_TOTAL_SIZE_EXCEEDED")
        digest = _stream_sha256(candidate)
        if digest != expected_sha:
            raise RuntimeError("UI_UPLOAD_FILE_HASH_MISMATCH")
        paths.append(str(candidate))
        evidence.append({
            "file_ref_fingerprint": _interaction._fingerprint(ref),
            "sha256": digest,
            "size_bytes": size,
            "content_type": _text(binding.get("content_type"), limit=120),
            "raw_file_ref_included": False,
            "raw_file_path_included": False,
            "raw_filename_included": False,
            "raw_file_content_included": False,
            "symlink_components_allowed": False,
            "streaming_hash_used": True,
        })
    return paths, evidence


def _hash_download_hardened(download: Any, max_bytes: int) -> dict[str, Any]:
    failure = _text(download.failure(), limit=300)
    if failure:
        raise RuntimeError("UI_DOWNLOAD_FAILED")
    path_value = download.path()
    if not path_value:
        raise RuntimeError("UI_DOWNLOAD_PATH_UNAVAILABLE")
    path = Path(path_value)
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("UI_DOWNLOAD_PATH_INVALID")
    size = int(path.stat().st_size)
    if size < 0 or size > max_bytes:
        raise RuntimeError("UI_DOWNLOAD_SIZE_LIMIT_EXCEEDED")
    suggested = _text(download.suggested_filename, limit=240)
    return {
        "download_sha256": _stream_sha256(path),
        "download_size_bytes": size,
        "suggested_filename_fingerprint": _interaction._fingerprint(suggested),
        "suggested_filename_suffix": Path(suggested).suffix.lower()[:20],
        "raw_download_path_included": False,
        "raw_download_filename_included": False,
        "raw_download_content_included": False,
        "download_persisted": False,
        "streaming_hash_used": True,
    }


def install_professional_ui_complex_interaction_hardening() -> None:
    if getattr(_complex, _INSTALL_MARKER, False):
        return
    setattr(
        _complex,
        _ORIGINAL_RESOLVER,
        getattr(_complex, _ORIGINAL_RESOLVER, _complex._resolve_upload_files),
    )
    setattr(
        _complex,
        _ORIGINAL_DOWNLOAD_HASH,
        getattr(_complex, _ORIGINAL_DOWNLOAD_HASH, _complex._hash_download),
    )
    _complex._resolve_upload_files = _resolve_upload_files_hardened
    _complex._hash_download = _hash_download_hardened
    setattr(_complex, _INSTALL_MARKER, True)


__all__ = [
    "install_professional_ui_complex_interaction_hardening",
]
