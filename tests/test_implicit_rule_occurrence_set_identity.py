from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_identity_reconciliation import (
    reconcile_implicit_rule_identities,
)


IDEMPOTENCY = "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"
REF_PRIMARY = "online-docs/rules/payment.md"
REF_MIRROR = "mirror-docs/rules/payment.md"
IDENTITY_AUTHORITY = "PROJECT_SCOPED_TYPED_SEMANTICS_WITH_OCCURRENCE_EVIDENCE"


def _fact(source_id: str) -> dict:
    return {
        "fact_id": f"fact:payment-idempotency:{source_id}",
        "fact_type": "BUSINESS_RULE",
        "status": "ACCEPTED",
        "raw_statement": IDEMPOTENCY,
        "subject": {"entity_refs": ["付款请求"]},
        "action": {"canonical": "付款"},
        "source_spans": [
            {
                "source_id": source_id,
                "locator": "payment.md#idempotency",
                "document_block_id": "payment-idempotency",
            }
        ],
        "confidence": 1.0,
    }


def _governed_asset(
    *,
    project_id: str,
    source_id: str,
    source_hash: str,
    source_version_id: str,
    source_refs: list[str],
    parser_rule_id: str,
    lifecycle: dict | None = None,
) -> dict:
    asset = {
        "project_id": project_id,
        "source_inventory": [
            {
                "source_id": source_id,
                "status": "active",
                "content_hash": source_hash,
                "source_version_id": source_version_id,
                "source_type": "business_rules",
                "source_refs": list(source_refs),
            }
        ],
        "field_dictionary": [],
        "data_tables": [],
        "interfaces": [],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [
            {
                "rule_id": parser_rule_id,
                "source_id": source_id,
                "source_type": "business_rules",
                "source_locator": "line:3",
                "statement": IDEMPOTENCY,
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "severity": "P0",
            }
        ],
        "business_fact_ledger": {"items": [_fact(source_id)]},
    }
    if lifecycle:
        asset["implicit_rule_lifecycle_ledger"] = deepcopy(lifecycle)
    return enrich_asset_with_governed_implicit_rule_projection(asset)


def test_occurrence_ref_add_remove_does_not_change_project_rule_id():
    first = _governed_asset(
        project_id="merchant-platform",
        source_id="canonical:payment:v1",
        source_hash="hash-v1",
        source_version_id="srcv-v1",
        source_refs=[REF_PRIMARY],
        parser_rule_id="rule:parser:v1:idempotency",
    )
    first_rule = first["rule_library"][0]
    authority_rule_id = first_rule["rule_id"]

    assert authority_rule_id.startswith("implicit_rule:")
    assert first_rule["rule_identity_authority"] == IDENTITY_AUTHORITY
    assert first_rule["rule_identity_project_id"] == "merchant-platform"
    assert first_rule["stable_source_refs"] == [REF_PRIMARY]
    assert first_rule["occurrence_ref_set_participates_in_rule_id"] is False

    with_mirror = _governed_asset(
        project_id="merchant-platform",
        source_id="canonical:payment:v2",
        source_hash="hash-v2",
        source_version_id="srcv-v2",
        source_refs=[REF_PRIMARY, REF_MIRROR],
        parser_rule_id="rule:parser:v2:idempotency",
        lifecycle=first["implicit_rule_lifecycle_ledger"],
    )
    mirror_rule = with_mirror["rule_library"][0]

    assert mirror_rule["rule_id"] == authority_rule_id
    assert mirror_rule["stable_source_refs"] == sorted([REF_PRIMARY, REF_MIRROR])
    assert with_mirror["implicit_rule_lifecycle_ledger"]["active_rule_count"] == 1
    assert with_mirror["implicit_rule_lifecycle_ledger"]["stale_rule_count"] == 0

    primary_removed = _governed_asset(
        project_id="merchant-platform",
        source_id="canonical:payment:v3",
        source_hash="hash-v3",
        source_version_id="srcv-v3",
        source_refs=[REF_MIRROR],
        parser_rule_id="rule:parser:v3:idempotency",
        lifecycle=with_mirror["implicit_rule_lifecycle_ledger"],
    )
    final_rule = primary_removed["rule_library"][0]

    assert final_rule["rule_id"] == authority_rule_id
    assert final_rule["stable_source_refs"] == [REF_MIRROR]
    lifecycle = primary_removed["implicit_rule_lifecycle_ledger"]
    assert lifecycle["active_rule_count"] == 1
    assert lifecycle["stale_rule_count"] == 0
    assert [row["rule_id"] for row in lifecycle["items"]] == [authority_rule_id]
    receipt = primary_removed["implicit_rule_identity_reconciliation_receipt"]
    assert receipt["project_scoped_rule_identity_count"] == 1
    assert receipt["occurrence_ref_set_participates_in_rule_id"] is False
    assert receipt["multiple_occurrences_create_parallel_rules"] is False


