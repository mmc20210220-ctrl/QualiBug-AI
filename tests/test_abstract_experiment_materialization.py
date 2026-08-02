from __future__ import annotations

from ai_test_asset_center.abstract_experiment import (
    ABSTRACT_SCHEMA,
    MATERIALIZATION_SCHEMA,
    attach_passthrough_materialization,
    is_capability_gap_reason,
    promote_blocked_to_abstract,
)
from ai_test_asset_center.experiment_compiler_obligation_core import blocked_experiment
from ai_test_asset_center.experiment_runtime_materialization import (
    materialize_and_recompile_abstract_pack,
    _resolve_planning_materialization,
)


def test_capability_gap_reasons() -> None:
    assert is_capability_gap_reason("BLOCKED_MISSING_FIXTURE")
    assert is_capability_gap_reason("BLOCKED_MISSING_ACTOR")
    assert not is_capability_gap_reason("BLOCKED_UNSUPPORTED_ADAPTER")


def test_promote_blocked_retains_abstract_intent() -> None:
    blocked = blocked_experiment("obl_1", "BLOCKED_MISSING_FIXTURE", "cart_item")
    obligation = {
        "obligation_id": "obl_1",
        "risk_family": "authorization",
        "required_operations": ["op_create"],
        "required_actors": ["actor_owner", "actor_other"],
        "required_fixtures": ["cart_item"],
        "required_observers": ["authorization_comparison"],
        "property": {
            "control_actor_ref": "actor_owner",
            "treatment_actor_ref": "actor_other",
        },
        "fact_refs": ["fact:draft-visibility"],
    }
    abstract = promote_blocked_to_abstract(blocked, obligation)
    assert abstract["compile_receipt"]["status"] == "ABSTRACT"
    assert abstract["compile_receipt"]["abstract_retained"] is True
    assert abstract["abstract_experiment"]["schema_version"] == ABSTRACT_SCHEMA
    assert abstract["abstract_experiment"]["control_arm"]["actor_ref"] == "actor_owner"
    assert abstract["abstract_experiment"]["treatment_arm"]["actor_ref"] == "actor_other"
    assert "cart_item" in abstract["abstract_experiment"]["required_capabilities"]["fixtures"]
    assert abstract["abstract_experiment"]["fact_refs"] == ["fact:draft-visibility"]


def test_passthrough_materialization_for_direct_compile() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "compile_receipt": {"status": "COMPILED"},
        "observers": [{"observer_id": "http_response"}],
        "cleanup_plan": [],
    }
    result = attach_passthrough_materialization(experiment)
    assert result["materialization_receipt"]["schema_version"] == MATERIALIZATION_SCHEMA
    assert result["materialization_receipt"]["status"] == "SKIPPED_DIRECT_COMPILE"
    assert result["experiment_phase"] == "CONCRETE"


def test_fixture_gap_materializes_when_create_op_exists() -> None:
    obligation = {
        "obligation_id": "obl_fix",
        "risk_family": "validation",
        "required_operations": ["op_read"],
        "required_actors": ["actor_a"],
        "required_fixtures": ["product"],
        "required_observers": ["http_response"],
        "property": {},
    }
    abstract = promote_blocked_to_abstract(
        blocked_experiment("obl_fix", "BLOCKED_MISSING_FIXTURE", "product"),
        obligation,
    )
    behavior_ir = {
        "operations": [
            {
                "id": "op_create_product",
                "method": "POST",
                "path": "/api/products",
                "name": "create product",
            },
            {"id": "op_read", "method": "GET", "path": "/api/products/{id}"},
        ],
        "actors": [
            {
                "id": "actor_a",
                "role": "member",
                "credential_secret_ref": "secret:actor_a",
            }
        ],
    }
    resolution = _resolve_planning_materialization(
        obligation=obligation,
        abstract_experiment=abstract,
        behavior_ir=behavior_ir,
    )
    assert resolution["can_recompile"] is True
    assert resolution["materialization_receipt"]["status"] == "MATERIALIZED"
    assert "product" in resolution["materialization_receipt"]["fixture_bindings"]
    assert resolution["binding_plan_extras"]


