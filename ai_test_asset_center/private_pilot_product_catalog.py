from __future__ import annotations

"""HTTP product composition without importing Bug Discovery authorities."""

import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from products.catalog import get_product_catalog
from products.requirement_intelligence import analyze_knowledge_asset
from products.test_intelligence import analyze_test_intelligence

from .product_intelligence_linkage import compose_requirement_test_linkage
from .real_project_onboarding import _safe_project_id


_INTELLIGENCE_CACHE: dict[
    str, tuple[str, float, dict[str, Any]]
] = {}
_INTELLIGENCE_CACHE_LOCK = threading.Lock()
_INTELLIGENCE_CACHE_MAX_ENTRIES = 64
_INTELLIGENCE_CACHE_TTL_SECONDS = 30.0
_INTELLIGENCE_BUILD_LOCKS: dict[str, threading.Lock] = {}
_INTELLIGENCE_BUILD_LOCKS_GUARD = threading.Lock()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _project_analysis_request(path: str) -> tuple[str, str]:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] in {"requirement-intelligence", "test-intelligence"}
    ):
        return parts[4], parts[3].strip()
    return "", ""


def _requirement_analysis_project(path: str) -> str:
    product, project = _project_analysis_request(path)
    return project if product == "requirement-intelligence" else ""


def _test_intelligence_project(path: str) -> str:
    product, project = _project_analysis_request(path)
    return project if product == "test-intelligence" else ""


def _intelligence_source_fingerprint(root: Path, project: str) -> str:
    """Cheap invalidation key for the persisted understanding consumed here.

    The product projections are derived from the enterprise knowledge asset and
    its source registry. Stat only those explicit artifacts instead of walking
    the project workspace: a cache hit must stay cheaper than parsing the large
    knowledge JSON it is meant to avoid.
    """
    relative_paths = (
        "enterprise_knowledge_center/enterprise_business_knowledge_asset.json",
        "defect_discovery/enterprise_business_knowledge_asset.json",
        "enterprise_knowledge_center/source_registry.json",
    )
    latest_mtime_ns = 0
    total_bytes = 0
    file_count = 0
    for base in (
        root / "platform_outputs" / project,
        root / "platform_workspace" / project,
    ):
        for relative in relative_paths:
            path = base / relative
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            file_count += 1
            total_bytes += stat.st_size
            latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
    return f"{latest_mtime_ns}:{file_count}:{total_bytes}"


def _cache_key(
    *,
    product_route: str,
    project: str,
    tenant_id: str,
    actor: Any,
) -> str:
    actor_row = actor if isinstance(actor, dict) else {}
    # The merged knowledge asset is ACL-filtered. Never share a projection
    # across tenants, roles or principals merely because project_id matches.
    return ":".join(
        (
            tenant_id,
            project,
            product_route,
            _text(actor_row.get("role")),
            _text(actor_row.get("name") or actor_row.get("username")),
        )
    )


def _cached_analysis(cache_key: str, fingerprint: str) -> dict[str, Any] | None:
    with _INTELLIGENCE_CACHE_LOCK:
        entry = _INTELLIGENCE_CACHE.get(cache_key)
    if entry is None:
        return None
    cached_fingerprint, cached_at, payload = entry
    if cached_fingerprint != fingerprint:
        return None
    if time.monotonic() - cached_at >= _INTELLIGENCE_CACHE_TTL_SECONDS:
        return None
    # Only the top-level project_id field is ever added by this HTTP adapter;
    # nested projection rows are treated as read-only by the JSON transport.
    return dict(payload)


def _store_analysis(cache_key: str, fingerprint: str, payload: dict[str, Any]) -> None:
    with _INTELLIGENCE_CACHE_LOCK:
        if len(_INTELLIGENCE_CACHE) >= _INTELLIGENCE_CACHE_MAX_ENTRIES:
            _INTELLIGENCE_CACHE.clear()
        _INTELLIGENCE_CACHE[cache_key] = (
            fingerprint,
            time.monotonic(),
            dict(payload),
        )


def _build_lock(cache_key: str) -> threading.Lock:
    """Return the per-analysis single-flight lock for one ACL-scoped cache key."""
    with _INTELLIGENCE_BUILD_LOCKS_GUARD:
        lock = _INTELLIGENCE_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _INTELLIGENCE_BUILD_LOCKS[cache_key] = lock
        return lock


class ProductCatalogHttpMixin:
    """Expose product catalog and analysis products before legacy routing."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        product_route, requested_project = _project_analysis_request(parsed.path)
        is_catalog = parsed.path == "/api/v1/products"
        if not is_catalog and not requested_project:
            return super().do_GET()

        self._init_request_context()
        root = self._root()
        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None

        if is_catalog:
            return self._json(
                {
                    "ok": True,
                    "products": list(get_product_catalog()),
                }
            )

        try:
            project = _safe_project_id(requested_project)
        except ValueError:
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
        if not self._require_project_scope(project):
            return None
        if not self._require_known_project(project, root):
            return None

        tenant_id = self._request_tenant()
        fingerprint = _intelligence_source_fingerprint(root, project)
        cache_key = _cache_key(
            product_route=product_route,
            project=project,
            tenant_id=tenant_id,
            actor=actor,
        )
        cached = _cached_analysis(cache_key, fingerprint)
        if cached is not None:
            return self._json({"ok": True, "data": cached})

        # Cold miss single-flight: the UI can issue overlapping mounts/refreshes
        # and the server is threaded. Without this lock every concurrent miss
        # reparses the same large asset and reruns the same deterministic product
        # projections. The first request builds; followers recheck and reuse it.
        with _build_lock(cache_key):
            cached = _cached_analysis(cache_key, fingerprint)
            if cached is not None:
                return self._json({"ok": True, "data": cached})

            asset = self._load_merged_knowledge_asset(project, root, actor)
            if product_route == "requirement-intelligence":
                analysis = analyze_knowledge_asset(asset)
            elif product_route == "test-intelligence":
                requirement_analysis = analyze_knowledge_asset(asset)
                analysis = compose_requirement_test_linkage(
                    requirement_analysis,
                    analyze_test_intelligence(asset),
                )
            else:  # Defensive fail-closed guard; route parser currently makes this unreachable.
                return self._json({"ok": False, "error": "PRODUCT_NOT_FOUND"}, 404)
            analysis["project_id"] = project
            _store_analysis(cache_key, fingerprint, analysis)
            return self._json({"ok": True, "data": analysis})
