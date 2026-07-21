"""Standalone helpers: observation read candidates."""
from __future__ import annotations

import re
from typing import Any

from ._common import *  # noqa: F401,F403
from ..business_state_graph import _api_facts  # noqa: F401


def _adjacent_read_for_entity(entity: str, write_path: str) -> str:
    """Derive the observation (GET) endpoint for a write path — structurally.

    A write like `/api/payments/pay` or `/api/orders/{id}/cancel` has its read
    counterpart at the resource collection `/api/payments` or `/api/orders`.
    We compute that purely from path structure — the first two segments form
    the resource collection — with no per-project or per-industry endpoint map.
    """
    candidates = _observation_read_candidates(write_path)
    return candidates[0] if candidates else normalize_path_placeholders(str(write_path or ""))


def _documented_observation_read_candidates(write_path: str, api_doc: str) -> list[str]:
    """Return source-declared GET observers related to a write path."""
    if not str(api_doc or "").strip():
        return []
    try:
        _, _, endpoints = _api_facts(
            api_doc,
            re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I),
        )
    except Exception:
        return []
    write_norm = normalize_path_placeholders(str(write_path or ""))
    write_parts = [
        part.lower()
        for part in write_norm.strip("/").split("/")
        if part and not part.startswith("{")
    ]
    stop = {"api", "v1", "v2", "v3", "admin"}
    write_tokens = {part for part in write_parts if part not in stop}
    if not write_tokens:
        return []
    write_collection = collection_path(write_norm).rstrip("/")
    scored: list[tuple[tuple[int, int, int, int], str]] = []
    for endpoint in endpoints:
        if str(endpoint.get("method") or "").upper() not in {"GET", "HEAD"}:
            continue
        read_path = normalize_path_placeholders(str(endpoint.get("path") or ""))
        if not read_path.startswith("/"):
            continue
        read_parts = [
            part.lower()
            for part in read_path.strip("/").split("/")
            if part and not part.startswith("{")
        ]
        read_tokens = {part for part in read_parts if part not in stop}
        overlap = len(write_tokens & read_tokens)
        if overlap <= 0:
            continue
        prefix_match = 0 if (
            read_path.rstrip("/") == write_collection
            or read_path.rstrip("/").startswith(write_collection + "/")
            or write_norm.rstrip("/").startswith(read_path.rstrip("/") + "/")
        ) else 1
        placeholder_count = len(re.findall(r"\{[A-Za-z_]\w*\}", read_path))
        depth = read_path.count("/")
        scored.append(((prefix_match, -overlap, placeholder_count, depth), read_path))
    return list(dict.fromkeys(path for _score, path in sorted(scored, key=lambda item: item[0])))


def _observation_read_candidates(write_path: str) -> list[str]:
    """Return ordered GET observation paths for a write endpoint."""
    normalized = normalize_path_placeholders(str(write_path or ""))
    paths: list[str] = []
    parts = [p for p in normalized.strip("/").split("/") if p and "{" not in p]
    if len(parts) >= 2:
        paths.append("/" + "/".join(parts[:2]))
    coll = collection_path(normalized)
    if coll.startswith("/") and coll not in paths:
        paths.append(coll)
    alternates: list[str] = []
    if len(parts) >= 2:
        prefix, resource = parts[0], parts[1].lower()
        synthetic = f"/{prefix}/{resource}/{{id}}"
        if resource in {"inventory", "stock", "warehouse"}:
            synthetic = f"/{prefix}/{resource}/{{sku}}"
        alternates.extend(alternate_collection_paths(synthetic))
        # Action-style writes (/api/inventory/reserve) usually have no list at /api/inventory.
        if len(parts) > 2 and resource in {"inventory", "stock", "warehouse"} and alternates:
            def _obs_rank(candidate: str) -> tuple[int, int]:
                last = candidate.rstrip("/").rsplit("/", 1)[-1].lower()
                catalog = 0 if last in {"products", "product", "materials", "material", "items", "goods", "skus", "catalog"} else 1
                return (catalog, candidate.count("/"))
            paths = sorted(alternates, key=_obs_rank) + paths
        else:
            paths.extend(alternates)
    return list(dict.fromkeys(item for item in paths if item.startswith("/")))
