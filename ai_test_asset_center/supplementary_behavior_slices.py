"""Supplementary behavior-slice generator — expands coverage beyond state-machine.

The BusinessStateGraphBuilder only creates transition / invariant / dependency slices
from the PRD and API specification, which naturally covers idempotency, state-forbidden
transitions, and business-rule invariants.  This module adds slices for the other
dimensions that a state machine cannot express:

  1. Actor-aware permission probes     (who can write what)
  2. Tenant-isolation probes            (cross-user data access)
  3. Data-boundary & injection probes   (negative-payload fuzzing)

All slices are source-grounded — the endpoint catalog comes from the same
_api_facts parser that feeds the state graph, and the actor/role catalog comes
from the project's test_accounts JSON.

Design contract
  - No per-project table / endpoint / role hardcoding.
  - Config-driven: actor catalog from test_accounts.json, endpoint catalog from
    the API specification parser.
  - Writes always use the actor's own token.  A readonly actor that succeeds on a
    write is an evidence-worthy permission defect, not a false positive.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .artifact_redactor import redact_artifact
from .business_state_graph import _api_facts, behavior_slice_id
from .enterprise_knowledge_center import _lexicon_dict

from .real_id_resolver import path_has_placeholders

# ── Pure-data structures ────────────────────────────────────────────────

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_OWNERSHIP_QUERY_PARAMS = ("userId", "user_id", "ownerId", "owner_id", "accountId", "account_id")

# Endpoints whose entity or path suggests authentication — money / concurrency
# probes are semantically meaningless here (login is not a financial or
# resource-contention operation) and the resulting 4xx/5xx noise pollutes
# the signal.  Detection is purely path/entity based — no per-project list.
_AUTH_ENTITY_TOKENS = frozenset({"auth", "login", "signin", "signup", "register", "session", "token", "oauth", "sso"})
"""Exclude auth endpoints from money and concurrency probes to avoid noise from login/register operations appearing as 'financial' or 'concurrency' issues."""

def _is_auth_endpoint(endpoint: dict[str, str]) -> bool:
    path = str(endpoint.get("path") or "").strip("/").lower()
    entity = str(endpoint.get("entity") or "").strip().lower()
    tokens = path.split("/") + [entity]
    return bool(_AUTH_ENTITY_TOKENS & set(tokens))


def _is_identity_mutation_endpoint(endpoint: dict[str, Any]) -> bool:
    """True when a write can invalidate the probing actor's own session.

    Admin status mutations and account-register creates remain valuable
    authorization probes; they must not be blanket-skipped just because they
    live under an auth path. Session/token invalidation and password reset of
    the calling principal remain excluded without a documented compensation.
    """
    method = str(endpoint.get("method") or "").upper()
    if method not in _WRITE_METHODS or not _is_auth_endpoint(endpoint):
        return False
    action = str(endpoint.get("action") or "").strip().lower()
    path = str(endpoint.get("path") or "").strip().lower()
    if action in {"login", "signin"}:
        return False
    # Privilege / lifecycle probes against other identities.
    if action in {"register", "signup", "status"} or path.rstrip("/").endswith("/status"):
        return False
    session_tokens = (
        "password",
        "reset",
        "refresh",
        "logout",
        "revoke",
        "invalidate",
        "session",
        "token",
    )
    hay = f"{action} {path}"
    return any(token in hay for token in session_tokens)


_AUTH_ACCEPTANCE_KEY_TOKENS = (
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "jwt",
    "token",
    "session",
    "session_id",
    "sessionid",
    "bearer",
)
_AUTH_SUCCESS_BOOL_KEYS = {
    "authenticated",
    "authorized",
    "logged_in",
    "login_success",
    "success",
    "ok",
}
_AUTH_PRINCIPAL_KEYS = {"user", "account", "principal", "profile", "identity"}
_AUTH_ACCEPTANCE_HEADER_TOKENS = {"authorization", "set-cookie", "x-auth-token", "x-session-id"}
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")


def _redact_probe_artifact(value: Any) -> Any:
    redacted, _receipt = redact_artifact(value)
    return redacted


def _auth_value_present(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return value not in (None, "", [], {})


def _auth_acceptance_observed(body: Any, headers: dict[str, Any] | None = None, *, _depth: int = 0) -> bool:
    """Return True only when a 2xx login response contains a real accept signal.

    A bare HTTP 200 can still be an application-level rejection envelope.  The
    account-status probe should become customer-deliverable only when the
    response issues credentials, a session, a principal, or an explicit success
    marker.  The signals are protocol/auth-shape based, not project-specific.
    """

    if _depth == 0:
        for key, value in (headers or {}).items():
            key_l = str(key or "").strip().lower()
            if key_l in _AUTH_ACCEPTANCE_HEADER_TOKENS and str(value or "").strip():
                return True
    if _depth > 8:
        return False
    if isinstance(body, dict):
        for key, value in body.items():
            key_l = str(key or "").strip().lower().replace("-", "_")
            if any(token in key_l for token in _AUTH_ACCEPTANCE_KEY_TOKENS) and _auth_value_present(value):
                return True
            if key_l in _AUTH_SUCCESS_BOOL_KEYS and value is True:
                return True
            if key_l in _AUTH_PRINCIPAL_KEYS and isinstance(value, dict) and bool(value):
                return True
            if _auth_acceptance_observed(value, None, _depth=_depth + 1):
                return True
        return False
    if isinstance(body, list):
        return any(_auth_acceptance_observed(item, None, _depth=_depth + 1) for item in body[:20])
    if isinstance(body, str):
        return bool(_JWT_RE.search(body))
    return False


def _active_actors(actors: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        actor for actor in actors
        if isinstance(actor, dict) and _account_status_token(actor) not in {"DISABLED", "LOCKED"}
    ]


def _singular_token(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 2:
        return token[:-1]
    return token


def _endpoint_resource_tokens(endpoint: dict[str, Any]) -> set[str]:
    path = str(endpoint.get("path") or "").lower()
    values = [endpoint.get("entity"), *path.strip("/").split("/")]
    return {
        _singular_token(value)
        for value in values
        if value and not str(value).startswith(("{", ":")) and str(value).lower() not in {"api", "v1", "v2", "v3", "admin"}
    }


def _resource_matches_endpoint(row: dict[str, Any], endpoint: dict[str, Any]) -> bool:
    resources = [row.get("resource"), *(row.get("resource_aliases") or [])]
    endpoint_path = str(endpoint.get("path") or "").strip().lower()
    for value in resources:
        resource_path = str(value or "").strip().lower()
        if resource_path.startswith("/") and (
            resource_path == endpoint_path
            or resource_path.rstrip("/") in endpoint_path.rstrip("/")
        ):
            return True
    normalized = {_singular_token(value) for value in resources if str(value or "").strip()}
    if "*" in normalized:
        return True
    entity = _singular_token(endpoint.get("entity"))
    if entity:
        return entity in normalized
    return bool(normalized & _endpoint_resource_tokens(endpoint))


def _endpoint_action_tokens(endpoint: dict[str, Any]) -> set[str]:
    method = str(endpoint.get("method") or "").upper()
    method_actions = {
        "GET": {"GET", "read", "view", "list", "query"},
        "HEAD": {"HEAD", "read"},
        "OPTIONS": {"OPTIONS", "read"},
        "POST": {"POST", "create", "submit", "request"},
        "PUT": {"PUT", "update", "modify"},
        "PATCH": {"PATCH", "update", "modify", "adjust"},
        "DELETE": {"DELETE", "delete", "remove"},
    }
    tokens = set(method_actions.get(method, {method} if method else set()))
    action = str(endpoint.get("action") or "").strip().lower()
    if action and action != "admin":
        tokens.add(action)
    evidence = " ".join((action, str(endpoint.get("summary") or ""))).lower()
    for source_token, aliases in _lexicon_dict("verb_action_lexicon").items():
        candidates = [source_token, *aliases]
        if any(str(token).strip().lower() in evidence for token in candidates if str(token).strip()):
            tokens.update(str(token).strip().lower() for token in aliases if str(token).strip())
    return tokens


def _declared_actions_allow(actions: set[str], endpoint: dict[str, Any]) -> bool:
    normalized = {str(action).strip().lower() for action in actions if str(action).strip()}
    if normalized & {"*", "manage"}:
        return True
    return bool(normalized & {token.lower() for token in _endpoint_action_tokens(endpoint)})


def _endpoint_declared_roles(endpoint: dict[str, Any], actors: list[dict[str, str]]) -> set[str]:
    evidence = str(endpoint.get("summary") or "").lower()
    if not evidence:
        return set()
    declared: set[str] = set()
    role_words = _lexicon_dict("role_words")
    for actor in actors:
        role = str(actor.get("role") or "").strip().lower()
        if not role:
            continue
        aliases = [role, *role_words.get(role, [])]
        if any(
            re.search(rf"(?<![a-z0-9_]){re.escape(alias.lower())}(?![a-z0-9_])", evidence)
            if alias.isascii() else alias.lower() in evidence
            for alias in aliases
            if alias
        ):
            declared.add(role)
    return declared


def _resource_has_declared_boundary(endpoint: dict[str, Any], permission_matrix: list[dict[str, Any]] | None) -> bool:
    return any(
        isinstance(row, dict)
        and str(row.get("resource") or "").strip() != "*"
        and _resource_matches_endpoint(row, endpoint)
        for row in permission_matrix or []
    )


def _select_actor_for_endpoint(
    endpoint: dict[str, Any],
    actors: list[dict[str, str]],
    permission_matrix: list[dict[str, Any]] | None,
    fallback: dict[str, str] | None,
) -> dict[str, str]:
    active = _active_actors(actors)
    declared_roles = _endpoint_declared_roles(endpoint, active)
    if declared_roles:
        selected = next((actor for actor in active if str(actor.get("role") or "").strip().lower() in declared_roles), None)
        if selected is not None:
            return selected
    for actor in active:
        role = str(actor.get("role") or "").strip().lower()
        actions = _permission_declared_actions(role, endpoint, permission_matrix=permission_matrix)
        if actions is not None and "*" not in actions and _declared_actions_allow(actions, endpoint):
            return actor
    if not _resource_has_declared_boundary(endpoint, permission_matrix):
        selected = fallback if fallback in active else None
        if selected is not None:
            return selected
    for actor in active:
        role = str(actor.get("role") or "").strip().lower()
        actions = _permission_declared_actions(role, endpoint, permission_matrix=permission_matrix)
        if actions is not None and _declared_actions_allow(actions, endpoint):
            return actor
    return {}


def load_settings_accounts(root: Path, project: str) -> tuple[list[dict[str, str]], str]:
    """Read role accounts from the Settings page store (PRIMARY source).

    The product's Settings UI persists per-service credentials to
    ``platform_workspace/<project>/multi_service_config.json`` as::

        {"services": [{"name", "base_url", "auth": {
            "type", "login_api",
            "<role>": {"username", "password"}, ...}}]}

    Every ``auth.<role>`` entry with a username becomes a test account. The
    per-service ``login_api`` is returned so callers prefer the operator-declared
    login route over auto-discovery. Returns (accounts, login_path).
    """
    config_path = root / "platform_workspace" / project / "multi_service_config.json"
    if not config_path.exists():
        return [], ""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return [], ""
    accounts: list[dict[str, str]] = []
    login_path = ""
    seen: set[str] = set()
    _reserved = {"type", "login_api", "bearer_token", "api_key"}
    for svc in data.get("services") or []:
        if not isinstance(svc, dict):
            continue
        auth = svc.get("auth") if isinstance(svc.get("auth"), dict) else {}
        if not login_path:
            candidate = str(auth.get("login_api") or "").strip()
            if candidate.startswith("/"):
                login_path = candidate
        for role, cred in auth.items():
            if role in _reserved or not isinstance(cred, dict):
                continue
            username = str(cred.get("username") or cred.get("email") or "").strip()
            if not username or username in seen:
                continue
            seen.add(username)
            accounts.append({
                "role": str(role).strip(),
                "email": username,
                "password": str(cred.get("password") or ""),
                "note": "settings",
            })
    return accounts, login_path


def _normalize_accounts_payload(raw: Any) -> list[dict[str, str]]:
    """Normalize test_accounts.json whether stored as a list or name→account dict."""
    if isinstance(raw, list):
        return [dict(a) for a in raw if isinstance(a, dict)]
    if isinstance(raw, dict):
        rows: list[dict[str, str]] = []
        for name, acct in raw.items():
            if not isinstance(acct, dict):
                continue
            row = {str(k): str(v) for k, v in acct.items() if v is not None}
            row.setdefault("name", str(name))
            if not row.get("role"):
                row["role"] = str(name)
            rows.append(row)
        return rows
    return []


def _load_test_accounts(root: Path, project: str) -> list[dict[str, str]]:
    """Read test_accounts.json from the project workspace, if present."""
    for base in (
        root / "platform_workspace" / project / "input",
        root / "platform_inputs" / project,
    ):
        path = base / "test_accounts.json"
        if path.exists():
            try:
                return _normalize_accounts_payload(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
    return []


def _parse_md_accounts(root: Path, project: str) -> list[dict[str, str]]:
    """Fallback: parse test_accounts.md when JSON is absent."""
    for base in (
        root / "platform_workspace" / project / "input",
        root / "platform_inputs" / project,
    ):
        for name in ("test_accounts.md", "TEST_ACCOUNTS.md"):
            path = base / name
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                return _extract_accounts_from_md(text)
    return []


def _extract_accounts_from_md(text: str) -> list[dict[str, str]]:
    accounts: list[dict[str, str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped and not in_table:
            in_table = True
            continue
        if in_table and stripped.startswith("|---"):
            continue
        if in_table and "|" in stripped:
            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                accounts.append({
                    "role": cells[0],
                    "email": cells[1],
                    "password": cells[2],
                    "note": cells[3] if len(cells) > 3 else "",
                })
    return accounts


# ── Supplementary slice generators ──────────────────────────────────────

def generate_permission_slices(
    endpoints: list[dict[str, str]],
    actors: list[dict[str, str]],
    max_slices: int = 60,
    login_path: str = "",
    login_body: dict[str, Any] | None = None,
    permission_matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Actor × endpoint permission matrix.

    For every (actor, write-endpoint) pair, create a behavior slice whose
    oracle should verify that the actor is *not* allowed to perform the action
    unless their role grants it.  The oracle is PermissionOracle.

    Focuses on low-privilege actors (the ones that reveal privilege escalation)
    and caps the total so it complements — rather than floods — the scheduler's
    per-round budget.
    """
    if not actors or not endpoints:
        return []
    slices: list[dict[str, Any]] = []
    write_endpoints = [e for e in endpoints if str(e.get("method") or "").upper() in _WRITE_METHODS]
    if not write_endpoints:
        return slices
    # Low-privilege actors are the highest-signal probes for privilege escalation.
    active = _active_actors(actors)
    unique_active: list[dict[str, str]] = []
    seen_roles: set[str] = set()
    for actor in active:
        role = str(actor.get("role") or actor.get("email") or "").strip().lower()
        if role and role not in seen_roles:
            seen_roles.add(role)
            unique_active.append(actor)
    low_priv = [a for a in unique_active if not _is_admin_like(a)]
    probe_actors = low_priv or unique_active
    per_actor: list[list[dict[str, Any]]] = []
    for actor in probe_actors:
        actor_label = (actor.get("role") or actor.get("email") or "").strip().lower()
        email = (actor.get("email") or "").strip()
        if not actor_label:
            continue
        role_defaults = _expected_permitted_roles(actor_label)
        actor_slices: list[dict[str, Any]] = []
        for ep in write_endpoints:
            declared_actions = _permission_declared_actions(
                actor_label,
                ep,
                permission_matrix=permission_matrix,
            )
            method = str(ep.get("method") or "").upper()
            path = str(ep.get("path") or "")
            if not method or not path:
                continue
            entity = str(ep.get("entity") or "resource")
            declared_roles = _endpoint_declared_roles(ep, active)
            boundary_declared = bool(
                declared_actions is not None
                or declared_roles
                or _resource_has_declared_boundary(ep, permission_matrix)
            )
            if not _permission_boundary_is_declared(
                actor_label,
                ep,
                permission_matrix=permission_matrix,
                role_defaults=role_defaults,
                boundary_declared=boundary_declared,
            ):
                # A bearer-authenticated write endpoint is not proof that this
                # particular role must be denied. Do not turn an undocumented
                # ACL assumption into a customer defect; leave the gap visible
                # to the planner for a source permission matrix or role policy.
                continue
            if declared_roles:
                permitted = actor_label in declared_roles
            else:
                permitted = (
                    "*" in role_defaults
                    or (declared_actions is not None and _declared_actions_allow(declared_actions, ep))
                )
            if (
                declared_roles
                and actor_label not in declared_roles
                and declared_actions is not None
                and _declared_actions_allow(declared_actions, ep)
            ):
                # Two customer sources disagree about the same role/action.
                # Do not choose a winner and manufacture a permission defect.
                continue
            expected_permitted = [method] if permitted else []
            permission_source_refs = [
                {
                    "kind": "permission_matrix",
                    "source_id": str(row.get("source_id") or ""),
                    "locator": str(row.get("resource") or f"{method} {path}"),
                    "quote": str(row.get("evidence") or "")[:240],
                }
                for row in permission_matrix or []
                if isinstance(row, dict)
                and str(row.get("resource") or "").strip() != "*"
                and _resource_matches_endpoint(row, ep)
            ]
            if declared_roles:
                permission_source_refs.append({
                    "kind": "api_permission_contract",
                    "locator": f"{method} {path}",
                    "quote": str(ep.get("summary") or path)[:240],
                })
            slice_id = behavior_slice_id("permission", entity, actor_label, method, path)
            identity_mutation = _is_identity_mutation_endpoint(ep)
            if identity_mutation:
                # Auth/session/account writes can invalidate the very tokens
                # needed by later probes. Without a source-declared reversible
                # compensation operation, do not schedule them as permission
                # probes in the default campaign.
                continue
            actor_slices.append({
                "slice_id": slice_id,
                "entity": entity,
                "kind": "permission",
                "states": [],
                "endpoints": [path],
                "priority": 0.72,
                "source_refs": [
                    {"kind": "test_account", "quote": email},
                    *permission_source_refs,
                ],
                "evidence_gaps": [],
                "_permission_actor": actor_label,
                "_permission_email": email,
                "_permission_password": actor.get("password", ""),
                "_permission_method": method,
                "_permission_path": path,
                "_permission_expected_permitted": expected_permitted,
                "_permission_oracle": "PermissionOracle",
                "_permission_source_strength": 2 if declared_roles else 1,
                "_cleanup_risk": 0.0,
                "_identity_mutation": False,
                "_login_path": login_path,
                "_login_body": dict(login_body or {}),
            })
        if actor_slices:
            safe = [row for row in actor_slices if not row.get("_identity_mutation")]
            risky = [row for row in actor_slices if row.get("_identity_mutation")]
            safe.sort(key=lambda row: -int(row.get("_permission_source_strength") or 0))
            diverse: list[dict[str, Any]] = []
            deferred: list[dict[str, Any]] = []
            seen_entities: set[str] = set()
            for row in safe:
                entity = str(row.get("entity") or "")
                if entity and entity not in seen_entities:
                    seen_entities.add(entity)
                    diverse.append(row)
                else:
                    deferred.append(row)
            per_actor.append([*diverse, *deferred, *risky])
    safe_groups = [[row for row in group if not row.get("_identity_mutation")] for group in per_actor]
    safe_limit = max_slices
    offset = 0
    while len(slices) < safe_limit and any(offset < len(group) for group in safe_groups):
        for group in safe_groups:
            if offset < len(group):
                slices.append(group[offset])
                if len(slices) >= safe_limit:
                    break
        offset += 1
    return slices


