from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.office_runtime_capabilities import (
    OFFICE_RUNTIME_CAPABILITY_SCHEMA,
    OFFICE_SOURCE_RUNTIME_PROBE_SCHEMA,
    build_compatible_office_runtime_report,
    probe_compatible_office_source_runtime,
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



def _row(report: dict, suffix: str) -> dict:
    return next(row for row in report["formats"] if row["source_suffix"] == suffix)



def test_runtime_report_never_confuses_recognition_with_verified_conversion() -> None:
    report = build_compatible_office_runtime_report(FakeNormalizer(True))

    assert report["schema"] == OFFICE_RUNTIME_CAPABILITY_SCHEMA
    assert report["status"] == "READY"
    assert report["recognition_is_not_conversion_verification"] is True
    assert report["real_source_verified_format_count"] == 0
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
