"""Finding classification, dedupe, and external evidence adjudication.

Extracted from ``__main__``. Symbols are re-exported for compatibility.
"""
from __future__ import annotations

import json
import re as _re
from typing import Any

from .customer_delivery_gate import (
    customer_delivery_rejection_reasons,
    is_customer_deliverable_defect,
)
from .enterprise_campaign import has_real_confirmation_receipt
from .product_scan_mainline import _as_dict, _first_text

def _classify_findings(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    confirmed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if has_real_confirmation_receipt(row) and is_customer_deliverable_defect(row):
            row["confirmation_status"] = "confirmed"
            confirmed.append(row)
        else:
            reasons = customer_delivery_rejection_reasons(row)
            row["upstream_gate_passed"] = bool(row.get("gate_passed"))
            row["gate_passed"] = False
            row["customer_delivery_status"] = "candidate"
            row["customer_delivery_gate_reasons"] = reasons
            row.setdefault("execution_status", "not_executed")
            row["confirmation_status"] = str(row.get("confirmation_status") or "candidate")
            candidates.append(row)
    return confirmed, candidates


# ── http_status_class quality filter ──
# Security-sensitive path patterns (general, industry-neutral)
_SECURITY_SENSITIVE_PATTERNS = _re.compile(
    r"/(auth|login|register|password|token|session|admin|permission|role|acl)\b",
    _re.IGNORECASE,
)


def _is_security_sensitive_path(path: str) -> bool:
    """Check if a path is security-sensitive (auth/admin/permission related)."""
    return bool(_SECURITY_SENSITIVE_PATTERNS.search(path or ""))


def _filter_http_status_class_quality(
    confirmed: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Demote low-quality http_status_class findings to candidates.

    http_status_class assertions without control evidence are prone to false
    positives: a 4xx response may be expected behavior (unauthorized access
    correctly rejected) rather than a bug. This filter keeps only findings
    that are likely real bugs:
    - 5xx responses (server errors are always bugs)
    - 4xx on security-sensitive paths (potential auth bugs)
    - Findings with control evidence (control/treatment comparison)
    - Admin role failures (privilege escalation indicators)
    """
    filtered_confirmed: list[dict[str, Any]] = []
    demoted: list[dict[str, Any]] = []

    for row in confirmed:
        category = str(row.get("category") or "").strip().lower()
        if category != "http_status_class":
            filtered_confirmed.append(row)
            continue

        # Check for control evidence
        raw = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else {}
        control_actor = str(raw.get("control_actor") or "").strip()
        observations = raw.get("observations") if isinstance(raw.get("observations"), dict) else {}
        control_succeeded = observations.get("control_succeeded")

        has_control = bool(control_actor) or control_succeeded is True
        if has_control:
            # Has control evidence - keep as confirmed
            filtered_confirmed.append(row)
            continue

        # No control evidence - check response status, path, and role
        response_raw = raw.get("response_raw") if isinstance(raw.get("response_raw"), dict) else {}
        status_code = int(response_raw.get("status_code") or 0)
        request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
        path = str(request_raw.get("path") or "")
        actor = str(request_raw.get("actor") or "").strip().lower()

        # 5xx is always a bug
        if 500 <= status_code < 600:
            filtered_confirmed.append(row)
            continue

        # 4xx on security-sensitive path is likely a bug
        if 400 <= status_code < 500 and _is_security_sensitive_path(path):
            filtered_confirmed.append(row)
            continue

        # Admin role failures are likely privilege escalation bugs
        if actor == "admin":
            filtered_confirmed.append(row)
            continue

        # 4xx on non-security path by non-admin without control - likely expected behavior
        # Demote to candidate
        row = dict(row)
        row["gate_passed"] = False
        row["customer_delivery_status"] = "candidate"
        row["confirmation_status"] = "candidate"
        row["_fp_filter_reason"] = "http_status_class_4xx_no_control_non_admin_non_security"
        demoted.append(row)

    return filtered_confirmed, candidates + demoted


def _dedupe_findings(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collapse near-identical findings that share the same reproduction path.

    A state-graph cross-product can stamp one probe (e.g. a duplicate-payment
    call) onto many lifecycle-state labels, inflating one real defect into N
    "P0" rows with byte-identical reproduction steps. This groups by
    (oracle rule + id-normalized reproduction fingerprint + primary target) and
    keeps a single representative, recording the collapsed lifecycle-state
    variants as coverage on the survivor so nothing is silently dropped.
    """
    import re as _re

    def _norm(text: str) -> str:
        # Neutralize concrete ids (uuid / long hex / digits) so the same probe
        # against different entity instances collapses to one fingerprint.
        text = _re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}", "{id}", str(text or ""))
        text = _re.sub(r"\b[0-9a-fA-F]{16,}\b", "{id}", text)
        text = _re.sub(r"\b\d+\b", "{n}", text)
        return text.strip()

    def _protocol_body(value: Any) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = str(value or "")
        # Normalize only volatile identities. Numeric boundary/amount values are
        # business semantics and must remain distinct.
        text = _re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}", "{id}", text)
        text = _re.sub(r"\b[0-9a-fA-F]{16,}\b", "{id}", text)
        return text

    groups: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        oracle = item.get("oracle") if isinstance(item.get("oracle"), dict) else {}
        rule = str(oracle.get("violated_rule") or oracle.get("oracle_name") or item.get("category") or "").strip().lower()
        ev = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        steps = ev.get("reproduction_steps") if isinstance(ev.get("reproduction_steps"), list) else []
        fingerprint = tuple(_norm(s) for s in steps)
        primary = _norm(str(ev.get("request") or ""))
        protocol_rules = {"server_5xx", "expected_status_mismatch", "wrong_create_status", "200_with_error"}
        if rule in protocol_rules:
            raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
            request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
            response_raw = raw.get("response_raw") if isinstance(raw.get("response_raw"), dict) else {}
            key = (
                "protocol_runtime",
                rule,
                str(request_raw.get("method") or "").upper(),
                _norm(str(request_raw.get("path") or "")),
                int(response_raw.get("status_code") or 0),
                str(request_raw.get("actor") or ""),
                _norm(str(oracle.get("expected") or item.get("expected") or "")),
                _protocol_body(request_raw.get("body")) if "body" in request_raw else "",
            )
        else:
            key = (rule, primary, fingerprint)
        variant = {
            "title": item.get("title"),
            "behavior_slice_id": item.get("behavior_slice_id"),
            "oracle_state": (oracle.get("expected") or item.get("expected") or ""),
        }
        if key not in groups:
            keep = dict(item)
            keep["_coverage_variants"] = [variant]
            keep["_duplicate_count"] = 1
            groups[key] = keep
            order.append(key)
        else:
            groups[key]["_coverage_variants"].append(variant)
            groups[key]["_duplicate_count"] += 1

    deduped = [groups[k] for k in order]
    _total = len([i for i in items if isinstance(i, dict)])
    report = {
        "input_count": _total,
        "unique_count": len(deduped),
        "collapsed_count": _total - len(deduped),
        "groups": [
            {
                "title": groups[k].get("title"),
                "duplicate_count": groups[k].get("_duplicate_count", 1),
                "variant_states": [v.get("oracle_state") for v in groups[k].get("_coverage_variants", [])],
            }
            for k in order
        ],
    }
    return deduped, report


