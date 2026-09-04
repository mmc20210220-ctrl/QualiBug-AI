from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

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
def _clear_intelligence_caches() -> None:
    with catalog._REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
        catalog._REQUIREMENT_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_CACHE_LOCK:
        catalog._TEST_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_BUILD_LOCKS_GUARD:
        catalog._TEST_INTELLIGENCE_BUILD_LOCKS.clear()
    with catalog._TEST_INTELLIGENCE_DB_DIGEST_LOCK:
        catalog._TEST_INTELLIGENCE_DB_DIGEST_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_REVALIDATE_LOCK:
        catalog._TEST_INTELLIGENCE_REVALIDATING.clear()
        catalog._TEST_INTELLIGENCE_LAST_REVALIDATE.clear()
    yield
    with catalog._REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
        catalog._REQUIREMENT_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_CACHE_LOCK:
        catalog._TEST_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_BUILD_LOCKS_GUARD:
        catalog._TEST_INTELLIGENCE_BUILD_LOCKS.clear()
    with catalog._TEST_INTELLIGENCE_DB_DIGEST_LOCK:
        catalog._TEST_INTELLIGENCE_DB_DIGEST_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_REVALIDATE_LOCK:
        catalog._TEST_INTELLIGENCE_REVALIDATING.clear()
        catalog._TEST_INTELLIGENCE_LAST_REVALIDATE.clear()


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


def test_requirement_hot_read_reuses_analysis_without_reloading_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fingerprint = ["fp-1"]
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project: fingerprint[0],
    )
    calls = _install_analysis_spies(monkeypatch)
    handler = _DummyCatalog()

    first = handler._get_requirement_intelligence_analysis("acme", tmp_path, "actor")
    first["mutated"] = True
    second = handler._get_requirement_intelligence_analysis("acme", tmp_path, "actor")

    assert handler.load_count == 1
    assert calls == {"requirement": 1, "test": 0, "compose": 0}
    assert second["project_id"] == "acme"
    assert "mutated" not in second


def test_test_intelligence_reuses_warmed_requirement_analysis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project: "fp-1",
    )
    calls = _install_analysis_spies(monkeypatch)
    handler = _DummyCatalog()

    requirement = handler._get_requirement_intelligence_analysis("acme", tmp_path, "actor")
    test_analysis = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert requirement["project_id"] == "acme"
    assert test_analysis["project_id"] == "acme"
    assert handler.load_count == 2
    assert calls == {"requirement": 1, "test": 1, "compose": 1}


def test_hot_read_reuses_analysis_without_reloading_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fingerprint = ["fp-1"]
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project: fingerprint[0],
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


def test_source_fingerprint_change_serves_stale_then_revalidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fingerprint = ["fp-1"]
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project: fingerprint[0],
    )
    monkeypatch.setattr(
        _DummyCatalog,
        "_spawn_test_intelligence_revalidation",
        lambda self, *args, **kwargs: None,
    )
    calls = _install_analysis_spies(monkeypatch)
    handler = _DummyCatalog()

    first = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")
    fingerprint[0] = "fp-2"
    stale = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert first["requirement"]["load_count"] == 1
    assert stale["requirement"]["load_count"] == 1
    assert handler.load_count == 1

    handler._revalidate_test_intelligence_projection(
        "acme",
        tmp_path,
        "actor",
        tenant_id="tenant-a",
        cache_key="tenant-a:acme",
    )
    fresh = handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert fresh["requirement"]["load_count"] == 2
    assert handler.load_count == 2
    assert calls == {"requirement": 2, "test": 2, "compose": 2}


