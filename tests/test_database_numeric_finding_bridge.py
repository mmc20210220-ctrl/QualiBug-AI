from __future__ import annotations

from ai_test_asset_center.database_numeric_finding_bridge import (
    enrich_database_numeric_finding,
)
from ai_test_asset_center.database_numeric_oracle import (
    DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
    DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
)


def _phase(phase: str, value: str) -> dict:
    return {
        "phase": phase,
        "draft_id": f"draft:accounts:{phase.lower()}",
        "phase_receipt_id": f"readback-{phase.lower()}",
        "source_observer_id": "approved_database_readback",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "database_table_ref": "table:accounts",
        "database_table_name": "accounts",
        "match_status": "MATCHED_ONE",
        "row_count": 1,
        "row_fingerprint": f"row-{phase.lower()}",
        "identity_key": ["id"],
        "identity_parameter_fingerprints": ["identity-a-1"],
        "field_name": "balance",
        "field_value": value,
        "raw_sql": "SELECT secret FROM accounts",
        "dsn": "postgres://secret",
    }


def _assertion(kind: str = DATABASE_NUMERIC_DELTA_ASSERTION_KIND) -> dict:
    return {
        "assertion_id": "assert:balance",
        "kind": kind,
        "status": "VIOLATION",
        "reason_code": (
            "DATABASE_NUMERIC_DELTA_MISMATCH"
            if kind == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
            else "DATABASE_NUMERIC_CONSERVATION_VIOLATED"
        ),
        "expected": {
            "numeric_policy": (
                "FIELD_DELTA"
                if kind == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
                else "UNCHANGED_WEIGHTED_SUM"
            )
        },
        "actual": {
            "numeric_policy": (
                "FIELD_DELTA"
                if kind == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
                else "UNCHANGED_WEIGHTED_SUM"
            ),
            "same_execution": True,
            "single_contract_scope": True,
            "before_weighted_sum": "100",
            "after_weighted_sum": "95",
            "difference": "-5",
            "observer_performed_oracle_verdict": False,
            "term_results": [
                {
                    "term_id": "term:balance",
                    "database_observer_contract_ref": "observer:accounts",
                    "database_table_ref": "table:accounts",
                    "database_table_name": "accounts",
                    "database_field_id": "field:accounts:balance",
                    "database_field_name": "balance",
                    "field_binding_id": "binding:accounts:balance",
                    "observed_before": "100.00",
                    "observed_after": "95.00",
                    "observed_before_decimal": "100",
                    "observed_after_decimal": "95",
                    "actual_delta": "-5",
                    "expected_delta": "-10",
                    "tolerance": "0",
                    "term_passed": False,
                    "lineage_match": True,
                    "identity_match": True,
                    "before_snapshot": _phase("BEFORE", "100.00"),
                    "after_snapshot": _phase("AFTER", "95.00"),
                    "observer_performed_oracle_verdict": False,
                }
            ],
        },
    }


def _result(assertion: dict) -> dict:
    return {
        "status": "VIOLATION",
        "finding": {
            "finding_id": "finding-1",
            "category": "business_integrity",
            "gate_passed": False,
            "customer_delivery_status": "candidate",
            "final_review_status": "PENDING_DELIVERY_GATE",
            "evidence": {"assertion": assertion, "api_response": {"status": 200}},
            "raw_evidence": {
                "db_snapshot": {"body": {"balance": "95.00"}},
                "response": {"status": 200},
            },
            "evidence_quality": {},
        },
    }


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def test_numeric_delta_finding_replaces_legacy_http_db_snapshot() -> None:
    enriched = enrich_database_numeric_finding(_result(_assertion()))

    finding = enriched["finding"]
    assert finding["category"] == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
    evidence = finding["database_numeric_evidence"]
    assert evidence["numeric_policy"] == "FIELD_DELTA"
    assert evidence["term_results"][0]["actual_delta"] == "-5"
    assert evidence["term_results"][0]["before_snapshot"]["phase_receipt_id"] == (
        "readback-before"
    )
    assert finding["raw_evidence"]["legacy_http_body_used_as_db_snapshot"] is False
    assert finding["raw_evidence"]["response"] == {"status": 200}
    assert finding["gate_passed"] is False
    assert finding["customer_delivery_status"] == "candidate"

    forbidden = {
        "raw_sql",
        "sql",
        "statement",
        "dsn",
        "password",
        "secret",
        "credential",
        "connection_string",
        "predicate_values",
    }
    assert _walk_keys(finding["database_numeric_evidence"]).isdisjoint(forbidden)


def test_conservation_finding_keeps_exact_sum_evidence() -> None:
    enriched = enrich_database_numeric_finding(
        _result(_assertion(DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND))
    )

    evidence = enriched["finding"]["database_numeric_evidence"]
    assert evidence["numeric_policy"] == "UNCHANGED_WEIGHTED_SUM"
    assert evidence["before_weighted_sum"] == "100"
    assert evidence["after_weighted_sum"] == "95"
    assert evidence["difference"] == "-5"
    assert evidence["oracle_authority"] == "ContractOracle"
    assert evidence["database_observer_authority"] == "FACT_ONLY"
