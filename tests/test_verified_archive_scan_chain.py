# -*- coding: utf-8 -*-
"""Task31 单测：已验证发现档案自动合并链（scan 收尾）修复锁定。

覆盖 run10 断链根因的三段：
  1. 合并链执行 —— _verified_archive_chain 端到端：load→merge→apply→save，
     输出 findings 含 archive_entry 历史保持，receipt 完整，档案落盘刷新；
  2. receipt 挂载 —— 全链路 scan()（stub v12 pipeline）：receipt 进入
     result 与 scan_result.json（分片 store 往返），save_report=False 时
     同样挂载；
  3. 异常可见 —— 链失败时返回 FAILED receipt（带原因）+ error 日志，扫描
     不阻塞、不静默。
合成数据，无基准材料、无 GT。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.__main__ import _verified_archive_chain, scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.scan_result_store import load_scan_result
from ai_test_asset_center.verified_discovery_archive import (
    finding_stable_identity,
    load_verified_discovery_archive,
    save_verified_discovery_archive,
)
from tests.mainline_test_support import authoritative_v12_double

API_SPEC = """
openapi: 3.0.0
info:
  title: Demo API
  version: '1.0'
servers:
  - url: http://example.test
paths:
  /api/orders/{orderId}/cancel:
    post:
      parameters:
        - in: path
          name: orderId
          required: true
          schema:
            type: string
      responses:
        '200':
          description: cancelled
