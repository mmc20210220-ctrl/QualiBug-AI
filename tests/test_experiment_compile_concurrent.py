# -*- coding: utf-8 -*-
"""Task 11 — optional concurrent compile wrapper tests (SPEC-11 thread-pool
attachment).

The GIL probe (offline, .scratch/_task11_gil_probe.py) showed the compile chain
is CPU-bound pure-Python, so thread pools cannot accelerate wall-clock compile
time on CPython; the mechanism is kept as an OPTIONAL attachment for
environments where the compile chain is I/O-bound (e.g. token-catalog HTTP
login inside materialization). These tests lock its SEMANTIC equivalence: the
concurrent wrapper must produce byte-identical packs to the serial chain
(volatile wall-clock receipt fields normalized), preserve input order, keep
receipts complete, and isolate per-obligation failures.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from ai_test_asset_center.experiment_compile_concurrent import (
    compile_experiments_concurrent,
    get_concurrency,
    materialize_and_recompile_abstract_pack_concurrent,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_compiler_base import (
    compile_experiments as serial_compile,
)
from ai_test_asset_center.experiment_runtime_materialization import (
    materialize_and_recompile_abstract_pack as serial_rescue,
)
from tests.test_task11_compile_rootcause import (
    _canonical_hash,
    _synthetic_behavior_ir,
    _synthetic_obligations,
)

_COMPILE_KW = dict(
    environment_type="non-production",
    policy_version="v-test",
    available_adapters=frozenset({"http_api"}),
)


def _fixture_pack() -> tuple[list[dict], dict[str, Any]]:
    behavior_ir = _synthetic_behavior_ir()
    obligations = _synthetic_obligations()
    return obligations, behavior_ir


def test_concurrency_env_default_and_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_concurrency() == 8
    monkeypatch.setenv("QUALIBUG_COMPILE_CONCURRENCY", "3")
    assert get_concurrency() == 3
    monkeypatch.setenv("QUALIBUG_COMPILE_CONCURRENCY", "1")
    # 1 is the documented exact-serial kill-switch (the wrapper delegates
    # to the serial base); the [2,16] clamp applies to values >= 2.
    assert get_concurrency() == 1
    monkeypatch.setenv("QUALIBUG_COMPILE_CONCURRENCY", "99")
    assert get_concurrency() == 16  # clamped at the cap
    monkeypatch.setenv("QUALIBUG_COMPILE_CONCURRENCY", "banana")
    assert get_concurrency() == 8  # invalid → default


_ADDITIVE_METADATA = ("concurrency", "compile_failures", "rescue_failures")


def _semantic_hash(pack: dict[str, Any]) -> str:
    """Hash the pack with additive concurrency metadata stripped, so the
    comparison covers the serial-equivalent surface only."""
    return _canonical_hash({
        key: value
        for key, value in pack.items()
        if key not in _ADDITIVE_METADATA
    })


def test_concurrent_equals_serial_byte_identical() -> None:
    """Core assertion: per-obligation concurrency reproduces the serial pack."""
    obligations, behavior_ir = _fixture_pack()
    serial_pack = serial_compile(
        copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    concurrent_pack = compile_experiments_concurrent(
        copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    assert _semantic_hash(concurrent_pack) == _semantic_hash(serial_pack)


def test_concurrent_mutates_obligations_like_serial() -> None:
    obligations, behavior_ir = _fixture_pack()
    serial_obls = copy.deepcopy(obligations)
    concurrent_obls = copy.deepcopy(obligations)
    serial_compile(
        serial_obls, behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation, **_COMPILE_KW,
    )
    compile_experiments_concurrent(
        concurrent_obls, behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation, **_COMPILE_KW,
    )
    for s_obl, c_obl in zip(serial_obls, concurrent_obls):
        assert c_obl.get("compile_status") == s_obl.get("compile_status")
        assert c_obl.get("block_reason") == s_obl.get("block_reason")
        assert c_obl.get("expanded_experiment_count") == s_obl.get(
            "expanded_experiment_count"
        )


def test_concurrent_receipts_complete_and_ordered() -> None:
    obligations, behavior_ir = _fixture_pack()
    pack = compile_experiments_concurrent(
        copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    all_experiments = (
        pack["experiments"] + pack["blocked_experiments"] + pack["abstract_experiments"]
    )
    # N input obligations → at least N receipts (variants may add more), every
    # receipt carries status + reason + experiment id.
    assert len(all_experiments) >= len(obligations)
    for exp in all_experiments:
        receipt = exp.get("compile_receipt")
        assert isinstance(receipt, dict) and receipt.get("status")
        assert exp.get("experiment_id") and exp.get("obligation_id")
    # Input-order preservation within each status list.
    order = [o["obligation_id"] for o in obligations]
    for key in ("experiments", "blocked_experiments", "abstract_experiments"):
        list_oids = [exp["obligation_id"] for exp in pack[key]]
        assert list_oids == [o for o in order if o in set(list_oids)], key


def test_concurrent_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """One obligation raising must not affect the others; the failure is
    receipt-visible (HARNESS_FAILED experiment + compile_failures block)."""
    obligations, behavior_ir = _fixture_pack()
    failing_id = obligations[1]["obligation_id"]

    def flaky_compile_one(obl, **kw):
        if obl.get("obligation_id") == failing_id:
            raise RuntimeError("injected compile failure")
        return compile_experiment_for_obligation(obl, **kw)
    pack = compile_experiments_concurrent(
        copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=flaky_compile_one,
        **_COMPILE_KW,
    )
    failures = pack.get("compile_failures", {})
    assert failing_id in failures
    assert "injected compile failure" in failures[failing_id]["error"]
    harness_rows = [
        row for row in pack["blocked_experiments"]
        if (row.get("compile_receipt") or {}).get("status") == "HARNESS_FAILED"
    ]
    assert len(harness_rows) == 1
    assert harness_rows[0]["obligation_id"] == failing_id
    assert pack["block_reason_counts"].get("COMPILE_HARNESS_FAILED") == 1
    # Other obligations still compiled normally.
    other_oids = {o["obligation_id"] for o in obligations} - {failing_id}
    compiled_oids = {
        exp["obligation_id"]
        for exp in pack["experiments"] + pack["blocked_experiments"] + pack["abstract_experiments"]
    }
    assert other_oids <= compiled_oids


def test_concurrent_serial_fallback_single_obligation() -> None:
    obligations, behavior_ir = _fixture_pack()
    pack = compile_experiments_concurrent(
        copy.deepcopy(obligations[:1]),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    assert pack["concurrency"]["mode"] == "serial"


def test_rescue_concurrent_equals_serial() -> None:
    """V1.8-rescue loop: concurrent per-row rescue reproduces the serial pack."""
    from ai_test_asset_center.rescue_dedupe import compile_rescue_cache_clear
    # Process-global rescue dedupe cache is shared by design; reset around
    # each leg so cumulative unique_count stats stay order-independent.
    compile_rescue_cache_clear()
    obligations, behavior_ir = _fixture_pack()
    compile_pack = serial_compile(
        copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    compile_rescue_cache_clear()
    serial_out = serial_rescue(
        json.loads(json.dumps(compile_pack, default=str)),
        obligations=copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    compile_rescue_cache_clear()
    concurrent_out = materialize_and_recompile_abstract_pack_concurrent(
        json.loads(json.dumps(compile_pack, default=str)),
        obligations=copy.deepcopy(obligations),
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        **_COMPILE_KW,
    )
    assert _semantic_hash(concurrent_out) == _semantic_hash(serial_out)
    assert concurrent_out["concurrency"]["mode"] == "concurrent"


def test_shared_cache_integrity_under_concurrency() -> None:
    """The only module-level mutable state in the compile chain (behavior_ir_core
    memoization caches) must stay consistent under concurrent reads/writes."""
    from ai_test_asset_center.behavior_ir_core import (
        _SEMANTIC_MARKER_CACHE,
        _SEMANTIC_PATH_SUFFIX_CACHE,
        _semantic_marker_set,
        _semantic_path_suffix_set,
    )

    marker_keys = ["endpoint_action_markers", "mutating_action_markers"]
    suffix_keys = ["ephemeral_session_path_suffixes"]
    results: dict[str, list] = {"marker": [], "suffix": []}
    errors: list[Exception] = []
    lock = __import__("threading").Lock()

    def worker(kind: str, key: str) -> None:
        try:
            for _ in range(20):
                if kind == "marker":
                    value = _semantic_marker_set(key)
                    with lock:
                        results["marker"].append((key, frozenset(value)))
                else:
                    value = _semantic_path_suffix_set(key)
                    with lock:
                        results["suffix"].append((key, frozenset(value)))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    import threading

    threads = []
    for key in marker_keys * 4:
        threads.append(threading.Thread(target=worker, args=("marker", key)))
    for key in suffix_keys * 4:
        threads.append(threading.Thread(target=worker, args=("suffix", key)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    for key in marker_keys:
        values = {value for k, value in results["marker"] if k == key}
        assert len(values) == 1, f"marker cache race for {key}"
        assert _SEMANTIC_MARKER_CACHE[key] == next(iter(values))
    for key in suffix_keys:
        values = {value for k, value in results["suffix"] if k == key}
        assert len(values) == 1, f"suffix cache race for {key}"
        assert _SEMANTIC_PATH_SUFFIX_CACHE[key] == next(iter(values))
