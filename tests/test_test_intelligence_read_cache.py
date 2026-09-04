from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center import private_pilot_http_routing as routing
from ai_test_asset_center import private_pilot_product_catalog as catalog


class _DummyCatalog(catalog.ProductCatalogHttpMixin):
    def __init__(self, tenant: str = "tenant-a") -> None:
        self.tenant = tenant
        self.load_count = 0

    def _request_tenant(self) -> str:
        return self.tenant

    def _load_merged_knowledge_asset(
        self,
        project: str,
        root: Path,
        actor: Any,
    ) -> dict[str, Any]:
        self.load_count += 1
        return {
            "project": project,
            "load_count": self.load_count,
            "actor": str(actor),
        }


@pytest.fixture(autouse=True)
def _clear_test_intelligence_cache() -> None:
    with catalog._TEST_INTELLIGENCE_CACHE_LOCK:
        catalog._TEST_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_BUILD_LOCKS_GUARD:
        catalog._TEST_INTELLIGENCE_BUILD_LOCKS.clear()
    yield
    with catalog._TEST_INTELLIGENCE_CACHE_LOCK:
        catalog._TEST_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_BUILD_LOCKS_GUARD:
        catalog._TEST_INTELLIGENCE_BUILD_LOCKS.clear()


def _install_analysis_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"requirement": 0, "test": 0, "compose": 0}

    def analyze_requirement(asset: dict[str, Any]) -> dict[str, Any]:
        calls["requirement"] += 1
        return {"kind": "requirement", "load_count": asset["load_count"]}

    def analyze_test(asset: dict[str, Any]) -> dict[str, Any]:
        calls["test"] += 1
        return {"kind": "test", "load_count": asset["load_count"]}

    def compose(
        requirement_analysis: dict[str, Any],
        test_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        calls["compose"] += 1
        return {
            "schema": "test-analysis",
            "requirement": requirement_analysis,
            "test": test_analysis,
        }

    monkeypatch.setattr(catalog, "analyze_knowledge_asset", analyze_requirement)
    monkeypatch.setattr(catalog, "analyze_test_intelligence", analyze_test)
    monkeypatch.setattr(catalog, "compose_requirement_test_linkage", compose)
    return calls


def test_hot_read_reuses_analysis_without_reloading_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fingerprint = ["fp-1"]
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, project: fingerprint[0],
    )
    calls = _install_analysis_spies(monkeypatch)
    handler = _DummyCatalog()

    first = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")
    first["requirement"]["mutated"] = True
    second = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert handler.load_count == 1
    assert calls == {"requirement": 1, "test": 1, "compose": 1}
    assert second["project_id"] == "acme"
    assert "mutated" not in second["requirement"]


def test_source_fingerprint_change_rebuilds_analysis_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fingerprint = ["fp-1"]
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, project: fingerprint[0],
    )
    calls = _install_analysis_spies(monkeypatch)
    handler = _DummyCatalog()

    first = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")
    fingerprint[0] = "fp-2"
    second = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")
    third = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert first["requirement"]["load_count"] == 1
    assert second["requirement"]["load_count"] == 2
    assert third["requirement"]["load_count"] == 2
    assert handler.load_count == 2
    assert calls == {"requirement": 2, "test": 2, "compose": 2}


def test_cache_is_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, project: "fp-1",
    )
    calls = _install_analysis_spies(monkeypatch)
    tenant_a = _DummyCatalog("tenant-a")
    tenant_b = _DummyCatalog("tenant-b")

    tenant_a._get_test_intelligence_analysis("shared-project", tmp_path, "actor-a")
    tenant_b._get_test_intelligence_analysis("shared-project", tmp_path, "actor-b")

    assert tenant_a.load_count == 1
    assert tenant_b.load_count == 1
    assert calls == {"requirement": 2, "test": 2, "compose": 2}


def test_fingerprint_tracks_sqlite_and_wal_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(routing, "_project_data_fingerprint", lambda root, project: "files")

    initial = catalog._test_intelligence_source_fingerprint(tmp_path, "acme")

    db_path = tmp_path / "qualibug.db"
    db_path.write_bytes(b"db-v1")
    with_db = catalog._test_intelligence_source_fingerprint(tmp_path, "acme")

    wal_path = tmp_path / "qualibug.db-wal"
    wal_path.write_bytes(b"wal-v1")
    with_wal = catalog._test_intelligence_source_fingerprint(tmp_path, "acme")

    assert initial != with_db
    assert with_db != with_wal
