from __future__ import annotations

"""
QualiBug Phase79 — Project Context Compiler (项目上下文编译器)

Compiles a rich ProjectContext from PRD markdown, OpenAPI/Swagger specs,
API documentation, data dictionaries, and optional existing fixtures/evidence.

This is the semantic graph entry point for Phase79 autonomous test generation:
  PRD + OpenAPI + API docs → ProjectContext → Fixture plan → Test generation

All entity inference uses generic type names — never hardcodes industry-specific
entities. Every candidate is tagged human_confirmation_required=True until a
human reviewer explicitly confirms or rejects it in the UI.
"""

import hashlib
import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ────────────────────────────────────────────────────────────
# Data model
# ────────────────────────────────────────────────────────────


@dataclass
class EntityCandidate:
    """A candidate business entity inferred from documents.

    Never auto-confirmed — human_confirmation_required is always True by default.
    Entity types use generic names: generic_business_object, transaction_record,
    workflow_instance, reference_data, audit_log, etc.
    """

    entity_alias: str
    entity_type: str
    identity_fields: list[str] = field(default_factory=list)
    state_fields: list[str] = field(default_factory=list)
    amount_fields: list[str] = field(default_factory=list)
    quantity_fields: list[str] = field(default_factory=list)
    tenant_fields: list[str] = field(default_factory=list)
    version_fields: list[str] = field(default_factory=list)
    api_sources: list[str] = field(default_factory=list)
    source_documents: list = field(default_factory=list)
    confidence: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    human_confirmation_required: bool = True


@dataclass
class RelationCandidate:
    """A candidate relationship between two entities inferred from documents."""

    from_entity: str
    to_entity: str
    relation_type: str  # e.g. parent_child, reference, workflow_dependency, event_producer
    confidence: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    human_confirmation_required: bool = True


@dataclass
class APICapability:
    """An API endpoint capability inferred from the OpenAPI spec."""

    path: str
    method: str
    capability: str = ""  # read | list | create | update | delete | action | unknown
    entity_alias: str = ""
    entity: str = ""
    entity_id_param: str = ""
    operation_id: str = ""
    summary: str = ""
    description: str = ""
    tags: list = field(default_factory=list)
    is_observer_candidate: bool = False
    is_action_candidate: bool = False
    has_entity_id: bool = False
    has_entity_id_in_path: bool = False
    has_entity_id_in_response: bool = False
    has_correlation_id: bool = False
    has_tenant_id: bool = False
    supports_pagination: bool = False
    supports_filtering: bool = False
    request_body_required: bool = False
    deprecated: bool = False
    path_params: list = field(default_factory=list)
    query_params: list = field(default_factory=list)
    header_params: list = field(default_factory=list)
    body_schema: dict | None = None
    response_schema: dict | None = None
    security: list = field(default_factory=list)
    confidence: float = 0.0
    evidence: list = field(default_factory=list)


@dataclass
class SourceRef:
    """A traceable reference back to a source document section.

    Used to pin every entity / rule inference to a concrete document excerpt.
    """
    source: str = ""
    line: int = 0
    excerpt: str = ""
    confidence: float = 0.0


@dataclass
class ObserverCandidate:
    """A read-only endpoint candidate that can observe entity state."""

    observer_id: str
    entity_alias: str
    method: str
    path: str
    read_only_confidence: float = 0.0
    projection: dict = field(default_factory=dict)
    confidence: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    requires_human_confirmation: bool = True


@dataclass
class BindingCandidate:
    """Binds an action step to an entity, resolving IDs and correlation IDs."""

    action_step: str
    entity_alias: str
    entity_id_source: str
    entity_id_path: str
    entity_id_confidence: float = 0.0
    correlation_id_source: str = ""
    correlation_id_path: str = ""
    confidence: float = 0.0
    evidence: list[dict] = field(default_factory=list)


@dataclass
class FixtureReadiness:
    """Assesses whether a flow has the data fixtures it needs."""

    flow_id: str
    readiness: str  # READY | PARTIALLY_READY | BLOCKED_BY_FIXTURE | BLOCKED_BY_PERMISSION
    missing_requirements: list[str] = field(default_factory=list)
    recommended_next_actions: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    auto_retryable: bool = False


@dataclass
class GapReport:
    """Reports a gap in coverage, documentation, or data."""

    gap_id: str
    category: str  # missing_endpoint | missing_fixture | ambiguous_entity | incomplete_schema
    severity: str  # critical | high | medium | low
    description: str
    affected_entities: list[str] = field(default_factory=list)
    recommended_action: str = ""


@dataclass
class ProjectContext:
    """The compiled semantic graph for a project.

    This is the central data structure consumed by all downstream Phase79 services:
    fixture generation, test scenario planning, observer binding, and gap analysis.
    """

    project_id: str
    compiled_at: str
    source_documents: list[dict] = field(default_factory=list)
    entities: list[EntityCandidate] = field(default_factory=list)
    relations: list[RelationCandidate] = field(default_factory=list)
    apis: list[APICapability] = field(default_factory=list)
    observers: list[ObserverCandidate] = field(default_factory=list)
    bindings: list[BindingCandidate] = field(default_factory=list)
    candidate_lifecycle_transitions: list[dict] = field(default_factory=list)
    candidate_invariants: list[dict] = field(default_factory=list)
    fixtures: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)
    version: int = 1


# ────────────────────────────────────────────────────────────
# P0-4: Multi-evidence field semantic classification
# ────────────────────────────────────────────────────────────

# Generic semantic types (industry-neutral)
FIELD_SEMANTIC_TYPES = (
    "IDENTITY", "FOREIGN_KEY", "OWNER", "TENANT", "STATE", "BALANCE",
    "DELTA", "AMOUNT", "QUANTITY", "VERSION", "SEQUENCE", "TIMESTAMP",
    "BOOLEAN_FLAG", "ENUM", "LIMIT", "THRESHOLD", "IDEMPOTENCY_KEY",
    "AUDIT", "UNKNOWN",
)

# Name-pattern evidence (generic suffixes/prefixes, never entity-specific)
_NAME_EVIDENCE: list[tuple[str, str, float]] = [
    # (regex_pattern, semantic_type, base_score)
    (r"^(id|uuid|guid|pk)$", "IDENTITY", 0.9),
    (r".*(_id|_uuid|_key|_ref|_no|_number)$", "FOREIGN_KEY", 0.7),
    (r".*(_by|_owner|_assignee|_creator|_author)$", "OWNER", 0.7),
    (r"^(tenant|org|organization|workspace)_", "TENANT", 0.75),
    (r".*(status|state|stage|phase)$", "STATE", 0.8),
    (r".*(balance|remaining|available|left)$", "BALANCE", 0.7),
    (r".*(delta|diff|change|adjustment|correction)$", "DELTA", 0.7),
    (r".*(amount|price|total|fee|cost|tax|discount|salary|wage|budget|credit|debit)$", "AMOUNT", 0.7),
    (r".*(quantity|qty|count|num|stock|volume|capacity)$", "QUANTITY", 0.7),
    (r".*(version|ver|revision|rev)$", "VERSION", 0.75),
    (r".*(seq|sequence|order_num|sort|rank|position)$", "SEQUENCE", 0.7),
    (r".*(_at|_on|_date|_time|timestamp|created|updated|expired)$", "TIMESTAMP", 0.8),
    (r"^(is_|has_|can_|should_|will_|enabled|active|deleted|archived|verified)", "BOOLEAN_FLAG", 0.8),
    (r".*(type|kind|category|class|mode|level|grade|tier)$", "ENUM", 0.65),
    (r".*(limit|max|min|quota|ceiling|floor|cap|allowance)$", "LIMIT", 0.7),
    (r".*(threshold|trigger|alert|warning|cutoff)$", "THRESHOLD", 0.7),
    (r".*(idempotency|dedup|correlation|request_id|trace_id|external_event)", "IDEMPOTENCY_KEY", 0.75),
    (r".*(audit|log|trail|history|changelog|modified_by|modified_at)", "AUDIT", 0.7),
]


