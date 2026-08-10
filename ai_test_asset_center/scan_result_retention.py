# -*- coding: utf-8 -*-
"""Scan-result retention — 产物生命周期轮转与垃圾清理。

每次扫描的 scan_result（含分片 parts）可达 GB 级；不轮转时历史产物、legacy
备份、中断残留（.q-*.tmp）无限累积（实测单项目 11GB+）。本模块提供：

- ``rotate_scan_result_archive``：扫描写入后调用——把旧 scan_result 归档为
  ``scan_result_archive_<ts>/`` 并只保留最近 N 份（``RETAIN``，默认 3，
  可配置 ``QUALIBUG_SCAN_RESULT_RETAIN``），更旧的归档整目录删除；
- ``cleanup_transient_artifacts``：扫描启动时调用——删除写入中断残留的
  ``.q-*.tmp`` 与转换遗留的 ``scan_result.json.legacy``（分片 store 转换
  后 legacy 不再需要；P0-3 引用化后产物更小，legacy 更无价值）。

通用机制：任何项目的 scan_result 产物都走同一轮转策略；轮转只删历史副本，
永不触碰当前索引/分片/脱敏边界。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

_RETAIN_DEFAULT = 3
_TMP_RE = re.compile(r"^\.q-.*\.tmp$")


def _retain_count() -> int:
    raw = os.getenv("QUALIBUG_SCAN_RESULT_RETAIN", "")
    try:
        count = int(raw)
    except (TypeError, ValueError):
        count = _RETAIN_DEFAULT
    return count if count >= 1 else _RETAIN_DEFAULT


def cleanup_transient_artifacts(
    project: str,
    root: Path | str,
) -> dict[str, Any]:
    """Remove interrupt leftovers and conversion-legacy backups.

    Returns a receipt with per-kind removed counts. Never touches the current
    index/shard files.
    """
    output_root = Path(root) / "platform_outputs" / re.sub(
        r"[^A-Za-z0-9_.-]+", "_", str(project)
    )
    removed_tmp = 0
    removed_legacy = 0
    if output_root.is_dir():
        for child in output_root.iterdir():
            if child.is_file():
                if _TMP_RE.match(child.name):
                    try:
                        child.unlink()
                        removed_tmp += 1
                    except OSError:
                        pass
                elif child.name == "scan_result.json.legacy":
                    try:
                        child.unlink()
                        removed_legacy += 1
                    except OSError:
                        pass
    return {
        "schema_version": "qualibug.scan-result-retention.v1",
        "project": project,
        "removed_tmp_files": removed_tmp,
        "removed_legacy_backups": removed_legacy,
        "retain_count": _retain_count(),
    }


def rotate_scan_result_archive(
    project: str,
    root: Path | str,
    *,
    current_index: Path | str | None = None,
) -> dict[str, Any]:
    """Archive the previous scan_result before a new run overwrites it.

    The current ``scan_result.json`` + ``scan_result.parts`` (when present)
    are moved into ``scan_result_archive_<ts>/``; only the most recent
    ``RETAIN`` archives are kept — older archive directories are deleted
    wholesale. The caller invokes this AFTER the new scan_result has been
    written (or before, with ``current_index`` pointing at the file about to
    be replaced); when ``current_index`` is omitted the existing index file
    is archived.
    """
    output_root = Path(root) / "platform_outputs" / re.sub(
        r"[^A-Za-z0-9_.-]+", "_", str(project)
    )
    index = Path(current_index) if current_index else (
        output_root / "scan_result.json"
    )
    archive_dir = output_root / (
        "scan_result_archive_"
        + time.strftime("%Y%m%d_%H%M%S")
    )
    moved = 0
    if index.is_file():
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(index), str(archive_dir / index.name))
        moved += 1
        parts = output_root / "scan_result.parts"
        if parts.is_dir():
            shutil.move(str(parts), str(archive_dir / "scan_result.parts"))
            moved += 1

    retain = _retain_count()
    archives = sorted(
        (
            child
            for child in output_root.iterdir()
            if child.is_dir() and child.name.startswith("scan_result_archive_")
        ),
        key=lambda p: p.name,
    )
    removed = 0
    for stale in archives[:-retain] if retain < len(archives) else []:
        shutil.rmtree(stale, ignore_errors=True)
        removed += 1
    return {
        "schema_version": "qualibug.scan-result-retention.v1",
        "project": project,
        "archived_now": moved,
        "retain_count": retain,
        "archives_kept": len(archives) - removed,
        "archives_removed": removed,
    }