def _is_admin_like(actor: dict[str, str]) -> bool:
    label = (actor.get("role") or actor.get("email") or "").strip().lower()
    return "admin" in label


def generate_isolation_slices(
    endpoints: list[dict[str, str]],
    actors: list[dict[str, str]],
    max_slices: int = 12,
    login_path: str = "",
    login_body: dict[str, Any] | None = None,
    permission_matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Cross-user tenant isolation probes.

    UserA authenticates and requests UserB's resources.  The oracle is
    TenantIsolationOracle.
    """
    actors = _active_actors(actors)
    if len(actors) < 2 or not endpoints:
        return []
    slices: list[dict[str, Any]] = []
    # Entity-owned reads = deeper paths (>=2 non-placeholder segments). No
    # assumption about the API prefix (/api, /v1, /rest, or none) — we select
    # by path depth, which is universal across REST styles.
    identity_path = next(
        (
            str(e.get("path") or "")
            for e in endpoints
            if str(e.get("method") or "").upper() in _READ_METHODS
            and str(e.get("path") or "").rstrip("/").endswith("/me")
        ),
        "",
    )
    actors_by_role: dict[str, list[dict[str, str]]] = {}
    for actor in actors:
        role = str(actor.get("role") or "").strip().lower()
        if role:
            actors_by_role.setdefault(role, []).append(actor)
    read_endpoints = [
        endpoint for endpoint in endpoints
        if str(endpoint.get("method") or "").upper() in _READ_METHODS
        and str(endpoint.get("path") or "").startswith("/")
    ]
    ownership_markers = ("自己的", "本人", "归属", "own", "owner", "cross-user", "只能查询")
    corpus_ownership_params: list[str] = []
    seen_ownership_params: set[str] = set()
    for endpoint in read_endpoints:
        summary = str(endpoint.get("summary") or "")
        if not any(marker in summary.lower() or marker in summary for marker in ownership_markers):
            continue
        for match in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", summary):
            if match not in _OWNERSHIP_QUERY_PARAMS and match.lower() not in {
                p.lower() for p in _OWNERSHIP_QUERY_PARAMS
            }:
                continue
            key = match.lower()
            if key in seen_ownership_params:
                continue
            seen_ownership_params.add(key)
            corpus_ownership_params.append(match)
    for role, role_actors in actors_by_role.items():
        if len(role_actors) < 2:
            continue
        owner, viewer = role_actors[0], role_actors[1]
        own_resources = {
            _singular_token(value)
            for row in permission_matrix or []
            if isinstance(row, dict)
            and str(row.get("role") or "").strip().lower() == role
            and str(row.get("scope") or "").strip().lower() in {"own", "self", "owned"}
            for value in [row.get("resource"), *(row.get("resource_aliases") or [])]
            if str(value or "").strip()
        }
        for endpoint in read_endpoints:
            path = str(endpoint.get("path") or "")
            summary = str(endpoint.get("summary") or "")
            source_declares_ownership = bool(
                own_resources & _endpoint_resource_tokens(endpoint)
                or any(marker in summary.lower() for marker in ownership_markers)
            )
            if not source_declares_ownership:
                continue
            documented_params = [
                match
                for match in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", summary)
                if match.lower().endswith("id") or match.lower().endswith("_id")
            ]
            # Prefer ownership query params already named in the endpoint summary.
            ownership_docs = [
                match
                for match in documented_params
                if match in _OWNERSHIP_QUERY_PARAMS or match.lower() in {p.lower() for p in _OWNERSHIP_QUERY_PARAMS}
            ]
            query_candidates: list[str] = []
            if not path_has_placeholders(path):
                if ownership_docs:
                    query_candidates.append(ownership_docs[0])
                elif documented_params:
                    query_candidates.append(documented_params[0])
                # Reuse ownership binders that the same API corpus already
                # documents with ownership language (for other own-scoped
                # collections). Do not invent binders absent from the catalog.
                for param in corpus_ownership_params:
                    if param not in query_candidates:
                        query_candidates.append(param)
            has_path_target = path_has_placeholders(path)
            modes: list[tuple[str, str]] = []
            if has_path_target:
                modes.append(("path", ""))
            for query_param in query_candidates:
                modes.append(("query_param", query_param))
            if not has_path_target and not query_candidates:
                modes.append(("owned_collection", ""))
            for mode, query_param in modes:
                entity = str(endpoint.get("entity") or _path_entity(path))
                slice_key = (
                    f"{path}?{query_param}"
                    if query_param
                    else (f"{path}#owned_collection" if mode == "owned_collection" else path)
                )
                row = {
                    "slice_id": behavior_slice_id("isolation", entity, role, slice_key),
                    "entity": entity,
                    "kind": "isolation",
                    "states": [],
                    "endpoints": [path],
                    "priority": 0.90,
                    "source_refs": [{"kind": "ownership_contract", "locator": f"{path}", "quote": summary[:240] or path}],
                    "evidence_gaps": [],
                    "_hypothesis_origin": "supplementary",
                    "_isolation_viewer_role": role,
                    "_isolation_viewer_email": viewer.get("email", ""),
                    "_isolation_viewer_password": viewer.get("password", ""),
                    "_isolation_owner_role": role,
                    "_isolation_owner_email": owner.get("email", ""),
                    "_isolation_owner_password": owner.get("password", ""),
                    "_isolation_path": path,
                    "_isolation_identity_path": identity_path,
                    "_isolation_oracle": "TenantIsolationOracle",
                    "_login_path": login_path,
                    "_login_body": dict(login_body or {}),
                }
                if mode == "query_param" and query_param:
                    row["_isolation_mode"] = "query_param"
                    row["_isolation_query_param"] = query_param
                    if not ownership_docs and query_param in corpus_ownership_params:
                        row["source_refs"].append({
                            "kind": "ownership_query_param_corpus",
                            "locator": query_param,
                            "quote": query_param,
                        })
                elif mode == "owned_collection":
                    row["_isolation_mode"] = "owned_collection"
                slices.append(row)
                if len(slices) >= max_slices:
                    return slices
    return slices


def _endpoint_declares_concurrency_contract(endpoint: dict[str, Any]) -> bool:
    evidence = " ".join((str(endpoint.get("summary") or ""), str(endpoint.get("description") or ""))).lower()
    risk_terms = _lexicon_dict("risk_terms")
    terms = [*risk_terms.get("idempotency", []), *risk_terms.get("concurrency", [])]
    return bool(evidence and any(str(term).strip().lower() in evidence for term in terms if str(term).strip()))


def generate_concurrency_slices(
    endpoints: list[dict[str, str]],
    default_actor: dict[str, str] | None = None,
    login_path: str = "",
    max_slices: int = 4,
    login_body: dict[str, Any] | None = None,
    actors: list[dict[str, str]] | None = None,
    permission_matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Concurrent-write (double-write) slices.

    Applies to write endpoints generically — no domain keyword gate. A
    double-write probe is meaningful for ANY mutating endpoint; the
    ConcurrencyOracle decides at runtime whether both writes succeeded where
    mutual exclusion was expected. Prefers collection-level writes (fewer path
    parameters) so more probes execute cleanly. Works on any industry's API.
    """
    writes = [e for e in endpoints if str(e.get("method") or "").upper() in _WRITE_METHODS]
    # Prefer shallow, collection-level writes first (cleanest to execute).
    writes.sort(key=lambda e: str(e.get("path") or "").count("/"))
    slices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ep in writes:
        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        if not path or path in seen:
            continue
        if _is_auth_endpoint(ep):  # login/register are not resource-contention endpoints
            continue
        da = _select_actor_for_endpoint(ep, actors or [], permission_matrix, default_actor)
        if actors and not da:
            continue
        seen.add(path)
        entity = str(ep.get("entity") or _path_entity(path))
        slice_id = behavior_slice_id("concurrency", entity, method, path)
        slices.append({
            "slice_id": slice_id,
            "entity": entity,
            "kind": "concurrency",
            "states": [],
            "endpoints": [path],
            "priority": 0.78,
            "source_refs": [{"kind": "api_endpoint", "locator": f"{method} {path}", "quote": path}],
            "evidence_gaps": [],
            "_concurrency_method": method,
            "_concurrency_path": path,
            "_concurrency_oracle": "ConcurrencyOracle",
            "_concurrency_contract_declared": _endpoint_declares_concurrency_contract(ep),
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_default_actor": (da.get("role") or da.get("email") or "").strip().lower(),
            "_default_email": da.get("email", ""),
            "_default_password": da.get("password", ""),
        })
        if len(slices) >= max_slices:
            break
    return slices


def generate_inventory_slices(
    endpoints: list[dict[str, str]],
    default_actor: dict[str, str] | None = None,
    login_path: str = "",
    max_slices: int = 5,
    login_body: dict[str, Any] | None = None,
    actors: list[dict[str, str]] | None = None,
    permission_matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Inventory-integrity slices for stock/reserve/consume endpoints."""
    writes = [
        e for e in endpoints
        if str(e.get("method") or "").upper() in _WRITE_METHODS
        and "inventory" in str(e.get("path") or "").lower()
    ]
    writes.sort(key=lambda e: str(e.get("path") or "").count("/"))
    slices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ep in writes:
        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        if not path or path in seen:
            continue
        if _is_auth_endpoint(ep):
            continue
        da = _select_actor_for_endpoint(ep, actors or [], permission_matrix, default_actor)
        if actors and not da:
            continue
        seen.add(path)
        entity = str(ep.get("entity") or _path_entity(path))
        slice_id = behavior_slice_id("inventory", entity, method, path)
        slices.append({
            "slice_id": slice_id,
            "entity": entity,
            "kind": "inventory",
            "states": [],
            "endpoints": [path],
            "priority": 0.84,
            "source_refs": [{"kind": "api_endpoint", "locator": f"{method} {path}", "quote": path}],
            "evidence_gaps": [],
            "_inventory_method": method,
            "_inventory_path": path,
            "_inventory_oracle": "InventoryOracle",
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_default_actor": (da.get("role") or da.get("email") or "").strip().lower(),
            "_default_email": da.get("email", ""),
            "_default_password": da.get("password", ""),
        })
        if len(slices) >= max_slices:
            break
    return slices


# Path/entity tokens that strongly suggest money/quantity conservation probes.
# Industry-neutral REST vocabulary — not project-specific routes.
_MONEY_PATH_TOKENS = frozenset({
    "pay", "payment", "payments", "refund", "refunds", "settle", "settlement",
    "balance", "wallet", "ledger", "billing", "invoice", "invoices", "charge",
    "amount", "price", "pricing", "coupon", "coupons", "discount", "promo",
    "inventory", "stock", "reserve", "release", "consume", "quota", "credit",
    "debit", "transfer", "checkout", "cart", "order", "orders",
})


def _money_endpoint_rank(endpoint: dict[str, str]) -> tuple[int, int, int]:
    """Prefer conservation-relevant writes, then shallower paths.

    Without ranking, a tiny ``max_slices`` budget only hits the shallowest
    POSTs (often create-collection) and starves pay/refund/inventory probes
    that actually exercise money_quantity_conservation bugs.
    """
    path = str(endpoint.get("path") or "").strip().lower()
    entity = str(endpoint.get("entity") or "").strip().lower()
    tokens = {part for part in path.strip("/").split("/") if part and not part.startswith("{")}
    tokens.add(entity)
    hit = 1 if tokens & _MONEY_PATH_TOKENS else 0
    # Stronger boost when the leaf action itself is financial (pay/refund/...).
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    leaf_hit = 1 if leaf in _MONEY_PATH_TOKENS else 0
    depth = path.count("/")
    return (-leaf_hit, -hit, depth)


def generate_money_slices(
    endpoints: list[dict[str, str]],
    default_actor: dict[str, str] | None = None,
    login_path: str = "",
    max_slices: int = 12,
    login_body: dict[str, Any] | None = None,
    actors: list[dict[str, str]] | None = None,
    permission_matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Financial-integrity slices.

    Prefers write endpoints whose path/entity tokens suggest money, payment,
    refund, inventory, or pricing semantics, then fills remaining budget with
    other writes. MoneyOracle judges responses — no per-project route map.
    """
    writes = [
        e for e in endpoints
        if str(e.get("method") or "").upper() in _WRITE_METHODS
        and _money_endpoint_rank(e)[1] < 0
    ]
    writes.sort(key=_money_endpoint_rank)
    slices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ep in writes:
        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        if not path or path in seen:
            continue
        if _is_auth_endpoint(ep):  # login/register have no financial semantics
            continue
        da = _select_actor_for_endpoint(ep, actors or [], permission_matrix, default_actor)
        if actors and not da:
            continue
        seen.add(path)
        entity = str(ep.get("entity") or _path_entity(path))
        slice_id = behavior_slice_id("money", entity, method, path)
        rank = _money_endpoint_rank(ep)
        # Higher priority for conservation-relevant leaves so budget steering
        # keeps pay/refund/inventory ahead of generic shallow creates.
        priority = 0.90 if rank[0] < 0 else (0.86 if rank[1] < 0 else 0.80)
        slices.append({
            "slice_id": slice_id,
            "entity": entity,
            "kind": "money",
            "states": [],
            "endpoints": [path],
            "priority": priority,
            "source_refs": [{"kind": "api_endpoint", "locator": f"{method} {path}", "quote": path}],
            "evidence_gaps": [],
            "_money_method": method,
            "_money_path": path,
            "_money_oracle": "MoneyOracle",
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_default_actor": (da.get("role") or da.get("email") or "").strip().lower(),
            "_default_email": da.get("email", ""),
            "_default_password": da.get("password", ""),
        })
        if len(slices) >= max_slices:
            break
    return slices


# ── Helpers ──────────────────────────────────────────────────────────────

def _expected_permitted_roles(actor_label: str) -> list[str]:
    """Minimal, source-derived permission matrix — no business assumptions.

    This is intentionally sparse.  The oracle must determine "not permitted"
    by runtime observation, not by a hardcoded ruleset.  Only uncontroversial
    defaults (admin → all, readonly → reads only) are declared.
    """
    label = actor_label.lower()
    if "admin" in label:
        return ["*"]
    if any(t in label for t in ("read", "view", "audit", "只读")):
        return ["GET", "HEAD", "OPTIONS"]
    return []  # oracle determines this at runtime


def _permission_boundary_is_declared(
    actor_label: str,
    endpoint: dict[str, Any],
    *,
    permission_matrix: list[dict[str, Any]] | None,
    role_defaults: list[str],
    boundary_declared: bool = False,
) -> bool:
    """Return whether source material declares a role/endpoint boundary."""

    if boundary_declared:
        return True
    return _permission_declared_actions(
        actor_label,
        endpoint,
        permission_matrix=permission_matrix,
    ) is not None


def _permission_declared_actions(
    actor_label: str,
    endpoint: dict[str, Any],
    *,
    permission_matrix: list[dict[str, Any]] | None,
) -> set[str] | None:
    matched = False
    declared: set[str] = set()
    for row in permission_matrix or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or row.get("actor") or row.get("role_id") or "").strip().lower()
        actions = row.get("actions") or row.get("methods") or row.get("allowed_actions") or []
        action_values = {
            str(item).strip()
            for item in actions
        } if isinstance(actions, (list, tuple, set)) else {str(actions).strip()}
        role_matches = bool(role and (role == actor_label or role in actor_label or actor_label in role))
        if role_matches and _resource_matches_endpoint(row, endpoint):
            matched = True
            declared.update(value for value in action_values if value)
    return declared if matched else None


def _path_entity(path: str) -> str:
    """Derive a human-readable entity name from an API path fragment."""
    clean = str(path).strip("/")
    parts = [p for p in clean.split("/") if p and not p.startswith("{")]
    for i, part in enumerate(parts):
        if part in ("api", "v1", "v2", "v3"):
            continue
        return part.replace("-", "_").replace(".", "_")
    return "resource"


# ── Aggregate entry ──────────────────────────────────────────────────────

def _discover_login_endpoint(endpoints: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    """Find the authentication endpoint and its documented request schema.

    Returns (login_path, login_body_template).  The body template is derived
    from the endpoint's summary field (Markdown API spec format) where the
    documented example is `{\"email\":\"...\",\"password\":\"...\"}`.  If no
    example body can be parsed, returns an empty template — the scenario
    builders will still attempt auto-discovered credentials but the body
    format must be declared in a project config for full reliability.

    No hardcoded field names (email / password / username / account).
    """
    auth_tokens = ("login", "signin", "sign-in", "session", "sessions", "token", "authenticate")
    best: tuple[str, dict[str, Any]] = ("", {})
    for e in endpoints:
        method = str(e.get("method") or "").upper()
        if method not in _WRITE_METHODS:
            continue
        path = str(e.get("path") or "")
        action = str(e.get("action") or "").lower()
        hay = (path + " " + action).lower()
        if not any(tok in hay for tok in auth_tokens):
            continue
        body = _parse_documented_request_body(str(e.get("summary") or ""))
        if not best[0] or len(path) < len(best[0]):
            best = (path, body)
    return best


def _parse_documented_request_body(summary: str) -> dict[str, Any]:
    """Extract a JSON-like request body from a Markdown API spec summary field.

    Common patterns in the project's docs:
      '请求： {\"email\":\"buyer01@example.com\",\"password\":\"Test@123456\"}'
      'Request body: {\"account\":\"...\",\"secret\":\"...\"}'

    Returns the parsed JSON dict on success, empty dict on failure. Generic
    across any field names the project documents.
    """
    import re as _re
    text = str(summary or "")
    for pattern in (r'[\u8bf7\u6c42\u6216\u8005]{1,4}[:：]\s*(\{.*?\})', r'[Rr]equest\s*(?:body)?[:：]\s*(\{.*?\})', r'(\{\s*"[^"]+"\s*:.*?\})'):
        m = _re.search(pattern, text)
        if m:
            try:
                candidate = _re.sub(r'"\s*\.\.\."', '""', m.group(1))
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and len(parsed) >= 1:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


def _account_status_token(account: dict[str, str]) -> str:
    for key in ("status", "account_status", "state"):
        value = str(account.get(key) or "").strip().upper()
        if value:
            return value
    email = str(account.get("email") or account.get("name") or "").lower()
    if "disabled" in email or "locked" in email:
        return "DISABLED"
    return ""


def generate_account_status_slices(
    actors: list[dict[str, str]],
    login_path: str = "",
    login_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fresh-login probes for DISABLED/LOCKED accounts.

    Catches bugs where suspended accounts still receive a valid token on login
    (e.g. benchmark AUTH-001). Uses only credentials declared in test_accounts —
    no hardcoded roles or endpoints.
    """
    if not actors or not login_path:
        return []
    slices: list[dict[str, Any]] = []
    for actor in actors:
        status = _account_status_token(actor)
        if status not in {"DISABLED", "LOCKED"}:
            continue
        email = str(actor.get("email") or "").strip()
        password = str(actor.get("password") or "").strip()
        role = str(actor.get("role") or actor.get("name") or email).strip()
        if not email or not password:
            continue
        slice_id = behavior_slice_id("account_status", "auth", role, "POST", login_path)
        slices.append({
            "slice_id": slice_id,
            "entity": "auth",
            "kind": "account_status",
            "states": [],
            "endpoints": [login_path],
            "priority": 0.95,
            "source_refs": [{"kind": "test_account", "locator": email, "quote": email}],
            "evidence_gaps": [],
            "_account_status": status,
            "_account_status_email": email,
            "_account_status_password": password,
            "_account_status_role": role,
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_permission_oracle": "PermissionOracle",
        })
    return slices


def generate_supplementary_slices(
    root: Path,
    project: str,
    api_spec_text: str,
    permission_matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Produce all supplementary slices in a single call.

    Fully source/config-driven — endpoints from the API spec parser, actors
    from test_accounts, login route auto-discovered. No project-specific,
    industry-specific, or endpoint hardcoding. Returns an empty list when a
    required data source is unavailable.
    """
    if not str(api_spec_text or "").strip():
        return []
    import re as _re
    state_re = _re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", _re.I,
    )
    _entities, _states, endpoints = _api_facts(api_spec_text, state_re)
    if not endpoints:
        return []
    # Account source priority: (1) Settings page (multi_service_config.json,
    # maintained by the customer via the UI), (2) test_accounts.json,
    # (3) TEST_ACCOUNTS.md.  The Settings store is authoritative.
    actors, settings_login = load_settings_accounts(root, project)
    if not actors:
        actors = _load_test_accounts(root, project)
    if not actors:
        actors = _parse_md_accounts(root, project)
    # Login route: prefer the operator-declared login_api from Settings, else
    # auto-discover from the API catalog.  The body template follows the login
    # endpoint's documented request schema (no hardcoded {email,password}).
    auto_login_path, auto_login_body = _discover_login_endpoint(endpoints)
    login_path = settings_login or auto_login_path
    login_body_template = auto_login_body
    # Default actor for un-scoped probes: first non-admin account, else first.
    default_actor: dict[str, str] = {}
    active_actors = _active_actors(actors)
    if active_actors:
        default_actor = next((a for a in active_actors if not _is_admin_like(a)), active_actors[0])
    all_slices: list[dict[str, Any]] = []
    if login_path and actors:
        all_slices.extend(generate_account_status_slices(actors, login_path=login_path, login_body=login_body_template))
    if actors and any(str(e.get("method") or "").upper() in _WRITE_METHODS for e in endpoints):
        all_slices.extend(generate_permission_slices(
            endpoints,
            actors,
            login_path=login_path,
            login_body=login_body_template,
            permission_matrix=permission_matrix,
        ))
    if len(actors) >= 2 and any(str(e.get("method") or "").upper() in _READ_METHODS for e in endpoints):
        all_slices.extend(generate_isolation_slices(
            endpoints,
            actors,
            login_path=login_path,
            login_body=login_body_template,
            permission_matrix=permission_matrix,
        ))
    if any(str(e.get("method") or "").upper() in _WRITE_METHODS for e in endpoints):
        all_slices.extend(generate_inventory_slices(
            endpoints, default_actor, login_path, login_body=login_body_template,
            actors=actors, permission_matrix=permission_matrix,
        ))
        all_slices.extend(generate_money_slices(
            endpoints, default_actor, login_path, login_body=login_body_template,
            actors=actors, permission_matrix=permission_matrix,
        ))
        all_slices.extend(generate_concurrency_slices(
            endpoints, default_actor, login_path, login_body=login_body_template,
            actors=actors, permission_matrix=permission_matrix,
        ))
    return all_slices


def probe_disabled_account_logins(
    root: Path,
    project: str,
    api_spec_text: str,
    base_url: str,
    *,
    campaign_id: str = "",
    discovery_round: int = 1,
) -> list[dict[str, Any]]:
    """Mandatory fresh-login probes for DISABLED/LOCKED test accounts.

    Runs before the slice-budget queue so AUTH-class login bugs (e.g. suspended
    accounts still receiving tokens) are never starved by probe_selection.
    Fully config-driven — actors and login route from customer materials only.
    """
    if not str(base_url or "").strip() or not str(api_spec_text or "").strip():
        return []
    import re as _re

    state_re = _re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", _re.I,
    )
    _entities, _states, endpoints = _api_facts(api_spec_text, state_re)
    actors, settings_login = load_settings_accounts(root, project)
    if not actors:
        actors = _load_test_accounts(root, project)
    if not actors:
        actors = _parse_md_accounts(root, project)
    auto_login_path, auto_login_body = _discover_login_endpoint(endpoints)
    login_path = settings_login or auto_login_path
    if not login_path or not actors:
        return []

    findings: list[dict[str, Any]] = []
    for actor in actors:
        status = _account_status_token(actor)
        if status not in {"DISABLED", "LOCKED"}:
            continue
        email = str(actor.get("email") or "").strip()
        password = str(actor.get("password") or "").strip()
        role = str(actor.get("role") or actor.get("name") or email).strip()
        if not email or not password:
            continue
        body = dict(auto_login_body or {})
        for key in list(body.keys()):
            low = str(key).lower()
            if low in {"email", "username", "user", "account"}:
                body[key] = email
            elif low in {"password", "passwd", "pass"}:
                body[key] = password
        if not body:
            body = {"email": email, "password": password}
        url = base_url.rstrip("/") + login_path
        method = "POST"
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url,
                method=method,
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(300_000).decode("utf-8", errors="replace")
                http_status = int(response.status)
                resp_headers = dict(response.headers.items()) if response.headers else {}
                resp_body: Any
                try:
                    resp_body = json.loads(raw)
                except Exception:
                    resp_body = {"_raw": raw[:2000]}
        except urllib.error.HTTPError as exc:
            http_status = int(exc.code)
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
            try:
                resp_body = json.loads(raw) if raw else {}
            except Exception:
                resp_body = {"_raw": raw[:2000]}
        except Exception as exc:
            continue
        if not (200 <= http_status < 300):
            continue
        if not _auth_acceptance_observed(resp_body, resp_headers):
            continue
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        evidence_id = f"EVID_LOGIN_{role}_{int(time.time())}"
        redacted_body = _redact_probe_artifact(body)
        redacted_resp_body = _redact_probe_artifact(resp_body)
        redacted_resp_headers = _redact_probe_artifact(resp_headers)
        expected_text = f"{status} account login must be rejected before credentials/session are issued (HTTP 401/403)."
        actual_text = f"HTTP {http_status} issued a login acceptance signal."
        failed_assertion = f"Expected {expected_text} Actual {actual_text}"
        reproduction_steps = [f"{method} {login_path} body={json.dumps(redacted_body, ensure_ascii=False)}"]
        findings.append({
            "severity": "P0",
            "title": f"[账号状态登录绕过] {status} 账号 {email} 仍可登录获 token",
            "category": "authorization_access_control",
            "source": "disabled_account_login_probe",
            "description": f"{status} 账号 {email} 调用 {method} {login_path} 返回 HTTP {http_status}，应拒绝登录",
            "confidence_score": 0.98,
            "behavior_slice_id": behavior_slice_id("account_status", "auth", role, method, login_path),
            "discovery_round": discovery_round,
            "campaign_id": campaign_id,
            "execution_status": "executed",
            "confirmation_status": "confirmed",
            "gate_passed": True,
            "customer_delivery_status": "defect",
            "bug_status": "reproduced",
            "expected": expected_text,
            "actual": actual_text,
            "timestamp": ts,
            "failed_assertions": [failed_assertion],
            "expected_actual_comparison": {
                "expected": expected_text,
                "actual": actual_text,
                "difference": "Disabled or locked account received a successful authentication response.",
            },
            "method": method,
            "path": login_path,
            "evidence_id": evidence_id,
            "evidence": {
                "request": f"{method} {login_path}",
                "response": {"status_code": http_status, "headers": redacted_resp_headers, "body": redacted_resp_body},
                "assertion": f"{status} account must not receive a valid login token",
                "timestamp": ts,
                "target": login_path,
                "actor": role,
                "reproduction_steps": reproduction_steps,
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "account_status": status,
                "email": email,
                "request_raw": {"method": method, "path": login_path, "actor": role, "body": redacted_body},
                "response_raw": {"status_code": http_status, "headers": redacted_resp_headers, "body": redacted_resp_body},
                "execution_trace": {"evidence_id": evidence_id, "layers": ["runtime_http_auth_acceptance"]},
                "timestamp": ts,
            },
            "reproduction": {
                "method": method,
                "path": login_path,
                "actor": role,
                "is_synthetic": False,
                "reproduction_steps": reproduction_steps,
                "har_evidence": {"status_code": http_status, "response_headers": redacted_resp_headers, "response_body": redacted_resp_body},
            },
            "evidence_quality": {
                "level": "validated",
                "score": 95,
                "can_reproduce": True,
                "evidence_strength": "runtime_http_auth_acceptance",
            },
            "evidence_status": {
                "semantic_verdict": "SEMANTIC_CONFIRMED",
                "business_evidence_status": "VALIDATED",
                "final_review_status": "VALIDATED_CANDIDATE",
                "missing_requirements": [],
            },
            "final_review_status": "VALIDATED_CANDIDATE",
            "business_evidence_status": "VALIDATED",
            "evidence_strength": "runtime_http_auth_acceptance",
            "reproduction_steps": reproduction_steps,
        })
    return findings