def classify_field_semantic_multi_evidence(
    field_name: str,
    *,
    schema_info: dict[str, Any] | None = None,
    description_text: str = "",
    operation_context: dict[str, Any] | None = None,
    runtime_values: list[Any] | None = None,
    relationships: list[str] | None = None,
) -> dict[str, Any]:
    """P0-4: Classify field semantic type using multi-evidence scoring.

    Never relies on a single keyword match. Combines:
    - name_score: field name pattern matching (generic suffixes/prefixes)
    - schema_score: data type, constraints (min/max/enum)
    - description_score: documentation text evidence
    - operation_context_score: read/write operation patterns
    - runtime_behavior_score: observed value patterns
    - relationship_score: foreign key / association evidence

    Returns:
        {"semantic_type": str, "confidence": float, "semantic_evidence": {...}}
    """
    normalized = re.sub(r"[_\s-]+", "_", str(field_name or "").lower()).strip("_")
    scores: dict[str, float] = {
        "name_score": 0.0,
        "schema_score": 0.0,
        "description_score": 0.0,
        "operation_context_score": 0.0,
        "runtime_behavior_score": 0.0,
        "relationship_score": 0.0,
    }
    candidates: dict[str, float] = {}  # semantic_type -> accumulated score

    # ── Evidence 1: Name pattern ──
    for pattern, sem_type, base_score in _NAME_EVIDENCE:
        if re.search(pattern, normalized):
            scores["name_score"] = max(scores["name_score"], base_score)
            candidates[sem_type] = candidates.get(sem_type, 0.0) + base_score

    # ── Evidence 2: Schema constraints ──
    schema = schema_info or {}
    schema_type = str(schema.get("type") or "").lower()
    if schema.get("enum"):
        scores["schema_score"] = max(scores["schema_score"], 0.8)
        candidates["ENUM"] = candidates.get("ENUM", 0.0) + 0.8
    elif schema_type in ("integer", "number"):
        if schema.get("minimum") is not None or schema.get("maximum") is not None:
            scores["schema_score"] = max(scores["schema_score"], 0.6)
            candidates["LIMIT"] = candidates.get("LIMIT", 0.0) + 0.4
            candidates["QUANTITY"] = candidates.get("QUANTITY", 0.0) + 0.3
        else:
            scores["schema_score"] = max(scores["schema_score"], 0.3)
    elif schema_type == "boolean":
        scores["schema_score"] = max(scores["schema_score"], 0.7)
        candidates["BOOLEAN_FLAG"] = candidates.get("BOOLEAN_FLAG", 0.0) + 0.7
    elif schema_type == "string" and schema.get("format") in ("date-time", "date"):
        scores["schema_score"] = max(scores["schema_score"], 0.8)
        candidates["TIMESTAMP"] = candidates.get("TIMESTAMP", 0.0) + 0.8

    # ── Evidence 3: Description text ──
    desc_lower = str(description_text or "").lower()
    if desc_lower:
        _desc_signals: list[tuple[str, str, float]] = [
            (r"(余额|balance|可用|remaining)", "BALANCE", 0.6),
            (r"(状态|status|state|阶段|phase)", "STATE", 0.6),
            (r"(数量|quantity|库存|stock)", "QUANTITY", 0.5),
            (r"(金额|amount|价格|price|费用|fee)", "AMOUNT", 0.5),
            (r"(版本|version|乐观锁)", "VERSION", 0.6),
            (r"(幂等|idempoten|去重|dedup)", "IDEMPOTENCY_KEY", 0.7),
            (r"(租户|tenant|隔离|isolation)", "TENANT", 0.6),
            (r"(归属|owner|负责人|assignee)", "OWNER", 0.6),
        ]
        for pattern, sem_type, score in _desc_signals:
            if re.search(pattern, desc_lower):
                scores["description_score"] = max(scores["description_score"], score)
                candidates[sem_type] = candidates.get(sem_type, 0.0) + score

    # ── Evidence 4: Operation context ──
    op_ctx = operation_context or {}
    if op_ctx.get("is_write_target"):
        # Field appears in write body → likely mutable business field
        if schema_type in ("integer", "number"):
            candidates["QUANTITY"] = candidates.get("QUANTITY", 0.0) + 0.2
            scores["operation_context_score"] = max(scores["operation_context_score"], 0.3)
    if op_ctx.get("is_path_param"):
        candidates["IDENTITY"] = candidates.get("IDENTITY", 0.0) + 0.5
        scores["operation_context_score"] = max(scores["operation_context_score"], 0.5)
    if op_ctx.get("is_filter_param"):
        candidates["STATE"] = candidates.get("STATE", 0.0) + 0.2
        scores["operation_context_score"] = max(scores["operation_context_score"], 0.3)

    # ── Evidence 5: Runtime values ──
    if runtime_values:
        non_null = [v for v in runtime_values if v is not None]
        if non_null:
            if all(isinstance(v, bool) for v in non_null):
                candidates["BOOLEAN_FLAG"] = candidates.get("BOOLEAN_FLAG", 0.0) + 0.6
                scores["runtime_behavior_score"] = 0.6
            elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
                if len(set(non_null)) <= 5 and len(non_null) >= 3:
                    candidates["ENUM"] = candidates.get("ENUM", 0.0) + 0.4
                    scores["runtime_behavior_score"] = 0.4
                elif all(v >= 0 for v in non_null):
                    candidates["QUANTITY"] = candidates.get("QUANTITY", 0.0) + 0.3
                    scores["runtime_behavior_score"] = 0.3

    # ── Evidence 6: Relationships ──
    rels = relationships or []
    if any("foreign_key" in r or "references" in r for r in rels):
        candidates["FOREIGN_KEY"] = candidates.get("FOREIGN_KEY", 0.0) + 0.7
        scores["relationship_score"] = 0.7
    if any("owner" in r or "belongs_to" in r for r in rels):
        candidates["OWNER"] = candidates.get("OWNER", 0.0) + 0.5
        scores["relationship_score"] = max(scores["relationship_score"], 0.5)

    # ── Final decision ──
    if not candidates:
        return {
            "semantic_type": "UNKNOWN",
            "confidence": 0.0,
            "semantic_evidence": scores,
        }
    best_type = max(candidates, key=candidates.get)  # type: ignore[arg-type]
    raw_score = candidates[best_type]
    # Normalize: require at least 2 evidence sources for high confidence
    evidence_sources_active = sum(1 for v in scores.values() if v > 0)
    if evidence_sources_active >= 3:
        confidence = min(0.95, raw_score * 0.9)
    elif evidence_sources_active >= 2:
        confidence = min(0.85, raw_score * 0.75)
    else:
        confidence = min(0.60, raw_score * 0.5)  # single-source: capped low

    return {
        "semantic_type": best_type,
        "confidence": round(confidence, 3),
        "semantic_evidence": {k: round(v, 3) for k, v in scores.items()},
        "total_confidence": round(confidence, 3),
        "evidence_sources_active": evidence_sources_active,
    }


# ────────────────────────────────────────────────────────────
# Entity type taxonomy (generic — never industry-specific)
# ────────────────────────────────────────────────────────────

_GENERIC_ENTITY_TYPES: dict[str, list[str]] = {
    "generic_business_object": [
        "entity_type is generic_business_object",
        "Represents a core business domain object with identity and lifecycle.",
    ],
    "transaction_record": [
        "entity_type is transaction_record",
        "Represents a recorded business transaction with amounts, dates, and parties.",
    ],
    "workflow_instance": [
        "entity_type is workflow_instance",
        "Represents an active or completed process instance with state transitions.",
    ],
    "reference_data": [
        "entity_type is reference_data",
        "Represents lookup/reference data (codes, types, categories) with low churn.",
    ],
    "audit_log": [
        "entity_type is audit_log",
        "Represents an immutable audit trail or event log entry.",
    ],
    "document_record": [
        "entity_type is document_record",
        "Represents a stored document or attachment with metadata.",
    ],
    "party_record": [
        "entity_type is party_record",
        "Represents a person, organisation, or role participating in business flows.",
    ],
}

