from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ai_test_asset_center import private_pilot_product_catalog as product_catalog


@pytest.fixture(autouse=True)
def _reset_intelligence_cache_state() -> None:
    with product_catalog._INTELLIGENCE_CACHE_LOCK:
        product_catalog._INTELLIGENCE_CACHE.clear()
    with product_catalog._INTELLIGENCE_BUILD_LOCKS_GUARD:
        product_catalog._INTELLIGENCE_BUILD_LOCKS.clear()
    yield
    with product_catalog._INTELLIGENCE_CACHE_LOCK:
        product_catalog._INTELLIGENCE_CACHE.clear()
    with product_catalog._INTELLIGENCE_BUILD_LOCKS_GUARD:
        product_catalog._INTELLIGENCE_BUILD_LOCKS.clear()


def test_intelligence_cache_key_isolates_acl_sensitive_dimensions() -> None:
    base = product_catalog._cache_key(
        product_route="test-intelligence",
        project="project-a",
        tenant_id="tenant-a",
        actor={"name": "alice", "role": "qa_lead"},
    )

    variants = {
        product_catalog._cache_key(
            product_route="requirement-intelligence",
            project="project-a",
            tenant_id="tenant-a",
            actor={"name": "alice", "role": "qa_lead"},
        ),
        product_catalog._cache_key(
            product_route="test-intelligence",
            project="project-b",
            tenant_id="tenant-a",
            actor={"name": "alice", "role": "qa_lead"},
        ),
        product_catalog._cache_key(
            product_route="test-intelligence",
            project="project-a",
            tenant_id="tenant-b",
            actor={"name": "alice", "role": "qa_lead"},
        ),
        product_catalog._cache_key(
            product_route="test-intelligence",
            project="project-a",
            tenant_id="tenant-a",
            actor={"name": "alice", "role": "viewer"},
        ),
        product_catalog._cache_key(
            product_route="test-intelligence",
            project="project-a",
            tenant_id="tenant-a",
            actor={"name": "bob", "role": "qa_lead"},
        ),
    }

    assert base not in variants
    assert len(variants) == 5


def test_intelligence_source_fingerprint_changes_when_asset_changes(tmp_path: Path) -> None:
    asset_path = (
        tmp_path
        / "platform_outputs"
        / "project-a"
        / "enterprise_knowledge_center"
        / "enterprise_business_knowledge_asset.json"
    )
    asset_path.parent.mkdir(parents=True)
    asset_path.write_text("{}", encoding="utf-8")

    before = product_catalog._intelligence_source_fingerprint(tmp_path, "project-a")
    asset_path.write_text('{"version":"larger-payload"}', encoding="utf-8")
    after = product_catalog._intelligence_source_fingerprint(tmp_path, "project-a")

    assert after != before


def test_intelligence_cache_rejects_changed_fingerprint_and_expired_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(product_catalog.time, "monotonic", lambda: clock["now"])

    product_catalog._store_analysis("key", "fingerprint-a", {"project_id": "project-a"})

    assert product_catalog._cached_analysis("key", "fingerprint-a") == {
        "project_id": "project-a"
    }
    assert product_catalog._cached_analysis("key", "fingerprint-b") is None

    clock["now"] += product_catalog._INTELLIGENCE_CACHE_TTL_SECONDS + 0.001
    assert product_catalog._cached_analysis("key", "fingerprint-a") is None


def test_intelligence_build_lock_is_single_flight_per_cache_key() -> None:
    first = product_catalog._build_lock("same-key")
    second = product_catalog._build_lock("same-key")
    other = product_catalog._build_lock("other-key")

    assert first is second
    assert first is not other


def test_intelligence_build_lock_serializes_concurrent_cold_builders() -> None:
    lock = product_catalog._build_lock("tenant:project:test:user")
    start = threading.Barrier(6)
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def worker() -> None:
        nonlocal active, max_active
        start.wait()
        with lock:
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.005)
            with state_lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert max_active == 1
