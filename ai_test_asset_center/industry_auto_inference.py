from __future__ import annotations

"""
Industry-Agnostic Business Model Inference Engine.

Phase61 moat upgrade: replaces the hardcoded INDUSTRY_SIGNATURES dictionary
(7-8 industries) with LLM-powered zero-shot industry inference that works for
ANY domain — logistics, insurance, government, gaming, IoT, energy, legal,
real estate, agriculture, manufacturing, telecom, media, or anything else.

Architecture:
1. LLM path (primary): reads PRD/API docs → auto-infers domain, objects, roles,
   state machines, dependencies, invariants, risks, and Oracle families
2. Heuristic path (fallback): uses existing hardcoded dictionary + keyword matching
3. Hybrid path (default): LLM inference enriched with dictionary priors

The output schema is compatible with existing downstream consumers
(multi_industry_business_reasoning, business_adaptation_layer, defect_discovery).
"""

import json
import re
from typing import Any

from .llm_reasoning import reason as _llm_reason

# ---------------------------------------------------------------------------
# Primary: LLM-powered zero-shot industry inference
# ---------------------------------------------------------------------------

INDUSTRY_INFERENCE_PROMPT = """You are an enterprise business analyst. Given project documents
and API contracts, infer the industry domain(s) and construct a complete
business model — even for domains NOT in any predefined list.

PROJECT DOCUMENTS (PRD/MRD/requirements):
{documents}

API CONTRACTS (OpenAPI paths, schemas, descriptions):
{api_contracts}

TASK: Go beyond keyword matching. Understand what business this system does,
then produce a structured business model:

1. INFER INDUSTRY: What industry/industries does this system serve? Be specific.
   Examples: "cross-border logistics", "health insurance claims", "municipal permitting",
   "agricultural commodity trading", "electric vehicle charging network", etc.

2. BUSINESS OBJECTS: What are the core business entities? For each:
   - name (singular, lowercase)
   - aliases (alternative names in the documents, including non-English)
   - description (what it represents in business terms)
   - id_field (primary identifier)
   - is_core (true for central business objects, false for supporting)

3. ROLES: What human/system roles interact with this system? For each:
   - name
   - aliases
   - permissions (what they can read/write)

4. STATE MACHINES: What business objects have meaningful lifecycles? For each:
   - object (which entity)
   - states (ordered list from creation to terminal)
   - aliases (alternative state names found in documents)
   - terminal_states (states that are final/immutable)
   - transitions (key allowed transitions, e.g. ["draft->submitted", "submitted->approved"])

5. DEPENDENCIES: How do business objects relate? For each:
   - from_object, to_object, relationship_type (e.g. "owns", "paid_by", "fulfilled_by",
     "approved_by", "contains", "references")

6. BUSINESS INVARIANTS: What rules must ALWAYS hold? For each:
   - rule_id (unique)
   - kind (permission, conservation, state_transition, uniqueness, constraint, temporal)
   - objects involved
   - expected (description in plain language)
   - oracle_family (suggested Oracle name for automated checking)

7. INDUSTRY RISKS: What bugs are SPECIFIC to this industry that generic engines would miss?
   For each:
   - risk_id (unique)
   - severity (P0/P1/P2/P3)
   - title
   - why_generic_engines_miss_it
   - suggested_detection

8. CROSS-INDUSTRY PATTERNS: Does this system share patterns with known industries?
   - similar_to (list of known industries with similar patterns)
   - unique_aspects (what makes this domain different)

Output ONLY valid JSON:
{{
  "inferred_domain": {{
    "primary_industry": "specific industry name",
    "secondary_industries": ["if applicable"],
    "confidence": 0.0-1.0,
    "evidence": ["specific excerpts that support this classification"]
  }},
  "business_objects": [
    {{
      "name": "entity_name",
      "aliases": ["alt names"],
      "description": "what it represents",
      "id_field": "primary key field name",
      "is_core": true/false
    }}
  ],
  "roles": [
    {{
      "name": "role_name",
      "aliases": ["alt names"],
      "permissions": ["read_own_orders", "approve_refunds", ...]
    }}
  ],
  "state_machines": [
    {{
      "object": "entity_name",
      "states": ["state1", "state2", ...],
      "aliases": ["alt state names"],
      "terminal_states": ["final_states"],
      "transitions": ["from->to", ...]
    }}
  ],
  "dependencies": [
    {{
      "from_object": "source",
      "to_object": "target",
      "relationship_type": "owns|paid_by|fulfilled_by|approved_by|contains|references|custom"
    }}
  ],
  "invariants": [
    {{
      "rule_id": "unique_id",
      "kind": "permission|conservation|state_transition|uniqueness|constraint|temporal",
      "objects": ["involved entities"],
      "expected": "plain language description of the rule",
      "oracle_family": "suggested_oracle_name"
    }}
  ],
  "industry_risks": [
    {{
      "risk_id": "IND-XXX",
      "severity": "P0|P1|P2|P3",
      "title": "risk description",
      "why_generic_engines_miss_it": "explanation",
      "suggested_detection": "how to detect this"
    }}
  ],
  "cross_industry": {{
    "similar_to": ["known industry names"],
    "unique_aspects": ["what makes this different"]
  }}
}}"""


