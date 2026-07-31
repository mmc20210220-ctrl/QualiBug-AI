from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.probe_policy import (
    build_gated_probes,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection,
)


def _asset():
    return {
        "source_inventory": [
            {
                "source_id": "schema.sql",
                "status": "active",
                "content_hash": "schema-v1",
                "source_version_id": "srcv-schema-v1",
            }
        ],
        "field_dictionary": [
            {
                "field_id": "field:orders.order_no",
                "field": "order_no",
                "table": "orders",
                "table_id": "table:orders",
                "source_id": "schema.sql",
                "nullable": False,
            }
        ],
        "data_tables": [],
        "interfaces": [],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [],
    }


def test_zero_probe_budget_still_persists_implicit_rule_governance():
    first = enrich_asset_with_governed_implicit_rule_projection(_asset())
    rule_id = next(
        row["rule_id"]
        for row in first["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    )
    changed = deepcopy(first)
    changed["source_inventory"] = [
        {
            "source_id": "schema.sql",
            "status": "active",
            "content_hash": "schema-v2",
            "source_version_id": "srcv-schema-v2",
        }
    ]
    changed["field_dictionary"] = []

    def must_not_compile(_asset, _limit):
        raise AssertionError("zero probe budget must not invoke the compiler")

    probes = build_gated_probes(changed, 0, compiler=must_not_compile)

    assert probes == []
    assert not any(row.get("rule_id") == rule_id for row in changed["rule_library"])
    lifecycle = next(
        row
        for row in changed["implicit_rule_lifecycle_ledger"]["items"]
        if row["rule_id"] == rule_id
    )
    assert lifecycle["status"] == "STALE"
    assert lifecycle["execution_allowed"] is False
    assert changed["governance"][
        "implicit_rule_source_version_lifecycle_is_explicit"
    ] is True
