from __future__ import annotations

import pytest

from ai_test_asset_center import private_pilot_scan_context_contract as context_contract
from ai_test_asset_center.performance_scan_context_bridge import (
    install_performance_scan_context_bridge,
    restore_performance_scan_context_bridge,
)


def test_bridge_preserves_explicit_performance_contracts() -> None:
    restore_performance_scan_context_bridge()
    install_performance_scan_context_bridge()
    contract = {
        "contract_id": "orders-p95",
        "source_refs": [{"source_id": "slo", "locator": "SLO-1"}],
        "operation_id": "list_orders",
        "actor_role": "public",
        "sample_count": 5,
        "warmup_count": 1,
        "percentile": "p95",
        "max_latency_ms": 300,
        "max_error_rate": 0,
        "expected_status_class": 2,
    }

    context = context_contract.build_campaign_context_from_scan_body({
        "performance_formal_contracts": [contract],
    })

    assert context["performance_formal_contracts"] == [contract]
    assert context["performance_formal_contracts"][0] is not contract


def test_bridge_preserves_explicit_empty_contract_list() -> None:
    restore_performance_scan_context_bridge()
    install_performance_scan_context_bridge()

    context = context_contract.build_campaign_context_from_scan_body({
        "performance_formal_contracts": [],
    })

    assert context["performance_formal_contracts"] == []


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ({}, "performance_formal_contracts_not_list"),
        (None, "performance_formal_contracts_not_list"),
        (["slo"], "performance_formal_contract_not_object:0"),
        ([{}, 1], "performance_formal_contract_not_object:1"),
    ],
)
def test_bridge_rejects_invalid_transport_shape(value: object, reason: str) -> None:
    restore_performance_scan_context_bridge()
    install_performance_scan_context_bridge()

    with pytest.raises(ValueError, match=rf"^{reason}$"):
        context_contract.build_campaign_context_from_scan_body({
            "performance_formal_contracts": value,
        })
