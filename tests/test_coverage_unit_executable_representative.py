from __future__ import annotations

from ai_test_asset_center.adaptive_discovery_planner import (
    plan_coverage_unit_round,
    promote_executable_coverage_unit_representatives,
)


def _obligation(oid: str, *, confidence: float = 0.5, executable=None) -> dict:
    row = {
        "obligation_id": oid,
        "risk_family": "authorization",
        "confidence": confidence,
        "required_operations": ["op_read"],
        "required_actors": ["actor_user"],
        "subject_refs": ["op_read", "actor_user"],
        "property": {
            "template": "permitted_operation_invocation",
            "operation_ref": "op_read",
            "actor_ref": "actor_user",
        },
    }
    if executable is not None:
        row["pre_transport_executable"] = executable
    return row


def _experiment(oid: str, *, compiled: bool = True, executable=None) -> dict:
    row = {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED" if compiled else "BLOCKED"},
        "treatment_plan": [
            {
                "method": "GET",
                "path": "/api/orders/1",
                "operation_ref": "op_read",
            }
        ],
    }
    if executable is not None:
        row["pre_transport_executable"] = executable
    return row


def _unit() -> dict:
    return {
        "coverage_unit_id": "cunit_orders",
        "canonical_obligation_key": "orders",
        "representative_obligation_id": "obl_rep",
        "obligation_ids": ["obl_rep", "obl_variant_a", "obl_variant_b"],
        "variant_count": 3,
        "actor_variants": ["actor_user"],
    }


def _behavior_ir() -> dict:
    return {
        "operations": [
            {"id": "op_read", "method": "GET", "path": "/api/orders/{id}"}
        ]
    }


def test_promotes_compiled_transport_executable_variant_when_rep_is_blocked() -> None:
    obligations = {
        "obl_rep": _obligation("obl_rep", confidence=0.9, executable=False),
        "obl_variant_a": _obligation("obl_variant_a", confidence=0.7, executable=True),
        "obl_variant_b": _obligation("obl_variant_b", confidence=0.6, executable=True),
    }
    experiments = {
        oid: _experiment(oid, executable=(oid != "obl_rep"))
        for oid in obligations
    }

    units, promotions = promote_executable_coverage_unit_representatives(
        [_unit()],
        obligations_by_id=obligations,
        experiments_by_obligation=experiments,
    )

    assert units[0]["representative_obligation_id"] == "obl_variant_a"
    assert promotions == [{
        "coverage_unit_id": "cunit_orders",
        "from_obligation_id": "obl_rep",
        "to_obligation_id": "obl_variant_a",
        "reason": "representative_pre_transport_not_executable",
    }]


def test_original_executable_representative_is_never_replaced() -> None:
    obligations = {
        "obl_rep": _obligation("obl_rep", confidence=0.1, executable=True),
        "obl_variant_a": _obligation("obl_variant_a", confidence=0.9, executable=True),
        "obl_variant_b": _obligation("obl_variant_b", confidence=0.8, executable=True),
    }
    experiments = {oid: _experiment(oid, executable=True) for oid in obligations}

    units, promotions = promote_executable_coverage_unit_representatives(
        [_unit()],
        obligations_by_id=obligations,
        experiments_by_obligation=experiments,
    )

    assert units[0]["representative_obligation_id"] == "obl_rep"
    assert promotions == []


def test_no_executable_variant_preserves_fail_closed_representative() -> None:
    obligations = {
        "obl_rep": _obligation("obl_rep", executable=False),
        "obl_variant_a": _obligation("obl_variant_a", executable=False),
        "obl_variant_b": _obligation("obl_variant_b", executable=False),
    }
    experiments = {oid: _experiment(oid, executable=False) for oid in obligations}

    units, promotions = promote_executable_coverage_unit_representatives(
        [_unit()],
        obligations_by_id=obligations,
        experiments_by_obligation=experiments,
    )

    assert units[0]["representative_obligation_id"] == "obl_rep"
    assert promotions == []


def test_blocked_compile_variant_cannot_be_promoted() -> None:
    obligations = {
        "obl_rep": _obligation("obl_rep", executable=False),
        "obl_variant_a": _obligation("obl_variant_a", confidence=0.9, executable=True),
        "obl_variant_b": _obligation("obl_variant_b", confidence=0.8, executable=True),
    }
    experiments = {
        "obl_rep": _experiment("obl_rep", executable=False),
        "obl_variant_a": _experiment("obl_variant_a", compiled=False, executable=True),
        "obl_variant_b": _experiment("obl_variant_b", compiled=True, executable=True),
    }

    units, _ = promote_executable_coverage_unit_representatives(
        [_unit()],
        obligations_by_id=obligations,
        experiments_by_obligation=experiments,
    )

    assert units[0]["representative_obligation_id"] == "obl_variant_b"


def test_planner_selects_unit_through_promoted_executable_variant() -> None:
    obligations = {
        "obl_rep": _obligation("obl_rep", confidence=0.9, executable=False),
        "obl_variant_a": _obligation("obl_variant_a", confidence=0.7, executable=True),
        "obl_variant_b": _obligation("obl_variant_b", confidence=0.6, executable=True),
    }
    experiments = {
        oid: _experiment(oid, executable=(oid != "obl_rep"))
        for oid in obligations
    }

    plan = plan_coverage_unit_round(
        [_unit()],
        obligations_by_id=obligations,
        experiments_by_obligation=experiments,
        behavior_ir=_behavior_ir(),
        budget=1,
        type_minimum_guarantees={},
    )

    assert plan["selected_count"] == 1
    assert plan["selected"][0]["obligation_id"] == "obl_variant_a"
    assert plan["selected_units"][0]["obligation_id"] == "obl_variant_a"
    receipt = plan["executable_representative_promotion"]
    assert receipt["promotion_count"] == 1
    assert receipt["promotions"][0]["to_obligation_id"] == "obl_variant_a"
