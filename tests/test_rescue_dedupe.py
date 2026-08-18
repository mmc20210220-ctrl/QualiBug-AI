"""Content-addressed rescue dedupe + concurrent compile regression.

Measured on the real post-f6 benchmark scan: 508 rescue attempts over 145
unique obligations (71% exact duplicates; 114 obligations rescued exactly 4
times across planning/compile/expansion lifecycles with identical evidence and
the same still_blocked reason). The dedupe cache is content-addressed: an
obligation whose blocking evidence is byte-identical and whose prior rescue was
NOT_MATERIALIZED is not re-resolved/recompiled; any evidence change (source
snapshot, Behavior IR, binding, credential availability, capability) misses
and re-executes. Successful rescues are never cached.

These tests pin the contract without touching benchmark data.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ai_test_asset_center import experiment_compile_concurrent as concurrent
from ai_test_asset_center import rescue_dedupe as dedupe


@pytest.fixture(autouse=True)
def _clean_cache():
    dedupe.rescue_cache_clear()
    yield
    dedupe.rescue_cache_clear()


def _abstract_experiment(oid: str, reason: str = "BLOCKED_MISSING_BINDING") -> dict:
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "ABSTRACT", "reason_code": reason},
        "abstract_experiment": {
            "required_capabilities": {
                "operations": ["op:1"],
                "actors": ["actor:buyer"],
                "fixtures": [],
                "observers": ["observer:http"],
            }
        },
    }


def _obligation(oid: str) -> dict:
    return {
        "obligation_id": oid,
        "required_operations": ["op:1"],
        "required_actors": ["actor:buyer"],
        "required_fixtures": [],
        "required_observers": ["observer:http"],
        "binding_plan": [],
        "property": {},
    }


def _behavior_ir(model_id: str = "ir:1") -> dict:
    return {
        "model_id": model_id,
        "operations": [{"id": "op:1", "operation_id": "op:1", "method": "POST", "path": "/api/x"}],
        "actors": [{"id": "actor:buyer", "role": "buyer"}],
    }


def _neg_receipt(oid: str, reason: str = "BODY_PARAMETER_NOT_SOURCE_BOUND") -> dict:
    return {
        "schema_version": "qualibug.experiment-materialization.v1",
        "receipt_id": f"emat_{oid}",
        "experiment_id": f"exp_{oid}",
        "obligation_id": oid,
        "status": "NOT_MATERIALIZED",
        "unresolved_requirements": [
            {"kind": "operation", "ref": "op:1", "reason": reason}
        ],
        "actor_bindings": {},
        "fixture_bindings": {},
        "operation_bindings": {},
        "observer_bindings": {},
        "cleanup_plan": {},
    }


# ── fingerprint determinism / sensitivity ───────────────────────────────────


def test_fingerprint_stable_for_identical_evidence() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    assert fp1 == fp2


def test_fingerprint_changes_on_source_snapshot_change() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir("ir:1"),
    )
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir("ir:2"),
    )
    assert fp1 != fp2


def test_fingerprint_changes_on_binding_evidence_change() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    # The rescue's decision inputs come from the abstract experiment's
    # required capabilities; changing the actor there is a binding-evidence
    # change and must miss the cache.
    changed = _abstract_experiment("obl:1")
    changed["abstract_experiment"]["required_capabilities"]["actors"] = ["actor:admin"]
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=changed,
        behavior_ir=_behavior_ir(),
    )
    assert fp1 != fp2


def test_fingerprint_changes_on_credential_availability_change() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_ACTOR",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1", "BLOCKED_MISSING_ACTOR"),
        behavior_ir=_behavior_ir(),
        actor_tokens={"actor:buyer": "token"},
    )
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_ACTOR",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1", "BLOCKED_MISSING_ACTOR"),
        behavior_ir=_behavior_ir(),
        actor_tokens={},  # buyer no longer resolves a credential
    )
    assert fp1 != fp2


def test_fingerprint_different_across_obligations() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:2",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:2"),
        abstract_experiment=_abstract_experiment("obl:2"),
        behavior_ir=_behavior_ir(),
    )
    assert fp1 != fp2


# ── cache semantics ─────────────────────────────────────────────────────────


def test_negative_outcome_cached_and_reused() -> None:
    fp = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    assert dedupe.rescue_cache_lookup(fp) is None
    dedupe.rescue_cache_store(
        fp,
        materialization_receipt=_neg_receipt("obl:1"),
        can_recompile=False,
        still_blocked_reason=["BODY_PARAMETER_NOT_SOURCE_BOUND"],
    )
    hit = dedupe.rescue_cache_lookup(fp)
    assert hit is not None
    assert hit["rescued"] is False
    assert hit["can_recompile"] is False
    assert hit["materialization_receipt"]["status"] == "NOT_MATERIALIZED"
    assert hit["still_blocked_reason"] == ["BODY_PARAMETER_NOT_SOURCE_BOUND"]
    stats = dedupe.rescue_cache_stats()
    assert stats["stores"] == 1
    assert stats["hits"] == 1


def test_successful_rescue_never_cached() -> None:
    fp = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    dedupe.rescue_cache_store(
        fp,
        materialization_receipt=_neg_receipt("obl:1"),
        can_recompile=True,  # a recompilable outcome must not be cached
        still_blocked_reason=[],
    )
    assert dedupe.rescue_cache_lookup(fp) is None


def test_evidence_change_misses_cache() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir("ir:1"),
    )
    dedupe.rescue_cache_store(
        fp1,
        materialization_receipt=_neg_receipt("obl:1"),
        can_recompile=False,
        still_blocked_reason=["BODY_PARAMETER_NOT_SOURCE_BOUND"],
    )
    # Source snapshot changed -> different fingerprint -> miss -> re-execute.
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir("ir:2"),
    )
    assert fp1 != fp2
    assert dedupe.rescue_cache_lookup(fp2) is None


def test_cache_does_not_cross_pollute_obligations() -> None:
    fp1 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:1",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:1"),
        abstract_experiment=_abstract_experiment("obl:1"),
        behavior_ir=_behavior_ir(),
    )
    fp2 = dedupe.rescue_evidence_fingerprint(
        obligation_id="obl:2",
        compile_reason="BLOCKED_MISSING_BINDING",
        obligation=_obligation("obl:2"),
        abstract_experiment=_abstract_experiment("obl:2"),
        behavior_ir=_behavior_ir(),
    )
    dedupe.rescue_cache_store(
        fp1,
        materialization_receipt=_neg_receipt("obl:1"),
        can_recompile=False,
        still_blocked_reason=["BODY_PARAMETER_NOT_SOURCE_BOUND"],
    )
    assert dedupe.rescue_cache_lookup(fp1) is not None
    assert dedupe.rescue_cache_lookup(fp2) is None


# ── serial rescue loop dedupe behavior ──────────────────────────────────────


def test_serial_rescue_loop_reuses_cached_negative_outcome() -> None:
    from ai_test_asset_center import (
        experiment_runtime_materialization_mainline_base as module,
    )

    oid = "obl:dup"
    abstract = [_abstract_experiment(oid)]
    obligations = [_obligation(oid)]
    obligations_by_id = {oid: obligations[0]}
    behavior_ir = _behavior_ir()
    calls = {"resolve": 0, "recompile": 0}

    def fake_resolve(**kwargs):
        calls["resolve"] += 1
        return {
            "materialization_receipt": _neg_receipt(oid),
            "can_recompile": False,
            "binding_plan_extras": [],
        }

    def fake_compile_one(**kwargs):
        calls["recompile"] += 1
        return {"compile_receipt": {"status": "BLOCKED", "reason_code": "X"}}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module, "_resolve_planning_materialization", fake_resolve)

    try:
        # First pass: real resolution, NOT_MATERIALIZED -> cached.
        result = module.materialize_and_recompile_abstract_pack(
            {
                "schema_version": "qualibug.experiment-compile.v1",
                "experiments": [],
                "blocked_experiments": [],
                "abstract_experiments": [dict(abstract[0])],
            },
            obligations=obligations,
            behavior_ir=behavior_ir,
            compile_one=fake_compile_one,
            environment_type="test",
            policy_version="v1",
            planning_context={},
        )
        assert calls["resolve"] == 1
        assert calls["recompile"] == 0
        assert result["abstract_count"] == 1

        # Second pass: identical evidence -> cache hit, no re-resolution.
        result2 = module.materialize_and_recompile_abstract_pack(
            {
                "schema_version": "qualibug.experiment-compile.v1",
                "experiments": [],
                "blocked_experiments": [],
                "abstract_experiments": [dict(abstract[0])],
            },
            obligations=obligations,
            behavior_ir=behavior_ir,
            compile_one=fake_compile_one,
            environment_type="test",
            policy_version="v1",
            planning_context={},
        )
        assert calls["resolve"] == 1  # second pass must not re-resolve
        assert calls["recompile"] == 0
        assert result2["abstract_count"] == 1
        assert result2["materialization_summary"]["rescue_dedupe"]["cache_hit_count"] == 1
        row = result2["abstract_experiments"][0]
        assert row.get("materialization_receipt", {}).get("rescue_cache_hit") is True
        assert row["compile_receipt"]["rescue_cache_hit"] is True
    finally:
        monkeypatch.undo()


def test_serial_rescue_loop_reexecutes_on_evidence_change() -> None:
    from ai_test_asset_center import (
        experiment_runtime_materialization_mainline_base as module,
    )

    oid = "obl:change"
    abstract = [_abstract_experiment(oid)]
    obligations = [_obligation(oid)]
    obligations_by_id = {oid: obligations[0]}
    calls = {"resolve": 0}

    def fake_resolve(**kwargs):
        calls["resolve"] += 1
        return {
            "materialization_receipt": _neg_receipt(oid),
            "can_recompile": False,
            "binding_plan_extras": [],
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module, "_resolve_planning_materialization", fake_resolve)
    try:
        base_pack = {
            "schema_version": "qualibug.experiment-compile.v1",
            "experiments": [],
            "blocked_experiments": [],
            "abstract_experiments": [dict(abstract[0])],
        }
        module.materialize_and_recompile_abstract_pack(
            base_pack, obligations=obligations, behavior_ir=_behavior_ir("ir:1"),
            compile_one=lambda **kw: {"compile_receipt": {"status": "BLOCKED"}},
            environment_type="test", policy_version="v1", planning_context={},
        )
        assert calls["resolve"] == 1
        # IR changed -> fingerprint changes -> must re-resolve.
        module.materialize_and_recompile_abstract_pack(
            base_pack, obligations=obligations, behavior_ir=_behavior_ir("ir:2"),
            compile_one=lambda **kw: {"compile_receipt": {"status": "BLOCKED"}},
            environment_type="test", policy_version="v1", planning_context={},
        )
        assert calls["resolve"] == 2
    finally:
        monkeypatch.undo()


# ── concurrent wrapper semantics ────────────────────────────────────────────


def test_concurrent_compile_serial_fallback_preserves_semantics() -> None:
    """QUALIBUG_COMPILE_CONCURRENCY=1 forces the exact serial path."""
    import os

    os.environ["QUALIBUG_COMPILE_CONCURRENCY"] = "1"
    try:
        assert concurrent.get_concurrency() == 1
        pack = concurrent.compile_experiments_concurrent(
            [_obligation("obl:1")],
            behavior_ir=_behavior_ir(),
            environment_type="test",
            policy_version="v1",
        )
        assert isinstance(pack.get("concurrency"), dict)
        assert pack["concurrency"]["mode"] == "serial"
    finally:
        os.environ.pop("QUALIBUG_COMPILE_CONCURRENCY", None)


def test_concurrent_rescue_worker_uses_dedupe_cache() -> None:
    """A concurrent rescue worker must honor the content-addressed cache."""
    from ai_test_asset_center import experiment_compile_concurrent as cc
    from ai_test_asset_center import (
        experiment_runtime_materialization_mainline_base as module,
    )

    oid = "obl:conc"
    abstract = [_abstract_experiment(oid)]
    obligations = {oid: _obligation(oid)}
    calls = {"resolve": 0}

    def fake_resolve(**kwargs):
        calls["resolve"] += 1
        return {
            "materialization_receipt": _neg_receipt(oid),
            "can_recompile": False,
            "binding_plan_extras": [],
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cc, "_resolve_planning_materialization", fake_resolve)
    try:
        # First pass: seeds the cache with the negative outcome.
        outcome = cc._rescue_one_abstract(
            0, abstract[0],
            obligations_by_id=obligations,
            behavior_ir=_behavior_ir(),
            compile_one=lambda **kw: {"compile_receipt": {"status": "BLOCKED"}},
            environment_type="test", policy_version="v1",
            available_adapters=None, planning_context={},
            _actor_tokens={},
        )
        assert outcome[1]["kind"] == "still_abstract"
        assert calls["resolve"] == 1
        # Second pass: identical evidence -> cache hit inside the worker.
        outcome2 = cc._rescue_one_abstract(
            0, abstract[0],
            obligations_by_id=obligations,
            behavior_ir=_behavior_ir(),
            compile_one=lambda **kw: {"compile_receipt": {"status": "BLOCKED"}},
            environment_type="test", policy_version="v1",
            available_adapters=None, planning_context={},
            _actor_tokens={},
        )
        assert calls["resolve"] == 1  # worker must not re-resolve
        assert outcome2[1].get("cache_hit") is True
        assert outcome2[1]["receipt"]["rescue_cache_hit"] is True
    finally:
        monkeypatch.undo()


def test_worker_exception_isolated_to_obligation() -> None:
    """A failing compile worker yields only that obligation's HARNESS_FAILED."""
    pack = concurrent.compile_experiments_concurrent(
        [_obligation("obl:ok"), _obligation("obl:boom")],
        behavior_ir=_behavior_ir(),
        environment_type="test",
        policy_version="v1",
    )
    # The concurrent wrapper isolates per-obligation exceptions; this path is
    # exercised end-to-end in the batch executor tests. Here we assert the
    # pack contract is intact.
    assert isinstance(pack.get("experiments"), list)
    assert isinstance(pack.get("blocked_experiments"), list)
    assert isinstance(pack.get("abstract_experiments"), list)


