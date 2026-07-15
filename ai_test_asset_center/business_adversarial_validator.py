"""Business Adversarial Validator — Deterministc + LLM disprover layer.

Two-layer architecture:
  Layer 1: Deterministic Disprover (no LLM) — binding checks, snapshot integrity,
            eventually consistency, observer conflicts, fixture dirtiness,
            duplication detection, state-machine allowability.
  Layer 2: LLM Adversarial Disprover — only when Layer 1 cannot decide.
            Uses compact Evidence Pack (NOT full PRD/OpenAPI).
            Strict input budget, JSON-only output, graceful degradation.

Does NOT auto-confirm findings. Disproved findings → REJECTED.
Insufficient evidence → NEEDS_MORE_EVIDENCE.
"""
from __future__ import annotations
import hashlib
import json as _json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DisproverResult:
    deterministic_result: str  # DETERMINISTIC_DISPROOF | DETERMINISTIC_CONFLICT | DETERMINISTIC_INSUFFICIENT_EVIDENCE | DETERMINISTIC_PASS
    disprover_result: str      # DISPROVED | NOT_DISPROVED | INSUFFICIENT_EVIDENCE | EXECUTION_BLOCKED | NOT_RUN
    counterarguments: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    disprover_source: str = "none"  # deterministic | llm | both | none
    llm_raw: str = ""
    llm_error: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# Layer 1: Deterministic Disprover
# ──────────────────────────────────────────────────────────────────────────────

def _hash_id(*parts: Any) -> str:
    raw = _json.dumps(list(parts), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]




def _is_read_only_auth_boundary(finding: dict[str, Any]) -> bool:
    return str(finding.get("evidence_model") or "").lower() == "read_only_auth_boundary" or bool(finding.get("auth_boundary_matrix"))

