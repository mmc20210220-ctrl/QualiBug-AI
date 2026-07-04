from __future__ import annotations

from typing import Any


_STRICT_VERIFIER_PASS_VERDICTS = {
    "validated_bug",
    "validated_candidate",
    "confirmed",
    "confirmed_bug",
    "pass",
    "passed",
    "success",
}

_STRICT_VERIFIER_FAIL_VERDICTS = {
    "falsified",
    "rejected",
    "failed",
    "fail",
    "inconclusive",
    "needs_more_evidence",
    "schema_invalid",
    "observation_pending",
    "configuration_invalid",
    "execution_error",
    "entity_binding_missing",
}

_CANDIDATE_ONLY_ERRORS = {
    "candidate_only",
    "candidate_only_or_missing_base_url",
    "destructive_probe_blocked",
    "write_probe_blocked_by_safety_gate",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    return [value]


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _non_empty_items(values: list[Any]) -> list[Any]:
    return [item for item in values if str(item or "").strip()]


def _extract_verifier_records(issue: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("verification", "verifier", "strict_verification", "verification_result"):
        value = issue.get(key)
        if isinstance(value, dict):
            records.append(value)
    evidence = _as_dict(issue.get("evidence"))
    for key in ("verification", "verifier", "strict_verification"):
        value = evidence.get(key)
        if isinstance(value, dict):
            records.append(value)
    return records


def _find_verifier_verdict(issue: dict[str, Any]) -> str:
    for record in _extract_verifier_records(issue):
        for key in ("verdict", "status", "result"):
            verdict = _norm(record.get(key))
            if verdict:
                return verdict
    for key in ("verdict", "final_review_status", "review_status"):
        verdict = _norm(issue.get(key))
        if verdict:
            return verdict
    return ""


def _has_reproduction(issue: dict[str, Any]) -> bool:
    evidence = _as_dict(issue.get("evidence"))
    reproduction_pack = _as_dict(issue.get("reproduction_pack"))
    if _non_empty_items(_as_list(issue.get("reproduction_steps"))):
        return True
    if _non_empty_items(_as_list(evidence.get("reproduction_steps"))):
        return True
    if _non_empty_items(_as_list(reproduction_pack.get("reproduction_steps"))):
        return True
    if _as_dict(reproduction_pack.get("request")) and _as_dict(reproduction_pack.get("response")):
        return True
    if _as_dict(evidence.get("request")) and _as_dict(evidence.get("response")):
        return True
    if _non_empty_items(_as_list(evidence.get("trace"))):
        return True
    return False


def _collect_evidence_refs(issue: dict[str, Any]) -> list[str]:
    evidence = _as_dict(issue.get("evidence"))
    refs: list[str] = []
    refs.extend(str(item).strip() for item in _as_list(issue.get("evidence_refs")))
    if issue.get("evidence_ref"):
        refs.append(str(issue.get("evidence_ref")).strip())
    if issue.get("system_state_evidence"):
        refs.append("system_state_evidence")
    if issue.get("probe_id"):
        refs.append(f"probe:{issue.get('probe_id')}")
    if _as_dict(evidence.get("request")):
        refs.append("evidence.request")
    if _as_dict(evidence.get("response")):
        refs.append("evidence.response")
    if _non_empty_items(_as_list(evidence.get("trace"))):
        refs.append("evidence.trace")
    if _non_empty_items(_as_list(issue.get("reproduction_steps"))):
        refs.append("reproduction_steps")
    return sorted({ref for ref in refs if ref})


def classify_issue_accounting(issue: dict[str, Any]) -> dict[str, Any]:
    verdict = _find_verifier_verdict(issue)
    verifier_passed = verdict in _STRICT_VERIFIER_PASS_VERDICTS
    verifier_failed = verdict in _STRICT_VERIFIER_FAIL_VERDICTS
    evidence = _as_dict(issue.get("evidence"))
    response = _as_dict(evidence.get("response"))
    response_error = _norm(response.get("error"))
    has_reproduction = _has_reproduction(issue)
    evidence_refs = _collect_evidence_refs(issue)
    has_evidence_refs = bool(evidence_refs)
    quality_gap = bool(issue.get("quality_assurance_gap"))

    blocker_reason_codes: list[str] = []
    if quality_gap:
        blocker_reason_codes.append("quality_assurance_gap")
    if response_error in _CANDIDATE_ONLY_ERRORS:
        blocker_reason_codes.append(response_error)
    elif response_error:
        blocker_reason_codes.append(f"execution_{response_error}")
    if verifier_failed:
        blocker_reason_codes.append(f"verifier_{verdict}")
    elif not verifier_passed:
        blocker_reason_codes.append("missing_strict_verifier")
    if not has_reproduction:
        blocker_reason_codes.append("missing_reproduction")
    if not has_evidence_refs:
        blocker_reason_codes.append("missing_evidence_refs")

    strict_validated_bug = bool(
        not quality_gap
        and verifier_passed
        and has_reproduction
        and has_evidence_refs
    )

    candidate_signal = bool(
        quality_gap
        or response_error in _CANDIDATE_ONLY_ERRORS
        or (
            not verifier_passed
            and not verifier_failed
            and not has_reproduction
            and not has_evidence_refs
        )
    )

    accounting_state = "validated" if strict_validated_bug else "candidate" if candidate_signal else "pending"

    # ── Quality tier: separate real bugs from governance/coverage gaps ──
    quality_tier = "confirmed_bug"
    if quality_gap:
        quality_tier = "coverage_gap"
    elif response_error in _CANDIDATE_ONLY_ERRORS:
        quality_tier = "unexecuted"
    elif verifier_failed:
        quality_tier = "falsified"
    elif not verifier_passed and not strict_validated_bug:
        quality_tier = "heuristic_signal"
    elif not has_reproduction or not has_evidence_refs:
        quality_tier = "pending_verification"

    # Saleable = confirmed bugs only, not gaps/unexecuted/heuristic
    saleable = quality_tier == "confirmed_bug"

    return {
        "accounting_state": accounting_state,
        "quality_tier": quality_tier,
        "saleable": saleable,
        "strict_validated_bug": strict_validated_bug,
        "verifier_verdict": verdict,
        "verifier_passed": verifier_passed,
        "verifier_failed": verifier_failed,
        "has_reproduction": has_reproduction,
        "has_evidence_refs": has_evidence_refs,
        "evidence_refs": evidence_refs,
        "blocker_reason_codes": blocker_reason_codes,
        "primary_blocker_reason_code": blocker_reason_codes[0] if blocker_reason_codes else "",
    }


def enrich_issue_accounting(issue: dict[str, Any]) -> dict[str, Any]:
    accounting = classify_issue_accounting(issue)
    return {
        **issue,
        "validated_bug": accounting["strict_validated_bug"],
        "validated_bug_accounting": accounting,
    }