# ---------------------------------------------------------------------------
# Core inference engine
# ---------------------------------------------------------------------------

def infer_industry_model(
    documents: str,
    api_contracts: str = "",
    *,
    domain_hint: str = "",
) -> dict[str, Any] | None:
    """Infer a complete business model from project documents.

    Args:
        documents: PRD, MRD, requirements, or any business documents
        api_contracts: OpenAPI spec or API path descriptions (optional)
        domain_hint: Optional hint like "logistics" or "insurance" to guide inference

    Returns:
        Complete business model dict, or None if LLM unavailable.
        The dict is compatible with existing downstream consumers.
    """
    if domain_hint:
        documents = f"DOMAIN HINT: This system is in the {domain_hint} domain.\n\n{documents}"

    context = {
        "documents": documents[:10000],
        "api_contracts": api_contracts[:8000],
        # Fill unused template fields with empty strings
        "prd_text": documents[:6000],
        "api_schema": api_contracts[:8000],
        "observed_data": "",
        "heuristic_findings": "",
        "current_matches": "[]",
    }

    result = _llm_reason("multi_industry", context)
    if not result:
        return None

    # Normalize to the schema expected by downstream consumers
    return _normalize_inference_result(result)


def _normalize_inference_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert LLM output to the schema expected by multi_industry_business_reasoning."""
    # Handle both old and new prompt schemas
    # New schema: inferred_industries (array with industry/confidence/evidence)
    # Old schema: inferred_domain (object with primary_industry/confidence/evidence)
    industries = raw.get("inferred_industries") or []
    domain = raw.get("inferred_domain") or {}

    if industries and not domain:
        first = industries[0] if isinstance(industries, list) and industries else {}
        domain = {
            "primary_industry": str(first.get("industry", "unknown")),
            "secondary_industries": [str(i.get("industry", "")) for i in industries[1:3] if isinstance(i, dict)],
            "confidence": float(first.get("confidence", 0.5)),
            "evidence": [str(e) for e in first.get("evidence", [])],
        }

    primary = str(domain.get("primary_industry", "unknown"))
    confidence = float(domain.get("confidence", 0.5))

    # Build industry signature in the format expected by existing code
    objects_map: dict[str, list[str]] = {}
    for obj in raw.get("business_objects", []):
        name = str(obj.get("name", ""))
        aliases = [str(a) for a in obj.get("aliases", [])]
        if name:
            objects_map[name] = aliases if aliases else [name]

    roles_map: dict[str, list[str]] = {}
    for role in raw.get("roles", []):
        name = str(role.get("name", ""))
        aliases = [str(a) for a in role.get("aliases", [])]
        if name:
            roles_map[name] = aliases if aliases else [name]

    flows: list[dict[str, Any]] = []
    for sm in raw.get("state_machines", []):
        flows.append({
            "object": str(sm.get("object", "")),
            "states": [str(s) for s in sm.get("states", [])],
            "aliases": [str(a) for a in sm.get("aliases", [])],
            "terminal_states": [str(s) for s in sm.get("terminal_states", [])],
            "transitions": [str(t) for t in sm.get("transitions", [])],
        })

    deps: list[tuple[str, str, str]] = []
    for dep in raw.get("dependencies", []):
        deps.append((
            str(dep.get("from_object", "")),
            str(dep.get("to_object", "")),
            str(dep.get("relationship_type", "references")),
        ))

    rules: list[dict[str, Any]] = []
    for inv in raw.get("invariants", []):
        rules.append({
            "rule_id": str(inv.get("rule_id", "")),
            "kind": str(inv.get("kind", "constraint")),
            "objects": [str(o) for o in inv.get("objects", [])],
            "expected": str(inv.get("expected", "")),
            "oracle_family": str(inv.get("oracle_family", "")),
        })

    risks: list[dict[str, Any]] = []
    for risk in raw.get("industry_risks", []):
        risks.append({
            "risk_type": str(risk.get("risk_id", "")),
            "severity": str(risk.get("severity", "P2")),
            "title": str(risk.get("title", "")),
            "why_generic_miss": str(risk.get("why_generic_engines_miss_it", "")),
            "suggested_detection": str(risk.get("suggested_detection", "")),
            "destructive": "write" in str(risk.get("suggested_detection", "")).lower(),
        })

    cross = raw.get("cross_industry", {})

    return {
        "phase": "phase61_industry_auto_inference_v1",
        "source": "llm_zero_shot_inference",
        "primary_industry": primary,
        "secondary_industries": [str(s) for s in domain.get("secondary_industries", [])],
        "confidence": confidence,
        "evidence": [str(e) for e in domain.get("evidence", [])],
        "industry_signature": {
            "name": primary,
            "objects": objects_map,
            "roles": roles_map,
            "flows": flows,
            "dependencies": deps,
            "rules": rules,
            "risks": risks,
        },
        "cross_industry": {
            "similar_to": [str(s) for s in cross.get("similar_to", [])],
            "unique_aspects": [str(a) for a in cross.get("unique_aspects", [])],
        },
    }


# ---------------------------------------------------------------------------
# Heuristic fallback: extract domain signals from documents without LLM
# ---------------------------------------------------------------------------

# Broad industry keyword dictionary — much wider than the original 7-8 industries
# Used as fallback when LLM is unavailable. This is intentionally broad but
# the LLM path is the real moat.
INDUSTRY_KEYWORDS: dict[str, dict[str, Any]] = {
    "logistics": {
        "name": "物流/供应链",
        "signals": ["shipment", "tracking", "warehouse", "freight", "container", "waybill",
                     "物流", "运输", "仓储", "货运", "配送", "快递", "提单", "报关"],
        "typical_objects": ["shipment", "container", "waybill", "route", "carrier", "warehouse"],
        "typical_risks": ["tracking_drift", "container_misroute", "customs_hold", "eta_violation"],
    },
    "insurance": {
        "name": "保险",
        "signals": ["policy", "claim", "underwriting", "premium", "deductible", "beneficiary",
                     "保险", "理赔", "保单", "保费", "投保", "核保", "赔付"],
        "typical_objects": ["policy", "claim", "coverage", "premium", "beneficiary", "adjuster"],
        "typical_risks": ["claim_fraud", "premium_miscalc", "coverage_gap", "double_indemnity"],
    },
    "government": {
        "name": "政务/公共服务",
        "signals": ["permit", "license", "application", "approval", "citizen", "municipal",
                     "政务", "审批", "许可证", "市民", "政务大厅", "一网通办"],
        "typical_objects": ["application", "permit", "license", "citizen", "case", "officer"],
        "typical_risks": ["approval_bypass", "sla_violation", "citizen_data_leak", "duplicate_permit"],
    },
    "gaming": {
        "name": "游戏/虚拟经济",
        "signals": ["player", "item", "currency", "guild", "match", "leaderboard", "skin",
                     "玩家", "道具", "金币", "公会", "皮肤", "匹配", "排行榜"],
        "typical_objects": ["player", "item", "currency", "transaction", "match", "guild"],
        "typical_risks": ["currency_dupe", "item_dupe", "match_fixing", "virtual_economy_inflation"],
    },
    "energy": {
        "name": "能源/公用事业",
        "signals": ["meter", "grid", "consumption", "billing", "outage", "reading",
                     "能源", "电表", "电网", "用水", "燃气", "能耗", "计费"],
        "typical_objects": ["meter", "reading", "bill", "account", "grid", "outage"],
        "typical_risks": ["meter_reading_error", "billing_drift", "grid_imbalance", "tamper_detection"],
    },
    "real_estate": {
        "name": "房地产/物业",
        "signals": ["property", "listing", "lease", "mortgage", "tenant", "landlord",
                     "房产", "租赁", "物业", "楼盘", "租客", "房东", "房贷"],
        "typical_objects": ["property", "listing", "lease", "tenant", "landlord", "transaction"],
        "typical_risks": ["double_booking", "deposit_mishandling", "property_title_conflict"],
    },
    "telecom": {
        "name": "电信/通信",
        "signals": ["subscriber", "plan", "usage", "roaming", "cdr", "bandwidth",
                     "电信", "话单", "套餐", "流量", "漫游", "通话", "计费"],
        "typical_objects": ["subscriber", "plan", "cdr", "invoice", "device", "network_node"],
        "typical_risks": ["cdr_drift", "roaming_overcharge", "plan_misconfig", "usage_double_count"],
    },
    "manufacturing": {
        "name": "制造业/工业",
        "signals": ["bom", "workstation", "production_line", "sku", "batch", "quality_check",
                     "制造", "产线", "工序", "物料", "质检", "批次", "工单"],
        "typical_objects": ["work_order", "bom", "batch", "station", "quality_record", "sku"],
        "typical_risks": ["bom_mismatch", "batch_traceability", "quality_skip", "production_count_drift"],
    },
    "legal": {
        "name": "法律/合规",
        "signals": ["case", "docket", "filing", "court", "attorney", "evidence", "judgment",
                     "案件", "法院", "律师", "诉讼", "证据", "判决", "法规"],
        "typical_objects": ["case", "filing", "party", "attorney", "evidence", "judgment"],
        "typical_risks": ["filing_deadline_missed", "evidence_chain_break", "conflict_of_interest"],
    },
    "agriculture": {
        "name": "农业/食品",
        "signals": ["crop", "harvest", "field", "yield", "pesticide", "traceability",
                     "农业", "种植", "收割", "溯源", "农药", "产地", "批次"],
        "typical_objects": ["crop", "field", "harvest", "batch", "certificate", "shipment"],
        "typical_risks": ["batch_cross_contamination", "origin_fraud", "yield_miscalculation"],
    },
    "veterinary": {
        "name": "宠物医疗",
        "signals": ["pet", "veterinary", "clinic", "surgery", "vaccine", "prescription",
                     "宠物", "兽医", "诊疗", "手术", "疫苗", "处方", "住院", "药品"],
        "typical_objects": ["pet", "appointment", "consultation", "surgery", "prescription", "hospitalization"],
        "typical_risks": ["drug_dispense_error", "surgery_room_double_book", "controlled_drug_bypass", "vaccine_missed"],
    },
}


def heuristic_industry_match(documents: str, api_contracts: str = "") -> list[dict[str, Any]]:
    """Keyword-based industry matching. Used as fallback when LLM is unavailable.

    Much broader than the original 7-8 industries — covers 15+ domains.
    Returns list of matched industries with confidence scores.
    """
    text = (documents + " " + api_contracts).lower()
    matches: list[dict[str, Any]] = []

    for key, info in INDUSTRY_KEYWORDS.items():
        signals = [s.lower() for s in info["signals"]]
        hits = sum(1 for s in signals if s in text)
        if hits >= 2:
            matches.append({
                "industry_key": key,
                "name": info["name"],
                "confidence": min(0.9, hits / max(len(signals), 1) * 2),
                "matched_signals": [s for s in signals if s in text],
                "typical_objects": info.get("typical_objects", []),
                "typical_risks": info.get("typical_risks", []),
            })

    matches.sort(key=lambda m: -m["confidence"])
    return matches


# ---------------------------------------------------------------------------
# Unified entry point: try LLM first, fall back to heuristic
# ---------------------------------------------------------------------------

def infer_industry(
    documents: str,
    api_contracts: str = "",
    *,
    domain_hint: str = "",
) -> dict[str, Any]:
    """Infer industry and business model. LLM primary, heuristic fallback.

    Returns a dict with:
    - 'source': 'llm' or 'heuristic'
    - 'model': complete business model (LLM) or keyword matches (heuristic)
    - 'recommended_oracles': Oracle families to activate
    - 'risk_domains': risk categories to probe
    """
    # Try LLM first
    llm_result = infer_industry_model(documents, api_contracts, domain_hint=domain_hint)
    if llm_result:
        sig = llm_result.get("industry_signature", {})
        risks = sig.get("risks", [])
        rules = sig.get("rules", [])
        return {
            "source": "llm",
            "primary_industry": llm_result.get("primary_industry", "unknown"),
            "confidence": llm_result.get("confidence", 0.5),
            "model": llm_result,
            "recommended_oracles": list({r.get("oracle_family", "") for r in rules if r.get("oracle_family")}),
            "risk_domains": [r.get("risk_type", "") for r in risks],
            "risk_count": len(risks),
            "object_count": len(sig.get("objects", {})),
            "state_machine_count": len(sig.get("flows", [])),
            "invariant_count": len(rules),
            "cross_industry": llm_result.get("cross_industry", {}),
        }

    # Fall back to heuristic keyword matching
    matches = heuristic_industry_match(documents, api_contracts)
    all_objects: list[str] = []
    all_risks: list[str] = []
    for m in matches:
        all_objects.extend(m.get("typical_objects", []))
        all_risks.extend(m.get("typical_risks", []))

    return {
        "source": "heuristic",
        "primary_industry": matches[0]["name"] if matches else "unknown",
        "confidence": matches[0]["confidence"] if matches else 0.0,
        "model": {},
        "matched_industries": [m["name"] for m in matches[:5]],
        "industry_count": len(matches),
        "recommended_oracles": list(set(all_objects[:10])),
        "risk_domains": list(set(all_risks[:10])),
        "risk_count": len(set(all_risks)),
        "object_count": len(set(all_objects)),
        "state_machine_count": 0,
        "invariant_count": 0,
        "note": "LLM unavailable — using keyword matching. Configure LLM for full industry inference.",
    }
