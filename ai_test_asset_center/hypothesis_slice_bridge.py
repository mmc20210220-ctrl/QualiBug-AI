"""Bind analyzer / LLM Reasoner hypotheses into source-only candidates.

Only hypotheses that bind to a real API-document endpoint become candidates.
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
    elif raw.endswith("es") and len(raw) > 4:
        variants.add(raw[:-2])
    elif raw.endswith("s") and len(raw) > 3 and not raw.endswith("ss"):
        variants.add(raw[:-1])
    elif raw.endswith(("s", "x", "z", "ch", "sh")):
        variants.add(raw + "es")
    elif not raw.endswith("s"):
        variants.add(raw + "s")
    return variants


_STRUCTURAL_ENTITY_SUFFIXES = {
    "detail",
    "details",
    "dto",
    "form",
    "info",
    "item",
    "items",
    "list",
    "lists",
    "model",
    "page",
    "record",
    "records",
    "request",
    "response",
    "view",
}


def _identifier_tokens(value: str) -> list[str]:
    """Split common API identifier spellings without relying on domain terms."""
    raw = _text(value)
    if not raw:
        return []
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    raw = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", raw)
    pieces = [
        piece.lower()
        for piece in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", raw)
        if len(piece.strip()) >= 2
    ]
    return list(dict.fromkeys(pieces))


def _expanded_identifier_tokens(value: str) -> set[str]:
    expanded: set[str] = set()
    for token in _identifier_tokens(value):
        expanded.add(token)
        expanded.update(_entity_variants(token))
    return expanded


def _normalized_token(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", _text(value).lower())


def _entity_action_hint(entity: str) -> tuple[str, str]:
    """Parse generic Entity.action hints emitted by LLM/analyzer engines."""
    raw = _text(entity).lower()
    if not raw:
        return "", ""
    raw = re.sub(r"\([^)]*\)", "", raw)
    parts = [
        item
        for item in re.split(r"[\s./:#>]+", raw)
        if item and item not in {"api", "service", "controller", "handler"}
    ]
    if len(parts) < 2:
        return raw.strip(), ""
    return parts[0], parts[-1]


def _path_action_tokens(path: str) -> set[str]:
    tokens: set[str] = set()
    for segment in _text(path).lower().strip("/").split("/"):
        if not segment or segment.startswith(":") or segment.startswith("{"):
            continue
        for normalized in _identifier_tokens(segment):
            if not normalized:
                continue
            tokens.add(normalized)
            tokens.update(_entity_variants(normalized))
    return tokens


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
    """Return the risk family declared on a hypothesis, WITHOUT collapsing
    unknown / open bug families to "invariant".

    An explicitly declared ``family`` / ``category`` / ``risk_type`` is preserved
    verbatim. The downstream lossless registry (``test_obligation.resolve_risk_family``
    and the obligation adapter) re-resolves it: registered families compile,
    open / unregistered families (performance_latency, stability_reliability,
    event_delivery_consistency, ui_state_consistency, message_chain, …) are
    marked ``BLOCKED`` and counted instead of being silently rewritten into a
    generic invariant/validation obligation. The ``_ORACLE_BY_FAMILY`` substring
    heuristic is retained only as a last-resort hint for hypotheses that declare
    no family at all and can only be inferred from free text; its final
    "invariant" fallback is kept for that genuine no-signal case, because
    "invariant" resolves to the canonical "validation" family (a real,
    compilable obligation) — collapsing it would only lose findings.
    """
    # Explicitly declared family takes precedence and is preserved as-is.
    for key in ("family", "category", "risk_type"):
        raw = _text(hypothesis.get(key)).lower()
        if raw:
            return raw
    # Engine name as a family hint (e.g. multi_tenant / business_rules engines).
    raw_engine = _text(hypothesis.get("_reasoner_engine")).lower()
    if raw_engine:
        for token, _mapping in _ORACLE_BY_FAMILY.items():
            if token in raw_engine:
                return token
        if raw_engine in _ORACLE_BY_FAMILY:
            return raw_engine
    # No declared family: infer from free text via the known vocabulary.
    blob = " ".join(
        _text(hypothesis.get(k))
        for k in ("title", "description", "trigger", "expected_behavior")
    ).lower()
    for token in _ORACLE_BY_FAMILY:
        if token in blob:
            return token
    return "invariant"


def _oracle_binding(family: str) -> tuple[str, str, str, str]:
    """Resolve a family to its legacy slice oracle binding, routed through the
    single registry authority (``test_obligation.resolve_risk_family``).

    Unknown / open bug families (performance_latency, stability_reliability,
    event_delivery_consistency, ui_state_consistency, message_chain, …) no longer
    silently fall back to the invariant consistency oracle. They return kind
    ``"unregistered"`` with the registry reason code, so the legacy champion slice
    records them as visibly blocked rather than as a bogus invariant check.
    """
    from .test_obligation import resolve_risk_family

    resolution = resolve_risk_family(family)
    reason_code = resolution.get("reason_code") or ""
    if resolution.get("registered"):
        kind, oracle_field, oracle_class = _ORACLE_BY_FAMILY.get(
            resolution["canonical"],
            ("invariant", "_consistency_oracle", "ConsistencyOracle"),
        )
        return (kind, oracle_field, oracle_class, reason_code)
    return ("unregistered", "_unregistered_oracle", "UnregisteredOracle", reason_code)


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


def _binding_strategies() -> set[str]:
    from .policy_wiring import get_policy_value

    configured = get_policy_value(
        "discovery",
        "endpoint_binding_strategy",
        [
            "source_operation_id",
            "method_path_shape",
            "schema_parameter_compatibility",
            "documented_example_binding",
        ],
    )
    return {
        _text(item).lower()
        for item in (configured if isinstance(configured, list) else [])
        if _text(item)
    }


def _operation_id_hint(hypothesis: dict[str, Any]) -> str:
    for key in ("operation_id", "operationId", "source_operation_id", "interface_id"):
        value = _text(hypothesis.get(key))
        if value:
            return value.lower()
    return ""


def _strategy_paths(hypothesis: dict[str, Any], strategies: set[str]) -> list[str]:
    paths = list(_endpoint_paths_from_hypothesis(hypothesis))
    extra_keys: list[str] = []
    if "documented_example_binding" in strategies:
        extra_keys.extend(("documented_example", "request_example", "api_example"))
    if "observed_operation_binding" in strategies:
        extra_keys.extend(("observed_operation", "observed_request", "prior_success_receipt"))
    for key in extra_keys:
        value = hypothesis.get(key)
        rows = value if isinstance(value, list) else [value]
        for row in rows:
            if isinstance(row, dict):
                path = _text(row.get("path") or row.get("api_path") or row.get("route"))
                if path.startswith("/") and path not in paths:
                    paths.append(path)
    return paths


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

    strategies = _binding_strategies()
    operation_hint = _operation_id_hint(hypothesis)
    if "source_operation_id" in strategies and operation_hint:
        for endpoint in catalog:
            if _text(endpoint.get("operation_id") or endpoint.get("operationId")).lower() == operation_hint:
                return {
                    "path": _text(endpoint.get("path")),
                    "method": _text(endpoint.get("method")).upper() or "GET",
                    "entity": _text(endpoint.get("entity")) or _text(hypothesis.get("entity")) or "resource",
                }

    hinted = _strategy_paths(hypothesis, strategies)
    by_path = {_text(ep.get("path")): ep for ep in catalog}

    for path in hinted:
        if path in by_path and (
            "method_path_shape" in strategies
            or "documented_example_binding" in strategies
            or "observed_operation_binding" in strategies
        ):
            ep = by_path[path]
            return {
                "path": _text(ep.get("path")),
                "method": _text(ep.get("method")).upper() or "GET",
                "entity": _text(ep.get("entity")) or _text(hypothesis.get("entity")) or "resource",
            }
        # Prefix / structural match against catalog paths
        for ep in catalog if "method_path_shape" in strategies else []:
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

    entity_raw = _text(hypothesis.get("entity") or hypothesis.get("source_entity") or hypothesis.get("resource"))
    entity = entity_raw.lower()
    method_hint = _method_hint_from_hypothesis(hypothesis)
    if "schema_parameter_compatibility" in strategies:
        declared_parameters = hypothesis.get("parameters") or hypothesis.get("request_parameters") or {}
        if isinstance(declared_parameters, dict):
            parameter_names = {str(key).strip().lower() for key in declared_parameters if str(key).strip()}
        elif isinstance(declared_parameters, list):
            parameter_names = {
                _text(item.get("name") if isinstance(item, dict) else item).lower()
                for item in declared_parameters
                if _text(item.get("name") if isinstance(item, dict) else item)
            }
        else:
            parameter_names = set()
        compatible: list[tuple[int, dict[str, Any]]] = []
        for endpoint in catalog:
            endpoint_parameters = endpoint.get("parameters") or []
            endpoint_names = {
                _text(item.get("name") if isinstance(item, dict) else item).lower()
                for item in endpoint_parameters
                if _text(item.get("name") if isinstance(item, dict) else item)
            }
            overlap = len(parameter_names & endpoint_names)
            method = _text(endpoint.get("method")).upper() or "GET"
            if overlap and (not method_hint or method == method_hint):
                compatible.append((overlap, endpoint))
        if compatible:
            _, endpoint = max(compatible, key=lambda item: (item[0], _text(item[1].get("path"))))
            return {
                "path": _text(endpoint.get("path")),
                "method": _text(endpoint.get("method")).upper() or "GET",
                "entity": _text(endpoint.get("entity")) or entity or "resource",
            }
    compound_entity_tokens = _identifier_tokens(entity_raw)
    if len(compound_entity_tokens) >= 2:
        core_tokens = [
            token
            for token in compound_entity_tokens
            if token not in _STRUCTURAL_ENTITY_SUFFIXES
        ] or compound_entity_tokens
        required_core_overlap = min(2, len(core_tokens))

        def _overlap_count(parts: list[str], endpoint_tokens: set[str]) -> int:
            count = 0
            for part in parts:
                variants = _entity_variants(part)
                variants.add(part)
                if variants & endpoint_tokens:
                    count += 1
            return count

        compound_matches: list[tuple[int, dict[str, Any]]] = []
        for endpoint in catalog:
            endpoint_tokens = _path_action_tokens(_text(endpoint.get("path")))
            endpoint_tokens.update(_expanded_identifier_tokens(_text(endpoint.get("entity"))))
            endpoint_tokens.update(_expanded_identifier_tokens(_text(endpoint.get("action"))))
            endpoint_tokens.update(_expanded_identifier_tokens(_text(endpoint.get("summary"))))
            core_overlap = _overlap_count(core_tokens, endpoint_tokens)
            if core_overlap < required_core_overlap:
                continue
            full_overlap = _overlap_count(compound_entity_tokens, endpoint_tokens)
            method = _text(endpoint.get("method")).upper() or "GET"
            method_bonus = 3 if method_hint and method == method_hint else 0
            method_penalty = 2 if method_hint and method != method_hint else 0
            read_bonus = 1 if not method_hint and method == "GET" else 0
            score = (core_overlap * 10) + (full_overlap * 3) + method_bonus + read_bonus - method_penalty
            compound_matches.append((score, endpoint))
        if compound_matches:
            _, endpoint = max(
                compound_matches,
                key=lambda item: (item[0], _text(item[1].get("path"))),
            )
            return {
                "path": _text(endpoint.get("path")),
                "method": _text(endpoint.get("method")).upper() or "GET",
                "entity": _text(endpoint.get("entity")) or core_tokens[0] or entity or "resource",
            }
    entity_name, action_name = _entity_action_hint(entity)
    if entity_name and action_name:
        entity_tokens = {_normalized_token(item) for item in _entity_variants(entity_name)}
        entity_tokens.discard("")
        action_tokens = {_normalized_token(item) for item in _entity_variants(action_name)}
        action_tokens.discard("")
        action_matches: list[tuple[int, dict[str, Any]]] = []
        for endpoint in catalog:
            endpoint_tokens = _path_action_tokens(_text(endpoint.get("path")))
            endpoint_tokens.update(_tokens(_text(endpoint.get("entity"))))
            endpoint_tokens.update(_tokens(_text(endpoint.get("action"))))
            if not (entity_tokens & endpoint_tokens) or not (action_tokens & endpoint_tokens):
                continue
            method = _text(endpoint.get("method")).upper() or "GET"
            method_bonus = 2 if method_hint and method == method_hint else 0
            tail = _text(endpoint.get("path")).lower().rstrip("/")
            action_tail_bonus = 1 if any(tail.endswith("/" + token) for token in action_tokens) else 0
            write_bonus = 1 if method in {"POST", "PUT", "PATCH", "DELETE"} else 0
            action_matches.append((10 + method_bonus + action_tail_bonus + write_bonus, endpoint))
        if action_matches:
            _, endpoint = max(action_matches, key=lambda item: (item[0], _text(item[1].get("path"))))
            return {
                "path": _text(endpoint.get("path")),
                "method": _text(endpoint.get("method")).upper() or "GET",
                "entity": _text(endpoint.get("entity")) or entity_name,
            }
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


def _binding_drop_reason(
    hypothesis: dict[str, Any],
    api_endpoints: list[dict[str, Any]],
) -> str:
    """Classify an unbound hypothesis without inventing an endpoint."""
    catalog = [
        ep
        for ep in (api_endpoints or [])
        if isinstance(ep, dict) and _text(ep.get("path")).startswith("/")
    ]
    if not catalog:
        return "api_catalog_empty"
    strategies = _binding_strategies()
    if "source_operation_id" in strategies and _operation_id_hint(hypothesis):
        return "operation_id_not_in_catalog"
    if _strategy_paths(hypothesis, strategies):
        return "path_hint_not_in_catalog"
    if _text(
        hypothesis.get("entity")
        or hypothesis.get("source_entity")
        or hypothesis.get("resource")
    ):
        return "entity_not_in_catalog"
    blob = " ".join(
        _text(hypothesis.get(key))
        for key in ("title", "description", "category", "family")
    )
    return "semantic_overlap_not_found" if _tokens(blob) else "binding_signals_missing"


def _binding_drop_sample(
    hypothesis: dict[str, Any],
    *,
    origin: str,
    reason: str,
) -> dict[str, Any]:
    """Build a bounded, secret-redacted diagnostic without request payloads."""
    raw = {
        "reason": reason,
        "origin": origin,
        "hypothesis_id": _text(
            hypothesis.get("hypothesis_id") or hypothesis.get("id")
        )[:120],
        "engine": _text(
            hypothesis.get("_reasoner_engine") or hypothesis.get("engine")
        )[:120],
        "family": _hypothesis_family(hypothesis),
        "method_hint": _method_hint_from_hypothesis(hypothesis),
        "operation_id_hint": _operation_id_hint(hypothesis)[:120],
        "path_hints": _endpoint_paths_from_hypothesis(hypothesis)[:3],
        "entity_hint": _text(
            hypothesis.get("entity")
            or hypothesis.get("source_entity")
            or hypothesis.get("resource")
        )[:120],
        "title_excerpt": _text(hypothesis.get("title"))[:200],
    }
    from .artifact_redactor import redact_artifact

    redacted, _ = redact_artifact(raw)
    return redacted if isinstance(redacted, dict) else {"reason": reason, "origin": origin}


def _binding_diagnostic_sample_limit() -> int:
    from .policy_wiring import get_policy_value

    return max(
        1,
        min(
            int(
                get_policy_value(
                    "discovery", "endpoint_binding_diagnostic_sample_limit", 20
                )
                or 1
            ),
            100,
        ),
    )


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
            if isinstance(item, dict) and any(
                _text(item.get(field))
                for field in ("source_id", "locator", "quote", "quote_hash", "kind")
            ):
                preserved = {
                    key: value
                    for key, value in item.items()
                    if key in {"source_id", "version", "locator", "kind", "quote", "quote_hash"}
                }
                preserved["kind"] = _text(item.get("kind")) or "source"
                preserved["quote"] = _text(item.get("quote"))[:300]
                refs.append(preserved)
    return refs


def _depth_fields(hypothesis: dict[str, Any]) -> dict[str, Any]:
    """Preserve the deep-comprehension fields the bridge would otherwise drop.

    Reasoner hypotheses carry cross-entity cascade chains, lifecycle source
    states, and multi-step verification intent. The single-operation obligation
    model cannot express all of them, but discarding them silently is a
    comprehension loss. These fields ride along on the candidate so the depth
    stays observable and can be turned into an explicit coverage gap instead of
    disappearing.
    """
    depth: dict[str, Any] = {}
    for key in (
        "cascade_chain",
        "cascade_check",
        "cascade_summary",
        "source_state",
        "target_entity",
        "symptoms_if_broken",
        "adversarial_angle",
        "negative_space_findings",
        "semantic_hypothesis_refs",
    ):
        value = hypothesis.get(key)
        if value not in (None, "", [], {}):
            depth[key] = value
    vm = hypothesis.get("verification_method")
    if isinstance(vm, dict):
        steps = {
            key: _text(vm.get(key))
            for key in ("step1", "step2", "step3", "step4", "check1", "check2", "check3")
            if _text(vm.get(key))
        }
        if steps:
            depth["verification_steps"] = steps
    return depth


def _normalized_path_shape(value: Any) -> str:
    path = _text(value).split("?", 1)[0].strip()
    if not path.startswith("/"):
        return ""
    return re.sub(r"\{[^}]+\}|:([A-Za-z_][A-Za-z0-9_]*)", "{}", path).rstrip("/") or "/"


def _source_operation_sequence(
    hypothesis: dict[str, Any],
    api_endpoints: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Resolve multi-step hints only through unique source-declared endpoints.

    The reasoner's step text is attention guidance, not execution authority.
    This function merely records exact catalog joins; the obligation adapter
    still requires one unique source-declared process graph before compiling
    the sequence.
    """
    hints: list[tuple[str, str]] = [
        (path, "") for path in _endpoint_paths_from_hypothesis(hypothesis)
    ]
    verification = hypothesis.get("verification_method")
    if isinstance(verification, dict):
        for key in (
            "step1", "step2", "step3", "step4",
            "check1", "check2", "check3",
        ):
            text = _text(verification.get(key))
            if not text:
                continue
            method_match = re.search(
                r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
                text.upper(),
            )
            method = method_match.group(1) if method_match else ""
            for path in re.findall(r"(/[A-Za-z0-9_:\-{}./]+)", text):
                if len(path) > 1 and not path.startswith("//"):
                    hints.append((path, method))

    catalog = [
        endpoint
        for endpoint in api_endpoints
        if isinstance(endpoint, dict)
        and _normalized_path_shape(endpoint.get("path"))
        and _text(endpoint.get("operation_id") or endpoint.get("operationId"))
    ]
    operation_refs: list[str] = []
    operation_paths: list[str] = []
    seen: set[str] = set()
    for hinted_path, hinted_method in hints:
        shape = _normalized_path_shape(hinted_path)
        matches = [
            endpoint
            for endpoint in catalog
            if _normalized_path_shape(endpoint.get("path")) == shape
            and (
                not hinted_method
                or _text(endpoint.get("method")).upper() == hinted_method
            )
        ]
        if len(matches) != 1:
            continue
        endpoint = matches[0]
        operation_ref = _text(
            endpoint.get("operation_id") or endpoint.get("operationId")
        )
        if operation_ref in seen:
            continue
        seen.add(operation_ref)
        operation_refs.append(operation_ref)
        operation_paths.append(_text(endpoint.get("path")))
    if len(operation_refs) < 2:
        return [], []
    return operation_refs, operation_paths


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


