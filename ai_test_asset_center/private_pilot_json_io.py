"""JSON artifact read/write helpers for the private-pilot service."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .scan_result_store import is_sharded_scan_result, load_scan_result


def _read_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return load_scan_result(path, keys=None)
    except Exception:
        return default
    return default


def _read_json_artifact(path: Path) -> Any:
    """Read a present JSON artifact or fail with its identity in the error.

    scan_result 分片 store 自动组装；普通 JSON 文件行为与 json.loads 一致，
    并保留原生顶层类型（dict / list），因为非 scan_result 产物（如
    performance/baseline.json 是历史基线 list）本就不受 dict-only 约束。
    """
    try:
        if is_sharded_scan_result(path):
            return load_scan_result(path, keys=None)
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc


def _read_json_object(path: Path, *, missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return dict(missing or {})
    payload = _read_json_artifact(path)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _write_json_object_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Persist a JSON object without exposing a partially written artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
