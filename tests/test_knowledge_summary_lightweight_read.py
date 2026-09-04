from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center import enterprise_knowledge_center as knowledge_center
from ai_test_asset_center import private_pilot_product_catalog as catalog


class _DummyCatalog(catalog.ProductCatalogHttpMixin):
    def __init__(self) -> None:
        self.full_asset_loads = 0

    def _list_project_inputs(self, project: str, root: Path) -> dict[str, Any]:
        assert project == "acme"
        assert root.exists()
        return {
            "sources": [
                {
                    "source_id": "db-doc",
                    "filename": "database-prd.md",
                    "source_type": "prd",
                    "status": "active",
                }
            ]
        }

    def _load_merged_knowledge_asset(
        self,
        project: str,
        root: Path,
        actor: Any,
    ) -> dict[str, Any]:
        self.full_asset_loads += 1
        raise AssertionError("source-only summary must not load the full knowledge asset")


def test_source_only_summary_reads_registry_without_full_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def list_sources(
        project_id: str,
        root: Path | None = None,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        assert project_id == "acme"
        assert root == tmp_path
        assert include_deleted is False
        return {
            "project_id": "acme",
            "sources": [
                {
                    "source_id": "uploaded-prd",
                    "source_ref": "document://uploaded-prd",
                    "source_origin": "DOCUMENT_REFERENCE",
                    "original_name": "prd.md",
                    "source_type": "prd",
                    "status": "active",
                    "version": 1,
                }
            ],
            "summary": {
                "active_source_count": 1,
                "canonical_source_count": 1,
            },
        }

    monkeypatch.setattr(
        knowledge_center,
        "list_enterprise_knowledge_sources",
        list_sources,
    )
    handler = _DummyCatalog()

    result = handler._get_knowledge_source_summary(
        "acme",
        tmp_path,
        {"name": "reader", "tenant_id": "tenant-a"},
    )

    assert handler.full_asset_loads == 0
    assert result["project_id"] == "acme"
    assert result["summary"]["active_source_count"] == 2
    assert result["summary"]["canonical_source_count"] == 1
    assert {row["source_id"] for row in result["sources"]} == {
        "uploaded-prd",
        "db-doc",
    }
