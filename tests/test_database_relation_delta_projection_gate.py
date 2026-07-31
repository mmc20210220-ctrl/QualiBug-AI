from __future__ import annotations

from ai_test_asset_center.database_relation_delta_projection_gate import (
    project_database_relation_delta_assertions,
)


def _delta_side(*, field: str = "balance") -> dict:
    return {
        "node_type": "delta",
        "operand": {
            "node_type": "field_ref",
            "entity": "accounts",
            "field": field,
        },
    }


def _pack(expression: dict) -> dict:
    return {
        "experiments": [
            {
                "experiment_id": "experiment:malformed-delta",
                "compile_receipt": {"status": "COMPILED"},
                "assertions": [
                    {
                        "assertion_id": "assert:malformed-delta",
                        "kind": "conservation",
                        "structured_expression": expression,
                    }
                ],
                "observers": [
                    {"observer_id": "before_state", "adapter": "http_api"},
                    {"observer_id": "after_state", "adapter": "http_api"},
                ],
            }
        ],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def test_explicit_delta_expression_without_operator_is_visible_gap() -> None:
    result = project_database_relation_delta_assertions(
        _pack(
            {
                "type": "delta_conservation",
                "left": _delta_side(),
                "right": _delta_side(field="amount"),
            }
        )
    )

    experiment = result["experiments"][0]
    assert experiment["database_relation_delta_projection_status"] == "INCOMPLETE"
    assert experiment["database_relation_delta_projection_gaps"] == [
        {
            "assertion_id": "assert:malformed-delta",
            "reason_code": "DATABASE_RELATION_DELTA_COMPARISON_OPERATOR_MISSING",
            "explicit_relation_delta_expression": True,
            "automatic_expression_repair_allowed": False,
            "automatic_sign_inference_allowed": False,
        }
    ]
    summary = result["database_relation_delta_experiment_projection"]
    assert summary["malformed_explicit_expression_count"] == 1
    assert summary["automatic_expression_repair_count"] == 0


def test_explicit_delta_expression_requires_both_delta_operands() -> None:
    result = project_database_relation_delta_assertions(
        _pack(
            {
                "type": "delta_conservation",
                "operator": "EQ",
                "left": _delta_side(),
                "right": {
                    "node_type": "field_ref",
                    "entity": "ledger_entries",
                    "field": "amount",
                },
            }
        )
    )

    gap = result["experiments"][0][
        "database_relation_delta_projection_gaps"
    ][0]
    assert gap["reason_code"] == (
        "DATABASE_RELATION_DELTA_BOTH_DELTA_OPERANDS_REQUIRED"
    )


def test_unsupported_operator_is_not_repaired() -> None:
    result = project_database_relation_delta_assertions(
        _pack(
            {
                "type": "delta_conservation",
                "operator": "APPROXIMATELY_EQUALS",
                "left": _delta_side(),
                "right": _delta_side(field="amount"),
            }
        )
    )

    experiment = result["experiments"][0]
    gap = experiment["database_relation_delta_projection_gaps"][0]
    assert gap["reason_code"] == (
        "DATABASE_RELATION_DELTA_COMPARISON_OPERATOR_UNSUPPORTED"
    )
    assert experiment["assertions"][0]["kind"] == "conservation"
