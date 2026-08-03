"""Business Finding Registry — unified entry for adversarial validation pipeline.

Orchestrates the full Phase83B/Phase92A chain:
  Hypothesis → Semantic Verifier → Evidence → Deduplicator →
  Adversarial Validator → Evidence Verifier → Schema Validator → Ledger → Human Review

PHASE92A: Preserves four-layer state (raw_runtime / semantic / business / final).

This is the SINGLE entry point that all discovery engines should route through
before writing findings to the ledger. It prevents:
- Auto-confirmation of findings
- Duplicate findings from different engines
- Findings with incomplete evidence entering Human Review
"""
from __future__ import annotations
import json as _json
import time as _time
from pathlib import Path
from typing import Any


def _now() -> str:
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())


def _preserve_four_layer_state(finding: dict[str, Any]) -> dict[str, Any]:
    """Phase92A: Preserve four-layer state fields before any validation."""
    return {
        "verdict": finding.get("verdict", ""),  # Phase92A: Also preserve verdict
        "raw_runtime_verdict": finding.get("raw_runtime_verdict", ""),
        "semantic_verdict": finding.get("semantic_verdict", ""),
        "business_evidence_status": finding.get("business_evidence_status", ""),
        "final_review_status": finding.get("final_review_status", ""),
        "runtime_gate_status": finding.get("runtime_gate_status", ""),
        "business_gate_status": finding.get("business_gate_status", ""),
        "missing_requirements": finding.get("missing_requirements", []),
    }


def validate_and_register_findings(
    findings: list[dict[str, Any]],
    *,
    project_id: str = "",
    rejection_memory_path: str = "",
    enable_llm_disprover: bool = True,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Run the full Phase83B/Phase92A validation pipeline on a batch of findings.

    Chain:
        1. Finding Deduplication (merge clusters, check rejection history)
        2. Adversarial Validation (deterministic + optional LLM)
        3. Independent Evidence Verification
        4. Business Finding Schema Validation
        5. Classify into VALIDATED_CANDIDATE / REJECTED / NEEDS_MORE_EVIDENCE

    PHASE92A: Four-layer state is preserved throughout the pipeline.
    The semantic verdict is NEVER downgraded to rejected/falsified
    by gate failures — only to NEEDS_MORE_EVIDENCE.

    Returns:
        {
            "total": int,
            "validated_candidates": [...],
            "rejected": [...],
            "needs_more_evidence": [...],
            "blocked": [...],
            "meta": {...},
        }
    """
    from .business_adversarial_validator import run_adversarial_validation
    from .finding_deduplicator import deduplicate_and_validate, cluster_findings, merge_evidence
    from .independent_evidence_verifier import verify_evidence_integrity
    from .business_finding_schema_validator import validate_finding

    # Phase92A: Preserve four-layer state for each finding before any mutation
    four_layer_states = {i: _preserve_four_layer_state(f) for i, f in enumerate(findings)}

    # Step 1: Deduplicate
    def _validate_fn(f: dict[str, Any]) -> dict[str, Any]:
        return run_adversarial_validation(
            f,
            enable_llm=enable_llm_disprover,
            llm_timeout=60,
        )

    deduped = deduplicate_and_validate(
        findings,
        rejection_memory_path=rejection_memory_path,
        validate_fn=_validate_fn,
    )

    # Step 2: Evidence verification + Schema validation for each deduped finding
    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    needs_evidence: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for i, f in enumerate(deduped):
        if not f.get("finding_id"):
            f["finding_id"] = f"FND_{_now()}_{i}"

        # Phase92A: Restore preserved four-layer state
        preserved = four_layer_states.get(i, {})
        for key, value in preserved.items():
            if value and not f.get(key):
                f[key] = value

        # Independent evidence verification
        ev_result = verify_evidence_integrity(f, workspace_root=workspace_root)
        f["_evidence_verification"] = ev_result

        if ev_result["verdict"] == "EVIDENCE_INCOMPLETE":
            # Phase92A: DO NOT reject semantic confirmed findings
            semantic = f.get("semantic_verdict", "")
            if semantic == "SEMANTIC_CONFIRMED":
                f["verdict"] = "NEEDS_MORE_EVIDENCE"
                f["business_evidence_status"] = f.get("business_evidence_status", "PENDING_EVIDENCE")
                f["final_review_status"] = "NEEDS_MORE_EVIDENCE"
            else:
                f["verdict"] = "NEEDS_MORE_EVIDENCE"
            needs_evidence.append(f)
            continue

        # Schema validation (strip internal fields before validating)
        schema_finding = {k: v for k, v in f.items() if not k.startswith("_")}
        schema_result = validate_finding(schema_finding)
        f["_schema_validation"] = schema_result

        if schema_result["verdict"] == "SCHEMA_INVALID":
            f["verdict"] = "SCHEMA_INVALID"
            blocked.append(f)
            continue
        if schema_result["verdict"] == "NEEDS_MORE_EVIDENCE":
            f["verdict"] = "NEEDS_MORE_EVIDENCE"
            needs_evidence.append(f)
            continue

        # Classify
        verdict = f.get("verdict", "NEEDS_MORE_EVIDENCE")
        semantic = f.get("semantic_verdict", "")
        
        # Phase92A: Protect semantic confirmed from rejection
        if semantic == "SEMANTIC_CONFIRMED" and verdict in ("REJECTED", "FALSIFIED", "SCHEMA_INVALID"):
            # Semantic confirmed but rejected by gate → needs_more_evidence
            f["verdict"] = "NEEDS_MORE_EVIDENCE"
            f["final_review_status"] = "NEEDS_MORE_EVIDENCE"
            needs_evidence.append(f)
            continue
        
        if verdict == "VALIDATED_CANDIDATE":
            validated.append(f)
        elif verdict == "REJECTED":
            rejected.append(f)
        elif verdict.startswith("BLOCKED"):
            blocked.append(f)
        else:
            needs_evidence.append(f)

    # Meta
    total_input = len(findings)
    clusters = len(cluster_findings(findings))
    meta = {
        "phase": "phase83b_adversarial_verification",
        "total_input_findings": total_input,
        "deduplicated_clusters": clusters,
        "validated_candidates": len(validated),
        "rejected": len(rejected),
        "needs_more_evidence": len(needs_evidence),
        "blocked": len(blocked),
        "rejection_rate": round(len(rejected) / max(total_input, 1), 3),
        "dedup_ratio": round(clusters / max(total_input, 1), 3),
        "llm_disprover_enabled": enable_llm_disprover,
    }

    return {
        "total": total_input,
        "validated_candidates": validated,
        "rejected": rejected,
        "needs_more_evidence": needs_evidence,
        "blocked": blocked,
        "meta": meta,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python business_finding_registry.py <findings.json> [--no-llm]")
        sys.exit(1)
    path = Path(sys.argv[1])
    findings = _json.loads(path.read_text(encoding="utf-8"))
    enable_llm = "--no-llm" not in sys.argv
    result = validate_and_register_findings(findings, enable_llm_disprover=enable_llm)
    print(_json.dumps({"meta": result["meta"]}, indent=2, ensure_ascii=False))
    for cat in ("validated_candidates", "rejected", "needs_more_evidence", "blocked"):
        items = result[cat]
        if items:
            print(f"\n--- {cat.upper()} ({len(items)}) ---")
            for item in items[:3]:
                print(f"  - {item.get('title', '?')[:100]}: {item.get('verdict', '?')}")
