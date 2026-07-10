"""Bridge analyzer / LLM Reasoner hypotheses into source-bound behavior slices.

Only hypotheses that bind to a real API-document endpoint become slices.
Unbound hypotheses are dropped and counted in funnel stats — never injected
as non-executable noise.
"""
from __future__ import annotations

import re
from typing import Any

from .business_state_graph import behavior_slice_id

# Oracle names that exist in oracle_engine.py (verified — do not invent).
_ORACLE_BY_FAMILY: dict[str, tuple[str, str, str]] = {
    # family/category token → (slice_kind, oracle_field, oracle_class)
    # Prefer kinds that SemanticScenarioGenerator can materialize from endpoint-only
    # metadata (invariant / permission / concurrency / money). Isolation without
    # actor pairs falls back to invariant + TenantIsolationOracle.
    "permission": ("permission", "_permission_oracle", "PermissionOracle"),
    "authorization": ("permission", "_permission_oracle", "PermissionOracle"),
    "auth": ("permission", "_permission_oracle", "PermissionOracle"),
    "access_control": ("permission", "_permission_oracle", "PermissionOracle"),
    "tenant": ("invariant", "_isolation_oracle", "TenantIsolationOracle"),
    "multi_tenant": ("invariant", "_isolation_oracle", "TenantIsolationOracle"),
    "isolation": ("invariant", "_isolation_oracle", "TenantIsolationOracle"),
    "privacy": ("invariant", "_isolation_oracle", "PrivacyOracle"),
    "concurrency": ("concurrency", "_concurrency_oracle", "ConcurrencyOracle"),
    "race": ("concurrency", "_concurrency_oracle", "ConcurrencyOracle"),
    "async_task": ("concurrency", "_concurrency_oracle", "ConcurrencyOracle"),
    "idempotency": ("invariant", "_idempotency_oracle", "IdempotencyOracle"),
    "conservation": ("money", "_money_oracle", "MoneyOracle"),
    "money": ("money", "_money_oracle", "MoneyOracle"),
    "inventory": ("money", "_inventory_oracle", "InventoryOracle"),
    "stock": ("money", "_inventory_oracle", "InventoryOracle"),
    "state_machine": ("invariant", "_state_oracle", "StateOracle"),
    "state": ("invariant", "_state_oracle", "StateOracle"),
    "lifecycle": ("invariant", "_state_oracle", "StateOracle"),
    "workflow": ("invariant", "_workflow_oracle", "WorkflowOracle"),
    "cache": ("invariant", "_cache_oracle", "CacheConsistencyOracle"),
    "cache_consistency": ("invariant", "_cache_oracle", "CacheConsistencyOracle"),
    "consistency": ("invariant", "_consistency_oracle", "ConsistencyOracle"),
    "business_rules": ("invariant", "_consistency_oracle", "ConsistencyOracle"),
    "invariant": ("invariant", "_consistency_oracle", "ConsistencyOracle"),
    "transaction": ("invariant", "_transaction_oracle", "TransactionOracle"),
    "audit": ("invariant", "_audit_oracle", "AuditOracle"),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-z0-9_\u4e00-\u9fff]+", text.lower()) if len(tok) >= 2}


def _entity_variants(entity: str) -> set[str]:
    """Singular/plural variants for generic entity→endpoint matching."""
    raw = _text(entity).lower()
    if not raw:
        return set()
    variants = {raw}
    if raw.endswith("ies") and len(raw) > 4:
        variants.add(raw[:-3] + "y")
    elif raw.endswith("s") and len(raw) > 3 and not raw.endswith("ss"):
        variants.add(raw[:-1])
    elif not raw.endswith("s"):
        variants.add(raw + "s")
    return variants


def _method_hint_from_hypothesis(hypothesis: dict[str, Any]) -> str:
    for key in ("method", "http_method", "verb"):
        method = _text(hypothesis.get(key)).upper()
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            return method
    vm = hypothesis.get("verification_method")
    if isinstance(vm, dict):
        method = _text(vm.get("method") or vm.get("http_method")).upper()
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            return method
    blob = " ".join(
        _text(hypothesis.get(k))
        for k in ("title", "description", "trigger", "expected_behavior")
    ).upper()
    for method in ("POST", "PATCH", "PUT", "DELETE", "GET"):
        if re.search(rf"\b{method}\b", blob):
            return method
    return ""