def test_identical_typed_semantics_are_isolated_by_project_scope():
    project_a = _governed_asset(
        project_id="merchant-platform-a",
        source_id="canonical:payment:a",
        source_hash="hash-a",
        source_version_id="srcv-a",
        source_refs=[REF_PRIMARY],
        parser_rule_id="rule:parser:a:idempotency",
    )
    project_b = _governed_asset(
        project_id="merchant-platform-b",
        source_id="canonical:payment:b",
        source_hash="hash-b",
        source_version_id="srcv-b",
        source_refs=[REF_PRIMARY],
        parser_rule_id="rule:parser:b:idempotency",
    )

    rule_a = project_a["rule_library"][0]
    rule_b = project_b["rule_library"][0]
    assert rule_a["stable_source_refs"] == rule_b["stable_source_refs"]
    assert rule_a["rule_id"] != rule_b["rule_id"]
    assert rule_a["rule_identity_project_id"] == "merchant-platform-a"
    assert rule_b["rule_identity_project_id"] == "merchant-platform-b"


def _validated_candidate(candidate_id: str, source_id: str, rule_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "kind": "rule",
        "statement": IDEMPOTENCY,
        "logical_form": "IDEMPOTENCY",
        "consequent": {
            "operator": "business_effect_count",
            "expected_effect_count": 1,
        },
        "subject_refs": ["付款请求"],
        "supporting_fact_refs": [f"fact:{candidate_id}"],
        "supporting_source_ids": [source_id],
        "source_refs": [
            {
                "source_id": source_id,
                "source_locator": "payment.md#idempotency",
            }
        ],
        "source_authority": "formal_constraint",
        "falsifiability": "EVALUABLE",
        "binding_readiness": "READY_FOR_IR_BINDING",
        "scope_status": "NOT_APPLICABLE",
        "exception_status": "NOT_APPLICABLE",
        "counterexample_plan": {"repetitions": 2},
        "risk_type": "idempotency",
        "authority_upgrade_target": {
            "rule_id": rule_id,
            "match_kind": "SOURCE_RULE_TYPED_SEMANTIC_UPGRADE",
            "source_statement_relation": "EXACT_NORMALIZED_STATEMENT",
        },
    }


def test_duplicate_source_declarations_fuse_into_one_project_rule():
    asset = {
        "project_id": "merchant-platform",
        "source_inventory": [
            {
                "source_id": "canonical:primary",
                "source_refs": [REF_PRIMARY],
            },
            {
                "source_id": "canonical:mirror",
                "source_refs": [REF_MIRROR],
            },
        ],
        "interfaces": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "rule_library": [
            {
                "rule_id": "rule:primary",
                "source_id": "canonical:primary",
                "source_type": "business_rules",
                "statement": IDEMPOTENCY,
                "risk_type": "idempotency",
            },
            {
                "rule_id": "rule:mirror",
                "source_id": "canonical:mirror",
                "source_type": "business_rules",
                "statement": IDEMPOTENCY,
                "risk_type": "idempotency",
            },
        ],
        "implicit_rule_candidate_validation_receipt": {
            "validated": [
                _validated_candidate(
                    "candidate:primary", "canonical:primary", "rule:primary"
                ),
                _validated_candidate(
                    "candidate:mirror", "canonical:mirror", "rule:mirror"
                ),
            ]
        },
        "implicit_rule_projection_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
    }

    reconciled = reconcile_implicit_rule_identities(asset)

    assert len(reconciled["rule_library"]) == 1
    rule = reconciled["rule_library"][0]
    assert rule["rule_id"].startswith("implicit_rule:")
    assert rule["source_rule_ids"] == ["rule:mirror", "rule:primary"]
    assert rule["stable_source_refs"] == [REF_MIRROR, REF_PRIMARY]
    assert len(rule["source_rule_origins"]) == 2
    assert rule["authority_upgrade_receipt"]["merged_source_rule_count"] == 2
    receipt = reconciled["implicit_rule_identity_reconciliation_receipt"]
    assert receipt["merged_rule_count"] == 1
    assert receipt["duplicate_source_rule_count"] == 1
    assert receipt["multiple_occurrences_create_parallel_rules"] is False
    assert len(reconciled["risk_domains"]) == 1
    assert len(reconciled["oracle_library"]) == 1
