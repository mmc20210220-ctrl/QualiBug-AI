from __future__ import annotations

from ai_test_asset_center.private_pilot_product_catalog import (
    _find_finding,
    _finding_detail_request,
)


def test_finding_detail_route_parses_project_and_finding_id() -> None:
    project, finding_id = _finding_detail_request(
        "/api/v1/projects/acme/findings/FIND-123"
    )
    assert project == "acme"
    assert finding_id == "FIND-123"


def test_finding_detail_selects_one_sanitized_projection_without_field_loss() -> None:
    finding = {
        "id": "FIND-123",
        "title": "订单状态异常",
        "evidence_chain": [{"kind": "http", "summary": "POST /orders"}],
        "raw_evidence": {
            "request_raw": {"method": "POST", "path": "/orders"},
            "response_raw": {"status_code": 500},
        },
        "reproduction": {
            "method": "POST",
            "path": "/orders",
            "steps": ["创建订单", "提交支付"],
        },
        "expected_actual_comparison": {
            "expected": "订单进入 paid",
            "actual": "订单仍为 pending",
            "difference": "状态未推进",
        },
        "product_responsibility_boundary": {"no_fix_advice": True},
    }
    payload = {
        "ok": True,
        "data": {
            "finding_classification": {
                "deliverable": [finding],
                "candidate": [],
                "rejected": [],
            },
            "defects": [finding],
        },
    }

    selected = _find_finding(payload, "FIND-123")

    assert selected is finding
    assert selected["evidence_chain"] == finding["evidence_chain"]
    assert selected["raw_evidence"] == finding["raw_evidence"]
    assert selected["reproduction"] == finding["reproduction"]
    assert selected["expected_actual_comparison"] == finding["expected_actual_comparison"]
    assert selected["product_responsibility_boundary"]["no_fix_advice"] is True


def test_finding_detail_can_resolve_persistence_id_but_never_title_guess() -> None:
    finding = {
        "id": "display-1",
        "finding_persistence_id": "sqlite-9",
        "title": "相似标题不能作为身份",
    }
    payload = {"data": {"defects": [finding]}}

    assert _find_finding(payload, "sqlite-9") is finding
    assert _find_finding(payload, "相似标题不能作为身份") is None