def hypotheses_to_source_candidates(
    hypotheses: list[dict],
    *,
    api_endpoints: list[dict],
    origin: str,
) -> tuple[list[dict], dict]:
    """Convert hypotheses into source-only, endpoint-bound candidates.

    Returns ``(candidates, funnel_stats)`` where funnel_stats contains at least
    ``input``, ``bound``, ``dropped_no_endpoint``, and ``by_origin``.
    """
    origin_key = _text(origin) or "unknown"
    items = [h for h in (hypotheses or []) if isinstance(h, dict)]
    candidates: list[dict] = []
    dropped = 0
    dropped_reason_counts: dict[str, int] = {}
    dropped_samples: list[dict[str, Any]] = []
    diagnostic_sample_limit = _binding_diagnostic_sample_limit()
    by_origin: dict[str, dict[str, int]] = {
        origin_key: {"input": len(items), "bound": 0, "dropped_no_endpoint": 0},
    }

    for hypothesis in items:
        binding = _bind_endpoint(hypothesis, api_endpoints or [])
        if not binding or not binding.get("path"):
            dropped += 1
            by_origin[origin_key]["dropped_no_endpoint"] += 1
            reason = _binding_drop_reason(hypothesis, api_endpoints or [])
            dropped_reason_counts[reason] = dropped_reason_counts.get(reason, 0) + 1
            if len(dropped_samples) < diagnostic_sample_limit:
                dropped_samples.append(
                    _binding_drop_sample(
                        hypothesis,
                        origin=origin_key,
                        reason=reason,
                    )
                )
            continue

        family = _hypothesis_family(hypothesis)
        entity = binding["entity"] or _text(hypothesis.get("entity")) or "resource"
        path = binding["path"]
        method = binding["method"]
        hyp_id = _text(hypothesis.get("hypothesis_id") or hypothesis.get("id") or hypothesis.get("title"))[:80]
        if not hyp_id:
            hyp_id = behavior_slice_id("candidate", entity, origin_key, family, method, path)[:80]
        matched_endpoint = next(
            (
                endpoint
                for endpoint in api_endpoints
                if isinstance(endpoint, dict)
                and _text(endpoint.get("path")) == path
                and (_text(endpoint.get("method")).upper() or "GET") == method
            ),
            {},
        )
        intent = _text(
            hypothesis.get("expected_behavior")
            or hypothesis.get("title")
            or hypothesis.get("description")
        )
        candidate: dict[str, Any] = {
            "candidate_id": hyp_id,
            "risk_family": family,
            "method": method,
            "path": path,
            "operation_id": _text(
                matched_endpoint.get("operation_id")
                or matched_endpoint.get("operationId")
            ),
            "entity": entity,
            "priority": _priority(hypothesis),
            "source_refs": _source_refs(hypothesis, origin_key),
            "property": {"source_intent": intent[:500]} if intent else {},
            "actor_ref_hint": _text(
                hypothesis.get("actor_role")
                or hypothesis.get("actor")
                or hypothesis.get("required_role")
            ),
            "origin": origin_key,
            "engine": _text(
                hypothesis.get("_reasoner_engine") or hypothesis.get("engine")
            ),
        }
        depth = _depth_fields(hypothesis)
        if depth:
            operation_refs, operation_paths = _source_operation_sequence(
                hypothesis,
                api_endpoints or [],
            )
            if operation_refs:
                depth["operation_refs"] = operation_refs
                depth["operation_paths"] = operation_paths
            candidate["depth"] = depth
        candidates.append(candidate)
        by_origin[origin_key]["bound"] += 1

    funnel_stats = {
        "input": len(items),
        "bound": len(candidates),
        "depth_preserved": sum(1 for c in candidates if c.get("depth")),
        "dropped_no_endpoint": dropped,
        "dropped_reason_counts": dict(sorted(dropped_reason_counts.items())),
        "dropped_samples": dropped_samples,
        "dropped_samples_truncated": max(0, dropped - len(dropped_samples)),
        "by_origin": by_origin,
        "origin": origin_key,
    }
    return candidates, funnel_stats


