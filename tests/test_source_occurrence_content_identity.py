from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_core import (
    ingest_enterprise_knowledge_documents,
)


ACTOR = {"name": "source-occurrence-test", "role": "project_owner"}
CONTENT = b"# Refund Rules\nOnly OPEN orders may be refunded.\n"


def _doc(source_ref: str, *, source_type: str = "business_rules", filename: str = "rules.md") -> dict:
    return {
        "content_bytes": CONTENT,
        "filename": filename,
        "source_type": source_type,
        "external_ref": source_ref,
    }


def test_same_interpretation_content_is_parsed_once_and_keeps_two_occurrences(tmp_path) -> None:
    result = ingest_enterprise_knowledge_documents(
        "occurrence-demo",
        [
            _doc("departments/support/rules.md"),
            _doc("departments/finance/rules.md"),
        ],
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["ok"] is True
    assert len(result["created"]) == 1
    assert len(result["duplicates"]) == 1
    assert result["source_count"] == 1
    assert result["source_occurrence_count"] == 2
    assert result["content_asset_count"] == 1
    assert result["interpretation_asset_count"] == 1
    assert len(result["source_occurrences"]) == 2

    occurrences = sorted(result["source_occurrences"], key=lambda row: row["source_ref"])
    assert {row["source_ref"] for row in occurrences} == {
        "departments/support/rules.md",
        "departments/finance/rules.md",
    }
    assert len({row["canonical_source_id"] for row in occurrences}) == 1
    assert len({row["content_asset_id"] for row in occurrences}) == 1
    assert len({row["interpretation_asset_id"] for row in occurrences}) == 1
    assert sum(bool(row["parse_reused"]) for row in occurrences) == 1
    assert all(row["independent_evidence_identity"] is True for row in occurrences)


def test_reingesting_exact_occurrence_does_not_create_second_occurrence(tmp_path) -> None:
    first = ingest_enterprise_knowledge_documents(
        "occurrence-repeat",
        [_doc("rules/refund.md")],
        root=tmp_path,
        actor=ACTOR,
    )
    second = ingest_enterprise_knowledge_documents(
        "occurrence-repeat",
        [_doc("rules/refund.md")],
        root=tmp_path,
        actor=ACTOR,
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["source_occurrences"] == []
    assert len(second["duplicate_source_occurrences"]) == 1
    assert second["source_occurrence_count"] == 1
    assert second["content_asset_count"] == 1
    assert second["interpretation_asset_count"] == 1


def test_same_bytes_with_different_source_type_fail_closed(tmp_path) -> None:
    first = ingest_enterprise_knowledge_documents(
        "occurrence-type-conflict",
        [_doc("rules/business.md", source_type="business_rules")],
        root=tmp_path,
        actor=ACTOR,
    )
    second = ingest_enterprise_knowledge_documents(
        "occurrence-type-conflict",
        [_doc("api/openapi.md", source_type="openapi")],
        root=tmp_path,
        actor=ACTOR,
    )

    assert first["ok"] is True
    assert second["ok"] is False
    error = next(row for row in second["errors"] if row["code"] == "SOURCE_OCCURRENCE_IDENTITY_BLOCKED")
    assert "SOURCE_INTERPRETATION_CONFLICT" in error["detail"]
    assert error["blocks_formal_understanding"] is True
    assert second["source_occurrence_count"] == 1
    assert second["interpretation_asset_count"] == 1


def test_same_bytes_with_different_format_identity_fail_closed(tmp_path) -> None:
    first = ingest_enterprise_knowledge_documents(
        "occurrence-format-conflict",
        [_doc("rules/refund.md", filename="refund.md")],
        root=tmp_path,
        actor=ACTOR,
    )
    second = ingest_enterprise_knowledge_documents(
        "occurrence-format-conflict",
        [_doc("rules/refund.txt", filename="refund.txt")],
        root=tmp_path,
        actor=ACTOR,
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert any(
        "SOURCE_INTERPRETATION_CONFLICT" in str(row.get("detail") or "")
        for row in second["errors"]
    )
    assert second["source_occurrence_count"] == 1