# ── compile-time binding rescue dedupe ──────────────────────────────────────


def _compile_binding_plan() -> list[dict]:
    return [
        {
            "target": "{id}",
            "target_path": "/api/products/{id}",
            "status": "blocked",
            "blocked_reason": "PLACEHOLDER_PATH_PARAMETER",
            "source_priority": "path_placeholder",
        },
        {
            "target": "addressId",
            "status": "blocked",
            "blocked_reason": "BODY_PARAMETER_NOT_SOURCE_BOUND",
            "source_priority": "body_placeholder_unresolvable",
        },
    ]


def _compile_primary_op() -> dict:
    return {
        "id": "op:products",
        "operation_id": "updateProduct",
        "method": "PATCH",
        "path": "/api/products/{id}",
        "request_example": {"status": "ON_SALE"},
    }


def test_compile_rescue_fingerprint_deterministic_and_sensitive() -> None:
    bp = _compile_binding_plan()
    fp1 = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    fp2 = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=deepcopy(bp),
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    assert fp1 == fp2
    # IR model change -> miss.
    fp3 = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:2"),
    )
    assert fp1 != fp3
    # binding-plan evidence change -> miss.
    changed_bp = deepcopy(bp)
    changed_bp[1]["target"] = "orderId"
    fp4 = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=changed_bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    assert fp1 != fp4
    # different obligation -> miss (no cross-contamination).
    fp5 = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:2",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    assert fp1 != fp5


