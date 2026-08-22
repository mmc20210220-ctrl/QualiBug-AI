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
import socket
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
    # Input/context token budget. ``max_tokens`` is the completion cap only;
    # this bounds the request messages so an oversized corpus fails fast with a
    # visible ``context_overflow`` receipt instead of an unbounded provider 400
    # retried by every engine. Default 900000 stays below DeepSeek's ~1M window
    # while allowing large-but-legitimate prompts. CJK text can be ~2-4x its
    # character count, so this is enforced by an estimate, not by char slicing.
    max_input_tokens: int = 900000
    thinking_mode: str = ""
    response_format: str = ""
    embedding_model: str = ""
    # Optional unit prices (USD per 1M tokens) used only for the LLM
    # observability receipt's cost estimate. When unset the receipt records
    # token counts without any cost claim (never invents a price).
    cost_per_1m_input_usd: float | None = None
    cost_per_1m_output_usd: float | None = None
    embedding_cost_per_1m_input_usd: float | None = None

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
            max_input_tokens=int(os.getenv("LLM_MAX_INPUT_TOKENS", "900000")),
            thinking_mode=thinking_mode,
            response_format=response_format,
            embedding_model=os.getenv("LLM_EMBEDDING_MODEL", "").strip(),
            cost_per_1m_input_usd=_env_float("LLM_COST_PER_1M_INPUT_USD"),
            cost_per_1m_output_usd=_env_float("LLM_COST_PER_1M_OUTPUT_USD"),
            embedding_cost_per_1m_input_usd=_env_float("LLM_EMBEDDING_COST_PER_1M_INPUT_USD"),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def _env_float(name: str) -> float | None:
    """Parse an optional numeric env var; empty/invalid values stay None."""
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# CJK-aware input-token estimate. OpenAI-style tokenizers treat most CJK
# ideographs as one token each while ASCII words split further; the widely-used
# character/4 heuristic drastically *under*-estimates Chinese text, which is
# exactly why an oversized corpus passed a char-based guard and hit the
# provider 400. This estimator weights non-ASCII (mostly CJK) as ~1 token per
# char and ASCII as ~1 token per 4 chars — conservative, dependency-free, and
# never claims tokenizer precision (it is a guard, not a meter).
_CJK_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]")


def estimate_input_tokens(text: str) -> int:
    """Conservative dependency-free input-token estimate for a message string."""
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    ascii_chars = len(text) - cjk_chars
    return cjk_chars + int(math.ceil(ascii_chars / 4))


# ---------------------------------------------------------------------------
# Per-call LLM observation ledger (process-global)
# ---------------------------------------------------------------------------
#
# Every ReasoningClient (engines create fresh short-lived clients per attempt,
# including the stage_reason_all_v2 workers) records one observation per LLM
# round trip into this module-level ledger, so a scan can aggregate usage
# across all call points. Records contain counts, latency and status only —
# never prompt text, model output text, credentials or request bodies.

LLM_OBSERVATION_LEDGER_MAX = 20000
_LLM_OBSERVATION_LOCK = threading.Lock()
_LLM_OBSERVATIONS: list[dict[str, Any]] = []
_LLM_OBSERVATIONS_TRUNCATED = False
# ── 运行级成本熔断器（⑥）────────────────────────────────────────────
# 操作员经 LLM_RUN_MAX_INPUT_TOKENS 声明本次运行允许消耗的输入 token 上限
# （0/未设 = 关闭）。进程内累计计数随观测账本同生命周期；传输前预检，
# 超限调用以具名错误 llm_run_budget_exhausted（QB-L009）快速失败——
# 在途调用不受影响，后续调用全部快速失败并留痕，杜绝“供应商 402 才
# 发现破产”的最坏成本形态。
RUN_INPUT_BUDGET_ENV = "LLM_RUN_MAX_INPUT_TOKENS"
_LLM_RUN_INPUT_SPENT = 0


