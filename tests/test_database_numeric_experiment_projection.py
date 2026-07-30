from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_numeric_experiment_projection import (
    project_database_numeric_assertions,
)
from ai_test_asset_center.database_numeric_oracle import (
    DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
    DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
)


def _contract(
    *,
    observer_ref: str = "observer:accounts",
    table_id: str = "table:accounts",
    table_name: str = "accounts",
    fields: tuple[str, ...] = ("balance", "reserved"),
) -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": observer_ref,
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "database_table_id": table_id,
        "database_table_name": table_name,
        "field_bindings": [
            {
                "field_binding_id": f"binding:{table_name}:{field}",
                "api_field_id": f"api-field:Account.{field}",
                "api_field_name": field,
                "api_property_path": [field],
                "database_field_id": f"field:{table_name}:{field}",
                "database_field_name": field,
                "mapping_decision_id": f"decision:{table_name}:{field}",
                "authoritative": True,
                "read_only": True,
                "oracle_authority_allowed": False,
                "evidence": [
                    {
                        "kind": "OPERATOR_DATABASE_MAPPING_AUTHORITY",
                        "decision_id": f"decision:{table_name}:{field}",
                        "exact": True,
                    }
                ],
            }
            for field in fields
        ],
    }


def _draft(phase: str, contract: dict) -> dict:
    observer_ref = contract["observer_id"]
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": f"draft:{observer_ref}:{phase.lower()}",
        "observer_handler_id": "approved_database_readback",
        "observer_contract_ref": observer_ref,
        "observation_phase": phase,
        "database_observer_contract": deepcopy(contract),
        "required": True,
    }


def _experiment(assertion: dict) -> dict:
    contract = _contract()
    return {
        "experiment_id": "experiment:accounts",
        "obligation_id": "obligation:accounts",
        "compile_receipt": {"status": "COMPILED"},
        "compiled_adapters": ["http_api", "db_sql"],
        "observers": [
            {"observer_id": "before_state", "adapter": "http_api"},
            {"observer_id": "after_state", "adapter": "http_api"},
            {
                "observer_id": "approved_database_phase_aggregate",
                "adapter": "db_sql",
            },
        ],
        "database_observer_execution_drafts": [
            _draft("BEFORE", contract),
            _draft("AFTER", contract),
        ],
        "assertions": [assertion],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "status": "RESOLVED",
        },
    }


