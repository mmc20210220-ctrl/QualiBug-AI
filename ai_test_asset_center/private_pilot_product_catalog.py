from __future__ import annotations

"""HTTP product composition and lightweight read routes before legacy routing."""

import copy
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


_TEST_INTELLIGENCE_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_TEST_INTELLIGENCE_CACHE_LOCK = threading.Lock()
_TEST_INTELLIGENCE_CACHE_MAX_ENTRIES = 64
_TEST_INTELLIGENCE_BUILD_LOCKS: dict[str, threading.Lock] = {}
_TEST_INTELLIGENCE_BUILD_LOCKS_GUARD = threading.Lock()


def _test_intelligence_build_lock(cache_key: str) -> threading.Lock:
    with _TEST_INTELLIGENCE_BUILD_LOCKS_GUARD:
        lock = _TEST_INTELLIGENCE_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _TEST_INTELLIGENCE_BUILD_LOCKS[cache_key] = lock
        return lock


def _test_intelligence_source_fingerprint(root: Path, project: str) -> str:
    """Fingerprint every persisted source that can change Test Intelligence output.

    The shared project fingerprint covers knowledge assets, source registries and
    other file-backed projections. The merged knowledge loader can also consume
    SQLite knowledge_docs, whose latest writes may still live in the WAL, so both
    DB files participate as well. This lets hot GETs skip both asset loading and
    analysis while still invalidating as soon as source data changes.
    """
    from . import private_pilot_http_routing as routing

    parts = [routing._project_data_fingerprint(root, project)]
    for filename in ("qualibug.db", "qualibug.db-wal"):
        path = root / filename
        try:
            stat = path.stat()
        except OSError:
            parts.append(f"{filename}:missing")
            continue
        parts.append(f"{filename}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


def _project_analysis_request(path: str) -> tuple[str, str]:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] in {"requirement-intelligence", "test-intelligence"}
    ):
        return parts[4], parts[3].strip()
    return "", ""


def _finding_detail_request(path: str) -> tuple[str, str]:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 6
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == "findings"
    ):
        return parts[3].strip(), parts[5].strip()
    return "", ""


def _requirement_analysis_project(path: str) -> str:
    product, project = _project_analysis_request(path)
    return project if product == "requirement-intelligence" else ""


def _test_intelligence_project(path: str) -> str:
    product, project = _project_analysis_request(path)
    return project if product == "test-intelligence" else ""


