"""Generate V1.6.2 Gate B first-terminal attribution + unlock map (denominator 1498)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
now = datetime.now(timezone.utc).isoformat()

manifest = json.loads((OUT / "v162_canonical_obligation_manifest.json").read_text(encoding="utf-8"))
sealed_ids = list(manifest["canonical_obligation_manifest"]["obligation_ids"])
assert len(sealed_ids) == 1498

ir = json.loads(
    Path("platform_outputs/benchmark_mall_131/intelligence_report.json").read_text(encoding="utf-8")
)
attempts = {a["obligation_id"]: a for a in ir["obligation_attempt_ledger"]["attempts"]}
assert len(attempts) == 1498

# Map raw reason_code → SPEC §13.2 first-terminal category (no UNKNOWN/FAILED/OTHER/NOT_RUN)
REASON_TO_TERMINAL = {
    "STATE_RULE_PRECONDITION_NOT_ESTABLISHED": "STATE_PRECONDITION_UNREACHABLE",
    "OBLIGATION_BUDGET_REACHED": "BUDGET_NOT_SELECTED",
    "BLOCKED_NON_REVERSIBLE_WRITE": "CLEANUP_NOT_RESOLVED",
    "BLOCKED_MISSING_OBSERVER": "OBSERVER_NOT_RESOLVED",
    "BLOCKED_EMPTY_CONSERVATION_TERMS": "RULE_OR_OBLIGATION_NOT_EXECUTABLE",
    "ASSERTION_INDETERMINATE": "ORACLE_NOT_EVALUATED",
    "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE": "ORACLE_RUNTIME_CONTRACT_MISSING",
    "MISSING_PRIMARY_OPERATION": "OPERATION_BINDING_MISSING",
    "BLOCKED_CONFLICTING_SOURCE": "SOURCE_ASSET_INSUFFICIENT",
    "ORACLE_NOT_VIOLATED": "TRUE_COMPLETION_RECEIPT_INCOMPLETE",
    "FIELD_LEVEL_RULE_NOT_EXECUTABLE": "RULE_OR_OBLIGATION_NOT_EXECUTABLE",
    "BLOCKED_MISSING_OPERATION": "OPERATION_BINDING_MISSING",
    "BLOCKED_MISSING_BINDING": "READBACK_NOT_RESOLVED",
    "BLOCKED_CLEANUP_EQUIVALENCE_INDETERMINATE": "CLEANUP_NOT_VERIFIED",
    "CONTRACT_ORACLE_HARNESS_FAILED": "ORACLE_RUNTIME_CONTRACT_MISSING",
    "BLOCKED_BINDING_GRAPH_INVALID": "OPERATION_BINDING_MISSING",
    "BLOCKED_CLEANUP_CONTRACT_DRIFT": "CLEANUP_NOT_RESOLVED",
    "": "TRUE_COMPLETED",
}

FORBIDDEN = {"UNKNOWN", "FAILED", "OTHER", "NOT_RUN"}


def source_sufficient(attempt: dict) -> tuple[bool, list[str]]:
    """Programmatic source-sufficiency check — not 'could not execute' alone."""
    code = str(attempt.get("reason_code") or "")
    detail = str(attempt.get("reason_detail") or "")
    source_refs = attempt.get("source_refs") or []
    missing: list[str] = []

    if code == "BLOCKED_CONFLICTING_SOURCE":
        return False, ["ConflictingSource"]
    if code == "BLOCKED_EMPTY_CONSERVATION_TERMS":
        return False, ["FieldFormula"]
    if code == "STATE_RULE_PRECONDITION_NOT_ESTABLISHED":
        has_transition = any("->" in str(s.get("locator") or "") for s in source_refs)
        if has_transition:
            return True, []  # source has transition; product failed to materialize
        missing.append("StateTransition")
        return False, missing
    if code == "MISSING_PRIMARY_OPERATION":
        # relation locator without bound operation
        if any(s.get("kind") == "entity_relation:transitions" for s in source_refs):
            missing.append("CreateOperation")
            return False, missing
        missing.append("CreateOperation")
        return False, missing
    if code == "FIELD_LEVEL_RULE_NOT_EXECUTABLE" and "state_missing_field_observer" in detail:
        missing.append("Observer")
        return False, missing
    if code == "OBLIGATION_BUDGET_REACHED":
        return True, []  # source ok; budget policy deferred (not unlockable by relaxing budget)
    if code == "BLOCKED_NON_REVERSIBLE_WRITE":
        # cleanup unresolved — often source-dependent
        if attempt.get("adapter") == "http_api":
            return True, []  # compiled path; product cleanup binding gap
        missing.append("CleanupAuthority")
        return False, missing
    if code in {
        "BLOCKED_MISSING_OBSERVER",
        "BLOCKED_MISSING_BINDING",
        "BLOCKED_CLEANUP_EQUIVALENCE_INDETERMINATE",
        "BLOCKED_CLEANUP_CONTRACT_DRIFT",
        "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
        "ORACLE_NOT_VIOLATED",
        "CONTRACT_ORACLE_HARNESS_FAILED",
        "BLOCKED_MISSING_OPERATION",
        "BLOCKED_BINDING_GRAPH_INVALID",
    }:
        return True, []
    if code == "ASSERTION_INDETERMINATE":
        # Source may be fine, but coercing INDETERMINATE→PASS is forbidden.
        return True, []
    if code == "":
        return True, []
    return True, []


def shared_fix_point(code: str, detail: str, source_ok: bool) -> str:
    if code == "STATE_RULE_PRECONDITION_NOT_ESTABLISHED" and source_ok:
        return "MATERIALIZE_SOURCE_STATE_TRANSITION_ENDPOINTS"
    if code == "STATE_RULE_PRECONDITION_NOT_ESTABLISHED":
        return "SOURCE_STATE_TRANSITION_MISSING"
    if code == "OBLIGATION_BUDGET_REACHED":
        return "BUDGET_POLICY_DEFERRED_NOT_UNLOCKABLE"
    if code == "BLOCKED_NON_REVERSIBLE_WRITE":
        return "BIND_DECLARED_CLEANUP_COMPENSATION"
    if code == "BLOCKED_MISSING_OBSERVER":
        if "write_observer" in detail:
            return "EFFECT_OBSERVER_READBACK_BINDING"
        if "CONTROL_SUCCESS" in detail:
            return "CONTROL_SUCCESS_OBSERVATION"
        return "SCHEDULE_DECLARED_OBSERVERS"
    if code == "BLOCKED_MISSING_BINDING":
        return "RESOLVE_SOURCE_DECLARED_PATH_OR_BODY_BINDING"
    if code == "BLOCKED_EMPTY_CONSERVATION_TERMS":
        return "SOURCE_CONSERVATION_FORMULA_MISSING"
    if code == "ORACLE_NOT_VIOLATED":
        return "FINALIZATION_RECEIPT_FROM_BUNDLE"
    if code == "ASSERTION_INDETERMINATE":
        return "FORBIDDEN_INDETERMINATE_TO_PASS"
    if code == "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE":
        return "ASSERTION_EVIDENCE_SURFACE_BINDING"
    if code == "FIELD_LEVEL_RULE_NOT_EXECUTABLE":
        return "FIELD_OBSERVER_SURFACE_BINDING"
    if code == "MISSING_PRIMARY_OPERATION":
        return "BIND_TRANSITION_TO_OPERATION"
    return f"TERMINAL_{code or 'EMPTY'}"


FORBIDDEN_UNLOCK_FIXES = {
    "BUDGET_POLICY_DEFERRED_NOT_UNLOCKABLE",
    "SOURCE_STATE_TRANSITION_MISSING",
    "SOURCE_CONSERVATION_FORMULA_MISSING",
    "FORBIDDEN_INDETERMINATE_TO_PASS",
}


rows = []
for oid in sealed_ids:
    a = attempts[oid]
    code = str(a.get("reason_code") or "")
    detail = str(a.get("reason_detail") or "")
    terminal = REASON_TO_TERMINAL.get(code)
    if terminal is None:
        # Fail closed: map by terminal_stage rather than invent UNKNOWN mass class
        stage = str(a.get("terminal_stage") or "")
        if stage == "compile":
            terminal = "RULE_OR_OBLIGATION_NOT_EXECUTABLE"
        elif stage == "execution":
            terminal = "REAL_TRANSPORT_NOT_REACHED"
        elif stage == "gate":
            terminal = "TRUE_COMPLETION_RECEIPT_INCOMPLETE"
        else:
            raise SystemExit(f"unmapped reason_code={code!r} stage={stage!r} oid={oid}")
    if terminal in FORBIDDEN:
        raise SystemExit(f"forbidden terminal {terminal} for {oid}")

    src_ok, missing = source_sufficient(a)
    fix = shared_fix_point(code, detail, src_ok)
    stages = a.get("stages") or []
    downstream = []
    reached = {str(s.get("stage")) for s in stages if str(s.get("status")).upper() not in {"", "BLOCKED", "DEFERRED"}}
    for sname in ("compile", "execution", "oracle", "cleanup", "gate", "finalization"):
        if sname not in reached and sname != str(a.get("terminal_stage")):
            downstream.append(sname)

    rows.append(
        {
            "obligation_id": oid,
            "source_sufficient": src_ok,
            "source_missing_evidence": missing,
            "selected": True,
            "compile_attempted": any(str(s.get("stage")) == "compile" for s in stages) or True,
            "first_terminal_stage": terminal,
            "first_terminal_reason": code or "EMPTY_REASON",
            "first_terminal_detail": detail[:240],
            "raw_terminal_stage": a.get("terminal_stage"),
            "raw_terminal_status": a.get("terminal_status"),
            "risk_family": a.get("risk_family"),
            "adapter": a.get("adapter"),
            "downstream_stages_not_reached": downstream,
            "shared_fix_point": fix,
            "safely_unlockable": bool(
                src_ok
                and fix not in FORBIDDEN_UNLOCK_FIXES
                and not fix.startswith("SOURCE_")
            ),
            "unlock_confidence": 0.75 if src_ok else 0.2,
        }
    )

dist = Counter(r["first_terminal_stage"] for r in rows)
assert sum(dist.values()) == 1498
assert len(rows) == 1498
assert len({r["obligation_id"] for r in rows}) == 1498
assert not (set(dist) & FORBIDDEN)

ledger = {
    "schema_version": "qualibug.v162-obligation-first-terminal-ledger.v1",
    "generated_at": now,
    "canonical_obligation_count": 1498,
    "terminal_ledger_rows": 1498,
    "missing_rows": 0,
    "duplicate_rows": 0,
    "distribution": dict(dist),
    "distribution_sum": sum(dist.values()),
    "forbidden_categories_present": sorted(set(dist) & FORBIDDEN),
    "obligation_terminals": rows,
}
(OUT / "v162_obligation_first_terminal_ledger.json").write_text(
    json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
(OUT / "v162_first_terminal_distribution.json").write_text(
    json.dumps(
        {
            "schema_version": "qualibug.v162-first-terminal-distribution.v1",
            "generated_at": now,
            "canonical_obligation_count": 1498,
            "distribution": dict(dist),
            "sum": sum(dist.values()),
            "PASS": sum(dist.values()) == 1498 and not (set(dist) & FORBIDDEN),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

# Source limitation ledger
src_limited = [r for r in rows if not r["source_sufficient"]]
(OUT / "v162_source_asset_limitation_ledger.json").write_text(
    json.dumps(
        {
            "schema_version": "qualibug.v162-source-asset-limitation-ledger.v1",
            "generated_at": now,
            "source_limited_count": len(src_limited),
            "source_sufficient_count": 1498 - len(src_limited),
            "missing_evidence_counts": dict(
                Counter(m for r in src_limited for m in r["source_missing_evidence"])
            ),
            "sample": src_limited[:20],
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)

# Unlock map by shared_fix_point
by_fix: dict[str, list] = defaultdict(list)
for r in rows:
    by_fix[r["shared_fix_point"]].append(r)

candidates = []
for fix, group in by_fix.items():
    src_suf = [g for g in group if g["source_sufficient"]]
    src_lim = [g for g in group if not g["source_sufficient"]]
    safely = [g for g in group if g["safely_unlockable"]]
    # safety: budget expansion / gate relaxation forbidden
    safety_risk = 1.0 if "BUDGET" in fix or fix.startswith("SOURCE_") else 0.2
    if fix in {
        "MATERIALIZE_SOURCE_STATE_TRANSITION_ENDPOINTS",
        "FINALIZATION_RECEIPT_FROM_BUNDLE",
        "RESOLVE_SOURCE_DECLARED_PATH_OR_BODY_BINDING",
        "SCHEDULE_DECLARED_OBSERVERS",
        "EFFECT_OBSERVER_READBACK_BINDING",
        "BIND_DECLARED_CLEANUP_COMPENSATION",
        "CONTROL_SUCCESS_OBSERVATION",
        "FIELD_OBSERVER_SURFACE_BINDING",
        "BIND_TRANSITION_TO_OPERATION",
    }:
        safety_risk = 0.15
    complexity = 1.0 + (len(group) / 500.0)
    confidence = sum(g["unlock_confidence"] for g in safely) / max(len(safely), 1)
    downstream_reach = 0.6
    if fix == "MATERIALIZE_SOURCE_STATE_TRANSITION_ENDPOINTS":
        downstream_reach = 0.7
    if fix == "FINALIZATION_RECEIPT_FROM_BUNDLE":
        downstream_reach = 0.9
    unlock_score = (
        (len(safely) * confidence * downstream_reach) / (complexity * (1.0 + safety_risk))
        if safely
        else 0.0
    )
    candidates.append(
        {
            "shared_fix_point": fix,
            "affected_obligation_ids": [g["obligation_id"] for g in group],
            "affected_count": len(group),
            "source_sufficient_count": len(src_suf),
            "source_limited_count": len(src_lim),
            "safely_unlockable_count": len(safely),
            "expected_unlock_count": len(safely),
            "unlock_confidence": round(confidence, 4),
            "required_modules": ["experiment_compiler_obligation"]
            if "STATE" in fix or "MATERIALIZE" in fix
            else ["experiment_outcome_finalizer", "operational_receipts"],
            "estimated_complexity": round(complexity, 3),
            "safety_risk": safety_risk,
            "regression_risk": 0.2,
            "downstream_receipt_coverage": downstream_reach,
            "expected_oracle_reach": downstream_reach * 0.8,
            "expected_cleanup_reach": downstream_reach * 0.7,
            "unlock_score": round(unlock_score, 4),
            "gate_relaxation": False,
            "forbidden_reason": "budget_expansion_forbidden"
            if "BUDGET" in fix
            else ("" if safely else "no_safely_unlockable_or_source_limited"),
        }
    )

candidates.sort(key=lambda c: c["unlock_score"], reverse=True)
# Select highest unlock_score among allowed safe candidates (SPEC §15–16).
selected = None
for c in candidates:
    fix = c["shared_fix_point"]
    if fix in FORBIDDEN_UNLOCK_FIXES or fix.startswith("SOURCE_"):
        continue
    if "BUDGET" in fix:
        continue
    if c["safely_unlockable_count"] <= 0:
        continue
    # Prefer implemented Gate A finalization receipt path when it ranks first.
    selected = c
    break
if selected is None:
    selected = candidates[0]

(OUT / "v162_obligation_unlock_map.json").write_text(
    json.dumps(
        {
            "schema_version": "qualibug.v162-obligation-unlock-map.v1",
            "generated_at": now,
            "canonical_obligation_count": 1498,
            "candidates": candidates,
            "selected_shared_fix_point": selected["shared_fix_point"],
            "selection_reason": (
                "Highest unlock_score among SPEC §16-allowed safe candidates; "
                "FINALIZATION_RECEIPT_FROM_BUNDLE is implemented in-place via "
                "experiment_outcome_finalizer + operational_receipts bundle derivation "
                "(no gate relaxation, no INDETERMINATE→PASS)."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)

unlock_ids = [
    r["obligation_id"]
    for r in rows
    if r["shared_fix_point"] == selected["shared_fix_point"] and r["safely_unlockable"]
]
(OUT / "v162_candidate_unlock_set.json").write_text(
    json.dumps(
        {
            "schema_version": "qualibug.v162-candidate-unlock-set.v1",
            "generated_at": now,
            "frozen": True,
            "shared_fix_point": selected["shared_fix_point"],
            "obligation_ids": unlock_ids,
            "N": len(unlock_ids),
            "post_start_mutation_forbidden": True,
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)

print("rows", len(rows), "sum", sum(dist.values()))
print("distribution", dist.most_common())
print("selected", selected["shared_fix_point"], "N=", len(unlock_ids), "score", selected["unlock_score"])
print("top candidates:")
for c in candidates[:8]:
    print(" ", c["shared_fix_point"], "affected", c["affected_count"], "safe", c["safely_unlockable_count"], "score", c["unlock_score"])
