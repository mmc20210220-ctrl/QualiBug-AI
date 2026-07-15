from __future__ import annotations

"""
Evidence & Impact Analyzer — Bug Reproducibility and Business Impact Assessment.

QualiBug NEVER modifies customer code. Our job is detection, evidence, and
impact assessment. The customer's engineering team decides how to fix.

For each confirmed bug, this module generates:
1. Reproducibility evidence — exact conditions, API calls, data state
2. Business impact assessment — what happens if this hits production
3. Risk classification — data corruption, financial loss, compliance, etc.
4. Investigation guidance — which team/service/area to look at (NOT how to fix)
5. Severity justification — why P0/P1/P2/P3 with business reasoning
"""

import json
import time
from typing import Any


# ---------------------------------------------------------------------------
# Evidence & Impact prompt
# ---------------------------------------------------------------------------

EVIDENCE_IMPACT_PROMPT = """You are an enterprise quality assurance analyst. Your job is to
assess the business impact and reproducibility of a bug — NOT to suggest fixes.

CRITICAL RULE: Do NOT suggest code changes, configuration changes, or fixes.
The customer's engineering team owns all code. You only provide evidence,
impact assessment, and investigation guidance.

BUG FINDING:
{bug_finding}

PROJECT CONTEXT (industry, API schema, business rules):
{project_context}

TASK: Produce an evidence and impact assessment:

1. REPRODUCIBILITY: What exact conditions reproduce this bug? What API calls,
   parameters, data state, and sequence of operations trigger it?

2. BUSINESS IMPACT: If this bug hits production or is exploited maliciously,
   what is the concrete business consequence? Quantify if possible.

3. RISK CLASSIFICATION: What type of risk does this represent?
   - data_corruption: data becomes inconsistent or wrong
   - financial_loss: direct monetary loss
   - compliance_violation: regulatory/legal violation
   - security_exposure: unauthorized access or data leak
   - operational_failure: system becomes unavailable or incorrect
   - reputation_damage: customer trust or brand damage

4. BLAST RADIUS: What other parts of the system could be affected?
   Which other entities, workflows, or reports might show cascading errors?

5. INVESTIGATION GUIDANCE: Which area of the system should the engineering
   team investigate? (e.g. "order service payment validation", "refund calculation
   in billing module", "database trigger on payments table"). Do NOT say how to fix.

6. SEVERITY JUSTIFICATION: Detailed business reasoning for P0/P1/P2/P3.

7. DETECTION STRENGTH: How confident are we that this is a real bug vs false positive?

Output ONLY valid JSON:
{{
  "reproducibility": {{
    "preconditions": "what state must the system be in",
    "trigger_sequence": ["step 1", "step 2", "..."],
    "evidence_fingerprint": "hash of redacted evidence for dedup",
    "reproducible": true/false,
    "reproduction_confidence": 0.0-1.0
  }},
  "business_impact": {{
    "summary": "one-line impact statement",
    "quantified_impact": "if quantifiable, e.g. 'up to X yuan per occurrence'",
    "affected_stakeholders": ["who is harmed"],
    "urgency": "immediate|this_sprint|next_sprint|backlog"
  }},
  "risk_classification": {{
    "primary_risk": "data_corruption|financial_loss|compliance_violation|security_exposure|operational_failure|reputation_damage",
    "secondary_risks": ["other risk types"],
    "data_risk": "none|corruption|leak|loss",
    "financial_risk": "none|potential|certain",
    "compliance_risk": "none|potential|certain"
  }},
  "blast_radius": {{
    "affected_entities": ["entity names"],
    "affected_workflows": ["business process names"],
    "cascading_effects": ["what else could break"],
    "detection_lag": "how long before someone notices"
  }},
  "investigation_guidance": {{
    "primary_area": "which module/service to investigate",
    "relevant_constraints": ["business rules that should hold"],
    "related_apis": ["API paths involved"],
    "data_to_examine": ["specific tables/fields/logs to check"]
  }},
  "severity_justification": {{
    "reasoning": "detailed business reasoning for the severity level",
    "customer_impact": "what the customer experiences",
    "regulatory_implications": "if any compliance rules are violated"
  }},
  "detection_assessment": {{
    "is_real_bug": true/false,
    "false_positive_risk": "why this might be a legitimate business case",
    "detection_quality": "heuristic_only|llm_confirmed|cross_validated"
  }}
}}"""


# ---------------------------------------------------------------------------
# Evidence & Impact Analyzer
# ---------------------------------------------------------------------------