def _find_finding(payload: dict[str, Any], finding_id: str) -> dict[str, Any] | None:
    """Mirror the existing Findings page semantics: deliverable findings only."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    classification = (
        data.get("finding_classification")
        if isinstance(data.get("finding_classification"), dict)
        else {}
    )
    deliverable = classification.get("deliverable")
    rows = deliverable if isinstance(deliverable, list) else data.get("defects")
    if not isinstance(rows, list):
        return None
    for item in rows:
        if not isinstance(item, dict):
            continue
        display_id = str(item.get("id") or "").strip()
        persistence_id = str(item.get("finding_persistence_id") or "").strip()
        if finding_id in {display_id, persistence_id}:
            return item
    return None


class ProductCatalogHttpMixin:
    """Expose lightweight read products before the legacy routing layer."""

    def _handle_finding_detail(
        self,
        project: str,
        finding_id: str,
        root: Path,
    ) -> Any:
        """Return one already-sanitized finding without shipping command-center.

        Scan completion already pre-warms the command-center cache. Detail reads use
        that exact sanitized projection so evidence/reproduction/redaction semantics
        cannot drift from the list. Stale entries are served immediately while the
        canonical command-center rebuild runs in the background. Only a true cold
        start falls back to one canonical build.
        """
        from . import private_pilot_http_routing as routing

        tenant_id = self._request_tenant()
        cache_key = f"{tenant_id}:{project}"
        fingerprint = routing._project_data_fingerprint(root, project)

        def cached_entry() -> tuple[str, float, dict[str, Any]] | None:
            with routing._COMMAND_CENTER_CACHE_LOCK:
                return routing._COMMAND_CENTER_CACHE.get(cache_key)

        def render(entry: tuple[str, float, dict[str, Any]], *, stale: bool) -> Any:
            body = entry[2]
            if isinstance(body, dict) and isinstance(body.get("__qualibug_error__"), dict):
                return self._json(body["__qualibug_error__"], 500)
            finding = _find_finding(body, finding_id)
            if finding is None:
                return self._json(
                    {
                        "ok": False,
                        "error": "FINDING_NOT_FOUND",
                        "message": "该问题不存在于当前可交付结果中。",
                    },
                    404,
                )
            return self._json(
                {
                    "ok": True,
                    "project_id": project,
                    "data": finding,
                    "cache_status": {
                        "state": "revalidating" if stale else "fresh",
                        "age_seconds": int(max(0.0, time.monotonic() - entry[1])),
                    },
                },
                cache_control="no-cache",
            )

        entry = cached_entry()
        if entry is not None:
            fresh = entry[0] == fingerprint and (
                time.monotonic() - entry[1]
            ) < routing._COMMAND_CENTER_CACHE_TTL_SECONDS
            if not fresh:
                self._spawn_command_center_rebuild(project, root, cache_key, fingerprint)
            return render(entry, stale=not fresh)

        build_lock = routing._command_center_build_lock(cache_key)
        with build_lock:
            entry = cached_entry()
            if entry is not None:
                fresh = entry[0] == fingerprint
                if not fresh:
                    self._spawn_command_center_rebuild(project, root, cache_key, fingerprint)
                return render(entry, stale=not fresh)
            body, is_error = self._build_and_cache_command_center(
                project,
                root,
                cache_key,
                fingerprint,
            )
            if is_error:
                return self._json(body, 500)
            entry = cached_entry()
            if entry is None:
                return self._json(
                    {
                        "ok": False,
                        "error": "FINDING_DETAIL_UNAVAILABLE",
                        "message": "问题详情缓存未能建立。",
                    },
                    500,
                )
            return render(entry, stale=False)

    def _get_test_intelligence_analysis(
        self,
        project: str,
        root: Path,
        actor: Any,
    ) -> dict[str, Any]:
        """Return a source-fingerprint cached Test Intelligence projection.

        A cache hit does not load the merged knowledge asset and does not execute
        either analysis product. A source fingerprint change causes exactly one
        request per tenant/project to rebuild while concurrent readers wait for and
        then reuse that result.
        """
        tenant_id = self._request_tenant()
        cache_key = f"{tenant_id}:{project}"
        fingerprint = _test_intelligence_source_fingerprint(root, project)

        def cached_analysis() -> dict[str, Any] | None:
            with _TEST_INTELLIGENCE_CACHE_LOCK:
                entry = _TEST_INTELLIGENCE_CACHE.get(cache_key)
                if entry is None or entry[0] != fingerprint:
                    return None
                return copy.deepcopy(entry[1])

        cached = cached_analysis()
        if cached is not None:
            return cached

        build_lock = _test_intelligence_build_lock(cache_key)
        with build_lock:
            cached = cached_analysis()
            if cached is not None:
                return cached

            asset = self._load_merged_knowledge_asset(project, root, actor)
            requirement_analysis = analyze_knowledge_asset(asset)
            analysis = compose_requirement_test_linkage(
                requirement_analysis,
                analyze_test_intelligence(asset),
            )
            analysis["project_id"] = project

            with _TEST_INTELLIGENCE_CACHE_LOCK:
                if (
                    cache_key not in _TEST_INTELLIGENCE_CACHE
                    and len(_TEST_INTELLIGENCE_CACHE) >= _TEST_INTELLIGENCE_CACHE_MAX_ENTRIES
                ):
                    oldest_key = next(iter(_TEST_INTELLIGENCE_CACHE))
                    _TEST_INTELLIGENCE_CACHE.pop(oldest_key, None)
                _TEST_INTELLIGENCE_CACHE[cache_key] = (
                    fingerprint,
                    copy.deepcopy(analysis),
                )
            return analysis

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        product_route, requested_project = _project_analysis_request(parsed.path)
        finding_project, finding_id = _finding_detail_request(parsed.path)
        is_catalog = parsed.path == "/api/v1/products"
        if not is_catalog and not requested_project and not finding_project:
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
            project = _safe_project_id(finding_project or requested_project)
        except ValueError:
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
        if not self._require_project_scope(project):
            return None
        if not self._require_known_project(project, root):
            return None

        if finding_project:
            if not finding_id:
                return self._json({"ok": False, "error": "FINDING_NOT_FOUND"}, 404)
            return self._handle_finding_detail(project, finding_id, root)

        if product_route == "requirement-intelligence":
            asset = self._load_merged_knowledge_asset(project, root, actor)
            analysis = analyze_knowledge_asset(asset)
            analysis["project_id"] = project
        elif product_route == "test-intelligence":
            analysis = self._get_test_intelligence_analysis(project, root, actor)
        else:  # Defensive fail-closed guard; route parser currently makes this unreachable.
            return self._json({"ok": False, "error": "PRODUCT_NOT_FOUND"}, 404)
        return self._json({"ok": True, "data": analysis})
