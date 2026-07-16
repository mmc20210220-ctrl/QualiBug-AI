from __future__ import annotations

"""Agent intent generation constrained to source-backed knowledge identities."""

import hashlib
import json
from copy import deepcopy
from typing import Any, Protocol

from .observed_product_scan_protocol import find_evaluator_private_context_paths


RECEIPT_SCHEMA = "qualibug.agent-semantic-link-receipt.v1"
MIN_CONFIDENCE = 0.65
MAX_LINKS_PER_RULE = 4
MAX_PROVIDER_ATTEMPTS = 2
_TRANSIENT_PROVIDER_ERROR_NAMES = frozenset({
    "IncompleteRead",
    "TimeoutError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "RemoteDisconnected",
    "SSLEOFError",
})


class AgentSemanticLinkerError(ValueError):
    """Agent output is unavailable, malformed, or outside Behavior IR inputs."""


class AgentJsonClient(Protocol):
    def complete_json(self, **kwargs: Any) -> dict[str, Any]: ...

    def usage_snapshot(self) -> dict[str, float]: ...


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


def _default_client() -> AgentJsonClient:
    from .llm_reasoning import ReasoningClient, ReasoningConfig

    config = ReasoningConfig.from_env()
    if not config.enabled:
        raise AgentSemanticLinkerError("agent_provider_not_configured")
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


def _prompt(asset: dict[str, Any]) -> str:
    rules = [
        {
            "rule_id": _text(row.get("rule_id")),
            "statement": _text(row.get("statement"))[:800],
            "kind": _text(
                row.get("kind") or row.get("rule_type") or row.get("risk_type")
            ),
        }
        for row in asset.get("rule_library") or []
        if isinstance(row, dict) and _text(row.get("rule_id"))
    ]
    interfaces = [
        {
            "interface_id": _text(row.get("interface_id")),
            "method": _text(row.get("method")).upper(),
            "path": _text(row.get("path")),
            "summary": _text(row.get("summary"))[:500],
        }
        for row in asset.get("interfaces") or []
        if isinstance(row, dict) and _text(row.get("interface_id"))
    ]
    return (
        "Map each explicit business rule only to documented interfaces that can "
        "exercise or observe that rule. This generates experiment intent, not bug "
        "findings. Use only exact rule_id and interface_id values supplied below. "
        "Do not invent IDs, endpoints, rules, fields, evidence, or observed results. "
        f"Return at most {MAX_LINKS_PER_RULE} non-duplicate interfaces per rule and "
        f"omit uncertain links below confidence {MIN_CONFIDENCE}. Return JSON exactly "
        "as {\"relationships\":[{\"rule_id\":\"...\","
        "\"interface_id\":\"...\",\"confidence\":0.0,"
        "\"reason\":\"brief semantic rationale\"}]}.\n\n"
        "RULES:\n"
        + json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
        + "\n\nINTERFACES:\n"
        + json.dumps(interfaces, ensure_ascii=False, separators=(",", ":"))
    )


