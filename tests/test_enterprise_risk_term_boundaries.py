from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _risk_type_from_text,
    _ticket_rows,
)


def test_ascii_risk_term_requires_a_lexical_boundary() -> None:
    statement = (
        "In the QualiBug product, operators maintain source material and "
        "service addresses."
    )

    assert _risk_type_from_text(statement) == "business_rule"
    assert _ticket_rows(statement, None, "source-1", "deploy") == []


def test_standalone_historical_defect_term_is_still_detected() -> None:
    statement = "A previously confirmed bug must not recur."

    assert _risk_type_from_text(statement) == "historical_regression"
    assert len(_ticket_rows(statement, None, "source-1", "test_report")) == 1
