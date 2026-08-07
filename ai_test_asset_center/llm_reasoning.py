from __future__ import annotations

"""
LLM-powered business reasoning layer for QualiBug.

This is the intelligence upgrade for the 17+ business reasoning engines.
Currently each engine uses regex/heuristic pattern matching. This module
adds LLM-powered semantic reasoning that:

1. Understands business semantics — not just field name matches
2. Reasons about cross-system state consistency
3. Discovers novel bug patterns beyond hardcoded dictionaries
4. Falls back to existing heuristic logic when LLM is unavailable

Architecture:
- Each engine calls `reason(context, engine_type)` to get LLM-powered findings
- If LLM is available → returns semantically reasoned results
- If not → returns None, engine falls through to existing heuristic path
- This is a pure addition — no existing code paths are broken
"""

import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from hashlib import sha256
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aitestops.env_loader import load_dotenv

_llm_logger = logging.getLogger("qualibug.llm")

# ---------------------------------------------------------------------------
# Engine types — each maps to one or more existing reasoning modules
# ---------------------------------------------------------------------------

EngineType = Literal[
    "causality",            # business_causality_conservation — financial bugs
    "reconciliation",       # business_reconciliation — cross-view drift
    "invariant",            # business_invariant_mining — schema contract violations
    "counterexample",       # counterexample_discovery — semantic contradictions
    "lifecycle",            # business_lifecycle_reasoning — state machine bugs
    "saga",                 # business_saga_compensation_reasoning — missing compensation
    "consistency",          # consistency_isolation_reasoning — tenant/read-model drift
    "event_chain",          # business_event_chain_reasoning — ordering/duplication
    "population",           # business_population_constraints — capacity/limit bugs
    "outcome",              # business_outcome_validation — end-to-end result drift
    "metamorphic",          # metamorphic_differential_reasoning — differential behavior
    "temporal",             # temporal_data_regression_reasoning — historical data drift
    "assurance",            # business_assurance_coverage — coverage gap reasoning
    "adaptation",           # business_adaptation_layer — industry-specific reasoning
    "defect_classification",# confirmed_bug_flywheel — bug pattern learning
    "multi_source",         # multisource_reasoning — cross-source evidence synthesis
    "multi_industry",       # multi_industry_business_reasoning — industry inference
    "oracle_compiler",      # LLM proposes evidence-first, read-only Oracle hypotheses
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ReasoningConfig:
    """LLM configuration for business reasoning. Reads from same env vars as
    the rest of QualiBug (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)."""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    model_light: str = ""
    temperature: float = 0.1
    timeout_seconds: int = 120
    max_tokens: int = 4096
    thinking_mode: str = ""
    response_format: str = ""
    embedding_model: str = ""

    @classmethod
    def from_env(cls) -> "ReasoningConfig":
        load_dotenv()
        thinking_mode = os.getenv("LLM_THINKING_MODE", "").strip().lower()
        if thinking_mode not in {"enabled", "disabled"}:
            thinking_mode = ""
        response_format = os.getenv("LLM_RESPONSE_FORMAT", "").strip().lower()
        if response_format not in {"json_object"}:
            response_format = ""
        return cls(
            base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            model_light=os.getenv("LLM_MODEL_LIGHT", "").strip(),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            thinking_mode=thinking_mode,
            response_format=response_format,
            embedding_model=os.getenv("LLM_EMBEDDING_MODEL", "").strip(),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


# ---------------------------------------------------------------------------
# System prompt — shared across all reasoning types
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an enterprise business-quality reasoning engine. Your job is to find
high-value defects that ordinary endpoint tests miss: cross-system state drift,
cross-view reconciliation errors, financial conservation violations, tenant
isolation failures, lifecycle regressions, and missing compensation logic.

Rules:
- Output ONLY valid JSON. No markdown, no explanation outside the JSON.
- Never fabricate findings. If evidence is insufficient, report "insufficient_evidence".
- Field names in findings must reference actual schema fields from the provided context.
- Severity: P0=data loss/money leak, P1=business rule violation, P2=consistency gap, P3=minor.
- Be specific: cite exact field paths, expected vs actual semantics, and the business rule violated.
- Think like an auditor, not a test script generator.
- Your output is advisory only. It never confirms a defect and must never claim
  that deterministic evidence exists unless the provided context contains it.
"""

# ---------------------------------------------------------------------------
# Three-layer prompt imports (Phase61+ moat upgrade)
# ---------------------------------------------------------------------------

from .reader_prompt import READER_SYSTEM_PROMPT, READER_PROMPTS
from .reasoner_prompt import REASONER_SYSTEM_PROMPT, REASONER_PROMPTS
from .verifier_prompt import VERIFIER_SYSTEM_PROMPT, VERIFIER_PROMPTS

from .reasoning_fact_retrieval import FACT_BLOCK_HEADER

# Layer-aware system prompts
LAYERED_SYSTEM_PROMPTS = {
    "reader": READER_SYSTEM_PROMPT,
    "reasoner": REASONER_SYSTEM_PROMPT,
    "verifier": VERIFIER_SYSTEM_PROMPT,
}

# ---------------------------------------------------------------------------
# Per-engine prompt templates
# ---------------------------------------------------------------------------

CAUSALITY_PROMPT = """Analyze the following business API context for causality and conservation violations.

BUSINESS CONTEXT (PRD/requirements):
{prd_text}

API SCHEMA (OpenAPI endpoints and response models):
{api_schema}

OBSERVED DATA (redacted samples from live API responses):
{observed_data}

CURRENT HEURISTIC FINDINGS (regex-based, may be noisy):
{heuristic_findings}

TASK: Find causality and conservation defects that regex patterns would miss:

1. CAUSALITY COVERAGE: For each business state (e.g. "paid", "shipped", "approved"),
   does the system guarantee the required dependent records exist?
   - Example: order.status="paid" but no payment record with matching order_id
   - Example: contract.status="signed" but no approval audit trail
   - Example: work_order.status="completed" but no goods_receipt record

2. IDEMPOTENT SIDE EFFECTS: Could a one-time business action create duplicates?
   - Example: two payment records for the same order with identical amounts
   - Example: two shipment records from the same fulfillment event

3. REFERENTIAL CAUSALITY: Does every dependent record point to a real source entity?
   - Example: payment row references order_id=99999 which doesn't exist
   - Example: refund references a payment that was already voided

4. CONSERVATION: Do document totals agree with component fields?
   - Example: order.total != sum of line_item amounts - discounts + tax + shipping
   - Example: refund.amount > original payment.amount (money leak)
   - Example: inventory ledger: sum(ins) - sum(outs) != current stock count

Output this JSON structure:
{{
  "findings": [
    {{
      "id": "CAUSAL-XXX",
      "rule": "causality_coverage|idempotent_side_effect|referential_causality|conservation",
      "severity": "P0|P1|P2|P3",
      "title": "one-line description of the bug",
      "source_entity": "e.g. order",
      "source_field": "e.g. status",
      "source_value": "e.g. paid",
      "target_entity": "e.g. payment",
      "expected": "what the business rule requires",
      "observed": "what the data/API actually shows",
      "evidence": ["specific field paths and values that prove the violation"],
      "confidence": 0.0-1.0,
      "false_positive_risk": "why this might be a legitimate business case"
    }}
  ],
  "insufficient_evidence": true/false,
  "reasoning_summary": "1-2 sentences on the overall analysis"
}}

If no meaningful defects are found beyond what heuristics already caught, return
an empty findings array and set insufficient_evidence as appropriate."""


ORACLE_COMPILER_PROMPT = """Turn the following PRD and OpenAPI contract into a small
set of high-value, read-only business Oracle hypotheses. These are NOT bug
findings. They are candidates that must be verified by deterministic replay.

BUSINESS CONTEXT (PRD/requirements):
{prd_text}

API SCHEMA (OpenAPI endpoints and response models):
{api_schema}

CURRENT DETERMINISTIC FINDINGS (may be empty):
{heuristic_findings}

TASK:
Propose at most five non-duplicative Oracle hypotheses that can expose a severe
business bug not already covered by the supplied findings. Every proposed
endpoint MUST be an exact endpoint from the API schema. Every validation plan
MUST use GET only. Do not invent write operations, fields, entities, or evidence.
Return an empty list when the schema cannot support a read-only observation.

Output this JSON structure:
{{
  "oracle_candidates": [
    {{
      "candidate_id": "ORACLE-XXX",
      "family": "causality_coverage|conservation_check|cross_view_reconciliation|permission_boundary|state_consistency|temporal_regression",
      "title": "one-line business invariant",
      "business_rule": "the invariant in concrete terms",
      "source_endpoint": "/exact/openapi/path",
      "comparison_endpoint": "/exact/openapi/path or empty",
      "field_paths": ["actual response field paths required"],
      "evidence_required": ["specific observations that would prove or disprove it"],
      "read_only_validation": {{"method": "GET", "requests": ["GET /exact/openapi/path"]}},
      "severity_potential": "P0|P1|P2|P3",
      "confidence": 0.0,
      "false_positive_risk": "legitimate business exception to rule out"
    }}
  ],
  "insufficient_evidence": true/false
}}
"""


RECONCILIATION_PROMPT = """Analyze the following business data for cross-view reconciliation errors.

PRIMARY VIEW (e.g. list/detail API responses for the same resource):
{primary_view}

SECONDARY VIEW (e.g. export, report, or different API path for the same resource):
{secondary_view}

SCHEMA CONTEXT:
{schema_context}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find cross-view data drift that regex field matching would miss:

1. COLLECTION-DETAIL MISMATCH: Do collection items agree with their detail representations?
   - Example: GET /orders shows order.total=100 but GET /orders/123 shows total=99.99
   - Example: list shows status="active" but detail shows status="suspended"

2. AGGREGATE DRIFT: Do counts/sums/totals match between views?
   - Example: list count=50 but detail page shows 48 items
   - Example: dashboard revenue widget shows different total than order export

3. TEMPORAL INCONSISTENCY: Does data change between views taken at nearly the same time?
   - Example: list snapshot timestamp is AFTER detail snapshot but shows outdated data

4. SCHEMA FIELD DRIFT: Do the same logical fields have different types/constraints across views?
   - Example: amount is integer in list but string in detail
   - Example: required field in detail schema is optional in list schema

Output this JSON structure:
{{
  "findings": [
    {{
      "id": "RECON-XXX",
      "rule": "collection_detail|aggregate_drift|temporal_inconsistency|schema_drift",
      "severity": "P0|P1|P2|P3",
      "title": "one-line description",
      "primary_view": "API path or data source",
      "primary_value": "value from primary",
      "secondary_view": "API path or data source",
      "secondary_value": "value from secondary",
      "field_path": "e.g. data.total_amount",
      "expected": "what consistency requires",
      "observed": "the actual mismatch",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false,
  "reconciliation_summary": "overall consistency assessment"
}}"""


INVARIANT_PROMPT = """Analyze the following API specification and observed data for business invariant violations.

API SCHEMA:
{api_schema}

PRD/REQUIREMENTS:
{prd_text}

OBSERVED DATA SAMPLES:
{observed_data}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Discover business invariants that should hold and check if they're violated:

1. UNIQUENESS: Business identities must be unique across the observed collection
2. CONSTRAINT: Runtime rows must honor documented required/enum/numeric constraints
3. TEMPORAL: start/end, created/updated pairs must not be inverted
4. REFERENTIAL: foreign keys must resolve to real entities
5. SEMANTIC: field values must make business sense (e.g. negative quantities, future dates for "created_at")
6. FILTER: query parameters must actually restrict results

Output this JSON structure:
{{
  "invariants": [
    {{
      "id": "INV-XXX",
      "rule": "uniqueness|constraint|temporal|referential|semantic|filter",
      "severity": "P0|P1|P2|P3",
      "title": "invariant description",
      "invariant": "the business rule that must hold",
      "violations": [
        {{
          "entity": "resource name",
          "field": "field path",
          "expected": "what should be",
          "observed": "what actually is",
          "evidence_fingerprint": "hash of redacted evidence"
        }}
      ],
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


COUNTEREXAMPLE_PROMPT = """Analyze the following API pairs for semantic counterexamples.

RESOURCE A (collection/list endpoint):
{resource_a}

RESOURCE B (detail/item endpoint, possibly different path for same resource):
{resource_b}

RELATIONSHIP CONTEXT:
{relationship_context}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find semantic counterexamples — cases where two supposedly-related endpoints
disagree about the same underlying business entity:

1. PROJECTION DISAGREEMENT: Same entity, different fields visible in A vs B
2. STATE VOCABULARY DRIFT: Same status field uses different value sets
3. IDENTITY CONTRACT DRIFT: id field has different type/format across endpoints
4. PAGINATION ANOMALIES: Adjacent pages share IDs or skip records
5. QUERY SEMANTICS: Filter/query parameter that should restrict results but doesn't
6. CROSS-ACTOR ISOLATION: Entity visible to actor who shouldn't have access

Output:
{{
  "counterexamples": [
    {{
      "id": "CEX-XXX",
      "rule": "projection|vocabulary_drift|identity_drift|pagination|query_semantics|isolation",
      "severity": "P0|P1|P2|P3",
      "title": "counterexample description",
      "endpoint_a": "GET /resource",
      "endpoint_b": "GET /resource/{{id}}",
      "field": "field path",
      "value_a": "value from endpoint A",
      "value_b": "value from endpoint B",
      "expected": "what consistency requires",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


LIFECYCLE_PROMPT = """Analyze the following business entity lifecycle for state machine violations.

ENTITY LIFECYCLE DEFINITION:
{lifecycle_definition}

OBSERVED STATE TRANSITIONS:
{observed_transitions}

SCHEMA CONTEXT:
{schema_context}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find lifecycle violations:

1. ILLEGAL TRANSITION: Entity moved from state A to B, but no allowed path exists
2. MISSING TRANSITION: Entity stuck in intermediate state with no path to terminal
3. ORPHANED STATE: Terminal state entity with dangling references from active entities
4. REVERSAL ANOMALY: Entity moved backwards in lifecycle without audit trail
5. CONCURRENT STATE: Two operations set conflicting states on same entity

Output:
{{
  "findings": [
    {{
      "id": "LIFE-XXX",
      "rule": "illegal_transition|missing_transition|orphaned|reversal|concurrent",
      "severity": "P0|P1|P2|P3",
      "entity": "entity type",
      "entity_id": "redacted id",
      "from_state": "previous state",
      "to_state": "current state",
      "expected": "what the lifecycle requires",
      "observed": "what actually happened",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


SAGA_PROMPT = """Analyze the following business event chain for Saga compensation violations.

EVENT CHAIN:
{event_chain}

BUSINESS CONTEXT:
{business_context}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find Saga and event-chain defects:

1. MISSING COMPENSATION: A step failed but no compensating action was triggered
2. DUPLICATE EVENT: Same business event delivered/processed more than once
3. ORDERING VIOLATION: Events processed in wrong order (e.g. "shipped" before "paid")
4. DEAD LETTER: Event stuck in dead-letter queue with no retry or alert
5. PARTIAL COMPENSATION: Compensation fired but didn't fully reverse the original action

Output:
{{
  "findings": [
    {{
      "id": "SAGA-XXX",
      "rule": "missing_compensation|duplicate_event|ordering|dead_letter|partial_compensation",
      "severity": "P0|P1|P2|P3",
      "saga_name": "business process name",
      "step": "failing step",
      "expected_compensation": "what should have happened",
      "observed": "what actually happened",
      "evidence": ["timestamps, event IDs, state values"],
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


CONSISTENCY_PROMPT = """Analyze for tenant isolation and read-model consistency violations.

TENANT CONTEXT:
{tenant_context}

READ MODEL vs WRITE MODEL:
{model_comparison}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find isolation and consistency defects:

1. CROSS-TENANT LEAK: tenant A can see data belonging to tenant B
2. READ-MODEL DRIFT: event-sourced read model doesn't match write model
3. STALE READ: read returns data that was already overwritten
4. DIRTY READ: read returns uncommitted data
5. PHANTOM READ: same query returns different row sets within a transaction

Output:
{{
  "findings": [
    {{
      "id": "CONS-XXX",
      "rule": "cross_tenant_leak|read_model_drift|stale_read|dirty_read|phantom_read",
      "severity": "P0|P1|P2|P3",
      "tenant_a": "tenant identifier (redacted)",
      "tenant_b": "tenant identifier (redacted)",
      "resource": "API path",
      "expected": "isolation requirement",
      "observed": "leak/drift description",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


EVENT_CHAIN_PROMPT = """Analyze the following event chain for ordering, duplication, and dead-letter defects.

EVENTS (ordered by timestamp):
{events}

SCHEMA CONTEXT:
{schema_context}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find event processing defects:

1. DUPLICATE DELIVERY: Same event ID processed multiple times
2. OUT-OF-ORDER: Event B processed before Event A when A must precede B
3. MISSING EVENT: Expected event in sequence is absent
4. DEAD LETTER: Event in dead-letter queue with no retry
5. POISON PILL: Event that crashes consumer on every retry

Output:
{{
  "findings": [
    {{
      "id": "EVT-XXX",
      "rule": "duplicate|out_of_order|missing|dead_letter|poison_pill",
      "severity": "P0|P1|P2|P3",
      "event_id": "event identifier",
      "event_type": "business event type",
      "expected": "correct behavior",
      "observed": "anomaly description",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


POPULATION_PROMPT = """Analyze for business population constraint violations.

CONSTRAINT DEFINITIONS:
{constraints}

OBSERVED POPULATION DATA:
{observed_data}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find population constraint bugs:

1. CAPACITY OVERFLOW: More entities than allowed (e.g. enrollment > class capacity)
2. UNIQUENESS VIOLATION: Duplicate entity in a unique-constrained collection
3. CARDINALITY VIOLATION: Wrong number of related entities
4. RANGE VIOLATION: Value outside documented bounds
5. RATE LIMIT: Operations exceeding defined rate

Output:
{{
  "findings": [
    {{
      "id": "POP-XXX",
      "rule": "capacity|uniqueness|cardinality|range|rate_limit",
      "severity": "P0|P1|P2|P3",
      "constraint": "the business constraint",
      "entity": "entity type",
      "expected": "limit or requirement",
      "observed": "actual count/value",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


OUTCOME_PROMPT = """Analyze business outcomes for end-to-end validation gaps.

BUSINESS PROCESS:
{business_process}

EXPECTED OUTCOMES:
{expected_outcomes}

OBSERVED RESULTS:
{observed_results}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find outcome validation gaps — places where the system reports success
but the actual business outcome is wrong:

1. FALSE SUCCESS: API returns 200/success but business state is incorrect
2. SILENT FAILURE: Business rule violation with no error or alert
3. PARTIAL EXECUTION: Only some steps of a multi-step process completed
4. ROLLBACK GAP: Failure in step N didn't rollback steps 1..N-1
5. SIDE EFFECT LEAK: Operation succeeded but left unintended side effects

Output:
{{
  "findings": [
    {{
      "id": "OUT-XXX",
      "rule": "false_success|silent_failure|partial_execution|rollback_gap|side_effect_leak",
      "severity": "P0|P1|P2|P3",
      "process": "business process name",
      "expected_outcome": "what should happen",
      "observed_outcome": "what actually happened",
      "evidence": ["specific API responses, state values"],
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


METAMORPHIC_PROMPT = """Analyze for metamorphic relation violations.

METAMORPHIC RELATIONS:
{relations}

TEST INPUTS AND OUTPUTS:
{test_data}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find metamorphic bugs — cases where a known input-output relationship
breaks for new inputs:

1. PERMUTATION: Reordering inputs should not change the set of results
2. SCALING: Multiplying an input by N should scale a specific output by N
3. ADDITION: Adding a new entity should increase count by 1
4. FILTER STRENGTHENING: Adding a filter should produce a subset of previous results
5. COMPLEMENT: A query and its complement should together cover the full set

Output:
{{
  "findings": [
    {{
      "id": "META-XXX",
      "rule": "permutation|scaling|addition|filter|complement",
      "severity": "P0|P1|P2|P3",
      "relation": "the metamorphic relation",
      "input_a": "first input",
      "input_b": "second input",
      "expected_relation": "what should hold",
      "observed": "what actually happened",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


TEMPORAL_PROMPT = """Analyze for temporal data regression — historical data that has changed
in ways that violate business rules.

HISTORICAL SNAPSHOT (T1):
{snapshot_t1}

CURRENT SNAPSHOT (T2):
{snapshot_t2}

SCHEMA CONTEXT:
{schema_context}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Find temporal regressions:

1. IMMUTABLE FIELD CHANGE: Field that should be immutable changed between T1 and T2
2. BACKDATED MODIFICATION: created_at or similar field changed retroactively
3. CALCULATED FIELD DRIFT: Derived field doesn't match formula with current inputs
4. AUDIT TRAIL GAP: Change exists but no audit record
5. RETROACTIVE STATE CHANGE: Historical state changed without business justification

Output:
{{
  "findings": [
    {{
      "id": "TEMP-XXX",
      "rule": "immutable_change|backdated|calculation_drift|audit_gap|retroactive_state",
      "severity": "P0|P1|P2|P3",
      "entity": "entity type",
      "field": "field path",
      "value_t1": "historical value",
      "value_t2": "current value",
      "expected": "what should be preserved",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


DEFECT_CLASSIFICATION_PROMPT = """Classify the following bug finding and suggest related patterns
to improve future detection.

BUG FINDING:
{finding}

CONFIRMED BUG HISTORY (similar findings from the flywheel):
{bug_history}

TASK: Classify this finding and generate learning signals:

1. CATEGORIZE: Map to the most specific bug category
2. SIMILARITY: Find related confirmed bugs that share patterns
3. GENERALIZE: What pattern should future detection look for?
4. PRIORITIZE: Should this be promoted to regression suite? Why?
5. LEARN: What keyword/semantic signals would have caught this earlier?

Output:
{{
  "classification": {{
    "primary_category": "causality|reconciliation|invariant|lifecycle|saga|consistency|event_chain|population|outcome|metamorphic|temporal|counterexample",
    "sub_category": "specific pattern name",
    "severity": "P0|P1|P2|P3",
    "is_novel_pattern": true/false
  }},
  "similar_confirmed_bugs": ["bug_ids"],
  "generalized_pattern": {{
    "pattern_name": "human-readable name for this bug class",
    "detection_signals": ["keywords, field patterns, semantic conditions"],
    "false_positive_risks": ["when this pattern is actually correct"],
    "suggested_oracle": "what invariant/check would catch this"
  }},
  "promotion_recommendation": {{
    "promote_to_regression": true/false,
    "reason": "why this bug is worth permanent monitoring"
  }}
}}"""


MULTI_SOURCE_PROMPT = """Synthesize evidence from multiple sources to find cross-source defects.

SOURCE A (API responses):
{source_a}

SOURCE B (Database/export):
{source_b}

SOURCE C (Logs/events):
{source_c}

CURRENT HEURISTIC FINDINGS:
{heuristic_findings}

TASK: Cross-reference evidence from all sources:

1. API-vs-DB DRIFT: API returns data that doesn't match the database
2. LOG-vs-STATE: Logged event doesn't match current system state
3. THREE-WAY INCONSISTENCY: All three sources disagree
4. HIDDEN FIELD: Field present in DB/logs but not exposed in API (shadow data)
5. ORPHANED LOG: Event logged but no corresponding state change

Output:
{{
  "findings": [
    {{
      "id": "MSRC-XXX",
      "rule": "api_db_drift|log_state_mismatch|three_way|hidden_field|orphaned_log",
      "severity": "P0|P1|P2|P3",
      "sources": ["source names that disagree"],
      "field": "field path",
      "values": {{"source_a": "...", "source_b": "..."}},
      "expected": "what consistency requires",
      "confidence": 0.0-1.0
    }}
  ],
  "insufficient_evidence": true/false
}}"""


MULTI_INDUSTRY_PROMPT = """You are an enterprise business analyst. Given project documents
and API contracts, infer the industry domain(s) and construct a complete
business model — even for domains NOT in any predefined list.

PROJECT DOCUMENTS (PRD/MRD):
{documents}

API CONTRACTS:
{api_contracts}

CURRENT INDUSTRY MATCHES (dictionary-based):
{current_matches}

TASK: Go beyond keyword matching. Understand what business this system does,
then produce a structured business model:

1. INFER INDUSTRY: What specific industry? Be precise (e.g. "cross-border logistics",
   "health insurance claims", "municipal permitting", "EV charging network").
   Not just "ecommerce" or "fintech".

2. BUSINESS OBJECTS: Core entities. For each: name, aliases, description, id_field, is_core.

3. ROLES: Human/system roles. For each: name, aliases, permissions.

4. STATE MACHINES: Objects with lifecycles. For each: object, states (ordered),
   aliases, terminal_states, transitions.

5. DEPENDENCIES: How objects relate. For each: from_object, to_object, relationship_type
   (owns|paid_by|fulfilled_by|approved_by|contains|references).

6. INVARIANTS: Rules that must ALWAYS hold. For each: rule_id, kind
   (permission|conservation|state_transition|uniqueness|constraint|temporal),
   objects, expected, oracle_family.

7. INDUSTRY RISKS: Bugs specific to this industry that generic engines miss.
   For each: risk_id, severity, title, why_generic_engines_miss_it, suggested_detection.

8. CROSS-INDUSTRY: similar_to (known industries with shared patterns), unique_aspects.

Output ONLY valid JSON:
{{
  "inferred_industries": [
    {{
      "industry": "specific industry name",
      "confidence": 0.0-1.0,
      "evidence": ["document excerpts supporting this"]
    }}
  ],
  "business_objects": [
    {{
      "name": "entity_name",
      "aliases": ["alt names"],
      "description": "business meaning",
      "id_field": "primary key",
      "is_core": true/false
    }}
  ],
  "roles": [
    {{
      "name": "role_name",
      "aliases": ["alt names"],
      "permissions": ["can_read_x", "can_write_y"]
    }}
  ],
  "state_machines": [
    {{
      "object": "entity_name",
      "states": ["s1", "s2", "..."],
      "aliases": ["alt state names"],
      "terminal_states": ["final_states"],
      "transitions": ["s1->s2", "s2->s3"]
    }}
  ],
  "dependencies": [
    {{
      "from_object": "source",
      "to_object": "target",
      "relationship_type": "owns|paid_by|fulfilled_by|approved_by|contains|references"
    }}
  ],
  "invariants": [
    {{
      "rule_id": "unique_id",
      "kind": "permission|conservation|state_transition|uniqueness|constraint|temporal",
      "objects": ["entities"],
      "expected": "plain language rule",
      "oracle_family": "suggested_oracle_name"
    }}
  ],
  "industry_risks": [
    {{
      "risk_id": "IND-XXX",
      "severity": "P0|P1|P2|P3",
      "title": "risk description",
      "why_generic_engines_miss_it": "explanation",
      "suggested_detection": "how to detect"
    }}
  ],
  "cross_industry": {{
    "similar_to": ["known industries"],
    "unique_aspects": ["what makes this different"]
  }},
  "recommended_oracle_families": ["oracle names"],
  "insufficient_evidence": true/false
}}"""


# ---------------------------------------------------------------------------
# Prompt registry — maps engine types to their prompts (backward compatible)
# ---------------------------------------------------------------------------

# Legacy prompts kept for backward compatibility
PROMPTS: dict[EngineType, str] = {
    "causality": CAUSALITY_PROMPT,
    "reconciliation": RECONCILIATION_PROMPT,
    "invariant": INVARIANT_PROMPT,
    "counterexample": COUNTEREXAMPLE_PROMPT,
    "lifecycle": LIFECYCLE_PROMPT,
    "saga": SAGA_PROMPT,
    "consistency": CONSISTENCY_PROMPT,
    "event_chain": EVENT_CHAIN_PROMPT,
    "population": POPULATION_PROMPT,
    "outcome": OUTCOME_PROMPT,
    "metamorphic": METAMORPHIC_PROMPT,
    "temporal": TEMPORAL_PROMPT,
    "defect_classification": DEFECT_CLASSIFICATION_PROMPT,
    "multi_source": MULTI_SOURCE_PROMPT,
    "multi_industry": MULTI_INDUSTRY_PROMPT,
    "oracle_compiler": ORACLE_COMPILER_PROMPT,
    "assurance": MULTI_INDUSTRY_PROMPT,
    "adaptation": MULTI_INDUSTRY_PROMPT,
}

# Layer mapping: which layer does each engine type belong to?
ENGINE_LAYER: dict[EngineType, str] = {
    "multi_industry": "reader",
    "multi_source": "reader",
    "lifecycle": "reader",
    "causality": "reasoner",
    "reconciliation": "reasoner",
    "invariant": "reasoner",
    "counterexample": "reasoner",
    "saga": "reasoner",
    "consistency": "reasoner",
    "event_chain": "reasoner",
    "population": "reasoner",
    "outcome": "reasoner",
    "metamorphic": "reasoner",
    "temporal": "reasoner",
    "defect_classification": "verifier",
    "oracle_compiler": "reasoner",
    "assurance": "reader",
    "adaptation": "reader",
}

# Reasoner-layer engines get the reasoner system prompt + known bug patterns
REASONER_ENGINES = {
    "causality", "reconciliation", "invariant", "counterexample",
    "saga", "consistency", "event_chain", "population", "outcome",
    "metamorphic", "temporal", "oracle_compiler",
}


# ---------------------------------------------------------------------------
# Model routing (tier-aware)
# ---------------------------------------------------------------------------
#
# Extraction/classification tasks route to the light tier (LLM_MODEL_LIGHT)
# when configured; deep reasoning engines stay on the primary LLM_MODEL.
# Routing is a pure cost optimization: when LLM_MODEL_LIGHT is unset every
# tier resolves to the primary model, so behavior never changes and routing
# can never become a capability requirement.

LIGHT_TIER_ENGINES: frozenset[str] = frozenset({"defect_classification"})
DEFAULT_TIER = "strong"
LIGHT_TIER = "light"


def resolve_model_for_tier(config: "ReasoningConfig", tier: str) -> str:
    """Resolve the concrete model for a routing tier.

    ``light`` uses LLM_MODEL_LIGHT when configured, otherwise falls back to
    the primary model; ``strong`` (default) always uses the primary model.
    Unknown tiers fail safe to the primary model.
    """
    tier = str(tier or "").strip().lower()
    if tier == LIGHT_TIER and getattr(config, "model_light", ""):
        return config.model_light
    return config.model


# ---------------------------------------------------------------------------
# Core reasoning client
# ---------------------------------------------------------------------------

class ReasoningClientError(RuntimeError):
    pass


class ReasoningClient:
    """LLM client for business reasoning. Uses OpenAI-compatible Chat Completions
    API (stdlib only, same as the rest of QualiBug)."""

    def __init__(self, config: ReasoningConfig | None = None):
        self.config = config or ReasoningConfig.from_env()
        self._usage_lock = threading.Lock()
        self._usage_totals: dict[str, float] = {
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "responses_with_cost": 0,
        }

    def usage_snapshot(self) -> dict[str, float]:
        with self._usage_lock:
            return dict(self._usage_totals)

    def _record_usage(self, response_text: str) -> None:
        try:
            response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return
        usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
        total = usage.get("total_tokens", 0)
        cost_value = usage.get("cost_usd", response.get("cost_usd") if isinstance(response, dict) else None)
        with self._usage_lock:
            self._usage_totals["request_count"] += 1
            for key, value in (
                ("prompt_tokens", prompt),
                ("completion_tokens", completion),
                ("total_tokens", total),
            ):
                try:
                    self._usage_totals[key] += max(0, int(value or 0))
                except (TypeError, ValueError):
                    pass
            if cost_value is not None:
                try:
                    cost = float(cost_value)
                except (TypeError, ValueError):
                    cost = -1.0
                if cost >= 0:
                    self._usage_totals["cost_usd"] += cost
                    self._usage_totals["responses_with_cost"] += 1

    def reason(self, engine_type: EngineType, context: dict[str, str], *, use_layered: bool = False) -> dict[str, Any] | None:
        """Run LLM-powered reasoning. Returns parsed JSON result, or None if
        LLM is unavailable (caller should fall back to heuristic path).
        
        If use_layered=True, uses the three-layer prompt architecture
        (Reader/Reasoner/Verifier) with layer-specific system prompts.

        Model routing: extraction/classification engine types resolve to the
        light tier (LLM_MODEL_LIGHT when configured); deep reasoning engines
        stay on the primary LLM_MODEL.
        """
        if not self.config.enabled:
            return None
        if engine_type not in PROMPTS:
            return None

        template = PROMPTS[engine_type]
        
        # Choose system prompt based on engine layer
        layer = ENGINE_LAYER.get(engine_type, "reasoner")
        system_prompt = LAYERED_SYSTEM_PROMPTS.get(layer, SYSTEM_PROMPT)
        model = resolve_model_for_tier(
            self.config,
            LIGHT_TIER if engine_type in LIGHT_TIER_ENGINES else DEFAULT_TIER,
        )
        
        try:
            user_prompt = template.format(**{
                k: (v or "(not provided)")[:8000]
                for k, v in context.items()
            })
        except KeyError as exc:
            raise ReasoningClientError(
                f"Missing context field '{exc.args[0]}' for engine '{engine_type}'"
            ) from exc

        # Reasoner toolization hook: engines that already hold a structured
        # knowledge payload may pass "_retrieved_facts" (a bounded,
        # source-anchored fact block from reasoning_fact_retrieval) which is
        # appended verbatim.  Advisory only — never inferred here, and absent
        # keys change nothing for existing callers.
        retrieved_facts = context.get("_retrieved_facts")
        if retrieved_facts:
            user_prompt += FACT_BLOCK_HEADER + str(retrieved_facts)[:3000]

        try:
            response_text = self._chat(user_prompt, system_prompt=system_prompt, model=model)
            return self._parse_json(response_text)
        except Exception:
            return None

    def _chat(self, user_prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> str:
        resolved_model = model or self.config.model
        payload = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        # These options are opt-in through environment variables. Existing
        # OpenAI-compatible providers retain their historical payload unless
        # a deployment explicitly advertises support (for example DeepSeek V4).
        if self.config.thinking_mode:
            payload["thinking"] = {"type": self.config.thinking_mode}
        if self.config.response_format:
            payload["response_format"] = {"type": self.config.response_format}

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.config.base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        _llm_start = time.time()
        _prompt_len = len(user_prompt)
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as resp:
                response_text = resp.read().decode("utf-8")
                _elapsed_ms = int((time.time() - _llm_start) * 1000)
                self._record_usage(response_text)
                _llm_logger.info(
                    f"LLM call OK: model={resolved_model} prompt={_prompt_len}c resp={len(response_text)}c {_elapsed_ms}ms",
                    extra={"context": {
                        "model": resolved_model,
                        "prompt_chars": _prompt_len,
                        "response_chars": len(response_text),
                        "elapsed_ms": _elapsed_ms,
                        "timeout_seconds": self.config.timeout_seconds,
                    }},
                )
                return response_text
        except urllib.error.HTTPError as exc:
            _elapsed_ms = int((time.time() - _llm_start) * 1000)
            error_body = exc.read().decode("utf-8", errors="replace")
            _code = "QB-L006" if exc.code in (401, 403) else ("QB-L002" if exc.code == 429 else "QB-L001")
            _llm_logger.error(
                f"LLM HTTP {exc.code} after {_elapsed_ms}ms: {error_body[:200]}",
                extra={"error_code": _code, "context": {
                    "model": resolved_model,
                    "http_status": exc.code,
                    "elapsed_ms": _elapsed_ms,
                    "prompt_chars": _prompt_len,
                }},
            )
            raise ReasoningClientError(f"LLM HTTP {exc.code}: {error_body[:500]}") from exc
        except urllib.error.URLError as exc:
            _elapsed_ms = int((time.time() - _llm_start) * 1000)
            _is_timeout = "timed out" in str(exc).lower() or _elapsed_ms >= (self.config.timeout_seconds * 1000 - 500)
            _code = "QB-L001" if _is_timeout else "QB-L004"
            _llm_logger.error(
                f"LLM network error after {_elapsed_ms}ms: {exc}",
                extra={"error_code": _code, "context": {
                    "model": self.config.model,
                    "elapsed_ms": _elapsed_ms,
                    "is_timeout": _is_timeout,
                    "base_url": self.config.base_url,
                }},
            )
            raise ReasoningClientError(f"LLM network error: {exc}") from exc

    @staticmethod
    def _parse_json(response_text: str) -> dict[str, Any]:
        try:
            raw = json.loads(response_text)
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ReasoningClientError(f"Unexpected LLM response shape: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            raise ReasoningClientError("LLM response did not include JSON content")

        # Handle models that wrap JSON in ``` fences
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].lstrip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise ReasoningClientError("LLM JSON root must be an object")
            return parsed
        except json.JSONDecodeError as exc:
            raise ReasoningClientError(
                f"LLM output is not valid JSON: {cleaned[:300]}"
            ) from exc

    def chat_json(self, user_prompt: str, *, system_prompt: str | None = None, tier: str = DEFAULT_TIER) -> dict[str, Any]:
        """Run one JSON-only advisory request using the shared provider settings.

        ``tier`` selects the routed model: "light" (LLM_MODEL_LIGHT when
        configured) for extraction/classification tasks, "strong" (primary
        LLM_MODEL) otherwise. Defaults to strong so existing callers keep
        their historical model.
        """
        if not self.config.enabled:
            raise ReasoningClientError("LLM is not configured")
        model = resolve_model_for_tier(self.config, tier)
        raw = self._chat(user_prompt, system_prompt=system_prompt, model=model)
        return self._parse_json(raw)

    def complete_json(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
        tier: str = DEFAULT_TIER,
    ) -> dict[str, Any]:
        """Expose the fail-fast JSON contract used by constrained Agent planners."""

        return self.chat_json(user_prompt, system_prompt=system_prompt, tier=tier)

    def health_check(self) -> dict[str, Any]:
        """Perform a bounded provider check without storing credentials or prompts."""
        result = self.chat_json('Return only this JSON object: {"ok":true}.')
        if result.get("ok") is not True:
            raise ReasoningClientError("LLM health response did not confirm ok=true")
        return {"ok": True, "model": self.config.model}

    def _record_embedding_usage(self, response_text: str) -> None:
        try:
            response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return
        usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        with self._usage_lock:
            self._usage_totals["request_count"] += 1
            try:
                self._usage_totals["prompt_tokens"] += max(0, int(prompt or 0))
            except (TypeError, ValueError):
                pass

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Non-decision embedding: candidate de-duplication and fact-retrieval
        ranking only.

        Returns None whenever the embedding model is unconfigured or the
        provider fails — deterministic paths are never blocked by this
        advisory capability (fail-soft by design).  Never feeds formal fact
        merging, assertion evaluation, or the delivery gate.
        """
        if not self.config.enabled or not self.config.embedding_model:
            return None
        clean = [str(text or "")[:8000] for text in (texts or [])]
        clean = [text for text in clean if text.strip()]
        if not clean:
            return None
        payload = {"model": self.config.embedding_model, "input": clean}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.config.base_url}/embeddings"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as resp:
                response_text = resp.read().decode("utf-8")
            self._record_embedding_usage(response_text)
            data = json.loads(response_text)
            if not isinstance(data, dict):
                return None
            vectors: list[list[float]] = []
            for item in data.get("data") or []:
                embedding = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(embedding, list) or not embedding:
                    return None
                if not all(isinstance(value, (int, float)) for value in embedding):
                    return None
                vectors.append([float(value) for value in embedding])
            if len(vectors) != len(clean):
                return None
            _llm_logger.info(
                "LLM embedding OK: model=%s texts=%d dim=%d",
                self.config.embedding_model,
                len(clean),
                len(vectors[0]),
            )
            return vectors
        except Exception as exc:
            _llm_logger.warning(
                "LLM embedding unavailable (non-decision path, skipped): %s:%s",
                type(exc).__name__,
                str(exc)[:160],
            )
            return None


def compile_oracle_hypotheses(
    *,
    prd_text: str,
    api_schema: str,
    heuristic_findings: list[dict[str, Any]],
    known_paths: set[str],
    client: "ReasoningClient | None" = None,
) -> list[dict[str, Any]]:
    """Compile only schema-grounded, read-only Oracle *hypotheses*.

    This is intentionally separate from discovery findings: model output never
    changes severity, never enters the validation queue, and cannot be learned
    as a confirmed defect until a deterministic engine produces evidence.
    """
    if not prd_text or not api_schema or not known_paths:
        return []
    result = reason("oracle_compiler", {
        "prd_text": prd_text,
        "api_schema": api_schema,
        "heuristic_findings": json.dumps(heuristic_findings[:20], ensure_ascii=False, default=str),
    })
    raw_candidates = (result or {}).get("oracle_candidates")
    if not isinstance(raw_candidates, list):
        return []

    allowed_families = {
        "causality_coverage", "conservation_check", "cross_view_reconciliation",
        "permission_boundary", "state_consistency", "temporal_regression",
    }
    allowed_severities = {"P0", "P1", "P2", "P3"}
    hypotheses: list[dict[str, Any]] = []
    for candidate in raw_candidates[:5]:
        if not isinstance(candidate, dict):
            continue
        source_endpoint = str(candidate.get("source_endpoint") or "").strip()
        comparison_endpoint = str(candidate.get("comparison_endpoint") or "").strip()
        read_only = candidate.get("read_only_validation") or {}
        method = str(read_only.get("method") or "").upper() if isinstance(read_only, dict) else ""
        title = str(candidate.get("title") or "").strip()
        business_rule = str(candidate.get("business_rule") or "").strip()
        evidence_required = candidate.get("evidence_required")
        field_paths = candidate.get("field_paths")
        if (
            not title
            or not business_rule
            or source_endpoint not in known_paths
            or (comparison_endpoint and comparison_endpoint not in known_paths)
            or method != "GET"
            or not isinstance(evidence_required, list)
            or not evidence_required
            or not isinstance(field_paths, list)
            or not field_paths
        ):
            continue
        family = str(candidate.get("family") or "").strip()
        if family not in allowed_families:
            continue
        severity = str(candidate.get("severity_potential") or "P2").upper()
        if severity not in allowed_severities:
            severity = "P2"
        try:
            confidence = min(0.60, max(0.0, float(candidate.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        provenance = build_model_provenance("oracle_compiler", client=client)
        fingerprint_payload = {
            "family": family,
            "title": title[:300],
            "source_endpoint": source_endpoint,
            "comparison_endpoint": comparison_endpoint,
            "field_paths": [str(item)[:160] for item in field_paths[:8]],
            **provenance,
        }
        fingerprint = sha256(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        hypotheses.append({
            "hypothesis_id": f"LLM_ORACLE_{fingerprint[:12].upper()}",
            "source": "llm_oracle_compiler",
            "status": "unverified_hypothesis",
            "requires_deterministic_replay": True,
            "execution_policy": "candidate_only",
            "evidence_strength": "llm_inferred",
            "family": family,
            "severity_potential": severity,
            "title": title[:300],
            "business_rule": business_rule[:700],
            "source_endpoint": source_endpoint,
            "comparison_endpoint": comparison_endpoint,
            "field_paths": [str(item)[:160] for item in field_paths[:8]],
            "evidence_required": [str(item)[:300] for item in evidence_required[:8]],
            "read_only_validation": {"method": "GET", "requests": [str(item)[:240] for item in (read_only.get("requests") or [])[:8]]},
            "confidence": confidence,
            "false_positive_risk": str(candidate.get("false_positive_risk") or "")[:400],
            "model_id": provenance["model_id"],
            "temperature": provenance["temperature"],
            "prompt_template_hash": provenance["prompt_template_hash"],
        })
    return hypotheses


_HYPOTHESIS_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+\-/=]+|(?:api[_\s-]?key|token|secret|password|credential)\s*[:=]\s*[^\s,;]+|sk-[a-z0-9_-]{8,})"
)
_HYPOTHESIS_EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")


def _safe_hypothesis_text(value: Any, limit: int) -> str:
    """Keep advisory LLM output useful without persisting secrets or raw rows."""
    text = " ".join(str(value or "").split())
    text = _HYPOTHESIS_SECRET_RE.sub("[REDACTED]", text)
    text = _HYPOTHESIS_EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return text[:limit]


def _prompt_template_hash(engine: str | None) -> str:
    """Content hash of the prompt template used for one engine type.

    Binds advisory hypotheses to the exact template version that produced
    them.  Empty when the engine has no registered template (the compiler
    still emits provenance so receipts never silently lose the binding).
    """
    template = PROMPTS.get(str(engine or "")) if engine else None
    if not template:
        return ""
    return sha256(template.encode("utf-8", errors="replace")).hexdigest()[:16]


def build_model_provenance(
    engine: str | None = None,
    *,
    client: "ReasoningClient | None" = None,
) -> dict[str, str]:
    """Model identity for hypothesis provenance.

    Every advisory hypothesis is bound to the exact model, temperature, and
    prompt-template hash that produced it, so a champion/challenger replay or
    a regression comparison can attribute discovery deltas to a model or
    prompt change instead of guessing.  Values are literal config facts, never
    credentials or prompts; empty strings mean "not configured" and stay
    visible rather than being invented.
    """
    cfg = (client or _get_client()).config
    return {
        "model_id": str(cfg.model or ""),
        "temperature": str(cfg.temperature),
        "prompt_template_hash": _prompt_template_hash(engine),
    }


def compile_unverified_semantic_hypotheses(
    raw_findings: Any,
    *,
    engine: str,
    type_field: str,
    max_count: int = 5,
    client: "ReasoningClient | None" = None,
) -> list[dict[str, Any]]:
    """Normalize legacy engine LLM output into non-authoritative hypotheses.

    Older engines predate the evidence-first boundary and appended model output
    to their deterministic findings.  This adapter preserves the semantic lead
    while making it impossible for a model response to affect finding counts,
    evidence registries, learning, validation queues, or release gates.
    """
    if not isinstance(raw_findings, list):
        return []

    allowed_severities = {"P0", "P1", "P2", "P3"}
    hypotheses: list[dict[str, Any]] = []
    engine_key = re.sub(r"[^a-z0-9]+", "_", str(engine or "semantic").lower()).strip("_") or "semantic"
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        rule = re.sub(r"[^a-z0-9_\-.]+", "_", str(raw.get("rule") or "unknown").lower()).strip("_") or "unknown"
        severity = str(raw.get("severity") or "P2").upper()
        if severity not in allowed_severities:
            severity = "P2"
        try:
            confidence = min(0.60, max(0.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        title = _safe_hypothesis_text(raw.get("title"), 300) or f"LLM 建议补充 {rule} 的确定性验证"
        observation = _safe_hypothesis_text(raw.get("expected") or raw.get("observed") or raw.get("actual"), 500)
        provenance = build_model_provenance(engine_key, client=client)
        fingerprint_payload = {
            "engine": engine_key, "rule": rule, "title": title,
            **provenance,
        }
        fingerprint = sha256(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        hypothesis = {
            "hypothesis_id": f"LLM_{engine_key.upper()}_{fingerprint[:12].upper()}",
            "source": "llm_reasoning",
            "status": "unverified_hypothesis",
            "requires_deterministic_replay": True,
            "execution_policy": "candidate_only",
            "evidence_strength": "llm_inferred",
            "engine": engine_key,
            "severity_potential": severity,
            "title": title,
            "suggested_next_observation": observation or "补充受控、可回放的确定性证据。",
            "confidence": confidence,
            "false_positive_risk": "模型推断未经过确定性回放，不能作为正式缺陷或发布依据。",
            "model_id": provenance["model_id"],
            "temperature": provenance["temperature"],
            "prompt_template_hash": provenance["prompt_template_hash"],
        }
        hypothesis[str(type_field or "semantic_type")] = f"llm_semantic_{rule}"
        hypotheses.append(hypothesis)
        if len(hypotheses) >= max(1, min(int(max_count or 5), 20)):
            break
    return hypotheses


# ---------------------------------------------------------------------------
# Singleton — created once, reused by all engines
# ---------------------------------------------------------------------------

_client: ReasoningClient | None = None


def _get_client() -> ReasoningClient:
    global _client
    if _client is None:
        _client = ReasoningClient()
    return _client


def reset_client() -> None:
    """Drop the cached LLM client after runtime configuration changes."""
    global _client
    _client = None


def _provider_health_paths(project_id: str, root: Path) -> dict[str, Path]:
    """Return private-runtime paths for a redacted provider verification record."""
    from .real_project_onboarding import _safe_project_id

    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "llm_provider_health"
    output = root / "platform_outputs" / project / "llm_provider_health"
    return {
        "workspace": workspace,
        "output": output,
        "record": workspace / "provider_health.json",
        "public_record": output / "provider_health.json",
    }


def _redact_provider_error(value: Any) -> str:
    """Keep failure classification useful without persisting credentials or raw provider bodies."""
    text = str(value or "")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}", r"\1<REDACTED>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "<REDACTED_KEY>", text)
    return text[:300]


def probe_provider_health(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    client: "ReasoningClient | None" = None,
) -> dict[str, Any]:
    """Perform and persist a bounded, real OpenAI-compatible provider check.

    A saved credential is not proof of availability.  The persisted result is
    marked ``online`` only after the provider returns the exact health JSON
    contract.  It intentionally contains no API key, raw prompt or raw model
    response.
    """
    from .real_project_onboarding import ROOT, _write_json

    root = root or ROOT
    current = client or _get_client()
    cfg = current.config
    paths = _provider_health_paths(project_id, root)
    record: dict[str, Any] = {
        "phase": "phase72_provider_health_evidence",
        "project_id": project_id,
        "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "configured": bool(cfg.enabled),
        "model": cfg.model or None,
        "base_url_digest": sha256(cfg.base_url.encode("utf-8", errors="replace")).hexdigest()[:24] if cfg.base_url else None,
        "verification_policy": "actual_provider_roundtrip_required_for_online",
        "status": "unconfigured",
        "online": False,
        "error_class": None,
        "error": None,
    }
    if cfg.enabled:
        try:
            checked = current.health_check()
            record.update({"status": "online", "online": True, "model": checked.get("model") or cfg.model})
        except ReasoningClientError as exc:
            error = _redact_provider_error(exc)
            lowered = error.lower()
            category = "provider_response"
            if "network" in lowered or "urlerror" in lowered or "name or service" in lowered:
                category = "network"
            elif "http 401" in lowered or "http 403" in lowered:
                category = "authentication_or_authorization"
            record.update({"status": "offline", "online": False, "error_class": category, "error": error})
        except Exception as exc:  # provider failure must not break deterministic discovery
            record.update({"status": "offline", "online": False, "error_class": "unexpected", "error": _redact_provider_error(type(exc).__name__)})
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["record"], record)
    _write_json(paths["public_record"], record)
    return record


def reason(engine_type: EngineType, context: dict[str, str]) -> dict[str, Any] | None:
    """Entry point for all business reasoning engines.

    Usage from any engine:
        from .llm_reasoning import reason

        llm_result = reason("causality", {
            "prd_text": prd_content,
            "api_schema": json.dumps(schema),
            "observed_data": json.dumps(samples),
            "heuristic_findings": json.dumps(heuristic_results),
        })
        if llm_result:
            # Advisory only: normalize model output into semantic hypotheses.
            # Never append raw model findings to the formal defect list.
            hypotheses = compile_unverified_semantic_hypotheses(
                llm_result.get("findings", []),
                engine="your_engine",
                type_field="your_type_field",
            )
        else:
            # LLM unavailable — fall through to existing heuristic logic.
            hypotheses = []
    """
    return _get_client().reason(engine_type, context)


def reason_layered(
    engine_type: EngineType,
    context: dict[str, str],
    *,
    api_responses: str = "",
    runtime_observations: str = "",
) -> dict[str, Any] | None:
    """Three-layer reasoning pipeline: Reader → Reasoner → Verifier.
    
    Stage 1 (Reader): Extract business facts from documents
    Stage 2 (Reasoner): Derive risk hypotheses from facts  
    Stage 3 (Verifier): Verify hypotheses against actual API responses
    
    Returns a dict with 'reader_output', 'reasoner_hypotheses', 'verifications'.
    """
    client = _get_client()
    if not client.config.enabled:
        return None
    
    result: dict[str, Any] = {}
    
    # Stage 1: Reader — extract business facts
    reader_engine = "multi_industry" if engine_type in ("assurance", "adaptation", "multi_industry") else engine_type
    if reader_engine in READER_PROMPTS or ENGINE_LAYER.get(engine_type) == "reader":
        try:
            reader_template = READER_PROMPTS.get("business_world") if engine_type in ("multi_industry", "assurance", "adaptation") else PROMPTS.get(engine_type, "")
            if reader_template:
                reader_prompt = reader_template.format(**{
                    k: (v or "(not provided)")[:8000] for k, v in context.items()
                })
                reader_output = client._parse_json(
                    client._chat(reader_prompt, system_prompt=READER_SYSTEM_PROMPT)
                )
                result["reader_output"] = reader_output
        except Exception:
            result["reader_output"] = None
    
    # Stage 2: Reasoner — derive hypotheses
    reasoner_engine = engine_type if engine_type in REASONER_ENGINES else "invariant"
    if reasoner_engine in PROMPTS:
        try:
            reasoner_template = PROMPTS[reasoner_engine]
            # Inject reader output into context if available
            enriched_context = dict(context)
            if result.get("reader_output"):
                enriched_context["prd_text"] = json.dumps(result["reader_output"], ensure_ascii=False, default=str)[:6000]
            reasoner_prompt = reasoner_template.format(**{
                k: (v or "(not provided)")[:8000] for k, v in enriched_context.items()
            })
            reasoner_output = client._parse_json(
                client._chat(reasoner_prompt, system_prompt=REASONER_SYSTEM_PROMPT)
            )
            result["reasoner_hypotheses"] = reasoner_output
        except Exception:
            result["reasoner_hypotheses"] = None
    
    # Stage 3: Verifier — verify against real data (if api_responses provided)
    if api_responses and result.get("reasoner_hypotheses"):
        try:
            verifier_prompt = VERIFIER_PROMPTS["general"].format(
                hypotheses=json.dumps(result["reasoner_hypotheses"], ensure_ascii=False, default=str)[:6000],
                api_responses=api_responses[:6000],
                runtime_observations=runtime_observations[:3000],
            )
            verifications = client._parse_json(
                client._chat(verifier_prompt, system_prompt=VERIFIER_SYSTEM_PROMPT)
            )
            result["verifications"] = verifications
        except Exception:
            result["verifications"] = None
    
    return result if result else None


def is_available() -> bool:
    """Check if LLM reasoning is available without making an API call."""
    return _get_client().config.enabled


def embed_texts(
    texts: list[str],
    *,
    client: "ReasoningClient | None" = None,
) -> list[list[float]] | None:
    """Advisory embedding helper (non-decision role).

    Used for near-duplicate hypothesis merging and fact-retrieval ranking
    only.  Returns None (never raises) when embeddings are unavailable, so
    deterministic pipelines keep running unchanged.
    """
    return (client or _get_client()).embed(texts)


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors; 0.0 on mismatch."""
    if not vector_a or not vector_b or len(vector_a) != len(vector_b):
        return 0.0
    dot = sum(x * y for x, y in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(x * x for x in vector_a))
    norm_b = math.sqrt(sum(y * y for y in vector_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