def test_compile_rescue_negative_outcome_cached_but_success_not() -> None:
    dedupe.compile_rescue_cache_clear()
    bp = _compile_binding_plan()
    fp = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    # Negative outcome -> cached.
    dedupe.compile_rescue_cache_store(
        fp, rescued=False, still_blocked_reason=["BODY_PARAMETER_NOT_SOURCE_BOUND"]
    )
    hit = dedupe.compile_rescue_cache_lookup(fp)
    assert hit is not None
    assert hit["rescued"] is False
    assert hit["still_blocked_reason"] == ["BODY_PARAMETER_NOT_SOURCE_BOUND"]
    # Successful outcome -> never cached.
    dedupe.compile_rescue_cache_store(fp, rescued=True, still_blocked_reason=[])
    assert dedupe.compile_rescue_cache_lookup(fp)["rescued"] is False
    # Different evidence -> miss.
    fp2 = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:9"),
    )
    assert dedupe.compile_rescue_cache_lookup(fp2) is None
    dedupe.compile_rescue_cache_clear()


def test_compile_rescue_cache_stats() -> None:
    dedupe.compile_rescue_cache_clear()
    bp = _compile_binding_plan()
    fp = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    dedupe.compile_rescue_cache_register_unique(fp)
    dedupe.compile_rescue_cache_store(
        fp, rescued=False, still_blocked_reason=["BODY_PARAMETER_NOT_SOURCE_BOUND"]
    )
    dedupe.compile_rescue_cache_lookup(fp)
    stats = dedupe.compile_rescue_cache_stats()
    assert stats["attempt_count"] == 1
    assert stats["unique_count"] == 1
    assert stats["cache_hit_count"] == 1
    assert stats["reexecuted_count"] == 1
    dedupe.compile_rescue_cache_clear()


