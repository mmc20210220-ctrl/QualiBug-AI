from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection,
)


def _asset(*, source_hash: str, source_version_id: str, required: bool = True):
    return {
        "source_inventory": [
            {
                "source_id": "schema.sql",
                "status": "active",
                "content_hash": source_hash,
                "source_version_id": source_version_id,
            }
        ],
        "field_dictionary": (
            [
                {
                    "field_id": "field:orders.order_no",
                    "field": "order_no",
                    "table": "orders",
                    "table_id": "table:orders",
                    "source_id": "schema.sql",
                    "nullable": False,
                }
            ]
            if required
            else []
        ),
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


def _implicit_rules(asset):
    return [
        row
        for row in asset["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]


def test_changed_source_version_stales_rule_that_is_not_rederived():
    first = enrich_asset_with_governed_implicit_rule_projection(
        _asset(source_hash="hash-v1", source_version_id="srcv-v1")
    )
    active = _implicit_rules(first)
    assert len(active) == 1
    rule_id = active[0]["rule_id"]
    assert active[0]["source_version_refs"] == [
        {
            "source_id": "schema.sql",
            "source_hash": "hash-v1",
            "source_version_id": "srcv-v1",
        }
    ]

    changed = deepcopy(first)
    changed["source_inventory"] = [
        {
            "source_id": "schema.sql",
            "status": "active",
            "content_hash": "hash-v2",
            "source_version_id": "srcv-v2",
        }
    ]
    changed["field_dictionary"] = []
    second = enrich_asset_with_governed_implicit_rule_projection(changed)

    assert _implicit_rules(second) == []
    lifecycle = second["implicit_rule_lifecycle_ledger"]
    stale = next(row for row in lifecycle["items"] if row["rule_id"] == rule_id)
    assert stale["status"] == "STALE"
    assert stale["execution_allowed"] is False
    assert stale["reason"] == "SOURCE_VERSION_CHANGED_RULE_NOT_REDERIVED"
    assert stale["rule_snapshot"]["rule_id"] == rule_id
    assert stale["current_source_version_refs"] == [
        {
            "source_id": "schema.sql",
            "source_hash": "hash-v2",
            "source_version_id": "srcv-v2",
        }
    ]
    assert any(
        row.get("kind") == "IMPLICIT_RULE_STALE"
        and row.get("rule_id") == rule_id
        for row in second["coverage_gaps"]
    )
    assert second["implicit_rule_projection_gate"]["stale_rule_count"] == 1
    assert second["implicit_rule_projection_gate"][
        "stale_rule_execution_allowed"
    ] is False


def test_stale_lifecycle_reprojection_is_idempotent():
    first = enrich_asset_with_governed_implicit_rule_projection(
        _asset(source_hash="hash-v1", source_version_id="srcv-v1")
    )
    changed = deepcopy(first)
    changed["source_inventory"] = [
        {
            "source_id": "schema.sql",
            "status": "active",
            "content_hash": "hash-v2",
            "source_version_id": "srcv-v2",
        }
    ]
    changed["field_dictionary"] = []
    once = enrich_asset_with_governed_implicit_rule_projection(changed)
    event_ids_once = [
        row["event_id"] for row in once["implicit_rule_lifecycle_ledger"]["events"]
    ]

    twice = enrich_asset_with_governed_implicit_rule_projection(deepcopy(once))
    event_ids_twice = [
        row["event_id"] for row in twice["implicit_rule_lifecycle_ledger"]["events"]
    ]

    assert event_ids_twice == event_ids_once
    assert twice["implicit_rule_lifecycle_ledger"]["stale_rule_count"] == 1
    assert _implicit_rules(twice) == []


def test_deactivated_source_is_distinct_from_changed_version():
    first = enrich_asset_with_governed_implicit_rule_projection(
        _asset(source_hash="hash-v1", source_version_id="srcv-v1")
    )
    removed = deepcopy(first)
    removed["source_inventory"] = []
    removed["field_dictionary"] = []

    result = enrich_asset_with_governed_implicit_rule_projection(removed)
    stale = next(
        row
        for row in result["implicit_rule_lifecycle_ledger"]["items"]
        if row["status"] == "STALE"
    )
    assert stale["reason"] == "SOURCE_DEACTIVATED"
