from __future__ import annotations

"""HTTP product composition and lightweight read routes before legacy routing."""

import copy
import hashlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from products.catalog import get_product_catalog
from products.requirement_intelligence import analyze_knowledge_asset
from products.test_intelligence import analyze_test_intelligence

from .product_intelligence_linkage import compose_requirement_test_linkage
from .real_project_onboarding import _safe_project_id


_REQUIREMENT_INTELLIGENCE_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_REQUIREMENT_INTELLIGENCE_CACHE_LOCK = threading.Lock()
_TEST_INTELLIGENCE_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_TEST_INTELLIGENCE_CACHE_LOCK = threading.Lock()
_TEST_INTELLIGENCE_CACHE_MAX_ENTRIES = 64
# Requirement Intelligence and Test Intelligence share this per tenant/project lock.
# A cold Test Intelligence build also materializes Requirement Intelligence, so the
# two routes must not race and repeat the same requirement analysis.
_TEST_INTELLIGENCE_BUILD_LOCKS: dict[str, threading.Lock] = {}
_TEST_INTELLIGENCE_BUILD_LOCKS_GUARD = threading.Lock()
_TEST_INTELLIGENCE_DB_DIGEST_CACHE: dict[str, tuple[str, str]] = {}
_TEST_INTELLIGENCE_DB_DIGEST_LOCK = threading.Lock()
_TEST_INTELLIGENCE_REVALIDATING: set[str] = set()
_TEST_INTELLIGENCE_REVALIDATE_LOCK = threading.Lock()
_TEST_INTELLIGENCE_LAST_REVALIDATE: dict[str, float] = {}
_TEST_INTELLIGENCE_REVALIDATE_INTERVAL_SECONDS = 15.0
_TEST_INTELLIGENCE_PROJECTION_SCHEMA = "qualibug.test-intelligence.read-projection.v1"


def _test_intelligence_build_lock(cache_key: str) -> threading.Lock:
    with _TEST_INTELLIGENCE_BUILD_LOCKS_GUARD:
        lock = _TEST_INTELLIGENCE_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _TEST_INTELLIGENCE_BUILD_LOCKS[cache_key] = lock
        return lock


