from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._document_ir_fact_evidence import (
    align_business_facts_to_document_ir,
)


def _fact(statement: str, fact_id: str = "fact:1") -> dict:
    return {
        "fact_id": fact_id,
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": statement,
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#section=rules",
                "quote": statement,
            }
        ],
    }


def _block(
    block_id: str,
    text: str,
    order: int,
    *,
    page: int = 1,
    block_type: str = "PARAGRAPH",
) -> dict:
    return {
        "block_id": block_id,
        "type": block_type,
        "region": "body",
        "order": order,
        "page": page,
        "text": text,
        "source_locator": f"rules.docx#page={page};block={block_id}",
    }


def _run(statement: str, blocks: list[dict]) -> dict:
    asset = {"business_fact_ledger": {"items": [_fact(statement)]}}
    sources = [
        {
            "source_id": "source:rules",
            "document_structure": {"blocks": blocks},
        }
    ]
    return align_business_facts_to_document_ir(asset, sources)


def test_fact_can_align_to_one_unique_contiguous_block_span() -> None:
    result = _run(
        "客服必须关闭本人创建的工单",
        [
            _block("b1", "客服必须关闭", 1),
            _block("b2", "本人创建的工单", 2),
        ],
    )

    receipt = result["document_ir_fact_evidence_receipt"]
    assert receipt["aligned_fact_count"] == 1
    assert receipt["contiguous_span_aligned_fact_count"] == 1
    assert receipt["unresolved_fact_count"] == 0
    assert receipt["aligned"][0]["block_ids"] == ["b1", "b2"]
    assert receipt["aligned"][0]["match_kind"] == "CONTIGUOUS_BLOCK_SPAN"

    fact = result["business_fact_ledger"]["items"][0]
    alignment = fact["document_structure_alignment"]
    assert alignment["block_ids"] == ["b1", "b2"]
    assert alignment["source_locator_start"].endswith("block=b1")
    assert alignment["source_locator_end"].endswith("block=b2")
    assert alignment["automatic_business_inference_used"] is False


def test_ambiguous_repeated_statement_is_not_auto_selected() -> None:
    result = _run(
        "客服可以查看工单",
        [
            _block("b1", "客服可以查看工单", 1),
            _block("b2", "客服可以查看工单", 2),
        ],
    )

    unresolved = result["document_ir_fact_evidence_receipt"]["unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["reason"] == "DOCUMENT_IR_FACT_BLOCK_NOT_UNIQUE"
    # Backward compatibility: existing consumers still receive a flat list.
    assert unresolved[0]["candidate_block_ids"] == ["b1", "b2"]
    # New consumers can inspect the actual candidate span grouping.
    assert unresolved[0]["candidate_block_spans"] == [["b1"], ["b2"]]


def test_contiguous_alignment_never_crosses_page_boundary() -> None:
    result = _run(
        "审批通过后申请单进入已通过状态",
        [
            _block("b1", "审批通过后", 1, page=1),
            _block("b2", "申请单进入已通过状态", 2, page=2),
        ],
    )

    receipt = result["document_ir_fact_evidence_receipt"]
    assert receipt["aligned_fact_count"] == 0
    assert receipt["unresolved_fact_count"] == 1
    assert receipt["unresolved"][0]["reason"] == "DOCUMENT_IR_FACT_BLOCK_NOT_FOUND"


def test_table_cells_can_form_one_source_backed_rule_span() -> None:
    result = _run(
        "审批人可以审批申请单",
        [
            _block("c1", "审批人", 1, block_type="TABLE_CELL"),
            _block("c2", "可以审批", 2, block_type="TABLE_CELL"),
            _block("c3", "申请单", 3, block_type="TABLE_CELL"),
        ],
    )

    aligned = result["document_ir_fact_evidence_receipt"]["aligned"]
    assert len(aligned) == 1
    assert aligned[0]["block_ids"] == ["c1", "c2", "c3"]


def test_block_normalization_is_once_per_source_not_once_per_fact(monkeypatch) -> None:
    from ai_test_asset_center.enterprise_knowledge_center import (
        _document_ir_fact_evidence as evidence,
    )

    calls = 0
    original = evidence._normalized

    def counted(value) -> str:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(evidence, "_normalized", counted)
    statements = [f"Rule number {index}" for index in range(40)]
    asset = {
        "business_fact_ledger": {
            "items": [_fact(statement, f"fact:{index}") for index, statement in enumerate(statements)]
        }
    }
    blocks = [_block(f"b{index}", statement, index) for index, statement in enumerate(statements)]

    align_business_facts_to_document_ir(
        asset,
        [{"source_id": "source:rules", "document_structure": {"blocks": blocks}}],
    )

    # One normalization per block plus one per fact statement. The former nested
    # fact-by-block implementation performed at least 40 * 40 block normalizations.
    assert calls == len(blocks) + len(statements)