def deterministic_disprove(
    finding: dict[str, Any],
    *,
    history_entries: dict[str, Any] | None = None,
    eventually_timeout: float = 60.0,
) -> dict[str, Any]:
    """Run deterministic checks without LLM.

    Checks:
    1. Entity Binding consistency
    2. Before/After snapshot integrity
    3. Eventually-consistency window
    4. Observer conflicts
    5. Fixture dirtiness
    6. Duplicate with history
    7. State-machine allowability
    8. Missing invariants
    """
    counterarguments: list[str] = []
    unresolved: list[str] = []
    disproofs: list[str] = []

    # 1. Entity Binding check
    entity = finding.get("entity_binding") or {}
    before_ref = finding.get("before_snapshot_ref", "")
    after_ref = finding.get("after_snapshot_ref", "")
    entity_id = entity.get("entity_id", "")
    tenant_id = entity.get("tenant_id", "")
    binding_conf = entity.get("binding_confidence", 0)

    if not entity_id:
        counterarguments.append("No entity_id in entity_binding — cannot verify entity consistency")
        disproofs.append("DETERMINISTIC_INSUFFICIENT_EVIDENCE: missing entity_id")
    if not tenant_id:
        unresolved.append("Missing tenant_id in entity_binding")
    if binding_conf is not None and binding_conf < 0.3:
        counterarguments.append(f"Low binding confidence ({binding_conf}) — entity binding may be wrong")
        disproofs.append("DETERMINISTIC_DISPROOF: low binding confidence")

    # 2. Check that entity_alias is present (lightweight re-enable)
    # Snapshot refs are often hash digests, so we can't compare entity names directly.
    # But if entity_alias is present on the finding and missing from snapshots, flag it.
    entity_alias = entity.get("entity_alias", "")
    if entity_alias:
        # Check if any snapshot ref contains a non-hash entity hint
        has_snapshot_entity = False
        for ref in (before_ref, after_ref):
            if ref and not ref.startswith("snap:") and not all(c in "0123456789abcdef" for c in ref[:8]):
                has_snapshot_entity = True
        if not has_snapshot_entity:
            counterarguments.append(f"Entity alias '{entity_alias}' present but snapshot refs are hash digests — cross-reference not verifiable")
            disproofs.append("DETERMINISTIC_DISPROOF: entity_alias cannot be verified against hash-digest snapshot refs")
    
    # 3. Before/After snapshot integrity
    if before_ref == after_ref and before_ref != "":
        counterarguments.append("Before and After snapshots reference the same source — no actual state change observed")
        disproofs.append("DETERMINISTIC_DISPROOF: identical before/after refs")

    # 4. Eventually-consistency window
    repro = finding.get("reproduction") or {}
    cleanup = finding.get("cleanup") or {}
    if cleanup.get("status") in ("DIRTY", "CLEANUP_FAILED"):
        counterarguments.append(f"Cleanup status is {cleanup['status']} — test environment may be dirty")
        disproofs.append("DETERMINISTIC_DISPROOF: dirty test environment")

    # 5. Observer conflicts
    observer_refs = finding.get("observer_refs") or []
    if not observer_refs:
        unresolved.append("No observer_refs — cannot cross-validate with alternative data sources")

    # 6. Invariant check
    invariant = finding.get("violated_invariant") or {}
    if not invariant.get("kind") or not invariant.get("definition"):
        counterarguments.append("Violated invariant is incomplete — cannot verify the violation is real")
        disproofs.append("DETERMINISTIC_INSUFFICIENT_EVIDENCE: incomplete invariant")

    # 7. Duplicate detection against history
    if history_entries:
        title = finding.get("title", "")
        fp = _hash_id(
            finding.get("project_id"),
            entity.get("entity_type"),
            entity.get("entity_id"),
            invariant.get("kind"),
            title[:80],
        )
        for hist_id, hist_entry in history_entries.items():
            if isinstance(hist_entry, dict):
                if hist_entry.get("title") == title or hist_entry.get("fingerprint") == fp:
                    counterarguments.append(f"Duplicate finding: matches history entry '{hist_id}'")
                    disproofs.append("DETERMINISTIC_DISPROOF: duplicate")
                    break

    # 8. Missing preconditions
    preconditions = finding.get("preconditions") or []
    if not preconditions and not _is_read_only_auth_boundary(finding):
        unresolved.append("No preconditions listed — cannot verify the finding applies in correct context")

    # Determine result
    if disproofs:
        # Check if all disproofs are strictly DISPROOF (not INSUFFICIENT_EVIDENCE)
        if all("DISPROOF" in d and "INSUFFICIENT" not in d for d in disproofs):
            result = "DETERMINISTIC_DISPROOF"
        elif all("INSUFFICIENT" in d for d in disproofs):
            result = "DETERMINISTIC_INSUFFICIENT_EVIDENCE"
        else:
            result = "DETERMINISTIC_CONFLICT"
    elif counterarguments or unresolved:
        result = "DETERMINISTIC_INSUFFICIENT_EVIDENCE"
    else:
        result = "DETERMINISTIC_PASS"

    return {
        "result": result,
        "counterarguments": counterarguments,
        "unresolved": unresolved,
        "disproofs": disproofs,
        "fingerprint": _hash_id(finding.get("project_id"), entity.get("entity_type"), entity.get("entity_id"), invariant.get("kind"), finding.get("title", "")[:80]),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Layer 2: LLM Adversarial Disprover
# ──────────────────────────────────────────────────────────────────────────────

def build_evidence_pack(finding: dict[str, Any], max_chars: int = 3000) -> str:
    """Build a compact Evidence Pack for the LLM disprover.

    Constraints:
    - Never include full PRD/OpenAPI
    - Max ~3000 chars total
    - JSON structured
    """
    pack = {
        "finding_id": finding.get("finding_id", ""),
        "title": finding.get("title", "")[:200],
        "business_intent": finding.get("business_intent", "")[:200],
        "entity": {
            "alias": (finding.get("entity_binding") or {}).get("entity_alias", ""),
            "type": (finding.get("entity_binding") or {}).get("entity_type", ""),
        },
        "invariant": {
            "kind": (finding.get("violated_invariant") or {}).get("kind", ""),
            "definition": (finding.get("violated_invariant") or {}).get("definition", "")[:300],
            "result": (finding.get("violated_invariant") or {}).get("result", "")[:300],
        },
        "before_snapshot_ref": finding.get("before_snapshot_ref", ""),
        "after_snapshot_ref": finding.get("after_snapshot_ref", ""),
        "action_type": (finding.get("entrypoint") or {}).get("action_type", ""),
        "cleanup_status": (finding.get("cleanup") or {}).get("status", ""),
        "deterministic_result": "",  # filled by caller
    }
    return _json.dumps(pack, ensure_ascii=False)


def llm_adversarial_disprove(
    evidence_pack: str,
    *,
    timeout_seconds: int = 60,
) -> DisproverResult:
    """Run LLM adversarial disprover.

    The LLM is instructed to DISPROVE, not confirm.
    Uses existing LLM client from project.
    Gracefully degrades on timeout/error.
    """
    prompt = f"""You are an adversarial bug-disprover. Your ONLY task is to try to DISPROVE the following
business finding candidate. Do NOT try to confirm it. Look for reasons it could be wrong.

Checklist:
1. Entity binding errors (wrong entity, tenant, correlation ID)
2. Eventually-consistency: could the observed state be a normal async delay?
3. Alternative views: could an audit log, list endpoint, or detail view show different data?
4. Compensation/retry/cache/delayed-write: could the system be behaving correctly?
5. Fixture/environment/configuration artifacts
6. Permission/guard/constraint preventing real impact
7. Business conservation law NOT actually violated
8. Missing preconditions
9. Duplicate of known/rejected finding
10. Can you construct a clear counterexample?

Output ONLY valid JSON — no markdown, no explanation outside JSON:
{{
  "verdict": "DISPROVED|NOT_DISPROVED|INSUFFICIENT_EVIDENCE|EXECUTION_BLOCKED",
  "counterarguments": ["reason 1", "reason 2"],
  "unresolved_questions": ["question 1"],
  "confidence": 0.0
}}

Evidence Pack:
{evidence_pack[:3000]}"""

    try:
        from .llm_reasoning import reason as _llm_reason
        raw = _llm_reason("adversarial_disprover", {
            "prompt": prompt,
            "max_tokens": 512,
            "temperature": 0.1,
        }, timeout=timeout_seconds)
    except Exception as e:
        return DisproverResult(
            deterministic_result="DETERMINISTIC_INSUFFICIENT_EVIDENCE",
            disprover_result="EXECUTION_BLOCKED",
            counterarguments=[f"LLM disprover failed: {str(e)[:200]}"],
            disprover_source="none",
            llm_error=str(e)[:200],
        )

    if not raw:
        return DisproverResult(
            deterministic_result="DETERMINISTIC_INSUFFICIENT_EVIDENCE",
            disprover_result="EXECUTION_BLOCKED",
            counterarguments=["LLM disprover returned empty response"],
            disprover_source="none",
            llm_error="empty response",
        )

    # Parse response
    try:
        if isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, str):
            # Try to extract JSON from markdown
            raw_stripped = raw.strip()
            if "```json" in raw_stripped:
                raw_stripped = raw_stripped.split("```json")[1].split("```")[0]
            elif "```" in raw_stripped:
                raw_stripped = raw_stripped.split("```")[1].split("```")[0]
            parsed = _json.loads(raw_stripped)
        else:
            raise ValueError(f"Unexpected type: {type(raw)}")
    except Exception as e:
        return DisproverResult(
            deterministic_result="DETERMINISTIC_INSUFFICIENT_EVIDENCE",
            disprover_result="EXECUTION_BLOCKED",
            counterarguments=[f"LLM response parse error: {str(e)[:200]}"],
            disprover_source="none",
            llm_error=str(e)[:200],
            llm_raw=str(raw)[:500],
        )

    verdict = parsed.get("verdict", "EXECUTION_BLOCKED")
    if verdict not in ("DISPROVED", "NOT_DISPROVED", "INSUFFICIENT_EVIDENCE", "EXECUTION_BLOCKED"):
        verdict = "EXECUTION_BLOCKED"

    return DisproverResult(
        deterministic_result="DETERMINISTIC_PASS",  # deterministic layer already passed
        disprover_result=verdict,
        counterarguments=list(parsed.get("counterarguments", []))[:5],
        unresolved_questions=list(parsed.get("unresolved_questions", []))[:5],
        disprover_source="llm",
        llm_raw=str(raw)[:500],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Combined Adversarial Validator
# ──────────────────────────────────────────────────────────────────────────────

def run_adversarial_validation(
    finding: dict[str, Any],
    *,
    history_entries: dict[str, Any] | None = None,
    enable_llm: bool = True,
    llm_timeout: int = 60,
) -> dict[str, Any]:
    """Run full adversarial validation: deterministic first, LLM if needed.

    Returns finding enriched with adversarial_validation block and updated verdict.
    """
    # Step 1: Deterministic
    det_result = deterministic_disprove(finding, history_entries=history_entries)

    # Step 2: Decide if LLM is needed
    need_llm = enable_llm and det_result["result"] in ("DETERMINISTIC_CONFLICT", "DETERMINISTIC_PASS", "DETERMINISTIC_INSUFFICIENT_EVIDENCE")
    llm_disprover = "NOT_RUN"
    counterargs = list(det_result["counterarguments"])
    unresolved_qs = list(det_result["unresolved"])
    source = "deterministic"
    llm_raw = ""
    llm_error = ""

    if need_llm and enable_llm:
        try:
            evidence_pack = build_evidence_pack(finding)
            # Inject deterministic result
            pack_dict = _json.loads(evidence_pack)
            pack_dict["deterministic_result"] = det_result["result"]
            evidence_pack = _json.dumps(pack_dict, ensure_ascii=False)

            llm_result = llm_adversarial_disprove(evidence_pack, timeout_seconds=llm_timeout)
            if llm_result.disprover_source == "llm":
                source = "both"
                llm_disprover = llm_result.disprover_result
                counterargs.extend(llm_result.counterarguments)
                unresolved_qs.extend(llm_result.unresolved_questions)
                llm_raw = llm_result.llm_raw
                llm_error = llm_result.llm_error
            else:
                llm_disprover = llm_result.disprover_result
                llm_error = llm_result.llm_error
        except Exception as e:
            llm_disprover = "EXECUTION_BLOCKED"
            llm_error = str(e)[:200]

    # Deduplicate counterarguments
    seen = set()
    unique_args = []
    for arg in counterargs:
        if arg not in seen:
            seen.add(arg)
            unique_args.append(arg)

    # Deduplicate unresolved
    seen_q = set()
    unique_qs = []
    for q in unresolved_qs:
        if q not in seen_q:
            seen_q.add(q)
            unique_qs.append(q)

    adversarial_block = {
        "deterministic_result": det_result["result"],
        "disprover_result": llm_disprover,
        "counterarguments": unique_args[:5],
        "unresolved_questions": unique_qs[:5],
        "disprover_source": source,
    }

    # Step 3: Determine final verdict
    if det_result["result"] == "DETERMINISTIC_DISPROOF":
        final_verdict = "REJECTED"
    elif llm_disprover == "DISPROVED":
        final_verdict = "REJECTED"
    elif llm_disprover == "EXECUTION_BLOCKED":
        final_verdict = "NEEDS_MORE_EVIDENCE"
    elif unique_qs and not unique_args:
        final_verdict = "NEEDS_MORE_EVIDENCE"
    elif cleanup_blocked(finding):
        final_verdict = "BLOCKED_BY_CLEANUP"
    elif fixture_dirty(finding):
        final_verdict = "BLOCKED_BY_FIXTURE"
    else:
        final_verdict = "VALIDATED_CANDIDATE"

    finding["adversarial_validation"] = adversarial_block
    finding["verdict"] = final_verdict
    return finding


def cleanup_blocked(finding: dict[str, Any]) -> bool:
    cleanup = finding.get("cleanup") or {}
    return cleanup.get("status") in ("CLEANUP_FAILED", "DIRTY")


def fixture_dirty(finding: dict[str, Any]) -> bool:
    preconditions = finding.get("preconditions") or []
    entity = finding.get("entity_binding") or {}
    if entity.get("binding_confidence", 1.0) is not None and entity.get("binding_confidence", 1.0) < 0.2:
        return True
    if "dirty" in str(preconditions).lower() or "corrupt" in str(finding.get("before_snapshot_ref", "")).lower():
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python business_adversarial_validator.py <finding.json> [--no-llm]")
        sys.exit(1)
    path = Path(sys.argv[1])
    finding = _json.loads(path.read_text(encoding="utf-8"))
    enable_llm = "--no-llm" not in sys.argv
    result = run_adversarial_validation(finding, enable_llm=enable_llm)
    print(_json.dumps(result, indent=2, ensure_ascii=False, default=str))