# Identity-field tokens we look for in schema property names
_IDENTITY_TOKENS: set[str] = {"id", "code", "number", "key", "identifier", "uuid", "ref", "reference"}

# State-field tokens
_STATE_TOKENS: set[str] = {
    "status", "status_code", "state", "lifecycle", "lifecycle_state",
    "phase", "stage", "condition", "disposition",
}

# Amount / quantity signals in property types
_AMOUNT_TYPES: set[str] = {"number", "integer"}
_QUANTITY_TYPES: set[str] = {"integer"}

# Tenant-field tokens
_TENANT_TOKENS: set[str] = {"tenant", "tenant_id", "org", "organization", "company", "client_id"}

# Version-field tokens
_VERSION_TOKENS: set[str] = {"version", "revision", "etag", "row_version", "concurrency_token"}

# PRD heading pattern (markdown headings)
_HEADING_PATTERN: re.Pattern = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

# Noun-phrase extractor (simple: capitalised multi-word phrases from headings)
_NOUN_PHRASE_PATTERN: re.Pattern = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b"
)
# Chinese entity extraction: matches 2-4 character compound nouns
# e.g., 订单, 支付系统, 库存管理
_CN_ENTITY_PATTERN: re.Pattern = re.compile(
    r'[\u4e00-\u9fff]{2,6}(?:系统|管理|模块|服务|中心|平台|引擎)?'
)
# Common Chinese stopwords that are not entities
_CN_STOP_WORDS = frozenset({
    '系统', '管理', '模块', '服务', '中心', '平台', '引擎',
    '包括', '如下', '所有', '每个', '一个', '这个', '那个',
    '功能', '需求', '说明', '文档', '概述', '背景', '目标',
})

# Confidence weights by source strength
_CONFIDENCE_BY_SOURCE: dict[str, float] = {
    "explicit_heading": 0.90,
    "schema_required_field": 0.85,
    "schema_optional_field": 0.65,
    "api_path_segment": 0.70,
    "prd_body_text": 0.50,
    "inferred_from_relation": 0.35,
    "heuristic_guess": 0.20,
}


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _make_evidence(
    source: str,
    section: str,
    extracted: str,
    line: int | None = None,
) -> dict:
    """Build a standard evidence record."""
    record: dict[str, Any] = {
        "source_document": source,
        "section": section,
        "extracted_text": extracted,
    }
    if line is not None:
        record["line"] = line
    return record


def _classify_entity_type(entity_name: str, schema_properties: dict | None = None) -> str:
    """Heuristically classify an entity into a generic type.

    Uses field composition and name clues — never industry-specific labels.
    """
    name_lower = entity_name.lower()

    # Strong signals: has amount fields + identity → transaction_record
    has_amount = False
    has_state = False
    has_identity = False

    if schema_properties:
        for prop_name, prop_schema in schema_properties.items():
            pn = prop_name.lower()
            ptype = prop_schema.get("type", "") if isinstance(prop_schema, dict) else ""
            if any(tok in pn for tok in _IDENTITY_TOKENS):
                has_identity = True
            if any(tok in pn for tok in _STATE_TOKENS):
                has_state = True
            if ptype in _AMOUNT_TYPES:
                has_amount = True

    # Name-based heuristics (weak, only used when schema is unavailable)
    transaction_keywords = {"order", "payment", "invoice", "transaction", "charge", "sale", "purchase", "transfer"}
    workflow_keywords = {"process", "workflow", "job", "task", "request", "approval", "claim", "case"}
    reference_keywords = {"type", "code", "category", "lookup", "master", "reference", "catalog", "list"}
    audit_keywords = {"log", "audit", "event", "history", "trace", "entry"}
    document_keywords = {"document", "file", "attachment", "record", "note", "memo"}
    party_keywords = {"user", "customer", "client", "vendor", "supplier", "partner", "agent", "employee", "person", "contact", "account"}

    if has_amount and has_identity:
        return "transaction_record"
    if has_state and has_identity:
        return "workflow_instance"
    if any(kw in name_lower for kw in transaction_keywords):
        return "transaction_record"
    if any(kw in name_lower for kw in workflow_keywords):
        return "workflow_instance"
    if any(kw in name_lower for kw in audit_keywords):
        return "audit_log"
    if any(kw in name_lower for kw in document_keywords):
        return "document_record"
    if any(kw in name_lower for kw in reference_keywords):
        return "reference_data"
    if any(kw in name_lower for kw in party_keywords):
        return "party_record"

    return "generic_business_object"


def _classify_capability(method: str, path: str, operation: dict) -> str:
    """Classify an API operation into a capability category."""
    method_lower = method.lower()
    op_id = (operation.get("operationId") or "").lower()
    summary = (operation.get("summary") or "").lower()

    if method_lower == "get":
        # Check if it's a list (collection) or read (single item)
        if path.rstrip("/").endswith("}") or "{" in path:
            return "read"
        # Check for list-like indicators
        list_signals = {"list", "search", "query", "all", "find", "browse", "index", "get_all", "getall"}
        if any(s in op_id for s in list_signals) or any(s in summary for s in list_signals):
            return "list"
        return "list"  # default GET on collection = list

    if method_lower == "post":
        action_signals = {"process", "execute", "run", "trigger", "send", "submit", "approve", "reject", "cancel", "close", "activate", "deactivate", "publish", "archive"}
        if any(s in op_id for s in action_signals) or any(s in summary for s in action_signals):
            return "action"
        return "create"

    if method_lower == "put":
        return "update"
    if method_lower == "patch":
        return "update"
    if method_lower == "delete":
        return "delete"
    if method_lower == "head":
        return "read"
    if method_lower == "options":
        return "unknown"

    return "unknown"


def _extract_entity_from_path(path: str) -> str | None:
    """Extract a likely entity name from a URL path segment.

    e.g. /api/v1/purchase-orders/{id} → purchase_orders
         /api/items → items
    """
    # Split path and find the last non-parameter segment
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    # Skip common prefixes
    skip = {"api", "v1", "v2", "v3", "v4", "rest", "public", "private"}
    filtered = [s for s in segments if s.lower() not in skip]
    if filtered:
        return filtered[-1].replace("-", "_")
    return None


# ────────────────────────────────────────────────────────────
# Compiler
# ────────────────────────────────────────────────────────────


