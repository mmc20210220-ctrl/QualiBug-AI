"""Tests for API doc merge and historical-bug behavior slices."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.api_doc_assets import collect_merged_api_catalog, enrich_api_spec_text
from ai_test_asset_center.historical_behavior_slices import (
    generate_historical_behavior_slices,
    load_historical_bug_records,
    _parse_markdown_sections,
)


def test_enrich_api_spec_merges_openapi_paths(tmp_path: Path) -> None:
    project = "demo_mall"
    inputs = tmp_path / "platform_inputs" / project
    inputs.mkdir(parents=True)
    (inputs / "API_SPEC.md").write_text(
        "### GET /api/orders\n\n### POST /api/orders\n",
        encoding="utf-8",
    )
    (inputs / "openapi.json").write_text(
        '{"openapi":"3.0.0","paths":{"/api/auth/me":{"get":{"summary":"me"}},'
        '"/api/refunds/{id}/approve":{"post":{"summary":"approve refund"}}}}',
        encoding="utf-8",
    )
    merged = enrich_api_spec_text(tmp_path, project, "")
    assert "/api/auth/me" in merged
    assert "/api/refunds" in merged
    assert "GET /api/orders" in merged


def test_parse_historical_markdown_sections() -> None:
    text = (
        "## HB-001 订单金额口径不一致\n"
        "历史上优惠券和支付金额计算顺序不一致。\n\n"
        "## HB-004 普通用户越权查看订单\n"
        "历史接口只校验登录态，未校验订单归属。\n"
    )
    rows = _parse_markdown_sections(text, "HISTORICAL_BUGS.md")
    assert len(rows) == 2
    assert rows[0]["historical_bug_id"] == "HB-001"
    assert "金额" in rows[0]["title"] or "订单" in rows[0]["title"]


def test_generate_historical_slices_binds_orders_for_idor(tmp_path: Path) -> None:
    project = "hist_demo"
    input_dir = tmp_path / "projects" / project / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "HISTORICAL_BUGS.md").write_text(
        "## HB-004 普通用户越权查看订单\n"
        "历史接口曾只校验登录态，未校验订单归属。\n",
        encoding="utf-8",
    )
    api = (
        "### GET /api/orders/:id\n\n"
        "### POST /api/orders\n"
        "### POST /api/payments/pay\n"
    )
    slices = generate_historical_behavior_slices(tmp_path, project, api)
    assert slices, "expected at least one historical slice"
    paths = {s["endpoints"][0] for s in slices}
    assert any("/api/orders" in p for p in paths)
    assert all(s["source_refs"][0]["kind"] == "historical_bug" for s in slices)


def test_load_historical_records_from_project_input(tmp_path: Path) -> None:
    project = "hist_load"
    input_dir = tmp_path / "projects" / project / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "HISTORICAL_BUGS.md").write_text("## HB-005 退款重复审批\n重复点击审批。\n", encoding="utf-8")
    rows = load_historical_bug_records(tmp_path, project)
    assert len(rows) == 1
    assert rows[0]["historical_bug_id"] == "HB-005"