def enrich_knowledge_asset_with_agent_relationships(
    knowledge_asset: dict[str, Any],
    *,
    client: AgentJsonClient | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add only structurally validated Agent rule-to-interface intent edges."""

    if not isinstance(knowledge_asset, dict):
        raise AgentSemanticLinkerError("knowledge_asset_not_object")
    private_paths = find_evaluator_private_context_paths(knowledge_asset)
    if private_paths:
        raise AgentSemanticLinkerError(
            "evaluator_private_context_forbidden:" + ",".join(private_paths)
        )
    rules = {
        _text(row.get("rule_id")): dict(row)
        for row in knowledge_asset.get("rule_library") or []
        if isinstance(row, dict) and _text(row.get("rule_id"))
    }
    interfaces = {
        _text(row.get("interface_id")): dict(row)
        for row in knowledge_asset.get("interfaces") or []
        if isinstance(row, dict) and _text(row.get("interface_id"))
    }
    if not rules or not interfaces:
        raise AgentSemanticLinkerError("agent_semantic_inputs_empty")
    resolved_client = client or _default_client()
    provider_attempt_count = 0
    provider_retry_count = 0
    last_provider_error: Exception | None = None
    for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
        provider_attempt_count = attempt
        try:
            response = resolved_client.complete_json(
                system_prompt=(
                    "You generate bounded enterprise test intent. Source documents and "
                    "Behavior IR identifiers are the only semantic authority. Output JSON only."
                ),
                user_prompt=_prompt(knowledge_asset),
            )
            break
        except Exception as exc:
            last_provider_error = exc
            retryable = _is_transient_provider_error(exc)
            if not retryable or attempt >= MAX_PROVIDER_ATTEMPTS:
                raise AgentSemanticLinkerError(
                    f"agent_semantic_provider_failed:{type(exc).__name__}:{exc}"
                    f":attempts={attempt}"
                ) from exc
            provider_retry_count += 1
    else:  # pragma: no cover - loop exits via break or raise
        assert last_provider_error is not None
        raise AgentSemanticLinkerError(
            "agent_semantic_provider_failed:"
            f"{type(last_provider_error).__name__}:{last_provider_error}"
            f":attempts={provider_attempt_count}"
        ) from last_provider_error
    if not isinstance(response, dict) or set(response) != {"relationships"}:
        raise AgentSemanticLinkerError("agent_semantic_response_schema_invalid")
    proposals = response.get("relationships")
    if not isinstance(proposals, list):
        raise AgentSemanticLinkerError("agent_semantic_relationships_not_list")
    existing = {
        (_text(row.get("from")), _text(row.get("to")))
        for row in knowledge_asset.get("relationships") or []
        if isinstance(row, dict)
        and _text(row.get("relation") or row.get("relation_type"))
        == "rule_to_interface"
        and _text(row.get("status") or "accepted").lower() == "accepted"
    }
    accepted: list[dict[str, Any]] = []
    rejected_low_confidence = 0
    rejected_invalid_identity = 0
    rejected_duplicates = 0
    rejected_rule_limit = 0
    existing_count = 0
    rejections: list[dict[str, Any]] = []

    def reject(index: int, raw: dict[str, Any], reason_code: str) -> None:
        rejections.append({
            "proposal_index": index,
            "reason_code": reason_code,
            "proposal_fingerprint": _fingerprint(raw),
        })

    seen: set[tuple[str, str]] = set()
    per_rule: dict[str, int] = {}
    for index, raw in enumerate(proposals):
        if not isinstance(raw, dict) or set(raw) != {
            "rule_id",
            "interface_id",
            "confidence",
            "reason",
        }:
            raise AgentSemanticLinkerError(
                f"agent_semantic_relationship_fields_invalid:{index}"
            )
        rule_id = _text(raw.get("rule_id"))
        interface_id = _text(raw.get("interface_id"))
        if rule_id not in rules:
            rejected_invalid_identity += 1
            reject(index, raw, "UNKNOWN_RULE_ID")
            continue
        if interface_id not in interfaces:
            rejected_invalid_identity += 1
            reject(index, raw, "UNKNOWN_INTERFACE_ID")
            continue
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise AgentSemanticLinkerError(
                f"agent_semantic_confidence_invalid:{index}"
            ) from exc
        if not 0.0 <= confidence <= 1.0:
            raise AgentSemanticLinkerError(
                f"agent_semantic_confidence_invalid:{index}"
            )
        if not _text(raw.get("reason")):
            raise AgentSemanticLinkerError(
                f"agent_semantic_reason_missing:{index}"
            )
        pair = (rule_id, interface_id)
        if pair in seen:
            rejected_duplicates += 1
            reject(index, raw, "DUPLICATE_PROPOSAL")
            continue
        seen.add(pair)
        if confidence < MIN_CONFIDENCE:
            rejected_low_confidence += 1
            reject(index, raw, "LOW_CONFIDENCE")
            continue
        if per_rule.get(rule_id, 0) >= MAX_LINKS_PER_RULE:
            rejected_rule_limit += 1
            reject(index, raw, "RULE_LINK_LIMIT_EXCEEDED")
            continue
        per_rule[rule_id] = per_rule.get(rule_id, 0) + 1
        if pair in existing:
            existing_count += 1
            continue
        proposal_fingerprint = _fingerprint({
            "rule_id": rule_id,
            "interface_id": interface_id,
            "confidence": confidence,
            "reason": _text(raw.get("reason")),
        })
        accepted.append({
            "edge_id": "edge:" + _fingerprint({
                "rule": rule_id,
                "interface": interface_id,
                "derivation": "agent_semantic_mapping",
            })[:20],
            "from": rule_id,
            "to": interface_id,
            "relation": "rule_to_interface",
            "confidence": round(confidence, 4),
            "status": "accepted",
            "derivation": "agent_semantic_mapping",
            "evidence_gate": "behavior_ir_ids_and_runtime_oracle_required",
            "source_id": "agent_semantic_linker",
            "evidence": {
                "rule_source_id": _text(rules[rule_id].get("source_id")),
                "interface_source_id": _text(
                    interfaces[interface_id].get("source_id")
                ),
                "proposal_fingerprint": proposal_fingerprint,
                "runtime_verification_required": True,
            },
        })
    enriched = deepcopy(knowledge_asset)
    enriched["relationships"] = [
        *[
            dict(row)
            for row in knowledge_asset.get("relationships") or []
            if isinstance(row, dict)
        ],
        *accepted,
    ]
    usage = resolved_client.usage_snapshot()
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "status": (
            "VERIFIED_WITH_REJECTIONS" if rejections else "VERIFIED"
        ),
        "knowledge_asset_id": _text(knowledge_asset.get("asset_id")),
        "semantic_authority": "source_documents_and_behavior_ir_ids",
        "proposal_count": len(proposals),
        "accepted_relationship_count": len(accepted),
        "rejected_proposal_count": len(rejections),
        "rejected_low_confidence_count": rejected_low_confidence,
        "rejected_invalid_identity_count": rejected_invalid_identity,
        "rejected_duplicate_count": rejected_duplicates,
        "rejected_rule_limit_count": rejected_rule_limit,
        "existing_relationship_count": existing_count,
        "provider_attempt_count": provider_attempt_count,
        "provider_retry_count": provider_retry_count,
        "rejections": rejections,
        "usage": dict(usage) if isinstance(usage, dict) else {},
        "accepted_edge_ids": [row["edge_id"] for row in accepted],
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    enriched["agent_semantic_link_receipt"] = receipt
    return enriched, receipt