class ProjectContextCompiler:
    """Compiles a ProjectContext from PRD, OpenAPI, API docs, and data dictionary.

    Usage:
        compiler = ProjectContextCompiler()
        ctx = compiler.compile(
            prd_text=open("prd.md").read(),
            openapi_spec=json.load(open("openapi.json")),
            api_docs_text=open("api_docs.md").read(),
            data_dictionary={...},
        )
        print(json.dumps(compiler.to_dict(ctx), indent=2))
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(
        self,
        prd_text: str,
        openapi_spec: dict,
        api_docs_text: str = "",
        data_dictionary: dict | None = None,
        fixtures: dict | None = None,
        existing_context: ProjectContext | None = None,
    ) -> ProjectContext:
        """Compile a complete ProjectContext from source documents.

        Args:
            prd_text: PRD markdown text.
            openapi_spec: OpenAPI/Swagger spec as a dict.
            api_docs_text: Optional API documentation markdown.
            data_dictionary: Optional data dictionary (entity → field definitions).
            fixtures: Optional existing fixture definitions.
            existing_context: Optional previous context to merge/rebase.

        Returns:
            A fully populated ProjectContext.
        """
        data_dictionary = data_dictionary or {}
        project_id = self._derive_project_id(prd_text, openapi_spec, existing_context)

        source_docs: list[dict] = []
        entities: list[EntityCandidate] = []
        relations: list[RelationCandidate] = []
        apis: list[APICapability] = []

        # Track source documents
        if prd_text:
            source_docs.append({"type": "prd", "size_bytes": len(prd_text), "format": "markdown"})
        if openapi_spec:
            source_docs.append({"type": "openapi", "size_bytes": len(json.dumps(openapi_spec)), "format": "json"})
        if api_docs_text:
            source_docs.append({"type": "api_docs", "size_bytes": len(api_docs_text), "format": "markdown"})
        if data_dictionary:
            source_docs.append({"type": "data_dictionary", "size_bytes": len(json.dumps(data_dictionary)), "format": "json"})

        # Phase 1: Entity inference from PRD headings
        prd_entities = self._infer_entities_from_prd(prd_text)
        entities.extend(prd_entities)

        # Phase 2: Entity inference from OpenAPI schemas
        schema_entities = self._infer_entities_from_openapi_schemas(openapi_spec)
        entities = self._merge_entities(entities, schema_entities)

        # Phase 3: Entity enrichment from data dictionary
        if data_dictionary:
            dd_entities = self._infer_entities_from_data_dictionary(data_dictionary)
            entities = self._merge_entities(entities, dd_entities)

        # Phase 4: API capability extraction
        apis = self._extract_api_capabilities(openapi_spec, entities)

        # Phase 5: Relation inference
        relations = self._infer_relations(entities, apis, prd_text, openapi_spec)

        # Phase 6: Observer candidates (read-only endpoints)
        observers = self._infer_observers(apis, entities)

        # Phase 7: Binding candidates (action → entity bindings)
        bindings = self._infer_bindings(apis, entities, openapi_spec)

        # Phase 8: Lifecycle transitions & invariants
        lifecycle = self._infer_lifecycle_transitions(entities, prd_text, openapi_spec)
        invariants = self._infer_invariants(entities, prd_text)

        # Phase 9: Fixture readiness assessment
        fixture_list = self._assess_fixtures(fixtures or {}, entities, apis)

        # Phase 10: Coverage & gaps
        coverage, gaps = self._compute_coverage_and_gaps(entities, apis, relations, observers, bindings)

        compiled_at = datetime.now(timezone.utc).isoformat()

        ctx = ProjectContext(
            project_id=project_id,
            compiled_at=compiled_at,
            source_documents=source_docs,
            entities=entities,
            relations=relations,
            apis=apis,
            observers=observers,
            bindings=bindings,
            candidate_lifecycle_transitions=lifecycle,
            candidate_invariants=invariants,
            fixtures=fixture_list,
            coverage=coverage,
            gaps=gaps,
            version=(existing_context.version + 1) if existing_context else 1,
        )

        return ctx

    def diff(self, old: ProjectContext, new: ProjectContext) -> dict:
        """Compute a structural diff between two ProjectContext objects."""
        result: dict[str, Any] = {
            "project_id": new.project_id,
            "diff_at": datetime.now(timezone.utc).isoformat(),
            "old_compiled_at": old.compiled_at,
            "new_compiled_at": new.compiled_at,
            "version": f"{old.version} → {new.version}",
            "changes": {},
        }

        # Entities
        old_aliases = {e.entity_alias for e in old.entities}
        new_aliases = {e.entity_alias for e in new.entities}
        result["changes"]["entities"] = {
            "added": sorted(new_aliases - old_aliases),
            "removed": sorted(old_aliases - new_aliases),
            "kept": sorted(old_aliases & new_aliases),
        }

        # APIs
        old_paths = {f"{a.method.upper()} {a.path}" for a in old.apis}
        new_paths = {f"{a.method.upper()} {a.path}" for a in new.apis}
        result["changes"]["apis"] = {
            "added": sorted(new_paths - old_paths),
            "removed": sorted(old_paths - new_paths),
            "kept": sorted(old_paths & new_paths),
        }

        # Relations
        old_rels = {f"{r.from_entity} → {r.to_entity} ({r.relation_type})" for r in old.relations}
        new_rels = {f"{r.from_entity} → {r.to_entity} ({r.relation_type})" for r in new.relations}
        result["changes"]["relations"] = {
            "added": sorted(new_rels - old_rels),
            "removed": sorted(old_rels - new_rels),
        }

        # Observers
        result["changes"]["observers"] = {
            "old_count": len(old.observers),
            "new_count": len(new.observers),
        }

        # Bindings
        result["changes"]["bindings"] = {
            "old_count": len(old.bindings),
            "new_count": len(new.bindings),
        }

        # Coverage delta
        result["changes"]["coverage_delta"] = {
            "old_entity_count": len(old.entities),
            "new_entity_count": len(new.entities),
            "old_api_count": len(old.apis),
            "new_api_count": len(new.apis),
        }

        return result

    def to_dict(self, ctx: ProjectContext) -> dict:
        """Serialize a ProjectContext to a JSON-compatible dict."""

        def _dataclass_to_dict(obj: Any) -> Any:
            if hasattr(obj, "__dataclass_fields__"):
                result: dict[str, Any] = {}
                for fld in obj.__dataclass_fields__:
                    value = getattr(obj, fld)
                    if isinstance(value, list):
                        result[fld] = [
                            _dataclass_to_dict(v) if hasattr(v, "__dataclass_fields__") else v
                            for v in value
                        ]
                    elif hasattr(value, "__dataclass_fields__"):
                        result[fld] = _dataclass_to_dict(value)
                    else:
                        result[fld] = value
                return result
            return obj

        return _dataclass_to_dict(ctx)

    def from_dict(self, data: dict) -> ProjectContext:
        """Deserialize a dict into a ProjectContext."""

        def _build_entity(d: dict) -> EntityCandidate:
            return EntityCandidate(
                entity_alias=d["entity_alias"],
                entity_type=d.get("entity_type", "generic_business_object"),
                identity_fields=d.get("identity_fields", []),
                state_fields=d.get("state_fields", []),
                amount_fields=d.get("amount_fields", []),
                quantity_fields=d.get("quantity_fields", []),
                tenant_fields=d.get("tenant_fields", []),
                version_fields=d.get("version_fields", []),
                api_sources=d.get("api_sources", []),
                confidence=d.get("confidence", 0.0),
                evidence=d.get("evidence", []),
                human_confirmation_required=d.get("human_confirmation_required", True),
            )

        def _build_relation(d: dict) -> RelationCandidate:
            return RelationCandidate(
                from_entity=d["from_entity"],
                to_entity=d["to_entity"],
                relation_type=d.get("relation_type", "unknown"),
                confidence=d.get("confidence", 0.0),
                evidence=d.get("evidence", []),
                human_confirmation_required=d.get("human_confirmation_required", True),
            )

        def _build_api(d: dict) -> APICapability:
            return APICapability(
                path=d["path"],
                method=d["method"],
                capability=d.get("capability", "unknown"),
                entity_alias=d.get("entity_alias", ""),
                entity=d.get("entity", ""),
                entity_id_param=d.get("entity_id_param", ""),
                operation_id=d.get("operation_id", ""),
                summary=d.get("summary", ""),
                description=d.get("description", ""),
                tags=d.get("tags", []),
                is_observer_candidate=d.get("is_observer_candidate", False),
                is_action_candidate=d.get("is_action_candidate", False),
                has_entity_id=d.get("has_entity_id", False),
                has_entity_id_in_path=d.get("has_entity_id_in_path", False),
                has_entity_id_in_response=d.get("has_entity_id_in_response", False),
                has_correlation_id=d.get("has_correlation_id", False),
                has_tenant_id=d.get("has_tenant_id", False),
                supports_pagination=d.get("supports_pagination", False),
                supports_filtering=d.get("supports_filtering", False),
                request_body_required=d.get("request_body_required", False),
                deprecated=d.get("deprecated", False),
                path_params=d.get("path_params", []),
                query_params=d.get("query_params", []),
                header_params=d.get("header_params", []),
                body_schema=d.get("body_schema"),
                response_schema=d.get("response_schema"),
                security=d.get("security", []),
                confidence=d.get("confidence", 0.0),
                evidence=d.get("evidence", []),
            )

        def _build_observer(d: dict) -> ObserverCandidate:
            return ObserverCandidate(
                observer_id=d.get("observer_id", str(uuid.uuid4())),
                entity_alias=d["entity_alias"],
                method=d.get("method", "GET"),
                path=d["path"],
                read_only_confidence=d.get("read_only_confidence", 0.0),
                projection=d.get("projection", {}),
                confidence=d.get("confidence", 0.0),
                evidence=d.get("evidence", []),
                requires_human_confirmation=d.get("requires_human_confirmation", True),
            )

        def _build_binding(d: dict) -> BindingCandidate:
            return BindingCandidate(
                action_step=d["action_step"],
                entity_alias=d["entity_alias"],
                entity_id_source=d.get("entity_id_source", ""),
                entity_id_path=d.get("entity_id_path", ""),
                entity_id_confidence=d.get("entity_id_confidence", 0.0),
                correlation_id_source=d.get("correlation_id_source", ""),
                correlation_id_path=d.get("correlation_id_path", ""),
                confidence=d.get("confidence", 0.0),
                evidence=d.get("evidence", []),
            )

        return ProjectContext(
            project_id=data["project_id"],
            compiled_at=data.get("compiled_at", ""),
            source_documents=data.get("source_documents", []),
            entities=[_build_entity(e) for e in data.get("entities", [])],
            relations=[_build_relation(r) for r in data.get("relations", [])],
            apis=[_build_api(a) for a in data.get("apis", [])],
            observers=[_build_observer(o) for o in data.get("observers", [])],
            bindings=[_build_binding(b) for b in data.get("bindings", [])],
            candidate_lifecycle_transitions=data.get("candidate_lifecycle_transitions", []),
            candidate_invariants=data.get("candidate_invariants", []),
            fixtures=data.get("fixtures", []),
            coverage=data.get("coverage", {}),
            gaps=data.get("gaps", []),
            version=data.get("version", 1),
        )

    # ------------------------------------------------------------------
    # Entity inference
    # ------------------------------------------------------------------

    def _derive_project_id(
        self,
        prd_text: str,
        openapi_spec: dict,
        existing_context: ProjectContext | None,
    ) -> str:
        """Derive a stable project ID from source content or existing context."""
        if existing_context and existing_context.project_id:
            return existing_context.project_id
        # Hash the OpenAPI info title + version if available
        info = openapi_spec.get("info", {})
        title = info.get("title", "")
        version_str = info.get("version", "")
        if title:
            raw = f"{title}:{version_str}"
            return f"proj-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"
        # Fallback: hash PRD first 1KB
        raw = prd_text[:1024] if prd_text else str(uuid.uuid4())
        return f"proj-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _infer_entities_from_prd(self, prd_text: str) -> list[EntityCandidate]:
        """Extract entity candidates from PRD markdown headings and body text."""
        if not prd_text:
            return []

        entities: list[EntityCandidate] = []
        seen: set[str] = set()

        # Extract headings as entity name sources
        for match in _HEADING_PATTERN.finditer(prd_text):
            heading = match.group(1).strip()
            line_number = prd_text[: match.start()].count("\n") + 1

            # Extract noun phrases from the heading (English + Chinese)
            phrases = _NOUN_PHRASE_PATTERN.findall(heading)
            cn_phrases = [m.group(0) for m in _CN_ENTITY_PATTERN.finditer(heading)
                          if m.group(0) not in _CN_STOP_WORDS]
            for phrase in phrases:
                alias = phrase.lower().replace(" ", "_").replace("-", "_")
                skip_words = {"overview", "introduction", "summary", "background", "appendix",
                              "requirements", "assumptions", "scope", "glossary", "setup",
                              "installation", "deployment", "conclusion", "references",
                              "the", "this", "that", "and", "for", "with", "from"}
                if alias in skip_words or len(alias) < 3:
                    continue
                if alias in seen:
                    continue
                seen.add(alias)
                entity_type = _classify_entity_type(phrase)
                entities.append(EntityCandidate(
                    entity_alias=alias, entity_type=entity_type,
                    confidence=_CONFIDENCE_BY_SOURCE["explicit_heading"],
                    source_documents=[SourceRef(source="prd", line=line_number, excerpt=heading[:200],
                                              confidence=_CONFIDENCE_BY_SOURCE["explicit_heading"])],
                ))
            # Chinese entity extraction
            for cn_phrase in cn_phrases:
                alias = cn_phrase.lower().replace(" ", "_")
                if alias in seen or len(alias) < 2:
                    continue
                seen.add(alias)
                entity_type = _classify_entity_type(cn_phrase)
                entities.append(EntityCandidate(
                    entity_alias=alias, entity_type=entity_type,
                    confidence=_CONFIDENCE_BY_SOURCE["explicit_heading"],
                    source_documents=[SourceRef(source="prd_cn", line=line_number, excerpt=heading[:200],
                                              confidence=0.80)],
                ))


        return entities

    def _infer_entities_from_openapi_schemas(self, spec: dict) -> list[EntityCandidate]:
        """Extract entity candidates from OpenAPI schema definitions."""
        entities: list[EntityCandidate] = []
        schemas = spec.get("components", {}).get("schemas", {})

        for schema_name, schema_def in schemas.items():
            if not isinstance(schema_def, dict):
                continue
            props = schema_def.get("properties", {})
            if not props:
                continue

            alias = schema_name.lower().replace(" ", "_")
            entity_type = _classify_entity_type(schema_name, props)

            identity_fields: list[str] = []
            state_fields: list[str] = []
            amount_fields: list[str] = []
            quantity_fields: list[str] = []
            tenant_fields: list[str] = []
            version_fields: list[str] = []

            for prop_name, prop_schema in props.items():
                pn = prop_name.lower()
                ptype = prop_schema.get("type", "") if isinstance(prop_schema, dict) else ""

                if any(tok in pn for tok in _IDENTITY_TOKENS):
                    identity_fields.append(prop_name)
                if any(tok in pn for tok in _STATE_TOKENS):
                    state_fields.append(prop_name)
                if ptype == "number":
                    amount_fields.append(prop_name)
                if ptype == "integer":
                    quantity_fields.append(prop_name)
                if any(tok in pn for tok in _TENANT_TOKENS):
                    tenant_fields.append(prop_name)
                if any(tok in pn for tok in _VERSION_TOKENS):
                    version_fields.append(prop_name)

            # Determine confidence
            required = schema_def.get("required", [])
            has_required = bool(required)
            confidence = (
                _CONFIDENCE_BY_SOURCE["schema_required_field"]
                if has_required
                else _CONFIDENCE_BY_SOURCE["schema_optional_field"]
            )

            entities.append(EntityCandidate(
                entity_alias=alias,
                entity_type=entity_type,
                identity_fields=identity_fields,
                state_fields=state_fields,
                amount_fields=amount_fields,
                quantity_fields=quantity_fields,
                tenant_fields=tenant_fields,
                version_fields=version_fields,
                api_sources=[],
                confidence=confidence,
                evidence=[
                    _make_evidence(
                        source="openapi_spec",
                        section=f"components/schemas/{schema_name}",
                        extracted=f"Schema {schema_name} with {len(props)} properties",
                    )
                ],
                human_confirmation_required=True,
            ))

        return entities

    def _infer_entities_from_data_dictionary(self, dd: dict) -> list[EntityCandidate]:
        """Extract entity candidates from a data dictionary."""
        entities: list[EntityCandidate] = []
        for entity_name, fields in dd.items():
            alias = entity_name.lower().replace(" ", "_")
            field_list: list[str] = []
            identity_fields: list[str] = []
            state_fields: list[str] = []
            amount_fields: list[str] = []
            quantity_fields: list[str] = []

            if isinstance(fields, list):
                field_list = [f if isinstance(f, str) else f.get("name", "") for f in fields]
            elif isinstance(fields, dict):
                field_list = list(fields.keys())

            for fname in field_list:
                fn = fname.lower()
                if any(tok in fn for tok in _IDENTITY_TOKENS):
                    identity_fields.append(fname)
                if any(tok in fn for tok in _STATE_TOKENS):
                    state_fields.append(fname)
                if any(tok in fn for tok in _TENANT_TOKENS):
                    pass  # tenant tagging
                # Heuristic: fields with "amount", "price", "total", "sum" → amount
                if any(kw in fn for kw in ("amount", "price", "total", "sum", "value", "balance", "cost", "fee")):
                    amount_fields.append(fname)
                if any(kw in fn for kw in ("quantity", "qty", "count", "units")):
                    quantity_fields.append(fname)

            entities.append(EntityCandidate(
                entity_alias=alias,
                entity_type=_classify_entity_type(entity_name),
                identity_fields=identity_fields,
                state_fields=state_fields,
                amount_fields=amount_fields,
                quantity_fields=quantity_fields,
                tenant_fields=[],
                version_fields=[],
                api_sources=[],
                confidence=_CONFIDENCE_BY_SOURCE["prd_body_text"],
                evidence=[
                    _make_evidence(
                        source="data_dictionary",
                        section=entity_name,
                        extracted=f"Data dictionary entry for {entity_name}",
                    )
                ],
                human_confirmation_required=True,
            ))

        return entities

    def _merge_entities(
        self, existing: list[EntityCandidate], new: list[EntityCandidate]
    ) -> list[EntityCandidate]:
        """Merge new entity candidates into existing, deduplicating by alias.

        When a conflict is found, the higher-confidence entity wins.
        """
        merged: dict[str, EntityCandidate] = {e.entity_alias: e for e in existing}

        for candidate in new:
            if candidate.entity_alias in merged:
                old = merged[candidate.entity_alias]
                # Merge fields (union of lists)
                old.identity_fields = sorted(set(old.identity_fields) | set(candidate.identity_fields))
                old.state_fields = sorted(set(old.state_fields) | set(candidate.state_fields))
                old.amount_fields = sorted(set(old.amount_fields) | set(candidate.amount_fields))
                old.quantity_fields = sorted(set(old.quantity_fields) | set(candidate.quantity_fields))
                old.tenant_fields = sorted(set(old.tenant_fields) | set(candidate.tenant_fields))
                old.version_fields = sorted(set(old.version_fields) | set(candidate.version_fields))
                old.api_sources = sorted(set(old.api_sources) | set(candidate.api_sources))
                old.evidence.extend(candidate.evidence)
                # Confidence: take max
                old.confidence = max(old.confidence, candidate.confidence)
                # Keep entity_type from higher-confidence source
                if candidate.confidence > old.confidence:
                    old.entity_type = candidate.entity_type
            else:
                merged[candidate.entity_alias] = candidate

        return list(merged.values())

    # ------------------------------------------------------------------
    # API capability extraction
    # ------------------------------------------------------------------

    def _extract_api_capabilities(
        self, spec: dict, entities: list[EntityCandidate]
    ) -> list[APICapability]:
        """Extract API capabilities from OpenAPI paths."""
        apis: list[APICapability] = []
        entity_map: dict[str, EntityCandidate] = {e.entity_alias: e for e in entities}
        paths = spec.get("paths", {})

        def _extract_content_schema(content: dict | None) -> dict | None:
            if not isinstance(content, dict):
                return None
            preferred = (
                "application/json",
                "application/problem+json",
                "application/vnd.api+json",
            )
            for media_type in preferred:
                media = content.get(media_type)
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    return deepcopy(media["schema"])
            for media in content.values():
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    return deepcopy(media["schema"])
            return None

        def _extract_response_schema(operation: dict) -> dict | None:
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                return None
            preferred_codes = (
                "200",
                "201",
                "202",
                "203",
                "204",
                "default",
            )
            for code in preferred_codes:
                response = responses.get(code)
                if isinstance(response, dict):
                    schema = _extract_content_schema(response.get("content"))
                    if isinstance(schema, dict):
                        return schema
            for response in responses.values():
                if isinstance(response, dict):
                    schema = _extract_content_schema(response.get("content"))
                    if isinstance(schema, dict):
                        return schema
            return None

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                operation = path_item.get(method)
                if not operation:
                    continue
                if not isinstance(operation, dict):
                    continue

                capability = _classify_capability(method, path, operation)
                entity_alias = _extract_entity_from_path(path) or "unknown"
                parameters = [p for p in operation.get("parameters", []) if isinstance(p, dict)]
                request_body = operation.get("requestBody") if isinstance(operation.get("requestBody"), dict) else {}
                request_body_content = request_body.get("content") if isinstance(request_body, dict) else {}
                body_schema = _extract_content_schema(request_body_content)
                response_schema = _extract_response_schema(operation)
                summary = str(operation.get("summary") or "").strip()
                description = str(operation.get("description") or "").strip()
                tags = [str(tag) for tag in (operation.get("tags") or []) if str(tag).strip()]
                security = deepcopy(
                    operation.get("security")
                    or path_item.get("security")
                    or spec.get("security")
                    or []
                )
                path_params = [deepcopy(p) for p in parameters if str(p.get("in") or "").lower() == "path"]
                query_params = [deepcopy(p) for p in parameters if str(p.get("in") or "").lower() == "query"]
                header_params = [deepcopy(p) for p in parameters if str(p.get("in") or "").lower() == "header"]

                # Check if this API corresponds to a known entity
                is_observer = capability in ("read", "list") and entity_alias in entity_map
                is_action = capability in ("create", "update", "delete", "action")

                # Check for entity ID in path or parameters
                has_entity_id = "{" in path or any(
                    p.get("name", "").lower() in _IDENTITY_TOKENS
                    for p in parameters
                )
                has_entity_id_in_path = "{" in path
                has_entity_id_in_response = any(
                    isinstance(response_schema.get("properties", {}).get(name), dict)
                    for name in (response_schema.get("properties", {}) if isinstance(response_schema, dict) else {})
                    if str(name).lower() in _IDENTITY_TOKENS
                ) if isinstance(response_schema, dict) else False

                # Check for correlation ID
                has_correlation_id = any(
                    "correlation" in (p.get("name", "") or "").lower()
                    for p in parameters
                )

                # Check for tenant ID
                has_tenant_id = any(
                    any(tok in ((p.get("name") or "").lower()) for tok in _TENANT_TOKENS)
                    for p in parameters
                )

                apis.append(APICapability(
                    path=path,
                    method=method.upper(),
                    capability=capability,
                    entity_alias=entity_alias,
                    operation_id=str(operation.get("operationId") or ""),
                    summary=summary,
                    description=description,
                    tags=tags,
                    is_observer_candidate=is_observer,
                    is_action_candidate=is_action,
                    has_entity_id=has_entity_id,
                    has_entity_id_in_path=has_entity_id_in_path,
                    has_entity_id_in_response=has_entity_id_in_response,
                    has_correlation_id=has_correlation_id,
                    has_tenant_id=has_tenant_id,
                    supports_pagination=any(
                        str(p.get("name") or "").lower() in {"page", "page_size", "limit", "offset"}
                        for p in query_params
                    ),
                    supports_filtering=any(
                        str(p.get("name") or "").lower() not in {"page", "page_size", "limit", "offset", "sort"}
                        for p in query_params
                    ),
                    request_body_required=bool(request_body.get("required")),
                    deprecated=bool(operation.get("deprecated")),
                    path_params=path_params,
                    query_params=query_params,
                    header_params=header_params,
                    body_schema=body_schema,
                    response_schema=response_schema,
                    security=security if isinstance(security, list) else [],
                    confidence=_CONFIDENCE_BY_SOURCE["api_path_segment"],
                    evidence=[
                        _make_evidence(
                            source="openapi_spec",
                            section=f"paths/{path}/{method}",
                            extracted=f"{method.upper()} {path} → {capability}",
                        )
                    ],
                ))

        # Link APIs to entities
        for api in apis:
            if api.entity_alias in entity_map:
                ent = entity_map[api.entity_alias]
                if api.path not in ent.api_sources:
                    ent.api_sources.append(api.path)

        return apis

    # ------------------------------------------------------------------
    # Relation inference
    # ------------------------------------------------------------------

    def _infer_relations(
        self,
        entities: list[EntityCandidate],
        apis: list[APICapability],
        prd_text: str,
        spec: dict,
    ) -> list[RelationCandidate]:
        """Infer entity relationships from schema references and PRD text."""
        relations: list[RelationCandidate] = []
        entity_names = {e.entity_alias for e in entities}
        seen: set[tuple[str, str, str]] = set()

        # From OpenAPI schema $ref references
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema_def in schemas.items():
            if not isinstance(schema_def, dict):
                continue
            alias_from = schema_name.lower().replace(" ", "_")
            if alias_from not in entity_names:
                continue
            props = schema_def.get("properties", {})
            for prop_name, prop_schema in props.items():
                if not isinstance(prop_schema, dict):
                    continue
                ref = prop_schema.get("$ref", "")
                if ref:
                    # Extract referenced schema name
                    ref_name = ref.split("/")[-1].lower().replace(" ", "_")
                    if ref_name in entity_names and ref_name != alias_from:
                        key = (alias_from, ref_name, "reference")
                        if key not in seen:
                            seen.add(key)
                            relations.append(RelationCandidate(
                                from_entity=alias_from,
                                to_entity=ref_name,
                                relation_type="reference",
                                confidence=_CONFIDENCE_BY_SOURCE["schema_required_field"],
                                evidence=[
                                    _make_evidence(
                                        source="openapi_spec",
                                        section=f"components/schemas/{schema_name}/properties/{prop_name}",
                                        extracted=f"$ref to {ref_name}",
                                    )
                                ],
                            ))

        # From nested API paths (e.g. /parent/{id}/children)
        paths = spec.get("paths", {})
        for path in paths:
            segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
            skip = {"api", "v1", "v2", "v3", "v4", "rest", "public", "private"}
            filtered = [s.replace("-", "_") for s in segments if s.lower() not in skip]
            if len(filtered) >= 2 and filtered[-1] in entity_names and filtered[-2] in entity_names:
                parent = filtered[-2]
                child = filtered[-1]
                key = (parent, child, "parent_child")
                if key not in seen:
                    seen.add(key)
                    relations.append(RelationCandidate(
                        from_entity=parent,
                        to_entity=child,
                        relation_type="parent_child",
                        confidence=_CONFIDENCE_BY_SOURCE["api_path_segment"],
                        evidence=[
                            _make_evidence(
                                source="openapi_spec",
                                section=f"paths/{path}",
                                extracted=f"Nested path: {parent} → {child}",
                            )
                        ],
                    ))

        # From PRD text: look for "relates to", "belongs to", "has many" patterns
        if prd_text:
            relation_patterns = [
                (r"(\w+)\s+(?:is related to|relates to|belongs to|is part of)\s+(\w+)", "reference"),
                (r"(\w+)\s+(?:has many|contains|owns|manages)\s+(\w+)", "parent_child"),
                (r"(\w+)\s+(?:triggers|creates|generates|produces)\s+(\w+)", "event_producer"),
                (r"(\w+)\s+(?:depends on|requires|needs)\s+(\w+)", "workflow_dependency"),
            ]
            for pattern, rel_type in relation_patterns:
                for m in re.finditer(pattern, prd_text, re.IGNORECASE):
                    from_raw = m.group(1).lower().replace(" ", "_")
                    to_raw = m.group(2).lower().replace(" ", "_")
                    if from_raw in entity_names and to_raw in entity_names and from_raw != to_raw:
                        key = (from_raw, to_raw, rel_type)
                        if key not in seen:
                            seen.add(key)
                            line_num = prd_text[: m.start()].count("\n") + 1
                            relations.append(RelationCandidate(
                                from_entity=from_raw,
                                to_entity=to_raw,
                                relation_type=rel_type,
                                confidence=_CONFIDENCE_BY_SOURCE["prd_body_text"],
                                evidence=[
                                    _make_evidence(
                                        source="prd",
                                        section=f"body text (line {line_num})",
                                        extracted=m.group(0),
                                        line=line_num,
                                    )
                                ],
                            ))

        return relations

    # ------------------------------------------------------------------
    # Observer inference
    # ------------------------------------------------------------------

    def _infer_observers(
        self, apis: list[APICapability], entities: list[EntityCandidate]
    ) -> list[ObserverCandidate]:
        """Infer observer candidates from read-only API endpoints."""
        observers: list[ObserverCandidate] = []
        entity_map = {e.entity_alias: e for e in entities}

        for api in apis:
            if api.capability in ("read", "list") and api.entity_alias in entity_map:
                observer_id = f"obs-{hashlib.sha256(f'{api.method}{api.path}'.encode()).hexdigest()[:8]}"
                observers.append(ObserverCandidate(
                    observer_id=observer_id,
                    entity_alias=api.entity_alias,
                    method=api.method,
                    path=api.path,
                    read_only_confidence=0.85 if api.capability == "read" else 0.70,
                    projection={"path": api.path, "method": api.method},
                    confidence=api.confidence,
                    evidence=api.evidence,
                    requires_human_confirmation=True,
                ))

        return observers

    # ------------------------------------------------------------------
    # Binding inference
    # ------------------------------------------------------------------

    def _infer_bindings(
        self, apis: list[APICapability], entities: list[EntityCandidate], spec: dict
    ) -> list[BindingCandidate]:
        """Infer binding candidates linking action steps to entities."""
        bindings: list[BindingCandidate] = []
        entity_map = {e.entity_alias: e for e in entities}

        for api in apis:
            if not api.is_action_candidate:
                continue
            if api.entity_alias not in entity_map:
                continue

            # Determine entity ID source
            entity_id_source = ""
            entity_id_path = ""
            # Check path parameters for entity ID
            path_segments = api.path.strip("/").split("/")
            for seg in path_segments:
                if seg.startswith("{") and seg.endswith("}"):
                    param_name = seg[1:-1].lower()
                    if any(tok in param_name for tok in _IDENTITY_TOKENS):
                        entity_id_path = seg
                        entity_id_source = "path_parameter"
                        break

            # Check for correlation ID
            corr_source = ""
            corr_path = ""
            paths = spec.get("paths", {})
            path_item = paths.get(api.path, {})
            operation = path_item.get(api.method.lower(), {})
            for param in operation.get("parameters", []):
                pname = (param.get("name") or "").lower()
                if "correlation" in pname:
                    corr_source = "header" if param.get("in") == "header" else "query_parameter"
                    corr_path = param.get("name", "")

            bindings.append(BindingCandidate(
                action_step=f"{api.method} {api.path}",
                entity_alias=api.entity_alias,
                entity_id_source=entity_id_source,
                entity_id_path=entity_id_path,
                entity_id_confidence=0.80 if entity_id_source else 0.30,
                correlation_id_source=corr_source,
                correlation_id_path=corr_path,
                confidence=api.confidence,
                evidence=api.evidence,
            ))

        return bindings

    # ------------------------------------------------------------------
    # Lifecycle and invariants
    # ------------------------------------------------------------------

    def _infer_lifecycle_transitions(
        self, entities: list[EntityCandidate], prd_text: str, spec: dict
    ) -> list[dict]:
        """Infer candidate lifecycle transitions from state fields and PRD text."""
        transitions: list[dict] = []

        for entity in entities:
            if not entity.state_fields:
                continue

            # Look for status-value patterns in PRD
            status_values: set[str] = set()
            if prd_text:
                for field in entity.state_fields:
                    # Find "status: X" or "state: X" patterns near the entity
                    pattern = re.compile(
                        rf"{re.escape(field)}[:\s]*(\w+)",
                        re.IGNORECASE,
                    )
                    for m in pattern.finditer(prd_text):
                        status_values.add(m.group(1).lower())

            if status_values:
                transitions.append({
                    "entity_alias": entity.entity_alias,
                    "state_field": entity.state_fields[0],
                    "observed_values": sorted(status_values),
                    "confidence": _CONFIDENCE_BY_SOURCE["prd_body_text"],
                    "evidence": f"Inferred from PRD state-field references for {entity.entity_alias}",
                    "human_confirmation_required": True,
                })
            else:
                transitions.append({
                    "entity_alias": entity.entity_alias,
                    "state_field": entity.state_fields[0],
                    "observed_values": [],
                    "confidence": _CONFIDENCE_BY_SOURCE["heuristic_guess"],
                    "evidence": f"State field '{entity.state_fields[0]}' detected in schema — values require human confirmation",
                    "human_confirmation_required": True,
                })

        return transitions

    def _infer_invariants(
        self, entities: list[EntityCandidate], prd_text: str
    ) -> list[dict]:
        """Infer candidate business invariants from PRD text."""
        invariants: list[dict] = []

        # Scan PRD for invariant-like language
        if prd_text:
            invariant_patterns = [
                (r"(?:must|shall|should|always|never|must not)\s+.+?(?:\.|;|\n)", "regulatory"),
                (r"(?:sum|total|balance)\s+of\s+.+?(?:\.|;|\n)", "mathematical"),
                (r"(?:unique|no duplicate|exactly one)\s+.+?(?:\.|;|\n)", "uniqueness"),
                (r"(?:before|after|prior)\s+.+?(?:\.|;|\n)", "temporal"),
            ]
            for pattern, category in invariant_patterns:
                for m in re.finditer(pattern, prd_text, re.IGNORECASE):
                    text = m.group(0).strip()
                    line_num = prd_text[: m.start()].count("\n") + 1
                    invariants.append({
                        "category": category,
                        "description": text,
                        "source_line": line_num,
                        "confidence": _CONFIDENCE_BY_SOURCE["prd_body_text"],
                        "human_confirmation_required": True,
                    })

        # Schema-level invariants (required fields, unique constraints)
        for entity in entities:
            if entity.identity_fields and entity.amount_fields:
                invariants.append({
                    "category": "schema_invariant",
                    "description": f"{entity.entity_alias} has both identity and amount fields — likely requires consistency between them",
                    "confidence": _CONFIDENCE_BY_SOURCE["schema_required_field"],
                    "human_confirmation_required": True,
                })

        return invariants

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    def _assess_fixtures(
        self, fixtures: dict, entities: list[EntityCandidate], apis: list[APICapability]
    ) -> list[dict]:
        """Assess fixture readiness for each entity."""
        result: list[dict] = []

        for entity in entities:
            entity_fixtures = fixtures.get(entity.entity_alias, [])
            has_identity = bool(entity.identity_fields)
            has_state = bool(entity.state_fields)
            has_api = any(a.entity_alias == entity.entity_alias for a in apis)

            missing: list[str] = []
            if not entity_fixtures:
                missing.append(f"No fixtures found for {entity.entity_alias}")
            if has_identity and not any(
                f.get("identity") for f in (entity_fixtures if isinstance(entity_fixtures, list) else [])
            ):
                missing.append(f"Identity fixture needed for {entity.entity_alias}")
            if has_state and not any(
                f.get("state") for f in (entity_fixtures if isinstance(entity_fixtures, list) else [])
            ):
                missing.append(f"State-based fixture needed for {entity.entity_alias}")

            readiness = "BLOCKED_BY_FIXTURE" if missing else ("READY" if not missing else "PARTIALLY_READY")
            if not has_api:
                readiness = "BLOCKED_BY_FIXTURE"
                missing.append(f"No API endpoint found for {entity.entity_alias}")

            result.append({
                "entity_alias": entity.entity_alias,
                "readiness": readiness,
                "missing_requirements": missing,
                "risk_level": "high" if not missing else "low",
                "auto_retryable": not missing,
                "has_existing_fixtures": bool(entity_fixtures),
            })

        return result

    # ------------------------------------------------------------------
    # Coverage and gaps
    # ------------------------------------------------------------------

    def _compute_coverage_and_gaps(
        self,
        entities: list[EntityCandidate],
        apis: list[APICapability],
        relations: list[RelationCandidate],
        observers: list[ObserverCandidate],
        bindings: list[BindingCandidate],
    ) -> tuple[dict, list[dict]]:
        """Compute coverage metrics and gap reports."""
        coverage: dict[str, Any] = {
            "total_entities": len(entities),
            "entities_with_identity": sum(1 for e in entities if e.identity_fields),
            "entities_with_state": sum(1 for e in entities if e.state_fields),
            "entities_with_amount": sum(1 for e in entities if e.amount_fields),
            "entities_with_api": sum(1 for e in entities if e.api_sources),
            "total_apis": len(apis),
            "apis_by_capability": {},
            "total_relations": len(relations),
            "total_observers": len(observers),
            "total_bindings": len(bindings),
        }

        for api in apis:
            coverage["apis_by_capability"][api.capability] = (
                coverage["apis_by_capability"].get(api.capability, 0) + 1
            )

        gaps: list[dict] = []

        # Entities without APIs
        for entity in entities:
            if not entity.api_sources:
                gaps.append({
                    "gap_id": f"gap-no-api-{entity.entity_alias}",
                    "category": "missing_endpoint",
                    "severity": "high",
                    "description": f"Entity '{entity.entity_alias}' has no known API endpoint",
                    "affected_entities": [entity.entity_alias],
                    "recommended_action": f"Check OpenAPI spec for endpoints serving {entity.entity_alias} or add to spec",
                })

        # Entities without identity fields
        for entity in entities:
            if not entity.identity_fields:
                gaps.append({
                    "gap_id": f"gap-no-identity-{entity.entity_alias}",
                    "category": "incomplete_schema",
                    "severity": "medium",
                    "description": f"Entity '{entity.entity_alias}' lacks identity fields — CRUD operations may be unreliable",
                    "affected_entities": [entity.entity_alias],
                    "recommended_action": f"Review schema for {entity.entity_alias} and identify unique key fields",
                })

        # Entities without observers
        observed_entities = {o.entity_alias for o in observers}
        for entity in entities:
            if entity.entity_alias not in observed_entities and entity.api_sources:
                gaps.append({
                    "gap_id": f"gap-no-observer-{entity.entity_alias}",
                    "category": "missing_endpoint",
                    "severity": "low",
                    "description": f"No read-only observer endpoint found for entity '{entity.entity_alias}'",
                    "affected_entities": [entity.entity_alias],
                    "recommended_action": f"Add a GET endpoint for {entity.entity_alias} in the API spec",
                })

        # APIs without entity mapping
        mapped_paths = {a.path for a in apis if a.entity_alias != "unknown"}
        for api in apis:
            if api.entity_alias == "unknown":
                gaps.append({
                    "gap_id": f"gap-unknown-entity-{hashlib.md5(api.path.encode()).hexdigest()[:6]}",
                    "category": "ambiguous_entity",
                    "severity": "medium",
                    "description": f"API {api.method} {api.path} could not be mapped to any known entity",
                    "affected_entities": [],
                    "recommended_action": f"Review {api.method} {api.path} and map to an entity manually",
                })

        return coverage, gaps