def _has_verified_db_evidence(finding: dict[str, Any]) -> bool:
    db_evidence = finding.get("db_evidence") if isinstance(finding.get("db_evidence"), dict) else {}
    return bool(
        db_evidence
        and (db_evidence.get("before_db_snapshot") or db_evidence.get("before"))
        and (db_evidence.get("after_db_snapshot") or db_evidence.get("after"))
        and (db_evidence.get("db_assertion") or db_evidence.get("assertion"))
        and (db_evidence.get("business_operation") or db_evidence.get("operation"))
    )


def _is_external_signal_finding(finding: dict[str, Any]) -> bool:
    source = str(finding.get("source") or "").strip().lower()
    return source.startswith("external_signal:") or bool(str(finding.get("external_signal_provider") or "").strip())


def _snapshot_entry_from_external(value: Any, *, fallback_method: str, fallback_path: str, fallback_kind: str) -> dict[str, Any]:
    item = _as_dict(value)
    method = str(item.get("method") or fallback_method or "").upper().strip()
    path = str(item.get("path") or fallback_path or "").strip()
    status_code = item.get("status_code")
    response: dict[str, Any] = {}
    if isinstance(status_code, int):
        response["status_code"] = status_code
    elif str(status_code or "").isdigit():
        response["status_code"] = int(status_code)
    if "body" in item:
        response["body"] = item.get("body")
    return {
        "observer_kind": str(item.get("observer_kind") or fallback_kind or "external_runtime_projection"),
        "evidence_goal": str(item.get("evidence_goal") or "before_after_snapshot"),
        "method": method,
        "path": path,
        "response": response,
    }


