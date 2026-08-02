from __future__ import annotations


def _operations() -> list[dict[str, object]]:
    return [{
        "method": "GET",
        "path": "/api/resources",
        "operation_id": "listResources",
        "source_id": "api_spec",
        "parameters": [],
        "request_schema": {},
        "response_schema": {},
    }]


def _observation(status_code: int) -> dict[str, object]:
    from ai_test_asset_center.runtime_interface_discovery import (
        build_runtime_interface_observation_receipt,
        plan_runtime_interface_candidates,
    )

    candidate = plan_runtime_interface_candidates(
        _operations(),
        action_markers=["export"],
        max_candidates=1,
    )["candidates"][0]
    return build_runtime_interface_observation_receipt(
        candidate,
        {
            "status_code": status_code,
            "request_receipt_id": f"request-{status_code}",
            "response_fingerprint": "a" * 64,
        },
    )


def test_runtime_observation_creates_a_traceable_second_planning_round(
    monkeypatch,
) -> None:
    import ai_test_asset_center.adaptive_behavior_ir_expansion as expansion

    initial_ir = {"model_id": "model-round-1", "operations": [{"id": "op-1"}]}
    rebuilt_ir = {
        "model_id": "model-round-2",
        "operations": [
            {"id": "op-1", "source_refs": [{"source_id": "api_spec"}]},
            {"id": "op-runtime", "source_refs": [{"source_id": "request-200"}]},
        ],
    }
    delta_obligation = {
        "obligation_id": "obl-runtime",
        "risk_family": "authorization",
        "compile_status": "COMPILED",
        "required_operations": ["op-runtime"],
        "required_actors": [],
        "relation_refs": [],
        "source_refs": [{"source_id": "request-200"}],
    }
    compiled_experiment = {
        "obligation_id": "obl-runtime",
        "experiment_id": "exp-runtime",
        "compile_receipt": {"status": "COMPILED"},
        "observers": [{"observer_id": "observer-runtime", "adapter": "http_api"}],
        "source_refs": [{"source_id": "request-200"}],
    }
    monkeypatch.setattr(
        expansion,
        "build_behavior_ir_from_knowledge_asset",
        lambda *args, **kwargs: rebuilt_ir,
    )
    monkeypatch.setattr(
        expansion,
        "compile_obligations_from_behavior_ir",
        lambda behavior_ir: {"obligations": [
            {"obligation_id": "obl-existing"},
            delta_obligation,
        ]},
    )
    monkeypatch.setattr(
        expansion,
        "compile_experiments",
        lambda *args, **kwargs: {
            "experiments": [compiled_experiment],
            "blocked_experiments": [],
        },
    )
    monkeypatch.setattr(
        expansion,
        "attach_fixture_dag_to_experiments",
        lambda pack, **kwargs: pack,
    )

    result = expansion.expand_behavior_ir_from_runtime_observations(
        initial_behavior_ir=initial_ir,
        existing_obligation_ids={"obl-existing"},
        knowledge_asset={"asset_id": "asset-1"},
        documented_operations=_operations(),
        observation_receipts=[_observation(200)],
        project_id="project",
        source_snapshot_hash="source-hash",
        runtime_actors=[],
        environment_type="test",
        policy_version="policy-1",
        budget=10,
        planning_round=2,
    )

    assert result["status"] == "EXPANDED"
    assert result["behavior_ir"]["model_id"] == "model-round-2"
    assert [row["obligation_id"] for row in result["delta_obligations"]] == [
        "obl-runtime"
    ]
    assert result["selected_rows"][0]["planning_round"] == 2
    assert result["round_receipt"]["input_behavior_ir_id"] == "model-round-1"
    assert result["round_receipt"]["output_behavior_ir_id"] == "model-round-2"
    assert result["round_receipt"]["discovered_operation_count"] == 1
    assert result["round_receipt"]["new_obligation_count"] == 1
    assert result["round_receipt"]["receipt_fingerprint"]