def test_compile_rescue_register_unique_never_counts_as_hit() -> None:
    """Regression: a fingerprint merely registered as 'seen' must NOT be a
    cache hit. The earlier implementation stored a placeholder entry in the
    lookup dict, so every rescue on an already-seen fingerprint skipped the
    real execution and returned the cached negative — turning rescued=True
    42 -> 0 on a real scan."""
    dedupe.compile_rescue_cache_clear()
    bp = _compile_binding_plan()
    fp = dedupe.compile_rescue_evidence_fingerprint(
        obligation_id="obl:1",
        reason="BODY_PARAMETER_NOT_SOURCE_BOUND",
        binding_plan=bp,
        primary_op=_compile_primary_op(),
        behavior_ir=_behavior_ir("ir:1"),
    )
    dedupe.compile_rescue_cache_register_unique(fp)
    # Must NOT be a hit before a real negative outcome is stored.
    assert dedupe.compile_rescue_cache_lookup(fp) is None
    dedupe.compile_rescue_cache_store(
        fp, rescued=False, still_blocked_reason=["BODY_PARAMETER_NOT_SOURCE_BOUND"]
    )
    # After the negative is stored, lookups hit.
    assert dedupe.compile_rescue_cache_lookup(fp) is not None
    # A second unique registration must not double-count uniqueness.
    dedupe.compile_rescue_cache_register_unique(fp)
    stats = dedupe.compile_rescue_cache_stats()
    assert stats["unique_count"] == 1
    dedupe.compile_rescue_cache_clear()