def _pack(experiment: dict) -> dict:
    return {
        "experiments": [experiment],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def test_exact_field_id_projects_database_delta() -> None:
    experiment = _experiment(
        {
            "assertion_id": "assert:balance",
            "kind": "field_delta",
            "fields": [
                {
                    "field_id": "api-field:Account.balance",
                    "field": "balance",
                    "expected_delta": "-10.00",
                }
            ],
            "source_refs": [{"kind": "business_rule", "locator": "BR-BALANCE-1"}],
        }
    )

    result = project_database_numeric_assertions(_pack(experiment))

    assert result["blocked_count"] == 0
    projected = result["experiments"][0]
    assertion = projected["assertions"][0]
    assert assertion["kind"] == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
    assert assertion["source_assertion_kind"] == "field_delta"
    term = assertion["numeric_terms"][0]
    assert term["database_observer_contract_ref"] == "observer:accounts"
    assert term["database_field_id"] == "field:accounts:balance"
    assert term["expected_delta"] == "-10.00"
    assert term["match_basis"] == "EXACT_FIELD_ID"
    assert {row["observer_id"] for row in projected["observers"]} == {
        "approved_database_phase_aggregate"
    }
    assert projected["compile_receipt"]["database_numeric_assertion_fingerprint"]


def test_same_contract_unchanged_sum_projects_conservation() -> None:
    experiment = _experiment(
        {
            "assertion_id": "assert:funds-conserved",
            "kind": "conservation",
            "equation": {
                "operator": "unchanged_sum",
                "terms": [
                    {"field_id": "api-field:Account.balance", "coefficient": 1},
                    {"field_id": "api-field:Account.reserved", "coefficient": 1},
                ],
            },
        }
    )

    result = project_database_numeric_assertions(_pack(experiment))

    assertion = result["experiments"][0]["assertions"][0]
    assert assertion["kind"] == DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND
    assert assertion["numeric_policy"] == "UNCHANGED_WEIGHTED_SUM"
    assert len(assertion["numeric_terms"]) == 2
    assert {
        row["database_observer_contract_ref"] for row in assertion["numeric_terms"]
    } == {"observer:accounts"}


def test_cross_contract_conservation_stays_visible_gap() -> None:
    experiment = _experiment(
        {
            "assertion_id": "assert:cross-table",
            "kind": "conservation",
            "equation": {
                "operator": "unchanged_sum",
                "terms": [
                    {"field_id": "api-field:Account.balance"},
                    {"field_id": "api-field:Account.reserved"},
                ],
            },
        }
    )
    second = _contract(
        observer_ref="observer:ledger",
        table_id="table:ledger",
        table_name="ledger",
        fields=("reserved",),
    )
    second["field_bindings"][0]["api_field_id"] = "api-field:Account.reserved"
    experiment["database_observer_execution_drafts"] = [
        experiment["database_observer_execution_drafts"][0],
        experiment["database_observer_execution_drafts"][1],
        _draft("BEFORE", second),
        _draft("AFTER", second),
    ]
    # Remove the original reserved binding so each term is exact but lives in a different contract.
    original = experiment["database_observer_execution_drafts"][0][
        "database_observer_contract"
    ]
    original["field_bindings"] = [
        row for row in original["field_bindings"] if row["api_field_name"] == "balance"
    ]
    experiment["database_observer_execution_drafts"][1][
        "database_observer_contract"
    ] = deepcopy(original)

    result = project_database_numeric_assertions(_pack(experiment))

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "conservation"
    assert projected["database_numeric_projection_status"] == "INCOMPLETE"
    assert projected["database_numeric_projection_gaps"][0]["reason_code"] == (
        "DATABASE_NUMERIC_CROSS_CONTRACT_SCOPE_UNPROVEN"
    )
    assert {row["observer_id"] for row in projected["observers"]} >= {
        "before_state",
        "after_state",
    }


def test_explicit_field_id_never_downgrades_to_matching_name() -> None:
    experiment = _experiment(
        {
            "assertion_id": "assert:wrong-id",
            "kind": "field_delta",
            "fields": [
                {
                    "field_id": "api-field:Account.ledger_balance",
                    "field": "balance",
                    "expected_delta": -10,
                }
            ],
        }
    )

    result = project_database_numeric_assertions(_pack(experiment))

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "field_delta"
    gap = projected["database_numeric_projection_gaps"][0]
    assert gap["reason_code"] == "DATABASE_NUMERIC_EXACT_FIELD_BINDING_MISSING"
    assert gap["explicit_field_ids"] == ["api-field:Account.ledger_balance"]


def test_two_exact_bindings_block_without_automatic_winner() -> None:
    experiment = _experiment(
        {
            "assertion_id": "assert:balance",
            "kind": "field_delta",
            "fields": [
                {
                    "field_id": "api-field:Account.balance",
                    "expected_delta": -10,
                }
            ],
        }
    )
    second = _contract(
        observer_ref="observer:account-history",
        table_id="table:account_history",
        table_name="account_history",
        fields=("balance",),
    )
    second["field_bindings"][0]["api_field_id"] = "api-field:Account.balance"
    experiment["database_observer_execution_drafts"].extend(
        [_draft("BEFORE", second), _draft("AFTER", second)]
    )

    result = project_database_numeric_assertions(_pack(experiment))

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    blocked = result["blocked_experiments"][0]
    assert blocked["compile_receipt"]["reason_code"] == (
        "BLOCKED_DATABASE_NUMERIC_ORACLE_BINDING_AMBIGUOUS"
    )
    assert blocked["compile_receipt"]["database_numeric_oracle_detail"][
        "automatic_winner_allowed"
    ] is False
