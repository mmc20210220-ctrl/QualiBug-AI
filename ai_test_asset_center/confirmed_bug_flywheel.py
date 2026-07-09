from __future__ import annotations

"""Phase55: enterprise confirmed-Bug learning flywheel.

The existing project has human review queues and heuristic feedback weights.  This
module turns them into an auditable, approval-gated enterprise learning loop:

1. Candidate finding -> reviewer decision is appended to a tamper-evident ledger.
2. Confirmed defects and narrow false-positive exceptions become *pending*
   promotions; nothing changes execution priority automatically.
3. A different quality owner approves a promotion before it can influence probe
   priority or be added to a regression suite.
4. Persisted learning artifacts contain only redacted metadata and hashes; raw
   request/response payloads and reviewer notes are never copied into memory.

The module deliberately does not create new Bug templates.  It improves the
quality and reuse of evidence already produced by the discovery engines.
"""

import argparse
import hashlib
import html
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .bug_pattern_memory import BugPatternMemory, llm_enhanced_learn
from .real_project_onboarding import ROOT, _safe_project_id, _write_json, config_paths, load_real_project_config

PHASE = "phase55_confirmed_bug_learning_flywheel"
LEDGER_VERSION = 1
SEVERITIES = {"P0", "P1", "P2", "P3"}
DECISIONS = {
    "confirmed",
    "false_positive",
    "duplicate",
    "accepted_risk",
    "needs_evidence",
    "fixed_verified",
    "regression_passed",
    "reopened",
}
APPROVER_ROLES = {"quality_owner", "qa_lead", "release_manager", "security_owner", "admin"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SECRET_KEY_RE = re.compile(r"(token|secret|password|authorization|cookie|api[_-]?key|session)", re.I)
PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "enabled_bugs", "current_bug_set", "bug_instance_id"}
STOPWORDS = {
    "the", "and", "with", "from", "this", "that", "interface", "system", "service", "data",
    "接口", "系统", "功能", "数据", "问题", "用户", "业务", "发现", "异常", "需要", "正常",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short_hash(value: Any, length: int = 16) -> str:
    return _hash(value)[:length]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _path_template(value: Any) -> str:
    path = str(value or "/").strip() or "/"
    path = re.sub(r"https?://[^/]+", "", path)
    path = path.split("?", 1)[0]
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    path = re.sub(r"/[0-9a-f]{8,}(?=/|$)", "/{id}", path, flags=re.I)
    path = re.sub(r"/[A-Za-z0-9_-]{16,}(?=/|$)", "/{id}", path)
    if not path.startswith("/"):
        path = "/" + path
    return path[:300]


def _keywords(value: Any, limit: int = 20) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", str(value or ""))
    out: list[str] = []
    for token in tokens:
        key = _norm(token)
        if key and key not in STOPWORDS and key not in out:
            out.append(key)
        if len(out) >= limit:
            break
    return out


def _redacted_digest(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return _short_hash(value, 24)


def _safe_dict(value: Any, depth: int = 0) -> Any:
    """Keep field shape but replace raw values with non-reversible fingerprints."""
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:60]:
            key_text = str(key)[:120]
            if SECRET_KEY_RE.search(key_text):
                out[key_text] = "[redacted]"
            elif isinstance(item, (dict, list)):
                out[key_text] = _safe_dict(item, depth + 1)
            elif item is None or isinstance(item, bool):
                out[key_text] = item
            elif isinstance(item, (int, float)):
                out[key_text] = "[number]"
            else:
                out[key_text] = f"[hash:{_short_hash(item, 12)}]"
        return out
    if isinstance(value, list):
        return [_safe_dict(item, depth + 1) for item in value[:30]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"[hash:{_short_hash(value, 12)}]"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    cfg = config_paths(project, root)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    out = root / "platform_outputs" / project / "confirmed_bug_flywheel"
    return {
        **cfg,
        "workspace": workspace,
        "out": out,
        "ledger": workspace / "confirmed_bug_decision_ledger.jsonl",
        "profile": workspace / "confirmed_bug_flywheel_profile.json",
        "registry": workspace / "confirmed_bug_registry.json",
        "promotion_manifest": workspace / "confirmed_bug_promotion_manifest.json",
        "exceptions": workspace / "confirmed_bug_exception_registry.json",
        "regression_candidates": workspace / "confirmed_bug_regression_candidates.json",
        "feedback_projection": workspace / "confirmed_bug_feedback.jsonl",
    }


def _candidate_ref(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("evidence") or candidate.get("evidence_bundle") or {}
    if not isinstance(evidence, dict):
        evidence = {"raw": evidence}
    method = str(candidate.get("method") or (evidence.get("request") or {}).get("method") or "GET").upper()
    path = candidate.get("path") or candidate.get("affected_api") or candidate.get("api") or (evidence.get("request") or {}).get("url") or "/"
    title = str(candidate.get("title") or candidate.get("name") or candidate.get("issue_id") or candidate.get("source_bug_key") or "candidate")
    risk = str(candidate.get("risk_type") or candidate.get("predicted_risk_type") or "business_rule")
    oracle_family = str(
        candidate.get("oracle_family")
        or candidate.get("reasoning_type")
        or candidate.get("business_invariant_type")
        or candidate.get("business_reconciliation_type")
        or candidate.get("business_outcome_type")
        or candidate.get("source")
        or risk
    )
    ref = {
        "issue_id": str(candidate.get("issue_id") or candidate.get("source_bug_key") or candidate.get("discovered_bug_id") or candidate.get("probe_id") or "")[:160] or None,
        "probe_id": str(candidate.get("probe_id") or candidate.get("contract_id") or "")[:160] or None,
        "contract_id": str(candidate.get("contract_id") or "")[:160] or None,
        "risk_type": risk[:120],
        "oracle_family": oracle_family[:160],
        "source": str(candidate.get("source") or "unknown")[:120],
        "method": method[:12],
        "path_template": _path_template(path),
        "severity": str(candidate.get("severity") or "P2").upper()[:8],
        "title_keywords": _keywords(title),
        "expected_digest": _redacted_digest(candidate.get("expected") or evidence.get("expected")),
        "actual_digest": _redacted_digest(candidate.get("actual") or evidence.get("actual")),
        "evidence_digest": _redacted_digest(_safe_dict(evidence)),
    }
    ref["business_fingerprint"] = _short_hash({
        "risk_type": _norm(ref["risk_type"]),
        "oracle_family": _norm(ref["oracle_family"]),
        "source": _norm(ref["source"]),
        "method": ref["method"],
        "path_template": ref["path_template"],
        "probe_id": ref["probe_id"],
        "expected": ref["expected_digest"],
        "actual": ref["actual_digest"],
    }, 24)
    return ref


def _evidence_score(candidate: dict[str, Any], ref: dict[str, Any], override: Any = None) -> float:
    try:
        if override is not None:
            return max(0.0, min(1.0, float(override)))
    except Exception:
        pass
    score = 0.0
    if ref.get("expected_digest"):
        score += 0.30
    if ref.get("actual_digest"):
        score += 0.30
    if ref.get("evidence_digest"):
        score += 0.20
    if ref.get("path_template") and ref.get("path_template") != "/":
        score += 0.10
    if candidate.get("confidence") is not None:
        try:
            score += min(0.10, max(0.0, float(candidate.get("confidence"))) * 0.10)
        except Exception:
            pass
    return round(min(1.0, score), 3)


def _safe_exception_scope(raw: Any, ref: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    risk = str(raw.get("risk_type") or ref.get("risk_type") or "").strip()
    method = str(raw.get("method") or ref.get("method") or "").upper().strip()
    path = _path_template(raw.get("path") or raw.get("path_template") or ref.get("path_template") or "")
    # Broad risk suppression is intentionally forbidden.
    if not risk or not method or not path or path == "/":
        return None
    scope = {
        "risk_type": risk[:120],
        "method": method[:12],
        "path_template": path,
        "source": str(raw.get("source") or ref.get("source") or "")[:120] or None,
        "contract_id": str(raw.get("contract_id") or "")[:160] or None,
        "expires_at_utc": str(raw.get("expires_at_utc") or raw.get("expires_at") or "")[:40] or None,
        "condition_fingerprint": _short_hash(_safe_dict(raw.get("condition") or raw.get("why") or ""), 18),
    }
    return scope


def verify_decision_ledger(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    rows = _read_jsonl(_paths(project, root)["ledger"])
    previous = ""
    errors: list[dict[str, Any]] = []
    for index, event in enumerate(rows, start=1):
        claimed = str(event.get("event_hash") or "")
        payload = dict(event)
        payload.pop("event_hash", None)
        expected = _hash(payload)
        if claimed != expected:
            errors.append({"index": index, "event_id": event.get("event_id"), "reason": "event_hash_mismatch"})
        if str(event.get("previous_event_hash") or "") != previous:
            errors.append({"index": index, "event_id": event.get("event_id"), "reason": "chain_link_mismatch"})
        previous = claimed
    return {
        "passed": not errors,
        "ledger_version": LEDGER_VERSION,
        "event_count": len(rows),
        "head_hash": previous or None,
        "errors": errors[:50],
    }


def _append_event(project: str, root: Path, event_type: str, payload: dict[str, Any], actor: str, actor_role: str) -> dict[str, Any]:
    paths = _paths(project, root)
    check = verify_decision_ledger(project, root)
    if not check.get("passed"):
        raise ValueError("confirmed Bug ledger verification failed; append is blocked until audit issue is resolved")
    previous = str(check.get("head_hash") or "")
    event = {
        "ledger_version": LEDGER_VERSION,
        "event_id": f"CBF_{event_type.upper()}_{_short_hash({'project': project, 'time': _now(), 'payload': payload, 'previous': previous}, 20)}",
        "event_type": event_type,
        "event_at_utc": _now(),
        "project_id": project,
        "actor_id": str(actor or "unknown")[:120],
        "actor_role": str(actor_role or "unknown")[:80],
        "payload": payload,
        "previous_event_hash": previous,
    }
    event["event_hash"] = _hash(event)
    _append_jsonl(paths["ledger"], event)
    return event


def _decision_from_review(review: dict[str, Any]) -> str:
    explicit = str(review.get("decision") or review.get("status") or review.get("review_status") or "").strip().lower()
    aliases = {
        "confirmed": "confirmed", "valid": "confirmed", "accepted": "confirmed", "true_bug": "confirmed", "已确认": "confirmed", "确认缺陷": "confirmed",
        "false_positive": "false_positive", "not_a_bug": "false_positive", "invalid": "false_positive", "误报": "false_positive",
        "duplicate": "duplicate", "重复": "duplicate",
        "accepted_risk": "accepted_risk", "风险接受": "accepted_risk",
        "needs_evidence": "needs_evidence", "needs_review": "needs_evidence", "待补证据": "needs_evidence",
        "fixed_verified": "fixed_verified", "已修复": "fixed_verified",
        "regression_passed": "regression_passed", "回归通过": "regression_passed",
        "reopened": "reopened", "重新打开": "reopened",
    }
    if explicit in aliases:
        return aliases[explicit]
    if review.get("is_false_positive") is True or review.get("is_valid_bug") is False:
        return "false_positive"
    if review.get("is_duplicate") is True:
        return "duplicate"
    if review.get("is_valid_bug") is True or review.get("confirmed") is True:
        return "confirmed"
    return "needs_evidence"


def record_bug_review(
    project_id: str,
    candidate: dict[str, Any],
    review: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    """Append one human triage decision and rebuild the derived learning state.

    A reviewer may confirm a defect, but a separate quality owner must approve
    the generated promotion before it changes probe priority or regression scope.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    decision = _decision_from_review(review)
    if decision not in DECISIONS:
        raise ValueError(f"unsupported review decision: {decision}")
    reviewer = str(review.get("reviewer") or review.get("actor") or "qa_reviewer").strip()[:120]
    role = str(review.get("reviewer_role") or review.get("role") or "qa_reviewer").strip()[:80]
    if not reviewer:
        raise ValueError("reviewer is required")
    ref = _candidate_ref(candidate)
    severity = str(review.get("human_severity") or review.get("severity") or ref.get("severity") or "P2").upper()
    if severity not in SEVERITIES:
        severity = "P2"
    evidence_score = _evidence_score(candidate, ref, review.get("evidence_completeness"))
    duplicate_of = str(review.get("duplicate_of") or review.get("duplicate_of_fingerprint") or "").strip()[:160] or None
    exception_scope = _safe_exception_scope(review.get("exception_scope"), ref) if decision == "false_positive" else None
    payload = {
        "candidate": ref,
        "decision": decision,
        "severity": severity,
        "root_cause": str(review.get("root_cause") or "unknown")[:120],
        "is_high_value": bool(review.get("is_high_value")),
        "evidence_completeness": evidence_score,
        "duplicate_of": duplicate_of,
        "exception_scope": exception_scope,
        "notes_digest": _redacted_digest(review.get("feedback_notes") or review.get("notes") or ""),
        "note_keywords": _keywords(review.get("feedback_notes") or review.get("notes") or ""),
        "review_source": str(review.get("source") or "enterprise_review")[:120],
    }
    event = _append_event(project, root, "review_recorded", payload, reviewer, role)
    profile = build_confirmed_bug_flywheel(project, root)
    return {
        "ok": True,
        "phase": PHASE,
        "event_id": event["event_id"],
        "business_fingerprint": ref["business_fingerprint"],
        "decision": decision,
        "promotion_required": decision in {"confirmed", "false_positive"},
        "profile_summary": profile.get("summary"),
        "ledger_check": profile.get("ledger_check"),
    }


def _event_rows(project: str, root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(_paths(project, root)["ledger"])


def _review_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("event_type") == "review_recorded" and isinstance((row.get("payload") or {}).get("candidate"), dict)]


def _approval_events(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event_type") != "promotion_approved":
            continue
        promotion_id = str((row.get("payload") or {}).get("promotion_id") or "")
        if promotion_id:
            result[promotion_id] = row
    return result


def _promotion_id(review_event: dict[str, Any], kind: str) -> str:
    candidate = ((review_event.get("payload") or {}).get("candidate") or {})
    return f"PROMO_{kind.upper()}_{_short_hash({'fingerprint': candidate.get('business_fingerprint'), 'review_event': review_event.get('event_id')}, 18)}"


def _pending_promotions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    approved = _approval_events(rows)
    promotions: list[dict[str, Any]] = []
    for review_event in _review_events(rows):
        payload = review_event.get("payload") or {}
        decision = str(payload.get("decision") or "")
        candidate = payload.get("candidate") or {}
        evidence = float(payload.get("evidence_completeness") or 0)
        if decision == "confirmed":
            kind = "learning_and_regression"
            eligible = evidence >= 0.55 and not payload.get("duplicate_of")
        elif decision == "false_positive" and isinstance(payload.get("exception_scope"), dict):
            kind = "narrow_exception"
            eligible = True
        else:
            continue
        promotion_id = _promotion_id(review_event, kind)
        approval = approved.get(promotion_id)
        promotions.append({
            "promotion_id": promotion_id,
            "kind": kind,
            "review_event_id": review_event.get("event_id"),
            "business_fingerprint": candidate.get("business_fingerprint"),
            "risk_type": candidate.get("risk_type"),
            "oracle_family": candidate.get("oracle_family"),
            "path_template": candidate.get("path_template"),
            "reviewer_id": review_event.get("actor_id"),
            "evidence_completeness": evidence,
            "eligible": eligible,
            "status": "approved" if approval else ("pending_approval" if eligible else "needs_evidence"),
            "approval": {
                "event_id": approval.get("event_id"),
                "approver_id": approval.get("actor_id"),
                "approver_role": approval.get("actor_role"),
                "approved_at_utc": approval.get("event_at_utc"),
            } if approval else None,
            "exception_scope": payload.get("exception_scope"),
        })
    return promotions


def approve_learning_promotion(
    project_id: str,
    promotion_id: str,
    approver: str,
    approver_role: str = "quality_owner",
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    role = str(approver_role or "").strip().lower()
    if role not in APPROVER_ROLES:
        raise ValueError("approver_role must be one of: " + ", ".join(sorted(APPROVER_ROLES)))
    rows = _event_rows(project, root)
    target = next((item for item in _pending_promotions(rows) if item.get("promotion_id") == promotion_id), None)
    if not target:
        raise ValueError("promotion_id not found")
    if target.get("status") == "approved":
        return {"ok": True, "already_approved": True, "promotion_id": promotion_id, "profile_summary": build_confirmed_bug_flywheel(project, root).get("summary")}
    if not target.get("eligible"):
        raise ValueError("promotion is not eligible; add evidence or correct duplicate/exception metadata")
    if str(target.get("reviewer_id") or "") == str(approver or ""):
        raise ValueError("reviewer and approver must be different people")
    event = _append_event(project, root, "promotion_approved", {
        "promotion_id": promotion_id,
        "kind": target.get("kind"),
        "review_event_id": target.get("review_event_id"),
        "business_fingerprint": target.get("business_fingerprint"),
        "approval_policy": "four_eyes",
    }, approver, role)
    profile = build_confirmed_bug_flywheel(project, root)
    return {"ok": True, "promotion_id": promotion_id, "approval_event_id": event.get("event_id"), "profile_summary": profile.get("summary")}


def _is_expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        return value[:20] < _now()[:20]
    except Exception:
        return False


def _build_materialized(project: str, root: Path) -> dict[str, Any]:
    rows = _event_rows(project, root)
    ledger_check = verify_decision_ledger(project, root)
    promotions = _pending_promotions(rows)
    reviews = _review_events(rows)
    approvals = _approval_events(rows)
    approved_by_review = {str(item.get("review_event_id")): item for item in promotions if item.get("status") == "approved"}

    registry: dict[str, dict[str, Any]] = {}
    patterns: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    exceptions: list[dict[str, Any]] = []
    feedback_projection: list[dict[str, Any]] = []
    regression_candidates: list[dict[str, Any]] = []

    for event in reviews:
        payload = event.get("payload") or {}
        candidate = payload.get("candidate") or {}
        fingerprint = str(candidate.get("business_fingerprint") or "")
        if not fingerprint:
            continue
        decision = str(payload.get("decision") or "needs_evidence")
        promoted = approved_by_review.get(str(event.get("event_id")))
        entry = registry.setdefault(fingerprint, {
            "business_fingerprint": fingerprint,
            "first_seen_at_utc": event.get("event_at_utc"),
            "latest_seen_at_utc": event.get("event_at_utc"),
            "review_count": 0,
            "confirmed_count": 0,
            "approved_confirmation_count": 0,
            "false_positive_count": 0,
            "duplicate_count": 0,
            "risk_type": candidate.get("risk_type"),
            "oracle_family": candidate.get("oracle_family"),
            "path_template": candidate.get("path_template"),
            "method": candidate.get("method"),
            "source": candidate.get("source"),
            "title_keywords": candidate.get("title_keywords") or [],
            "severity_distribution": {},
            "root_cause_distribution": {},
            "latest_decision": decision,
            "learning_status": "not_approved",
            "raw_business_payloads_persisted": False,
        })
        entry["review_count"] += 1
        entry["latest_seen_at_utc"] = event.get("event_at_utc")
        entry["latest_decision"] = decision
        severity = str(payload.get("severity") or "P2")
        entry["severity_distribution"][severity] = int(entry["severity_distribution"].get(severity) or 0) + 1
        root_cause = str(payload.get("root_cause") or "unknown")
        entry["root_cause_distribution"][root_cause] = int(entry["root_cause_distribution"].get(root_cause) or 0) + 1
        if decision == "confirmed":
            entry["confirmed_count"] += 1
            if promoted and promoted.get("kind") == "learning_and_regression":
                entry["approved_confirmation_count"] += 1
                entry["learning_status"] = "approved"
                pattern_key = (
                    str(candidate.get("risk_type") or "business_rule"),
                    str(candidate.get("oracle_family") or "unknown"),
                    str(candidate.get("path_template") or "/"),
                    root_cause,
                )
                pattern = patterns.setdefault(pattern_key, {
                    "pattern_id": f"PAT_{_short_hash(pattern_key, 16)}",
                    "risk_type": pattern_key[0],
                    "oracle_family": pattern_key[1],
                    "path_template": pattern_key[2],
                    "source": candidate.get("source"),
                    "root_cause": pattern_key[3],
                    "confirmed_count": 0,
                    "high_value_count": 0,
                    "severity_distribution": Counter(),
                    "keywords": Counter(),
                    "member_fingerprints": [],
                })
                pattern["confirmed_count"] += 1
                if payload.get("is_high_value"):
                    pattern["high_value_count"] += 1
                pattern["severity_distribution"][severity] += 1
                for token in candidate.get("title_keywords") or []:
                    pattern["keywords"][token] += 1
                if fingerprint not in pattern["member_fingerprints"]:
                    pattern["member_fingerprints"].append(fingerprint)
                feedback_projection.append({
                    "feedback_id": f"phase55:{fingerprint}",
                    "is_valid_bug": True,
                    "confirmed": True,
                    "risk_type": candidate.get("risk_type"),
                    "oracle_family": candidate.get("oracle_family"),
                    "human_severity": severity,
                    "root_cause": root_cause,
                    "affected_api": candidate.get("path_template"),
                    "source": "phase55_confirmed_bug_flywheel",
                    "evidence_policy": "redacted_metadata_only",
                })
                regression_candidates.append({
                    "regression_probe_id": f"CBF_REG_{fingerprint[:16]}",
                    "issue_id": candidate.get("issue_id") or f"CBF_{fingerprint[:12]}",
                    "title": f"已确认缺陷回归：{candidate.get('risk_type') or 'business_rule'}",
                    "risk_type": candidate.get("risk_type") or "business_rule",
                    "severity": severity,
                    "method": candidate.get("method") or "GET",
                    "path": candidate.get("path_template") or "/",
                    "actor": "normal_user",
                    "expected": "已确认的业务反例不得再次出现；若需要写操作，应在隔离沙箱中验证。",
                    "oracle_family": candidate.get("oracle_family"),
                    "origin_confirmed_fingerprint": fingerprint,
                    "promotion_id": promoted.get("promotion_id"),
                    "source": "phase55_confirmed_bug_flywheel",
                    "execution_policy": "safe_read_only" if str(candidate.get("method") or "GET").upper() not in WRITE_METHODS else "sandbox_required",
                    "approved": True,
                })
        elif decision == "false_positive":
            entry["false_positive_count"] += 1
            if promoted and promoted.get("kind") == "narrow_exception":
                scope = payload.get("exception_scope") or {}
                if isinstance(scope, dict) and not _is_expired(scope.get("expires_at_utc")):
                    exceptions.append({
                        "exception_id": f"EXC_{_short_hash({'promotion': promoted.get('promotion_id'), 'scope': scope}, 16)}",
                        "promotion_id": promoted.get("promotion_id"),
                        "business_fingerprint": fingerprint,
                        "scope": scope,
                        "created_at_utc": event.get("event_at_utc"),
                        "approver": (promoted.get("approval") or {}).get("approver_id"),
                        "expires_at_utc": scope.get("expires_at_utc"),
                        "governance": "exact_scope_only_no_global_risk_suppression",
                    })
        elif decision == "duplicate":
            entry["duplicate_count"] += 1

    finalized_patterns: list[dict[str, Any]] = []
    for pattern in patterns.values():
        severity_counter = pattern.pop("severity_distribution")
        keyword_counter = pattern.pop("keywords")
        count = int(pattern.get("confirmed_count") or 0)
        high = int(pattern.get("high_value_count") or 0)
        severity_boost = 0.04 if any(key in {"P0", "P1"} for key in severity_counter) else 0.0
        bonus = min(0.25, 0.05 + 0.03 * min(count, 5) + 0.02 * min(high, 3) + severity_boost)
        pattern.update({
            "learning_bonus": round(bonus, 3),
            "severity_distribution": dict(severity_counter),
            "keywords": [key for key, _ in keyword_counter.most_common(15)],
            "member_count": len(pattern.get("member_fingerprints") or []),
            "member_fingerprints": (pattern.get("member_fingerprints") or [])[:100],
        })
        finalized_patterns.append(pattern)

    # De-dupe projections by feedback_id/regression id.
    projection_by_id = {str(row.get("feedback_id")): row for row in feedback_projection}
    regression_by_id = {str(row.get("regression_probe_id")): row for row in regression_candidates}
    summary = {
        "ledger_event_count": len(rows),
        "review_event_count": len(reviews),
        "approved_promotion_count": sum(1 for item in promotions if item.get("status") == "approved"),
        "pending_promotion_count": sum(1 for item in promotions if item.get("status") == "pending_approval"),
        "needs_evidence_promotion_count": sum(1 for item in promotions if item.get("status") == "needs_evidence"),
        "confirmed_bug_count": sum(1 for item in registry.values() if item.get("approved_confirmation_count")),
        "confirmed_bug_observation_count": sum(int(item.get("approved_confirmation_count") or 0) for item in registry.values()),
        "false_positive_count": sum(int(item.get("false_positive_count") or 0) for item in registry.values()),
        "approved_exception_count": len(exceptions),
        "learning_pattern_count": len(finalized_patterns),
        "approved_regression_candidate_count": len(regression_by_id),
        "raw_business_payloads_persisted": False,
    }
    return {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "summary": summary,
        "ledger_check": ledger_check,
        "registry": sorted(registry.values(), key=lambda item: (-int(item.get("approved_confirmation_count") or 0), str(item.get("business_fingerprint")))),
        "patterns": sorted(finalized_patterns, key=lambda item: (-int(item.get("confirmed_count") or 0), str(item.get("pattern_id")))),
        "promotions": promotions,
        "exceptions": sorted(exceptions, key=lambda item: str(item.get("exception_id"))),
        "feedback_projection": list(projection_by_id.values()),
        "regression_candidates": list(regression_by_id.values()),
        "governance": {
            "reviewer_approval_required": True,
            "four_eyes_required_for_promotion": True,
            "unconfirmed_findings_never_train_priority": True,
            "false_positive_requires_exact_exception_scope": True,
            "global_risk_suppression_forbidden": True,
            "raw_request_response_payloads_not_persisted": True,
            "ledger_hash_chain_required": True,
        },
    }


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = _json(data).lower()
    leaks = sorted(marker for marker in PRIVATE_MARKERS if marker in text)
    return {"passed": not leaks, "leak_terms": leaks}


def _render_report(profile: dict[str, Any]) -> str:
    summary = profile.get("summary") or {}
    ledger = profile.get("ledger_check") or {}
    rows = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
        for key, value in summary.items()
    )
    patterns = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('risk_type')))}</td>"
        f"<td>{html.escape(str(item.get('oracle_family')))}</td>"
        f"<td>{html.escape(str(item.get('path_template')))}</td>"
        f"<td>{html.escape(str(item.get('confirmed_count')))}</td>"
        f"<td>{html.escape(str(item.get('learning_bonus')))}</td>"
        "</tr>"
        for item in (profile.get("patterns") or [])[:80]
    ) or "<tr><td colspan='5'>尚无经独立审批的确认缺陷模式。</td></tr>"
    promotions = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('promotion_id')))}</td>"
        f"<td>{html.escape(str(item.get('kind')))}</td>"
        f"<td>{html.escape(str(item.get('status')))}</td>"
        f"<td>{html.escape(str(item.get('reviewer_id')))}</td>"
        f"<td>{html.escape(str((item.get('approval') or {}).get('approver_id') or '—'))}</td>"
        "</tr>"
        for item in (profile.get("promotions") or [])[:120]
    ) or "<tr><td colspan='5'>暂无待审批或已审批的回灌项。</td></tr>"
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>确认缺陷数据飞轮</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;background:#f6f8fb;color:#111827;margin:28px}}.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px;margin:16px 0;box-shadow:0 8px 24px #0001}}table{{border-collapse:collapse;width:100%}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left}}.ok{{color:#047857;font-weight:700}}.bad{{color:#b91c1c;font-weight:700}}code{{background:#eef2ff;border-radius:4px;padding:2px 5px}}</style></head><body>
<section class='panel'><h1>Phase55 确认 Bug 数据飞轮</h1><p>候选缺陷 → 人工确认/误报/重复 → 独立审批 → 优先级学习与回归固化。学习只使用脱敏元数据，且仅已批准的确认缺陷生效。</p><p>账本完整性：<span class='{ 'ok' if ledger.get('passed') else 'bad' }'>{html.escape(str(ledger.get('passed')))}</span> · 事件数：{html.escape(str(ledger.get('event_count')))}</p></section>
<section class='panel'><h2>汇总</h2><table>{rows}</table></section>
<section class='panel'><h2>已批准学习模式</h2><table><thead><tr><th>风险</th><th>Oracle</th><th>路径</th><th>确认次数</th><th>优先级加分</th></tr></thead><tbody>{patterns}</tbody></table></section>
<section class='panel'><h2>审批队列</h2><table><thead><tr><th>Promotion</th><th>类型</th><th>状态</th><th>评审人</th><th>审批人</th></tr></thead><tbody>{promotions}</tbody></table></section>
<section class='panel'><h2>治理边界</h2><ul><li>确认与学习审批必须双人完成。</li><li>误报不允许压低整类风险；只能创建精确、可过期的例外范围。</li><li>写路径仅进入隔离沙箱回归候选。</li><li>账本与学习资产不保留原始请求、响应、令牌、业务行或评审文本。</li></ul></section>
</body></html>"""


def build_confirmed_bug_flywheel(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = _paths(project, root)
    profile = _build_materialized(project, root)
    profile["private_leak_check"] = _private_leak_check(profile)
    paths["out"].mkdir(parents=True, exist_ok=True)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["profile"], profile)
    _write_json(paths["out"] / "confirmed_bug_flywheel_profile.json", profile)
    _write_json(paths["registry"], {"items": profile.get("registry") or []})
    _write_json(paths["promotion_manifest"], {"items": profile.get("promotions") or []})
    _write_json(paths["exceptions"], {"items": profile.get("exceptions") or []})
    _write_json(paths["regression_candidates"], {"items": profile.get("regression_candidates") or []})
    _write_json(paths["out"] / "confirmed_bug_registry.json", {"items": profile.get("registry") or []})
    _write_json(paths["out"] / "confirmed_bug_promotion_manifest.json", {"items": profile.get("promotions") or []})
    _write_json(paths["out"] / "confirmed_bug_exception_registry.json", {"items": profile.get("exceptions") or []})
    _write_json(paths["out"] / "confirmed_bug_regression_candidates.json", {"items": profile.get("regression_candidates") or []})
    _write_jsonl(paths["feedback_projection"], profile.get("feedback_projection") or [])
    _write_jsonl(paths["out"] / "confirmed_bug_feedback_projection.jsonl", profile.get("feedback_projection") or [])
    (paths["out"] / "confirmed_bug_flywheel_report.html").write_text(_render_report(profile), encoding="utf-8")

    # --- Pattern Memory learning (Phase61 moat upgrade) ---
    try:
        memory = BugPatternMemory()
        for item in (profile.get("registry") or []):
            if isinstance(item, dict):
                finding = item.get("finding") or item
                if isinstance(finding, dict) and finding.get("title"):
                    memory.add(finding)
        for item in (profile.get("promotions") or []):
            if isinstance(item, dict) and item.get("status") == "approved":
                finding = item.get("finding") or {}
                if isinstance(finding, dict) and finding.get("title"):
                    memory.add(finding)
        signals = memory.extract_detection_signals(min_frequency=2)
        stats = memory.stats()
        llm_insights = None
        recent = (profile.get("registry") or [])[-3:]
        for item in recent:
            finding = (item.get("finding") or item) if isinstance(item, dict) else {}
            if isinstance(finding, dict) and finding.get("title"):
                llm_insights = llm_enhanced_learn(finding, memory)
                if llm_insights:
                    break
        learning_manifest = {
            "phase": "phase61_bug_pattern_memory_v1",
            "generated_at_utc": _now(),
            "memory_stats": stats,
            "extracted_signals": signals,
            "llm_insights": llm_insights,
        }
        _write_json(paths["out"] / "confirmed_bug_learning_manifest.json", learning_manifest)
        profile["pattern_memory"] = learning_manifest

        # ── Learning Generator: produce NEW probes/oracles/fixtures ──
        try:
            from .learning_generator import LearningGenerator

            confirmed_findings = []
            for item in (profile.get("registry") or []):
                finding = (item.get("finding") or item) if isinstance(item, dict) else {}
                if isinstance(finding, dict) and finding.get("title"):
                    finding.setdefault("verdict", "confirmed")
                    confirmed_findings.append(finding)
            for item in (profile.get("promotions") or []):
                if isinstance(item, dict) and item.get("status") == "approved":
                    finding = item.get("finding") or {}
                    if isinstance(finding, dict) and finding.get("title"):
                        finding.setdefault("verdict", "confirmed")
                        confirmed_findings.append(finding)

            if confirmed_findings:
                lg = LearningGenerator(project_context={})
                manifest = lg.generate_from_confirmed_bugs(confirmed_findings)
                profile["generated_artifacts"] = lg.manifest_to_dict(manifest)
                # Persist manifest so generated artifacts survive across runs
                try:
                    lg.persist_manifest(manifest, paths["out"].parent)
                except Exception:
                    pass
            else:
                profile["generated_artifacts"] = {"status": "no_confirmed_findings"}
        except Exception as e:
            import sys
            print(f"[confirmed_bug_flywheel] LearningGenerator failed: {e}", file=sys.stderr)
            profile["generated_artifacts"] = {"status": "unavailable", "error": str(e)}

    except Exception:
        profile["pattern_memory"] = {"status": "unavailable"}

    return profile


def load_confirmed_bug_flywheel_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _read_json(_paths(project, root)["profile"], {})
    return data if isinstance(data, dict) and data else None


def _probe_match(probe: dict[str, Any], pattern: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    risk = _norm(probe.get("risk_type"))
    source = _norm(probe.get("source"))
    path = _path_template(probe.get("path") or "/")
    family = _norm(
        probe.get("oracle_family")
        or probe.get("reasoning_type")
        or probe.get("business_invariant_type")
        or probe.get("business_reconciliation_type")
        or probe.get("business_outcome_type")
        or probe.get("source")
    )
    prisk = _norm(pattern.get("risk_type"))
    pfamily = _norm(pattern.get("oracle_family"))
    ppath = _path_template(pattern.get("path_template") or "/")
    psource = _norm(pattern.get("source"))
    risk_match = bool(risk and risk == prisk)
    family_match = bool(family and pfamily and (family == pfamily or family in pfamily or pfamily in family or source == pfamily))
    source_match = bool(source and psource and source == psource)
    path_match = bool(path == ppath and path != "/")
    if risk_match:
        reasons.append("confirmed_risk_type")
    if family_match:
        reasons.append("confirmed_oracle_family")
    if source_match:
        reasons.append("confirmed_source")
    if path_match:
        reasons.append("confirmed_endpoint")
    # Exact endpoint+engine origin, endpoint+one semantic dimension, or two
    # semantic dimensions is safe enough to reprioritize without broad boosts.
    return (path_match and (risk_match or family_match or source_match)) or (risk_match and family_match), reasons


def _exception_match(probe: dict[str, Any], exception: dict[str, Any]) -> bool:
    scope = exception.get("scope") or {}
    if not isinstance(scope, dict) or _is_expired(scope.get("expires_at_utc")):
        return False
    return (
        _norm(probe.get("risk_type")) == _norm(scope.get("risk_type"))
        and str(probe.get("method") or "GET").upper() == str(scope.get("method") or "").upper()
        and _path_template(probe.get("path") or "/") == _path_template(scope.get("path_template") or "/")
        and (not scope.get("source") or _norm(probe.get("source")) == _norm(scope.get("source")))
        and (not scope.get("contract_id") or str(probe.get("contract_id") or "") == str(scope.get("contract_id")))
    )


def annotate_probes_with_confirmed_learning(
    probes: list[dict[str, Any]],
    project_id: str = "real_project_demo",
    root: Path | None = None,
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach approval-gated learning metadata without changing execution safety."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    profile = profile or load_confirmed_bug_flywheel_profile(project, root) or build_confirmed_bug_flywheel(project, root)
    patterns = [item for item in (profile.get("patterns") or []) if isinstance(item, dict)]
    exceptions = [item for item in (profile.get("exceptions") or []) if isinstance(item, dict)]
    out: list[dict[str, Any]] = []
    for raw in probes:
        probe = dict(raw)
        matches: list[dict[str, Any]] = []
        bonus = 0.0
        for pattern in patterns:
            matched, reasons = _probe_match(probe, pattern)
            if not matched:
                continue
            current = float(pattern.get("learning_bonus") or 0.0)
            bonus = max(bonus, current)
            matches.append({
                "pattern_id": pattern.get("pattern_id"),
                "risk_type": pattern.get("risk_type"),
                "oracle_family": pattern.get("oracle_family"),
                "confirmed_count": pattern.get("confirmed_count"),
                "bonus": round(current, 3),
                "match_reasons": reasons,
            })
        exact_exceptions = [item.get("exception_id") for item in exceptions if _exception_match(probe, item)]
        exception_penalty = 0.10 if exact_exceptions else 0.0
        existing = max(0.0, float(probe.get("learning_bonus") or 0.0))
        effective = max(existing, bonus)
        effective = max(0.0, round(effective - exception_penalty, 3))
        probe["learning_bonus"] = effective
        probe["confirmed_bug_flywheel_bonus"] = round(bonus, 3)
        probe["confirmed_bug_flywheel_matches"] = matches[:5]
        probe["confirmed_bug_flywheel_exception_ids"] = exact_exceptions[:5]
        if matches:
            reasons = list(probe.get("priority_reasons") or [])
            reasons.append(f"Phase55 已批准确认缺陷模式加分 {effective:.2f}")
            probe["priority_reasons"] = reasons[:10]
        if exact_exceptions:
            probe["needs_human_review"] = True
            probe["exception_scope_applied"] = "priority_only_exact_scope"
        out.append(probe)
    return out


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Phase55 confirmed Bug learning flywheel")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--record", default="", help="Path to a JSON document with candidate and review fields")
    parser.add_argument("--approve", default="")
    parser.add_argument("--approver", default="")
    parser.add_argument("--role", default="quality_owner")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = Path(args.root) if args.root else ROOT
    if args.record:
        payload = _read_json(Path(args.record), {})
        result = record_bug_review(args.project, payload.get("candidate") or {}, payload.get("review") or {}, root)
    elif args.approve:
        result = approve_learning_promotion(args.project, args.approve, args.approver, args.role, root)
    elif args.verify:
        result = verify_decision_ledger(args.project, root)
    else:
        result = build_confirmed_bug_flywheel(args.project, root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
