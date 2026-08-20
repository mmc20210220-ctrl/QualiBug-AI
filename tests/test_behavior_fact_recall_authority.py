"""Regressions for preserving test-worthy facts in Business Behavior IR."""
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_fact_recall_authority import (
    BEHAVIOR_WORTHY_FACT_KINDS,
    is_behavior_worthy_fact,
    normalized_fact_kind,
)


def test_test_worthy_taxonomy_is_generic() -> None:
    assert {
        "AUTHORIZATION",
        "INVARIANT",
        "CONCURRENCY",
        "IDEMPOTENCY",
        "STATE_TRANSITION",
    } <= BEHAVIOR_WORTHY_FACT_KINDS


def test_fact_type_takes_precedence_over_legacy_kind() -> None:
    assert normalized_fact_kind(
        {"fact_type": "authorization", "kind": "DESCRIPTION"}
    ) == "AUTHORIZATION"


def test_accepted_test_worthy_fact_is_preserved_candidate() -> None:
    assert is_behavior_worthy_fact(
        {"status": "ACCEPTED", "fact_type": "PERMISSION"}
    )


def test_descriptive_fact_is_not_promoted() -> None:
    assert not is_behavior_worthy_fact(
        {"status": "ACCEPTED", "kind": "DESCRIPTION"}
    )


def test_nonaccepted_fact_is_not_promoted() -> None:
    assert not is_behavior_worthy_fact(
        {"status": "PENDING", "kind": "AUTHORIZATION"}
    )