def _external_finding_snapshots(finding: dict[str, Any], *, method: str, path: str) -> dict[str, list[dict[str, Any]]]:
    before_after = _as_dict(finding.get("before_after_snapshot"))
    before = _as_dict(before_after.get("before"))
    after = _as_dict(before_after.get("after"))
    if before or after:
        return {
            "before": [_snapshot_entry_from_external(before, fallback_method=method, fallback_path=path, fallback_kind="external_runtime_before")] if before else [],
            "after": [_snapshot_entry_from_external(after, fallback_method=method, fallback_path=path, fallback_kind="external_runtime_after")] if after else [],
        }
    db_evidence = _as_dict(finding.get("db_evidence"))
    db_before = db_evidence.get("before_db_snapshot") if isinstance(db_evidence.get("before_db_snapshot"), dict) else {}
    db_after = db_evidence.get("after_db_snapshot") if isinstance(db_evidence.get("after_db_snapshot"), dict) else {}
    table = str(db_evidence.get("table") or "").strip()
    operation = str(db_evidence.get("business_operation") or "").strip()
    before_row = {
        "observer_kind": "database_projection",
        "evidence_goal": "db_before_snapshot",
        "method": method,
        "path": path,
        "table": table,
        "business_operation": operation,
        "payload": db_before,
        "response": {},
    } if db_before else {}
    after_row = {
        "observer_kind": "database_projection",
        "evidence_goal": "db_after_snapshot",
        "method": method,
        "path": path,
        "table": table,
        "business_operation": operation,
        "payload": db_after,
        "response": {},
    } if db_after else {}
    return {
        "before": [before_row] if before_row else [],
        "after": [after_row] if after_row else [],
    }


def _external_finding_runtime_observation(finding: dict[str, Any]) -> dict[str, Any]:
    runtime_replay = _as_dict(finding.get("runtime_replay"))
    raw_evidence = _as_dict(finding.get("raw_evidence"))
    request_raw = _as_dict(raw_evidence.get("request_raw"))
    response_raw = _as_dict(raw_evidence.get("response_raw"))
    har_evidence = _as_dict(finding.get("har_evidence"))
    invariant_eval = _as_dict(finding.get("business_invariant_evaluation"))
    evidence_quality = _as_dict(finding.get("evidence_quality"))
    method = str(
        finding.get("method")
        or finding.get("_api_method")
        or runtime_replay.get("method")
        or request_raw.get("method")
        or har_evidence.get("method")
        or ""
    ).upper().strip()
    path = str(
        finding.get("path")
        or finding.get("_api_path")
        or runtime_replay.get("path")
        or request_raw.get("path")
        or har_evidence.get("path")
        or ""
    ).strip()
    response_status = runtime_replay.get("http_status")
    if response_status is None and response_raw.get("status_code") is not None:
        response_status = response_raw.get("status_code")
    if response_status is None and har_evidence.get("status_code") is not None:
        response_status = har_evidence.get("status_code")
    response: dict[str, Any] = {}
    if response_status is not None:
        try:
            response["status_code"] = int(response_status)
        except Exception:
            pass
    if response_raw.get("body") is not None:
        response["body"] = response_raw.get("body")
    elif har_evidence.get("response_body") is not None:
        response["body"] = har_evidence.get("response_body")
    if response_raw.get("duration_ms") is not None:
        response["duration_ms"] = response_raw.get("duration_ms")
    elif runtime_replay.get("duration_ms") is not None:
        response["duration_ms"] = runtime_replay.get("duration_ms")
    verification = {
        "verdict": str(finding.get("confirmation_status") or "candidate"),
        "reason": str(
            finding.get("actual")
            or finding.get("actual_behavior")
            or invariant_eval.get("reason")
            or finding.get("description")
            or ""
        ).strip(),
        "confidence": round(min(max(float(evidence_quality.get("score") or finding.get("confidence_score") or 0.88) / 100.0, 0.0), 0.99), 2),
        "replay_ids": [str(item) for item in [finding.get("risk_id"), finding.get("finding_id"), finding.get("candidate_id")] if str(item or "").strip()],
        "payload_summary": str(response.get("body") or "")[:200],
        "negative_values": [],
        "db_evidence": _as_dict(finding.get("db_evidence")),
        "business_invariant_evaluation": invariant_eval,
    }
    return {
        "candidate_id": str(finding.get("risk_id") or finding.get("finding_id") or finding.get("candidate_id") or "").strip(),
        "risk_type": str(finding.get("category") or "external_signal_violation").strip(),
        "method": method,
        "path": path,
        "request": {
            "method": method,
            "path": path,
            "body": request_raw.get("body", finding.get("request_body")),
        },
        "response": response,
        "responses": [response] if response else [],
        "snapshots": _external_finding_snapshots(finding, method=method, path=path),
        "verification": verification,
        "source_refs": [str(item) for item in [finding.get("source"), _as_dict(finding.get("evidence")).get("junit_report"), _as_dict(finding.get("evidence")).get("trace_id")] if str(item or "").strip()],
        "grounding_basis": {
            "engine": "external_signal_bridge",
            "rule": _as_dict(finding.get("external_evidence_adjudication")).get("rule"),
            "source": str(finding.get("source") or "").strip(),
        },
    }