def test_materialize_pack_keeps_abstract_when_unresolved() -> None:
    obligation = {
        "obligation_id": "obl_x",
        "risk_family": "validation",
        "required_operations": ["op_missing"],
        "required_actors": [],
        "required_fixtures": ["no_such_fixture"],
        "required_observers": ["http_response"],
        "property": {},
    }
    abstract = promote_blocked_to_abstract(
        blocked_experiment("obl_x", "BLOCKED_MISSING_FIXTURE", "no_such_fixture"),
        obligation,
    )
    pack = {
        "experiments": [],
        "blocked_experiments": [],
        "abstract_experiments": [abstract],
    }

    def _compile_one(obl, **kwargs):
        return blocked_experiment(
            obl["obligation_id"], "BLOCKED_MISSING_FIXTURE", "still_missing"
        )

    result = materialize_and_recompile_abstract_pack(
        pack,
        obligations=[obligation],
        behavior_ir={"operations": [], "actors": []},
        compile_one=_compile_one,
    )
    assert result["compiled_count"] == 0
    assert result["abstract_count"] == 1
    assert result["abstract_experiments"][0]["compile_receipt"]["status"] == "ABSTRACT"
    assert (
        result["abstract_experiments"][0]["materialization_receipt"]["status"]
        == "NOT_MATERIALIZED"
    )


def test_state_and_cleanup_front_loaded_in_materialization() -> None:
    obligation = {
        "obligation_id": "obl_state",
        "risk_family": "state",
        "required_operations": ["op_transition"],
        "required_actors": ["actor_a"],
        "required_fixtures": [],
        "required_observers": ["before_state", "after_state"],
        "cleanup_requirement": {"required": True},
        "property": {"pre_state": "draft", "control_actor_ref": "actor_a"},
    }
    abstract = promote_blocked_to_abstract(
        blocked_experiment(
            "obl_state", "BLOCKED_PRECONDITION_UNREACHABLE", "draft"
        ),
        obligation,
    )
    behavior_ir = {
        "operations": [
            {"id": "op_transition", "method": "POST", "path": "/api/items/publish"},
            {"id": "op_delete", "method": "DELETE", "path": "/api/items/{id}"},
        ],
        "actors": [
            {
                "id": "actor_a",
                "role": "member",
                "credential_secret_ref": "secret:actor_a",
            }
        ],
    }
    resolution = _resolve_planning_materialization(
        obligation=obligation,
        abstract_experiment=abstract,
        behavior_ir=behavior_ir,
        planning_context={"available_adapters": {"http_api"}},
    )
    receipt = resolution["materialization_receipt"]
    assert receipt["state_establishment_steps"]
    assert receipt["state_establishment_steps"][0]["established_before_concrete_compile"]
    assert receipt["cleanup_plan"].get("authority_resolved") is True
    assert receipt["cleanup_plan"].get("established_before_concrete_compile") is True
    assert receipt["observer_bindings"]["before_state"]["established_before_concrete_compile"]


def test_materialize_pack_recompiles_when_fixture_resolved() -> None:
    obligation = {
        "obligation_id": "obl_ok",
        "risk_family": "validation",
        "required_operations": ["op_read"],
        "required_actors": ["actor_a"],
        "required_fixtures": ["product"],
        "required_observers": ["http_response"],
        "property": {},
    }
    abstract = promote_blocked_to_abstract(
        blocked_experiment("obl_ok", "BLOCKED_MISSING_FIXTURE", "product"),
        obligation,
    )
    behavior_ir = {
        "operations": [
            {
                "id": "op_create_product",
                "method": "POST",
                "path": "/api/products",
                "name": "create product",
            }
        ],
        "actors": [
            {
                "id": "actor_a",
                "role": "member",
                "credential_secret_ref": "secret:actor_a",
            }
        ],
    }

    def _compile_one(obl, **kwargs):
        extras = (obl.get("_planning_materialization") or {}).get("binding_plan_extras") or []
        assert extras, "expected planning materialization bindings"
        return {
            "experiment_id": "exp_ok",
            "obligation_id": "obl_ok",
            "compile_receipt": {"status": "COMPILED"},
            "observers": [{"observer_id": "http_response"}],
            "cleanup_plan": [],
            "binding_plan": list(extras),
        }

    result = materialize_and_recompile_abstract_pack(
        {
            "experiments": [],
            "blocked_experiments": [],
            "abstract_experiments": [abstract],
        },
        obligations=[obligation],
        behavior_ir=behavior_ir,
        compile_one=_compile_one,
    )
    assert result["compiled_count"] == 1
    assert result["abstract_count"] == 0
    concrete = result["experiments"][0]
    assert concrete["compile_receipt"]["status"] == "COMPILED"
    assert concrete["materialization_receipt"]["status"] == "MATERIALIZED"
    assert concrete["experiment_phase"] == "CONCRETE"
    assert concrete["abstract_experiment"]["schema_version"] == ABSTRACT_SCHEMA