def _candidate_to_legacy_slice(candidate: dict[str, Any]) -> dict[str, Any]:
    """Compatibility projection for the temporary legacy champion only."""

    family = _text(candidate.get("risk_family")) or "invariant"
    kind, oracle_field, oracle_name, reason_code = _oracle_binding(family)
    entity = _text(candidate.get("entity")) or "resource"
    method = _text(candidate.get("method")).upper() or "GET"
    path = _text(candidate.get("path"))
    origin = _text(candidate.get("origin")) or "unknown"
    candidate_id = _text(candidate.get("candidate_id"))
    slice_row: dict[str, Any] = {
        "slice_id": behavior_slice_id(
            kind,
            entity,
            origin,
            family,
            method,
            path,
            candidate_id,
        ),
        "entity": entity,
        "kind": kind,
        "states": [],
        "endpoints": [path],
        "priority": float(candidate.get("priority") or 0.5),
        "source_refs": list(candidate.get("source_refs") or []),
        "evidence_gaps": [],
        oracle_field: oracle_name,
        "_hypothesis_origin": origin,
        "_hypothesis_id": candidate_id,
        "_hypothesis_family": family,
        "_bound_method": method,
        "_bound_path": path,
        "_selection_family": f"unified:{origin}:{family}",
        "_source_candidate_id": candidate_id,
        "_family_reason_code": reason_code,
    }
    if kind == "unregistered":
        # Open / unknown bug family: recorded as visibly unregistered, never
        # compiled as a bogus invariant check. Breadth loss stays observable
        # and countable in the legacy champion slice.
        slice_row["_oracle_binding_status"] = "UNREGISTERED"
        slice_row["_unregistered_oracle"] = oracle_name
        return slice_row
    if kind == "permission":
        slice_row.update({
            "_permission_method": method,
            "_permission_path": path,
            "_permission_actor": _text(candidate.get("actor_ref_hint")),
            "_permission_expected_permitted": [],
        })
    elif kind == "concurrency":
        slice_row.update({
            "_concurrency_method": method,
            "_concurrency_path": path,
        })
    elif kind == "money":
        slice_row.update({
            "_money_method": method,
            "_money_path": path,
        })
        if oracle_field == "_inventory_oracle":
            slice_row["_inventory_oracle"] = oracle_name
    else:
        property_spec = candidate.get("property") if isinstance(candidate.get("property"), dict) else {}
        invariant_text = _text(property_spec.get("source_intent"))
        if invariant_text:
            slice_row["_invariant_text"] = invariant_text[:300]
        if oracle_field != "_consistency_oracle":
            slice_row.setdefault("_consistency_oracle", "ConsistencyOracle")
    return slice_row


