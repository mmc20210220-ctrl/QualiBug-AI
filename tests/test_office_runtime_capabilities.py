from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    compatible_office_adapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.compatible_office_adapter import (
    NormalizedOfficeContainer,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.office_runtime_capabilities import (
    OFFICE_RUNTIME_CAPABILITY_SCHEMA,
    OFFICE_SOURCE_RUNTIME_PROBE_SCHEMA,
    RuntimeAwareCompatibleOfficeDocumentAdapter,
    build_compatible_office_runtime_report,
    probe_compatible_office_source_runtime,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.registry import (
    build_default_registry,
)


class FakeNormalizer:
    name = "fake-office-normalizer"
    version = "7"

    def __init__(self, available: bool) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available

    def normalize(self, source, target_suffix):  # pragma: no cover
        raise AssertionError("readiness reporting must not execute source conversion")


class WorkingNormalizer(FakeNormalizer):
    def __init__(self) -> None:
        super().__init__(True)

    def normalize(self, source, target_suffix):
        return NormalizedOfficeContainer(
            data=b"derived-ooxml",
            filename="需求.docx",
            target_suffix=".docx",
            receipt={
                "schema": "qualibug.office-container-normalization-receipt.v1",
                "status": "COMPLETE",
                "normalizer_name": self.name,
                "normalizer_version": self.version,
                "source_filename": source.filename,
                "source_format": source.suffix.lstrip("."),
                "source_hash": source.content_hash,
                "target_format": "docx",
                "derived_filename": "需求.docx",
                "derived_hash": "derived-hash",
                "network_access_used": False,
                "derived_container_is_not_evidence_root": True,
            },
        )


class FakeDocxDelegate:
    def extract(self, source):
        return {
            "schema": "qualibug.document-ir.v1",
            "format": "docx",
            "filename": source.filename,
            "plain_text": "审批规则",
            "blocks": [
                {
                    "block_id": "derived-block",
                    "type": "PARAGRAPH",
                    "parent_id": "",
                    "order": 1,
                    "region": "body",
                    "text": "审批规则",
                    "source_locator": f"{source.filename}#paragraph=1",
                }
            ],
            "sections": [],
            "tables": [],
            "pages": [],
            "unsupported_content": [],
            "structure_receipt": {
                "status": "COMPLETE",
                "format": "docx",
                "unsupported_content": [],
            },
        }


def _row(report: dict, suffix: str) -> dict:
    return next(row for row in report["formats"] if row["source_suffix"] == suffix)


def test_runtime_report_never_confuses_recognition_with_verified_conversion() -> None:
    report = build_compatible_office_runtime_report(FakeNormalizer(True))

    assert report["schema"] == OFFICE_RUNTIME_CAPABILITY_SCHEMA
    assert report["status"] == "READY"
    assert report["recognition_is_not_conversion_verification"] is True
    assert report["real_source_verified_format_count"] == 0
    assert report["network_isolation_enforced"] is False
    for suffix in (".wps", ".et", ".dps", ".xlsb", ".doc", ".xls", ".ppt"):
        row = _row(report, suffix)
        assert row["runtime_status"] == "READY_FOR_SOURCE_CONVERSION"
        assert row["format_conversion_verified_with_real_source"] is False
        assert row["original_source_remains_evidence_root"] is True
        assert row["derived_container_is_evidence_root"] is False


def test_runtime_report_blocks_all_conversion_claims_when_dependency_is_missing() -> None:
    report = build_compatible_office_runtime_report(FakeNormalizer(False))

    assert report["status"] == "BLOCKED_DEPENDENCY_UNAVAILABLE"
    assert report["ready_for_conversion_count"] == 0
    assert all(
        row["runtime_status"] == "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
        for row in report["formats"]
    )


def test_source_probe_is_source_hashed_and_non_converting() -> None:
    source = DocumentSource("src_1", "需求.wps", b"immutable-wps-source")

    probe = probe_compatible_office_source_runtime(source, FakeNormalizer(True))

    assert probe["schema"] == OFFICE_SOURCE_RUNTIME_PROBE_SCHEMA
    assert probe["status"] == "READY_FOR_SOURCE_CONVERSION"
    assert probe["recognized_compatible_office_format"] is True
    assert probe["normalized_target_format"] == "docx"
    assert probe["source_hash"] == source.content_hash
    assert probe["format_conversion_verified_with_this_source"] is False
    assert probe["conversion_must_succeed_before_activation"] is True


def test_source_probe_rejects_unknown_suffix_without_guessing() -> None:
    source = DocumentSource("src_2", "unknown.bin", b"opaque")

    probe = probe_compatible_office_source_runtime(source, FakeNormalizer(True))

    assert probe["status"] == "UNSUPPORTED_SOURCE_SUFFIX"
    assert probe["recognized_compatible_office_format"] is False
    assert probe["declared_capabilities"] == []


def test_runtime_aware_adapter_preserves_format_capabilities_but_blocks_runtime() -> None:
    adapter = RuntimeAwareCompatibleOfficeDocumentAdapter(FakeNormalizer(False))
    source = DocumentSource("src_3", "规则.et", b"legacy-sheet")

    match = adapter.probe(source)

    assert match is not None
    assert match.capabilities
    assert match.runtime_ready is False
    assert match.runtime_reason == "RUNTIME_DEPENDENCY_UNAVAILABLE"
    receipt = adapter.receipt(source, match)
    assert receipt["runtime_ready"] is False
    assert receipt["runtime_dependency_available"] is False
    assert receipt["format_conversion_verified_with_this_source"] is False


def test_successful_real_source_conversion_is_marked_only_after_extract(monkeypatch) -> None:
    monkeypatch.setattr(
        compatible_office_adapter,
        "_delegate_for",
        lambda target_suffix: FakeDocxDelegate(),
    )
    adapter = RuntimeAwareCompatibleOfficeDocumentAdapter(WorkingNormalizer())
    source = DocumentSource("src_4", "需求.wps", b"legacy-word")

    result = adapter.extract(source)

    receipt = result["office_normalization_receipt"]
    assert result["filename"] == "需求.wps"
    assert result["blocks"][0]["source_locator"] == "需求.wps#paragraph=1"
    assert receipt["source_conversion_succeeded"] is True
    assert receipt["format_conversion_verified_with_this_source"] is True
    assert receipt["network_access_used"] == "UNKNOWN"
    assert receipt["network_isolation_enforced"] is False
    assert result["structure_receipt"]["format_conversion_verified_with_this_source"] is True


def test_default_registry_uses_runtime_aware_compatible_office_adapter() -> None:
    adapter = build_default_registry().get("compatible-office-normalization")

    assert isinstance(adapter, RuntimeAwareCompatibleOfficeDocumentAdapter)
    assert adapter.parser_version == "3"
