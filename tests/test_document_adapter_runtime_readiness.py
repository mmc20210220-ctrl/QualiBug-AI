from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.builtin_adapters import (
    UnknownBinaryDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.compatible_office_adapter import (
    CompatibleOfficeDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_TEXT_EXTRACTION,
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.pipeline import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.planner import (
    plan_document_parsing,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.registry import (
    DocumentAdapterRegistry,
)


class UnavailableNormalizer:
    name = "unavailable-test-normalizer"
    version = "1"

    def __init__(self) -> None:
        self.normalize_called = False

    def available(self) -> bool:
        return False

    def normalize(self, source, target_suffix):
        self.normalize_called = True
        raise AssertionError("known-unavailable normalizer must not be executed")


def _registry(normalizer: UnavailableNormalizer) -> DocumentAdapterRegistry:
    return DocumentAdapterRegistry(
        [
            CompatibleOfficeDocumentAdapter(normalizer),
            UnknownBinaryDocumentAdapter(),
        ]
    )


def test_planner_blocks_recognized_format_when_runtime_dependency_is_missing() -> None:
    normalizer = UnavailableNormalizer()
    source = DocumentSource("src", "需求.wps", b"legacy-wps-container")

    plan = plan_document_parsing(source, _registry(normalizer))

    assert plan["detected_format"] == "wps"
    assert plan["capability_family"] == "word"
    assert plan["status"] == "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
    assert plan["selected_adapters"][0]["runtime_ready"] is False
    assert plan["runtime_blockers"] == [
        {
            "adapter_name": "compatible-office-normalization",
            "runtime_reason": "RUNTIME_DEPENDENCY_UNAVAILABLE",
        }
    ]
    assert normalizer.normalize_called is False


def test_pipeline_skips_known_unavailable_adapter_and_uses_fail_visible_fallback() -> None:
    normalizer = UnavailableNormalizer()

    result = build_document_structure_ir(
        b"legacy-wps-container",
        filename="需求.wps",
        source_id="src_runtime_dependency",
        registry=_registry(normalizer),
    )

    assert normalizer.normalize_called is False
    assert result["parsing_plan"]["status"] == "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
    assert result["parsing_plan"]["runtime_fallback_adapter"]["adapter_name"] == "unknown-binary-fallback"
    assert CAP_TEXT_EXTRACTION in result["parsing_plan"]["missing_capabilities"]
    assert result["ingestion_pipeline_receipt"]["runtime_fallback_used"] is True
    assert result["ingestion_pipeline_receipt"]["runtime_blocker_count"] == 1
    assert result["ingestion_pipeline_receipt"]["executed_capability_count"] == 0
    assert result["structure_receipt"]["status"] == "BLOCKED"
    reasons = {
        row.get("reason_code") for row in result.get("unsupported_content") or []
    }
    assert "DOCUMENT_ADAPTER_RUNTIME_DEPENDENCY_UNAVAILABLE" in reasons
    assert "DOCUMENT_ADAPTER_CAPABILITY_GAP" in reasons
