"""Unit tests for the resource-domain isolated concurrent batch scheduler.

Covers: group correctness (same resource serial / different resource parallel /
read experiments free), concurrency enforcement, receipt completeness and
ordering, failure isolation, global-budget semantics, cross-group deliverable
dedupe, and the QUALIBUG_EXECUTOR_CONCURRENCY configuration surface.

No live target, no HTTP, no LLM: the serial core is replaced with a recording
fake so the scheduler's grouping / pooling / aggregation logic is tested in
isolation.
"""
from __future__ import annotations

import threading
import time

import pytest

from ai_test_asset_center import (
    _experiment_batch_executor_single_finding_mechanics as core_m,
)
from ai_test_asset_center import safe_experiment_prioritizer as prio_m
from ai_test_asset_center.experiment_batch_concurrent_scheduler import (
    _apply_global_budget,
    execute_selected_experiments_concurrent,
    get_concurrency,
    partition_serial_groups,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _exp(
    oid: str,
    *,
    method: str = "POST",
    path: str = "/api/orders/{order_id}",
    actor: str = "actor:admin",
    resource: str | None = "res-1",
    read_only: bool = False,
) -> dict:
    """Compiled-experiment shaped fixture (fields mirror the compiler output)."""
    method = "GET" if read_only else method
    step = {"step_id": "s1", "actor_ref": actor, "path": path, "method": method}
    exp: dict = {
        "experiment_id": f"exp:{oid}",
        "obligation_id": oid,
        "risk_family": "authorization",
        "operation_key": f"op:{path}",
        "compile_receipt": {"status": "COMPILED"},
        "actor_selection_contract": {
            "control_actor_ref": "actor:control",
            "treatment_actor_ref": actor,
        },
        "treatment_plan": [step],
        "control_plan": [
            {
                "step_id": "c1",
                "actor_ref": "actor:control",
                "path": "/api/orders",
                "method": "GET",
            }
        ],
    }
    if resource is not None and not read_only:
        exp["runtime_bindings"] = {"order_id": resource}
    return exp


def _selected(oids: list[str]) -> list[dict]:
    return [
        {
            "obligation_id": oid,
            "experiment_id": f"exp:{oid}",
            "risk_family": "authorization",
        }
        for oid in oids
    ]


def _fake_core(monkeypatch: pytest.MonkeyPatch, **options) -> dict:
    """Install a recording fake serial core; returns instrumentation state."""
    state: dict = {
        "calls": [],
        "active": 0,
        "max_active": 0,
        "lock": threading.Lock(),
        "sleep": options.get("sleep", 0.0),
        "raise_for_oid": options.get("raise_for_oid", None),
    }

    def fake_batch(
        selected, *, experiments_by_obligation, behavior_ir, root, project,
        base_url, runtime_contract, mainline_run, campaign_id,
        experiment_budget, validation_phase,
    ):
        with state["lock"]:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            state["calls"].append(
                [row.get("obligation_id") for row in selected]
            )
        try:
            if state["sleep"]:
                time.sleep(state["sleep"])
            results, execution_results, compile_results = [], {}, {}
            for item in selected:
                row = dict(item)
                oid = row["obligation_id"]
                if state["raise_for_oid"] and oid == state["raise_for_oid"]:
                    raise RuntimeError("boom:" + oid)
                results.append({
                    "schema_version": "qualibug.experiment-execution.v1",
                    "obligation_id": oid,
                    "selected_obligation_id": oid,
                    "experiment_id": row.get("experiment_id"),
                    "execution_id": f"exec:{oid}",
                    "evidence_id": f"ev:{oid}",
                    "candidate_id": f"cand:{oid}",
                    "slice_id": f"slice:{oid}",
                    "campaign_id": campaign_id,
                    "status": "EXECUTED",
                    "reason_code": "",
                    "finding": None,
                    "execution_receipt": {
                        "status": "EXECUTED",
                        "obligation_id": oid,
                        "experiment_id": row.get("experiment_id"),
                        "campaign_id": campaign_id,
                    },
                })
                execution_results[oid] = {
                    "status": "EXECUTED",
                    "obligation_id": oid,
                    "experiment_id": row.get("experiment_id"),
                    "execution_id": f"exec:{oid}",
                    "receipt_id": f"r:{oid}",
                    "elapsed_ms": 1,
                    "operational_receipt": {
                        "receipt_id": f"op:{oid}", "status": "ok"
                    },
                }
                compile_results[oid] = {
                    "status": "COMPILED",
                    "experiment_id": row.get("experiment_id"),
                }
            return {
                "results": results,
                "execution_results": execution_results,
                "compile_results": compile_results,
                "gate_results": {},
                "findings": [],
                "budget_deferred": [],
                "executed_count": len(results),
                "blocked_count": 0,
                "harness_failure_count": 0,
                "cleanup_failures": 0,
                "duplicate_delivery_count": 0,
                "validation_phase": validation_phase,
                "selected_count": len(selected),
            }
        finally:
            with state["lock"]:
                state["active"] -= 1

    monkeypatch.setattr(core_m, "execute_selected_experiments", fake_batch)
    monkeypatch.setattr(
        prio_m, "prioritize_experiments",
        lambda **kw: {"ordered_experiment_ids": []},
    )
    return state


def _run(selected, exps, monkeypatch, **options) -> tuple[dict, dict]:
    """Install a recording fake serial core once and run the scheduler.

    Returns (batch, instrumentation_state).
    """
    state = _fake_core(monkeypatch, **options)
    batch = execute_selected_experiments_concurrent(
        selected,
        experiments_by_obligation=exps,
        behavior_ir={"operations": []},
        root=".",
        project="probe",
        base_url="http://target.local",
        runtime_contract={"status": "approved", "validation_phase": "formal"},
        mainline_run={
            "campaign_id": "camp-1",
            "run_id": "run-1",
            "contract_fingerprint": "fp",
        },
        campaign_id="camp-1",
        validation_phase="formal",
    )
    return batch, state


# ── concurrency configuration ───────────────────────────────────────────────

def test_concurrency_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUALIBUG_EXECUTOR_CONCURRENCY", raising=False)
    assert get_concurrency() == 8
    monkeypatch.setenv("QUALIBUG_EXECUTOR_CONCURRENCY", "4")
    assert get_concurrency() == 4
    monkeypatch.setenv("QUALIBUG_EXECUTOR_CONCURRENCY", "abc")
    assert get_concurrency() == 8  # invalid → default
    monkeypatch.setenv("QUALIBUG_EXECUTOR_CONCURRENCY", "100")
    assert get_concurrency() == 16  # clamped
    monkeypatch.setenv("QUALIBUG_EXECUTOR_CONCURRENCY", "1")
    assert get_concurrency() == 2  # floor


# ── grouping correctness ────────────────────────────────────────────────────

def test_group_same_resource_serial() -> None:
    exps = {
        "a": _exp("a", resource="res-1", actor="actor:admin"),
        "b": _exp("b", resource="res-1", actor="actor:manager"),
        "c": _exp("c", resource="res-1", actor="actor:admin"),
    }
    groups = partition_serial_groups(_selected(["a", "b", "c"]), exps, {})
    keys = [tuple(row.get("obligation_id") for row in g) for g in groups]
    assert len(keys) == 1
    assert set(keys[0]) == {"a", "b", "c"}
    assert list(keys[0]) == ["a", "b", "c"]  # relative order kept


def test_group_different_resources_parallel() -> None:
    exps = {
        "a": _exp("a", resource="res-1"),
        "b": _exp("b", resource="res-2"),
        "c": _exp("c", resource="res-3"),
    }
    groups = partition_serial_groups(_selected(["a", "b", "c"]), exps, {})
    assert len(groups) == 3


def test_group_read_experiments_free_and_not_joined() -> None:
    exps = {
        "w1": _exp("w1", resource="res-1"),
        "r1": _exp("r1", read_only=True),
        "w2": _exp("w2", resource="res-1"),
        "r2": _exp("r2", read_only=True),
    }
    groups = partition_serial_groups(
        _selected(["w1", "r1", "w2", "r2"]), exps, {}
    )
    group_ids = [sorted(_gid(row) for row in g) for g in groups]
    write_group = next(g for g in group_ids if "w1" in g)
    assert write_group == ["w1", "w2"]  # same resource serial
    read_groups = [g for g in group_ids if "r1" in g or "r2" in g]
    assert len(read_groups) == 2  # each read experiment is its own free group
    assert all(len(g) == 1 for g in read_groups)


def test_group_unknown_resource_same_actor_serial_different_actor_parallel() -> None:
    # No runtime_bindings → unknown resource instance.
    exps = {
        "a": _exp("a", resource=None, actor="actor:admin"),
        "b": _exp("b", resource=None, actor="actor:admin"),
        "c": _exp("c", resource=None, actor="actor:manager"),
    }
    groups = partition_serial_groups(_selected(["a", "b", "c"]), exps, {})
    group_ids = [sorted(_gid(row) for row in g) for g in groups]
    assert ["a", "b"] in group_ids  # same actor serial
    assert ["c"] in group_ids  # different actor parallel


def test_group_different_interface_parallel() -> None:
    exps = {
        "a": _exp("a", path="/api/orders/{order_id}", resource="res-1"),
        "b": _exp("b", path="/api/carts/{cart_id}", resource="cart-1"),
        "c": _exp("c", path="/api/users/{user_id}", resource="user-1"),
    }
    groups = partition_serial_groups(_selected(["a", "b", "c"]), exps, {})
    assert len(groups) == 3


def test_group_method_unknown_fails_closed_as_write() -> None:
    exps = {
        "a": {
            "experiment_id": "exp:a",
            "obligation_id": "a",
            "compile_receipt": {"status": "COMPILED"},
            "treatment_plan": [
                {"step_id": "s1", "actor_ref": "actor:admin", "path": "/api/x/{id}"}
            ],
            "control_plan": [],
        },
        "b": {
            "experiment_id": "exp:b",
            "obligation_id": "b",
            "compile_receipt": {"status": "COMPILED"},
            "treatment_plan": [
                {"step_id": "s1", "actor_ref": "actor:admin", "path": "/api/x/{id}"}
            ],
            "control_plan": [],
        },
    }
    groups = partition_serial_groups(_selected(["a", "b"]), exps, {})
    assert len(groups) == 1  # unclassifiable steps → serialized together


def _gid(row: dict) -> str:
    return str(row.get("obligation_id") or "")


# ── concurrency / receipts / isolation ──────────────────────────────────────

def test_concurrency_workers_active(monkeypatch: pytest.MonkeyPatch) -> None:
    selected, exps = [], {}
    for i in range(16):  # 16 distinct resources → 16 parallel groups
        oid = f"obl:{i}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource=f"res-{i}")
    batch, state = _run(selected, exps, monkeypatch, sleep=0.1)
    assert batch["concurrency"]["mode"] == "concurrent"
    assert batch["concurrency"]["group_count"] == 16
    assert state["max_active"] >= 2, f"no real parallelism: {state['max_active']}"
    assert len(state["calls"]) == 16


def test_receipts_complete_and_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    selected, exps = [], {}
    for i in range(12):
        oid = f"obl:{i}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource=f"res-{i % 3}")
    batch, _ = _run(selected, exps, monkeypatch, sleep=0.02)
    assert batch["every_experiment_has_receipt"] is True
    ids = [row["selected_obligation_id"] for row in batch["results"]]
    assert ids == [f"obl:{i}" for i in range(12)]  # original order preserved
    assert batch["executed_count"] == 12
    assert batch["selected_count"] == 12


def test_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    selected, exps = [], {}
    for i in range(9):
        oid = f"obl:{i}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource=f"res-{i}")
    # Introduce one failing group with two same-resource experiments.
    boom_oid = "obl:boom"
    selected.append(
        {"obligation_id": boom_oid, "experiment_id": "exp:boom"}
    )
    exps[boom_oid] = _exp(boom_oid, resource="res-boom")
    twin_oid = "obl:twin"
    selected.append(
        {"obligation_id": twin_oid, "experiment_id": "exp:twin"}
    )
    exps[twin_oid] = _exp(twin_oid, resource="res-boom")
    batch, _ = _run(
        selected, exps, monkeypatch, sleep=0.02, raise_for_oid="obl:boom"
    )
    assert batch["every_experiment_has_receipt"] is True
    by_oid = {
        row["selected_obligation_id"]: row for row in batch["results"]
    }
    assert len(by_oid) == 11  # no receipt lost
    assert by_oid[boom_oid]["status"] == "HARNESS_FAILED"
    assert by_oid[twin_oid]["status"] == "HARNESS_FAILED"
    assert by_oid["obl:0"]["status"] == "EXECUTED"  # other groups unaffected
    assert batch["harness_failure_count"] == 2
    assert len(batch["concurrency"]["group_errors"]) == 1
    assert "concurrent_group_execution_failed" in batch["concurrency"]["group_errors"][0]


def test_aggregation_envelope_present(monkeypatch: pytest.MonkeyPatch) -> None:
    selected, exps = [], {}
    for i in range(8):
        oid = f"obl:{i}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource=f"res-{i % 2}")
    batch, _ = _run(selected, exps, monkeypatch)
    assert isinstance(batch.get("execution_coverage_funnel"), dict)
    assert isinstance(batch.get("blocker_attribution"), dict)
    assert isinstance(batch.get("validation_gate"), dict)
    assert batch["campaign_validation_receipt"]["campaign_validation_status"] == "PASSED"
    assert batch["concurrency"]["mode"] == "concurrent"
    assert batch["concurrency"]["max_workers"] == 8
    assert batch["compile_results"] and batch["execution_results"]


def test_cross_group_deliverable_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two groups delivering the same operation-level property collapse to one."""
    state: dict = {"lock": threading.Lock()}

    def fake_with_findings(
        selected, *, experiments_by_obligation, behavior_ir, root, project,
        base_url, runtime_contract, mainline_run, campaign_id,
        experiment_budget, validation_phase,
    ):
        results, execution_results = [], {}
        for item in selected:
            oid = item["obligation_id"]
            results.append({
                "schema_version": "qualibug.experiment-execution.v1",
                "obligation_id": oid,
                "selected_obligation_id": oid,
                "experiment_id": item.get("experiment_id"),
                "execution_id": f"exec:{oid}",
                "evidence_id": f"ev:{oid}",
                "candidate_id": f"cand:{oid}",
                "slice_id": f"slice:{oid}",
                "campaign_id": campaign_id,
                "status": "EXECUTED",
                "reason_code": "",
                "finding": {
                    "id": f"finding:{oid}",
                    "finding_id": f"finding:{oid}",
                    "title": (
                        "[ContractOracle] authorization: admin"
                        f"{oid[-1]} POST /api/orders"
                    ),
                    "obligation_id": oid,
                },
                "execution_receipt": {
                    "status": "EXECUTED",
                    "obligation_id": oid,
                    "experiment_id": item.get("experiment_id"),
                    "campaign_id": campaign_id,
                },
            })
            execution_results[oid] = {
                "status": "EXECUTED", "obligation_id": oid,
                "experiment_id": item.get("experiment_id"),
                "execution_id": f"exec:{oid}", "receipt_id": f"r:{oid}",
                "elapsed_ms": 1,
            }
        return {
            "results": results,
            "execution_results": execution_results,
            "compile_results": {item["obligation_id"]: {"status": "COMPILED"} for item in selected},
            "gate_results": {},
            "findings": [row["finding"] for row in results],
            "budget_deferred": [],
            "executed_count": len(results),
            "blocked_count": 0,
            "harness_failure_count": 0,
            "cleanup_failures": 0,
            "duplicate_delivery_count": 0,
            "validation_phase": validation_phase,
            "selected_count": len(selected),
        }

    monkeypatch.setattr(core_m, "execute_selected_experiments", fake_with_findings)
    monkeypatch.setattr(prio_m, "prioritize_experiments",
                        lambda **kw: {"ordered_experiment_ids": []})
    selected, exps = [], {}
    for i in range(4):
        oid = f"obl:{i}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource=f"res-{i}")
    batch = execute_selected_experiments_concurrent(
        selected,
        experiments_by_obligation=exps,
        behavior_ir={"operations": []},
        root=".",
        project="probe",
        base_url="http://target.local",
        runtime_contract={"status": "approved", "validation_phase": "formal"},
        mainline_run={
            "campaign_id": "camp-1",
            "run_id": "run-1",
            "contract_fingerprint": "fp",
        },
        campaign_id="camp-1",
        validation_phase="formal",
    )
    assert len(batch["findings"]) == 1  # property delivered once
    assert batch["duplicate_delivery_count"] == 3
    first = batch["findings"][0]
    assert len(first.get("duplicate_variants", [])) == 3


def test_global_budget_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    """Budget is enforced once at the scheduler level (serial semantics)."""
    selected, exps = [], {}
    for i in range(40):
        oid = f"obl:{i:02d}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource=f"res-{i}")
    batch, _ = _run(selected, exps, monkeypatch)
    # Formal phase budget ≤ 100, distinct operations=1, distinct families=1
    # → budget = max(validation, 2), below the shared hard cap → all 40 fit.
    assert batch["budget_exceeded_count"] == 0
    assert batch["selected_count"] == 40
    assert batch["concurrency"]["group_count"] == 40  # one group per resource


def test_global_budget_honors_contract_above_legacy_cap() -> None:
    """The concurrent mainline must not re-cap a valid 250 budget at 200."""
    selected = [
            {
                "obligation_id": f"obl:{i:03d}",
                "operation_key": "POST /api/resources",
                "risk_family": "validation",
            }
        for i in range(300)
    ]
    experiments = {
        row["obligation_id"]: {
            "experiment_id": f"exp:{row['obligation_id']}",
            "obligation_id": row["obligation_id"],
        }
        for row in selected
    }

    budgeted, deferred, receipt, budget = _apply_global_budget(
        selected,
        runtime_contract={"experiment_budget": 250},
        validation_phase="formal",
        behavior_ir={},
        experiments_by_obligation=experiments,
        family_quota=1,
    )

    assert budget == 250
    assert len(budgeted) == 250
    assert len(deferred) == 50
    assert receipt["budget"] == 250


def test_serial_fallback_single_group(monkeypatch: pytest.MonkeyPatch) -> None:
    selected, exps = [], {}
    for i in range(3):
        oid = f"obl:{i}"
        selected.append(
            {"obligation_id": oid, "experiment_id": f"exp:{oid}"}
        )
        exps[oid] = _exp(oid, resource="res-shared")
    batch, _ = _run(selected, exps, monkeypatch)
    assert batch["concurrency"]["mode"] == "serial_fallback"
    assert batch["concurrency"]["group_count"] == 1
    assert len(batch["results"]) == 3


def test_identity_validation() -> None:
    # Duplicate obligation ids are rejected before any execution.
    with pytest.raises(ValueError):
        execute_selected_experiments_concurrent(
            [
                {"obligation_id": "x", "experiment_id": "e1"},
                {"obligation_id": "x", "experiment_id": "e2"},
            ],
            experiments_by_obligation={},
            behavior_ir={},
            root=".",
            project="p",
            base_url="http://t",
            runtime_contract={},
            mainline_run={"campaign_id": "camp-1"},
            campaign_id="camp-1",
        )
    # Campaign identity mismatch is rejected before any execution.
    with pytest.raises(ValueError):
        execute_selected_experiments_concurrent(
            [{"obligation_id": "y", "experiment_id": "e3"}],
            experiments_by_obligation={},
            behavior_ir={},
            root=".",
            project="p",
            base_url="http://t",
            runtime_contract={},
            mainline_run={"campaign_id": "camp-other"},
            campaign_id="camp-1",
        )