def analyze_impact(
    bug_finding: dict[str, Any],
    project_context: str = "",
) -> dict[str, Any] | None:
    """Generate evidence and impact assessment for a bug finding.

    IMPORTANT: This does NOT suggest fixes. It provides evidence, impact
    assessment, and investigation guidance so the customer's engineering
    team can make their own informed decisions.
    """
    try:
        from .llm_reasoning import _get_client

        client = _get_client()
        if not client.config.enabled:
            return None

        bug_json = json.dumps(bug_finding, ensure_ascii=False, default=str)[:8000]
        user_prompt = EVIDENCE_IMPACT_PROMPT.format(
            bug_finding=bug_json,
            project_context=project_context[:4000],
        )

        system = "You are an enterprise quality assurance analyst. Output ONLY valid JSON. Never suggest code changes or fixes."
        # Use the shared client so provider-specific JSON/thinking controls are
        # applied consistently. The assessment is advisory, never defect proof.
        result = client.chat_json(user_prompt, system_prompt=system)
        if not isinstance(result, dict):
            return None
        model_detection = result.pop("detection_assessment", None)

        return {
            "bug_title": bug_finding.get("title", ""),
            "bug_severity": bug_finding.get("severity", ""),
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "llm_evidence_impact",
            "analysis_status": "advisory_unverified",
            "evidence_governance": {
                "llm_output_is_not_defect_confirmation": True,
                "requires_deterministic_replay": True,
                "source_finding_verification_level": str(bug_finding.get("verification_level") or "unknown"),
                "model_detection_assessment_preserved_as_advisory": bool(model_detection),
            },
            "detection_assessment": {
                "finding_verdict": "not_set_by_llm",
                "detection_quality": "llm_advisory_only",
                "requires_deterministic_replay": True,
                "model_advisory": model_detection if isinstance(model_detection, dict) else {},
            },
            **result,
        }
    except Exception:
        return None


def batch_analyze_impact(
    findings: list[dict[str, Any]],
    project_context: str = "",
    max_findings: int = 10,
) -> list[dict[str, Any]]:
    """Generate impact assessments for multiple findings. P0/P1 first."""
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    sorted_findings = sorted(
        findings,
        key=lambda f: priority_order.get(str(f.get("severity", "P3")), 3),
    )
    results = []
    for finding in sorted_findings[:max_findings]:
        analysis = analyze_impact(finding, project_context)
        if analysis:
            results.append(analysis)
    return results


# ---------------------------------------------------------------------------
# Lightweight heuristic — no LLM needed
# ---------------------------------------------------------------------------

def heuristic_impact_assessment(bug_finding: dict[str, Any]) -> dict[str, Any]:
    """Basic impact assessment from pattern matching. No LLM required."""

    bug_type = str(
        bug_finding.get("business_causality_type") or
        bug_finding.get("counterexample_type") or
        ""
    )

    templates = {
        "missing_side_effect": {
            "primary_risk": "operational_failure",
            "impact": "业务流程不完整：关键业务动作未产生必需的副作用记录。可能导致下游系统状态不一致。",
            "investigation": "检查触发副作用记录的业务逻辑和事务边界。",
        },
        "duplicate_side_effect": {
            "primary_risk": "data_corruption",
            "impact": "重复记录导致数据不一致。如果是支付/退款场景，可能导致重复扣款或重复退款。",
            "investigation": "检查幂等性保证机制：唯一约束、幂等键、分布式锁。",
        },
        "side_effect_amount_mismatch": {
            "primary_risk": "financial_loss",
            "impact": "金额不守恒：主实体与副作用实体的金额不一致。直接造成资损。",
            "investigation": "检查金额计算逻辑、精度处理、事务原子性。",
        },
        "referential_causality": {
            "primary_risk": "data_corruption",
            "impact": "引用完整性破坏：副作用记录引用了不存在的主实体。可能导致数据孤岛或错误关联。",
            "investigation": "检查外键约束和软删除级联逻辑。",
        },
        "collection_detail_projection": {
            "primary_risk": "data_corruption",
            "impact": "列表与详情数据不一致：不同视图对同一资源返回不同数据。影响所有依赖列表数据的下游系统。",
            "investigation": "检查列表和详情的查询路径是否一致，是否存在读副本延迟。",
        },
    }

    template = templates.get(bug_type, {
        "primary_risk": "operational_failure",
        "impact": "需要工程团队进一步分析业务影响范围。",
        "investigation": "检查相关业务逻辑和约束条件。",
    })

    sev = str(bug_finding.get("severity", "P2"))
    urgency_map = {"P0": "immediate", "P1": "this_sprint", "P2": "next_sprint", "P3": "backlog"}

    return {
        "bug_title": bug_finding.get("title", ""),
        "bug_type": bug_type,
        "severity": sev,
        "source": "heuristic_template",
        "reproducibility": {
            "preconditions": "重现该Bug的系统状态取决于具体业务场景",
            "trigger_sequence": ["执行相关业务操作"],
            "reproducible": True,
            "reproduction_confidence": 0.5,
        },
        "business_impact": {
            "summary": template["impact"],
            "urgency": urgency_map.get(sev, "backlog"),
        },
        "risk_classification": {
            "primary_risk": template["primary_risk"],
            "data_risk": "corruption" if bug_type in ("duplicate_side_effect", "collection_detail_projection") else "none",
            "financial_risk": "certain" if bug_type == "side_effect_amount_mismatch" else "potential",
            "compliance_risk": "none",
        },
        "investigation_guidance": {
            "primary_area": template["investigation"],
            "relevant_constraints": [bug_finding.get("expected", "")],
            "related_apis": [bug_finding.get("actual", "")],
        },
        "severity_justification": {
            "reasoning": f"严重度 {sev}：{template['impact']}",
        },
        "detection_assessment": {
            "is_real_bug": True,
            "false_positive_risk": "需人工确认是否为业务特例",
            "detection_quality": "heuristic_only",
        },
    }
