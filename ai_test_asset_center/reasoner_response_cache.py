# -*- coding: utf-8 -*-
"""Reasoner 引擎响应缓存（内容寻址、持久化、诚实计数）。

迭代加速：stage_reason_all_v2 的 11+ 引擎每次扫描都全量调用 DeepSeek
（约 30 分钟）。prompt 完全由（引擎模板 + enterprise 资料截断文本 +
引擎注意力 nudge + model/temperature）决定——同一输入在同一模型下产生
同一语义响应，因此按完整 prompt 内容寻址缓存原始响应是语义安全的：
任何资料/模板/模型变化都自动 miss。

诚实性红线（AGENTS.md）：
- 缓存命中时 model_attempt_count=0 / model_response_count=0（真实 LLM
  调用数，绝不虚报），但 hypotheses 正常产出、status=success；
- 结果携带 cache_hit=True / cache_source="content_addressed"；
- 引擎报告 engine_durations_seconds 记 0.0 + cache_hit 标记；
- 缓存损坏/不可读 → 静默 miss（fail-open 走 LLM，绝不返回损坏数据）；
- QUALIBUG_DISABLE_REASONER_CACHE=1 完全绕过（调试/污染隔离）。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# 与 agent_semantic_linker 共用同一持久化目录环境变量，生命周期一致。
CACHE_DIRECTORY_ENV = "QUALIBUG_SEMANTIC_CACHE_DIR"
DISABLE_ENV = "QUALIBUG_DISABLE_REASONER_CACHE"
_SUBDIR = "reasoner_engine"

_MEMORY_CACHE: dict[str, str] = {}

# Workspace fallback directory, set by the mainline once the scan root is known.
# Without it the reasoner was the only cache layer in the product that stayed
# off unless an operator exported QUALIBUG_SEMANTIC_CACHE_DIR by hand: the
# semantic linker and the semantic extractor both fall back to
# ``<root>/platform_workspace/_shared/...``, so every run paid for all 11
# engine calls again while those two layers reused their work.
_INJECTED_CACHE_DIRECTORY: str = ""


def set_default_cache_directory(value: str | os.PathLike | None) -> None:
    """Declare the workspace cache directory for the current run.

    Takes effect only when the operator did not pin
    ``QUALIBUG_SEMANTIC_CACHE_DIR``; an explicit operator declaration always
    wins so a shared cache root stays under operator control.
    """
    global _INJECTED_CACHE_DIRECTORY
    _INJECTED_CACHE_DIRECTORY = str(value or "").strip()


def _package_relative_cache_dir() -> str:
    """Last-resort fallback: the workspace sibling of this package.

    Only used when neither the operator nor the mainline declared a directory,
    so a source checkout still persists cross-run reuse instead of silently
    re-paying for every engine call.
    """
    try:
        root = Path(__file__).resolve().parent.parent
        return str(root / "platform_workspace" / "_shared" / "reasoner_cache")
    except OSError:
        return ""


def _cache_dir() -> Path | None:
    raw = os.environ.get(CACHE_DIRECTORY_ENV, "").strip()
    if not raw:
        raw = _INJECTED_CACHE_DIRECTORY
    if not raw:
        raw = _package_relative_cache_dir()
    if not raw:
        return None
    try:
        path = Path(raw) / _SUBDIR
        return path
    except OSError:
        return None


def _disabled() -> bool:
    return str(os.environ.get(DISABLE_ENV, "")).lower() in {
        "1", "true", "yes", "on",
    }


def cache_key(
    engine_name: str,
    prompt: str,
    system_prompt: str,
    model: str,
    temperature: str,
) -> str:
    """Content-addressed key: any input change (material, nudge, model,
    template) invalidates the entry."""
    material = "|".join([
        str(engine_name or ""),
        str(model or ""),
        str(temperature or ""),
        str(prompt or ""),
        str(system_prompt or ""),
    ])
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def load(key: str) -> str | None:
    """Load a cached raw response. Corrupt/unreadable → None (fail-open)."""
    cached = _MEMORY_CACHE.get(key)
    if cached is not None:
        return cached
    if _disabled():
        return None
    directory = _cache_dir()
    if directory is None:
        return None
    path = directory / f"{key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = payload.get("raw")
    if not isinstance(raw, str):
        return None
    if str(payload.get("key") or "") != key:
        return None
    _MEMORY_CACHE[key] = raw
    return raw


def store(key: str, raw: str, *, model: str = "", temperature: str = "") -> None:
    """Persist a raw LLM response (best-effort; failures never raise)."""
    if _disabled():
        return
    if not isinstance(raw, str) or not raw:
        return
    directory = _cache_dir()
    if directory is None:
        return
    payload = {
        "schema": "qualibug.reasoner-engine-response-cache.v1",
        "key": key,
        "model": str(model or ""),
        "temperature": str(temperature or ""),
        "raw": raw,
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f"{key}.tmp"
        target = directory / f"{key}.json"
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(target)
    except OSError:
        return
    _MEMORY_CACHE[key] = raw
