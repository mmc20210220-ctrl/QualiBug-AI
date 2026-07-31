from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_test_asset_center.enterprise_knowledge_center import _crud


class _Batch:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.documents = documents
        self.errors = list(errors or [])
        self.warnings: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_count": len(self.documents),
            "package_count": 0,
            "packages": [],
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _registry() -> dict[str, Any]:
    return {
        "sources": [],
        "audit_events": [],
        "governance": {},
    }


def _parsed(source_id: str) -> dict[str, Any]:
    return {
        "text": "business source",
        "parse_status": "complete",
        "parser": "test",
        "document_ir_status": "COMPLETE",
        "document_ir": {"format": "test", "blocks": []},
        "parser_receipt": {"receipt_id": f"receipt-{source_id}"},
        "parse_errors": [],
    }


def _install_storage_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    source_dir = tmp_path / "knowledge_sources"
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(
        _crud,
        "_paths",
        lambda project, root: {
            "source_dir": source_dir,
            "workspace": workspace,
        },
    )
    monkeypatch.setattr(_crud, "_load_registry", lambda project, root: registry)

    def save(project: str, root: Path, value: dict[str, Any]) -> None:
        saved.append(value)

    monkeypatch.setattr(_crud, "_save_registry", save)
    monkeypatch.setattr(
        _crud,
        "read_document_envelope_bytes",
        lambda document: (
            bytes(document["content_bytes"]),
            str(document["filename"]),
            bytes(document["content_bytes"]).decode(),
        ),
    )
    monkeypatch.setattr(
        _crud,
        "_classify_source",
        lambda filename, raw_text, explicit: "prd",
    )
    return saved


def test_batch_parse_failure_activates_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    _install_storage_fakes(monkeypatch, tmp_path, registry)
    documents = [
        {"filename": "one.md", "content_bytes": b"one"},
        {"filename": "two.md", "content_bytes": b"two"},
    ]
    monkeypatch.setattr(
        _crud,
        "expand_document_envelopes",
        lambda documents, package_store_dir: _Batch(list(documents)),
    )
    parse_calls: list[str] = []

    def parse(blob: bytes, filename: str, source_type: str, source_id: str):
        parse_calls.append(filename)
        if filename == "two.md":
            raise ValueError("second source invalid")
        return _parsed(source_id)

    monkeypatch.setattr(_crud, "parse_enterprise_source", parse)
    activation_calls: list[str] = []
    monkeypatch.setattr(
        _crud,
        "_register_runtime_source",
        lambda **kwargs: activation_calls.append(kwargs["source_id"]),
    )

    result = _crud.ingest_enterprise_knowledge_documents(
        "project-a",
        documents,
        root=tmp_path,
        actor={"name": "owner", "role": "project_owner"},
    )
    assert result["ok"] is False
    assert result["transaction_status"] == "BLOCKED"
    assert result["created"] == []
    assert activation_calls == []
    assert parse_calls == ["one.md", "two.md"]


def test_activation_failure_rolls_back_prior_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    _install_storage_fakes(monkeypatch, tmp_path, registry)
    documents = [
        {"filename": "one.md", "content_bytes": b"one"},
        {"filename": "two.md", "content_bytes": b"two"},
    ]
    monkeypatch.setattr(
        _crud,
        "expand_document_envelopes",
        lambda documents, package_store_dir: _Batch(list(documents)),
    )
    monkeypatch.setattr(
        _crud,
        "parse_enterprise_source",
        lambda blob, filename, source_type, source_id: _parsed(source_id),
    )
    activation_count = 0

    def activate(**kwargs: Any) -> dict[str, Any]:
        nonlocal activation_count
        activation_count += 1
        if activation_count == 2:
            raise RuntimeError("second activation failed")
        return {
            "source_id": kwargs["runtime_asset_id"],
            "source_hash": "a" * 64,
            "source_version_id": "v1",
            "status": "REGISTERED",
        }

    monkeypatch.setattr(_crud, "_register_runtime_source", activate)
    monkeypatch.setattr(
        _crud,
        "_register_chunks",
        lambda **kwargs: ({"status": "EMPTY", "chunk_count": 0}, None),
    )
    written: list[Path] = []

    def write(target: Path, blob: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        written.append(target)

    monkeypatch.setattr(_crud, "_write_blob_atomic", write)
    rolled_back: list[str] = []
    monkeypatch.setattr(
        _crud,
        "rollback_source_asset_activation",
        lambda project, source_id, **kwargs: rolled_back.append(source_id)
        or {"rolled_back": True},
    )

    result = _crud.ingest_enterprise_knowledge_documents(
        "project-a",
        documents,
        root=tmp_path,
        actor={"name": "owner", "role": "project_owner"},
    )
    assert result["ok"] is False
    assert result["transaction_status"] == "ROLLED_BACK"
    assert result["created"] == []
    assert len(rolled_back) == 1
    assert all(not path.exists() for path in written)


def test_private_service_composition_root_imports() -> None:
    from ai_test_asset_center import private_pilot_service

    assert private_pilot_service.PrivatePilotHandler is not None
    assert private_pilot_service.QualiBugHTTPServer is not None