def test_compile_blocked_obligation_is_recompiled_after_runtime_expansion(
    monkeypatch,
) -> None:
    import ai_test_asset_center.adaptive_behavior_ir_expansion as expansion

    initial_ir = {"model_id": "model-round-1", "operations": [{"id": "op-1"}]}
    rebuilt_ir = {
        "model_id": "model-round-2",
        "operations": [
            {"id": "op-1", "source_refs": [{"source_id": "api_spec"}]},
            {"id": "op-runtime", "source_refs": [{"source_id": "request-200"}]},
        ],
    }
    blocked_obligation = {
        "obligation_id": "obl-blocked",
        "risk_family": "authorization",
        "compile_status": "BLOCKED",
        "required_operations": ["op-runtime"],
        "required_actors": [],
        "relation_refs": [],
        "source_refs": [{"source_id": "api_spec"}],
    }
    delta_obligation = {
        "obligation_id": "obl-runtime",
        "risk_family": "authorization",
        "compile_status": "COMPILED",
        "required_operations": ["op-runtime"],
        "required_actors": [],
        "relation_refs": [],
        "source_refs": [{"source_id": "request-200"}],
    }

    monkeypatch.setattr(
        expansion,
        "build_behavior_ir_from_knowledge_asset",
        lambda *args, **kwargs: rebuilt_ir,
    )
    monkeypatch.setattr(
        expansion,
        "compile_obligations_from_behavior_ir",
        lambda behavior_ir: {
            "obligations": [blocked_obligation, delta_obligation],
        },
    )

    def fake_compile(obligations, **kwargs):
        experiments = [
            {
                "obligation_id": row["obligation_id"],
                "experiment_id": f"exp-{row['obligation_id']}",
                "compile_receipt": {"status": "COMPILED"},
                "observers": [
                    {"observer_id": "observer-runtime", "adapter": "http_api"}
                ],
                "source_refs": list(row.get("source_refs") or []),
            }
            for row in obligations
        ]
        return {"experiments": experiments, "blocked_experiments": []}

    monkeypatch.setattr(expansion, "compile_experiments", fake_compile)
    monkeypatch.setattr(
        expansion,
        "attach_fixture_dag_to_experiments",
        lambda pack, **kwargs: pack,
    )

    result = expansion.expand_behavior_ir_from_runtime_observations(
        initial_behavior_ir=initial_ir,
        existing_obligation_ids={"obl-blocked"},
        recompile_obligation_ids={"obl-blocked"},
        knowledge_asset={"asset_id": "asset-1"},
        documented_operations=_operations(),
        observation_receipts=[_observation(200)],
        project_id="project",
        source_snapshot_hash="source-hash",
        runtime_actors=[],
        environment_type="test",
        policy_version="policy-1",
        budget=10,
        planning_round=2,
    )

    assert result["status"] == "EXPANDED"
    assert [row["obligation_id"] for row in result["recompile_obligations"]] == [
        "obl-blocked"
    ]
    assert [row["obligation_id"] for row in result["delta_obligations"]] == [
        "obl-runtime"
    ]
    assert set(result["by_obligation"]) == {"obl-blocked", "obl-runtime"}
    assert result["round_receipt"]["recompiled_obligation_count"] == 1
    assert result["round_receipt"]["recompiled_obligation_ids"] == [
        "obl-blocked"
    ]


def test_indeterminate_observation_stagnates_without_recompiling(monkeypatch) -> None:
    import ai_test_asset_center.adaptive_behavior_ir_expansion as expansion

    monkeypatch.setattr(
        expansion,
        "build_behavior_ir_from_knowledge_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not rebuild")),
    )
    result = expansion.expand_behavior_ir_from_runtime_observations(
        initial_behavior_ir={"model_id": "model-round-1", "operations": []},
        existing_obligation_ids=set(),
        knowledge_asset={"asset_id": "asset-1"},
        documented_operations=_operations(),
        observation_receipts=[_observation(401)],
        project_id="project",
        source_snapshot_hash="source-hash",
        runtime_actors=[],
        environment_type="test",
        policy_version="policy-1",
        budget=10,
        planning_round=2,
    )

    assert result["status"] == "STAGNATED"
    assert result["delta_obligations"] == []
    assert result["selected_rows"] == []
    assert result["round_receipt"]["stop_reason"] == "no_new_runtime_operations"
