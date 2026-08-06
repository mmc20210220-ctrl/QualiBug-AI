from ai_test_asset_center.experiment_outcome_finalizer import (
    _attach_source_rule_identity,
    _source_rule_identity,
)


def _experiment(*, statement="通行证必须在有效期内", with_source=True):
    return {
        "source_refs": (
            [
                {
                    "kind": "requirement",
                    "locator": "prd.md#pass-validity",
                    "source_id": "prd",
                }
            ]
            if with_source
            else []
        ),
        "assertions": [
            {
                "assertion_id": "assert_validation",
                "kind": "validation_rejection",
                "property": {
                    "invariant_ref": "bir_pass_validity",
                    "expression": {
                        "kind": "business_rule",
                        "operator": "must_hold",
                        "raw": statement,
                    },
                    "field_rule_binding": {
                        "rule_id": "bir_pass_validity",
                        "typed_expression": {
                            "kind": "business_rule",
                            "operator": "must_hold",
                            "raw": statement,
                        },
                    },
                },
            }
        ],
    }


def _finding(outcome_ref="outcome-validity"):
    return {
        "title": "[ContractOracle] validation_rejection: member POST /api/passes/validate",
        "description": "typed assertion violated",
        "category": "validation_rejection",
        "outcome_ref": outcome_ref,
        "evidence": {
            "request": "POST /api/passes/validate",
            "response": "HTTP 200",
        },
    }


def test_compiled_source_rule_identity_is_extracted_without_runtime_guessing():
    identity = _source_rule_identity(_experiment())

    assert identity == {
        "source_rule_ref": "bir_pass_validity",
        "source_rule_statement": "通行证必须在有效期内",
        "source_rule_identity_basis": (
            "compiled_property_expression_and_source_refs"
        ),
    }


def test_source_rule_becomes_primary_customer_visible_finding_identity():
    result = {
        "finding": _finding(),
        "findings": [_finding()],
        "oracle_verdict": {"status": "VIOLATION"},
    }

    governed = _attach_source_rule_identity(result, _experiment())
    finding = governed["finding"]

    assert finding is governed["findings"][0]
    assert finding["source_rule_ref"] == "bir_pass_validity"
    assert finding["source_rule_statement"] == "通行证必须在有效期内"
    assert "通行证必须在有效期内" in finding["title"]
    assert "validation_rejection" in finding["title"]
    assert "POST /api/passes/validate" in finding["title"]
    assert finding["description"].startswith(
        "Source rule violated: 通行证必须在有效期内."
    )
    assert finding["evidence"]["source_rule_ref"] == "bir_pass_validity"


def test_fanout_occurrences_keep_the_same_rule_and_distinct_outcomes():
    result = {
        "finding": _finding("outcome-a"),
        "findings": [
            _finding("outcome-a"),
            _finding("outcome-b"),
        ],
    }

    governed = _attach_source_rule_identity(result, _experiment())

    assert [row["outcome_ref"] for row in governed["findings"]] == [
        "outcome-a",
        "outcome-b",
    ]
    assert {
        row["source_rule_statement"] for row in governed["findings"]
    } == {"通行证必须在有效期内"}


def test_missing_grounded_source_keeps_generic_finding_unchanged():
    result = {
        "finding": _finding(),
        "findings": [_finding()],
    }

    governed = _attach_source_rule_identity(
        result,
        _experiment(with_source=False),
    )

    assert governed["finding"]["title"] == result["finding"]["title"]
    assert "source_rule_ref" not in governed["finding"]