"""


def _delivered_finding(
    finding_id: str,
    title: str,
    *,
    method: str = "POST",
    path: str = "/api/orders/ord_1/cancel",
    assertion_kind: str = "authorization",
    expected: object = {"cancelled": False},
    actual: object = {"cancelled": True},
    fingerprint: str = "current-run-fingerprint",
) -> dict:
    """Delivered-defect-shaped finding（与 scan 主链交付契约一致）。

    身份由 操作 + 断言种类 + 违例形态 决定（角色无关聚合语义），因此不同
    档案身份必须改 method/path/assertion_kind/expected/actual，标题不参与。
    已交付 finding 携带其交付 run 的 mainline_run 权威指纹（与真实档案条目
    一致——run10 档案条目均带 contract_fingerprint）。
    """
    return {
        "candidate_id": f"candidate-{finding_id}",
        "slice_id": f"slice-{finding_id}",
        "obligation_id": f"obligation-{finding_id}",
        "experiment_id": f"experiment-{finding_id}",
        "execution_id": f"execution-{finding_id}",
        "evidence_id": f"evidence-{finding_id}",
        "finding_id": finding_id,
        "title": title,
        "risk_type": "business_invariant",
        "severity": "P1",
        "method": method,
        "path": path,
        "bug_status": "reproduced",
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "gate_passed": True,
        "actor": "qa_lead",
        "mainline_run": {"contract_fingerprint": fingerprint},
        "semantic_verdict": "SEMANTIC_CONFIRMED",
        "business_evidence_status": "VALIDATED",
        "timestamp": "2026-08-09T00:00:00Z",
        "failed_assertions": ["invariant violated"],
        "reproduction": {
            "method": method,
            "path": path,
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"status": "changed"}},
        },
        "reproduction_steps": [f"{method} {path}", "observe state changed"],
        "evidence_quality": {"level": "validated", "score": 96, "missing": [], "next_actions": [], "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "VALIDATED_CANDIDATE",
            "missing_requirements": [],
        },
        "evidence": {
            "request": f"{method} {path}",
            "response": "HTTP 200",
            "assertion": {"kind": assertion_kind, "expected": expected, "actual": actual},
            "timestamp": "2026-08-09T00:00:00Z",
            "target": path,
            "actor": "qa_lead",
        },
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-08-09T00:00:00Z",
            "request_raw": {"method": method, "path": path, "actor": "qa_lead", "body": {"actor": "buyer"}},
            "response_raw": {"status_code": 200, "body": {"status": "changed"}},
            "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/unit-1"}},
        },
        "evidence_package": {"status": "packaged", "runtime": {"http_status": 200}},
        "source_refs": [{"type": "prd", "ref": "invariant"}],
    }


def _seed_archive(tmp_path: Path, project: str, seed_specs: list[dict]) -> dict:
    """Seed archive with synthetic held entries（身份由 finding_stable_identity 决定）。

    seed_specs: [{finding_id, title, method, path, assertion_kind, expected, actual}, ...]
    每个 spec 必须对应独立身份（不同操作/断言种类/违例形态）。
    """
    archive = {
        "schema_version": "qualibug.verified-discovery-archive.v1",
        "project": project,
        "entries": {},
        "retired": {},
    }
    for i, spec in enumerate(seed_specs):
        finding = _delivered_finding(
            spec.get("finding_id", f"seed-{i}"),
            spec["title"],
            method=spec.get("method", "GET"),
            path=spec.get("path", "/api/audit/logs"),
            assertion_kind=spec.get("assertion_kind", "authorization"),
            expected=spec.get("expected", {"visible": False}),
            actual=spec.get("actual", {"visible": True}),
            # 档案条目携带其交付 run 的指纹（≠ 当前 run，证明来源而非伪造）
            fingerprint=spec.get("fingerprint", "prior-run-fingerprint"),
        )
        identity = finding_stable_identity(finding)
        archive["entries"][identity] = {
            "identity": identity,
            "first_verified_run": "RUN-seed",
            "first_verified_at": "2026-08-01T00:00:00Z",
            "last_verified_run": "RUN-seed",
            "last_verified_at": "2026-08-01T00:00:00Z",
            "campaign_id": "CMP-seed",
            "fix_signal_count": 0,
            "finding": finding,
        }
    save_verified_discovery_archive(project, tmp_path, archive)
    return archive


# ──────────────────────────────────────────────────────────────────────────
# 1. 合并链执行（单元级）
# ──────────────────────────────────────────────────────────────────────────

def test_verified_archive_chain_merges_applies_and_saves(tmp_path: Path) -> None:
    project = "chain-unit"
    seeded = _seed_archive(tmp_path, project, [
        {"title": "GET /api/audit/logs exposes cross-tenant rows", "method": "GET", "path": "/api/audit/logs"},
        {"title": "DELETE /api/sessions lacks ownership check", "method": "DELETE", "path": "/api/sessions", "assertion_kind": "visibility"},
    ])
    run_finding = _delivered_finding("RUN-1", "POST /api/orders/ord_1/cancel violates paid-order invariant")

    output, receipt = _verified_archive_chain(
        project,
        tmp_path,
        v12={"mainline_run": {"run_id": "RUN-CHAIN"}},
        campaign={"campaign_id": "CMP-CHAIN"},
        findings=[run_finding],
    )

    held = [f for f in output if f.get("archive_entry") is True]
    assert len(held) == 2, "两个已激活档案条目应作为 archive_entry 历史保持"
    assert receipt["run_delivered"] == 1
    assert receipt["archive_held"] == 2
    assert receipt["total_output"] == 3
    assert receipt.get("status") is None, "成功路径 receipt 不应带 FAILED status"
    # 本 run 新交付仍在输出中，且不是 archive_entry
    assert any(f.get("finding_id") == "RUN-1" and not f.get("archive_entry") for f in output)
    # 档案落盘：3 条（2 条保持 + 1 条新交付）；种子条目未被本 run 重新交付
    # 则保持原 last_verified_run（单调保持语义，只有重新交付才刷新）
    saved = load_verified_discovery_archive(project, tmp_path)
    assert len(saved["entries"]) == 3
    first_seed_id = next(iter(seeded["entries"]))
    assert saved["entries"][first_seed_id]["last_verified_run"] == "RUN-seed"
    new_id = finding_stable_identity(run_finding)
    assert saved["entries"][new_id]["first_verified_run"] == "RUN-CHAIN"
    assert saved["entries"][new_id]["last_verified_run"] == "RUN-CHAIN"


def test_verified_archive_chain_refreshes_redelivered_identity(tmp_path: Path) -> None:
    project = "chain-redeliver"
    seed_spec = {"title": "GET /api/audit/logs exposes cross-tenant rows", "method": "GET", "path": "/api/audit/logs"}
    seeded = _seed_archive(tmp_path, project, [seed_spec])
    redelivered = _delivered_finding(
        "RUN-REDELIVER",
        "GET /api/audit/logs exposes cross-tenant rows",
        method="GET",
        path="/api/audit/logs",
        expected={"visible": False},
        actual={"visible": True},
    )

    output, receipt = _verified_archive_chain(
        project,
        tmp_path,
        v12={"mainline_run": {"run_id": "RUN-REDELIVER"}},
        campaign={"campaign_id": "CMP-REDELIVER"},
        findings=[redelivered],
    )

    # 本 run 重新交付：身份碰撞 → run finding 胜出，不标记 archive_entry
    assert len(output) == 1
    assert output[0]["finding_id"] == "RUN-REDELIVER"
    assert output[0].get("archive_entry") is not True
    assert receipt["run_delivered"] == 1
    assert receipt["archive_held"] == 0
    assert receipt["total_output"] == 1
    # 档案中该身份 last_verified_run 刷新到本次 run
    saved = load_verified_discovery_archive(project, tmp_path)
    identity = finding_stable_identity(redelivered)
    assert identity in saved["entries"]
    assert saved["entries"][identity]["last_verified_run"] == "RUN-REDELIVER"
    assert saved["entries"][identity]["last_verified_at"] != "2026-08-01T00:00:00Z"


def test_verified_archive_chain_run_identity_falls_back_to_campaign(tmp_path: Path) -> None:
    project = "chain-fallback"
    _seed_archive(tmp_path, project, [
        {"title": "GET /api/legacy leaks state", "method": "GET", "path": "/api/legacy"},
    ])
    run_finding = _delivered_finding("RUN-FB", "POST /api/migrate breaks invariant")

    output, receipt = _verified_archive_chain(
        project,
        tmp_path,
        v12={},
        campaign={"campaign_id": "CMP-FALLBACK"},
        findings=[run_finding],
    )

    assert receipt["run_delivered"] == 1
    saved = load_verified_discovery_archive(project, tmp_path)
    new_id = finding_stable_identity(run_finding)
    assert saved["entries"][new_id]["last_verified_run"] == "CMP-FALLBACK"


# ──────────────────────────────────────────────────────────────────────────
# 3. 异常可见（FAILED receipt + 日志，不阻塞、不静默）
# ──────────────────────────────────────────────────────────────────────────

def test_verified_archive_chain_failure_is_visible_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = "chain-fail"
    run_finding = _delivered_finding("RUN-EX", "POST /api/fragile violates invariant")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("archive-corrupt-marker")

    monkeypatch.setattr(
        "ai_test_asset_center.verified_discovery_archive.apply_verified_discovery_archive_to_run",
        _boom,
    )

    output, receipt = _verified_archive_chain(
        project,
        tmp_path,
        v12={"mainline_run": {"run_id": "RUN-EX"}},
        campaign={"campaign_id": "CMP-EX"},
        findings=[run_finding],
    )

    # 扫描不阻塞：findings 原样返回
    assert output == [run_finding]
    # 异常可见：FAILED receipt 带原因，绝不静默
    assert receipt["status"] == "FAILED"
    assert "RuntimeError" in receipt["reason"]
    assert "archive-corrupt-marker" in receipt["reason"]
    assert receipt["schema_version"] == "qualibug.verified-discovery-archive.v1"


# ──────────────────────────────────────────────────────────────────────────
# 2. receipt 挂载（scan 全链路，stub v12 pipeline）
# ──────────────────────────────────────────────────────────────────────────

def _run_stubbed_scan(tmp_path: Path, project: str, *, save_report: bool = True) -> dict:
    manifest = register_source_asset(project, "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "pass", "status": "ready"}

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 1},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "CMP-SCAN-CHAIN",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "source_hash": manifest["source_hash"],
            },
            "findings": [_delivered_finding("BUG-SCAN-1", "POST /api/orders/ord_1/cancel violates paid-order invariant")],
            "external_findings": [],
            "auto_har": {"status": "captured"},
            "total_duration_ms": 1,
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline.run_v12_pipeline",
        authoritative_v12_double(fake_v12_pipeline),
    )
    try:
        return scan(
            project=project,
            root=tmp_path,
            api_doc_text=API_SPEC,
            base_url="http://127.0.0.1:8080",
            save_report=save_report,
            campaign_context={"scope_id": "service-a", "environment_ref": "test-a", "source_manifest": manifest},
        )
    finally:
        monkeypatch.undo()


def test_scan_mounts_receipt_and_archive_holdover_on_result_and_scan_result(tmp_path: Path) -> None:
    project = "scan-chain-mount"
    _seed_archive(tmp_path, project, [
        {"title": "GET /api/audit/logs exposes cross-tenant rows", "method": "GET", "path": "/api/audit/logs"},
        {"title": "DELETE /api/sessions lacks ownership check", "method": "DELETE", "path": "/api/sessions", "assertion_kind": "visibility"},
    ])

    result = _run_stubbed_scan(tmp_path, project)

    # 1) receipt 挂入 result（scan_result.json 的 result 本体）
    receipt = result.get("verified_archive_receipt")
    assert receipt is not None, "scan result 必须携带 verified_archive_receipt"
    assert receipt["run_delivered"] == 1
    assert receipt["archive_held"] == 2
    assert receipt["total_output"] == 3
    # 2) findings 含 archive_entry 历史保持
    held = [f for f in (result.get("findings") or []) if f.get("archive_entry") is True]
    assert len(held) == 2
    # 3) scan_result.json（分片 store）往返仍含 receipt
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    assert scan_result_path.is_file()
    reloaded = load_scan_result(scan_result_path, keys=["findings", "verified_archive_receipt"])
    assert reloaded.get("verified_archive_receipt") == receipt
    reloaded_held = [f for f in (reloaded.get("findings") or []) if f.get("archive_entry") is True]
    assert len(reloaded_held) == 2
    # 4) intelligence_report 分支保留 receipt（既有契约不回归）
    report_path = tmp_path / "platform_outputs" / project / "intelligence_report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report.get("verified_archive_receipt") == receipt
    # 5) 档案落盘：3 条
    saved = load_verified_discovery_archive(project, tmp_path)
    assert len(saved["entries"]) == 3


def test_scan_mounts_receipt_even_when_save_report_false(tmp_path: Path) -> None:
    project = "scan-chain-noreport"
    _seed_archive(tmp_path, project, [
        {"title": "GET /api/audit/logs exposes cross-tenant rows", "method": "GET", "path": "/api/audit/logs"},
    ])

    result = _run_stubbed_scan(tmp_path, project, save_report=False)

    receipt = result.get("verified_archive_receipt")
    assert receipt is not None, "save_report=False 也必须执行合并链并挂载 receipt"
    assert receipt["archive_held"] == 1
    assert receipt["run_delivered"] == 1
    held = [f for f in (result.get("findings") or []) if f.get("archive_entry") is True]
    assert len(held) == 1
    # scan_result.json 仍写且含 receipt（合并链不依赖 save_report）
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    assert scan_result_path.is_file()
    reloaded = load_scan_result(scan_result_path, keys=["verified_archive_receipt"])
    assert reloaded.get("verified_archive_receipt") == receipt
