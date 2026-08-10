"""Unit tests for the content-addressed artifact store (SPEC P0-4 §41).

Covers Test 1 (same content -> same id + one physical object), Test 2
(canonical JSON key-order invariance), Test 3 (compression roundtrip), Test 4
(concurrent identical writes -> one physical object), Test 10 (knowledge.db
never touched by store/GC operations), plus streaming put_file for large
files, semantic-bytes preservation, sidecar metadata and delete/list.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_store import (
    ArtifactStoreError,
    LocalArtifactStore,
    canonical_json_bytes,
    parse_artifact_id,
)


@pytest.fixture(params=["zstd", "none"])
def store(tmp_path: Path, request) -> LocalArtifactStore:
    return LocalArtifactStore(
        tmp_path / "qualibug", compression=request.param
    )


def _physical_object_count(store: LocalArtifactStore) -> int:
    return len(store.list_all())


class TestContentAddressing:
    def test_1_same_content_puts_same_id_one_object(self, store):
        ref1 = store.put({"payload": "x" * 5000}, "EXECUTION_OUTPUT")
        ref2 = store.put({"payload": "x" * 5000}, "EXECUTION_OUTPUT")
        assert ref1.artifact_id == ref2.artifact_id
        assert ref1.artifact_id.startswith("sha256:")
        assert len(ref1.artifact_id) == 7 + 64
        assert _physical_object_count(store) == 1
        stats = store.snapshot_stats()
        assert stats["artifact_new_count"] == 1
        assert stats["artifact_reused_count"] == 1

    def test_2_json_key_order_same_id(self, store):
        ref1 = store.put({"a": 1, "b": {"nested": [1, 2]}}, "EXECUTION_OUTPUT")
        ref2 = store.put({"b": {"nested": [1, 2]}, "a": 1}, "EXECUTION_OUTPUT")
        assert ref1.artifact_id == ref2.artifact_id
        assert store.get(ref1.artifact_id) == canonical_json_bytes(
            {"a": 1, "b": {"nested": [1, 2]}}
        )
        assert _physical_object_count(store) == 1

    def test_json_canonical_is_not_silent_on_semantic_bytes(self, store):
        # SPEC §9: HTTP raw bodies keep original bytes — a differently-spaced
        # byte payload must NOT be rewritten into canonical JSON.
        raw = b'{"b": 2, "a":  1}'
        ref = store.put(raw, "HTTP_RESPONSE")
        assert store.get(ref.artifact_id) == raw
        ref2 = store.put(b'{"a": 1, "b": 2}', "HTTP_RESPONSE")
        assert ref2.artifact_id != ref.artifact_id

    def test_3_compression_roundtrip(self, store):
        original = ("重复的证据内容" * 2000).encode("utf-8") + os.urandom(64)
        ref = store.put(original, "TRACE_LEDGER")
        assert store.get(ref.artifact_id) == original
        meta = store.metadata(ref.artifact_id)
        assert meta.original_size == len(original)
        if store.compression == "zstd":
            assert meta.stored_size < len(original)
            assert meta.compression == "zstd"
        else:
            assert meta.stored_size == len(original)
            assert meta.compression == "none"
        # stat exposes the same identity
        stat = store.stat(ref.artifact_id)
        assert stat.size_bytes == len(original)
        assert stat.compressed_size_bytes == meta.stored_size


class TestConcurrency:
    def test_4_concurrent_identical_puts_single_object(self, tmp_path):
        store = LocalArtifactStore(tmp_path / "qualibug", compression="zstd")
        content = {"same": "content", "blob": "z" * 20000}
        refs: list[str] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                refs.append(store.put(content, "EXECUTION_OUTPUT").artifact_id)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert len(set(refs)) == 1
        assert _physical_object_count(store) == 1
        assert store.get(refs[0]) == canonical_json_bytes(content)


class TestPutFileStreaming:
    def test_put_file_large_roundtrip_and_dedup(self, tmp_path):
        store = LocalArtifactStore(tmp_path / "qualibug", compression="zstd")
        source = tmp_path / "large.bin"
        # 12 MiB — larger than any single-buffer assumption; streamed paths
        # must never load it whole (read chunk sizes are capped at 1 MiB).
        source.write_bytes(os.urandom(12 * 1024 * 1024))
        expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        ref = store.put_file(source, "SCREENSHOT")
        assert ref.content_hash == expected_hash
        assert store.get(ref.artifact_id) == source.read_bytes()
        ref2 = store.put_file(source, "SCREENSHOT")
        assert ref2.artifact_id == ref.artifact_id
        assert _physical_object_count(store) == 1

    def test_put_file_missing_source_raises(self, tmp_path):
        store = LocalArtifactStore(tmp_path / "qualibug", compression="zstd")
        with pytest.raises(ArtifactStoreError):
            store.put_file(tmp_path / "nope.bin", "SCREENSHOT")


class TestMetadataAndLifecycle:
    def test_sidecar_metadata_fields(self, store):
        ref = store.put({"k": "v"}, "METADATA")
        meta = store.metadata(ref.artifact_id)
        assert meta.artifact_id == ref.artifact_id
        assert meta.artifact_type == "METADATA"
        assert meta.content_hash == parse_artifact_id(ref.artifact_id)
        assert meta.original_size > 0
        assert meta.stored_size > 0
        assert meta.created_at

    def test_delete_and_exists(self, store):
        ref = store.put(b"data", "LOG_SEGMENT")
        assert store.exists(ref.artifact_id)
        store.delete(ref.artifact_id)
        assert not store.exists(ref.artifact_id)
        with pytest.raises(ArtifactStoreError):
            store.stat(ref.artifact_id)

    def test_invalid_artifact_id_fails_loud(self, store):
        with pytest.raises(ArtifactStoreError):
            store.get("not-an-id")
        with pytest.raises(ArtifactStoreError):
            store.get("sha256:zz")
        assert store.exists("../escape") is False

    def test_10_knowledge_db_untouched(self, tmp_path):
        store = LocalArtifactStore(tmp_path / "qualibug", compression="zstd")
        store.put(b"payload", "EXECUTION_OUTPUT")
        knowledge = tmp_path / "platform_outputs" / "proj" / "knowledge.db"
        knowledge.parent.mkdir(parents=True)
        knowledge.write_bytes(b"SQLITE-CONTENT")
        # list/delete/GC-family operations only ever see store-internal ids.
        for artifact_id in store.list_all():
            store.delete(artifact_id)
        assert knowledge.read_bytes() == b"SQLITE-CONTENT"
        assert not store.list_all()

    def test_open_streams_decompressed_content(self, store):
        original = b"stream me " * 5000
        ref = store.put(original, "EXECUTION_OUTPUT")
        with store.open(ref.artifact_id) as handle:
            assert handle.read() == original

    def test_put_string_content_verbatim(self, store):
        ref = store.put("plain text 中文", "LOG_SEGMENT")
        assert store.get(ref.artifact_id) == "plain text 中文".encode("utf-8")
