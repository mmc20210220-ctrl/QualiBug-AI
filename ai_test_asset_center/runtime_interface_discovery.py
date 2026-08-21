"""Source-derived, read-only discovery of undocumented runtime interfaces.

The planner derives bounded GET candidates from source-declared route
vocabulary.  The executor acquires observations only through correlated,
governed requests so every probe remains present in the obligation ledger and
evaluator-owned gateway.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .operational_receipts import build_execution_operational_receipt
from .sandbox_write_executor import _http_request
from .sandbox_write_executor_base import evaluator_request_trace


PLAN_SCHEMA = "qualibug.runtime-interface-discovery-plan.v1"
OBSERVATION_SCHEMA = "qualibug.runtime-interface-observation.v1"
_PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}|:[A-Za-z_][A-Za-z0-9_]*")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ── P2 instrumentation: bounded repeat-sampling for latency/stability ──
# Read-only GET only, sequential, no state mutation, low risk.  Retries are
# disabled on the extra samples so each is a single clean attempt (the latency
# observer rejects multi-attempt durations).  Product-owned methodology
# default, not a business SLA.  This is what lets open-class bug families
# (performance_latency / stability_reliability) become reachable on a system
# with no source-declared contract — the governed probe now records the
# observations those contracts are derived from.
#
# Must be >= 5: the stability surface (formal_stability_surface) hard-requires
# sample_count in [5, 20] before it will compile a stability_reliability
# protocol, while the performance surface needs [3, 20].  3 repeats would make
# stability_reliability structurally unreachable via runtime probe.  5 is the
# minimum that satisfies BOTH surfaces; read-only GETs are cheap and safe.
_RUNTIME_PROBE_SAMPLE_COUNT = 5

# ── P2b instrumentation: capture response schema (field names) for event-surface
# detection.  Field names only — never response values — so this is safe,
# non-PII structure observation.  A read-only GET that returns a JSON listing
# whose shape resembles an event/audit log (id + type + time fields) is a
# surface the event-delivery observer can target; learning its schema at runtime
# lets 档位 D derive an event contract without the customer hard-declaring the
# endpoint.  Detection is purely structural (field-name shapes), so no business
# paths or terms are hardcoded.  This is the observation half of making
# event_delivery_consistency reachable on a system that exposes (but does not
# declare) an event surface.
_OBSERVED_FIELDS_LIMIT = 200
_OBSERVED_EVENT_TYPE_LIMIT = 20


def _extract_observed_fields(body: Any) -> list[str]:
    """Top-level JSON field names of a discovered GET response (schema only)."""
    if isinstance(body, dict):
        return [str(k) for k in body.keys()][:_OBSERVED_FIELDS_LIMIT]
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return [str(k) for k in body[0].keys()][:_OBSERVED_FIELDS_LIMIT]
    return []


def _is_listing_body(body: Any) -> bool:
    return isinstance(body, list) and len(body) > 0


def _extract_event_type_values(body: Any, field_names: list[str], limit: int = _OBSERVED_EVENT_TYPE_LIMIT) -> list[str]:
    """Bounded distinct values of a type-like field in a listing response.

    Observation of the system's own event taxonomy (short categorical codes),
    not payload data.  Methodology default (limit) bounds the capture.
    """
    if not isinstance(body, (dict, list)):
        return []
    type_fields = [
        f for f in field_names
        if f in ("type", "kind", "event_type") or f.endswith("_type") or f.endswith("_event")
    ]
    if not type_fields:
        return []
    values: list[str] = []
    items = body if isinstance(body, list) else [body]
    for item in items:
        if not isinstance(item, dict):
            continue
        for f in type_fields:
            v = item.get(f)
            if v is None:
                continue
            s = str(v)
            if s and s not in values:
                values.append(s)
            if len(values) >= limit:
                return values
    return values


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_ref(operation: dict[str, Any]) -> dict[str, str]:
    method = _text(operation.get("method")).upper()
    path = _text(operation.get("path"))
    return {
        "source_id": _text(operation.get("source_id")) or "api_spec",
        "locator": f"{method} {path}",
        "kind": "source_route_vocabulary",
    }


def _source_id_hint(operations: list[dict[str, Any]]) -> str:
    """Pick the most common declared source_id as the general-ref provenance."""

    counts: dict[str, int] = defaultdict(int)
    for operation in operations:
        source_id = _text(operation.get("source_id")) or "api_spec"
        counts[source_id] += 1
    if not counts:
        return "api_spec"
    return max(sorted(counts), key=lambda key: counts[key])


def _general_source_ref(prefix_path: str, source_id: str) -> dict[str, str]:
    """Provenance anchor for candidates derived from the general vocabulary.

    These candidates are not tied to one documented operation; they are bounded
    by the declared transport namespace (the common route prefix) plus the
    deployment-owned general resource vocabulary. The locator records that
    derivation so the probe stays source-bound rather than an unbounded fuzz.
    """

    return {
        "source_id": source_id or "api_spec",
        "locator": f"general-vocabulary {prefix_path or '/'}",
        "kind": "source_route_vocabulary",
    }


def _segments(path: str) -> list[str]:
    clean = path.split("?", 1)[0].strip()
    return [segment for segment in clean.split("/") if segment]


def load_runtime_interface_discovery_actions() -> list[str]:
    """Load the deployment-owned action vocabulary; missing policy fails fast."""

    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"runtime_interface_semantic_policy_unreadable:{type(exc).__name__}"
        ) from exc
    raw = payload.get("runtime_interface_discovery_actions")
    if not isinstance(raw, list):
        raise ValueError("runtime_interface_discovery_actions_missing")
    actions: list[str] = []
    for value in raw:
        action = _text(value).strip("/").lower()
        if not action or not _SAFE_SEGMENT_RE.fullmatch(action):
            raise ValueError("runtime_interface_action_marker_invalid")
        if action not in actions:
            actions.append(action)
    if not actions:
        raise ValueError("runtime_interface_discovery_actions_empty")
    return actions


def load_runtime_interface_discovery_budget() -> int:
    """Load the deployment-owned probe budget with strict type/range checks."""

    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"runtime_interface_semantic_policy_unreadable:{type(exc).__name__}"
        ) from exc
    value = payload.get("runtime_interface_discovery_max_candidates")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5000:
        raise ValueError("runtime_interface_discovery_budget_invalid")
    return value


def _load_lexicon_segment_list(key: str) -> list[str]:
    """Load an optional list of safe path segments from the semantic lexicon.

    Returns an empty list when the key is absent so deployments that have not
    extended the lexicon keep the prior (narrower) discovery behaviour.
    """

    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    segments: list[str] = []
    for value in raw:
        segment = _text(value).strip("/").lower()
        if not segment or not _SAFE_SEGMENT_RE.fullmatch(segment):
            continue
        if segment not in segments:
            segments.append(segment)
    return segments


def load_runtime_interface_discovery_subresources() -> list[str]:
    """Intermediate path segments used to build nested discovery candidates."""

    return _load_lexicon_segment_list("runtime_interface_discovery_subresources")


def load_runtime_interface_discovery_resources() -> list[str]:
    """Top-level resource vocabulary for reaching undocumented namespaces."""

    return _load_lexicon_segment_list("runtime_interface_discovery_resources")


def load_runtime_interface_confirmation_tokens(
    root: Path,
    project: str,
    *,
    base_url: str = "",
) -> list[str]:
    """Load unique active bearer tokens from the declared test-actor catalog.

    Tokens are returned only for transport use and must never be copied into a
    receipt.  A malformed catalog fails fast because silently treating broken
    credentials as an empty actor set would make interface absence ambiguous.

    Active rows resolve through ``load_actor_tokens`` so password-declared
    accounts refresh against the approved target instead of orphan JWT snapshots.
    Disabled/locked accounts remain excluded from confirmation probes.
    """

    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    rows: list[Any] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"runtime_interface_actor_catalog_invalid:{type(exc).__name__}"
            ) from exc
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            declared = payload.get("accounts") or payload.get("actors") or payload.get("users")
            if declared is None:
                rows = [
                    {**(value if isinstance(value, dict) else {}), "_source_key": key}
                    for key, value in payload.items()
                    if key not in {"schema", "schema_version", "meta"}
                ]
            elif isinstance(declared, list):
                rows = declared
            else:
                raise ValueError("runtime_interface_actor_catalog_rows_invalid")
        else:
            raise ValueError("runtime_interface_actor_catalog_root_invalid")

        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("runtime_interface_actor_catalog_row_invalid")

    from .experiment_runtime_support import load_actor_tokens

    token_map = load_actor_tokens(root, project, base_url=_text(base_url))
    tokens: list[str] = []
    if rows:
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = _text(
                row.get("status")
                or row.get("account_status")
                or row.get("authenticated_status")
                or row.get("state")
                or "active"
            ).upper()
            if status in {"DISABLED", "LOCKED", "SUSPENDED", "INACTIVE"}:
                continue
            identities = [
                row.get("email"),
                row.get("username"),
                row.get("account_ref"),
                row.get("profile"),
                row.get("name"),
                row.get("id"),
                row.get("_source_key"),
                row.get("authenticated_role"),
                row.get("role"),
            ]
            email = _text(row.get("email"))
            if email.count("@") == 1:
                identities.append(email.split("@", 1)[0])
            resolved = ""
            for identity in identities:
                key = _text(identity)
                if not key:
                    continue
                resolved = _text(token_map.get(key) or token_map.get(f"secret_ref:test_accounts:{key}"))
                if resolved:
                    break
            if resolved and resolved not in tokens:
                tokens.append(resolved)
        return tokens

    for token in token_map.values():
        token = _text(token)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _common_prefix(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    prefix: list[str] = []
    for values in zip(*rows):
        if len(set(values)) != 1 or _PLACEHOLDER_RE.search(values[0]):
            break
        prefix.append(values[0])
    # Preserve only a transport namespace.  A full resource path is not a
    # namespace from which sibling interfaces may be derived.
    if not prefix or any(len(row) <= 1 for row in rows):
        return []
    return prefix[:1]


def _candidate(
    path: str,
    *,
    derivation: str,
    source_refs: Iterable[dict[str, str]],
) -> dict[str, Any]:
    refs = sorted(
        {json.dumps(ref, sort_keys=True): ref for ref in source_refs}.values(),
        key=lambda row: (row["source_id"], row["locator"]),
    )
    candidate_id = "surface_" + _fingerprint(
        {"method": "GET", "path": path, "derivation": derivation, "source_refs": refs}
    )[:20]
    return {
        "candidate_id": candidate_id,
        "method": "GET",
        "path": path,
        "derivation": derivation,
        "source_refs": refs,
    }


def plan_runtime_interface_candidates(
    documented_operations: list[dict[str, Any]],
    *,
    action_markers: list[str] | None,
    max_candidates: int,
) -> dict[str, Any]:
    """Build deterministic, bounded GET candidates from source route tokens."""

    if isinstance(max_candidates, bool) or int(max_candidates) <= 0:
        raise ValueError("runtime_interface_candidate_budget_invalid")
    configured_actions = (
        load_runtime_interface_discovery_actions()
        if action_markers is None
        else action_markers
    )
    actions = []
    for value in configured_actions:
        action = _text(value).strip("/").lower()
        if not action or not _SAFE_SEGMENT_RE.fullmatch(action):
            raise ValueError("runtime_interface_action_marker_invalid")
        if action not in actions:
            actions.append(action)
    if not actions:
        raise ValueError("runtime_interface_action_markers_missing")

    operations = [
        dict(row)
        for row in documented_operations
        if isinstance(row, dict)
        and _text(row.get("path")).startswith("/")
        and _text(row.get("method"))
    ]
    if not operations:
        raise ValueError("runtime_interface_documented_operations_missing")
    segmented = [_segments(_text(row["path"])) for row in operations]
    prefix = _common_prefix(segmented)
    prefix_len = len(prefix)
    prefix_path = "/" + "/".join(prefix) if prefix else ""
    documented_paths = {_text(row["path"]).split("?", 1)[0] for row in operations}

    refs_by_token: dict[str, list[dict[str, str]]] = defaultdict(list)
    child_tokens: dict[str, set[str]] = defaultdict(set)
    admin_shape_observed = False
    for operation, parts in zip(operations, segmented):
        tail = parts[prefix_len:]
        for index, token in enumerate(tail):
            normalized = token.lower()
            if index > 0:
                if normalized == "admin":
                    admin_shape_observed = True
                continue
            if (
                normalized == "admin"
                or normalized in actions
                or _PLACEHOLDER_RE.search(token)
                or not _SAFE_SEGMENT_RE.fullmatch(token)
            ):
                if normalized == "admin":
                    admin_shape_observed = True
                continue
            refs_by_token[normalized].append(_source_ref(operation))
            if index + 1 < len(tail):
                child = tail[index + 1].lower()
                if (
                    child != "admin"
                    and not _PLACEHOLDER_RE.search(child)
                    and _SAFE_SEGMENT_RE.fullmatch(child)
                ):
                    child_tokens[normalized].add(child)

    resources = sorted(refs_by_token)
    namespaces = sorted(
        token for token, children in child_tokens.items() if len(children) >= 2
    )

    # Deployment-owned general vocabulary (optional). These extend discovery to
    # source namespaces that the supplied documents did not enumerate (for
    # example, an undocumented service mounted under the same transport prefix).
    # A gateway route is not necessarily segment-aware: ``/api/cart`` can match
    # ``/api/carts/...`` and forward the leftover ``s/...`` to the cart service.
    # Treating that malformed response as an interface would turn a policy word
    # into a discovered operation and then into customer-facing false positives.
    # Keep general resources available, but reject every candidate whose base
    # path is a non-boundary extension of a source-declared route prefix.
    source_route_prefixes = {
        "/" + "/".join(parts[: prefix_len + 1])
        for parts in segmented
        if len(parts) > prefix_len
        and not _PLACEHOLDER_RE.search(parts[prefix_len])
    }
    shadowed_general_resources: list[str] = []
    general_resources: list[str] = []
    for resource in load_runtime_interface_discovery_resources():
        if resource in refs_by_token:
            continue
        candidate_base = f"{prefix_path}/{resource}" if prefix_path else f"/{resource}"
        candidate_base = candidate_base.rstrip("/").lower()
        if any(
            candidate_base.startswith(route_prefix.lower())
            and len(candidate_base) > len(route_prefix)
            and candidate_base[len(route_prefix)] != "/"
            for route_prefix in source_route_prefixes
        ):
            shadowed_general_resources.append(resource)
            continue
        general_resources.append(resource)
    subresources = load_runtime_interface_discovery_subresources()
    general_ref = _general_source_ref(prefix_path, _source_id_hint(operations))

    planned: list[dict[str, Any]] = []
    seen: set[str] = set(documented_paths)
    # Hard generation ceiling so the nested lattices cannot exhaust memory before
    # the budget truncation below applies.
    generation_cap = int(max_candidates) * 8

    def add(
        path: str,
        derivation: str,
        tokens: list[str],
        *,
        extra_refs: Iterable[dict[str, str]] = (),
    ) -> bool:
        if len(planned) >= generation_cap or path in seen:
            return False
        seen.add(path)
        refs = [ref for token in tokens for ref in refs_by_token.get(token, [])]
        refs.extend(extra_refs)
        if not refs:
            raise ValueError("runtime_interface_candidate_source_refs_missing")
        planned.append(_candidate(path, derivation=derivation, source_refs=refs))
        return True

    # Reserve part of the existing discovery budget for undocumented nested
    # collection roots.  Without this reservation, a large action policy can
    # exhaust the entire round in the documented resource/action lattice before
    # a resolver-critical collection is ever probed.
    collection_reservation = max(1, int(max_candidates) // 4)
    route_lattice_budget = max(0, int(max_candidates) - collection_reservation)

    # Tier 1: documented resource x action (source-anchored on the operation
    # that declared the resource).
    for action in actions:
        if len(planned) >= route_lattice_budget:
            break
        for resource in resources:
            if len(planned) >= route_lattice_budget:
                break
            add(
                f"{prefix_path}/{resource}/{action}",
                "resource_action_lattice",
                [resource],
            )

    # Tier 2: observed namespace/resource/action lattice. A namespace and its
    # child resource are both source evidence, so this outranks the broad
    # deployment vocabulary below and cannot be starved by a large policy.
    for action in actions:
        if len(planned) >= route_lattice_budget:
            break
        for namespace in namespaces:
            if len(planned) >= route_lattice_budget:
                break
            for resource in resources:
                if len(planned) >= route_lattice_budget:
                    break
                if resource == namespace:
                    continue
                add(
                    f"{prefix_path}/{namespace}/{resource}/{action}",
                    "observed_namespace_resource_action_lattice",
                    [namespace, resource],
                )

    # Tier 3: nested collection roots.  A collection endpoint is a valid
    # read-only surface in its own right; requiring an action suffix here skips
    # routes such as ``/users/addresses`` and prevents later body bindings from
    # resolving an exact source-backed resource.  The parent and child tokens
    # both come from the deployment-owned policy asset (or the documented
    # route vocabulary), so this does not invent an enterprise path.
    nested_collection_pool = list(dict.fromkeys([*resources, *general_resources]))
    nested_collection_subresources = sorted(set(subresources))
    nested_collection_cap = min(
        int(max_candidates),
        len(planned) + collection_reservation,
    )
    if nested_collection_pool and nested_collection_subresources:
        for subresource in nested_collection_subresources:
            if len(planned) >= nested_collection_cap:
                break
            for resource in nested_collection_pool:
                if len(planned) >= nested_collection_cap:
                    break
                if subresource == resource:
                    continue
                add(
                    f"{prefix_path}/{resource}/{subresource}",
                    "nested_resource_collection_lattice",
                    [resource] if resource in refs_by_token else [],
                    extra_refs=() if resource in refs_by_token else [general_ref],
                )

    # Tier 4: general resource vocabulary x action, reaching undocumented
    # service namespaces mounted under the declared transport prefix.  Iterated
    # resource-major and capped to a budget share so every general resource is
    # probed with the most diagnostic actions first (breadth before depth),
    # instead of one resource exhausting the budget across all actions.
    tier2_cap = len(planned) + max(1, int(max_candidates) // 2)
    for resource in general_resources:
        if len(planned) >= tier2_cap or len(planned) >= generation_cap:
            break
        for action in actions:
            if len(planned) >= tier2_cap or len(planned) >= generation_cap:
                break
            add(
                f"{prefix_path}/{resource}/{action}",
                "general_resource_action_lattice",
                [],
                extra_refs=[general_ref],
            )

    # Tier 5: nested resource/subresource/action lattice (reaches deeper
    # undocumented paths such as a resource's child collections).  Distributed
    # evenly across (subresource, action) pairs so no single pair monopolises the
    # budget.  Placed before the admin shape because nested child paths are a
    # richer source of undocumented behaviour than admin variants.
    nested_pool = sorted(set(resources) | set(general_resources))
    if subresources and nested_pool:
        pairs = [
            (sub, action)
            for sub in subresources
            for action in actions
            if sub != action
        ]
        per_pair = max(1, int(max_candidates) // max(1, len(pairs)))
        for sub, action in pairs:
            if len(planned) >= generation_cap:
                break
            emitted = 0
            for resource in nested_pool:
                if emitted >= per_pair:
                    break
                if sub == resource:
                    continue
                if add(
                    f"{prefix_path}/{resource}/{sub}/{action}",
                    "nested_resource_subresource_action_lattice",
                    [resource] if resource in refs_by_token else [],
                    extra_refs=() if resource in refs_by_token else [general_ref],
                ):
                    emitted += 1

    # Tier 6: admin shape lattice (observed admin convention or general admin
    # vocabulary) across documented and general resources.
    if admin_shape_observed or "admin" in subresources or "admin" in general_resources:
        admin_pool = sorted(set(resources) | set(general_resources))
        for action in actions:
            for resource in admin_pool:
                add(
                    f"{prefix_path}/{resource}/admin/{action}",
                    "observed_admin_shape_action_lattice",
                    [resource] if resource in refs_by_token else [],
                    extra_refs=() if resource in refs_by_token else [general_ref],
                )

    selected = planned[: int(max_candidates)]
    receipt = {
        "schema_version": PLAN_SCHEMA,
        "documented_operation_count": len(operations),
        "source_resource_count": len(resources),
        "source_namespace_count": len(namespaces),
        "general_resource_count": len(general_resources),
        "general_resource_shadowed_count": len(shadowed_general_resources),
        "general_resource_shadowed": sorted(shadowed_general_resources),
        "subresource_count": len(subresources),
        "policy_action_count": len(actions),
        "candidate_budget": int(max_candidates),
        "candidate_count": len(selected),
        "unbounded_candidate_count": len(planned),
        "truncated": len(planned) > len(selected),
        "candidates": selected,
    }
    receipt["plan_fingerprint"] = _fingerprint(receipt)
    return receipt


def build_runtime_interface_observation_receipt(
    candidate: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Turn one governed GET result into a provenance-bound operation fact."""

    row = dict(candidate) if isinstance(candidate, dict) else {}
    if _text(row.get("method")).upper() != "GET":
        raise ValueError("runtime_interface_candidate_not_read_only")
    candidate_id = _text(row.get("candidate_id"))
    path = _text(row.get("path"))
    if not candidate_id or not path.startswith("/") or _PLACEHOLDER_RE.search(path):
        raise ValueError("runtime_interface_candidate_invalid")
    source_refs = [
        dict(ref) for ref in row.get("source_refs", []) if isinstance(ref, dict)
    ]
    if not source_refs:
        raise ValueError("runtime_interface_candidate_source_refs_missing")

    observed = dict(observation) if isinstance(observation, dict) else {}
    request_receipt_id = _text(observed.get("request_receipt_id"))
    primary_duration_ms = observed.get("primary_duration_ms")
    if primary_duration_ms is not None:
        try:
            primary_duration_ms = int(primary_duration_ms)
        except (TypeError, ValueError):
            primary_duration_ms = None
    raw_observed_fields = observed.get("observed_fields")
    observed_fields: list[str] | None = None
    if isinstance(raw_observed_fields, list):
        observed_fields = [
            str(x) for x in raw_observed_fields if isinstance(x, str)
        ][:_OBSERVED_FIELDS_LIMIT]
    raw_event_types = observed.get("observed_event_types")
    observed_event_types: list[str] | None = None
    if isinstance(raw_event_types, list):
        observed_event_types = [
            str(x) for x in raw_event_types if isinstance(x, str)
        ][:_OBSERVED_EVENT_TYPE_LIMIT]
    is_listing_response = bool(observed.get("is_listing_response"))
    raw_samples = observed.get("samples")
    probe_samples: list[dict[str, Any]] = []
    if isinstance(raw_samples, list):
        for sample in raw_samples:
            if not isinstance(sample, dict):
                continue
            sample_status = sample.get("status_code")
            sample_duration = sample.get("duration_ms")
            sample_attempts = sample.get("attempts")
            if sample_duration is not None:
                try:
                    sample_duration = int(sample_duration)
                except (TypeError, ValueError):
                    sample_duration = None
            if sample_attempts is not None:
                try:
                    sample_attempts = int(sample_attempts)
                except (TypeError, ValueError):
                    sample_attempts = 1
            probe_samples.append({
                "status_code": int(sample_status or -1),
                "duration_ms": sample_duration,
                "attempts": sample_attempts or 1,
            })
    if not request_receipt_id:
        raise ValueError("runtime_interface_request_receipt_missing")
    response_fingerprint = _text(observed.get("response_fingerprint")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", response_fingerprint):
        raise ValueError("runtime_interface_response_fingerprint_invalid")
    try:
        status_code = int(observed.get("status_code"))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime_interface_status_code_invalid") from exc
    if status_code < 0 or status_code > 599:
        raise ValueError("runtime_interface_status_code_invalid")

    raw_confirmations = observed.get("confirmation_observations")
    confirmations = (
        [dict(value) for value in raw_confirmations if isinstance(value, dict)]
        if isinstance(raw_confirmations, list)
        else []
    )
    if observed.get("confirmation_status_code") is not None:
        confirmations.append({
            "status_code": observed.get("confirmation_status_code"),
            "request_receipt_id": observed.get(
                "confirmation_request_receipt_id"
            ),
            "response_fingerprint": observed.get(
                "confirmation_response_fingerprint"
            ),
        })
    normalized_confirmations: list[dict[str, Any]] = []
    for confirmation in confirmations:
        confirmation_receipt_id = _text(
            confirmation.get("request_receipt_id")
        )
        confirmation_fingerprint = _text(
            confirmation.get("response_fingerprint")
        ).lower()
        if not confirmation_receipt_id:
            raise ValueError(
                "runtime_interface_confirmation_request_receipt_missing"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", confirmation_fingerprint):
            raise ValueError(
                "runtime_interface_confirmation_response_fingerprint_invalid"
            )
        try:
            confirmation_status = int(confirmation.get("status_code"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "runtime_interface_confirmation_status_code_invalid"
            ) from exc
        if confirmation_status < 0 or confirmation_status > 599:
            raise ValueError(
                "runtime_interface_confirmation_status_code_invalid"
            )
        normalized_confirmations.append({
            "status_code": confirmation_status,
            "request_receipt_id": confirmation_receipt_id,
            "response_fingerprint": confirmation_fingerprint,
        })

    if status_code == 404:
        status = "NOT_FOUND"
    elif status_code == 0 or status_code >= 500:
        status = "INDETERMINATE"
    elif status_code in {401, 403}:
        confirmation_statuses = {
            row["status_code"] for row in normalized_confirmations
        }
        if any(
            100 <= value < 500 and value not in {401, 403, 404}
            for value in confirmation_statuses
        ):
            status = "DISCOVERED"
        elif confirmation_statuses and confirmation_statuses == {404}:
            status = "NOT_FOUND"
        else:
            status = "INDETERMINATE"
    else:
        status = "DISCOVERED"
    receipt: dict[str, Any] = {
        "schema_version": OBSERVATION_SCHEMA,
        "candidate_id": candidate_id,
        "method": "GET",
        "path": path,
        "status": status,
        "status_code": status_code,
        "request_receipt_id": request_receipt_id,
        "response_fingerprint": response_fingerprint,
        "primary_duration_ms": primary_duration_ms,
        "samples": probe_samples,
        "observed_fields": observed_fields,
        "observed_event_types": observed_event_types,
        "is_listing_response": is_listing_response,
        "source_refs": source_refs,
    }
    if normalized_confirmations:
        receipt["confirmation_observations"] = normalized_confirmations
    if status == "DISCOVERED":
        receipt["operation"] = {
            "method": "GET",
            "path": path,
            "operation_id": f"runtime-observed:get:{path}",
            "source_id": request_receipt_id,
            "summary": "Runtime-observed interface",
            "description": "Interface existence proven by a governed read-only request.",
            "parameters": [],
            "request_schema": {},
            "response_schema": {},
            "derivation": "runtime-observed",
            "runtime_observation_receipt_id": request_receipt_id,
            "source_refs": source_refs,
        }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    return receipt


def merge_runtime_discovered_operations(
    documented_operations: list[dict[str, Any]],
    observation_receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only fingerprint-valid DISCOVERED observations by method/path."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in documented_operations:
        if not isinstance(operation, dict):
            continue
        key = (_text(operation.get("method")).upper(), _text(operation.get("path")))
        if key[0] and key[1]:
            merged[key] = dict(operation)
    for raw in observation_receipts:
        receipt = dict(raw) if isinstance(raw, dict) else {}
        fingerprint = _text(receipt.pop("receipt_fingerprint"))
        if not fingerprint or fingerprint != _fingerprint(receipt):
            raise ValueError("runtime_interface_observation_fingerprint_invalid")
        if receipt.get("schema_version") != OBSERVATION_SCHEMA:
            raise ValueError("runtime_interface_observation_schema_invalid")
        if _text(receipt.get("status")) != "DISCOVERED":
            continue
        operation = receipt.get("operation")
        if not isinstance(operation, dict):
            raise ValueError("runtime_interface_discovered_operation_missing")
        key = (_text(operation.get("method")).upper(), _text(operation.get("path")))
        if key[0] != "GET" or not key[1]:
            raise ValueError("runtime_interface_discovered_operation_invalid")
        merged.setdefault(key, dict(operation))
    return list(merged.values())


def execute_runtime_interface_discovery(
    plan: dict[str, Any],
    *,
    base_url: str,
    mainline_run: dict[str, Any],
    confirmation_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Execute planned GET candidates with correlation and ledger-ready receipts."""

    discovery_plan = dict(plan) if isinstance(plan, dict) else {}
    if discovery_plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError("runtime_interface_discovery_plan_schema_invalid")
    claimed = _text(discovery_plan.get("plan_fingerprint"))
    unsigned_plan = {
        key: value
        for key, value in discovery_plan.items()
        if key != "plan_fingerprint"
    }
    if not claimed or claimed != _fingerprint(unsigned_plan):
        raise ValueError("runtime_interface_discovery_plan_fingerprint_invalid")
    target = _text(base_url).rstrip("/")
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("runtime_interface_base_url_invalid")
    authority = dict(mainline_run) if isinstance(mainline_run, dict) else {}
    identities = {
        key: _text(authority.get(key))
        for key in ("run_id", "campaign_id", "target_id")
    }
    if not all(identities.values()):
        raise ValueError("runtime_interface_mainline_identity_missing")

    selected_rows: list[dict[str, Any]] = []
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    observation_receipts: list[dict[str, Any]] = []
    discovered_operations: list[dict[str, Any]] = []
    harness_failure_count = 0
    declared_confirmation_tokens = list(dict.fromkeys(
        _text(value)
        for value in (confirmation_tokens or [])
        if _text(value)
    ))

    candidates = discovery_plan.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("runtime_interface_candidates_not_list")
    for raw_candidate in candidates:
        candidate = dict(raw_candidate) if isinstance(raw_candidate, dict) else {}
        if _text(candidate.get("method")).upper() != "GET":
            raise ValueError("runtime_interface_candidate_not_read_only")
        candidate_id = _text(candidate.get("candidate_id"))
        path = _text(candidate.get("path"))
        if not candidate_id or not path.startswith("/"):
            raise ValueError("runtime_interface_candidate_invalid")
        obligation_id = "surfobl_" + _fingerprint(candidate_id)[:20]
        experiment_id = "surfexp_" + _fingerprint(obligation_id)[:20]
        execution_id = "surfexec_" + _fingerprint(
            {"run_id": identities["run_id"], "obligation_id": obligation_id}
        )[:20]
        selected_rows.append({
            "obligation_id": obligation_id,
            "candidate_id": candidate_id,
            "risk_family": "interface_discovery",
            "source_refs": list(candidate.get("source_refs") or []),
            "required_operations": [],
            "required_actors": [],
            "relation_refs": [],
            "operation_refs": [],
            "actor_refs": [],
            "behavior_ir_refs": [],
            "adapter": "http_api_discovery",
            "planning_round": 0,
            "experiment_id": experiment_id,
            "property": {
                "kind": "runtime_interface_presence",
                "method": "GET",
                "path": path,
            },
        })
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "compile_receipt_id": "surfcompile_" + _fingerprint(experiment_id)[:20],
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "cost_coverage_status": "UNKNOWN",
        }
        trace = {
            **identities,
            "obligation_id": obligation_id,
            "execution_id": execution_id,
        }
        confirmation_responses: list[dict[str, Any]] = []
        with evaluator_request_trace(trace):
            response = _http_request("GET", target + path)
            if int(response.get("status") or 0) in {401, 403}:
                for confirmation_token in declared_confirmation_tokens:
                    confirmation_response = _http_request(
                        "GET",
                        target + path,
                        token=confirmation_token,
                    )
                    confirmation_responses.append(confirmation_response)
                    confirmation_status = int(
                        confirmation_response.get("status") or 0
                    )
                    if (
                        100 <= confirmation_status < 500
                        and confirmation_status not in {401, 403, 404}
                    ):
                        break
        status_code = int(response.get("status") or 0)
        # ── P2: bounded repeat-sampling for latency / stability observation ──
        # Sequential read-only GETs (no state mutation, low risk).  Retries are
        # disabled on the extra samples so each is a single clean attempt; the
        # latency observer rejects multi-attempt durations.  Methodology default
        # (_RUNTIME_PROBE_SAMPLE_COUNT).  This is what makes open-class bug
        # families reachable on a system with no source-declared contract.
        _primary_dur = response.get("duration_ms")
        probe_samples: list[dict[str, Any]] = [{
            "status_code": status_code,
            "duration_ms": int(_primary_dur) if _primary_dur is not None else None,
            "attempts": int(response.get("_attempts") or 1),
        }]
        # Only repeat-sample a response we could actually read.  Latency and
        # read-stability are meaningful only for successful reads, and the
        # latency observer rejects non-2xx samples; auth-gated / missing
        # endpoints yield no extra samples (the producer treats them as
        # auth-gated, never as reliability defects).  Guarding also keeps the
        # governed probe's request budget on the genuinely readable surface and
        # avoids issuing speculative repeats against endpoints that already
        # refused the primary read.
        if 200 <= status_code < 300:
            for _ in range(max(0, _RUNTIME_PROBE_SAMPLE_COUNT - 1)):
                _sample = _http_request("GET", target + path, max_retries=0)
                _sample_status = int(_sample.get("status") or 0)
                _sample_dur = _sample.get("duration_ms")
                probe_samples.append({
                    "status_code": _sample_status,
                    "duration_ms": int(_sample_dur) if _sample_dur is not None else None,
                    "attempts": int(_sample.get("_attempts") or 1),
                })
        request_receipt_id = "surfreq_" + _fingerprint({
            "run_id": identities["run_id"],
            "obligation_id": obligation_id,
            "execution_id": execution_id,
            "method": "GET",
            "path": path,
            "status_code": status_code,
        })[:20]
        response_fingerprint = _fingerprint({
            "status_code": status_code,
            "body": response.get("body"),
            "headers": response.get("headers"),
        })
        # ── P2b: capture response schema for event-surface detection ──
        # Field names + bounded event-type values only (never payload data).
        # Lets 档位 D derive an event contract when the system exposes (but
        # does not declare) an event/audit listing surface.
        _observed_body = response.get("body")
        _observed_fields = _extract_observed_fields(_observed_body)
        _observed_event_types = (
            _extract_event_type_values(_observed_body, _observed_fields)
            if _observed_fields else []
        )
        _is_listing = _is_listing_body(_observed_body)
        confirmation_observations: list[dict[str, Any]] = []
        for index, confirmation_response in enumerate(
            confirmation_responses,
            start=1,
        ):
            confirmation_status = int(
                confirmation_response.get("status") or 0
            )
            confirmation_observations.append({
                "status_code": confirmation_status,
                "request_receipt_id": "surfreq_" + _fingerprint({
                    "request_receipt_id": request_receipt_id,
                    "confirmation_index": index,
                    "status_code": confirmation_status,
                })[:20],
                "response_fingerprint": _fingerprint({
                    "status_code": confirmation_status,
                    "body": confirmation_response.get("body"),
                    "headers": confirmation_response.get("headers"),
                }),
            })
        observation_receipt = build_runtime_interface_observation_receipt(
            candidate,
            {
                "status_code": status_code,
                "request_receipt_id": request_receipt_id,
                "response_fingerprint": response_fingerprint,
                "primary_duration_ms": response.get("duration_ms"),
                "samples": probe_samples,
                "observed_fields": _observed_fields,
                "observed_event_types": _observed_event_types,
                "is_listing_response": _is_listing,
                "confirmation_observations": confirmation_observations,
            },
        )
        observation_receipts.append(observation_receipt)
        if isinstance(observation_receipt.get("operation"), dict):
            discovered_operations.append(dict(observation_receipt["operation"]))
        steps = [{
            "phase": "surface_discovery",
            "method": "GET",
            "path": path,
            "status_code": status_code,
        }]
        steps.extend({
            "phase": "surface_discovery_confirmation",
            "method": "GET",
            "path": path,
            "status_code": int(confirmation.get("status") or 0),
        } for confirmation in confirmation_responses)
        operational = build_execution_operational_receipt(
            receipt_id="surfop_" + _fingerprint(request_receipt_id)[:20],
            execution_status=("EXECUTED" if status_code else "HARNESS_FAILED"),
            steps=steps,
            cleanup_failures=0,
        )
        if not status_code:
            harness_failure_count += 1
            execution_results[obligation_id] = {
                "status": "HARNESS_FAILED",
                "reason_code": "SURFACE_DISCOVERY_TRANSPORT_FAILED",
                "reason_detail": _text(response.get("error")),
                "execution_receipt_id": "surfexecution_" + _fingerprint(execution_id)[:20],
                "execution_id": execution_id,
                "experiment_id": experiment_id,
                "candidate_id": candidate_id,
                "cost_coverage_status": "UNKNOWN",
                "operational_receipt": operational,
                "runtime_interface_observation": observation_receipt,
            }
            continue
        observation_receipt_id = "surfobs_" + _fingerprint(observation_receipt)[:20]
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "execution_receipt_id": "surfexecution_" + _fingerprint(execution_id)[:20],
            "execution_id": execution_id,
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "cost_coverage_status": "UNKNOWN",
            "observation_receipt_ids": [observation_receipt_id],
            "operational_receipt": operational,
            "runtime_interface_observation": observation_receipt,
        }
        gate_results[obligation_id] = {
            "status": "REJECTED",
            "reason_code": "SURFACE_DISCOVERY_OBSERVATION_ONLY",
            "reason_detail": _text(observation_receipt.get("status")),
            "gate_receipt_id": "surfgate_" + _fingerprint(observation_receipt_id)[:20],
            "cost_coverage_status": "UNKNOWN",
        }

    return {
        "schema_version": "qualibug.runtime-interface-discovery-execution.v1",
        "selected_count": len(selected_rows),
        "executed_count": len(execution_results) - harness_failure_count,
        "blocked_count": 0,
        "harness_failure_count": harness_failure_count,
        "cleanup_failures": 0,
        "selected_rows": selected_rows,
        "compile_results": compile_results,
        "execution_results": execution_results,
        "gate_results": gate_results,
        "observation_receipts": observation_receipts,
        "discovered_operations": discovered_operations,
        "findings": [],
    }
