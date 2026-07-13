"""Dependency-free project identity, paths, and strict artifact I/O.

This module is the inward-facing authority used by onboarding, safety,
deployment, and runtime adapters. Missing optional artifacts may use an
explicit caller default; corrupt or unreadable artifacts always fail with a
path-addressed error.
"""
from __future__ import annotations

import copy
import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProjectArtifactError(RuntimeError):
    """A declared project artifact exists but cannot be trusted."""


def safe_project_id(value: Any) -> str:
    raw = str(value or "real_project_demo").strip()
    safe = "".join(character for character in raw if character.isalnum() or character in "_-." )
    return safe or "real_project_demo"


def read_text_artifact(path: Path | str, *, missing: str = "") -> str:
    target = Path(path)
    if not target.exists():
        return missing
    try:
        return target.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ProjectArtifactError(
            f"project_text_artifact_invalid:{target.resolve()}:{type(exc).__name__}:{exc}"
        ) from exc


def load_json_artifact(path: Path | str, default: Any) -> Any:
    target = Path(path)
    if not target.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(target.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectArtifactError(
            f"project_json_artifact_invalid:{target.resolve()}:{type(exc).__name__}:{exc}"
        ) from exc


def write_json_artifact(path: Path | str, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def parse_json_input(
    inline_value: str | None = None,
    file_value: str | None = None,
    *,
    default: Any = None,
) -> Any:
    if file_value:
        return load_json_artifact(Path(str(file_value)), default)
    text = str(inline_value or "").strip()
    if not text:
        return copy.deepcopy(default)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectArtifactError(
            f"project_inline_json_invalid:{exc.lineno}:{exc.colno}:{exc.msg}"
        ) from exc


def write_json_if_allowed(
    path: Path | str,
    data: Any,
    *,
    overwrite: bool,
) -> bool:
    target = Path(path)
    if target.exists() and not overwrite:
        return False
    write_json_artifact(target, data)
    return True


def html_escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").rstrip("/")
    suffix = str(path or "").strip()
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return base + suffix


def project_config_paths(
    project_id: str,
    root: Path | None = None,
) -> dict[str, Path]:
    workspace_root = Path(root or PROJECT_ROOT)
    project = safe_project_id(project_id)
    return {
        "input_dir": workspace_root / "platform_inputs" / project,
        "workspace_dir": workspace_root / "platform_workspace" / project / "real_project",
        "output_dir": workspace_root / "platform_outputs" / project / "real_project",
    }