def hypotheses_to_slices(
    hypotheses: list[dict],
    *,
    api_endpoints: list[dict],
    origin: str,
) -> tuple[list[dict], dict]:
    """Project source candidates into temporary legacy-champion slices."""

    candidates, funnel = hypotheses_to_source_candidates(
        hypotheses,
        api_endpoints=api_endpoints,
        origin=origin,
    )
    return [_candidate_to_legacy_slice(candidate) for candidate in candidates], funnel


def hypotheses_to_obligations(
    hypotheses: list[dict],
    *,
    api_endpoints: list[dict],
    behavior_ir: dict[str, Any],
    origin: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Route bridge candidates through the pure Obligation adapter."""

    from .obligation_source_adapter import adapt_source_candidates_to_obligations

    candidates, funnel = hypotheses_to_source_candidates(
        hypotheses,
        api_endpoints=api_endpoints,
        origin=origin,
    )
    adapted = adapt_source_candidates_to_obligations(candidates, behavior_ir)
    return adapted, {
        **funnel,
        "adapted_obligation_count": len(adapted["obligations"]),
        "adapter_coverage_gap_count": len(adapted["coverage_gaps"]),
        "depth_carried_count": int(adapted.get("depth_carried_count") or 0),
        "depth_uncompiled_count": int(adapted.get("depth_uncompiled_count") or 0),
    }
