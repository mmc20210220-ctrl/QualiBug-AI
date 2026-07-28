from __future__ import annotations

import pytest

from ai_test_asset_center import private_pilot_scan_context_contract as context_contract
from ai_test_asset_center.stability_scan_context_bridge import (
    install_stability_scan_context_bridge,
    restore_stability_scan_context_bridge,
)


def test_bridge_preserves_explicit_stability_contracts() -> None:
    restore_stability_scan_context_bridge()
    install_stability_scan_context_bridge()
    contract = {
        "contract_id": "orders-stability",
        "source_refs": [{"source_id": "slo", "locator": "REL-1"}],
        "operation_id": "list_orders",
        "actor_role": "public",
        "sample_count": 5,
        "max_failed_samples": 0,
        "max_retried_samples": 0,
        "expected_status_class": 2,
    }

    context = context_contract.build_campaign_context_from_scan_body({
        "stability_formal_contracts": [contract],
    })

    assert context["stability_formal_contracts"] == [contract]
    assert context["stability_formal_contracts"][0] is not contract


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ({}, "stability_formal_contracts_not_list"),
        (None, "stability_formal_contracts_not_list"),
        (["reliability"], "stability_formal_contract_not_object:0"),
    ],
)
def test_bridge_rejects_invalid_contract_transport(value: object, reason: str) -> None:
    restore_stability_scan_context_bridge()
    install_stability_scan_context_bridge()
    with pytest.raises(ValueError, match=rf"^{reason}$"):
        context_contract.build_campaign_context_from_scan_body({
            "stability_formal_contracts": value,
        })
