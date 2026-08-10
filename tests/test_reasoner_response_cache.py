# -*- coding: utf-8 -*-
"""Reasoner 引擎响应缓存——内容寻址、持久化、诚实计数、fail-open。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_test_asset_center.reasoner_response_cache import (
    cache_key,
    load,
    store,
)


@pytest.fixture()
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "semantic_cache"
    monkeypatch.setenv("QUALIBUG_SEMANTIC_CACHE_DIR", str(directory))
    monkeypatch.delenv("QUALIBUG_DISABLE_REASONER_CACHE", raising=False)
    return directory


def test_store_then_load_roundtrip(cache_dir: Path) -> None:
    key = cache_key("causality", "prompt-A", "system", "deepseek-r1", "0.3")
    store(key, '{"hypotheses": [{"title": "x"}]}', model="deepseek-r1", temperature="0.3")
    assert load(key) == '{"hypotheses": [{"title": "x"}]}'
    assert (cache_dir / "reasoner_engine" / f"{key}.json").is_file()


def test_key_changes_with_any_input() -> None:
    base = cache_key("engine", "prompt", "system", "model", "0.3")
    assert cache_key("engine", "promptX", "system", "model", "0.3") != base
    assert cache_key("engine", "prompt", "systemX", "model", "0.3") != base
    assert cache_key("engine", "prompt", "system", "modelX", "0.3") != base
    assert cache_key("engine", "prompt", "system", "model", "0.7") != base
    assert cache_key("engine2", "prompt", "system", "model", "0.3") != base
    assert cache_key("engine", "prompt", "system", "model", "0.3") == base


def test_corrupt_payload_fails_open(cache_dir: Path) -> None:
    key = cache_key("engine", "prompt", "system", "model", "0.3")
    path = cache_dir / "reasoner_engine" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    assert load(key) is None


def test_key_mismatch_fails_open(cache_dir: Path) -> None:
    key = cache_key("engine", "prompt", "system", "model", "0.3")
    path = cache_dir / "reasoner_engine" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": "different-key", "raw": "x"}), encoding="utf-8")
    assert load(key) is None


def test_missing_returns_none(cache_dir: Path) -> None:
    assert load("does-not-exist") is None


def test_disabled_never_reads_or_writes(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALIBUG_DISABLE_REASONER_CACHE", "1")
    key = cache_key("engine", "prompt", "system", "model", "0.3")
    store(key, "raw-response", model="model", temperature="0.3")
    assert not (cache_dir / "reasoner_engine" / f"{key}.json").exists()
    assert load(key) is None


def test_empty_raw_not_stored(cache_dir: Path) -> None:
    key = cache_key("engine", "prompt", "system", "model", "0.3")
    store(key, "", model="model", temperature="0.3")
    assert not (cache_dir / "reasoner_engine" / f"{key}.json").exists()


def test_memory_cache_avoids_disk(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = cache_key("engine", "prompt", "system", "model", "0.3")
    store(key, "raw-value", model="model", temperature="0.3")
    # delete the file; in-memory hit must still return the value
    (cache_dir / "reasoner_engine" / f"{key}.json").unlink()
    assert load(key) == "raw-value"


def test_non_ascii_content_roundtrip(cache_dir: Path) -> None:
    key = cache_key("engine", "中文资料：支付金额必须等于应付金额", "system", "model", "0.3")
    store(key, '{"title": "已取消订单不能支付"}', model="model", temperature="0.3")
    assert load(key) == '{"title": "已取消订单不能支付"}'
