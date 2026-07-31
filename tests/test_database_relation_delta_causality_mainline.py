from __future__ import annotations

from ai_test_asset_center.database_relation_delta_causality_mainline import (
    project_database_relation_delta_causality,
)
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from tests.test_database_relation_delta_causality_projection import (
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
