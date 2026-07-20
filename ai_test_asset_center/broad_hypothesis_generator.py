"""Broad hypothesis generation using the 11-engine parallel reasoning system.

This module bridges the Legacy discovery engine's powerful multi-engine reasoning
with the V12 experiment pipeline. It generates diverse hypotheses from enterprise
materials and converts them to a format consumable by the V12 obligation compiler.

Key features:
- 11 parallel reasoning engines (causality, invariant, reconciliation, etc.)
- Adaptive hypothesis prioritization based on risk type and source strength
- Conversion to V12 obligation-compatible format
- Integration with the discovery runtime for broad coverage
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

# ── Risk type weights for prioritization ──
RISK_TYPE_WEIGHTS = {
    "permission_boundary": 1.0,
    "data_conservation": 0.95,
    "state_machine": 0.90,
    "authorization": 0.88,
    "isolation": 0.85,
    "idempotency": 0.80,
    "concurrency": 0.78,
    "data_reconciliation": 0.75,
    "async_event": 0.70,
    "sensitive_data": 0.68,
    "historical_regression": 0.60,
}

# ── Hypothesis source strength indicators ──
SOURCE_STRENGTH = {
    "explicit_requirement": 1.0,
    "api_contract": 0.90,
    "business_rule": 0.85,
    "schema_constraint": 0.80,
    "inferred_pattern": 0.60,
    "heuristic": 0.40,
}


def generate_broad_hypotheses(
    prd_text: str,
    api_spec_text: str,
    db_schema_text: str = "",
    *,
    behavior_ir: dict[str, Any] | None = None,
    prior_findings: list[dict[str, Any]] | None = None,
    max_hypotheses: int = 60,
    timeout_seconds: int = 300,
    project: str = "",
) -> dict[str, Any]:
    """Generate broad hypotheses using the 11-engine parallel reasoning system.

    Returns a dict with:
    - hypotheses: list of hypothesis dicts
    - engine_report: per-engine success/failure metadata
    - generation_receipt: timing and configuration metadata
    """
    started = time.time()
    receipt: dict[str, Any] = {
        "schema_version": "qualibug.broad-hypothesis-generation.v1",
        "project": project,
        "started_at": started,
        "max_hypotheses": max_hypotheses,
        "timeout_seconds": timeout_seconds,
    }

    # ── Try to use the full 11-engine reasoning system ──
    hypotheses: list[dict[str, Any]] = []
    engine_report: dict[str, Any] = {}

    try:
        from .stage_reason_all_v2 import (
            _stage_reason_all_v2_standalone,
            MAX_HYPOTHESES,
            MAX_REASONER_WORKERS,
        )
        # Use standalone version that doesn't require engine instance
        hypotheses, engine_report = _stage_reason_all_v2_standalone(
            prd_text=prd_text,
            api_spec=api_spec_text,
            reader_output=_build_reader_context(behavior_ir, db_schema_text),
            prior_findings=prior_findings or [],
            max_hypotheses_per_engine=min(max_hypotheses, MAX_HYPOTHESES),
            max_workers=MAX_REASONER_WORKERS,
            timeout_seconds=timeout_seconds,
        )
        receipt["mode"] = "full_11_engine"
    except ImportError:
        # Fallback: generate hypotheses from Behavior IR structure
        hypotheses = _generate_from_behavior_ir(behavior_ir or {}, api_spec_text)
        engine_report = {"mode": "behavior_ir_fallback", "engines": []}
        receipt["mode"] = "behavior_ir_fallback"
    except Exception as exc:
        # Fallback on any error
        hypotheses = _generate_from_behavior_ir(behavior_ir or {}, api_spec_text)
        engine_report = {"mode": "error_fallback", "error": str(exc)[:300]}
        receipt["mode"] = "error_fallback"

    # ── Prioritize and limit hypotheses ──
    prioritized = prioritize_hypotheses(hypotheses, max_count=max_hypotheses)

    # ── Convert to V12-compatible format ──
    v12_hypotheses = [
        _to_v12_hypothesis_format(h, idx)
        for idx, h in enumerate(prioritized)
    ]

    receipt["completed_at"] = time.time()
    receipt["elapsed_ms"] = int((receipt["completed_at"] - started) * 1000)
    receipt["raw_hypothesis_count"] = len(hypotheses)
    receipt["prioritized_count"] = len(prioritized)
    receipt["v12_hypothesis_count"] = len(v12_hypotheses)

    return {
        "hypotheses": v12_hypotheses,
        "engine_report": engine_report,
        "generation_receipt": receipt,
    }


def prioritize_hypotheses(
    hypotheses: list[dict[str, Any]],
    *,
    max_count: int = 60,
) -> list[dict[str, Any]]:
    """Prioritize hypotheses by risk weight, source strength, and diversity.

    Uses a scoring formula:
    score = risk_weight * 0.4 + source_strength * 0.3 + severity_score * 0.2 + diversity_bonus * 0.1
    """
    if not hypotheses:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    seen_risk_types: dict[str, int] = {}

    for h in hypotheses:
        if not isinstance(h, dict):
            continue

        # Extract risk type
        risk_type = str(h.get("risk_type") or h.get("category") or "").lower()
        risk_weight = RISK_TYPE_WEIGHTS.get(risk_type, 0.50)

        # Extract source strength
        source = str(h.get("source") or h.get("evidence_source") or "").lower()
        source_strength = SOURCE_STRENGTH.get(source, 0.50)

        # Extract severity
        severity = str(h.get("severity") or "").upper()
        severity_score = {"P0": 1.0, "P1": 0.85, "P2": 0.60, "P3": 0.40}.get(severity, 0.50)

        # Diversity bonus: penalize over-represented risk types
        type_count = seen_risk_types.get(risk_type, 0)
        diversity_bonus = max(0.0, 1.0 - type_count * 0.15)
        seen_risk_types[risk_type] = type_count + 1

        # Compute score
        score = (
            risk_weight * 0.4
            + source_strength * 0.3
            + severity_score * 0.2
            + diversity_bonus * 0.1
        )

        scored.append((score, h))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Take top N
    return [h for _, h in scored[:max_count]]


def _build_reader_context(
    behavior_ir: dict[str, Any] | None,
    db_schema_text: str,
) -> dict[str, Any]:
    """Build a reader-like context from Behavior IR for the reasoning engines."""
    ir = behavior_ir or {}
    operations = ir.get("operations", [])
    entities = ir.get("entities", [])
    actors = ir.get("actors", [])
    invariants = ir.get("invariants", [])

    return {
        "operations": operations[:50],  # Limit for prompt size
        "entities": entities[:30],
        "actors": actors[:20],
        "invariants": invariants[:20],
        "db_schema_summary": db_schema_text[:5000] if db_schema_text else "",
        "operation_count": len(operations),
        "entity_count": len(entities),
    }


def _generate_from_behavior_ir(
    behavior_ir: dict[str, Any],
    api_spec_text: str,
) -> list[dict[str, Any]]:
    """Fallback: generate hypotheses directly from Behavior IR structure."""
    hypotheses: list[dict[str, Any]] = []
    ir = behavior_ir or {}

    # Generate from operations
    for op in ir.get("operations", [])[:30]:
        if not isinstance(op, dict):
            continue
        method = str(op.get("method") or "GET").upper()
        path = str(op.get("path") or "")
        op_id = str(op.get("operation_id") or op.get("id") or "")

        # Write operations → potential state/conservation bugs
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            hypotheses.append({
                "hypothesis_id": f"bir_write_{op_id[:16]}",
                "title": f"Write operation {method} {path} may violate state constraints",
                "risk_type": "state_machine",
                "severity": "P1",
                "source": "api_contract",
                "verification_method": {
                    "method": method,
                    "path": path,
                    "check": "state_transition_valid",
                },
            })

        # Operations with parameters → potential boundary bugs
        params = op.get("parameters", [])
        if params:
            hypotheses.append({
                "hypothesis_id": f"bir_param_{op_id[:16]}",
                "title": f"Operation {method} {path} parameter validation may be incomplete",
                "risk_type": "data_conservation",
                "severity": "P2",
                "source": "schema_constraint",
                "verification_method": {
                    "method": method,
                    "path": path,
                    "check": "parameter_boundary",
                },
            })

    # Generate from invariants
    for inv in ir.get("invariants", [])[:20]:
        if not isinstance(inv, dict):
            continue
        inv_id = str(inv.get("invariant_id") or inv.get("id") or "")
        inv_type = str(inv.get("kind") or inv.get("type") or "constraint")

        hypotheses.append({
            "hypothesis_id": f"bir_inv_{inv_id[:16]}",
            "title": f"Invariant {inv_type} may be violated under certain conditions",
            "risk_type": "data_conservation" if "conserv" in inv_type.lower() else "state_machine",
            "severity": "P1",
            "source": "business_rule",
            "verification_method": {
                "check": "invariant_holds",
                "invariant_ref": inv_id,
            },
        })

    # Generate from permission relations
    for rel in ir.get("relations", [])[:30]:
        if not isinstance(rel, dict):
            continue
        rel_type = str(rel.get("relation_type") or "")
        if rel_type in {"permits", "denies", "permission_unknown"}:
            hypotheses.append({
                "hypothesis_id": f"bir_perm_{hashlib.sha256(str(rel).encode()).hexdigest()[:12]}",
                "title": f"Permission {rel_type} may not be enforced correctly",
                "risk_type": "permission_boundary",
                "severity": "P0" if rel_type == "denies" else "P1",
                "source": "explicit_requirement",
                "verification_method": {
                    "check": "permission_enforced",
                    "relation_ref": rel.get("relation_id", ""),
                },
            })

    return hypotheses


def _to_v12_hypothesis_format(hypothesis: dict[str, Any], index: int) -> dict[str, Any]:
    """Convert a hypothesis to V12 obligation-compatible format."""
    h_id = str(hypothesis.get("hypothesis_id") or f"broad_hyp_{index:04d}")

    return {
        "hypothesis_id": h_id,
        "obligation_source": "broad_discovery",
        "title": str(hypothesis.get("title") or hypothesis.get("hypothesis") or "")[:500],
        "description": str(hypothesis.get("description") or hypothesis.get("rationale") or "")[:1000],
        "risk_type": str(hypothesis.get("risk_type") or hypothesis.get("category") or "unknown"),
        "severity": str(hypothesis.get("severity") or "P2").upper(),
        "confidence": float(hypothesis.get("confidence") or 0.6),
        "source_engine": str(hypothesis.get("engine") or hypothesis.get("source") or "unknown"),
        "verification_method": hypothesis.get("verification_method") or {},
        "evidence_refs": hypothesis.get("evidence_refs") or [],
        "source_refs": hypothesis.get("source_refs") or [],
        "_broad_discovery": True,
        "_priority_score": float(hypothesis.get("_priority_score") or 0.5),
    }


# ── Standalone reasoning function (extracted from stage_reason_all_v2) ──

def _stage_reason_all_v2_standalone(
    prd_text: str,
    api_spec: str,
    reader_output: dict[str, Any],
    prior_findings: list[dict[str, Any]],
    max_hypotheses_per_engine: int = 15,
    max_workers: int = 4,
    timeout_seconds: int = 300,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Standalone version of the 11-engine reasoning that doesn't require engine instance.

    Returns (hypotheses, engine_report).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .reasoner_prompt import REASONER_PROMPTS, REASONER_SYSTEM_PROMPT

    all_hypotheses: list[dict[str, Any]] = []
    engine_results: dict[str, Any] = {}

    # Define engines
    engines = [
        ("causality", REASONER_PROMPTS.get("causality", "")),
        ("invariant", REASONER_PROMPTS.get("invariant", "")),
        ("reconciliation", REASONER_PROMPTS.get("reconciliation", "")),
        ("counterexample", REASONER_PROMPTS.get("counterexample", "")),
        ("consistency", REASONER_PROMPTS.get("consistency", "")),
        ("population", REASONER_PROMPTS.get("population", "")),
        ("outcome", REASONER_PROMPTS.get("outcome", "")),
        ("temporal", REASONER_PROMPTS.get("temporal", "")),
        ("saga", REASONER_PROMPTS.get("saga", "")),
        ("event_chain", REASONER_PROMPTS.get("event_chain", "")),
        ("metamorphic", REASONER_PROMPTS.get("metamorphic", "")),
    ]

    # Filter to engines with prompts
    engines = [(name, prompt) for name, prompt in engines if prompt]

    def run_engine(engine_name: str, prompt_template: str) -> tuple[str, list[dict], str]:
        """Run a single reasoning engine."""
        try:
            from ai_test_asset_center.llm_reasoning import ReasoningClient

            client = ReasoningClient(
                timeout_seconds=timeout_seconds,
                max_tokens=32768,
            )

            # Build prompt
            prompt = prompt_template.format(
                prd_text=prd_text[:45000],
                api_schema=api_spec[:50000],
                observed_data=json.dumps(reader_output, ensure_ascii=False)[:12000],
                heuristic_findings=json.dumps(prior_findings[:10], ensure_ascii=False)[:12000],
                max_hypotheses=max_hypotheses_per_engine,
            )

            response = client.complete(
                system=REASONER_SYSTEM_PROMPT,
                user=prompt,
            )

            # Parse response
            hypotheses = _parse_hypotheses_response(response)
            for h in hypotheses:
                h["engine"] = engine_name
            return engine_name, hypotheses, ""
        except Exception as exc:
            return engine_name, [], str(exc)[:200]

    # Run engines in parallel
    successful_engines: list[str] = []
    failed_engines: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_engine, name, prompt): name
            for name, prompt in engines
        }
        for future in as_completed(futures, timeout=timeout_seconds + 30):
            engine_name = futures[future]
            try:
                name, hypotheses, error = future.result()
                if error:
                    failed_engines.append(name)
                    engine_results[name] = {"status": "failed", "error": error}
                else:
                    successful_engines.append(name)
                    engine_results[name] = {"status": "success", "count": len(hypotheses)}
                    all_hypotheses.extend(hypotheses)
            except Exception as exc:
                failed_engines.append(engine_name)
                engine_results[engine_name] = {"status": "failed", "error": str(exc)[:200]}

    report = {
        "total_engines": len(engines),
        "successful_engines": successful_engines,
        "failed_engines": failed_engines,
        "engine_results": engine_results,
        "total_hypotheses": len(all_hypotheses),
    }

    return all_hypotheses, report


def _parse_hypotheses_response(response: Any) -> list[dict[str, Any]]:
    """Parse LLM response into hypothesis list."""
    if not response:
        return []

    # Handle different response formats
    content = ""
    if isinstance(response, str):
        content = response
    elif isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
    else:
        content = str(response)

    if not content:
        return []

    # Try to parse JSON
    try:
        # Remove code fences
        if "```" in content:
            lines = content.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    json_lines.append(line)
            content = "\n".join(json_lines)

        data = json.loads(content)
        if isinstance(data, dict):
            hypotheses = data.get("hypotheses", [])
        elif isinstance(data, list):
            hypotheses = data
        else:
            return []

        return [h for h in hypotheses if isinstance(h, dict)]
    except json.JSONDecodeError:
        # Try to salvage partial JSON
        try:
            import ast
            # Try Python literal eval as fallback
            data = ast.literal_eval(content)
            if isinstance(data, dict):
                return data.get("hypotheses", [])
            elif isinstance(data, list):
                return [h for h in data if isinstance(h, dict)]
        except Exception:
            pass

    return []