def _test_intelligence_db_identity(root: Path) -> str:
    parts: list[str] = []
    for filename in ("qualibug.db", "qualibug.db-wal"):
        path = root / filename
        try:
            stat = path.stat()
        except OSError:
            parts.append(f"{filename}:missing")
            continue
        parts.append(f"{filename}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


def _hash_fingerprint_value(digest: Any, value: Any) -> None:
    encoded = ("" if value is None else str(value)).encode("utf-8", errors="replace")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _test_intelligence_db_knowledge_digest(
    root: Path,
    tenant_id: str,
    project: str,
) -> str:
    """Hash only this tenant/project's SQLite knowledge documents.

    File metadata is used only as a cheap change detector. When the SQLite/WAL
    identity changes, this function recomputes the exact digest for the requested
    tenant/project. Unrelated database writes therefore cost at most a scoped read;
    they do not invalidate the expensive intelligence analyses.
    """
    db_identity = _test_intelligence_db_identity(root)
    cache_key = f"{tenant_id}:{project}"
    with _TEST_INTELLIGENCE_DB_DIGEST_LOCK:
        cached = _TEST_INTELLIGENCE_DB_DIGEST_CACHE.get(cache_key)
        if cached is not None and cached[0] == db_identity:
            return cached[1]

    db_path = root / "qualibug.db"
    if not db_path.exists():
        scoped_digest = "knowledge-db:missing"
    else:
        digest = hashlib.sha256()
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA query_only=ON")
                rows = conn.execute(
                    """
                    SELECT id, filename, type, content, created_at
                    FROM knowledge_docs
                    WHERE tenant_id = ? AND project_id = ?
                    ORDER BY id
                    """,
                    (tenant_id, project),
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                for value in row:
                    _hash_fingerprint_value(digest, value)
            scoped_digest = f"knowledge-db:{digest.hexdigest()}"
        except sqlite3.DatabaseError as exc:
            # Older/new installations can transiently lack the table or be busy.
            # Fail safe: never reuse a stale projection across a DB identity change.
            if "no such table" in str(exc).lower():
                scoped_digest = "knowledge-db:empty"
            else:
                scoped_digest = f"knowledge-db:unavailable:{db_identity}"

    with _TEST_INTELLIGENCE_DB_DIGEST_LOCK:
        if (
            cache_key not in _TEST_INTELLIGENCE_DB_DIGEST_CACHE
            and len(_TEST_INTELLIGENCE_DB_DIGEST_CACHE) >= _TEST_INTELLIGENCE_CACHE_MAX_ENTRIES
        ):
            oldest_key = next(iter(_TEST_INTELLIGENCE_DB_DIGEST_CACHE))
            _TEST_INTELLIGENCE_DB_DIGEST_CACHE.pop(oldest_key, None)
        _TEST_INTELLIGENCE_DB_DIGEST_CACHE[cache_key] = (db_identity, scoped_digest)
    return scoped_digest


def _test_intelligence_file_source_fingerprint(root: Path, project: str) -> str:
    """Fingerprint only files that can change Requirement/Test Intelligence inputs.

    Runtime scan/evidence/regression artifacts are intentionally excluded. They do
    not feed the intelligence analyzers and must not invalidate this read model.
    """
    safe_project = _safe_project_id(project)
    root = root.resolve()
    explicit = [
        root / ".qualibug" / "source_registry" / f"{safe_project}.json",
        root / "platform_workspace" / safe_project / "enterprise_knowledge_center" / "source_registry.json",
        root / "platform_outputs" / safe_project / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
        root / "platform_workspace" / safe_project / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
        root / "platform_outputs" / safe_project / "defect_discovery" / "enterprise_business_knowledge_asset.json",
        root / "platform_workspace" / safe_project / "defect_discovery" / "enterprise_business_knowledge_asset.json",
    ]
    inputs_root = root / "platform_inputs" / safe_project
    if inputs_root.is_dir():
        explicit.extend(path for path in inputs_root.rglob("*") if path.is_file())

    digest = hashlib.sha256()
    seen: set[Path] = set()
    for path in sorted(explicit, key=lambda item: item.as_posix()):
        try:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            stat = resolved.stat()
        except OSError:
            continue
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        _hash_fingerprint_value(digest, relative)
        _hash_fingerprint_value(digest, stat.st_mtime_ns)
        _hash_fingerprint_value(digest, stat.st_size)
    return f"knowledge-files:{digest.hexdigest()}"


def _test_intelligence_source_fingerprint(
    root: Path,
    tenant_id: str,
    project: str,
) -> str:
    """Fingerprint only persisted knowledge inputs consumed by the analyzers."""
    return "|".join(
        (
            _test_intelligence_file_source_fingerprint(root, project),
            _test_intelligence_db_knowledge_digest(root, tenant_id, project),
        )
    )


def _test_intelligence_projection_path(
    root: Path,
    tenant_id: str,
    project: str,
) -> Path:
    safe_project = _safe_project_id(project)
    tenant_key = hashlib.sha256(tenant_id.encode("utf-8", errors="replace")).hexdigest()[:20]
    return (
        root.resolve()
        / "platform_workspace"
        / safe_project
        / "read_models"
        / f"test_intelligence.{tenant_key}.json"
    )


def _load_persisted_test_intelligence(
    root: Path,
    tenant_id: str,
    project: str,
) -> tuple[str, dict[str, Any]] | None:
    from .private_pilot_json_io import _read_json_object

    path = _test_intelligence_projection_path(root, tenant_id, project)
    try:
        payload = _read_json_object(path)
    except (OSError, ValueError):
        return None
    if payload.get("schema") != _TEST_INTELLIGENCE_PROJECTION_SCHEMA:
        return None
    fingerprint = str(payload.get("source_fingerprint") or "").strip()
    analysis = payload.get("analysis")
    if not fingerprint or not isinstance(analysis, dict):
        return None
    return fingerprint, copy.deepcopy(analysis)


def _persist_test_intelligence(
    root: Path,
    tenant_id: str,
    project: str,
    fingerprint: str,
    analysis: dict[str, Any],
) -> None:
    from .private_pilot_json_io import _write_json_object_atomic

    _write_json_object_atomic(
        _test_intelligence_projection_path(root, tenant_id, project),
        {
            "schema": _TEST_INTELLIGENCE_PROJECTION_SCHEMA,
            "project_id": project,
            "source_fingerprint": fingerprint,
            "analysis": analysis,
        },
    )


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

    def _get_knowledge_source_summary(
        self,
        project: str,
        root: Path,
        actor: Any,
    ) -> dict[str, Any]:
        """Return only the source inventory without loading the full knowledge asset.

        Materials/source-list consumers need occurrence metadata, not the 100MB+
        enterprise business model. Keep the same merge and ACL boundaries as the
        legacy summary route while reading only the durable source registry and
        project input inventory.
        """
        from .connector_acl_authority import filter_connector_asset_for_actor
        from .enterprise_knowledge_center import list_enterprise_knowledge_sources
        from .private_pilot_project_assets import _knowledge_asset_sources

        inventory = list_enterprise_knowledge_sources(
            project,
            root=root,
            include_deleted=False,
        )
        registered = _knowledge_asset_sources(
            {"sources": inventory.get("sources", [])},
            root,
        )
        input_files = self._list_project_inputs(project, root)
        inputs = (
            input_files.get("sources", [])
            if isinstance(input_files, dict)
            and isinstance(input_files.get("sources"), list)
            else []
        )
        merged: dict[str, dict[str, Any]] = {}
        for item in [*registered, *inputs]:
            if not isinstance(item, dict):
                continue
            key = str(
                item.get("source_id")
                or item.get("id")
                or item.get("filename")
                or ""
            ).strip()
            if key:
                merged.setdefault(key, dict(item))

        raw_summary = inventory.get("summary")
        summary = dict(raw_summary) if isinstance(raw_summary, dict) else {}
        summary["active_source_count"] = len(merged)
        projected = filter_connector_asset_for_actor(
            project,
            {
                "project_id": project,
                "summary": summary,
                "sources": list(merged.values()),
            },
            actor={**actor, "project_id": project} if actor else actor,
            root=root,
        )
        sources = projected.get("sources")
        visible_sources = sources if isinstance(sources, list) else []
        projected_summary = projected.get("summary")
        public_summary = (
            dict(projected_summary)
            if isinstance(projected_summary, dict)
            else {}
        )
        public_summary["active_source_count"] = len(visible_sources)
        return {
            "project_id": project,
            "summary": public_summary,
            "sources": visible_sources,
        }

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

    def _get_requirement_intelligence_analysis(
        self,
        project: str,
        root: Path,
        actor: Any,
    ) -> dict[str, Any]:
        """Return cached Requirement Intelligence until its source fingerprint changes."""
        tenant_id = self._request_tenant()
        cache_key = f"{tenant_id}:{project}"
        fingerprint = _test_intelligence_source_fingerprint(root, tenant_id, project)

        def cached_requirement() -> dict[str, Any] | None:
            with _REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
                entry = _REQUIREMENT_INTELLIGENCE_CACHE.get(cache_key)
                if entry is None or entry[0] != fingerprint:
                    return None
                return copy.deepcopy(entry[1])

        cached = cached_requirement()
        if cached is not None:
            cached["project_id"] = project
            return cached

        build_lock = _test_intelligence_build_lock(cache_key)
        with build_lock:
            cached = cached_requirement()
            if cached is not None:
                cached["project_id"] = project
                return cached

            asset = self._load_merged_knowledge_asset(project, root, actor)
            analysis = analyze_knowledge_asset(asset)
            with _REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
                if (
                    cache_key not in _REQUIREMENT_INTELLIGENCE_CACHE
                    and len(_REQUIREMENT_INTELLIGENCE_CACHE) >= _TEST_INTELLIGENCE_CACHE_MAX_ENTRIES
                ):
                    oldest_key = next(iter(_REQUIREMENT_INTELLIGENCE_CACHE))
                    _REQUIREMENT_INTELLIGENCE_CACHE.pop(oldest_key, None)
                _REQUIREMENT_INTELLIGENCE_CACHE[cache_key] = (
                    fingerprint,
                    copy.deepcopy(analysis),
                )
            result = copy.deepcopy(analysis)
            result["project_id"] = project
            return result

    def _build_test_intelligence_projection(
        self,
        project: str,
        root: Path,
        actor: Any,
        *,
        tenant_id: str,
        cache_key: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        """Build one canonical projection and persist it for restart-safe reads."""
        build_lock = _test_intelligence_build_lock(cache_key)
        with build_lock:
            with _TEST_INTELLIGENCE_CACHE_LOCK:
                cached = _TEST_INTELLIGENCE_CACHE.get(cache_key)
                if cached is not None and cached[0] == fingerprint:
                    return copy.deepcopy(cached[1])

            with _REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
                requirement_entry = _REQUIREMENT_INTELLIGENCE_CACHE.get(cache_key)
                requirement_analysis = (
                    copy.deepcopy(requirement_entry[1])
                    if requirement_entry is not None and requirement_entry[0] == fingerprint
                    else None
                )

            asset = self._load_merged_knowledge_asset(project, root, actor)
            if requirement_analysis is None:
                requirement_analysis = analyze_knowledge_asset(asset)
                with _REQUIREMENT_INTELLIGENCE_CACHE_LOCK:
                    if (
                        cache_key not in _REQUIREMENT_INTELLIGENCE_CACHE
                        and len(_REQUIREMENT_INTELLIGENCE_CACHE) >= _TEST_INTELLIGENCE_CACHE_MAX_ENTRIES
                    ):
                        oldest_key = next(iter(_REQUIREMENT_INTELLIGENCE_CACHE))
                        _REQUIREMENT_INTELLIGENCE_CACHE.pop(oldest_key, None)
                    _REQUIREMENT_INTELLIGENCE_CACHE[cache_key] = (
                        fingerprint,
                        copy.deepcopy(requirement_analysis),
                    )

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
            try:
                _persist_test_intelligence(
                    root,
                    tenant_id,
                    project,
                    fingerprint,
                    analysis,
                )
            except OSError:
                # The request still has a correct in-memory result. Persistence is a
                # latency optimization and must not turn a successful analysis into 500.
                pass
            return copy.deepcopy(analysis)

    def _revalidate_test_intelligence_projection(
        self,
        project: str,
        root: Path,
        actor: Any,
        *,
        tenant_id: str,
        cache_key: str,
    ) -> None:
        fingerprint = _test_intelligence_source_fingerprint(root, tenant_id, project)
        with _TEST_INTELLIGENCE_CACHE_LOCK:
            entry = _TEST_INTELLIGENCE_CACHE.get(cache_key)
            if entry is not None and entry[0] == fingerprint:
                return
        self._build_test_intelligence_projection(
            project,
            root,
            actor,
            tenant_id=tenant_id,
            cache_key=cache_key,
            fingerprint=fingerprint,
        )

    def _spawn_test_intelligence_revalidation(
        self,
        project: str,
        root: Path,
        actor: Any,
        *,
        tenant_id: str,
        cache_key: str,
    ) -> None:
        now = time.monotonic()
        with _TEST_INTELLIGENCE_REVALIDATE_LOCK:
            if cache_key in _TEST_INTELLIGENCE_REVALIDATING:
                return
            last = _TEST_INTELLIGENCE_LAST_REVALIDATE.get(cache_key, 0.0)
            if now - last < _TEST_INTELLIGENCE_REVALIDATE_INTERVAL_SECONDS:
                return
            _TEST_INTELLIGENCE_LAST_REVALIDATE[cache_key] = now
            _TEST_INTELLIGENCE_REVALIDATING.add(cache_key)

        def revalidate() -> None:
            try:
                self._revalidate_test_intelligence_projection(
                    project,
                    root,
                    actor,
                    tenant_id=tenant_id,
                    cache_key=cache_key,
                )
            finally:
                with _TEST_INTELLIGENCE_REVALIDATE_LOCK:
                    _TEST_INTELLIGENCE_REVALIDATING.discard(cache_key)

        threading.Thread(
            target=revalidate,
            name=f"test-intelligence-revalidate-{project}",
            daemon=True,
        ).start()

    def _get_test_intelligence_analysis(
        self,
        project: str,
        root: Path,
        actor: Any,
    ) -> dict[str, Any]:
        """Serve the last materialized Test Intelligence projection immediately.

        Hot reads and post-restart reads do not calculate source fingerprints, load
        the 100MB+ merged knowledge asset, or execute analyzers. Freshness checking
        and any rebuild happen in a rate-limited background thread. Only the first
        ever read for a project/tenant blocks to materialize a projection.
        """
        tenant_id = self._request_tenant()
        cache_key = f"{tenant_id}:{project}"

        with _TEST_INTELLIGENCE_CACHE_LOCK:
            entry = _TEST_INTELLIGENCE_CACHE.get(cache_key)
            if entry is not None:
                cached = copy.deepcopy(entry[1])
            else:
                cached = None
        if cached is not None:
            self._spawn_test_intelligence_revalidation(
                project,
                root,
                actor,
                tenant_id=tenant_id,
                cache_key=cache_key,
            )
            return cached

        persisted = _load_persisted_test_intelligence(root, tenant_id, project)
        if persisted is not None:
            fingerprint, analysis = persisted
            with _TEST_INTELLIGENCE_CACHE_LOCK:
                _TEST_INTELLIGENCE_CACHE[cache_key] = (
                    fingerprint,
                    copy.deepcopy(analysis),
                )
            self._spawn_test_intelligence_revalidation(
                project,
                root,
                actor,
                tenant_id=tenant_id,
                cache_key=cache_key,
            )
            return analysis

        fingerprint = _test_intelligence_source_fingerprint(root, tenant_id, project)
        return self._build_test_intelligence_projection(
            project,
            root,
            actor,
            tenant_id=tenant_id,
            cache_key=cache_key,
            fingerprint=fingerprint,
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        product_route, requested_project = _project_analysis_request(parsed.path)
        finding_project, finding_id = _finding_detail_request(parsed.path)
        is_catalog = parsed.path == "/api/v1/products"
        query = parse_qs(parsed.query)
        is_source_summary = (
            parsed.path == "/api/knowledge/summary"
            and str((query.get("view") or [""])[0]).strip().lower() == "sources"
        )
        summary_project = str((query.get("project") or [""])[0]).strip() if is_source_summary else ""
        if (
            not is_catalog
            and not requested_project
            and not finding_project
            and not is_source_summary
        ):
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
            project = _safe_project_id(summary_project or finding_project or requested_project)
        except ValueError:
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
        if not self._require_project_scope(project):
            return None
        if not self._require_known_project(project, root):
            return None

        if is_source_summary:
            projection = self._get_knowledge_source_summary(project, root, actor)
            return self._json({"ok": True, **projection})

        if finding_project:
            if not finding_id:
                return self._json({"ok": False, "error": "FINDING_NOT_FOUND"}, 404)
            return self._handle_finding_detail(project, finding_id, root)

        if product_route == "requirement-intelligence":
            analysis = self._get_requirement_intelligence_analysis(project, root, actor)
        elif product_route == "test-intelligence":
            analysis = self._get_test_intelligence_analysis(project, root, actor)
        else:  # Defensive fail-closed guard; route parser currently makes this unreachable.
            return self._json({"ok": False, "error": "PRODUCT_NOT_FOUND"}, 404)
        return self._json({"ok": True, "data": analysis})
