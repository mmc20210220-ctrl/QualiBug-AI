"""主链 9 回归测试：修复后回归验证闭环（生产数据禁触 + 主链6/7 产物消费）。

覆盖：
  Gap A  — 回归 HTTP 探针命中主链 1 生产数据禁触边界时，绝不发包。
  Gap B1 — v12 将“可交付确认缺陷”按稳定 evidence_id 落盘，供主链 9 消费。
  Gap B2 — 回归运行消费主链 6/7 产物，对确认缺陷做 resolved / persisted / blocked 复验。
"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# private_pilot_service 在导入期校验 JWT 密钥，单测需预置（仅开发占位值）
os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.regression_runner import (  # noqa: E402
    _execute_http_probe,
    _is_production_data_blocked,
    _reverify_confirmed_findings,
)
from ai_test_asset_center.v12_pipeline import _persist_confirmed_findings  # noqa: E402


# ---------------------------------------------------------------------------
# Gap A: production-data exclusion must block the regression probe entirely
# ---------------------------------------------------------------------------

def test_gap_a_probe_blocked_never_sends_request():
    """命中生产数据禁触边界的回归探针不得发出任何 HTTP 请求。"""
    boundary = {"production_data_exclusion": ["/api/admin/users"]}
    probe = {"method": "GET", "path": "/api/admin/users/1", "risk_type": ""}
    cfg = {"base_url": "http://sut.invalid"}
    # 若 urlopen 被调用则说明拦截失败 —— 这里断言它绝不会被调用。
    with mock.patch("urllib.request.urlopen") as urlopen:
        result = _execute_http_probe(probe, cfg, "proj", Path("/tmp"), 3, boundary)
    urlopen.assert_not_called()
    assert result.get("production_data_blocked") is True
    assert result.get("error") == "production_data_blocked"
    assert result.get("reachable") is False


def test_gap_a_probe_allowed_when_not_excluded():
    """未命中边界的探针按正常路径尝试发包（此处仅验证边界判定通过）。"""
    boundary = {"production_data_exclusion": ["/api/admin/users"]}
    assert _is_production_data_blocked(boundary, {"path": "/api/orders/1"}) == ""
    # 命中则返回原因串
    assert _is_production_data_blocked(boundary, {"path": "/api/admin/users/x"}).startswith(
        "production_data_exclusion_matched:"
    )


# ---------------------------------------------------------------------------
# Gap B1: confirmed (deliverable) defects are persisted keyed by evidence_id
# ---------------------------------------------------------------------------

def _confirmed_finding(evidence_id, status_code, delivery="defect"):
    return {
        "evidence_id": evidence_id,
        "title": f"defect {evidence_id}",
        "severity": "P0",
        "customer_delivery_status": delivery,
        "bug_status": "reproduced",
        "confirmation_status": "confirmed",
        "expected": "200",
        "actual": "500",
        "evidence": {"request": f"GET /api/orders/{evidence_id}", "target": "http://sut/api/orders/{evidence_id}", "reproduction_steps": ["GET /api/orders/{evidence_id}"]},
        "raw_evidence": {"response_raw": {"status_code": status_code}},
    }


def test_gap_b1_persists_only_deliverable_confirmed_defects(tmp_path):
    """仅 customer_delivery_status=='defect' 且带 evidence_id 的缺陷落盘；
    被安全边界拦截的缺陷与无 id 缺陷被排除。"""
    findings = [
        _confirmed_finding("EVID_AAA", 500, delivery="defect"),
        _confirmed_finding("EVID_BBB", 500, delivery="blocked_safety_boundary"),  # 被拦截 → 排除
        _confirmed_finding("", 500, delivery="defect"),  # 无 id → 排除
    ]
    saved = _persist_confirmed_findings(tmp_path, "proj", findings)
    assert saved == 1
    path = tmp_path / "platform_workspace" / "proj" / "defect_discovery" / "confirmed_findings.json"
    assert path.exists()
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert set(ledger.keys()) == {"EVID_AAA"}
    assert ledger["EVID_AAA"]["buggy_status_code"] == 500
    assert ledger["EVID_AAA"]["reproduction"]["method"] == "GET"
    assert ledger["EVID_AAA"]["reproduction"]["path"] == "/api/orders/EVID_AAA"


# ---------------------------------------------------------------------------
# Gap B2: post-fix re-verification consumes 主链 6/7 products
# ---------------------------------------------------------------------------

def _seed_confirmed_findings(ws: Path, defects: dict) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "confirmed_findings.json").write_text(
        json.dumps(defects, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_gap_b2_resolved_and_persisted(tmp_path):
    """修复的缺陷复现返回非缺陷码 → resolved；仍为缺陷码 → persisted。"""
    ws = tmp_path / "platform_workspace" / "proj" / "defect_discovery"
    _seed_confirmed_findings(ws, {
        "E1": {"evidence_id": "E1", "title": "t1", "severity": "P0", "buggy_status_code": 500,
               "reproduction": {"method": "GET", "path": "/api/orders/1"}},
        "E2": {"evidence_id": "E2", "title": "t2", "severity": "P1", "buggy_status_code": 500,
               "reproduction": {"method": "GET", "path": "/api/orders/2"}},
    })

    def _fake_probe(probe, cfg, project, root, timeout, safety_boundary=None):
        status = 200 if probe["path"] == "/api/orders/1" else 500
        return {"reachable": True, "status_code": status, "error": "", "body_excerpt": ""}

    with mock.patch("ai_test_asset_center.regression_runner._execute_http_probe", side_effect=_fake_probe):
        rv = _reverify_confirmed_findings("proj", tmp_path, {"base_url": "http://sut"}, {}, 3, dry_run=False)
    assert rv["consumed"] is True
    by_id = {v["evidence_id"]: v["status"] for v in rv["verdicts"]}
    assert by_id["E1"] == "resolved"
    assert by_id["E2"] == "persisted"
    assert rv["counts"]["total"] == 2
    assert rv["counts"]["resolved"] == 1
    assert rv["counts"]["persisted"] == 1


def test_gap_b2_blocked_by_safety_boundary(tmp_path):
    """复现路径命中生产数据禁触边界 → blocked，且绝不发起探针请求。"""
    ws = tmp_path / "platform_workspace" / "proj" / "defect_discovery"
    _seed_confirmed_findings(ws, {
        "E3": {"evidence_id": "E3", "title": "t3", "severity": "P0", "buggy_status_code": 500,
               "reproduction": {"method": "GET", "path": "/api/admin/users/1"}},
    })
    boundary = {"production_data_exclusion": ["/api/admin/users"]}
    with mock.patch("ai_test_asset_center.regression_runner._execute_http_probe") as probe:
        rv = _reverify_confirmed_findings("proj", tmp_path, {"base_url": "http://sut"}, boundary, 3, dry_run=False)
    probe.assert_not_called()  # 命中边界直接 blocked，不发包
    assert rv["verdicts"][0]["status"] == "blocked"
    assert rv["counts"]["blocked"] == 1


def test_gap_b2_dry_run_needs_review(tmp_path):
    """dry_run 不发起真实复现，确认缺陷标记为 needs_review。"""
    ws = tmp_path / "platform_workspace" / "proj" / "defect_discovery"
    _seed_confirmed_findings(ws, {
        "E4": {"evidence_id": "E4", "title": "t4", "severity": "P2", "buggy_status_code": 500,
               "reproduction": {"method": "GET", "path": "/api/orders/9"}},
    })
    with mock.patch("ai_test_asset_center.regression_runner._execute_http_probe") as probe:
        rv = _reverify_confirmed_findings("proj", tmp_path, {"base_url": "http://sut"}, {}, 3, dry_run=True)
    probe.assert_not_called()
    assert rv["verdicts"][0]["status"] == "needs_review"
    assert rv["counts"]["needs_review"] == 1