def _hypothesis_family(hypothesis: dict[str, Any]) -> str:
    for key in ("family", "category", "risk_type", "_reasoner_engine"):
        raw = _text(hypothesis.get(key)).lower()
        if not raw:
            continue
        for token, mapping in _ORACLE_BY_FAMILY.items():
            if token in raw:
                return token
        # engine names like business_rules / multi_tenant
        if raw in _ORACLE_BY_FAMILY:
            return raw
    return "invariant"


def _oracle_binding(family: str) -> tuple[str, str, str]:
    return _ORACLE_BY_FAMILY.get(family, ("invariant", "_consistency_oracle", "ConsistencyOracle"))


def _endpoint_paths_from_hypothesis(hypothesis: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in (
        "related_endpoints",
        "affected_endpoints",
        "endpoints",
        "trigger",
        "api_path",
        "endpoint",
        "route",
        "path",
        "url",
    ):
        value = hypothesis.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip().startswith("/"):
                    paths.append(item.strip())
                elif isinstance(item, dict):
                    path = _text(item.get("path") or item.get("api_path"))
                    if path.startswith("/"):
                        paths.append(path)
        elif isinstance(value, str):
            text = value.strip()
            if text.startswith("/"):
                paths.append(text)
            else:
                for match in re.findall(r"(/[A-Za-z0-9_:\-{}./]+)", text):
                    if len(match) > 1 and not match.startswith("//"):
                        paths.append(match)
    vm = hypothesis.get("verification_method")
    if isinstance(vm, dict):
        path = _text(vm.get("path") or vm.get("api_path") or vm.get("endpoint"))
        if path.startswith("/"):
            paths.append(path)
        steps = vm.get("steps") or vm.get("calls") or []
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    p = _text(step.get("path") or step.get("api_path"))
                    if p.startswith("/"):
                        paths.append(p)
    # Scan free text for path-like tokens (generic, not industry-specific)
    blob = " ".join(
        _text(hypothesis.get(k))
        for k in ("title", "description", "expected_behavior", "actual_behavior", "verification_method")
        if not isinstance(hypothesis.get(k), (dict, list))
    )
    for match in re.findall(r"(/[A-Za-z0-9_:\-{}./]+)", blob):
        if len(match) > 1 and not match.startswith("//"):
            paths.append(match)
    # Dedupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _bind_endpoint(
    hypothesis: dict[str, Any],
    api_endpoints: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Bind hypothesis to a real endpoint from API document facts."""
    if not api_endpoints:
        return None
    catalog = [ep for ep in api_endpoints if isinstance(ep, dict) and _text(ep.get("path")).startswith("/")]
    if not catalog:
        return None

    hinted = _endpoint_paths_from_hypothesis(hypothesis)
    by_path = {_text(ep.get("path")): ep for ep in catalog}

    for path in hinted:
        if path in by_path:
            ep = by_path[path]
            return {
                "path": _text(ep.get("path")),
                "method": _text(ep.get("method")).upper() or "GET",
                "entity": _text(ep.get("entity")) or _text(hypothesis.get("entity")) or "resource",
            }
        # Prefix / structural match against catalog paths
        for ep in catalog:
            catalog_path = _text(ep.get("path"))
            if catalog_path == path or catalog_path.rstrip("/") == path.rstrip("/"):
                return {
                    "path": catalog_path,
                    "method": _text(ep.get("method")).upper() or "GET",
                    "entity": _text(ep.get("entity")) or _text(hypothesis.get("entity")) or "resource",
                }
            # Parameterized path equivalence: /orders/{id} vs /orders/:id
            norm_hint = re.sub(r"\{[^}]+\}|:([A-Za-z_][A-Za-z0-9_]*)", "{id}", path)
            norm_cat = re.sub(r"\{[^}]+\}|:([A-Za-z_][A-Za-z0-9_]*)", "{id}", catalog_path)
            if norm_hint == norm_cat:
                return {
                    "path": catalog_path,
                    "method": _text(ep.get("method")).upper() or "GET",
                    "entity": _text(ep.get("entity")) or _text(hypothesis.get("entity")) or "resource",
                }

    entity = _text(hypothesis.get("entity") or hypothesis.get("source_entity") or hypothesis.get("resource")).lower()
    method_hint = _method_hint_from_hypothesis(hypothesis)
    if entity:
        variants = _entity_variants(entity)
        entity_matches = [
            ep for ep in catalog
            if _text(ep.get("entity")).lower() in variants
            or any(tok in variants for tok in _tokens(_text(ep.get("path"))))
        ]
        if entity_matches:
            def _entity_rank(ep: dict[str, Any]) -> tuple[int, int, str]:
                method = _text(ep.get("method")).upper() or "GET"
                method_penalty = 0
                if method_hint:
                    method_penalty = 0 if method == method_hint else 1
                elif method in {"POST", "PUT", "PATCH", "DELETE"}:
                    method_penalty = 1
                return (method_penalty, 0 if method == "GET" else 1, _text(ep.get("path")))

            entity_matches.sort(key=_entity_rank)
            ep = entity_matches[0]
            return {
                "path": _text(ep.get("path")),
                "method": _text(ep.get("method")).upper() or "GET",
                "entity": _text(ep.get("entity")) or entity,
            }

    # Keyword overlap between hypothesis text and endpoint path/entity/action
    blob_tokens = _tokens(
        " ".join(
            [
                _text(hypothesis.get("title")),
                _text(hypothesis.get("description")),
                _text(hypothesis.get("category")),
                _text(hypothesis.get("family")),
                _text(hypothesis.get("entity")),
            ]
        )
    )
    if not blob_tokens:
        return None
    best: tuple[int, dict[str, str]] | None = None
    for ep in catalog:
        ep_tokens = _tokens(
            " ".join([_text(ep.get("path")), _text(ep.get("entity")), _text(ep.get("action")), _text(ep.get("summary"))])
        )
        score = len(blob_tokens & ep_tokens)
        if score < 1:
            continue
        method = _text(ep.get("method")).upper() or "GET"
        if method_hint and method != method_hint:
            score -= 1
        candidate = {
            "path": _text(ep.get("path")),
            "method": method,
            "entity": _text(ep.get("entity")) or "resource",
        }
        if best is None or score > best[0]:
            best = (score, candidate)
    if best:
        return best[1]

    # Path-segment overlap: bind when hypothesis text shares a concrete API segment.
    segment_best: tuple[int, dict[str, str]] | None = None
    for ep in catalog:
        path = _text(ep.get("path"))
        segments = {seg for seg in path.strip("/").split("/") if seg and not seg.startswith(":") and not seg.startswith("{")}
        if not segments:
            continue
        hits = len(blob_tokens & segments)
        if hits < 1:
            continue
        method = _text(ep.get("method")).upper() or "GET"
        rank = hits + (2 if method_hint and method == method_hint else 0)
        candidate = {
            "path": path,
            "method": method,
            "entity": _text(ep.get("entity")) or "resource",
        }
        if segment_best is None or rank > segment_best[0]:
            segment_best = (rank, candidate)
    return segment_best[1] if segment_best else None


def _source_refs(hypothesis: dict[str, Any], origin: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    engine = _text(hypothesis.get("_reasoner_engine") or hypothesis.get("engine") or hypothesis.get("category"))
    quote = _text(hypothesis.get("title") or hypothesis.get("description") or hypothesis.get("hypothesis_id"))
    refs.append({
        "kind": f"hypothesis:{origin}",
        "quote": quote[:300] or f"{origin}_hypothesis",
    })
    if engine:
        refs.append({"kind": "engine", "quote": engine[:120]})
    existing = hypothesis.get("source_refs")
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and (_text(item.get("quote")) or _text(item.get("kind"))):
                refs.append({
                    "kind": _text(item.get("kind")) or "source",
                    "quote": _text(item.get("quote"))[:300],
                })
    return refs


def _priority(hypothesis: dict[str, Any]) -> float:
    for key in ("priority", "confidence", "confidence_score", "exploit_potential"):
        raw = hypothesis.get(key)
        try:
            value = float(raw)
            if 0.0 <= value <= 1.0:
                return value
            if value > 1.0:
                return min(1.0, value / 100.0)
        except (TypeError, ValueError):
            continue
    severity = _text(hypothesis.get("severity")).upper()
    return {"P0": 0.9, "P1": 0.75, "P2": 0.55, "P3": 0.4}.get(severity, 0.5)


def hypotheses_to_slices(
    hypotheses: list[dict],
    *,
    api_endpoints: list[dict],
    origin: str,
) -> tuple[list[dict], dict]:
    """Convert hypotheses into source-grounded behavior slices.

    Returns ``(slices, funnel_stats)`` where funnel_stats contains at least
    ``input``, ``bound``, ``dropped_no_endpoint``, and ``by_origin``.
    """
    origin_key = _text(origin) or "unknown"
    items = [h for h in (hypotheses or []) if isinstance(h, dict)]
    slices: list[dict] = []
    dropped = 0
    by_origin: dict[str, dict[str, int]] = {
        origin_key: {"input": len(items), "bound": 0, "dropped_no_endpoint": 0},
    }

    for hypothesis in items:
        binding = _bind_endpoint(hypothesis, api_endpoints or [])
        if not binding or not binding.get("path"):
            dropped += 1
            by_origin[origin_key]["dropped_no_endpoint"] += 1
            continue

        family = _hypothesis_family(hypothesis)
        kind, oracle_field, oracle_name = _oracle_binding(family)
        entity = binding["entity"] or _text(hypothesis.get("entity")) or "resource"
        path = binding["path"]
        method = binding["method"]
        hyp_id = _text(hypothesis.get("hypothesis_id") or hypothesis.get("id") or hypothesis.get("title"))[:80]
        slice_id = behavior_slice_id(kind, entity, origin_key, family, method, path, hyp_id)

        slice_row: dict[str, Any] = {
            "slice_id": slice_id,
            "entity": entity,
            "kind": kind,
            "states": [],
            "endpoints": [path],
            "priority": _priority(hypothesis),
            "source_refs": _source_refs(hypothesis, origin_key),
            "evidence_gaps": [],
            oracle_field: oracle_name,
            "_hypothesis_origin": origin_key,
            "_hypothesis_id": hyp_id,
            "_hypothesis_family": family,
            "_bound_method": method,
            "_bound_path": path,
            "_selection_family": f"unified:{origin_key}:{family}",
        }
        # Kind-specific fields expected by SemanticScenarioGenerator fallbacks
        if kind == "permission":
            slice_row["_permission_method"] = method
            slice_row["_permission_path"] = path
            slice_row["_permission_actor"] = _text(
                hypothesis.get("actor_role")
                or hypothesis.get("actor")
                or hypothesis.get("required_role")
            )
            slice_row["_permission_expected_permitted"] = []
        elif kind == "concurrency":
            slice_row["_concurrency_method"] = method
            slice_row["_concurrency_path"] = path
        elif kind == "money":
            slice_row["_money_method"] = method
            slice_row["_money_path"] = path
            if oracle_field == "_inventory_oracle":
                slice_row["_inventory_oracle"] = oracle_name
        else:
            # invariant / observation-friendly
            invariant_text = _text(
                hypothesis.get("expected_behavior")
                or hypothesis.get("title")
                or hypothesis.get("description")
            )
            if invariant_text:
                slice_row["_invariant_text"] = invariant_text[:300]
            slice_row.setdefault(oracle_field, oracle_name)
            if oracle_field != "_consistency_oracle":
                slice_row.setdefault("_consistency_oracle", "ConsistencyOracle")

        slices.append(slice_row)
        by_origin[origin_key]["bound"] += 1

    funnel_stats = {
        "input": len(items),
        "bound": len(slices),
        "dropped_no_endpoint": dropped,
        "by_origin": by_origin,
        "origin": origin_key,
    }
    return slices, funnel_stats
