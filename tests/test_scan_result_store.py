"""Unit tests for the sharded scan_result store (scan_result 产物膨胀治理).

Covers: sharded write / compatible load (legacy + sharded) / keys streaming API /
index update (customer-ready style) / converter (legacy -> sharded) / integrity
verify / fail-loud on missing shards / consumer compatibility (compute_benchmark
via store-loaded findings, miss_diagnosis envelope loading).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from ai_test_asset_center.scan_result_store import (
    DEFAULT_SHARD_THRESHOLD_BYTES,
    SHARD_MARKER,
    SCAN_RESULT_SHARD_SCHEMA,
    is_sharded_scan_result,
    load_scan_result,
    shard_legacy_scan_result,
    shard_specs,
    update_scan_result_index,
    verify_scan_result_store,
    write_scan_result,
)


@pytest.fixture()
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "store"


def _build_result() -> dict:
    big_list = [{"i": i, "blob": "x" * 200} for i in range(30)]
    big_dict = {"k%d" % i: {"payload": "y" * 150} for i in range(30)}
    nested = {"outer": {"small": 1, "huge": big_dict, "arr": big_list}}
    return {
        "success": True,
        "score": 0.85,
        "total_findings": 74,
        "layers": {"source_grounded_discovery": {"findings": 74}},
        "findings": [{"title": "f%d" % i, "data": "z" * 120} for i in range(20)],
        "candidate_findings": [{"title": "c0", "data": "w" * 120}],
        "v12": nested,
        "obligation_attempt_ledger": {"a%d" % i: {"v": i} for i in range(2000)},
        "delivery_occurrences": [{"id": "O%d" % i, "s": "q" * 80} for i in range(30)],
    }


def _write_store(tmp_path: Path, threshold: int = 100) -> Path:
    index = tmp_path / "scan_result.json"
    write_scan_result(index, _build_result(), threshold_bytes=threshold)
    return index


class TestShardedWriteAndLoad:
    def test_roundtrip_equal(self, tmp_path):
        index = _write_store(tmp_path)
        assert is_sharded_scan_result(index)
        assert load_scan_result(index) == _build_result()

    def test_verify_integrity(self, tmp_path):
        index = _write_store(tmp_path)
        report = verify_scan_result_store(index, check_sha256=True)
        assert report["valid"] is True
        assert report["legacy"] is False
        assert report["shard_count"] >= 4

    def test_manifest_schema(self, tmp_path):
        index = _write_store(tmp_path)
        specs = shard_specs(index)
        assert specs
        for dotted, spec in specs.items():
            assert spec["file"].endswith(".json")
            assert spec["bytes"] > 0
            assert len(spec["sha256"]) == 64
        payload = json.loads(index.read_text(encoding="utf-8"))
        assert payload[SHARD_MARKER]["schema_version"] == SCAN_RESULT_SHARD_SCHEMA

    def test_small_result_stays_single_file_semantics(self, tmp_path):
        index = tmp_path / "scan_result.json"
        small = {"success": True, "score": 0.5, "findings": []}
        write_scan_result(index, small, threshold_bytes=4 * 1024 * 1024)
        assert is_sharded_scan_result(index)
        assert load_scan_result(index) == small
        assert shard_specs(index) == {}

    def test_keys_streaming_api(self, tmp_path):
        index = _write_store(tmp_path)
        result = _build_result()
        partial = load_scan_result(index, keys=["findings", "v12.outer.small"])
        assert partial["findings"] == result["findings"]
        assert partial["v12"]["outer"]["small"] == 1
        # 未请求的分片保持 null 占位（fail-loud，不静默缺失）
        assert partial["v12"]["outer"]["huge"]["k0"] is None
        assert partial["v12"]["outer"]["arr"] is None
        # P0-3：整体注册的子树在骨架中保持轻量引用标记（未加载，但可解析）；
        # 请求该键时解析为完整内容（与旧格式一致）。
        ledger_slot = partial["obligation_attempt_ledger"]
        assert ledger_slot is None or (
            isinstance(ledger_slot, dict)
            and ledger_slot.get("$qualibug_artifact_ref")
        )
        full_ledger = load_scan_result(index, keys=["obligation_attempt_ledger"])
        assert full_ledger["obligation_attempt_ledger"] == result["obligation_attempt_ledger"]

    def test_keys_deep_leaf_and_whole_shard(self, tmp_path):
        index = _write_store(tmp_path)
        result = _build_result()
        deep = load_scan_result(index, keys=["v12.outer.huge.k3"])
        assert deep["v12"]["outer"]["huge"]["k3"] == result["v12"]["outer"]["huge"]["k3"]
        whole = load_scan_result(index, keys=["delivery_occurrences"])
        assert whole["delivery_occurrences"] == result["delivery_occurrences"]

    def test_index_update_keeps_shards(self, tmp_path):
        index = _write_store(tmp_path)
        result = _build_result()
        update_scan_result_index(
            index,
            {"customer_ready_snapshot": {"defects": 3}, "customer_ready_defect_count": 3},
        )
        loaded = load_scan_result(index)
        assert loaded == {
            **result,
            "customer_ready_snapshot": {"defects": 3},
            "customer_ready_defect_count": 3,
        }
        assert verify_scan_result_store(index, check_sha256=True)["valid"] is True

    def test_missing_shard_fails_loud(self, tmp_path):
        index = _write_store(tmp_path)
        parts = tmp_path / "scan_result.parts"
        for shard in parts.glob("*.json"):
            shard.unlink()
            break
        with pytest.raises((FileNotFoundError, ValueError)):
            load_scan_result(index)


class TestLegacyCompatibility:
    def test_legacy_single_file(self, tmp_path):
        legacy = tmp_path / "scan_result.json"
        payload = {"success": True, "findings": [{"id": 1}], "v12": {"small": 1}}
        legacy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert is_sharded_scan_result(legacy) is False
        assert load_scan_result(legacy) == payload

    def test_legacy_with_marker_literal_in_data(self, tmp_path):
        # 数据字符串里出现分片标记字面量不应误判为分片 store
        legacy = tmp_path / "scan_result.json"
        payload = {"success": True, "note": 'contains "_scan_result_shards" text only'}
        legacy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert is_sharded_scan_result(legacy) is False
        assert load_scan_result(legacy) == payload

    def test_non_dict_fallback(self, tmp_path):
        path = tmp_path / "arr.json"
        write_scan_result(path, [1, 2, 3])
        assert json.loads(path.read_text(encoding="utf-8")) == [1, 2, 3]


class TestLegacyConverter:
    def test_converter_roundtrip(self, tmp_path):
        index = tmp_path / "scan_result.json"
        original = _build_result()
        index.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
        result = shard_legacy_scan_result(index, threshold_bytes=100)
        assert result["status"] == "sharded"
        assert (tmp_path / "scan_result.json.legacy").exists()
        assert is_sharded_scan_result(index)
        assert verify_scan_result_store(index, check_sha256=True)["valid"] is True
        assert load_scan_result(index) == original

    def test_converter_already_sharded(self, tmp_path):
        index = _write_store(tmp_path)
        result = shard_legacy_scan_result(index)
        assert result["status"] == "already_sharded"

    def test_converter_no_backup(self, tmp_path):
        index = tmp_path / "scan_result.json"
        index.write_text(json.dumps(_build_result()), encoding="utf-8")
        shard_legacy_scan_result(index, threshold_bytes=100, keep_legacy=False)
        assert not (tmp_path / "scan_result.json.legacy").exists()


class TestConsumerCompatibility:
    def test_compute_benchmark_with_store_findings(self, tmp_path):
        """compute_benchmark 收 findings/candidates 参数：store 加载后接入评分。"""
        from benchmark_evaluator.benchmark_compute import compute_benchmark

        index = _write_store(tmp_path)
        result = _build_result()
        scan = load_scan_result(index, keys=["findings", "candidate_findings"])
        assert scan["findings"] == result["findings"]
        # 无 GT：走非造数据覆盖矩阵分支（不崩溃、不造假 recall）
        metrics = compute_benchmark(
            "native_stable_e2e",
            scan["findings"],
            scan["candidate_findings"],
            root=tmp_path,
            ground_truth_path="",
        )
        assert metrics["benchmark_active"] is False
        assert isinstance(metrics["coverage_matrix"], dict)

    def test_miss_diagnosis_envelope_loader(self, tmp_path):
        """tools/miss_diagnosis._load_scan_result 对分片 store 可加载。"""
        from tools.miss_diagnosis import _load_scan_result

        index = _write_store(tmp_path)
        scan = _load_scan_result(index)
        assert scan["findings"] == _build_result()["findings"]

    def test_private_pilot_json_io_helpers(self, tmp_path):
        """private_pilot 报告加载链（_read_json_object）自动组装分片 store。"""
        from ai_test_asset_center.private_pilot_json_io import _read_json_object

        index = _write_store(tmp_path)
        payload = _read_json_object(index)
        assert payload == _build_result()
        # 普通 JSON 文件行为不变
        plain = tmp_path / "plain.json"
        plain.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert _read_json_object(plain) == {"a": 1}


class TestPostWriteSkeletonAttach:
    """Post-hook proof attach must not re-copy or destroy the sharded result."""

    def test_attach_skeleton_keys_preserves_shards(self, tmp_path):
        from ai_test_asset_center.scan_result_store import (
            attach_skeleton_keys,
            load_scan_result,
            write_scan_result,
        )

        result = {
            "project": "p",
            "findings": [{"finding_id": "F-1", "title": "t", "evidence": {"request": "x" * 20000}}],
        }
        target = tmp_path / "scan_result.json"
        write_scan_result(target, result, threshold_bytes=1024)
        parts = tmp_path / "scan_result.parts"
        shard_count = len(list(parts.glob("*.json")))
        assert shard_count >= 1

        # Post-hook attach: skeleton-only, shards untouched.
        attach_skeleton_keys(target, {"job_planning_proof": {"proof_id": "proof-1"}, "job_planning_proof_ref": "r"})
        assert len(list(parts.glob("*.json"))) == shard_count

        loaded = load_scan_result(target)
        assert loaded.get("job_planning_proof", {}).get("proof_id") == "proof-1"
        assert loaded.get("job_planning_proof_ref") == "r"
        assert loaded["findings"][0]["finding_id"] == "F-1"

    def test_rewrite_keeps_manifest_shards_cleans_stale(self, tmp_path):
        """A second full write to an existing sharded store is refused
        (write-once invariant); stale shards from an older run are removed
        after the write."""
        from ai_test_asset_center.artifact_redactor import ArtifactSecretLeakError
        from ai_test_asset_center.scan_result_store import (
            load_scan_result,
            write_scan_result,
        )

        result = {
            "project": "p",
            "findings": [{"finding_id": "F-1", "title": "t", "evidence": {"request": "x" * 20000}}],
        }
        target = tmp_path / "scan_result.json"
        # Simulate a stale shard from an older run before the write: the
        # post-write cleanup must remove it (cross-run residue zeroing).
        parts = tmp_path / "scan_result.parts"
        parts.mkdir(parents=True, exist_ok=True)
        stale = parts / "stale_old_run.json"
        stale.write_text("{}", encoding="utf-8")
        write_scan_result(target, result, threshold_bytes=1024)
        names = {p.name for p in parts.glob("*.json")}
        assert "stale_old_run.json" not in names
        assert len(names) >= 1
        count_after_first = len(names)

        # Write-once invariant: a further full rewrite is refused instead of
        # destroying the shard set (run25c regression).
        with pytest.raises(ArtifactSecretLeakError, match="already_sharded_refuse_rewrite"):
            write_scan_result(target, dict(result), threshold_bytes=1024)
        loaded = load_scan_result(target)
        assert loaded["findings"][0]["finding_id"] == "F-1"


class TestCompiledPayloadPruning:
    """Compiled experiment/obligation plan snapshots are pruned on the
    persisted copy (identity rows only), while execution records survive."""

    def test_prune_compiled_experiments_shrinks_payload(self, tmp_path):
        from ai_test_asset_center.scan_result_store import (
            _prune_compiled_experiment_payload,
        )

        def big_experiment(i):
            return {
                "experiment_id": f"exp_{i}", "obligation_id": f"obl_{i}",
                "risk_family": "authorization", "compile_status": "COMPILED",
                "status": "selected",
                "control_plan": [{"path": "/x", "body": "y" * 2000}],
                "treatment_plan": [{"path": "/x", "body": "y" * 2000}],
                "binding_plan": [{"target": "id", "resolver": "x" * 500} for _ in range(10)],
                "fixture_dag": {"nodes": [{"node_id": "n1"}]},
                "assertions": [{"kind": "validation_rejection"} for _ in range(8)],
            }

        result = {"v12": {
            "experiments": {"all_experiments": [big_experiment(i) for i in range(5)],
                            "experiments": [big_experiment(i) for i in range(5)]},
            "experiment_compile": {"all_experiments": [big_experiment(i) for i in range(5)]},
            "experiments_by_obligation": {f"obl_{i}": big_experiment(i) for i in range(5)},
            "obligations": {"obligations": [
                {"obligation_id": f"o{i}", "risk_family": "x", "property": {"big": "z" * 2000}}
                for i in range(5)]},
            "obligation_attempt_ledger": {"attempts": [{"candidate_id": "c1", "status": "EXECUTED"}]},
            "execution_results": {"executed": [{"experiment_id": "e1", "finding": {"evidence": "x" * 500}}]},
        }}
        import json
        before = len(json.dumps(result))
        _prune_compiled_experiment_payload(result)
        after = len(json.dumps(result))
        assert after < before
        # Identity + plan-size counts survive; heavy plans are dropped.
        e = result["v12"]["experiments"]["all_experiments"][0]
        assert e["experiment_id"] == "exp_0"
        assert e["binding_plan_length"] == 10
        assert e["assertion_count"] == 8
        assert "binding_plan" not in e and "control_plan" not in e
        # Execution records untouched.
        assert len(result["v12"]["obligation_attempt_ledger"]["attempts"]) == 1
        assert len(result["v12"]["execution_results"]["executed"]) == 1

    def test_prune_leaves_non_compiled_v12_intact(self, tmp_path):
        from ai_test_asset_center.scan_result_store import (
            _prune_compiled_experiment_payload,
        )

        result = {"v12": {
            "obligation_attempt_ledger": {"attempts": [{"candidate_id": "c1"}]},
            "execution_results": {"executed": [{"experiment_id": "e1"}]},
            "behavior_slice_ledger": {"slices": [{"slice_id": "s1"}]},
        }}
        snapshot = dict(result["v12"])
        _prune_compiled_experiment_payload(result)
        assert result["v12"]["obligation_attempt_ledger"] == snapshot["obligation_attempt_ledger"]
        assert result["v12"]["execution_results"] == snapshot["execution_results"]
        assert result["v12"]["behavior_slice_ledger"] == snapshot["behavior_slice_ledger"]
