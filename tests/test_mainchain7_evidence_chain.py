"""主链 7 (证据链采集) 回归测试。

真实断点：
- A. evidence_id 此前是随机 uuid4，每次扫描都不同 → 同一缺陷无法用稳定 id
  检索/去重其证据链，破坏"复现步骤生成"与 主链 9 回归。
- B. evidence_graphs 只留内存，未落盘 → 无按 evidence_id 可检索的持久化证据链。

本测试证明：同一缺陷签名 → 同一 evidence_id（可复现/可去重）；且证据链按
evidence_id 落盘后可被检索。
"""
from types import SimpleNamespace
import pytest

from ai_test_asset_center.oracle_engine import EvidenceGraphBuilder, OracleResult
from ai_test_asset_center.v12_pipeline import _evidence_chain_path, _persist_evidence_chain


def _oracle(violated_rule="server_5xx", layer="L1", name="HttpStatusOracle"):
    return OracleResult(
        passed=False, oracle_name=name, layer=layer, violated_rule=violated_rule,
        expected="服务应正常响应", actual="HTTP 500", severity="P0",
        confidence=0.95, explanation="5xx",
    )


def _scenario(scenario_id="SCN_1", slice_id="BHV_1"):
    return {
        "id": scenario_id,
        "behavior_slice_id": slice_id,
        "title": "管理员创建用户",
        "steps": [{"method": "POST", "path": "/api/admin/users", "status": 500}],
    }


def test_evidence_id_is_deterministic_for_same_defect():
    """Fix A: 同一缺陷签名（scenario id + slice + violated rules）两次构建
    得到完全相同的 evidence_id —— 可复现、可去重。"""
    trace = {"steps": [{"method": "POST", "path": "/api/admin/users", "status": 500}]}
    b1 = EvidenceGraphBuilder()
    g1 = b1.build(_scenario(), trace, None, [_oracle()])
    b2 = EvidenceGraphBuilder()
    g2 = b2.build(_scenario(), trace, None, [_oracle()])
    assert g1.evidence_id == g2.evidence_id
    assert g1.evidence_id.startswith("EVID_")
    assert len(g1.evidence_id) == 21  # EVID_ + 16 hex


def test_evidence_id_differs_by_violated_rule():
    """Fix A: 不同的 violated_rule → 不同的 evidence_id（不会误合并证据链）。"""
    trace = {"steps": [{"method": "POST", "path": "/api/orders", "status": 500}]}
    g_a = EvidenceGraphBuilder().build(_scenario(), trace, None, [_oracle("server_5xx")])
    g_b = EvidenceGraphBuilder().build(_scenario(), trace, None, [_oracle("wrong_create_status")])
    assert g_a.evidence_id != g_b.evidence_id


def test_evidence_chain_persisted_and_retrievable(tmp_path):
    """Fix B: 证据链按 evidence_id 落盘且可被检索；内容含真实执行 trace。"""
    root = tmp_path / "ws"
    project = "mc7"
    trace = {"steps": [{"method": "POST", "path": "/api/orders", "status": 500}]}
    graph = EvidenceGraphBuilder().build(_scenario(), trace, None, [_oracle()])
    eg = graph.to_dict()
    written = _persist_evidence_chain(root, project, eg)
    assert written
    path = _evidence_chain_path(root, project, eg["evidence_id"])
    assert path.exists()
    import json
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["evidence_id"] == eg["evidence_id"]
    # 真实执行证据随链保存（复现步骤 + 执行 trace）
    assert "/api/orders" in loaded.get("reproduction_steps", "")
    assert loaded.get("execution_trace") == trace


def test_persist_fails_fast_for_evidence_without_id(tmp_path):
    """Fix B: 无 evidence_id 的证据不应写出文件，返回空字符串。"""
    with pytest.raises(ValueError, match="EVIDENCE_ID_MISSING"):
        _persist_evidence_chain(tmp_path / "ws", "p", {"evidence_id": "", "foo": 1})
