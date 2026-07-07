from ai_test_asset_center.oracle_engine import RequiredFieldOracle


def test_required_field_oracle_skips_aggregate_collection_response():
    oracle = RequiredFieldOracle()
    scenario = {"category": "source_observation"}
    trace = {
        "steps": [
            {
                "expected_status": 200,
                "response": {
                    "status_code": 200,
                    "body": {
                        "from": "1970-01-01",
                        "to": "2999-01-01",
                        "rows": [
                            {"status": "CANCELLED", "order_count": 2, "amount": "13798.00"},
                        ],
                    },
                },
            }
        ]
    }

    result = oracle.evaluate(scenario, trace, None)

    assert result.passed is True


def test_required_field_oracle_still_flags_explicit_null_required_field():
    oracle = RequiredFieldOracle()
    scenario = {"category": "source_observation"}
    trace = {
        "steps": [
            {
                "expected_status": 200,
                "response": {
                    "status_code": 200,
                    "body": {
                        "id": None,
                        "status": "PAID",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                },
            }
        ]
    }

    result = oracle.evaluate(scenario, trace, None)

    assert result.passed is False
    assert result.violated_rule == "null_required"
    assert result.actual == "id=null"
