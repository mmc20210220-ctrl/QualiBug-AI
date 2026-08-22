from __future__ import annotations

# CANDIDATE_RECALL_MISS is intentionally a diagnostic/recovery signal, not an
# acceptance path. A globally known interface that falls outside the current
# candidate set must remain rejected by the contract boundary.

"""Expert semantic linking constrained to source-backed knowledge identities.

The model is used for the part machines are good at: understanding how an
enterprise rule relates to a documented interface in the surrounding business
model. It is not a fact authority. Every accepted link names existing rule,
interface, and supporting-fact identities; malformed output fails fast and
uncertain or invented proposals stay visible in the receipt.

Workload structure (incremental candidate-scoped linking)
----------------------------------------------------------
The linker no longer sends every rule against the full interface catalog.
Linking is a three-tier pipeline, all three tiers deterministic except tier 2:

* Tier 1 - structured candidate recall (no LLM): each rule is matched against
  the documented interfaces using deterministic, asset-driven signals: entity
  tokens (language-neutral entity lexicon + alias groups + path-segment
  variants), schema field overlap (rule-mentioned columns/fields vs interface
  fields), state tokens, operation verbs, the permission matrix
  (role -> resource/actions), request/response field intersection, the rule
  source locator (same source document), and entity-relation expansion.
  Recall is deliberately high-recall and bounded: each rule keeps at most
  ``MAX_CANDIDATES_PER_RULE`` candidates, falling back to a cheap lexical
  ranking when fewer than ``MIN_CANDIDATES_PER_RULE`` signals matched. Every
  rule also recalls its own supporting facts (``MAX_SUPPORTING_FACTS_PER_RULE``)
  instead of one globally truncated 400-fact dump, so no fact needed by any
  rule is silently dropped.

* Tier 2 - small-candidate LLM re-ranking: the model judges "1 rule x its
  candidate shortlist" (plus that rule's supporting facts) instead of
  "40 rules x all interfaces x all transitions x 400 facts". State transitions
  are sent exactly once per asset in a dedicated request instead of being
  repeated inside every rule batch.

* Tier 3 - deterministic contract validation: every model output (fresh or
  cached) passes the same exact-identity/evidence/confidence validation used
  before. A model link to an interface that was not in the rule's candidate
  shortlist is receipted as ``CANDIDATE_RECALL_MISS`` and rejected; invented
  identities stay rejected as ``UNKNOWN_INTERFACE_ID``.

Content-addressed cache
-----------------------
The raw model assessment for a unit is cached under a key derived from
``rule fingerprint + candidate interface fingerprints + supporting fact
fingerprints + model config fingerprint``. Only material or candidate changes
re-issue a model call; unchanged assets re-link without any provider request.
Cache entries store the raw model output and always pass through Tier 3
validation, so a stale or corrupt cache entry can never bypass the contract.

Batch independence
------------------
Every request batch (and the single transition request) succeeds or fails on
its own. A failed batch is receipted in ``failed_units`` and its rules remain
unassessed (visible gaps); successful batches keep their accepted edges.
The linker degrades to source-only only when *every* unit failed (raised as
``agent_semantic_all_units_failed``), never when a subset failed.

A cache directory can be enabled with the ``QUALIBUG_SEMANTIC_CACHE_DIR``
environment variable (JSON files keyed by content hash); without it the cache
is in-memory for the current process.
"""

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from .artifact_redactor import redact_and_validate
from .llm_reasoning import estimate_input_tokens
from .observed_product_scan_protocol import find_evaluator_private_context_paths


def _linker_input_budget() -> int:
    """操作员可覆盖的 linker 每调用输入预算（token）。"""
    raw = os.getenv(LINKER_MAX_INPUT_TOKENS_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return LINKER_MAX_INPUT_TOKENS_DEFAULT
    return value if value > 0 else LINKER_MAX_INPUT_TOKENS_DEFAULT


RECEIPT_SCHEMA = "qualibug.agent-semantic-link-receipt.v2"
PROMPT_PROTOCOL = "qualibug.agent-business-semantic-assessment.v3"
MIN_CONFIDENCE = 0.65
MAX_LINKS_PER_RULE = 4
MAX_PROVIDER_ATTEMPTS = 2
MAX_RULES_PER_REQUEST = 40
MAX_PROVIDER_REQUESTS = 8
# Legacy global fact budget. The incremental linker replaces the global
# truncation with per-rule fact recall; the constant is preserved as the
# documented upper bound a single supporting-fact slice may never exceed.
MAX_CONTEXT_FACTS = 400
MAX_CANDIDATES_PER_RULE = 12
MIN_CANDIDATES_PER_RULE = 3
MAX_SUPPORTING_FACTS_PER_RULE = 20
MAX_TRANSITIONS_PER_REQUEST = 200
CACHE_DIRECTORY_ENV = "QUALIBUG_SEMANTIC_CACHE_DIR"

# ── 输入预算守卫（20260821 成本事故根因修复）─────────────────────────────
# 该次运行中 linker 以 ~102K token/请求的巨型 prompt 反复调用，直至供应商
# 402 Insufficient Balance。全局 LLM_MAX_INPUT_TOKENS 默认 900000 是引擎级
# 预算，对组装型消费者形同虚设。linker 由此声明自己的每调用输入预算：
# 组批前用同一 CJK 感知估算器预检，超限逐级二分缩批（保持单规则上下文完整，
# 不砍召回）；缩到单规则仍超限的 unit 显式 BLOCKED（具名 reason code），
# 绝不静默发送、绝不静默丢弃。预算可由操作员经 env 覆盖。
LINKER_MAX_INPUT_TOKENS_DEFAULT = 32768
LINKER_MAX_INPUT_TOKENS_ENV = "LLM_LINKER_MAX_INPUT_TOKENS"

# The bounded, source-backed semantic frames the linker actually consumes.
# The private-context guard must cover exactly these collections; product-owned
# bookkeeping sections elsewhere in the knowledge asset (identity benchmark
# annotations, understanding-model receipts) are not linker inputs.
LINKER_INPUT_COLLECTIONS = (
    "rule_library",
    "interfaces",
    "data_tables",
    "field_dictionary",
    "roles",
    "state_machines",
    "permission_matrix",
    "entity_relations",
)
_DISPOSITIONS = frozenset({
    "LINKED",
    "NO_EXECUTABLE_INTERFACE",
    "AMBIGUOUS",
})
_TRANSIENT_PROVIDER_ERROR_NAMES = frozenset({
    "IncompleteRead",
    "TimeoutError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "RemoteDisconnected",
    "SSLEOFError",
})
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]{2,}")


class AgentSemanticLinkerError(ValueError):
    """Agent output is unavailable, malformed, or outside source-backed inputs."""


class AgentJsonClient(Protocol):
    def complete_json(self, **kwargs: Any) -> dict[str, Any]: ...

    def usage_snapshot(self) -> dict[str, float]: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_client() -> AgentJsonClient:
    from .llm_reasoning import ReasoningClient, ReasoningConfig

    config = ReasoningConfig.from_env()
    if not config.enabled:
        raise AgentSemanticLinkerError("agent_provider_not_configured")
    # This client maps immutable source identities for an audit receipt.  A
    # non-zero sampling temperature makes the same source batch occasionally
    # change from NO_EXECUTABLE_INTERFACE to malformed LINKED output, which
    # either aborts the mainline or changes the compiled obligation set.  Keep
    # provider sampling deterministic here; creative temperature belongs to
    # advisory reasoning, never to source-to-interface authority.
    config.temperature = 0.0
    config.timeout_seconds = max(int(config.timeout_seconds or 0), 300)
    config.max_tokens = max(int(config.max_tokens or 0), 32768)
    return ReasoningClient(config=config)


def _is_transient_provider_error(exc: BaseException) -> bool:
    if type(exc).__name__ in _TRANSIENT_PROVIDER_ERROR_NAMES:
        return True
    message = str(exc).lower()
    return (
        type(exc).__name__ == "ReasoningClientError"
        and "did not include json content" in message
    )


def _prompt_safe_text(value: Any, *, limit: int) -> str:
    text = _EMAIL_RE.sub("<REDACTED_EMAIL>", _text(value))
    return text[:limit]


def _rule_statement(row: dict[str, Any]) -> str:
    frame = _dict(row.get("semantic_frame"))
    condition = _prompt_safe_text(frame.get("condition"), limit=320)
    subject = _prompt_safe_text(frame.get("subject"), limit=320)
    behavior = _prompt_safe_text(frame.get("behavior"), limit=640)
    if condition or subject or behavior:
        return " | ".join(
            part for part in (condition, subject, behavior) if part
        )

    statement = _text(row.get("statement"))
    cells = [cell.strip() for cell in statement.strip("|").split("|")]
    if len(cells) > 1:
        semantic = [
            cell
            for cell in cells
            if re.search(
                r"\b(?:must|shall|should|cannot|forbidden|prohibited|"
                r"not\s+allowed|does\s+not)\b|"
                r"(?:必须|应当|应该|不得|不能|不可|不应|禁止|只能|仅限)",
                cell,
                flags=re.I,
            )
        ]
        if semantic:
            statement = " | ".join(semantic)
    return _prompt_safe_text(statement, limit=800)


def _schema_field_names(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 6:
        return []
    if isinstance(value, dict):
        names: list[str] = []
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.extend(_text(key) for key in properties if _text(key))
        for key in ("schema", "items", "content", "requestBody", "responses"):
            names.extend(_schema_field_names(value.get(key), depth=depth + 1))
        for child in value.values():
            if isinstance(child, (dict, list)):
                names.extend(_schema_field_names(child, depth=depth + 1))
        return list(dict.fromkeys(names))[:120]
    if isinstance(value, list):
        names: list[str] = []
        for child in value:
            names.extend(_schema_field_names(child, depth=depth + 1))
        return list(dict.fromkeys(names))[:120]
    return []


def _rule_prompt_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": _text(row.get("rule_id")),
        "statement": _rule_statement(row),
        "kind": _text(
            row.get("kind") or row.get("rule_type") or row.get("risk_type")
        ),
        "semantic_frame": {
            key: _dict(row.get("semantic_frame")).get(key)
            for key in (
                "modality",
                "polarity",
                "condition",
                "subject",
                "behavior",
                "source_anchors",
            )
            if _dict(row.get("semantic_frame")).get(key) not in (None, "", [])
        },
        "causal_chain": {
            key: _dict(row.get("causal_chain")).get(key)
            for key in ("preconditions", "trigger_action", "postconditions")
            if _dict(row.get("causal_chain")).get(key) not in (None, "", [])
        },
        "source_id": _text(row.get("source_id")),
        "source_locator": _text(row.get("source_locator")),
    }


