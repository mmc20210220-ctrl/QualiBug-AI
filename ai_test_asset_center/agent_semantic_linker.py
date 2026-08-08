from __future__ import annotations

"""Expert semantic linking constrained to source-backed knowledge identities.

The model is used for the part machines are good at: understanding how an
enterprise rule relates to a documented interface in the surrounding business
model. It is not a fact authority. Every accepted link names existing rule,
interface, and supporting-fact identities; malformed output fails fast and
uncertain or invented proposals stay visible in the receipt.
"""

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Protocol

from .artifact_redactor import redact_and_validate
from .observed_product_scan_protocol import find_evaluator_private_context_paths


RECEIPT_SCHEMA = "qualibug.agent-semantic-link-receipt.v1"
PROMPT_PROTOCOL = "qualibug.agent-business-semantic-assessment.v2"
MIN_CONFIDENCE = 0.65
MAX_LINKS_PER_RULE = 4
MAX_PROVIDER_ATTEMPTS = 2
MAX_RULES_PER_REQUEST = 40
MAX_PROVIDER_REQUESTS = 8
MAX_CONTEXT_FACTS = 400

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


def _fact_rows(asset: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Build compact source-backed business facts; never include example values."""

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
    omitted = max(0, len(facts) - MAX_CONTEXT_FACTS)
    return facts[:MAX_CONTEXT_FACTS], omitted


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


def _prompt(
    asset: dict[str, Any],
    rule_batch: list[dict[str, Any]],
) -> tuple[str, set[str], int, int]:
    rules = [_rule_prompt_row(row) for row in rule_batch]
    interfaces = [
        _interface_prompt_row(row)
        for row in _list(asset.get("interfaces"))
        if isinstance(row, dict) and _text(row.get("interface_id"))
    ]
    transitions = _asset_transition_rows(asset)
    facts, omitted_fact_count = _fact_rows(asset)
    valid_refs = {
        *(_text(row.get("rule_id")) for row in rules),
        *(_text(row.get("transition_id")) for row in transitions),
        *(_text(row.get("interface_id")) for row in interfaces),
        *(_text(row.get("fact_id")) for row in facts),
    }
    valid_refs.discard("")
    packet = {
        "protocol": PROMPT_PROTOCOL,
        "business_semantic_model": {
            "rules_to_assess": rules,
            "state_transitions_to_assess": transitions,
            "documented_interfaces": interfaces,
            "source_backed_context_facts": facts,
            "context_fact_omitted_count": omitted_fact_count,
        },
    }
    safe_packet, _ = redact_and_validate(packet)
    response_contract = {
        "assessments": [{
            "rule_id": "exact supplied rule_id",
            "disposition": "LINKED|NO_EXECUTABLE_INTERFACE|AMBIGUOUS",
            "reason": "brief business and test rationale",
            "relationships": [{
                "interface_id": "exact supplied interface_id",
                "confidence": 0.0,
                "reason": "how this interface exercises or observes the rule",
                "evidence_refs": [
                    "rule_id",
                    "interface_id",
                    "optional exact fact_id",
                ],
            }],
        }],
        "transition_assessments": [{
            "transition_id": "exact supplied transition_id",
            "disposition": "LINKED|NO_EXECUTABLE_INTERFACE|AMBIGUOUS",
            "reason": "brief business and test rationale",
            "relationships": [{
                "interface_id": "exact supplied interface_id",
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
    prompt = (
        "Act as both an enterprise product expert and a senior test expert. "
        "Understand the supplied business semantic model before linking anything. "
        "For every rule in rules_to_assess, decide whether a documented interface "
        "can exercise or observe it. Consider actors, permissions, entities, fields, "
        "state transitions, preconditions, postconditions, negative paths, and "
        "business outcomes. This creates bounded experiment intent, never a bug "
        "finding or a new business fact.\n\n"
        "For every state transition in state_transitions_to_assess, link the "
        "documented interface that performs that transition (allowed) or the "
        "interface that would attempt the forbidden move (forbidden transitions: "
        "the operation that changes the entity's state, e.g. the payment or "
        "status-change operation for a payment/status machine). A transition "
        "without any interface that can perform or attempt it is "
        "NO_EXECUTABLE_INTERFACE.\n\n"
        "Use only exact identifiers and facts supplied in the model. Never invent "
        "endpoints, fields, actors, states, credentials, request bodies, rules, or "
        "observations. LINKED requires one to four relationships and evidence_refs "
        "must contain the exact rule_id or transition_id, the interface_id, and "
        "any supporting fact_ids you relied on. Use NO_EXECUTABLE_INTERFACE when "
        "the source has no callable surface. Use AMBIGUOUS when several "
        "interpretations remain plausible. The disposition and relationships "
        "fields are one atomic contract: LINKED requires at least one "
        "relationship; NO_EXECUTABLE_INTERFACE and AMBIGUOUS require "
        "relationships to be exactly an empty array. Never put a candidate "
        "interface in relationships for an unlinked disposition. Omit links below "
        f"confidence {MIN_CONFIDENCE}. Return exactly one assessment for every "
        "supplied rule and exactly one transition assessment for every supplied "
        "state transition, and JSON only, with this exact shape:\n"
        + json.dumps(response_contract, ensure_ascii=False, separators=(",", ":"))
        + "\n\nINPUT:\n"
        + json.dumps(safe_packet, ensure_ascii=False, separators=(",", ":"))
        + "\n\nFINAL CONTRACT CHECK: for every assessment and transition "
        "assessment, if disposition is not LINKED, emit `relationships: []`; if "
        "relationships is non-empty, emit `disposition: LINKED`."
    )
    return prompt, valid_refs, len(facts), omitted_fact_count


def _complete_batch(
    client: AgentJsonClient,
    *,
    prompt: str,
) -> tuple[dict[str, Any], int, int]:
    retry_count = 0
    last_error: Exception | None = None
    for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
        try:
            response = client.complete_json(
                system_prompt=(
                    "You perform source-grounded enterprise business semantic analysis. "
                    "Identifiers and supplied facts are the only authority. Output JSON only."
                ),
                user_prompt=prompt,
            )
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


def enrich_knowledge_asset_with_agent_relationships(
    knowledge_asset: dict[str, Any],
    *,
    client: AgentJsonClient | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add validated rule-to-interface intent edges and a complete audit receipt."""

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
    rule_rows = list(rules.values())
    max_rules = MAX_RULES_PER_REQUEST * MAX_PROVIDER_REQUESTS
    scheduled = rule_rows[:max_rules]
    budget_skipped_rule_ids = [
        _text(row.get("rule_id")) for row in rule_rows[max_rules:]
    ]
    batches = [
        scheduled[index:index + MAX_RULES_PER_REQUEST]
        for index in range(0, len(scheduled), MAX_RULES_PER_REQUEST)
    ]

    responses: list[tuple[list[dict[str, Any]], dict[str, Any], set[str]]] = []
    provider_attempt_count = 0
    provider_retry_count = 0
    context_fact_count = 0
    context_fact_omitted_count = 0
    for batch in batches:
        prompt, valid_refs, fact_count, omitted_count = _prompt(
            knowledge_asset,
            batch,
        )
        response, attempts, retries = _complete_batch(
            resolved_client,
            prompt=prompt,
        )
        if not isinstance(response, dict) or not set(response) <= {
            "assessments",
            "transition_assessments",
        } or not (
            set(response) & {"assessments", "transition_assessments"}
        ):
            raise AgentSemanticLinkerError("agent_semantic_response_schema_invalid")
        assessments = response.get("assessments")
        transition_assessments = response.get("transition_assessments")
        if assessments is not None and not isinstance(assessments, list):
            raise AgentSemanticLinkerError("agent_semantic_assessments_not_list")
        if transition_assessments is not None and not isinstance(
            transition_assessments, list
        ):
            raise AgentSemanticLinkerError(
                "agent_semantic_transition_assessments_not_list"
            )
        if transitions and not transition_assessments:
            raise AgentSemanticLinkerError(
                "agent_semantic_transition_assessments_missing"
            )
        if not transitions and transition_assessments:
            raise AgentSemanticLinkerError(
                "agent_semantic_unexpected_transition_assessments"
            )
        responses.append((batch, response, valid_refs))
        provider_attempt_count += attempts
        provider_retry_count += retries
        context_fact_count = max(context_fact_count, fact_count)
        context_fact_omitted_count = max(
            context_fact_omitted_count,
            omitted_count,
        )

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
    rejected_invalid_evidence = 0
    rejected_duplicates = 0
    rejected_rule_limit = 0
    existing_count = 0
    proposal_count = 0
    assessment_offset = 0

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
    ) -> tuple[int, bool]:
        """Validate one assessment's relationships under the shared contract.

        Returns ``(accepted_count, handled)``. ``handled=False`` means the
        assessment was identity-invalid or a duplicate, so the caller must not
        count it toward the provider-completeness check. Every rejection is
        receipted; malformed output raises instead of degrading silently.
        """
        nonlocal proposal_count, rejected_low_confidence
        nonlocal rejected_invalid_identity, rejected_invalid_evidence
        nonlocal rejected_duplicates, rejected_rule_limit, existing_count
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
            raise AgentSemanticLinkerError(
                f"agent_semantic_linked_relationship_missing:{assessment_index}"
            )
        if disposition != "LINKED" and relationships:
            raise AgentSemanticLinkerError(
                f"agent_semantic_unlinked_relationship_present:{assessment_index}"
            )
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
    batch_transition_seen: set[str] = set()

    for batch, response, valid_refs in responses:
        expected_rule_ids = {
            _text(row.get("rule_id")) for row in batch if _text(row.get("rule_id"))
        }
        batch_seen: set[str] = set()
        for local_index, raw_assessment in enumerate(response["assessments"]):
            assessment_index = assessment_offset + local_index
            if not isinstance(raw_assessment, dict) or set(raw_assessment) != {
                "rule_id",
                "disposition",
                "reason",
                "relationships",
            }:
                raise AgentSemanticLinkerError(
                    f"agent_semantic_assessment_fields_invalid:{assessment_index}"
                )
            rule_id = _text(raw_assessment.get("rule_id"))
            accepted_for_assessment, handled = accept_relationships(
                subject_id=rule_id,
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
                expected_ids=expected_rule_ids,
            )
            if handled:
                batch_seen.add(rule_id)
            rule_assessments.append({
                "rule_id": rule_id,
                "disposition": _text(raw_assessment.get("disposition")).upper(),
                "accepted_relationship_count": accepted_for_assessment,
                "reason": _prompt_safe_text(
                    raw_assessment.get("reason"), limit=600
                ),
                "reason_is_not_business_fact": True,
                "assessment_fingerprint": _fingerprint(raw_assessment),
            })

        for missing_rule_id in sorted(expected_rule_ids - batch_seen):
            rejections.append({
                "assessment_index": -1,
                "relationship_index": -1,
                "reason_code": "PROVIDER_OMITTED_RULE",
                "proposal_fingerprint": _fingerprint({
                    "rule_id": missing_rule_id,
                }),
            })

        for local_index, raw_transition in enumerate(
            response.get("transition_assessments") or []
        ):
            assessment_index = assessment_offset + local_index
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
            )
            if handled:
                batch_transition_seen.add(transition_id)
            transition_assessments.append({
                "transition_id": transition_id,
                "disposition": _text(raw_transition.get("disposition")).upper(),
                "accepted_relationship_count": accepted_for_assessment,
                "reason": _prompt_safe_text(
                    raw_transition.get("reason"), limit=600
                ),
                "reason_is_not_business_fact": True,
                "assessment_fingerprint": _fingerprint(raw_transition),
            })

        for missing_transition_id in sorted(
            expected_transition_ids - batch_transition_seen
        ):
            rejections.append({
                "assessment_index": -1,
                "relationship_index": -1,
                "reason_code": "PROVIDER_OMITTED_TRANSITION",
                "proposal_fingerprint": _fingerprint({
                    "transition_id": missing_transition_id,
                }),
            })
        assessment_offset += len(response["assessments"])

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
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "prompt_protocol": PROMPT_PROTOCOL,
        "status": (
            "VERIFIED_WITH_REJECTIONS"
            if rejections
            else "VERIFIED_WITH_GAPS"
            if has_gaps
            else "VERIFIED"
        ),
        "knowledge_asset_id": _text(knowledge_asset.get("asset_id")),
        "semantic_authority": "source_documents_and_behavior_ir_ids",
        "rule_count": len(rules),
        "assessed_rule_count": len(seen_assessments),
        "unassessed_rule_count": len(unassessed_rule_ids),
        "unassessed_rule_ids": unassessed_rule_ids,
        "budget_skipped_rule_count": len(budget_skipped_rule_ids),
        "budget_skipped_rule_ids": budget_skipped_rule_ids,
        "batch_count": len(batches),
        "context_fact_count": context_fact_count,
        "context_fact_omitted_count": context_fact_omitted_count,
        "proposal_count": proposal_count,
        "accepted_relationship_count": len(accepted),
        "rejected_proposal_count": len(rejections),
        "rejected_low_confidence_count": rejected_low_confidence,
        "rejected_invalid_identity_count": rejected_invalid_identity,
        "rejected_invalid_evidence_count": rejected_invalid_evidence,
        "rejected_duplicate_count": rejected_duplicates,
        "rejected_rule_limit_count": rejected_rule_limit,
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
