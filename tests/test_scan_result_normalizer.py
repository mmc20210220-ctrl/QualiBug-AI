"""P0-3 unit tests: per-shard redaction + object-tree normalization (referencing).

Covers:
  * redact-per-shard equivalence — new store content == whole-tree
    ``redact_and_validate`` content (including the sealed-ledger authority
    re-derivation path, verified against a real validated ledger);
  * normalization: duplicate-subtree dedup into by-id maps / content-hash
    fallback / sha256 blob store, and hydration restoring the exact tree;
  * sealed ledger interior exclusion (fingerprint contract);
  * identity keys unchanged (finding_id / canonical_defect_id …);
  * caller tree non-mutation; partial loads through whole-value refs;
  * fail-loud on unresolvable refs.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ai_test_asset_center.canonical_defect_registry import (
    CANONICAL_DEFECT_REGISTRY_SCHEMA,
)
from ai_test_asset_center.artifact_redactor import redact_and_validate
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    build_obligation_attempt_ledger,
    validate_obligation_attempt_ledger,
)
from ai_test_asset_center.scan_result_normalizer import (
    BLOB_KEY,
    REF_KEY,
    REGISTRY_KEY,
    ArtifactRegistry,
    blob_to_dotted,
    decode_id,
    encode_id,
    hydrate_refs,
    normalize_scan_result,
    ref_key,
    ref_to_dotted,
)
from ai_test_asset_center.scan_result_store import (
    is_sharded_scan_result,
    load_scan_result,
    shard_specs,
    verify_scan_result_store,
    write_scan_result,
)


# ═════════════════════════════════════════════════════════════════════════
# 引用解析（ID resolution）
# ═════════════════════════════════════════════════════════════════════════

class TestRefResolution:
    def test_semantic_ref_dotted_path(self):
        key = ref_key("findings_by_id", "finding-1/with:odd chars")
        assert ref_to_dotted(key) == (
            f"{REGISTRY_KEY}.entries.findings_by_id."
            f"{encode_id('finding-1/with:odd chars')}"
        )

    def test_content_ref_dotted_path(self):
        assert ref_to_dotted("content:abc123") == (
            f"{REGISTRY_KEY}.entries.content_by_hash.abc123"
        )

    def test_blob_ref_dotted_path(self):
        assert blob_to_dotted("sha256:deadbeef") == (
            f"{REGISTRY_KEY}.blobs.deadbeef"
        )

    def test_id_encoding_roundtrip(self):
        for identifier in ("finding-1", "a/b:c d", "中文id", "x.y.z"):
            assert decode_id(encode_id(identifier)) == identifier


# ═════════════════════════════════════════════════════════════════════════
# 规范化（引用化）
# ═════════════════════════════════════════════════════════════════════════

def _big(payload: str, size: int = 300_000) -> str:
    return (payload * (size // len(payload) + 1))[:size]


def _medium(size: int = 60_000) -> str:
    """中等字符串：> dedup floor、< blob 阈值 → 留在容器内。"""
    return "m" * size


def _dup_tree() -> dict:
    """A tree with heavy nested duplication (the 4GB shape in miniature).

    Runtime receipt trees copy the same object into many positions with
    ``dict(row)``-style independent copies; content-hash dedup must collapse
    them. Each wrapper keeps >= dedup floor of its own content AFTER inner
    dedup (medium fields stay inline; only >= 256KB strings move to blobs),
    which is the real run16 shape (e.g. ~1MB experiments).
    """

    def _finding(fid: str) -> dict:
        return {
            "finding_id": fid,
            "canonical_defect_id": "cd-" + fid,
            "title": "权限绕过",
            "evidence": {
                "evidence_id": "ev-" + fid,
                "body": _big("HTTP evidence " + fid),
                "meta_a": _medium(),
                "meta_b": _medium(),
            },
            "detail_a": _medium(),
            "detail_b": _medium(),
            "detail_c": _medium(),
        }

    finding = _finding("f-1")
    execution = {
        "execution_id": "exec-1",
        "obligation_id": "obl-1",
        "status": "EXECUTED",
        "finding": dict(finding),
        "execution_receipt": {"receipt_id": "er-1", "body": _big("execution receipt detail ")},
        "trace_a": _medium(),
        "trace_b": _medium(),
        "trace_c": _medium(),
    }
    gate = {
        "gate_receipt_id": "gate-1",
        "status": "DELIVERABLE",
        "finding_id": "f-1",
        "detail": _big("gate evidence detail "),
        "note_a": _medium(),
        "note_b": _medium(),
    }
    experiment = {
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "compile_receipt": {"status": "COMPILED", "body": _big("compile detail ")},
        "param_a": _medium(),
        "param_b": _medium(),
        "param_c": _medium(),
    }
    return {
        "mainline_run": {"run_id": "RUN-1", "campaign_id": "CMP-1"},
        "findings": [dict(finding), dict(finding), dict(finding)],   # 同一 finding 3 份独立副本
        "delivery_occurrences": [dict(finding), dict(gate)],
        "experiment_execution": {"results": [dict(execution), dict(execution)]},
        "gate_results": {"obl-1": dict(gate)},
        "v12": {
            "experiments": {
                "by_obligation": {"obl-1": dict(experiment)},
                "all_experiments": [dict(experiment)],
            },
            "experiment_compile": {"experiments": [dict(experiment)]},
            "execution": [dict(execution)],
        },
        "big_string": _big("unique big captured response body "),
        "big_string_dup": _big("unique big captured response body "),
    }


class TestNormalizeAndHydrate:
    def test_dedup_registry_and_hydration_roundtrip(self):
        tree = _dup_tree()
        registry = normalize_scan_result(tree)
        assert registry.stats["registered"] >= 4
        assert registry.stats["duplicates"] >= 5
        # by-id maps populated with product identity keys (b64-encoded ids)
        assert "findings_by_id" in registry.entries
        assert encode_id("f-1") in registry.entries["findings_by_id"]
        assert encode_id("exec-1") in registry.entries["executions_by_id"]
        assert encode_id("exp-1") in registry.entries["experiments_by_id"]
        assert encode_id("ev-f-1") in registry.entries["evidence_by_id"]
        assert encode_id("gate-1") in registry.entries["gate_receipts_by_id"]
        # sha256 blob store holds the repeated big strings once
        assert registry.stats["blobs"] >= 1
        # every duplicate position became a ref marker
        refs: list[str] = []

        def count_refs(node):
            if isinstance(node, dict):
                if len(node) == 1 and REF_KEY in node:
                    refs.append(node[REF_KEY])
                for child in node.values():
                    count_refs(child)
            elif isinstance(node, list):
                for child in node:
                    count_refs(child)

        count_refs(tree)
        assert len(refs) >= 8
        # hydration restores the exact original tree
        hydrated = hydrate_refs(copy.deepcopy(tree), registry.resolver())
        original = _dup_tree()
        assert hydrated == original
        # identity keys unchanged
        assert hydrated["findings"][0]["finding_id"] == "f-1"
        assert hydrated["findings"][0]["canonical_defect_id"] == "cd-f-1"

    def test_content_hash_fallback_for_id_collision(self):
        # 同一 finding_id、不同内容 → 语义键冲突时落入 content_by_hash（不吞身份冲突）
        tree = {
            "one": {"finding_id": "same", "payload": _big("A", 200_000)},
            "two": {"finding_id": "same", "payload": _big("B", 200_000)},
        }
        registry = normalize_scan_result(tree)
        assert encode_id("same") in registry.entries["findings_by_id"]
        assert len(registry.entries["content_by_hash"]) >= 1
        # hydration 后两个位置各自恢复原内容
        hydrated = hydrate_refs(copy.deepcopy(tree), registry.resolver())
        assert hydrated["one"]["payload"] != hydrated["two"]["payload"]
        assert hydrated["two"]["finding_id"] == "same"

    def test_ledger_interior_excluded(self):
        ledger = {
            "schema_version": "qualibug.obligation-attempt-ledger.v1",
            "run_id": "RUN-1",
            "campaign_id": "CMP-1",
            "attempts": [
                {
                    "obligation_id": "obl-1",
                    "terminal_status": "BLOCKED",
                    "delivery_evidence_bundle": {
                        "finding": {"finding_id": "f-1", "payload": _big("dup evidence ")},
                    },
                },
                {
                    "obligation_id": "obl-2",
                    "terminal_status": "BLOCKED",
                    "delivery_evidence_bundle": {
                        "finding": {"finding_id": "f-1", "payload": _big("dup evidence ")},
                    },
                },
            ],
            "ledger_fingerprint": "fp",
        }
        tree = {"obligation_attempt_ledger": ledger}
        registry = normalize_scan_result(tree)
        # ledger 内部（含重复的 evidence）不被引用化
        assert tree["obligation_attempt_ledger"]["attempts"][0][
            "delivery_evidence_bundle"
        ]["finding"]["finding_id"] == "f-1"
        assert "f-1" not in registry.entries["findings_by_id"]
        assert registry.entries["content_by_hash"] == {}

    def test_blob_ref_and_hydration(self):
        tree = {"body": _big("captured response body ")}
        registry = normalize_scan_result(tree)
        assert len(registry.blobs) == 1
        marker = tree["body"]
        assert isinstance(marker, dict) and BLOB_KEY in marker
        hydrated = hydrate_refs(copy.deepcopy(tree), registry.resolver())
        assert hydrated["body"] == _big("captured response body ")


# ═════════════════════════════════════════════════════════════════════════
# 写读等价：逐片 redact == 整树 redact（含 ledger authority 重建路径）
# ═════════════════════════════════════════════════════════════════════════

def _mainline_contract() -> dict:
    return dict(
        build_mainline_run_contract(
            mainline_authority="experiment_candidate",
            run_id="RUN-1",
            campaign_id="CMP-1",
            target_id="T-1",
            environment_id="env-test",
            policy_version="p-1",
            evaluation_mode="operational",
            source_snapshot_hash="snap-1",
        )
    )


def _valid_ledger() -> dict:
    """Build a fully validated sealed ledger (REJECTED terminal)."""
    return build_obligation_attempt_ledger(
        mainline_run=_mainline_contract(),
        selected=[
            {"obligation_id": "obl-1", "candidate_id": "cand-1"},
            {"obligation_id": "obl-2", "candidate_id": "cand-2"},
        ],
        compile_results={
            "obl-1": {"status": "COMPILED", "experiment_id": "exp-1"},
            "obl-2": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "receipt_id": "compile-receipt-2",
            },
        },
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "execution_id": "exec-1",
                "observation_receipt_ids": ["obs-1"],
                "oracle_receipt_id": "oracle-1",
                "elapsed_ms": 12,
            },
        },
        gate_results={
            "obl-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
                "gate_receipt_id": "gate-1",
            },
        },
    )


def _envelope_result() -> dict:
    return {
        "schema_version": "qualibug.discovery-runtime.v1",
        "mainline_run": _mainline_contract(),
        "obligation_attempt_ledger": _valid_ledger(),
        "delivery_occurrences": [],
        "canonical_defect_registry": {
            "schema_version": CANONICAL_DEFECT_REGISTRY_SCHEMA,
            "canonical_defect_ids": [],
            "delivery_occurrence_finding_ids": [],
        },
        "formal_delivery_authority": {"schema_version": "stale"},
        "findings": [],
        "candidate_findings": [],
    }


class TestWriteReadEquivalence:
    def test_redact_per_shard_equals_whole_tree(self, tmp_path):
        """非 envelope 载荷：分片先行逐片 redact 的内容 == 整树 redact。"""
        payload = _dup_tree()
        expected = redact_and_validate(copy.deepcopy(payload))[0]
        index = tmp_path / "scan_result.json"
        write_scan_result(index, payload, threshold_bytes=64 * 1024)
        assert is_sharded_scan_result(index)
        assert verify_scan_result_store(index, check_sha256=True)["valid"] is True
        loaded = load_scan_result(index)
        assert loaded == expected
        # 调用方树未被改动（规范化在副本上进行）
        assert payload["findings"][0]["finding_id"] == "f-1"
        assert isinstance(payload["findings"][1], dict)
        assert "evidence_id" in payload["findings"][0]["evidence"]

    def test_envelope_authority_rebuild_equals_whole_tree(self, tmp_path):
        """密封 ledger + authority 指纹链：逐片流程重建结果 == 整树流程。"""
        payload = _envelope_result()
        expected = redact_and_validate(copy.deepcopy(payload))[0]
        index = tmp_path / "scan_result.json"
        write_scan_result(index, payload, threshold_bytes=1024)
        loaded = load_scan_result(index)
        assert loaded == expected
        # 重建产物就位且指纹链仍可验证（fail-closed 不放松）
        assert loaded["formal_delivery_authority"]["schema_version"] != "stale"
        validate_obligation_attempt_ledger(loaded["obligation_attempt_ledger"])
        # 身份键不变
        assert loaded["obligation_attempt_ledger"]["campaign_id"] == "CMP-1"

    def test_envelope_equivalence_when_fully_inline(self, tmp_path):
        """全部内联（无分片）时骨架 redaction 重建与逐片重建结果一致。"""
        payload = _envelope_result()
        expected = redact_and_validate(copy.deepcopy(payload))[0]
        index = tmp_path / "scan_result.json"
        write_scan_result(index, payload, threshold_bytes=4 * 1024 * 1024)
        loaded = load_scan_result(index)
        assert loaded == expected
        assert shard_specs(index) == {}

    def test_partial_load_hydrates_refs(self, tmp_path):
        payload = _dup_tree()
        index = tmp_path / "scan_result.json"
        write_scan_result(index, payload, threshold_bytes=64 * 1024)
        result = _dup_tree()
        # 部分加载：findings 列表中的 finding 是引用 → 自动 hydrate 为完整内容
        findings = load_scan_result(index, keys=["findings"])["findings"]
        assert findings == result["findings"]
        delivery = load_scan_result(index, keys=["delivery_occurrences"])
        assert delivery["delivery_occurrences"] == result["delivery_occurrences"]
        assert "gate_receipt_id" in delivery["delivery_occurrences"][1]

    def test_unresolvable_ref_fails_loud(self, tmp_path):
        payload = _dup_tree()
        index = tmp_path / "scan_result.json"
        write_scan_result(index, payload, threshold_bytes=64 * 1024)
        # 破坏 registry：删除一个分片 → 全量加载 fail-loud
        parts = tmp_path / "scan_result.parts"
        for shard in parts.glob("_artifact_registry_entries_*.json"):
            shard.unlink()
            break
        with pytest.raises((FileNotFoundError, ValueError)):
            load_scan_result(index)

    def test_sealed_ledger_survives_as_atomic_piece(self, tmp_path):
        """密封 ledger 不被引用化、作为原子分片，加载后指纹可验证。"""
        payload = _envelope_result()
        index = tmp_path / "scan_result.json"
        write_scan_result(index, payload, threshold_bytes=1024)
        specs = shard_specs(index)
        # ledger 是整体分片（含 attempts），而不是 attempts 单独分片
        ledger_dots = [d for d in specs if "obligation_attempt_ledger" in d]
        assert any(d == "obligation_attempt_ledger" for d in ledger_dots)
        loaded = load_scan_result(index)
        ledger = loaded["obligation_attempt_ledger"]
        validate_obligation_attempt_ledger(ledger)
        # 内部无引用标记（指纹封印契约）
        raw = json.dumps(ledger, ensure_ascii=False)
        assert REF_KEY not in raw and BLOB_KEY not in raw

    def test_non_dict_fallback_and_legacy_unchanged(self, tmp_path):
        path = tmp_path / "arr.json"
        write_scan_result(path, [1, 2, 3])
        assert json.loads(path.read_text(encoding="utf-8")) == [1, 2, 3]
        legacy = tmp_path / "legacy.json"
        payload = {"success": True, "findings": [{"id": 1}]}
        legacy.write_text(json.dumps(payload), encoding="utf-8")
        assert load_scan_result(legacy) == payload