def _transition_token(value: Any) -> str:
    """Lowercase, whitespace-free state token shared with the IR state nodes."""
    return _text(value).lower().replace(" ", "").replace("-", "")


def _machine_transitions(
    machine: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Enumerate (kind, from, to) pairs for one source state machine.

    ``forbidden_transitions`` may carry a wildcard target (``CLOSED -> 任意状态``
    recorded as an empty destination) — such rows are kept with an empty
    ``to_state`` so the linker can still bind the operation that must never be
    performed from that state, but only when the machine explicitly declared
    them. Nothing is inferred here.
    """
    out: list[tuple[str, str, str]] = []
    machine_ref = _text(machine.get("state_machine_id") or machine.get("id"))
    for kind, key in (("allowed", "transitions"), ("forbidden", "forbidden_transitions")):
        for raw in _list(machine.get(key)):
            transition = _dict(raw)
            from_state = _text(transition.get("from") or transition.get("from_state"))
            to_state = _text(transition.get("to") or transition.get("to_state"))
            if not from_state:
                continue
            out.append((kind, from_state, to_state))
    return out


def _transition_prompt_row(
    machine_ref: str,
    kind: str,
    from_state: str,
    to_state: str,
) -> dict[str, Any]:
    transition_id = transition_identity(machine_ref, kind, from_state, to_state)
    return {
        "transition_id": transition_id,
        "machine_id": machine_ref,
        "kind": kind,
        "entity": _text(
            machine_ref.partition(":")[2].partition(":")[0]
            if machine_ref.count(":") >= 2
            else ""
        ),
        "from_state": from_state,
        "to_state": to_state,
    }


def transition_identity(
    machine_ref: str,
    kind: str,
    from_state: str,
    to_state: str,
) -> str:
    """Stable identity for one state-machine transition.

    Shared by the linker (assessment id), the semantic binder (accepted edge
    resolution) and the Behavior IR derivation, so an accepted
    ``state_transition_to_interface`` edge resolves without fuzzy matching.
    """
    machine = _text(machine_ref) or "state_machine"
    kind_norm = "allowed" if kind in ("allowed", "transitions", "") else "forbidden"
    from_token = _transition_token(from_state)
    to_token = _transition_token(to_state)
    return f"st:{machine}:{kind_norm}:{from_token}:{to_token}"


def _asset_transition_rows(
    asset: dict[str, Any],
) -> list[dict[str, Any]]:
    """All assessed transitions across every source state machine."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _list(asset.get("state_machines")):
        machine = _dict(raw)
        machine_ref = _text(machine.get("state_machine_id") or machine.get("id"))
        if not machine_ref:
            continue
        for kind, from_state, to_state in _machine_transitions(machine):
            row = _transition_prompt_row(machine_ref, kind, from_state, to_state)
            transition_id = _text(row.get("transition_id"))
            if not transition_id or transition_id in seen:
                continue
            seen.add(transition_id)
            rows.append(row)
    return rows


def _interface_prompt_row(row: dict[str, Any]) -> dict[str, Any]:
    field_names = [
        _text(value)
        for value in _list(row.get("field_dictionary"))
        if _text(value)
    ]
    field_names.extend(
        _schema_field_names(row.get("request_schema") or row.get("requestBody"))
    )
    field_names.extend(
        _schema_field_names(row.get("response_schema") or row.get("responses"))
    )
    parameter_names = [
        _text(value.get("name") if isinstance(value, dict) else value)
        for value in _list(row.get("parameters"))
        if _text(value.get("name") if isinstance(value, dict) else value)
    ]
    return {
        "interface_id": _text(row.get("interface_id")),
        "method": _text(row.get("method")).upper(),
        "path": _text(row.get("path")),
        "summary": _prompt_safe_text(row.get("summary"), limit=320),
        "description": _prompt_safe_text(row.get("description"), limit=500),
        "fields": list(dict.fromkeys([*field_names, *parameter_names]))[:120],
        "tags": [
            _prompt_safe_text(value, limit=80)
            for value in _list(row.get("tags"))
            if _text(value)
        ][:20],
        "source_id": _text(row.get("source_id")),
    }


def _candidate_interface_prompt_row(row: dict[str, Any]) -> dict[str, Any]:
    """Compact candidate row: same identity surface, bounded field list."""
    candidate = _interface_prompt_row(row)
    candidate["fields"] = candidate["fields"][:40]
    return candidate


def _all_fact_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Build compact source-backed business facts; never include example values.

    Unlike the legacy global-truncation slice, this pool is complete: every
    fact row is available for per-unit recall, so no fact needed by any rule is
    silently dropped. Selection happens per rule in ``_recall_supporting_facts``.
    """

    specs = (
        (
            "data_tables",
            "table_id",
            ("name", "columns", "foreign_keys", "source_id"),
        ),
        (
            "field_dictionary",
            "field_id",
            ("table_id", "table", "field", "type", "required", "source_id"),
        ),
        (
            "roles",
            "role_id",
            ("role", "name", "scope", "source_id"),
        ),
        (
            "state_machines",
            "state_machine_id",
            (
                "object",
                "states",
                "transitions",
                "forbidden_transitions",
                "source_id",
            ),
        ),
        (
            "permission_matrix",
            "permission_id",
            (
                "role",
                "resource",
                "actions",
                "denied_actions",
                "decision",
                "scope",
                "source_id",
            ),
        ),
        (
            "entity_relations",
            "relation_id",
            (
                "from_entity",
                "to_entity",
                "relation_type",
                "source_id",
                "source_chunk_id",
            ),
        ),
    )
    facts: list[dict[str, Any]] = []
    for collection, id_key, keys in specs:
        for raw in _list(asset.get(collection)):
            row = _dict(raw)
            fact_id = _text(row.get(id_key) or row.get("id"))
            if not fact_id and collection == "entity_relations":
                fact_id = (
                    "fact:entity_relation:"
                    + _fingerprint({
                        key: row.get(key)
                        for key in keys
                        if row.get(key) not in (None, "", [])
                    })[:20]
                )
            if not fact_id:
                continue
            fact = {
                "fact_id": fact_id,
                "fact_kind": collection,
            }
            for key in keys:
                value = row.get(key)
                if value not in (None, "", []):
                    fact[key] = value
            facts.append(fact)
    facts.sort(key=lambda row: (row["fact_kind"], row["fact_id"]))
    return facts


def _index_exact_ids(
    rows: Any,
    *,
    id_key: str,
    collection: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in _list(rows):
        if not isinstance(raw, dict):
            continue
        identity = _text(raw.get(id_key))
        if not identity:
            continue
        if identity in indexed:
            raise AgentSemanticLinkerError(
                f"agent_semantic_duplicate_identity:{collection}:{identity}"
            )
        indexed[identity] = dict(raw)
    return indexed


def _complete_batch(
    client: AgentJsonClient,
    *,
    prompt: str,
    max_input_tokens: int | None = None,
) -> tuple[dict[str, Any], int, int]:
    retry_count = 0
    last_error: Exception | None = None
    for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
        try:
            kwargs: dict[str, Any] = {
                "caller": "agent_semantic_linker",
                "system_prompt": (
                    "You perform source-grounded enterprise business semantic analysis. "
                    "Identifiers and supplied facts are the only authority. Output JSON only."
                ),
                "user_prompt": prompt,
            }
            if max_input_tokens is not None:
                # 仅显式声明预算的调用才携带该键；未声明 = 全局语义（如 transition 单请求路径）
                kwargs["max_input_tokens"] = max_input_tokens
            response = client.complete_json(**kwargs)
            return response, attempt, retry_count
        except Exception as exc:
            last_error = exc
            if (
                not _is_transient_provider_error(exc)
                or attempt >= MAX_PROVIDER_ATTEMPTS
            ):
                raise AgentSemanticLinkerError(
                    f"agent_semantic_provider_failed:{type(exc).__name__}:{exc}"
                    f":attempts={attempt}"
                ) from exc
            retry_count += 1
    assert last_error is not None  # pragma: no cover
    raise AgentSemanticLinkerError(
        f"agent_semantic_provider_failed:{type(last_error).__name__}:{last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Tier 1: deterministic structured candidate recall (no LLM)
# ---------------------------------------------------------------------------

_SEMANTIC_LEXICON_CACHE: dict[str, Any] | None = None


def _semantic_lexicon() -> dict[str, Any]:
    """Load the single language-neutral semantic lexicon (product policy).

    Absence degrades to no lexicon entries: recall then relies on the
    asset-driven signals (fields, path segments, state machines, permission
    matrix, source locators) and the recall basis is receipted as
    ``lexicon_unavailable`` instead of failing the whole linker.
    """
    global _SEMANTIC_LEXICON_CACHE
    if _SEMANTIC_LEXICON_CACHE is not None:
        return _SEMANTIC_LEXICON_CACHE
    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = {}
    _SEMANTIC_LEXICON_CACHE = payload if isinstance(payload, dict) else {}
    return _SEMANTIC_LEXICON_CACHE


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value).lower()).strip()


def _token_variants(token: str) -> set[str]:
    """Stem variants for matching: plural/simple past/present participle."""
    token = token.lower().strip()
    if not token:
        return set()
    out = {token}
    if len(token) > 4:
        for suffix in ("ies", "es", "s", "ed", "ing", "d"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 3:
                stem = token[: -len(suffix)]
                if suffix == "ies":
                    out.add(stem + "y")
                if (
                    len(stem) >= 2
                    and not stem.endswith("ss")
                    and stem[-1] == stem[-2]
                ):
                    stem = stem[:-1]
                out.add(stem)
    return {item for item in out if len(item) >= 2}


def _contains_token(text_norm: str, token: str) -> bool:
    """Recall-biased containment: CJK and long latin tokens match as
    substrings; short latin tokens need word boundaries. Long-token substring
    matching intentionally prefers recall over precision: candidates are
    ranked and the LLM re-scores them, so a false candidate is harmless while
    a missed candidate is a lost link."""
    token = token.lower().strip()
    if not token:
        return False
    if _CJK_RE.search(token):
        return token in text_norm
    if len(token) >= 4:
        return token in text_norm
    return bool(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text_norm)
    )


def _words_from_text(text: Any) -> set[str]:
    norm = _norm_text(text)
    words: set[str] = set()
    for raw in _TOKEN_RE.findall(norm):
        words.add(raw)
        words.update(_token_variants(raw))
    return {word for word in words if len(word) >= 2}


def _lexicon_hit_tokens(
    text_norm: str,
    entries: list[tuple[str, list[str]]],
) -> set[str]:
    """Canonical (first target) tokens whose source or any target appears."""
    hits: set[str] = set()
    for source, targets in entries:
        if not source:
            continue
        if _contains_token(text_norm, source) or any(
            _contains_token(text_norm, target) for target in targets
        ):
            hits.add((targets[0] if targets else source).lower())
    return hits


def _lexicon_entries(lexicon: dict[str, Any], key: str) -> list[tuple[str, list[str]]]:
    raw = lexicon.get(key)
    if not isinstance(raw, dict):
        return []
    entries: list[tuple[str, list[str]]] = []
    for source, targets in raw.items():
        if source == "comment" or not isinstance(targets, list):
            continue
        cleaned = [_text(item) for item in targets if _text(item)]
        if cleaned:
            entries.append((_text(source), cleaned))
    return entries


def _lexicon_entity_tokens(text_norm: str, lexicon: dict[str, Any]) -> set[str]:
    tokens = _lexicon_hit_tokens(
        text_norm,
        _lexicon_entries(lexicon, "entity_token_lexicon"),
    )
    for group in _list(lexicon.get("entity_alias_groups")):
        members = [_text(item).lower() for item in group if _text(item)]
        if members and any(
            _contains_token(text_norm, member) for member in members
        ):
            tokens.add(_singular_token(members[0]))
    return tokens


def _singular_token(token: str) -> str:
    lowered = token.lower().strip()
    if not lowered or _CJK_RE.search(lowered) or len(lowered) <= 3:
        return lowered
    if lowered.endswith("ies") and len(lowered) > 4:
        return lowered[:-3] + "y"
    if lowered.endswith("ss"):
        return lowered
    if lowered.endswith("s"):
        return lowered[:-1]
    return lowered


def _lexicon_verb_tokens(text_norm: str, lexicon: dict[str, Any]) -> set[str]:
    verbs = _lexicon_hit_tokens(
        text_norm,
        _lexicon_entries(lexicon, "verb_action_lexicon"),
    )
    for marker in _list(lexicon.get("endpoint_action_markers")):
        if _contains_token(text_norm, _text(marker)):
            verbs.add(_text(marker).lower())
    return verbs


def _lexicon_state_tokens(text_norm: str, lexicon: dict[str, Any]) -> set[str]:
    states: set[str] = set()
    for token in _list(lexicon.get("state_tokens")):
        token = _text(token)
        if any(
            _contains_token(text_norm, variant)
            for variant in _token_variants(token)
        ):
            states.add(token)
    for alias, words in _dict(lexicon.get("state_aliases")).items():
        for word in _list(words):
            word = _text(word)
            if any(
                _contains_token(text_norm, variant)
                for variant in _token_variants(word)
            ):
                states.add(_text(alias).lower())
    for hint in _list(lexicon.get("state_hints")):
        hint = _text(hint)
        if any(
            _contains_token(text_norm, variant)
            for variant in _token_variants(hint)
        ):
            states.add(hint)
    return states


def _lexicon_role_tokens(text_norm: str, lexicon: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for role, words in _dict(lexicon.get("role_words")).items():
        for word in _list(words):
            if _contains_token(text_norm, _text(word)):
                roles.add(_text(role).lower())
    return roles


def _path_segments(path: Any) -> list[str]:
    segments: list[str] = []
    raw = _text(path).split("?", 1)[0].strip().strip("/")
    for part in raw.split("/"):
        part = part.strip()
        if not part:
            continue
        if (
            part.startswith(":")
            or (part.startswith("{") and part.endswith("}"))
            or part == "*"
        ):
            continue
        segments.append(part.lower())
    return segments


def _build_asset_signals(
    asset: dict[str, Any],
    lexicon: dict[str, Any],
) -> dict[str, Any]:
    table_columns: set[str] = set()
    for row in _list(asset.get("data_tables")):
        for column in _list(row.get("columns")):
            table_columns.add(_text(column).lower())
    field_names: set[str] = set()
    for row in _list(asset.get("field_dictionary")):
        field_names.add(_text(row.get("field") or row.get("field_name")).lower())
    machines_by_ref: dict[str, dict[str, Any]] = {}
    for raw in _list(asset.get("state_machines")):
        machine = _dict(raw)
        machine_ref = _text(machine.get("state_machine_id") or machine.get("id"))
        if machine_ref:
            machines_by_ref[machine_ref] = machine
    return {
        "table_columns": {value for value in table_columns if value},
        "field_names": {value for value in field_names if value},
        "machines_by_ref": machines_by_ref,
    }


def _interface_signal_map(
    interfaces: dict[str, dict[str, Any]],
    lexicon: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Precompute per-interface deterministic recall signals (no LLM)."""
    out: dict[str, dict[str, Any]] = {}
    for interface_id, row in interfaces.items():
        text_parts: list[str] = [
            _text(row.get(field))
            for field in (
                "operation_id",
                "id",
                "action",
                "intent",
                "summary",
                "title",
                "description",
                "path",
                "source_id",
            )
        ]
        text_parts.extend(_text(value) for value in _list(row.get("tags")))
        norm = _norm_text(" ".join(text_parts))
        segments = _path_segments(row.get("path"))
        segment_variants: set[str] = set()
        for segment in segments:
            segment_variants.add(segment)
            segment_variants.update(_token_variants(segment))
        operation_variants: set[str] = set()
        for token in _TOKEN_RE.findall(norm):
            operation_variants.update(_token_variants(token))
        words = _words_from_text(norm)
        fields: set[str] = set()
        for value in _list(row.get("field_dictionary")):
            fields.add(_text(value).lower())
        fields.update(
            _text(name)
            for name in _schema_field_names(
                row.get("request_schema") or row.get("requestBody")
            )
        )
        fields.update(
            _text(name)
            for name in _schema_field_names(
                row.get("response_schema") or row.get("responses")
            )
        )
        for value in _list(row.get("parameters")):
            if isinstance(value, dict):
                fields.add(_text(value.get("name")).lower())
            else:
                fields.add(_text(value).lower())
        out[interface_id] = {
            "norm": norm,
            "entity_tokens": set(_lexicon_entity_tokens(norm, lexicon))
            | segment_variants
            | {_singular_token(variant) for variant in operation_variants},
            "verbs": set(_lexicon_verb_tokens(norm, lexicon)),
            "states": set(_lexicon_state_tokens(norm, lexicon)),
            "fields": {value for value in fields if value},
            "words": {word for word in words if len(word) >= 3},
            "segments": set(segments),
            "source_id": _text(row.get("source_id")),
        }
    return out


def _rule_context(
    row: dict[str, Any],
    signals: dict[str, Any],
    lexicon: dict[str, Any],
) -> dict[str, Any]:
    frame = _dict(row.get("semantic_frame"))
    causal = _dict(row.get("causal_chain"))
    text = " ".join([
        _text(frame.get("condition")),
        _text(frame.get("subject")),
        _text(frame.get("behavior")),
        _text(row.get("statement")),
        _text(row.get("kind") or row.get("rule_type") or row.get("risk_type")),
        _text(causal.get("trigger_action")),
    ])
    norm = _norm_text(text)
    words = _words_from_text(norm)
    field_tokens: set[str] = set()
    for column in signals["table_columns"]:
        if _contains_token(norm, column):
            field_tokens.add(column)
    for name in signals["field_names"]:
        if _contains_token(norm, name):
            field_tokens.add(name)
    return {
        "norm": norm,
        "words": words,
        "lexicon_entities": set(_lexicon_entity_tokens(norm, lexicon)),
        "verb_tokens": set(_lexicon_verb_tokens(norm, lexicon)),
        "state_tokens": set(_lexicon_state_tokens(norm, lexicon)),
        "role_tokens": set(_lexicon_role_tokens(norm, lexicon)),
        "field_tokens": {token for token in field_tokens if token},
        "path_tokens": {word for word in words if len(word) >= 4},
        "source_id": _text(row.get("source_id")),
        "source_anchors": {
            _text(anchor)
            for anchor in _list(frame.get("source_anchors"))
            if _text(anchor)
        },
    }


def _permission_resource_action_tokens(
    ctx: dict[str, Any],
    asset: dict[str, Any],
) -> tuple[set[str], set[str]]:
    resource_tokens: set[str] = set()
    action_tokens: set[str] = set()
    if not ctx["role_tokens"]:
        return resource_tokens, action_tokens
    for raw in _list(asset.get("permission_matrix")):
        row = _dict(raw)
        role_value = _norm_text(row.get("role") or row.get("role_name"))
        if not any(_contains_token(role_value, role) for role in ctx["role_tokens"]):
            continue
        resource = _text(row.get("resource"))
        for token in _TOKEN_RE.findall(_norm_text(resource)):
            resource_tokens.add(token)
            resource_tokens.update(_token_variants(token))
        for action in [
            *_list(row.get("actions")),
            *_list(row.get("denied_actions")),
        ]:
            action_tokens.update(_words_from_text(action))
    return resource_tokens, action_tokens


def _recall_candidate_interfaces(
    ctx: dict[str, Any],
    interface_signals: dict[str, dict[str, Any]],
    asset: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Tier 1: deterministic multi-signal candidate recall for one rule.

    Returns ``(ranked interface_ids, stats)``. Ranking is purely deterministic:
    weighted signal channels; ties break on interface identity. High recall is
    preferred: false candidates are re-scored by the model, missed candidates
    would be lost links.
    """
    scores: dict[str, int] = {}
    channels: dict[str, set[str]] = {}

    def hit(interface_id: str, channel: str, weight: int) -> None:
        scores[interface_id] = scores.get(interface_id, 0) + weight
        channels.setdefault(interface_id, set()).add(channel)

    rule_entities = ctx["lexicon_entities"] | ctx["path_tokens"]
    for interface_id, sig in interface_signals.items():
        if rule_entities & sig["entity_tokens"]:
            hit(interface_id, "entity_token", 3)
        if ctx["field_tokens"] & sig["fields"]:
            hit(interface_id, "schema_field_overlap", 2)
        if ctx["state_tokens"] & sig["states"]:
            hit(interface_id, "state_token", 2)
        if ctx["verb_tokens"] & sig["verbs"]:
            hit(interface_id, "operation_verb", 2)
        if (
            ctx["source_id"]
            and sig["source_id"]
            and ctx["source_id"] == sig["source_id"]
        ):
            hit(interface_id, "source_id_co_location", 3)
        if ctx["words"] & sig["words"]:
            hit(interface_id, "lexical", 1)

    resource_tokens, action_tokens = _permission_resource_action_tokens(ctx, asset)
    if resource_tokens or action_tokens:
        for interface_id, sig in interface_signals.items():
            if (resource_tokens & sig["entity_tokens"]) or (
                action_tokens & sig["verbs"]
            ):
                hit(interface_id, "permission_matrix", 2)

    relation_endpoint_tokens: set[str] = set()
    for raw in _list(asset.get("entity_relations")):
        row = _dict(raw)
        endpoints = " ".join([
            _text(row.get("from_entity")),
            _text(row.get("to_entity")),
        ])
        endpoint_tokens: set[str] = set()
        for token in _TOKEN_RE.findall(_norm_text(endpoints)):
            endpoint_tokens.add(token)
            endpoint_tokens.update(_token_variants(token))
        if rule_entities & endpoint_tokens:
            relation_endpoint_tokens.update(endpoint_tokens)
    if relation_endpoint_tokens:
        for interface_id, sig in interface_signals.items():
            if sig["entity_tokens"] & relation_endpoint_tokens:
                hit(interface_id, "entity_relation", 1)

    ranked = sorted(
        scores,
        key=lambda interface_id: (-scores[interface_id], interface_id),
    )
    fallback_used = False
    if len(ranked) < MIN_CANDIDATES_PER_RULE:
        # Deterministic fallback: top lexical-overlap interfaces broaden the
        # shortlist so a rule with thin signal coverage still reaches the
        # model with candidates instead of a truncated set.
        remaining = [
            interface_id
            for interface_id in sorted(interface_signals)
            if interface_id not in scores
        ]
        remaining.sort(
            key=lambda interface_id: (
                -len(ctx["words"] & interface_signals[interface_id]["words"]),
                interface_id,
            )
        )
        for interface_id in remaining:
            if len(ranked) >= MAX_CANDIDATES_PER_RULE:
                break
            scores[interface_id] = 1
            channels.setdefault(interface_id, set()).add("global_fallback")
            ranked.append(interface_id)
            fallback_used = True
    ranked = ranked[:MAX_CANDIDATES_PER_RULE]
    return ranked, {
        "channels": {
            interface_id: sorted(channel_names)
            for interface_id, channel_names in channels.items()
            if interface_id in ranked
        },
        "fallback": fallback_used,
    }


def _fact_recall_score(fact: dict[str, Any], ctx: dict[str, Any]) -> int:
    text = " ".join(
        str(value)
        for key, value in fact.items()
        if key not in ("fact_id", "fact_kind") and value not in (None, "", [])
    )
    norm = _norm_text(text)
    if not norm:
        return 0
    score = 0
    for token in (
        ctx["field_tokens"] | ctx["state_tokens"]
        | ctx["role_tokens"] | ctx["lexicon_entities"]
    ):
        if token and _contains_token(norm, token):
            score += 3
    for token in ctx["path_tokens"]:
        if len(token) >= 4 and _contains_token(norm, token):
            score += 2
    for word in ctx["words"]:
        if len(word) >= 4 and _contains_token(norm, word):
            score += 1
    return score


def _recall_supporting_facts(
    ctx: dict[str, Any],
    fact_pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tier 1: per-rule supporting fact recall (replaces global truncation)."""
    forced = [
        fact
        for fact in fact_pool
        if fact["fact_id"] in ctx["source_anchors"]
    ]
    scored = [
        (score, fact)
        for fact in fact_pool
        if fact["fact_id"] not in ctx["source_anchors"]
        for score in (_fact_recall_score(fact, ctx),)
        if score > 0
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["fact_id"]))
    selected = [*forced, *(fact for _, fact in scored)]
    return selected[:MAX_SUPPORTING_FACTS_PER_RULE]


def _rule_unit(
    row: dict[str, Any],
    interface_signals: dict[str, dict[str, Any]],
    interfaces: dict[str, dict[str, Any]],
    asset: dict[str, Any],
    signals: dict[str, Any],
    fact_pool: list[dict[str, Any]],
    lexicon: dict[str, Any],
) -> dict[str, Any]:
    ctx = _rule_context(row, signals, lexicon)
    candidate_ids, recall_stats = _recall_candidate_interfaces(
        ctx,
        interface_signals,
        asset,
    )
    fact_rows = _recall_supporting_facts(ctx, fact_pool)
    candidate_rows = [
        _candidate_interface_prompt_row(interfaces[interface_id])
        for interface_id in candidate_ids
    ]
    fact_ids = {fact["fact_id"] for fact in fact_rows}
    rule_prompt_row = _rule_prompt_row(row)
    return {
        "rule_id": _text(row.get("rule_id")),
        "rule_prompt_row": rule_prompt_row,
        "fingerprint": _fingerprint(rule_prompt_row),
        "candidate_ids": candidate_ids,
        "candidate_rows": candidate_rows,
        "fact_rows": fact_rows,
        "fact_ids": fact_ids,
        "candidate_count": len(candidate_ids),
        "recall_channels": recall_stats["channels"],
        "recall_fallback": recall_stats["fallback"],
        "valid_refs": {
            ref
            for ref in {
                _text(row.get("rule_id")),
                *candidate_ids,
                *fact_ids,
            }
            if ref
        },
    }


def _transition_unit(
    transitions: list[dict[str, Any]],
    machines_by_ref: dict[str, dict[str, Any]],
    interface_signals: dict[str, dict[str, Any]],
    interfaces: dict[str, dict[str, Any]],
    asset: dict[str, Any],
    fact_pool: list[dict[str, Any]],
    lexicon: dict[str, Any],
) -> dict[str, Any]:
    """One dedicated unit covering every source transition exactly once."""
    budget_skipped_count = max(0, len(transitions) - MAX_TRANSITIONS_PER_REQUEST)
    transitions = transitions[:MAX_TRANSITIONS_PER_REQUEST]
    units: list[dict[str, Any]] = []
    for transition in transitions:
        transition_id = _text(transition.get("transition_id"))
        machine_ref = _text(transition.get("machine_id"))
        machine = machines_by_ref.get(machine_ref, {})
        entity = _text(
            machine.get("object")
            or transition.get("entity")
        )
        entity_variants: set[str] = set()
        for token in _TOKEN_RE.findall(_norm_text(entity)):
            entity_variants.add(token)
            entity_variants.update(_token_variants(token))
        state_variants: set[str] = set()
        for state in (
            _text(transition.get("from_state")),
            _text(transition.get("to_state")),
        ):
            for token in _TOKEN_RE.findall(_norm_text(state)):
                state_variants.add(token)
                state_variants.update(_token_variants(token))

        scores: dict[str, int] = {}
        channels: dict[str, set[str]] = {}

        def hit(interface_id: str, channel: str, weight: int) -> None:
            scores[interface_id] = scores.get(interface_id, 0) + weight
            channels.setdefault(interface_id, set()).add(channel)

        for interface_id, sig in interface_signals.items():
            if entity_variants & sig["entity_tokens"]:
                hit(interface_id, "entity_token", 3)
            if any(
                _contains_token(sig["norm"], token)
                for token in state_variants
                if len(token) >= 3
            ):
                hit(interface_id, "state_token", 2)
        ranked = sorted(
            scores,
            key=lambda interface_id: (-scores[interface_id], interface_id),
        )
        if len(ranked) < MIN_CANDIDATES_PER_RULE:
            remaining = [
                interface_id
                for interface_id in sorted(interface_signals)
                if interface_id not in scores
            ]
            remaining.sort(
                key=lambda interface_id: (
                    -len(
                        interface_signals[interface_id]["words"]
                        & (entity_variants | state_variants)
                    ),
                    interface_id,
                )
            )
            for interface_id in remaining:
                if len(ranked) >= MAX_CANDIDATES_PER_RULE:
                    break
                scores[interface_id] = 1
                channels.setdefault(interface_id, set()).add("global_fallback")
                ranked.append(interface_id)
        candidate_ids = ranked[:MAX_CANDIDATES_PER_RULE]

        ctx = {
            "field_tokens": set(),
            "state_tokens": state_variants,
            "role_tokens": set(),
            "lexicon_entities": entity_variants,
            "path_tokens": entity_variants | state_variants,
            "words": entity_variants | state_variants,
            "source_anchors": set(),
        }
        machine_fact_id = machine_ref
        fact_rows = [
            fact
            for fact in fact_pool
            if fact["fact_id"] == machine_fact_id
        ]
        fact_rows.extend(
            fact
            for fact in _recall_supporting_facts(ctx, fact_pool)
            if fact["fact_id"] != machine_fact_id
        )
        fact_rows = fact_rows[:MAX_SUPPORTING_FACTS_PER_RULE]
        fact_ids = {fact["fact_id"] for fact in fact_rows}
        units.append({
            "transition_id": transition_id,
            "transition_row": dict(transition),
            "candidate_ids": candidate_ids,
            "candidate_count": len(candidate_ids),
            "candidate_rows": [
                _candidate_interface_prompt_row(interfaces[interface_id])
                for interface_id in candidate_ids
            ],
            "fact_rows": fact_rows,
            "fact_ids": fact_ids,
            "valid_refs": {transition_id, *candidate_ids, *fact_ids},
            "recall_channels": {
                interface_id: sorted(channel_names)
                for interface_id, channel_names in channels.items()
                if interface_id in candidate_ids
            },
        })
    return {
        "units": units,
        "transition_rows": [dict(unit["transition_row"]) for unit in units],
        "candidate_rows": sorted(
            {
                _fingerprint(row)
                for unit in units
                for row in unit["candidate_rows"]
            }
        ),
        "fact_rows": sorted(
            {
                _fingerprint(row)
                for unit in units
                for row in unit["fact_rows"]
            }
        ),
        "fingerprint": _fingerprint(
            [_text(unit["transition_id"]) for unit in units]
        ),
        "budget_skipped_count": budget_skipped_count,
    }


# ---------------------------------------------------------------------------
# Tier 2: candidate-scoped LLM prompts
# ---------------------------------------------------------------------------

_RULE_RESPONSE_CONTRACT = {
    "assessments": [{
        "rule_id": "exact supplied rule_id",
        "disposition": "LINKED|NO_EXECUTABLE_INTERFACE|AMBIGUOUS",
        "reason": "brief business and test rationale",
        "relationships": [{
            "interface_id": "exact supplied interface_id from this rule's candidate_interfaces",
            "confidence": 0.0,
            "reason": "how this interface exercises or observes the rule",
            "evidence_refs": [
                "rule_id",
                "interface_id",
                "optional exact fact_id",
            ],
        }],
    }],
}

_TRANSITION_RESPONSE_CONTRACT = {
    "transition_assessments": [{
        "transition_id": "exact supplied transition_id",
        "disposition": "LINKED|NO_EXECUTABLE_INTERFACE|AMBIGUOUS",
        "reason": "brief business and test rationale",
        "relationships": [{
            "interface_id": "exact supplied interface_id from this transition's candidate_interfaces",
            "confidence": 0.0,
            "reason": "how this interface performs the state transition "
            "or would violate the forbidden transition",
            "evidence_refs": [
                "transition_id",
                "interface_id",
                "optional exact fact_id",
            ],
        }],
    }],
}


def _build_rule_request_prompt(units: list[dict[str, Any]]) -> str:
    rules = [{
        "rule": unit["rule_prompt_row"],
        "candidate_interfaces": unit["candidate_rows"],
        "supporting_facts": unit["fact_rows"],
    } for unit in units]
    packet = {
        "protocol": PROMPT_PROTOCOL,
        "assessment_mode": "rule_to_interface",
        "business_semantic_model": {
            "rules_to_assess": rules,
        },
    }
    safe_packet, _ = redact_and_validate(packet)
    prompt = (
        "Act as both an enterprise product expert and a senior test expert. "
        "Understand the supplied business semantic model before linking anything. "
        "For every rule in rules_to_assess, decide whether a documented interface "
        "can exercise or observe it. Every rule carries its own "
        "candidate_interfaces: the deterministic, source-grounded shortlist of "
        "documented interfaces that plausibly exercise or observe that rule, "
        "plus supporting_facts that describe the entities, fields, states, "
        "roles and permissions the rule touches. Consider actors, permissions, "
        "entities, fields, state transitions, preconditions, postconditions, "
        "negative paths, and business outcomes. This creates bounded "
        "experiment intent, never a bug finding or a new business fact.\n\n"
        "Link ONLY interfaces listed in that rule's candidate_interfaces. If "
        "the interface that exercises or observes the rule is not among the "
        "candidates, prefer AMBIGUOUS with a clear reason; never invent an "
        "interface and never link an interface that is not listed. Use only "
        "exact identifiers and facts supplied in the model. Never invent "
        "endpoints, fields, actors, states, credentials, request bodies, rules, "
        "or observations. LINKED requires one to four relationships and "
        "evidence_refs must contain the exact rule_id, the interface_id, and "
        "any supporting fact_ids you relied on. Use NO_EXECUTABLE_INTERFACE "
        "when the source has no callable surface. Use AMBIGUOUS when several "
        "interpretations remain plausible. The disposition and relationships "
        "fields are one atomic contract: LINKED requires at least one "
        "relationship; NO_EXECUTABLE_INTERFACE and AMBIGUOUS require "
        "relationships to be exactly an empty array. Never put a candidate "
        "interface in relationships for an unlinked disposition. Omit links "
        f"below confidence {MIN_CONFIDENCE}. Return exactly one assessment for "
        "every supplied rule, and JSON only, with this exact shape:\n"
        + json.dumps(
            _RULE_RESPONSE_CONTRACT,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n\nINPUT:\n"
        + json.dumps(safe_packet, ensure_ascii=False, separators=(",", ":"))
        + "\n\nFINAL CONTRACT CHECK: for every assessment, if disposition is "
        "not LINKED, emit `relationships: []`; if relationships is non-empty, "
        "emit `disposition: LINKED`."
    )
    return prompt


def _build_transition_request_prompt(unit: dict[str, Any]) -> str:
    transitions = [{
        "transition": dict(tunit["transition_row"]),
        "candidate_interfaces": tunit["candidate_rows"],
        "supporting_facts": tunit["fact_rows"],
    } for tunit in unit["units"]]
    packet = {
        "protocol": PROMPT_PROTOCOL,
        "assessment_mode": "state_transition_to_interface",
        "business_semantic_model": {
            "state_transitions_to_assess": transitions,
        },
    }
    safe_packet, _ = redact_and_validate(packet)
    prompt = (
        "Act as both an enterprise product expert and a senior test expert. "
        "Understand the supplied business semantic model before linking anything. "
        "For every state transition in state_transitions_to_assess, link the "
        "documented interface that performs that transition (allowed) or the "
        "interface that would attempt the forbidden move (forbidden transitions: "
        "the operation that changes the entity's state, e.g. the payment or "
        "status-change operation for a payment/status machine). Every transition "
        "carries its own candidate_interfaces: the deterministic, source-grounded "
        "shortlist of documented interfaces that plausibly perform or attempt "
        "the transition, plus supporting_facts. A transition without any "
        "candidate that can perform or attempt it is NO_EXECUTABLE_INTERFACE.\n\n"
        "Link ONLY interfaces listed in that transition's candidate_interfaces. "
        "If the interface that performs or would attempt the transition is not "
        "among the candidates, prefer AMBIGUOUS with a clear reason; never "
        "invent an interface and never link an interface that is not listed. "
        "Use only exact identifiers and facts supplied in the model. Never "
        "invent endpoints, fields, actors, states, credentials, request bodies, "
        "rules, or observations. LINKED requires one to four relationships and "
        "evidence_refs must contain the exact transition_id, the interface_id, "
        "and any supporting fact_ids you relied on. Use "
        "NO_EXECUTABLE_INTERFACE when the source has no callable surface. Use "
        "AMBIGUOUS when several interpretations remain plausible. The "
        "disposition and relationships fields are one atomic contract: LINKED "
        "requires at least one relationship; NO_EXECUTABLE_INTERFACE and "
        "AMBIGUOUS require relationships to be exactly an empty array. Never "
        "put a candidate interface in relationships for an unlinked "
        f"disposition. Omit links below confidence {MIN_CONFIDENCE}. Return "
        "exactly one transition assessment for every supplied state "
        "transition, and JSON only, with this exact shape:\n"
        + json.dumps(
            _TRANSITION_RESPONSE_CONTRACT,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n\nINPUT:\n"
        + json.dumps(safe_packet, ensure_ascii=False, separators=(",", ":"))
        + "\n\nFINAL CONTRACT CHECK: for every transition assessment, if "
        "disposition is not LINKED, emit `relationships: []`; if relationships "
        "is non-empty, emit `disposition: LINKED`."
    )
    return prompt


def _validate_rule_response_shape(response: Any) -> None:
    if (
        not isinstance(response, dict)
        or not set(response) <= {"assessments", "transition_assessments"}
        or "assessments" not in response
    ):
        raise AgentSemanticLinkerError("agent_semantic_rule_response_schema_invalid")
    if not isinstance(response.get("assessments"), list):
        raise AgentSemanticLinkerError("agent_semantic_assessments_not_list")
    if response.get("transition_assessments") is not None and not isinstance(
        response.get("transition_assessments"), list
    ):
        raise AgentSemanticLinkerError(
            "agent_semantic_transition_assessments_not_list"
        )


def _validate_transition_response_shape(response: Any) -> None:
    if (
        not isinstance(response, dict)
        or not set(response) <= {"assessments", "transition_assessments"}
        or "transition_assessments" not in response
    ):
        raise AgentSemanticLinkerError(
            "agent_semantic_transition_response_schema_invalid"
        )
    if not isinstance(response.get("transition_assessments"), list):
        raise AgentSemanticLinkerError(
            "agent_semantic_transition_assessments_not_list"
        )
    if response.get("assessments") is not None and not isinstance(
        response.get("assessments"), list
    ):
        raise AgentSemanticLinkerError("agent_semantic_assessments_not_list")


# ---------------------------------------------------------------------------
# Content-addressed cache (raw model output, always re-validated by Tier 3)
# ---------------------------------------------------------------------------


def _model_config_fingerprint(client: AgentJsonClient) -> str:
    fields: dict[str, Any] = {
        "protocol": PROMPT_PROTOCOL,
        "prompt_template": "candidate_scoped_v1",
    }
    config = getattr(client, "config", None)
    if config is not None:
        for key in (
            "model",
            "base_url",
            "temperature",
            "max_tokens",
            "timeout_seconds",
        ):
            value = getattr(config, key, None)
            if value is not None:
                fields[key] = value
    return _fingerprint(fields)


def _unit_cache_key(
    unit_fingerprint: str,
    candidate_fingerprints: list[str],
    fact_fingerprints: list[str],
    model_fingerprint: str,
) -> str:
    payload = {
        "unit_fingerprint": unit_fingerprint,
        "candidate_interface_fingerprints": sorted(candidate_fingerprints),
        "supporting_fact_fingerprints": sorted(fact_fingerprints),
        "model_config_fingerprint": model_fingerprint,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return digest


class _SemanticLinkCache:
    """Content-addressed cache storing raw model responses.

    In-memory by default; a directory from ``QUALIBUG_SEMANTIC_CACHE_DIR``
    adds cross-run persistence. Entries always pass Tier 3 deterministic
    validation again on read, so a corrupt entry degrades to a miss (recompute)
    and can never bypass the contract.
    """

    def __init__(self, directory: str | None = None) -> None:
        self._memory: dict[str, dict[str, Any]] = {}
        self._directory = directory
        self._persistence_failures = 0

    def get(self, key: str) -> dict[str, Any] | None:
        if key in self._memory:
            return self._memory[key]
        if self._directory:
            try:
                payload = json.loads(
                    Path(self._directory, key + ".json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
            if isinstance(payload, dict) and isinstance(
                payload.get("response"), dict
            ):
                self._memory[key] = payload["response"]
                return payload["response"]
        return None

    def put(self, key: str, response: dict[str, Any]) -> None:
        self._memory[key] = response
        if not self._directory:
            return
        try:
            Path(self._directory).mkdir(parents=True, exist_ok=True)
            Path(self._directory, key + ".json").write_text(
                json.dumps(
                    {"response": response},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError:
            # The cache is an accelerator. A write failure must not break
            # linking; it is receipted so operators can see it.
            self._persistence_failures += 1

    @property
    def persistence_failures(self) -> int:
        return self._persistence_failures


# ---------------------------------------------------------------------------
# Mainline: recall -> cache -> LLM -> contract validation -> receipt
# ---------------------------------------------------------------------------


def enrich_knowledge_asset_with_agent_relationships(
    knowledge_asset: dict[str, Any],
    *,
    client: AgentJsonClient | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add validated rule-to-interface intent edges and a complete audit receipt.

    Three-tier incremental linking (structured recall -> candidate-scoped LLM
    -> deterministic contract validation) with content-addressed caching and
    per-batch independent success/failure. See the module docstring.
    """

    if not isinstance(knowledge_asset, dict):
        raise AgentSemanticLinkerError("knowledge_asset_not_object")
    linker_input = {
        key: list(_list(knowledge_asset.get(key)))
        for key in LINKER_INPUT_COLLECTIONS
        if _list(knowledge_asset.get(key))
    }
    private_paths = find_evaluator_private_context_paths(linker_input)
    if private_paths:
        raise AgentSemanticLinkerError(
            "evaluator_private_context_forbidden:" + ",".join(private_paths)
        )
    rules = _index_exact_ids(
        knowledge_asset.get("rule_library"),
        id_key="rule_id",
        collection="rule_library",
    )
    interfaces = _index_exact_ids(
        knowledge_asset.get("interfaces"),
        id_key="interface_id",
        collection="interfaces",
    )
    transitions = _asset_transition_rows(knowledge_asset)
    if not rules or not interfaces:
        raise AgentSemanticLinkerError("agent_semantic_inputs_empty")
    if transitions:
        transition_ids = {
            _text(row.get("transition_id"))
            for row in transitions
            if _text(row.get("transition_id"))
        }
        if len(transition_ids) != len(transitions):
            raise AgentSemanticLinkerError(
                "agent_semantic_duplicate_transition_identity"
            )

    resolved_client = client or _default_client()
    lexicon = _semantic_lexicon()
    recall_basis = "semantic_lexicon" if lexicon else "lexicon_unavailable"
    cache = _SemanticLinkCache(
        directory=os.environ.get(CACHE_DIRECTORY_ENV) or None
    )
    model_fingerprint = _model_config_fingerprint(resolved_client)
    signals = _build_asset_signals(knowledge_asset, lexicon)
    interface_signals = _interface_signal_map(interfaces, lexicon)
    fact_pool = _all_fact_rows(knowledge_asset)

    rule_rows = list(rules.values())
    max_rules = MAX_RULES_PER_REQUEST * MAX_PROVIDER_REQUESTS
    scheduled = rule_rows[:max_rules]
    budget_skipped_rule_ids = [
        _text(row.get("rule_id")) for row in rule_rows[max_rules:]
    ]

    # --- Tier 1: per-rule structured candidate + fact recall ---
    units: list[dict[str, Any]] = []
    recall_candidate_total = 0
    recall_candidate_min: int | None = None
    recall_candidate_max = 0
    recall_fallback_count = 0
    recall_empty_count = 0
    for row in scheduled:
        unit = _rule_unit(
            row,
            interface_signals,
            interfaces,
            knowledge_asset,
            signals,
            fact_pool,
            lexicon,
        )
        units.append(unit)
        recall_candidate_total += unit["candidate_count"]
        recall_candidate_min = (
            unit["candidate_count"]
            if recall_candidate_min is None
            else min(recall_candidate_min, unit["candidate_count"])
        )
        recall_candidate_max = max(recall_candidate_max, unit["candidate_count"])
        if unit["recall_fallback"]:
            recall_fallback_count += 1
        if unit["candidate_count"] == 0:
            recall_empty_count += 1

    # --- content-addressed cache lookup per rule unit ---
    cache_hit_count = 0
    cache_miss_count = 0
    responses_by_rule: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    for unit in units:
        key = _unit_cache_key(
            unit["fingerprint"],
            [_fingerprint(row) for row in unit["candidate_rows"]],
            [_fingerprint(row) for row in unit["fact_rows"]],
            model_fingerprint,
        )
        unit["_cache_key"] = key
        cached = cache.get(key)
        if cached is not None:
            responses_by_rule[unit["rule_id"]] = cached
            cache_hit_count += 1
        else:
            pending.append(unit)
            cache_miss_count += 1

    # --- Tier 2: LLM for pending units, batched and independently failing ---
    failed_units: list[dict[str, Any]] = []
    provider_attempt_count = 0
    provider_retry_count = 0
    batch_assessments_ordered: list[dict[str, Any]] = []
    batches = [
        pending[index:index + MAX_RULES_PER_REQUEST]
        for index in range(0, len(pending), MAX_RULES_PER_REQUEST)
    ]

    # ── 输入预算预检：超限批次逐级二分，保持单规则上下文完整 ──
    # 缩到单规则仍超限的 unit 显式 BLOCKED（具名 reason code），绝不静默
    # 发送巨型请求、绝不静默丢弃。全部缩减事件计数入回执。
    input_budget = _linker_input_budget()
    sized_batches: list[list[dict[str, Any]]] = []
    input_budget_exhausted_rule_ids: list[str] = []
    batches_split_count = 0
    for batch in batches:
        stack: list[list[dict[str, Any]]] = [list(batch)]
        while stack:
            current = stack.pop()
            estimated = estimate_input_tokens(
                _build_rule_request_prompt(current)
            )
            if estimated <= input_budget:
                sized_batches.append(current)
                continue
            if len(current) <= 1:
                input_budget_exhausted_rule_ids.extend(
                    unit["rule_id"] for unit in current
                )
                continue
            mid = max(1, len(current) // 2)
            batches_split_count += 1
            stack.extend([current[:mid], current[mid:]])
    # Tier-2 batches run concurrently (bounded): provider calls are the
    # dominant LLM-phase latency (measured 36-217s each; 8 serial batches ≈
    # 30+ minutes). A small worker pool keeps the phase near the longest
    # single batch while staying under provider rate limits. Each batch
    # still fails independently and the ordered aggregation below preserves
    # the original rule order.
    _LINKER_BATCH_WORKERS = 4
    _batch_results: dict[int, dict[str, Any]] = {}

    def _run_batch(index: int, batch: list[dict[str, Any]]) -> None:
        prompt = _build_rule_request_prompt(batch)
        try:
            response, attempts, retries = _complete_batch(
                resolved_client,
                prompt=prompt,
                max_input_tokens=input_budget,
            )
        except AgentSemanticLinkerError as exc:
            _batch_results[index] = {
                "failed": {
                    "unit_kind": "rule_batch",
                    "reason_code": "provider_failure",
                    "error": str(exc)[:300],
                    "rule_ids": [unit["rule_id"] for unit in batch],
                },
            }
            return
        try:
            _validate_rule_response_shape(response)
        except AgentSemanticLinkerError as exc:
            _batch_results[index] = {
                "failed": {
                    "unit_kind": "rule_batch",
                    "reason_code": "response_schema_invalid",
                    "error": str(exc)[:300],
                    "rule_ids": [unit["rule_id"] for unit in batch],
                },
            }
            return
        _batch_results[index] = {
            "ok": {
                "attempts": attempts,
                "retries": retries,
                "response": response,
                "unit_ids": [unit["rule_id"] for unit in batch],
            },
        }

    if input_budget_exhausted_rule_ids:
        failed_units.append(
            {
                "unit_kind": "rule_unit",
                "reason_code": "llm_input_budget_exhausted",
                "error": (
                    f"single-rule prompt exceeds linker input budget "
                    f"({input_budget} tokens); unit blocked visibly, never sent"
                ),
                "rule_ids": list(input_budget_exhausted_rule_ids),
            }
        )

    if len(sized_batches) > 1:
        with ThreadPoolExecutor(max_workers=_LINKER_BATCH_WORKERS) as _pool:
            list(_pool.map(lambda b: _run_batch(*b), enumerate(sized_batches)))
    else:
        for _i, _b in enumerate(sized_batches):
            _run_batch(_i, _b)

    for index in range(len(sized_batches)):
        result = _batch_results.get(index)
        if result is None:
            continue
        if "failed" in result:
            failed_units.append(result["failed"])
            continue
        ok = result["ok"]
        provider_attempt_count += ok["attempts"]
        provider_retry_count += ok["retries"]
        response = ok["response"]
        batch_units_by_rule = {unit["rule_id"]: unit for unit in sized_batches[index]}
        for assessment in response["assessments"]:
            batch_assessments_ordered.append(assessment)
            rule_id = _text(assessment.get("rule_id"))
            unit = batch_units_by_rule.get(rule_id)
            if unit is not None:
                cache.put(unit["_cache_key"], assessment)
                responses_by_rule[rule_id] = assessment

    # --- state transitions: one dedicated unit, sent once, never per batch ---
    transition_unit: dict[str, Any] | None = None
    transition_response: dict[str, Any] | None = None
    transition_cache_hit = False
    if transitions:
        transition_unit = _transition_unit(
            transitions,
            signals["machines_by_ref"],
            interface_signals,
            interfaces,
            knowledge_asset,
            fact_pool,
            lexicon,
        )
        transition_key = _unit_cache_key(
            transition_unit["fingerprint"],
            transition_unit["candidate_rows"],
            transition_unit["fact_rows"],
            model_fingerprint,
        )
        cached_transition = cache.get(transition_key)
        if cached_transition is not None:
            transition_response = cached_transition
            transition_cache_hit = True
            cache_hit_count += 1
        else:
            cache_miss_count += 1
            # Transition 单元按设计是单次大请求（≤200 条迁移、每扫描一次、
            # 有独立分页权威契约）：保持全局预算语义，不套用规则批预算。
            prompt = _build_transition_request_prompt(transition_unit)
            try:
                response, attempts, retries = _complete_batch(
                    resolved_client,
                    prompt=prompt,
                )
            except AgentSemanticLinkerError as exc:
                failed_units.append({
                    "unit_kind": "transition",
                    "reason_code": "provider_failure",
                    "error": str(exc)[:300],
                })
            else:
                provider_attempt_count += attempts
                provider_retry_count += retries
                try:
                    _validate_transition_response_shape(response)
                except AgentSemanticLinkerError as exc:
                    failed_units.append({
                        "unit_kind": "transition",
                        "reason_code": "response_schema_invalid",
                        "error": str(exc)[:300],
                    })
                else:
                    cache.put(transition_key, response)
                    transition_response = response

    # --- granular degradation: raise only when every unit failed ---
    if (
        failed_units
        and not responses_by_rule
        and (not transitions or transition_response is None)
    ):
        raise AgentSemanticLinkerError(
            "agent_semantic_all_units_failed:"
            + "|".join(
                f"{unit_row['reason_code']}:{unit_row['error']}"
                for unit_row in failed_units
            )[:800]
            + f":units_failed={len(failed_units)}"
        )

    # --- Tier 3: deterministic contract validation of every assessment ---
    existing = {
        (
            _text(row.get("relation") or row.get("relation_type")),
            _text(row.get("from")),
            _text(row.get("to")),
        )
        for row in _list(knowledge_asset.get("relationships"))
        if isinstance(row, dict)
        and _text(row.get("status") or "accepted").lower() == "accepted"
    }
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    rule_assessments: list[dict[str, Any]] = []
    transition_assessments: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    seen_assessments: set[str] = set()
    seen_transition_assessments: set[str] = set()
    per_subject: dict[str, int] = {}
    rejected_low_confidence = 0
    rejected_invalid_identity = 0
    rejected_non_candidate = 0
    rejected_invalid_evidence = 0
    rejected_duplicates = 0
    rejected_rule_limit = 0
    rejected_inconsistent_disposition = 0
    existing_count = 0
    proposal_count = 0

    unit_by_rule_id = {unit["rule_id"]: unit for unit in units}

    def reject(
        assessment_index: int,
        relationship_index: int,
        raw: dict[str, Any],
        reason_code: str,
    ) -> None:
        rejections.append({
            "assessment_index": assessment_index,
            "relationship_index": relationship_index,
            "reason_code": reason_code,
            "proposal_fingerprint": _fingerprint(raw),
        })

    def accept_relationships(
        *,
        subject_id: str,
        subject_kind: str,
        disposition: str,
        reason: str,
        relationships: list[Any],
        assessment_index: int,
        relation: str,
        raw_assessment: dict[str, Any],
        expected_ids: set[str],
        allowed_interface_ids: set[str],
        valid_refs: set[str],
    ) -> tuple[int, bool]:
        """Validate one assessment's relationships under the shared contract.

        Returns ``(accepted_count, handled)``. ``handled=False`` means the
        assessment was identity-invalid, a duplicate, or disposition-
        inconsistent, so the caller must not count it toward the
        provider-completeness check. Single-assessment defects (unknown
        identity, duplicates, disposition/relationships mismatch) are receipted
        as rejections; only structural schema violations (invalid disposition
        token, missing reason, relationships not a list, wrong relationship
        field set, invalid confidence/evidence shape) raise, because those mean
        the provider output contract itself is untrustworthy.
        """
        nonlocal proposal_count, rejected_low_confidence
        nonlocal rejected_invalid_identity, rejected_non_candidate
        nonlocal rejected_invalid_evidence
        nonlocal rejected_duplicates, rejected_rule_limit, existing_count
        nonlocal rejected_inconsistent_disposition
        if disposition not in _DISPOSITIONS:
            raise AgentSemanticLinkerError(
                f"agent_semantic_disposition_invalid:{assessment_index}"
            )
        if not reason:
            raise AgentSemanticLinkerError(
                f"agent_semantic_assessment_reason_missing:{assessment_index}"
            )
        if not isinstance(relationships, list):
            raise AgentSemanticLinkerError(
                f"agent_semantic_relationships_not_list:{assessment_index}"
            )
        if disposition == "LINKED" and not relationships:
            # A single assessment self-contradiction (LINKED with no
            # relationship) is a per-assessment quality defect, not a batch
            # schema failure. Isolating it as a receipted rejection preserves
            # every other valid edge; raising here aborted the whole 153-edge
            # link set and degraded the comprehension channel to source-only.
            rejected_inconsistent_disposition += 1
            reject(
                assessment_index,
                -1,
                raw_assessment,
                "LINKED_WITHOUT_RELATIONSHIPS",
            )
            return 0, False
        if disposition != "LINKED" and relationships:
            rejected_inconsistent_disposition += 1
            reject(
                assessment_index,
                -1,
                raw_assessment,
                "UNLINKED_WITH_RELATIONSHIPS",
            )
            return 0, False
        if subject_id not in expected_ids:
            rejected_invalid_identity += 1
            reject(
                assessment_index,
                -1,
                raw_assessment,
                f"UNKNOWN_{subject_kind.upper()}_ID",
            )
            return 0, False
        if (
            subject_kind == "rule"
            and subject_id in seen_assessments
        ) or (
            subject_kind == "transition"
            and subject_id in seen_transition_assessments
        ):
            rejected_duplicates += 1
            reject(
                assessment_index,
                -1,
                raw_assessment,
                f"DUPLICATE_{subject_kind.upper()}_ASSESSMENT",
            )
            return 0, False
        if subject_kind == "rule":
            seen_assessments.add(subject_id)
        else:
            seen_transition_assessments.add(subject_id)
        accepted_for_assessment = 0

        for relationship_index, raw in enumerate(relationships):
            proposal_count += 1
            if not isinstance(raw, dict) or set(raw) != {
                "interface_id",
                "confidence",
                "reason",
                "evidence_refs",
            }:
                raise AgentSemanticLinkerError(
                    "agent_semantic_relationship_fields_invalid:"
                    f"{assessment_index}:{relationship_index}"
                )
            interface_id = _text(raw.get("interface_id"))
            if interface_id not in interfaces:
                rejected_invalid_identity += 1
                reject(
                    assessment_index,
                    relationship_index,
                    raw,
                    "UNKNOWN_INTERFACE_ID",
                )
                continue
            if interface_id not in allowed_interface_ids:
                rejected_non_candidate += 1
                reject(
                    assessment_index,
                    relationship_index,
                    raw,
                    "CANDIDATE_RECALL_MISS",
                )
                continue
            try:
                confidence = float(raw.get("confidence"))
            except (TypeError, ValueError) as exc:
                raise AgentSemanticLinkerError(
                    "agent_semantic_confidence_invalid:"
                    f"{assessment_index}:{relationship_index}"
                ) from exc
            if not 0.0 <= confidence <= 1.0:
                raise AgentSemanticLinkerError(
                    "agent_semantic_confidence_invalid:"
                    f"{assessment_index}:{relationship_index}"
                )
            rationale = _prompt_safe_text(raw.get("reason"), limit=600)
            if not rationale:
                raise AgentSemanticLinkerError(
                    "agent_semantic_reason_missing:"
                    f"{assessment_index}:{relationship_index}"
                )
            evidence_refs = raw.get("evidence_refs")
            if (
                not isinstance(evidence_refs, list)
                or not evidence_refs
                or any(not _text(ref) for ref in evidence_refs)
            ):
                raise AgentSemanticLinkerError(
                    "agent_semantic_evidence_refs_invalid:"
                    f"{assessment_index}:{relationship_index}"
                )
            evidence_refs = list(dict.fromkeys(
                _text(ref) for ref in evidence_refs
            ))
            if (
                subject_id not in evidence_refs
                or interface_id not in evidence_refs
                or any(ref not in valid_refs for ref in evidence_refs)
            ):
                rejected_invalid_evidence += 1
                reject(
                    assessment_index,
                    relationship_index,
                    raw,
                    "UNKNOWN_EVIDENCE_REF",
                )
                continue
            pair = (relation, subject_id, interface_id)
            if pair in seen_pairs:
                rejected_duplicates += 1
                reject(
                    assessment_index,
                    relationship_index,
                    raw,
                    "DUPLICATE_PROPOSAL",
                )
                continue
            seen_pairs.add(pair)
            if confidence < MIN_CONFIDENCE:
                rejected_low_confidence += 1
                reject(
                    assessment_index,
                    relationship_index,
                    raw,
                    "LOW_CONFIDENCE",
                )
                continue
            if per_subject.get(subject_id, 0) >= MAX_LINKS_PER_RULE:
                rejected_rule_limit += 1
                reject(
                    assessment_index,
                    relationship_index,
                    raw,
                    "RULE_LINK_LIMIT_EXCEEDED",
                )
                continue
            per_subject[subject_id] = per_subject.get(subject_id, 0) + 1
            if pair in existing:
                existing_count += 1
                accepted_for_assessment += 1
                continue

            proposal_fingerprint = _fingerprint({
                "subject_id": subject_id,
                "interface_id": interface_id,
                "confidence": confidence,
                "reason": rationale,
                "evidence_refs": evidence_refs,
            })
            accepted.append({
                "edge_id": "edge:" + _fingerprint({
                    "subject": subject_id,
                    "interface": interface_id,
                    "derivation": "agent_semantic_mapping",
                })[:20],
                "from": subject_id,
                "to": interface_id,
                "relation": relation,
                "confidence": round(confidence, 4),
                "status": "accepted",
                "derivation": "agent_semantic_mapping",
                "evidence_gate": (
                    "behavior_ir_ids_and_runtime_oracle_required"
                ),
                "source_id": "agent_semantic_linker",
                "evidence": {
                    "subject_source_id": _text(
                        (rules if subject_kind == "rule" else transitions_by_id).get(
                            subject_id, {}
                        ).get("source_id")
                    )
                    if subject_kind == "rule"
                    else _text(
                        transitions_by_id.get(subject_id, {}).get("machine_id")
                    ),
                    "interface_source_id": _text(
                        interfaces[interface_id].get("source_id")
                    ),
                    "proposal_fingerprint": proposal_fingerprint,
                    "supporting_fact_refs": evidence_refs,
                    "semantic_rationale": rationale,
                    "semantic_rationale_is_not_business_fact": True,
                    "runtime_verification_required": True,
                },
            })
            accepted_for_assessment += 1
        return accepted_for_assessment, True

    transitions_by_id: dict[str, dict[str, Any]] = {
        _text(row.get("transition_id")): dict(row)
        for row in transitions
        if _text(row.get("transition_id"))
    }
    expected_transition_ids = set(transitions_by_id)

    consumed_assessment_ids: set[int] = set()
    for assessment_index, (row, unit) in enumerate(zip(scheduled, units)):
        rule_id = _text(row.get("rule_id"))
        raw_assessment = responses_by_rule.get(rule_id)
        if raw_assessment is None:
            rejections.append({
                "assessment_index": assessment_index,
                "relationship_index": -1,
                "reason_code": "PROVIDER_OMITTED_RULE",
                "proposal_fingerprint": _fingerprint({"rule_id": rule_id}),
            })
            continue
        consumed_assessment_ids.add(id(raw_assessment))
        if not isinstance(raw_assessment, dict) or set(raw_assessment) != {
            "rule_id",
            "disposition",
            "reason",
            "relationships",
        }:
            raise AgentSemanticLinkerError(
                f"agent_semantic_assessment_fields_invalid:{assessment_index}"
            )
        accepted_for_assessment, handled = accept_relationships(
            subject_id=_text(raw_assessment.get("rule_id")),
            subject_kind="rule",
            disposition=_text(raw_assessment.get("disposition")).upper(),
            reason=_prompt_safe_text(
                raw_assessment.get("reason"),
                limit=600,
            ),
            relationships=raw_assessment.get("relationships"),
            assessment_index=assessment_index,
            relation="rule_to_interface",
            raw_assessment=raw_assessment,
            expected_ids=set(rules),
            allowed_interface_ids=unit["candidate_ids"],
            valid_refs=unit["valid_refs"],
        )
        rule_assessments.append({
            "rule_id": _text(raw_assessment.get("rule_id")),
            "disposition": _text(raw_assessment.get("disposition")).upper(),
            "accepted_relationship_count": accepted_for_assessment,
            "reason": _prompt_safe_text(
                raw_assessment.get("reason"), limit=600
            ),
            "reason_is_not_business_fact": True,
            "assessment_fingerprint": _fingerprint(raw_assessment),
            "candidate_interface_count": unit["candidate_count"],
            "recall_channels": unit["recall_channels"],
            "recall_fallback": unit["recall_fallback"],
        })

    # Assessments with an unknown or duplicate rule identity stay visible:
    # they are validated against the scheduled rule set and receipted as
    # rejected instead of vanishing.
    for extra_index, raw_assessment in enumerate(batch_assessments_ordered):
        if id(raw_assessment) in consumed_assessment_ids:
            continue
        if not isinstance(raw_assessment, dict) or set(raw_assessment) != {
            "rule_id",
            "disposition",
            "reason",
            "relationships",
        }:
            raise AgentSemanticLinkerError(
                "agent_semantic_assessment_fields_invalid:extra"
            )
        subject_id = _text(raw_assessment.get("rule_id"))
        unit = unit_by_rule_id.get(subject_id)
        assessment_index = len(scheduled) + extra_index
        accepted_for_assessment, _handled = accept_relationships(
            subject_id=subject_id,
            subject_kind="rule",
            disposition=_text(raw_assessment.get("disposition")).upper(),
            reason=_prompt_safe_text(
                raw_assessment.get("reason"),
                limit=600,
            ),
            relationships=raw_assessment.get("relationships"),
            assessment_index=assessment_index,
            relation="rule_to_interface",
            raw_assessment=raw_assessment,
            expected_ids=set(rules),
            allowed_interface_ids=(
                unit["candidate_ids"] if unit is not None else set()
            ),
            valid_refs=(unit["valid_refs"] if unit is not None else set()),
        )
        rule_assessments.append({
            "rule_id": subject_id,
            "disposition": _text(raw_assessment.get("disposition")).upper(),
            "accepted_relationship_count": accepted_for_assessment,
            "reason": _prompt_safe_text(
                raw_assessment.get("reason"), limit=600
            ),
            "reason_is_not_business_fact": True,
            "assessment_fingerprint": _fingerprint(raw_assessment),
            "candidate_interface_count": (
                unit["candidate_count"] if unit is not None else 0
            ),
        })

    if transition_response is not None:
        transition_units_by_id = {
            tunit["transition_id"]: tunit
            for tunit in (transition_unit or {}).get("units", [])
        }
        handled_transition_ids: set[str] = set()
        for local_index, raw_transition in enumerate(
            transition_response.get("transition_assessments") or []
        ):
            assessment_index = len(rule_assessments) + local_index
            if not isinstance(raw_transition, dict) or set(raw_transition) != {
                "transition_id",
                "disposition",
                "reason",
                "relationships",
            }:
                raise AgentSemanticLinkerError(
                    "agent_semantic_transition_assessment_fields_invalid:"
                    f"{assessment_index}"
                )
            transition_id = _text(raw_transition.get("transition_id"))
            tunit = transition_units_by_id.get(transition_id)
            accepted_for_assessment, handled = accept_relationships(
                subject_id=transition_id,
                subject_kind="transition",
                disposition=_text(raw_transition.get("disposition")).upper(),
                reason=_prompt_safe_text(
                    raw_transition.get("reason"),
                    limit=600,
                ),
                relationships=raw_transition.get("relationships"),
                assessment_index=assessment_index,
                relation="state_transition_to_interface",
                raw_assessment=raw_transition,
                expected_ids=expected_transition_ids,
                allowed_interface_ids=(
                    tunit["candidate_ids"] if tunit is not None else set()
                ),
                valid_refs=(
                    tunit["valid_refs"] if tunit is not None else set()
                ),
            )
            if handled:
                handled_transition_ids.add(transition_id)
            transition_assessments.append({
                "transition_id": transition_id,
                "disposition": _text(
                    raw_transition.get("disposition")
                ).upper(),
                "accepted_relationship_count": accepted_for_assessment,
                "reason": _prompt_safe_text(
                    raw_transition.get("reason"), limit=600
                ),
                "reason_is_not_business_fact": True,
                "assessment_fingerprint": _fingerprint(raw_transition),
                "candidate_interface_count": (
                    tunit["candidate_count"] if tunit is not None else 0
                ),
            })
        for missing_transition_id in sorted(
            expected_transition_ids - handled_transition_ids
        ):
            rejections.append({
                "assessment_index": -1,
                "relationship_index": -1,
                "reason_code": "PROVIDER_OMITTED_TRANSITION",
                "proposal_fingerprint": _fingerprint({
                    "transition_id": missing_transition_id,
                }),
            })

    for missing_rule_id in sorted(
        set(rules) - seen_assessments
    ):
        rejections.append({
            "assessment_index": -1,
            "relationship_index": -1,
            "reason_code": "PROVIDER_OMITTED_RULE",
            "proposal_fingerprint": _fingerprint({"rule_id": missing_rule_id}),
        })

    unassessed_rule_ids = [
        rule_id for rule_id in rules if rule_id not in seen_assessments
    ]
    unassessed_transition_ids = [
        transition_id
        for transition_id in expected_transition_ids
        if transition_id not in seen_transition_assessments
    ]
    usage = resolved_client.usage_snapshot()
    has_gaps = bool(
        rejections
        or unassessed_rule_ids
        or unassessed_transition_ids
        or budget_skipped_rule_ids
        or failed_units
        or any(
            row["disposition"] != "LINKED"
            or row["accepted_relationship_count"] == 0
            for row in [*rule_assessments, *transition_assessments]
        )
    )
    enriched = deepcopy(knowledge_asset)
    enriched["relationships"] = [
        *[
            dict(row)
            for row in _list(knowledge_asset.get("relationships"))
            if isinstance(row, dict)
        ],
        *accepted,
    ]
    if rejections:
        status = "VERIFIED_WITH_REJECTIONS"
    elif failed_units:
        status = "VERIFIED_WITH_FAILED_UNITS"
    elif has_gaps:
        status = "VERIFIED_WITH_GAPS"
    else:
        status = "VERIFIED"
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "prompt_protocol": PROMPT_PROTOCOL,
        "status": status,
        "knowledge_asset_id": _text(knowledge_asset.get("asset_id")),
        "semantic_authority": "source_documents_and_behavior_ir_ids",
        "linking_structure": "candidate_scoped_three_tier",
        "rule_count": len(rules),
        "assessed_rule_count": len(seen_assessments),
        "unassessed_rule_count": len(unassessed_rule_ids),
        "unassessed_rule_ids": unassessed_rule_ids,
        "budget_skipped_rule_count": len(budget_skipped_rule_ids),
        "budget_skipped_rule_ids": budget_skipped_rule_ids,
        "batch_count": len(batches) + (1 if transitions else 0),
        "request_count": max(0, provider_attempt_count - provider_retry_count),
        "transition_request_count": 1 if transitions else 0,
        "context_fact_count": max(
            [len(unit["fact_rows"]) for unit in units]
            + ([
                max(
                    len(tunit["fact_rows"])
                    for tunit in transition_unit["units"]
                )
            ] if transition_unit else []),
            default=0,
        ),
        "context_fact_omitted_count": 0,
        "supporting_fact_pool_count": len(fact_pool),
        "candidate_recall": {
            "rule_count": len(units),
            "candidate_total": recall_candidate_total,
            "candidate_min": recall_candidate_min,
            "candidate_max": recall_candidate_max,
            "fallback_rule_count": recall_fallback_count,
            "empty_candidate_rule_count": recall_empty_count,
            "recall_basis": recall_basis,
        },
        "cache": {
            "hit_count": cache_hit_count,
            "miss_count": cache_miss_count,
            "transition_cache_hit": transition_cache_hit,
            "persistence_failures": cache.persistence_failures,
            "cache_key_components": [
                "rule_fingerprint",
                "candidate_interface_fingerprints",
                "supporting_fact_fingerprints",
                "model_config_fingerprint",
            ],
        },
        "input_budget": {
            "budget_tokens": input_budget,
            "env_override": LINKER_MAX_INPUT_TOKENS_ENV,
            "scope": "rule_batches_only",
            "batches_before_sizing": len(batches),
            "batches_after_sizing": len(sized_batches),
            "batches_split_count": batches_split_count,
            "budget_exhausted_rule_ids": input_budget_exhausted_rule_ids,
            "budget_exhausted_unit_count": len(input_budget_exhausted_rule_ids),
        },
        "failed_unit_count": len(failed_units),
        "failed_units": failed_units,
        "proposal_count": proposal_count,
        "accepted_relationship_count": len(accepted),
        "rejected_proposal_count": len(rejections),
        "rejected_low_confidence_count": rejected_low_confidence,
        "rejected_invalid_identity_count": rejected_invalid_identity,
        "rejected_non_candidate_count": rejected_non_candidate,
        "rejected_invalid_evidence_count": rejected_invalid_evidence,
        "rejected_duplicate_count": rejected_duplicates,
        "rejected_rule_limit_count": rejected_rule_limit,
        "rejected_inconsistent_disposition_count": rejected_inconsistent_disposition,
        "existing_relationship_count": existing_count,
        "no_executable_interface_count": sum(
            row["disposition"] == "NO_EXECUTABLE_INTERFACE"
            for row in rule_assessments
        ),
        "ambiguous_rule_count": sum(
            row["disposition"] == "AMBIGUOUS"
            for row in rule_assessments
        ),
        "transition_count": len(transitions),
        "transition_budget_skipped_count": (
            transition_unit["budget_skipped_count"]
            if transition_unit is not None
            else 0
        ),
        "assessed_transition_count": len(seen_transition_assessments),
        "unassessed_transition_count": len(unassessed_transition_ids),
        "unassessed_transition_ids": unassessed_transition_ids,
        "no_executable_transition_count": sum(
            row["disposition"] == "NO_EXECUTABLE_INTERFACE"
            for row in transition_assessments
        ),
        "ambiguous_transition_count": sum(
            row["disposition"] == "AMBIGUOUS"
            for row in transition_assessments
        ),
        "transition_assessments": transition_assessments,
        "provider_attempt_count": provider_attempt_count,
        "provider_retry_count": provider_retry_count,
        "rule_assessments": rule_assessments,
        "rejections": rejections,
        "usage": dict(usage) if isinstance(usage, dict) else {},
        "accepted_edge_ids": [row["edge_id"] for row in accepted],
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    enriched["agent_semantic_link_receipt"] = receipt
    return enriched, receipt
