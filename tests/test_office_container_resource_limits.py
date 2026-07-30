from __future__ import annotations

import io
import zipfile

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    office_container_inspection,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.guarded_compatible_office_adapter import (
    GuardedCompatibleOfficeDocumentAdapter,
)


class NeverNormalizer:
    name = "never-resource-limit-normalizer"
    version = "1"

    def available(self) -> bool:
        return True

    def normalize(self, source, target_suffix):
        raise AssertionError("resource-limited container must not be normalized")


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", "<office:document-content />")
    return buffer.getvalue()


def test_zip_member_limit_blocks_normalization(monkeypatch) -> None:
    monkeypatch.setattr(office_container_inspection, "_MAX_ZIP_MEMBER_COUNT", 1)
    source = DocumentSource("src_limit", "需求.wps", _zip_bytes())

    inspection = office_container_inspection.inspect_office_container(source)
    result = GuardedCompatibleOfficeDocumentAdapter(NeverNormalizer()).extract(source)

    assert inspection["inspection_complete"] is False
    assert any(
        row.get("code") == "OFFICE_ZIP_MEMBER_LIMIT_EXCEEDED"
        for row in inspection["errors"]
    )
    assert result["structure_receipt"]["status"] == "BLOCKED"
    assert result["structure_receipt"]["normalization_attempted"] is False
    assert result["unsupported_content"][0]["reason_code"] == (
        "OFFICE_CONTAINER_SECURITY_INSPECTION_FAILED"
    )