def _run_input_budget() -> int:
    raw = os.getenv(RUN_INPUT_BUDGET_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _llm_run_input_spent() -> int:
    with _LLM_OBSERVATION_LOCK:
        return _LLM_RUN_INPUT_SPENT


def _llm_run_input_charge(input_tokens: Any) -> None:
    global _LLM_RUN_INPUT_SPENT
    try:
        cost = int(input_tokens)
    except (TypeError, ValueError):
        return
    if cost <= 0:
        return
    with _LLM_OBSERVATION_LOCK:
        _LLM_RUN_INPUT_SPENT += cost


def record_llm_observation(observation: dict[str, Any]) -> None:
    """Append one per-call observation; bounded FIFO so memory stays flat."""
    global _LLM_OBSERVATIONS_TRUNCATED
    with _LLM_OBSERVATION_LOCK:
        if len(_LLM_OBSERVATIONS) >= LLM_OBSERVATION_LEDGER_MAX:
            _LLM_OBSERVATIONS.pop(0)
            _LLM_OBSERVATIONS_TRUNCATED = True
        _LLM_OBSERVATIONS.append(dict(observation))


def llm_observation_snapshot() -> list[dict[str, Any]]:
    """Return a copy of all recorded per-call observations."""
    with _LLM_OBSERVATION_LOCK:
        return [dict(observation) for observation in _LLM_OBSERVATIONS]


def reset_llm_observations() -> None:
    """Clear the ledger (test isolation / operator reset)."""
    global _LLM_OBSERVATIONS_TRUNCATED, _LLM_RUN_INPUT_SPENT
    with _LLM_OBSERVATION_LOCK:
        _LLM_OBSERVATIONS.clear()
        _LLM_OBSERVATIONS_TRUNCATED = False
        _LLM_RUN_INPUT_SPENT = 0


def _llm_observations_truncated() -> bool:
    with _LLM_OBSERVATION_LOCK:
        return _LLM_OBSERVATIONS_TRUNCATED


def build_llm_observability_receipt() -> dict[str, Any]:
    """Aggregate all recorded LLM calls into a bounded, content-free receipt.

    Schema ``qualibug.llm-observability.v1``: total calls, latency percentiles,
    token totals, estimated cost (only when unit prices are configured),
    failure counts, per-call-point distribution, and the ≤20 slowest calls.
    Contains metadata only — never prompt text, model output, credentials or
    request bodies. Empty ledger produces an honest zero-filled receipt.
    """
    observations = llm_observation_snapshot()
    calls = [o for o in observations if o["kind"] in ("chat", "embedding")]
    processing_failures = [o for o in observations if o["kind"] == "response_processing" and not o["success"]]
    succeeded = [o for o in calls if o["success"]]
    failed = [o for o in calls if not o["success"]]
    latencies = sorted(int(o["latency_ms"] or 0) for o in calls)

    def _percentile(sorted_values: list[int], fraction: float) -> int | None:
        if not sorted_values:
            return None
        index = int(round(fraction * (len(sorted_values) - 1)))
        return sorted_values[index]

    total_latency = sum(latencies)
    total_input = sum(int(o["input_tokens"] or 0) for o in calls)
    total_output = sum(int(o["output_tokens"] or 0) for o in calls)
    tokens_estimated_calls = sum(1 for o in calls if o["tokens_estimated"])
    costs = [float(o["cost_estimate_usd"]) for o in calls if o["cost_estimate_usd"] is not None]
    total_cost = round(sum(costs), 6) if costs else None

    by_call_point: dict[str, dict[str, Any]] = {}
    for o in calls:
        entry = by_call_point.setdefault(str(o["call_point"] or "unknown"), {
            "calls": 0,
            "failed": 0,
            "total_latency_ms": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_estimated_cost_usd": 0.0,
            "cost_covered_calls": 0,
        })
        entry["calls"] += 1
        entry["failed"] += 0 if o["success"] else 1
        entry["total_latency_ms"] += int(o["latency_ms"] or 0)
        entry["total_input_tokens"] += int(o["input_tokens"] or 0)
        entry["total_output_tokens"] += int(o["output_tokens"] or 0)
        if o["cost_estimate_usd"] is not None:
            entry["total_estimated_cost_usd"] += float(o["cost_estimate_usd"])
            entry["cost_covered_calls"] += 1
    for entry in by_call_point.values():
        entry["total_estimated_cost_usd"] = round(entry["total_estimated_cost_usd"], 6)

    # Make It Observable：按调用者身份分桶——成本账本必须能回答
    # “这些 token 是谁花的”。未声明的调用聚合在 "unattributed" 桶，
    # 让归因缺口本身可见，而不是消失在总量里。
    by_caller: dict[str, dict[str, Any]] = {}
    for o in calls:
        entry = by_caller.setdefault(str(o.get("caller") or "") or "unattributed", {
            "calls": 0,
            "failed": 0,
            "total_latency_ms": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_estimated_cost_usd": 0.0,
            "cost_covered_calls": 0,
        })
        entry["calls"] += 1
        entry["failed"] += 0 if o["success"] else 1
        entry["total_latency_ms"] += int(o["latency_ms"] or 0)
        entry["total_input_tokens"] += int(o["input_tokens"] or 0)
        entry["total_output_tokens"] += int(o["output_tokens"] or 0)
        if o["cost_estimate_usd"] is not None:
            entry["total_estimated_cost_usd"] += float(o["cost_estimate_usd"])
            entry["cost_covered_calls"] += 1
    for entry in by_caller.values():
        entry["total_estimated_cost_usd"] = round(entry["total_estimated_cost_usd"], 6)

    failure_reasons: dict[str, int] = {}
    failure_codes: dict[str, int] = {}
    for o in failed:
        reason = str(o["failure_reason"] or "unknown")
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        code = str(o["failure_code"] or "none")
        failure_codes[code] = failure_codes.get(code, 0) + 1
    processing_reasons: dict[str, int] = {}
    for o in processing_failures:
        reason = str(o["failure_reason"] or "unknown")
        processing_reasons[reason] = processing_reasons.get(reason, 0) + 1
    # Parse *recoveries* are successful response_processing entries whose
    # failure_reason carries "recovered:<method>" — content-free visibility
    # into how often the tolerant parser had to salvage model output.
    recoveries = [o for o in observations if o["kind"] == "response_processing" and o["success"]]
    recovery_methods: dict[str, int] = {}
    for o in recoveries:
        method = str(o["failure_reason"] or "unknown")
        recovery_methods[method] = recovery_methods.get(method, 0) + 1

    top_slow = sorted(calls, key=lambda o: int(o["latency_ms"] or 0), reverse=True)[:20]
    top_slow_calls = [
        {
            "call_point": o["call_point"],
            "caller": str(o.get("caller") or "") or "unattributed",
            "kind": o["kind"],
            "model": o["model"],
            "success": o["success"],
            "http_status": o["http_status"],
            "latency_ms": int(o["latency_ms"] or 0),
            "input_tokens": o["input_tokens"],
            "output_tokens": o["output_tokens"],
            "tokens_estimated": o["tokens_estimated"],
            "failure_reason": o["failure_reason"],
            "started_at_utc": o["started_at_utc"],
        }
        for o in top_slow
    ]

    return {
        "schema_version": "qualibug.llm-observability.v1",
        "produced_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "observations_recorded": len(observations),
        "observations_truncated": _llm_observations_truncated(),
        "summary": {
            "total_calls": len(calls),
            "successful_calls": len(succeeded),
            "failed_calls": len(failed),
            "response_processing_failures": len(processing_failures),
            "parse_recovered_calls": len(recoveries),
            "total_latency_ms": total_latency,
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "latency_max_ms": _percentile(latencies, 1.00),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "tokens_estimated_calls": tokens_estimated_calls,
            "total_retry_count": sum(int(o["retry_count"] or 0) for o in calls),
            "total_estimated_cost_usd": total_cost,
            "cost_basis": "configured_unit_prices" if total_cost is not None else "not_configured",
            "cost_note": None if total_cost is not None else (
                "No unit price configured (LLM_COST_PER_1M_INPUT_USD / "
                "LLM_COST_PER_1M_OUTPUT_USD); tokens recorded without cost."
            ),
        },
        "by_call_point": by_call_point,
        "by_caller": by_caller,
        # 熔断器状态显影：limit=None 表示操作员未启用运行级预算
        "run_input_budget": {
            "limit": _run_input_budget() or None,
            "spent_input_tokens": total_input,
            "exhausted_calls": failure_codes.get("QB-L009", 0),
        },
        "failures": {
            "count": len(failed),
            "by_reason": failure_reasons,
            "by_code": failure_codes,
        },
        "response_processing_failures": {
            "count": len(processing_failures),
            "by_reason": processing_reasons,
        },
        "parse_recoveries": {
            "count": len(recoveries),
            "by_method": recovery_methods,
        },
        "top_slow_calls": top_slow_calls,
    }


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


# Sentinel returned by the tolerant JSON parser when a candidate does not
# parse at all (distinct from a candidate that parses to a non-dict root).
_UNPARSEABLE = object()


class _JsonContentParseError(ReasoningClientError):
    """Internal: content-level JSON parse failure carrying a granular reason.

    Never escapes :meth:`ReasoningClient._parse_json` — it is translated into
    a plain :class:`ReasoningClientError` (plus a granular observation) so
    downstream message/type matchers keep their existing contracts.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


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

    @staticmethod
    def _extract_usage(response_text: str) -> dict[str, Any]:
        """Extract the provider-reported usage object (never the content)."""
        try:
            response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return {}
        usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
        return dict(usage)

    @staticmethod
    def _usage_token_counts(usage: dict[str, Any]) -> tuple[int | None, int | None]:
        """(input, output) token counts from a provider usage object, or None
        when the provider did not report them."""
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))

        def _to_int(value: Any) -> int | None:
            if value is None:
                return None
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return None

        return _to_int(prompt), _to_int(completion)

    def _estimate_cost_usd(self, kind: str, input_tokens: int, output_tokens: int) -> float | None:
        """Estimate cost from configured unit prices. Returns None when no
        unit price is configured — the receipt then records tokens only."""
        if kind == "embedding":
            price_in = self.config.embedding_cost_per_1m_input_usd
            if price_in is None:
                price_in = self.config.cost_per_1m_input_usd
            if price_in is None:
                return None
            return round((input_tokens or 0) / 1_000_000.0 * price_in, 8)
        price_in = self.config.cost_per_1m_input_usd
        price_out = self.config.cost_per_1m_output_usd
        if price_in is None or price_out is None:
            return None
        return round(
            (input_tokens or 0) / 1_000_000.0 * price_in
            + (output_tokens or 0) / 1_000_000.0 * price_out,
            8,
        )

    def _record_observation(
        self,
        *,
        call_point: str,
        kind: str,
        model: str | None,
        success: bool,
        latency_ms: int,
        http_status: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        tokens_estimated: bool = False,
        failure_reason: str | None = None,
        failure_code: str | None = None,
        caller: str = "",
    ) -> None:
        """Record one per-call observation. Metadata only — never prompt or
        model-output content. Observation never affects the call outcome."""
        cost = None
        if success and input_tokens is not None and output_tokens is not None:
            cost = self._estimate_cost_usd(kind, input_tokens, output_tokens)
        record_llm_observation({
            "call_point": call_point,
            # Make It Observable：每条观测必须携带调用者身份，否则成本账本
            # 无法回答“这些 token 是谁花的”（20260821 审计教训）。
            "caller": str(caller or ""),
            "kind": kind,
            "model": model,
            "success": bool(success),
            "http_status": http_status,
            "latency_ms": int(latency_ms or 0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens_estimated": bool(tokens_estimated),
            # The client performs at most one bounded internal retry per
            # call — and only for response-parse failures (see
            # _chat_with_parse_retry). Every attempt surfaces as its own
            # observation, so the receipt never hides retried traffic.
            "retry_count": 0,
            "failure_reason": failure_reason,
            "failure_code": failure_code,
            "cost_estimate_usd": cost,
            "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        # 熔断器计费：任何真实/预估的输入消耗都计入运行累计（response_processing
        # 除外——它不产生传输）。预算预检在 _chat 传输前执行。
        if kind in ("chat", "embedding") and input_tokens is not None:
            _llm_run_input_charge(input_tokens)

    def _record_processing_failure(self, call_point: str, failure_reason: str, caller: str = "") -> None:
        """Record a response-processing (parse) failure for a round trip that
        already succeeded at the HTTP layer."""
        self._record_observation(
            call_point=call_point,
            caller=caller,
            kind="response_processing",
            model=None,
            success=False,
            latency_ms=0,
            http_status=None,
            failure_reason=failure_reason,
            failure_code=None,
        )

    def _record_processing_recovery(self, call_point: str, method: str, caller: str = "") -> None:
        """Record a successful response-processing parse that required
        tolerance (fence/comment stripping, substring extraction, truncation
        closure). ``failure_reason`` carries ``recovered:<method>`` and is
        aggregated by the receipt's ``parse_recoveries`` section."""
        self._record_observation(
            call_point=call_point,
            caller=caller,
            kind="response_processing",
            model=None,
            success=True,
            latency_ms=0,
            http_status=None,
            failure_reason=f"recovered:{method}",
            failure_code=None,
        )

    def _record_usage(self, response_text: str) -> None:
        try:
            response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return
        usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
        prompt, completion = self._usage_token_counts(usage)
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
            return self._chat_with_parse_retry(
                user_prompt,
                system_prompt=system_prompt,
                model=model,
                call_point=engine_type,
            )
        except Exception:
            return None

    def _chat(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        call_point: str | None = None,
        caller: str = "",
        max_input_tokens: int | None = None,
    ) -> str:
        resolved_model = model or self.config.model
        call_point = call_point or "chat"
        # Per-call budget override: consumers with small-context contracts
        # (e.g. the semantic linker) declare their own bound instead of
        # inheriting the engine-sized global default.
        _input_budget = self.config.max_input_tokens
        try:
            if max_input_tokens is not None and int(max_input_tokens) > 0:
                _input_budget = int(max_input_tokens)
        except (TypeError, ValueError):
            pass
        _prompt_len = len(user_prompt)
        _system_len = len(system_prompt or SYSTEM_PROMPT)
        # ── Input-context guard: fail fast before transport when the combined
        # messages exceed the configured input token budget. An oversized
        # corpus must surface as a visible ``context_overflow`` receipt at the
        # first engine that trips it, never as a provider 400 repeated by every
        # engine for the length of the scan.
        _input_tokens = estimate_input_tokens(user_prompt) + estimate_input_tokens(
            system_prompt or SYSTEM_PROMPT
        )
        # ── 运行级成本熔断器（⑥）───────────────────────────────────────
        # 传输前预检：本次预估 + 运行累计 > 操作员预算 → 快速失败，绝不发送。
        # 在途调用不受影响；后续调用同样快速失败并留痕，杜绝“供应商 402 才
        # 发现破产”的最坏成本形态。
        _run_budget = _run_input_budget()
        if _run_budget > 0:
            _projected_spend = _llm_run_input_spent() + _input_tokens
            if _projected_spend > _run_budget:
                self._record_observation(
                    call_point=call_point,
                    caller=caller,
                    kind="chat",
                    model=resolved_model,
                    success=False,
                    http_status=None,
                    latency_ms=0,
                    failure_reason="run_budget_exhausted",
                    failure_code="QB-L009",
                    input_tokens=_input_tokens,
                    tokens_estimated=True,
                )
                _llm_logger.error(
                    "LLM run input budget exhausted: call_point=%s projected=%d spent=%d limit=%d",
                    call_point,
                    _projected_spend,
                    _llm_run_input_spent(),
                    _run_budget,
                    extra={"error_code": "QB-L009", "context": {
                        "call_point": call_point,
                        "caller": caller,
                        "projected_input_tokens": _projected_spend,
                        "spent_input_tokens": _llm_run_input_spent(),
                        "run_max_input_tokens": _run_budget,
                    }},
                )
                raise ReasoningClientError(
                    f"LLM run input budget exhausted: "
                    f"projected {_projected_spend} tokens > limit {_run_budget}"
                )
        if _input_budget > 0 and _input_tokens > _input_budget:
            _elapsed_ms = 0
            self._record_observation(
                call_point=call_point,
                caller=caller,
                kind="chat",
                model=resolved_model,
                success=False,
                http_status=None,
                latency_ms=_elapsed_ms,
                failure_reason="context_overflow",
                failure_code="QB-L007",
                input_tokens=_input_tokens,
                tokens_estimated=True,
            )
            _llm_logger.error(
                "LLM input context overflow: call_point=%s prompt=%dc system=%dc est_tokens=%d budget=%d",
                call_point,
                _prompt_len,
                _system_len,
                _input_tokens,
                _input_budget,
                extra={"error_code": "QB-L007", "context": {
                    "call_point": call_point,
                    "model": resolved_model,
                    "prompt_chars": _prompt_len,
                    "system_chars": _system_len,
                    "estimated_input_tokens": _input_tokens,
                    "max_input_tokens": _input_budget,
                }},
            )
            raise ReasoningClientError(
                "LLM input context overflow: "
                f"estimated {_input_tokens} tokens > budget {_input_budget}"
            )
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
        # ── Final transport guard: the provider may accept the connection
        # and then never send a byte (half-open stream). Neither urllib's
        # socket timeout nor the application deadline can interrupt a recv
        # that never returns — the daemon worker + join(deadline) is the
        # last boundary. A hung worker is discarded (daemon, process can
        # exit); the failure path below records a visible timeout.
        _transport: dict[str, Any] = {}

        def _do_transport() -> None:
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as resp:
                    try:
                        _raw = getattr(getattr(resp, "fp", None), "raw", None)
                        _sock = getattr(_raw, "_sock", None)
                        if _sock is not None:
                            _sock.settimeout(self.config.timeout_seconds)
                    except Exception:
                        pass
                    # Application-level total-duration guard (half-open
                    # streams that trickle bytes); sized reads with a
                    # single-full-read fallback for test doubles.
                    _deadline = time.time() + self.config.timeout_seconds
                    try:
                        _chunk = resp.read(65536)
                    except TypeError:
                        _chunk = None
                    if _chunk is None:
                        _text_resp = resp.read().decode("utf-8")
                    else:
                        _chunks: list[bytes] = []
                        while _chunk:
                            if time.time() > _deadline:
                                raise socket.timeout(
                                    "LLM read exceeded timeout_seconds"
                                )
                            _chunks.append(_chunk)
                            _chunk = resp.read(65536)
                        _text_resp = b"".join(_chunks).decode("utf-8")
                    _transport["value"] = _text_resp
            except Exception as _exc:
                _transport["error"] = _exc

        _worker = threading.Thread(target=_do_transport, daemon=True)
        _worker.start()
        _worker.join(self.config.timeout_seconds + 10)
        if _worker.is_alive():
            # Transport hung beyond every deadline — discard the worker and
            # fail with the same timeout semantics as QB-L001.
            _elapsed_ms = int((time.time() - _llm_start) * 1000)
            raise socket.timeout(
                f"LLM transport exceeded timeout_seconds={self.config.timeout_seconds}"
            )
        try:
            if "error" in _transport:
                raise _transport["error"]
            response_text = _transport["value"]
            _elapsed_ms = int((time.time() - _llm_start) * 1000)
            self._record_usage(response_text)
            usage = self._extract_usage(response_text)
            input_tokens, output_tokens = self._usage_token_counts(usage)
            tokens_estimated = input_tokens is None or output_tokens is None
            if input_tokens is None:
                input_tokens = max(1, math.ceil(_prompt_len / 4))
            if output_tokens is None:
                output_tokens = max(0, math.ceil(len(response_text) / 4))
            self._record_observation(
                call_point=call_point,
                caller=caller,
                kind="chat",
                model=resolved_model,
                success=True,
                http_status=200,
                latency_ms=_elapsed_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tokens_estimated=tokens_estimated,
            )
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
            _error_low = error_body.lower()
            _is_context_overflow = (
                "context length" in _error_low
                or "maximum context" in _error_low
                or "reduce the length" in _error_low
                or "context_length_exceeded" in _error_low
            )
            if _is_context_overflow:
                _code = "QB-L007"
                _failure_reason = "context_overflow"
            elif exc.code in (401, 403):
                _code = "QB-L006"
                _failure_reason = "http_error"
            elif exc.code == 429:
                _code = "QB-L002"
                _failure_reason = "rate_limit"
            else:
                _code = "QB-L001"
                _failure_reason = "http_error"
            self._record_observation(
                call_point=call_point,
                caller=caller,
                kind="chat",
                model=resolved_model,
                success=False,
                http_status=exc.code,
                latency_ms=_elapsed_ms,
                failure_reason=_failure_reason,
                failure_code=_code,
            )
            _llm_logger.error(
                f"LLM HTTP {exc.code} after {_elapsed_ms}ms: {error_body[:200]}",
                extra={"error_code": _code, "context": {
                    "model": resolved_model,
                    "http_status": exc.code,
                    "elapsed_ms": _elapsed_ms,
                    "prompt_chars": _prompt_len,
                    "context_overflow": _is_context_overflow,
                }},
            )
            raise ReasoningClientError(f"LLM HTTP {exc.code}: {error_body[:500]}") from exc
        except urllib.error.URLError as exc:
            _elapsed_ms = int((time.time() - _llm_start) * 1000)
            _is_timeout = "timed out" in str(exc).lower() or _elapsed_ms >= (self.config.timeout_seconds * 1000 - 500)
            _code = "QB-L001" if _is_timeout else "QB-L004"
            self._record_observation(
                call_point=call_point,
                caller=caller,
                kind="chat",
                model=resolved_model,
                success=False,
                http_status=None,
                latency_ms=_elapsed_ms,
                failure_reason="timeout" if _is_timeout else "network_error",
                failure_code=_code,
            )
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

    def _chat_with_parse_retry(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None,
        model: str,
        call_point: str,
        caller: str = "",
        max_input_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run one chat round trip and parse its JSON contract, retrying the
        *identical payload* once when the parse fails.

        Parse failures happen when the provider returns output that the
        tolerant parser cannot salvage (explanation-wrapped prose, hard
        truncation, non-JSON content). Model output is sampled, so one bounded
        retry can yield a clean response; each attempt is recorded as its own
        observation and no more than one retry is ever made (run10 p50≈36s —
        unbounded retries would burn scan time). HTTP/network failures are
        never retried here; they already carry their own error contract.
        """
        raw = self._chat(
            user_prompt,
            system_prompt=system_prompt,
            model=model,
            call_point=call_point,
            caller=caller,
            max_input_tokens=max_input_tokens,
        )
        try:
            return self._parse_json(raw, call_point=call_point, caller=caller)
        except ReasoningClientError as exc:
            _llm_logger.info(
                "LLM parse failed, retrying once: %s",
                exc,
                extra={"context": {"call_point": call_point, "retry": 1}},
            )
            raw = self._chat(
                user_prompt,
                system_prompt=system_prompt,
                model=model,
                call_point=call_point,
                caller=caller,
                max_input_tokens=max_input_tokens,
            )
            return self._parse_json(raw, call_point=call_point, caller=caller)

    # ------------------------------------------------------------------
    # LLM output parse tolerance (root-cause fix for run10's 62% parse
    # failure rate on chat_json: DeepSeek wraps JSON in explanation text,
    # uses fenced JSON with language tags, emits JSONC comments/trailing
    # commas, returns content as part lists, or truncates at max_tokens).
    # Recovery is purely mechanical and content-free — it never infers
    # request bodies, credentials, business rules or impact.
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_message_content(raw: dict[str, Any]) -> tuple[Any, str]:
        """Pull the message content and finish_reason from a chat-completion
        envelope. Raises KeyError/IndexError/TypeError on shape violations so
        the caller can classify them as ``shape_error``.

        OpenAI-compatible providers (e.g. DeepSeek thinking mode) may return
        ``content`` as a list of typed parts or as a ``{"text": ...}`` dict
        instead of a plain string — all string parts are joined.
        """
        choices = raw["choices"]
        if not isinstance(choices, list) or not choices:
            raise KeyError("choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise KeyError("choice")
        message = choice["message"]
        if not isinstance(message, dict):
            raise KeyError("message")
        finish_reason = str(choice.get("finish_reason") or "").lower()
        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(part, str):
                    parts.append(part)
            content = "".join(parts)
        elif isinstance(content, dict) and isinstance(content.get("text"), str):
            content = content["text"]
        return content, finish_reason

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Strip one markdown ``` fence around JSON, tolerating a language
        tag (```json, ```JSON, ```text, ...). Text without a leading fence is
        returned unchanged; an unclosed fence keeps all lines after the
        opener as content."""
        stripped = text.strip()
        if not stripped.startswith("```"):
            return text
        lines = stripped.splitlines()
        if not lines or not lines[0].strip().startswith("```"):
            return text
        rest = lines[1:]
        if rest and rest[-1].strip().startswith("```"):
            rest = rest[:-1]
        return "\n".join(rest).strip()

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        """Remove JSONC comments (``//`` and ``/* */``) and trailing commas
        outside strings. String-aware, so markers inside quoted values are
        never touched."""
        out: list[str] = []
        i = 0
        n = len(text)
        in_str = False
        escape = False
        while i < n:
            ch = text[i]
            if in_str:
                out.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                i += 1
                continue
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
                continue
            if ch == "/" and i + 1 < n and text[i + 1] == "/":
                j = text.find("\n", i)
                i = n if j == -1 else j
                continue
            if ch == "/" and i + 1 < n and text[i + 1] == "*":
                j = text.find("*/", i + 2)
                i = n if j == -1 else j + 2
                continue
            if ch == ",":
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] in "}]":
                    i += 1
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _iter_json_spans(text: str):
        """Yield ``(candidate, closed)`` for each outermost JSON value found
        embedded in ``text``, scanning past a candidate when more text follows
        it (e.g. prose containing ``{placeholders}`` before the real JSON).

        ``closed=False`` means the text ends before the structure closes
        (provider truncation at max_tokens). Brace/bracket matching is
        string-aware: braces inside quoted values never count.
        """
        n = len(text)
        search_from = 0
        while search_from < n:
            start = -1
            in_str = False
            escape = False
            i = search_from
            while i < n:
                ch = text[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch in "{[":
                    start = i
                    break
                i += 1
            if start < 0:
                return
            stack: list[str] = []
            j = start
            in_str = False
            escape = False
            while j < n:
                ch = text[j]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch in "{[":
                        stack.append(ch)
                    elif ch == "}" and stack and stack[-1] == "{":
                        stack.pop()
                        if not stack:
                            break
                    elif ch == "]" and stack and stack[-1] == "[":
                        stack.pop()
                        if not stack:
                            break
                j += 1
            if stack:
                yield text[start:], False
                return
            end = j + 1
            yield text[start:end], True
            search_from = end

    @staticmethod
    def _close_truncated_json(text: str) -> str | None:
        """Progressively close truncated JSON: terminate a dangling string
        and every still-open ``{`` / ``[`` in reverse order. Returns the
        closed text, or None when nothing needs closing."""
        stack: list[str] = []
        in_str = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in "{[":
                stack.append(ch)
            elif ch == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "[":
                stack.pop()
        suffix = ""
        if in_str:
            suffix += '"'
        for opener in reversed(stack):
            suffix += "}" if opener == "{" else "]"
        if not suffix:
            return None
        return text + suffix

    @classmethod
    def _parse_content_json(cls, content: str) -> tuple[Any, str]:
        """Parse model ``content`` into a JSON object with progressive
        tolerance. Returns ``(parsed, recovery_method)`` where method is one
        of ``clean`` / ``fenced`` / ``comments`` / ``extracted`` /
        ``truncated_closed``. Raises :class:`_JsonContentParseError` with a
        granular reason (``not_json`` / ``prefix_text`` / ``truncated``) when
        nothing parses."""
        text = (content or "").lstrip("\ufeff \t\r\n")
        fenced_text = cls._strip_code_fence(text)
        fenced = fenced_text != text
        cleaned = cls._strip_json_comments(fenced_text)
        comments = cleaned != fenced_text

        def _parse_dict(candidate: str) -> tuple[Any, bool]:
            try:
                parsed = json.loads(candidate)
                return parsed, isinstance(parsed, dict)
            except (TypeError, json.JSONDecodeError):
                return _UNPARSEABLE, False

        non_dict_root = False
        saw_candidate = False

        parsed, is_dict = _parse_dict(fenced_text)
        if is_dict:
            return parsed, "fenced" if fenced else "clean"
        if parsed is not _UNPARSEABLE:
            non_dict_root = True

        parsed, is_dict = _parse_dict(cleaned)
        if is_dict:
            return parsed, "comments"
        if parsed is not _UNPARSEABLE:
            non_dict_root = True

        for candidate, closed in cls._iter_json_spans(cleaned):
            saw_candidate = True
            if not closed:
                closed_text = cls._close_truncated_json(candidate)
                if closed_text is not None:
                    parsed, is_dict = _parse_dict(closed_text)
                    if is_dict:
                        return parsed, "truncated_closed"
                    if parsed is not _UNPARSEABLE:
                        non_dict_root = True
                if non_dict_root:
                    raise _JsonContentParseError(
                        "shape_error", "LLM JSON root must be an object"
                    )
                raise _JsonContentParseError(
                    "truncated",
                    f"LLM output JSON is truncated and cannot be salvaged "
                    f"(parse_reason=truncated): {content[:300]}",
                )
            parsed, is_dict = _parse_dict(candidate)
            if is_dict:
                return parsed, "extracted"
            if parsed is not _UNPARSEABLE:
                non_dict_root = True

        if non_dict_root:
            raise _JsonContentParseError("shape_error", "LLM JSON root must be an object")
        if not saw_candidate:
            raise _JsonContentParseError(
                "not_json",
                f"LLM output is not valid JSON (parse_reason=not_json): {content[:300]}",
            )
        raise _JsonContentParseError(
            "prefix_text",
            f"LLM output JSON is malformed (parse_reason=prefix_text): {content[:300]}",
        )

    def _parse_json(
        self,
        response_text: str,
        *,
        call_point: str = "response_processing",
        caller: str = "",
    ) -> dict[str, Any]:
        """Parse the chat-completion envelope and its JSON content contract.

        Tolerance pipeline (all content-free):
        1. envelope shape extraction (``shape_error`` on violations, including
           DeepSeek-style content part lists);
        2. code-fence stripping with language tags;
        3. JSONC comment / trailing-comma stripping;
        4. embedded JSON substring extraction (``extracted``);
        5. truncation closure when the structure never closes
           (``truncated_closed`` when salvage succeeds).

        Failures are recorded with granular reasons — ``shape_error`` /
        ``not_json`` / ``prefix_text`` / ``truncated`` — and still raise
        plain :class:`ReasoningClientError`, so downstream matchers
        (agent_semantic_linker, stage_reason_all_v2) keep their contracts.
        """
        try:
            raw = json.loads(response_text)
        except (TypeError, json.JSONDecodeError) as exc:
            self._record_processing_failure(call_point, "shape_error", caller=caller)
            raise ReasoningClientError(f"Unexpected LLM response shape: {exc}") from exc
        if not isinstance(raw, dict):
            self._record_processing_failure(call_point, "shape_error", caller=caller)
            raise ReasoningClientError("Unexpected LLM response shape: envelope is not an object")
        try:
            content, finish_reason = self._extract_message_content(raw)
        except (KeyError, IndexError, TypeError) as exc:
            self._record_processing_failure(call_point, "shape_error", caller=caller)
            raise ReasoningClientError(f"Unexpected LLM response shape: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            self._record_processing_failure(call_point, "not_json", caller=caller)
            raise ReasoningClientError("LLM response did not include JSON content")

        truncated = finish_reason == "length"
        try:
            parsed, recovery = self._parse_content_json(content)
        except _JsonContentParseError as exc:
            # A length-limited response that still fails to parse is a
            # truncation casualty regardless of the mechanical reason.
            reason = "truncated" if truncated and exc.reason in ("prefix_text", "not_json") else exc.reason
            self._record_processing_failure(call_point, reason, caller=caller)
            message = str(exc)
            if reason != exc.reason:
                message = message.replace(f"parse_reason={exc.reason}", f"parse_reason={reason}")
            raise ReasoningClientError(message) from exc

        if recovery != "clean" or truncated:
            method = recovery
            if truncated and recovery in ("clean", "fenced", "comments", "extracted"):
                # Parsed but the provider cut the output at max_tokens —
                # mark the call so receipts never present a truncated dict
                # as a fully clean parse.
                method = "truncated_flagged"
            self._record_processing_recovery(call_point, method, caller=caller)
        return parsed

    def chat_json(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        tier: str = DEFAULT_TIER,
        caller: str = "",
        max_input_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run one JSON-only advisory request using the shared provider settings.

        ``tier`` selects the routed model: "light" (LLM_MODEL_LIGHT when
        configured) for extraction/classification tasks, "strong" (primary
        LLM_MODEL) otherwise. Defaults to strong so existing callers keep
        their historical model.

        ``caller`` is MANDATORY (Make It Observable): every LLM consumer must
        declare its identity so the cost ledger can attribute tokens to the
        stage that spent them. Empty/missing caller fails fast with
        ``llm_caller_attribution_required`` — an unattributed call is treated
        as a defect at the boundary, never silently recorded.

        ``max_input_tokens`` lets a consumer declare its own input budget for
        this call (overrides the global ``LLM_MAX_INPUT_TOKENS`` default,
        which is engine-sized). Oversized prompts fail fast with QB-L007.
        """
        if not self.config.enabled:
            raise ReasoningClientError("LLM is not configured")
        if not str(caller or "").strip():
            raise ReasoningClientError("llm_caller_attribution_required")
        model = resolve_model_for_tier(self.config, tier)
        return self._chat_with_parse_retry(
            user_prompt,
            system_prompt=system_prompt,
            model=model,
            call_point="chat_json",
            caller=str(caller).strip(),
            max_input_tokens=max_input_tokens,
        )

    def complete_json(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
        tier: str = DEFAULT_TIER,
        caller: str = "",
        max_input_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Expose the fail-fast JSON contract used by constrained Agent planners."""

        return self.chat_json(
            user_prompt,
            system_prompt=system_prompt,
            tier=tier,
            caller=caller,
            max_input_tokens=max_input_tokens,
        )

    def health_check(self) -> dict[str, Any]:
        """Perform a bounded provider check without storing credentials or prompts."""
        result = self.chat_json('Return only this JSON object: {"ok":true}.', caller="llm_health_check")
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

    def embed(self, texts: list[str], *, call_point: str = "embedding") -> list[list[float]] | None:
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
        _embed_start = time.time()
        _prompt_chars = sum(len(text) for text in clean)

        def _embedding_observation(*, success: bool, latency_ms: int, failure_reason: str | None = None, input_tokens: int | None = None, tokens_estimated: bool = False) -> None:
            self._record_observation(
                call_point=call_point,
                kind="embedding",
                model=self.config.embedding_model,
                success=success,
                http_status=200 if success else None,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=0,
                tokens_estimated=tokens_estimated,
                failure_reason=failure_reason,
                failure_code=None,
            )

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
            _embed_elapsed = int((time.time() - _embed_start) * 1000)
            self._record_embedding_usage(response_text)
            usage = self._extract_usage(response_text)
            input_tokens, _ = self._usage_token_counts(usage)
            tokens_estimated = input_tokens is None
            if input_tokens is None:
                input_tokens = max(1, math.ceil(_prompt_chars / 4))
            data = json.loads(response_text)
            if not isinstance(data, dict):
                _embedding_observation(success=False, latency_ms=_embed_elapsed, failure_reason="embedding_response_shape")
                return None
            vectors: list[list[float]] = []
            for item in data.get("data") or []:
                embedding = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(embedding, list) or not embedding:
                    _embedding_observation(success=False, latency_ms=_embed_elapsed, failure_reason="embedding_response_shape")
                    return None
                if not all(isinstance(value, (int, float)) for value in embedding):
                    _embedding_observation(success=False, latency_ms=_embed_elapsed, failure_reason="embedding_response_shape")
                    return None
                vectors.append([float(value) for value in embedding])
            if len(vectors) != len(clean):
                _embedding_observation(success=False, latency_ms=_embed_elapsed, failure_reason="embedding_response_shape")
                return None
            _embedding_observation(success=True, latency_ms=_embed_elapsed, input_tokens=input_tokens, tokens_estimated=tokens_estimated)
            _llm_logger.info(
                "LLM embedding OK: model=%s texts=%d dim=%d",
                self.config.embedding_model,
                len(clean),
                len(vectors[0]),
            )
            return vectors
        except Exception as exc:
            _embed_elapsed = int((time.time() - _embed_start) * 1000)
            _embedding_observation(success=False, latency_ms=_embed_elapsed, failure_reason="embedding_error")
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
                reader_output = client._chat_with_parse_retry(
                    reader_prompt,
                    system_prompt=READER_SYSTEM_PROMPT,
                    model=resolve_model_for_tier(client.config, DEFAULT_TIER),
                    call_point="reader",
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
            reasoner_output = client._chat_with_parse_retry(
                reasoner_prompt,
                system_prompt=REASONER_SYSTEM_PROMPT,
                model=resolve_model_for_tier(client.config, DEFAULT_TIER),
                call_point="reasoner",
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
            verifications = client._chat_with_parse_retry(
                verifier_prompt,
                system_prompt=VERIFIER_SYSTEM_PROMPT,
                model=resolve_model_for_tier(client.config, DEFAULT_TIER),
                call_point="verifier",
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
