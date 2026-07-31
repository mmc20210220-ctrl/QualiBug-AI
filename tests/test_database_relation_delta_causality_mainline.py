from __future__ import annotations

from ai_test_asset_center.database_relation_delta_causality_mainline import (
    project_database_relation_delta_causality,
)
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from tests.test_database_relation_delta_causality_projection import (
    _assertion,
    _experiment,
    _pack,
)


def test_causal_projection_resolves_field_oracle_runtime_contract() -> None:
    result = project_database_relation_delta_causality(_pack(_experiment()))

    experiment = result["experiments"][0]
    contract = experiment["field_oracle_runtime_contract"]
    assert contract["status"] == "RESOLVED"
    assert contract["assertion_kind"] == ASSERTION_KIND
    assert contract["operation_causality_bound"] is True
    assert contract["operation_causality_observer_id"] == (
        "operation_causality_transport"
    )
    assert contract["causal_attribution_mode"] == (
        "EXACT_REQUEST_CORRELATION"
    )
    assert contract["timestamp_window_attribution_allowed"] is False
    receipt = experiment["compile_receipt"]
    assert receipt["operation_causality_observer_required"] is True
    assert receipt["operation_causality_runtime_contract_resolved"] is True


def test_sensitive_operation_correlation_field_blocks_at_compile_time() -> None:
    assertion = _assertion(
        child_database_field_id="field:ledger_entries:password",
        child_database_field_name="password",
    )
    experiment = _experiment(assertion)
    for draft in experiment["database_relation_observer_execution_drafts"]:
        draft["database_relation_observer_contract"][
            "allowed_child_fields"
        ].append(
            {
                "database_field_id": "field:ledger_entries:password",
                "database_field_name": "password",
            }
        )

    result = project_database_relation_delta_causality(_pack(experiment))

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_relation_causality_detail"
    ]
    assert detail["causality_reason_code"] == (
        "DATABASE_RELATION_CAUSAL_SENSITIVE_FIELD_FORBIDDEN"
    )
    assert detail["raw_causal_value_allowed"] is False