def test_persisted_projection_survives_process_cache_reset_without_sync_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project: "fp-1",
    )
    calls = _install_analysis_spies(monkeypatch)
    first_handler = _DummyCatalog()
    built = first_handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert built["project_id"] == "acme"
    assert first_handler.load_count == 1
    projection_path = catalog._test_intelligence_projection_path(
        tmp_path,
        "tenant-a",
        "acme",
    )
    assert projection_path.is_file()

    with catalog._REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
        catalog._REQUIREMENT_INTELLIGENCE_CACHE.clear()
    with catalog._TEST_INTELLIGENCE_CACHE_LOCK:
        catalog._TEST_INTELLIGENCE_CACHE.clear()

    monkeypatch.setattr(
        _DummyCatalog,
        "_spawn_test_intelligence_revalidation",
        lambda self, *args, **kwargs: None,
    )

    def fail_fingerprint(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("persisted hot read must not fingerprint synchronously")

    monkeypatch.setattr(catalog, "_test_intelligence_source_fingerprint", fail_fingerprint)
    second_handler = _DummyCatalog()
    restored = second_handler._get_test_intelligence_analysis("acme", tmp_path, "actor")

    assert restored["project_id"] == "acme"
    assert restored["requirement"]["load_count"] == 1
    assert second_handler.load_count == 0
    assert calls == {"requirement": 1, "test": 1, "compose": 1}


def test_persisted_projection_is_tenant_scoped(tmp_path: Path) -> None:
    tenant_a = catalog._test_intelligence_projection_path(tmp_path, "tenant-a", "acme")
    tenant_b = catalog._test_intelligence_projection_path(tmp_path, "tenant-b", "acme")
    assert tenant_a != tenant_b
    assert tenant_a.parent == tenant_b.parent


def test_cache_is_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        catalog,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project: "fp-1",
    )
    calls = _install_analysis_spies(monkeypatch)
    tenant_a = _DummyCatalog("tenant-a")
    tenant_b = _DummyCatalog("tenant-b")

    tenant_a._get_test_intelligence_analysis("shared-project", tmp_path, "actor-a")
    tenant_b._get_test_intelligence_analysis("shared-project", tmp_path, "actor-b")

    assert tenant_a.load_count == 1
    assert tenant_b.load_count == 1
    assert calls == {"requirement": 2, "test": 2, "compose": 2}


def test_db_fingerprint_ignores_unrelated_writes_but_tracks_project_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_revision = ["db-rev-1"]
    monkeypatch.setattr(catalog, "_test_intelligence_db_identity", lambda root: db_revision[0])

    db_path = tmp_path / "qualibug.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE knowledge_docs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO knowledge_docs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("doc-a", "tenant-a", "acme", "prd.md", "prd", "ABCD", "2026-09-04T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    first = catalog._test_intelligence_source_fingerprint(tmp_path, "tenant-a", "acme")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO knowledge_docs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("doc-b", "tenant-b", "other", "other.md", "prd", "other", "2026-09-04T00:00:01Z"),
        )
        conn.commit()
    finally:
        conn.close()
    db_revision[0] = "db-rev-2"
    after_unrelated_write = catalog._test_intelligence_source_fingerprint(
        tmp_path,
        "tenant-a",
        "acme",
    )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE knowledge_docs SET content = ? WHERE id = ?",
            ("WXYZ", "doc-a"),
        )
        conn.commit()
    finally:
        conn.close()
    db_revision[0] = "db-rev-3"
    after_project_write = catalog._test_intelligence_source_fingerprint(
        tmp_path,
        "tenant-a",
        "acme",
    )

    assert first == after_unrelated_write
    assert after_project_write != first


def test_file_fingerprint_ignores_runtime_scan_outputs_but_tracks_knowledge_inputs(
    tmp_path: Path,
) -> None:
    project = "acme"
    knowledge = (
        tmp_path
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "enterprise_business_knowledge_asset.json"
    )
    knowledge.parent.mkdir(parents=True, exist_ok=True)
    knowledge.write_text('{"version":1}', encoding="utf-8")

    first = catalog._test_intelligence_file_source_fingerprint(tmp_path, project)

    scan = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan.parent.mkdir(parents=True, exist_ok=True)
    scan.write_text('{"scan":1}', encoding="utf-8")
    after_scan_write = catalog._test_intelligence_file_source_fingerprint(tmp_path, project)

    knowledge.write_text('{"version":22}', encoding="utf-8")
    after_knowledge_write = catalog._test_intelligence_file_source_fingerprint(
        tmp_path,
        project,
    )

    assert after_scan_write == first
    assert after_knowledge_write != first