def _attach_external_evidence_packages(items: Any) -> list[dict[str, Any]]:
    try:
        from .runtime_finding_evidence_packager import package_runtime_finding_evidence
    except Exception:
        return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    packaged: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if (
            _is_external_signal_finding(row)
            and str(row.get("confirmation_status") or "").strip().lower() == "validated_candidate"
            and str(_as_dict(row.get("evidence_package")).get("engine") or "").strip() != "runtime_finding_evidence_packager_v1_phase92t"
        ):
            obs = _external_finding_runtime_observation(row)
            evidence_package = package_runtime_finding_evidence(obs, source=str(row.get("source") or "external_signal"))
            row["evidence_package"] = evidence_package
            row["evidence_strength_score"] = evidence_package.get("evidence_strength_score")
            row["evidence_grade"] = evidence_package.get("evidence_grade")
            row["violated_invariants"] = evidence_package.get("violated_invariants") or []
            row["delta_summary"] = evidence_package.get("delta_summary") or {}
        packaged.append(row)
    return packaged


def _adjudicate_external_evidence_backed_candidates(items: Any) -> list[dict[str, Any]]:
    adjudicated: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if not _is_external_signal_finding(row):
            adjudicated.append(row)
            continue
        runtime_replay = row.get("runtime_replay") if isinstance(row.get("runtime_replay"), dict) else {}
        invariant_eval = row.get("business_invariant_evaluation") if isinstance(row.get("business_invariant_evaluation"), dict) else {}
        has_runtime_replay = str(runtime_replay.get("status") or "").strip().lower() == "executed"
        has_db_evidence = _has_verified_db_evidence(row)
        has_failed_invariant = str(invariant_eval.get("verdict") or "").strip().lower() == "failed"
        passes = has_runtime_replay and has_db_evidence and has_failed_invariant
        row["external_evidence_adjudication"] = {
            "status": "validated_candidate" if passes else "candidate",
            "has_runtime_replay": has_runtime_replay,
            "has_db_evidence": has_db_evidence,
            "has_failed_invariant": has_failed_invariant,
            "rule": "external_runtime_replay_and_db_evidence_and_failed_invariant",
        }
        if passes:
            row["confirmation_status"] = "validated_candidate"
            row["execution_status"] = str(row.get("execution_status") or "executed")
            row["evidence_strength"] = str(row.get("evidence_strength") or "runtime_and_db")
            row["bug_status"] = "reproduced"
            row["gate_passed"] = True
            row["quality_assurance_gap"] = False
            row["customer_delivery_status"] = str(row.get("customer_delivery_status") or "defect")
            row["semantic_verdict"] = str(row.get("semantic_verdict") or "SEMANTIC_CONFIRMED")
            row["business_evidence_status"] = str(row.get("business_evidence_status") or "VALIDATED")
            row["final_review_status"] = str(row.get("final_review_status") or "VALIDATED_CANDIDATE")
            evidence_status = _as_dict(row.get("evidence_status"))
            evidence_status.update({
                "semantic_verdict": row["semantic_verdict"],
                "business_evidence_status": row["business_evidence_status"],
                "final_review_status": row["final_review_status"],
                "missing_requirements": [str(item) for item in evidence_status.get("missing_requirements") or [] if str(item)],
            })
            row["evidence_status"] = evidence_status
            runtime_trace = _as_dict(runtime_replay.get("trace"))
            runtime_steps = runtime_trace.get("steps") if isinstance(runtime_trace.get("steps"), list) else []
            first_step = runtime_steps[0] if runtime_steps and isinstance(runtime_steps[0], dict) else {}
            runtime_response = _as_dict(first_step.get("response"))
            method = str(row.get("method") or runtime_replay.get("method") or row.get("_api_method") or "").upper().strip()
            path = str(row.get("path") or runtime_replay.get("path") or row.get("_api_path") or "").strip()
            if method:
                row["_api_method"] = method
            if path:
                row["_api_path"] = path
            invariant_results = invariant_eval.get("results") if isinstance(invariant_eval.get("results"), list) else []
            first_failed = next((item for item in invariant_results if isinstance(item, dict) and str(item.get("verdict") or "").lower() == "failed"), {})
            expected = str(
                row.get("expected_behavior")
                or row.get("expected")
                or first_failed.get("expected")
                or first_failed.get("name")
                or "业务不变量应保持成立"
            ).strip()
            actual = str(
                row.get("actual_behavior")
                or row.get("actual")
                or first_failed.get("actual")
                or first_failed.get("reason")
                or invariant_eval.get("reason")
                or f"运行时回放返回 HTTP {runtime_replay.get('http_status')}"
            ).strip()
            row["expected_behavior"] = expected
            row["expected"] = expected
            row["actual_behavior"] = actual
            row["actual"] = actual
            evidence = _as_dict(row.get("evidence"))
            evidence.update({
                "method": method,
                "path": path,
                "target": evidence.get("target") or f"{method} {path}".strip(),
                "expected": expected,
                "actual": actual,
                "trace_id": evidence.get("trace_id") or _as_dict(row.get("trace")).get("trace_id") or _as_dict(runtime_trace).get("trace_id") or "",
            })
            failed_reason = str(first_failed.get("reason") or invariant_eval.get("reason") or row.get("description") or "").strip()
            if failed_reason:
                evidence["assertion"] = evidence.get("assertion") or failed_reason
            row["evidence"] = evidence
            failed_fields = [str(item) for item in row.get("failed_fields") or [] if str(item)]
            if not failed_fields and isinstance(first_failed, dict):
                failed_fields = [str(item) for item in first_failed.get("failed_fields") or [] if str(item)]
            row["failed_fields"] = failed_fields
            if not isinstance(row.get("failed_assertions"), list) or not row.get("failed_assertions"):
                row["failed_assertions"] = [{
                    "type": "business_invariant_violation",
                    "rule": failed_reason or expected,
                    "expected": expected,
                    "actual": actual,
                    "failed_fields": failed_fields,
                }]
            raw_evidence = _as_dict(row.get("raw_evidence"))
            request_raw = _as_dict(raw_evidence.get("request_raw"))
            if method:
                request_raw["method"] = method
            if path:
                request_raw["path"] = path
            response_raw = _as_dict(raw_evidence.get("response_raw"))
            if runtime_replay.get("http_status") is not None:
                response_raw["status_code"] = runtime_replay.get("http_status")
            if runtime_response.get("body") is not None:
                response_raw["body"] = runtime_response.get("body")
            if runtime_replay.get("duration_ms") is not None:
                response_raw["duration_ms"] = runtime_replay.get("duration_ms")
            raw_evidence["request_raw"] = request_raw
            raw_evidence["response_raw"] = response_raw
            raw_evidence["has_real_evidence"] = True
            raw_evidence["timestamp"] = str(raw_evidence.get("timestamp") or row.get("timestamp") or row.get("last_verified_at") or "")
            row["raw_evidence"] = raw_evidence
            reproduction = _as_dict(row.get("reproduction"))
            reproduction.update({
                "method": method,
                "path": path,
                "is_synthetic": False,
                "har_evidence": {
                    "method": method,
                    "path": path,
                    "status_code": runtime_replay.get("http_status"),
                    "response_body": runtime_response.get("body"),
                    "duration_ms": runtime_replay.get("duration_ms"),
                },
            })
            row["reproduction"] = reproduction
            row["har_evidence"] = dict(reproduction.get("har_evidence") or {})
            row["timestamp"] = str(row.get("timestamp") or row.get("last_verified_at") or raw_evidence.get("timestamp") or "")
            row["last_verified_at"] = str(row.get("last_verified_at") or row.get("timestamp") or raw_evidence.get("timestamp") or "")
            if not isinstance(row.get("reproduction_steps"), list) or not row.get("reproduction_steps"):
                step_summary = f"{method} {path}".strip() if method or path else "runtime replay"
                status_text = f"HTTP {runtime_replay.get('http_status')}" if runtime_replay.get("http_status") is not None else "已执行"
                row["reproduction_steps"] = [f"{step_summary} -> {status_text}"]
            row.setdefault("evidence_quality", {})
            if isinstance(row.get("evidence_quality"), dict):
                quality = dict(row["evidence_quality"])
                quality["level"] = "validated"
                quality["score"] = max(int(quality.get("score") or 0), 88)
                quality["can_reproduce"] = True
                verified = [str(item) for item in quality.get("verified") or [] if str(item)]
                verified.extend([
                    "存在运行时回放证据",
                    "存在 DB 前后快照与断言",
                    "存在业务不变量失败结果",
                ])
                quality["verified"] = list(dict.fromkeys(verified))[:10]
                row["evidence_quality"] = quality
        adjudicated.append(row)
    return adjudicated


