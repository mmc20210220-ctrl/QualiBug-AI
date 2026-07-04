"""Mandatory evidence/adversarial gate for findings emitted by DiscoveryEngine.

PHASE92A: Split into two gate layers:
1. Runtime Evidence Gate — checks traceability of runtime probes
2. Business Evidence Gate — checks business contract completeness

CRITICAL RULES:
- Stage_verify's semantic verdict MUST be preserved
- Runtime Gate pass + Business Gate fail → NEEDS_MORE_EVIDENCE (not REJECTED)
- Runtime Gate fail → BLOCKED_BY_RUNTIME_EVIDENCE (not REJECTED)
- Business Gate pass → VALIDATED_CANDIDATE
- Adversarial/Schema/Human Review remain unbypassable
- CANDIDATE → CONFIRMED is FORBIDDEN without human review
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from .evidence_models import (
    SourcedValue,
    RawProbeEvidence,
    NormalizedRuntimeEvidence,
    SemanticVerificationEvidence,
    BusinessEvidenceDraft,
    BusinessFindingContract,
    MISSING_REQUIREMENTS,
    SEMANTIC_VERDICTS,
    BUSINESS_EVIDENCE_STATUS,
    FINAL_REVIEW_STATUS,
    RUNTIME_GATE_STATUS,
    BUSINESS_GATE_STATUS,
)


GATED_VERDICTS = {
    "VALIDATED_CANDIDATE": "validated_candidate",
    "REJECTED": "rejected",
    "NEEDS_MORE_EVIDENCE": "needs_more_evidence",
    "SCHEMA_INVALID": "schema_invalid",
    "BLOCKED_BY_SAFETY": "blocked_by_safety",
    "BLOCKED_BY_FIXTURE": "blocked_by_fixture",
    "BLOCKED_BY_BINDING": "blocked_by_binding",
    "BLOCKED_BY_OBSERVER": "blocked_by_observer",
    "BLOCKED_BY_CLEANUP": "blocked_by_cleanup",
    "BLOCKED_BY_RUNTIME_EVIDENCE": "blocked_by_runtime_evidence",
}

# Phase92A: Mapping from raw verdict to semantic namespace
VERDICT_TO_SEMANTIC = {
    "confirmed": "SEMANTIC_CONFIRMED",
    "falsified": "SEMANTIC_FALSIFIED",
    "inconclusive": "SEMANTIC_INCONCLUSIVE",
    "execution_error": "SEMANTIC_ERROR",
    "needs_more_evidence": "SEMANTIC_PENDING",
}


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _safe_kind(value: Any) -> str:
    raw = _text(value, 80).lower()
    if raw in {"state", "transition", "conservation", "temporal", "permission", "schema", "lifecycle"}:
        return raw
    if any(token in raw for token in ("auth", "permission", "role", "权限", "认证")):
        return "permission"
    if any(token in raw for token in ("state", "status", "transition", "状态", "流转")):
        return "transition"
    if any(token in raw for token in ("sum", "balance", "amount", "conservation", "金额", "对账")):
        return "conservation"
    return "other"


def _finding_fields(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    # Phase92A: Include four-layer state attributes
    result = {
        "hypothesis_id": getattr(item, "hypothesis_id", ""),
        "title": getattr(item, "title", ""),
        "severity": getattr(item, "severity", "P2"),
        "verdict": getattr(item, "verdict", "inconclusive"),
        "expected": getattr(item, "expected", ""),
        "actual": getattr(item, "actual", ""),
        "evidence": getattr(item, "evidence", {}) or {},
        "confidence": getattr(item, "confidence", 0.0),
    }
    # Phase92A: Extract four-layer state from object attributes
    for attr in ("_raw_runtime_verdict", "_semantic_verdict", "_business_evidence_status",
                 "_final_review_status", "_compound_status", "_enrichment_trace"):
        val = getattr(item, attr, None)
        if val is not None:
            result[attr] = val
    return result




def _call_method_and_path(call_str: str) -> tuple[str, str]:
    parts = str(call_str or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0].upper(), (parts[1] if len(parts) > 1 else "")


def _body_data_preview(body: Any, limit: int = 240) -> tuple[str, list[str], bool]:
    """Return a redacted response preview, top-level keys and whether body has real data."""
    if not isinstance(body, dict):
        text = _text(body, limit)
        return text, [], bool(text.strip())
    keys = [str(k) for k in body.keys()][:12]
    data = body.get("data", body)
    has_data = False
    if isinstance(data, dict):
        has_data = any(v not in (None, "", [], {}) for v in data.values())
    elif isinstance(data, list):
        has_data = len(data) > 0
    else:
        has_data = data not in (None, "", [], {})
    preview = json.dumps({"keys": keys, "data_type": type(data).__name__, "data_size": (len(data) if hasattr(data, "__len__") else 1)}, ensure_ascii=False)
    return preview[:limit], keys, has_data


def _auth_boundary_read_context(evidence: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """Build a strict read-only authorization-boundary context.

    This is not a verifier relaxation.  It recognizes a different evidence
    shape: for a GET authorization finding, one anonymous request returning
    HTTP 200 with non-empty business data is the action evidence.  Before/after
    mutation snapshots are not applicable because the probe is read-only.
    """
    title_expected = f"{row.get('title','')} {row.get('expected','')} {row.get('actual','')}".lower()
    if not any(k in title_expected for k in ("auth", "认证", "permission", "权限", "anonymous", "匿名", "401", "403")):
        return None
    calls = evidence.get("calls") or []
    for c in calls:
        if not isinstance(c, dict):
            continue
        method, path = _call_method_and_path(c.get("call", ""))
        if method != "GET":
            continue
        results = c.get("results", {}) if isinstance(c.get("results"), dict) else {}
        no_auth = results.get("no_auth", {}) if isinstance(results.get("no_auth"), dict) else {}
        status = int(no_auth.get("status") or 0)
        if status != 200:
            continue
        body = no_auth.get("body", {})
        preview, keys, has_data = _body_data_preview(body)
        if not has_data:
            continue
        return {
            "method": method,
            "path": path,
            "status": status,
            "body_preview": preview,
            "body_keys": keys,
            "has_business_data": has_data,
            "actor_role": "anonymous",
        }
    return None



def _redacted_body_summary(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        data = body.get("data", body)
        return {
            "_redacted": True,
            "top_level_keys": [str(k) for k in list(body.keys())[:20]],
            "data_type": type(data).__name__,
            "data_size": len(data) if hasattr(data, "__len__") else (1 if data not in (None, "", [], {}) else 0),
        }
    if isinstance(body, list):
        return {"_redacted": True, "data_type": "list", "data_size": len(body)}
    return {"_redacted": True, "data_type": type(body).__name__, "has_value": bool(body)}


def _redact_calls_for_contract(calls: list[Any]) -> list[dict[str, Any]]:
    """Remove raw response values from customer-shareable contracts.

    Runtime execution may observe sensitive fields precisely so the product can
    detect data exposure.  The business finding, however, must only carry a
    redacted summary; raw credentials/passwords stay out of the ledger/report.
    """
    redacted: list[dict[str, Any]] = []
    for c in calls or []:
        if not isinstance(c, dict):
            continue
        item = {k: v for k, v in c.items() if k != "results"}
        results: dict[str, Any] = {}
        for role, r in (c.get("results") or {}).items():
            if not isinstance(r, dict):
                continue
            results[str(role)] = {
                "status": r.get("status", 0),
                "body": _redacted_body_summary(r.get("body", {})),
            }
        item["results"] = results
        redacted.append(item)
    return redacted

def _extract_from_calls(evidence: dict, row: dict) -> dict[str, Any]:
    """Extract business-contract fields from discovery-engine calls evidence."""
    calls = evidence.get("calls") or []
    result: dict[str, Any] = {}

    # Read-only auth-boundary findings have a different complete evidence shape:
    # the anonymous GET request is the action and response snapshot.  Do not
    # require mutation before/after snapshots for this case.
    auth_ctx = _auth_boundary_read_context(evidence, row)
    if auth_ctx:
        path = auth_ctx["path"]
        status = auth_ctx["status"]
        result["evidence_model"] = "read_only_auth_boundary"
        result["auth_boundary_matrix"] = {
            "anonymous": {"method": "GET", "path": path, "status": status, "expected": "401_or_403"},
            "expected_policy": "anonymous_requests_must_not_return_business_data",
        }
        result["response_sensitivity"] = {
            "has_business_data": True,
            "top_level_keys": auth_ctx.get("body_keys", []),
            "preview_ref": auth_ctx.get("body_preview", ""),
        }
        result["before_snapshot_ref"] = f"expected_access_boundary: anonymous GET {path} must return HTTP 401/403"
        result["action_evidence_ref"] = f"anonymous GET {path} -> HTTP {status}; response contains business data keys={auth_ctx.get('body_keys', [])}"
        result["after_snapshot_ref"] = f"observed_response_snapshot: {auth_ctx.get('body_preview', '')}"
        result["invariant_evidence_ref"] = "AUTH_BOUNDARY_DENY_ANONYMOUS_BUSINESS_DATA"
        result["actor_role"] = "anonymous"
        result["preconditions"] = [
            "Target route is documented as business data, not a public health/docs/login endpoint",
            "Probe is read-only GET and executed without credentials",
            "Expected anonymous outcome is HTTP 401 or 403",
        ]

    # Snapshots from multi-step calls
    if len(calls) >= 3 and not auth_ctx:
        before_call = calls[0]
        action_call = calls[1]
        after_call = calls[-1]
        before_body = before_call.get("results", {}).get("admin", {}).get("body", {})
        after_body = after_call.get("results", {}).get("admin", {}).get("body", {})
        action_status = action_call.get("results", {}).get("admin", {}).get("status", 0)
        action_path = action_call.get("call", "")

        if isinstance(before_body, dict) and before_body:
            result["before_snapshot_ref"] = json.dumps(before_body, ensure_ascii=False, default=str)[:1000]
        else:
            result["before_snapshot_ref"] = f"GET {before_call.get('call', '')} -> HTTP {before_call.get('results', {}).get('admin', {}).get('status', '?')}"
            result["before_snapshot"] = result["before_snapshot_ref"]
        if isinstance(after_body, dict) and after_body:
            result["after_snapshot_ref"] = json.dumps(after_body, ensure_ascii=False, default=str)[:1000]
        else:
            result["after_snapshot_ref"] = f"GET {after_call.get('call', '')} -> HTTP {after_call.get('results', {}).get('admin', {}).get('status', '?')}"
            result["after_snapshot"] = result["after_snapshot_ref"]
        result["action_evidence_ref"] = f"{action_path} -> HTTP {action_status}"

    # Entity binding from first call path
    if calls:
        first_call = calls[0].get("call", "")
        method, first_path = _call_method_and_path(first_call)
        path_parts = first_path.strip("/").split("/") if first_path else []
        if len(path_parts) >= 2:
            entity_type = path_parts[1].rstrip("s")
            entity_id = path_parts[-1] if len(path_parts) >= 3 and path_parts[-1] != path_parts[1] else ""
            if not entity_id and result.get("evidence_model") == "read_only_auth_boundary":
                entity_id = first_path or first_call
            result.setdefault("entity_binding", {
                "entity_alias": row.get("title", "")[:80],
                "entity_type": entity_type,
                "entity_id": entity_id,
                "tenant_id": "single-tenant",
                "binding_confidence": 0.8 if result.get("evidence_model") == "read_only_auth_boundary" else 0.6,
            })

    result.setdefault("observer_refs", [
        f"{c.get('call', '')} -> HTTP {c.get('results', {}).get('admin', {}).get('status', '?')}"
        for c in calls[:5]
    ])

    # Phase92A: Detect write operations and set cleanup status accordingly
    write_methods = {"POST", "PUT", "DELETE", "PATCH"}
    has_write_operation = False
    for c in calls:
        if isinstance(c, dict):
            call_str = c.get("call", "")
            if call_str:
                method = call_str.split()[0] if call_str.split() else ""
                if method in write_methods:
                    has_write_operation = True
                    break
    
    result.setdefault("cleanup", {
        "status": "PENDING" if has_write_operation else "NOT_APPLICABLE",
        "run_id": f"discovery-{hashlib.md5(json.dumps(calls[0].get('call','') if calls else '', default=str).encode()).hexdigest()[:8]}",
        "evidence_ref": "before_after_observer_pattern",
    })

    if calls:
        first_call = calls[0].get("call", "")
        result.setdefault("flow_id", f"discovery_probe::{first_call}")
        result.setdefault("entrypoint", {
            "flow_id": f"discovery_probe::{first_call}",
            "step_id": "stage_verify",
            "action_type": "api_probe",
            "actor_role": "admin",
        })
        required_inputs = [c.get("call", "") for c in calls[:3]]
        if result.get("evidence_model") == "read_only_auth_boundary":
            required_inputs = [f"NO_AUTH {calls[0].get('call', '')}"]
        result.setdefault("reproduction", {
            "flow_id": f"discovery_probe::{first_call}",
            "fixture_refs": [],
            "required_inputs": required_inputs,
            "expected_observation": row.get("expected", ""),
        })

    check = evidence.get("check_condition", "") or row.get("expected", "")
    if check:
        result.setdefault("invariant_evidence_ref", check[:200])

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Phase92A: Two-layer Gate
# ══════════════════════════════════════════════════════════════════════════════

class RuntimeEvidenceGate:
    """Phase92A §4.1: Runtime Evidence Gate.

    Checks that runtime probe evidence is real, traceable, and reproducible.
    PASS does NOT mean the finding is confirmed; it means the probe
    evidence is trustworthy enough to proceed to business gate.

    If this gate fails, the finding is BLOCKED_BY_RUNTIME_EVIDENCE,
    not REJECTED — the semantic verdict is still preserved.
    """

    @staticmethod
    def check(contract: dict[str, Any]) -> tuple[str, list[str]]:
        """Check runtime evidence traceability AND data authenticity.

        Returns (status, reasons) where status is from RUNTIME_GATE_STATUS.
        """
        reasons: list[str] = []
        evidence = contract.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}

        calls = evidence.get("calls") or []
        raw_evidence_refs = evidence.get("raw_evidence_refs") or []

        # ── Call chain must exist ──
        if not calls and not raw_evidence_refs:
            return "FAILED_MISSING_CALLS", ["No probe calls or raw evidence references found"]

        # ── Request/response traceable ──
        if calls:
            untraceable = 0
            for c in calls:
                admin = c.get("results", {}).get("admin", {})
                # "status" key absent → truly untraceable (0 is a valid status: connection failure)
                if "status" not in admin and "body" not in admin:
                    untraceable += 1
            if untraceable == len(calls):
                return "FAILED_UNTRACEABLE", [f"All {len(calls)} calls have no admin status/body"]

            # ── NEW: Synthetic/unresolved ID detection ──
            synthetic_count = 0
            for c in calls:
                call_path = c.get("path", "") or c.get("call", "")
                if "QUALIBUG_UNRESOLVED_ID" in call_path:
                    synthetic_count += 1
            if synthetic_count == len(calls):
                reasons.append(f"All {len(calls)} calls use synthetic IDs (QUALIBUG_UNRESOLVED_ID)")

            # ── NEW: HTTP status code validity ──
            all_404 = True
            all_0 = True
            total_checked = 0
            status_counts = {}
            for c in calls:
                for role in ("admin", "viewer", "no_auth"):
                    role_data = c.get("results", {}).get(role)
                    if not isinstance(role_data, dict):
                        continue  # skip non-existent roles
                    status = role_data.get("status", 0)
                    total_checked += 1
                    status_counts[status] = status_counts.get(status, 0) + 1
                    if status != 404:
                        all_404 = False
                    if status != 0:
                        all_0 = False
            if all_404 and total_checked > 0:
                reasons.append(f"All {total_checked} HTTP responses were 404 — target endpoints may not exist")
            if all_0 and total_checked > 0:
                reasons.append(f"All {total_checked} HTTP calls failed with status 0 — network or auth issue")

            # ── NEW: Response body structure validity ──
            empty_bodies = 0
            total_role_results = 0
            for c in calls:
                for role in ("admin", "viewer", "no_auth"):
                    role_data = c.get("results", {}).get(role)
                    if not isinstance(role_data, dict):
                        continue
                    total_role_results += 1
                    body = role_data.get("body", {})
                    if isinstance(body, dict) and len(body) == 0:
                        empty_bodies += 1
                    elif body is None:
                        empty_bodies += 1
            if total_role_results > 0 and empty_bodies == total_role_results:
                reasons.append(f"All {total_role_results} role results have empty response bodies")

            # ── NEW: Cross-verification — at least one call should carry a request snapshot ──
            has_request_snapshot = False
            for c in calls:
                for role in ("admin", "viewer", "no_auth"):
                    role_data = c.get("results", {}).get(role)
                    if not isinstance(role_data, dict):
                        continue
                    req = role_data.get("_request", {})
                    if isinstance(req, dict) and req.get("url"):
                        has_request_snapshot = True
                        break
                if has_request_snapshot:
                    break

            if not has_request_snapshot:
                reasons.append("No request snapshots (_request.url) — evidence not independently reproducible")
            else:
                # Count how many calls have snapshots for quality scoring
                snapshot_count = sum(1 for c in calls
                    if any(isinstance(c.get("results", {}).get(r, {}).get("_request", {}), dict)
                           and c["results"][r]["_request"].get("url")
                           for r in ("admin", "viewer", "no_auth")
                           if isinstance(c.get("results", {}).get(r), dict)))
                if snapshot_count < len(calls) * 0.5:
                    reasons.append(f"Only {snapshot_count}/{len(calls)} calls have request snapshots — evidence partially reproducible")

            # ── NEW: Evidence quality scoring ──
            # Auto-downgrade when too many red flags accumulate
            quality_red_flags = sum([
                1 if synthetic_count == len(calls) else 0,
                1 if all_404 else 0,
                1 if all_0 else 0,
                1 if empty_bodies == total_role_results else 0,
                1 if not has_request_snapshot else 0,
            ])
            if quality_red_flags >= 3:
                reasons.append(f"Evidence quality critically low ({quality_red_flags}/5 red flags)")

        # ── Stage_verify trace exists ──
        semantic_ref = evidence.get("semantic_evidence_ref") or evidence.get("verifier_trace_ref", "")
        raw_runtime_verdict = evidence.get("raw_runtime_verdict", "")
        if not semantic_ref and not raw_runtime_verdict:
            reasons.append("No Stage_verify trace or raw_runtime_verdict")

        # ── Cross-project contamination check ──
        entity_binding = evidence.get("entity_binding") or {}
        if isinstance(entity_binding, dict):
            tenant = str(entity_binding.get("tenant_id", ""))
            # Single-tenant is ok

        # ── Key IDs or structured missing ──
        missing = evidence.get("missing_requirements") or []
        if isinstance(missing, list) and missing:
            pass

        if reasons:
            # Use FAILED_LOW_EVIDENCE_QUALITY when quality checks dominate,
            # keep FAILED_UNTRACEABLE for structural traceability issues
            quality_indicators = any(
                phrase in " ".join(reasons).lower()
                for phrase in ("synthetic", "404", "failed with status 0",
                              "empty response", "request snapshots",
                              "red flags")
            )
            if quality_indicators and not (
                "No probe calls" in " ".join(reasons)
                or "All " in " ".join(reasons) and "no admin status" in " ".join(reasons)
            ):
                return "FAILED_LOW_EVIDENCE_QUALITY", reasons
            return "FAILED_UNTRACEABLE", reasons

        return "PASSED", []


class BusinessEvidenceGate:
    """Phase92A §4.2: Business Evidence Gate.

    Checks whether the finding has complete business contract evidence
    to become a VALIDATED_CANDIDATE.

    CRITICAL:
    - If this gate fails but Runtime Gate passed, the finding becomes
      SEMANTIC_CONFIRMED_PENDING_EVIDENCE or NEEDS_MORE_EVIDENCE
    - It NEVER becomes REJECTED, FALSIFIED, or INCONCLUSIVE
    - The semantic verdict is always preserved
    """

    REQUIRED_FOR_VALIDATED = {
        "entity_binding": lambda v: isinstance(v, dict) and bool(v.get("entity_id")),
        "before_snapshot_ref": lambda v: bool(str(v).strip()),
        "after_snapshot_ref": lambda v: bool(str(v).strip()),
        "invariant_ref": lambda v: bool(str(v).strip()) or True,  # optional for some types
    }

    @staticmethod
    def check(contract: dict[str, Any], semantic_verdict: str = "") -> tuple[str, list[str]]:
        """Check business evidence completeness.

        Returns (status, missing_requirements) where status is from BUSINESS_GATE_STATUS.
        """
        evidence = contract.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}

        missing: list[str] = []

        # ── Entity binding ──
        entity_binding = evidence.get("entity_binding")
        if not isinstance(entity_binding, dict) or not entity_binding.get("entity_id"):
            missing.append("ENTITY_BINDING_MISSING")

        # ── Before snapshot ──
        before = evidence.get("before_snapshot_ref") or evidence.get("before_snapshot")
        if not str(before or "").strip():
            missing.append("BEFORE_SNAPSHOT_MISSING")

        # ── After snapshot ──
        after = evidence.get("after_snapshot_ref") or evidence.get("after_snapshot")
        if not str(after or "").strip():
            missing.append("AFTER_SNAPSHOT_MISSING")

        # ── Cleanup for write operations ──
        cleanup = evidence.get("cleanup")
        if isinstance(cleanup, dict):
            cleanup_status = str(cleanup.get("status", ""))
            if cleanup_status == "PENDING":
                missing.append("CLEANUP_PENDING")
            elif cleanup_status == "FAILED":
                missing.append("CLEANUP_FAILED")
        else:
            # Phase92A: Check if any call is a write operation (POST/PUT/DELETE/PATCH)
            # Look at all calls, not just the first one
            calls = evidence.get("calls") or []
            write_methods = {"POST", "PUT", "DELETE", "PATCH"}
            has_write_operation = False
            for c in calls:
                if isinstance(c, dict):
                    call_str = c.get("call", "")
                    if call_str:
                        method = call_str.split()[0] if call_str.split() else ""
                        if method in write_methods:
                            has_write_operation = True
                            break
            if has_write_operation:
                missing.append("CLEANUP_PENDING")

        # ── Observer conflict check ──
        observer_refs = evidence.get("observer_refs") or []
        if isinstance(observer_refs, list) and len(observer_refs) >= 2:
            # Check for conflicting observer results
            statuses = set()
            for o in observer_refs:
                if isinstance(o, dict):
                    statuses.add(str(o.get("status", "")))
                elif isinstance(o, str) and "HTTP" in o:
                    # Extract status from string like "GET /api/X -> HTTP 200"
                    parts = o.split("HTTP ")
                    if len(parts) >= 2:
                        statuses.add(parts[1].strip()[:3])

        # ── Semantic evidence ──
        semantic_ref = evidence.get("semantic_evidence_ref") or evidence.get("verifier_trace_ref", "")
        if not str(semantic_ref).strip():
            # Not blocking — just informational
            pass

        if missing:
            return "PENDING", missing

        return "PASSED", []


# ══════════════════════════════════════════════════════════════════════════════
# Contract builder with four-layer state
# ══════════════════════════════════════════════════════════════════════════════

def discovery_finding_to_contract(
    item: Any,
    *,
    project_id: str,
    policy_version: str,
    context_artifact_id: str,
) -> dict[str, Any]:
    """Convert a lightweight discovery signal into a conservative contract.

    Phase92A: Preserves four-layer state:
    - raw_runtime_verdict: from Stage_verify
    - semantic_verdict: mapped to SEMANTIC_VERDICTS
    - business_evidence_status: from enricher
    - final_review_status: from gate results
    """
    row = _finding_fields(item)
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}

    # Bridge runtime calls into business-contract fields.  For the auth-boundary
    # read-only evidence model we intentionally override generic snapshot refs
    # that may have been produced by body normalizers (e.g. snap:hash of the
    # returned payload), because the customer-relevant evidence is the anonymous
    # request/response boundary, not a state mutation before/after pair.
    if evidence.get("calls"):
        extracted = _extract_from_calls(evidence, row)
        force_auth_bridge = extracted.get("evidence_model") == "read_only_auth_boundary"
        force_keys = {
            "evidence_model", "auth_boundary_matrix", "response_sensitivity",
            "before_snapshot_ref", "action_evidence_ref", "after_snapshot_ref",
            "invariant_evidence_ref", "actor_role", "preconditions",
            "entity_binding", "reproduction",
        }
        for k, v in extracted.items():
            if force_auth_bridge and k in force_keys:
                evidence = dict(evidence)
                evidence[k] = v
            elif k not in evidence or not evidence.get(k):
                evidence = dict(evidence)
                evidence[k] = v

    hypothesis_id = _text(row.get("hypothesis_id") or evidence.get("hypothesis_id"), 160)
    digest = hashlib.sha256(
        json.dumps({"project": project_id, "hypothesis": hypothesis_id, "title": row.get("title", "")}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    binding = evidence.get("entity_binding") if isinstance(evidence.get("entity_binding"), dict) else {}
    raw_kind = evidence.get("kind") or row.get("risk_type") or row.get("severity")
    if evidence.get("evidence_model") == "read_only_auth_boundary":
        raw_kind = "permission"
    before = _text(evidence.get("before_snapshot") or evidence.get("before_snapshot_ref"))
    after = _text(evidence.get("after_snapshot") or evidence.get("after_snapshot_ref"))
    action = _text(evidence.get("action_evidence_ref") or evidence.get("evidence_graph_ref"))
    observer_refs = evidence.get("observer_refs") if isinstance(evidence.get("observer_refs"), list) else []
    cleanup = evidence.get("cleanup") if isinstance(evidence.get("cleanup"), dict) else {}

    # ── Extract four-layer state from enriched evidence ──
    # Phase92A: Priority: row attributes > evidence fields > defaults
    raw_runtime_verdict = row.get("_raw_runtime_verdict") or evidence.get("raw_runtime_verdict") or row.get("verdict", "inconclusive")
    
    # Map raw verdict to semantic namespace if not already mapped
    semantic_verdict = row.get("_semantic_verdict") or evidence.get("semantic_verdict")
    if not semantic_verdict or semantic_verdict == raw_runtime_verdict:
        # Map raw verdict to SEMANTIC_VERDICTS namespace
        semantic_verdict = VERDICT_TO_SEMANTIC.get(raw_runtime_verdict, "SEMANTIC_INCONCLUSIVE")
    business_evidence_status = row.get("_business_evidence_status") or evidence.get("business_evidence_status") or "NOT_ENRICHED"
    final_review_status = row.get("_final_review_status") or evidence.get("final_review_status") or "NEEDS_MORE_EVIDENCE"

    return {
        "finding_id": f"DISC_{digest}",
        "hypothesis_id": hypothesis_id,
        "project_id": project_id,
        "policy_version": policy_version,
        "context_artifact_id": context_artifact_id,
        # ── Four-layer state (Phase92A) ──
        "raw_runtime_verdict": raw_runtime_verdict,
        "semantic_verdict": semantic_verdict,
        "business_evidence_status": business_evidence_status,
        "final_review_status": final_review_status,
        # ── Verdict for backward compat ──
        "verdict": "CANDIDATE",
        "title": _text(row.get("title"), 300),
        "business_intent": _text(row.get("expected"), 500),
        "root_cause_candidate": _text(row.get("actual"), 500),
        "entrypoint": {
            "flow_id": _text(evidence.get("flow_id") or evidence.get("route") or ""),
            "step_id": _text(evidence.get("step_id") or ""),
            "action_type": _text(evidence.get("kind") or ("api_probe" if evidence.get("evidence_model") == "read_only_auth_boundary" else "")),
            "actor_role": _text(evidence.get("actor_role") or ""),
        },
        "entity_binding": {
            "entity_alias": _text(binding.get("entity_alias") or evidence.get("entity") or ""),
            "entity_type": _text(binding.get("entity_type") or evidence.get("entity_type") or ""),
            "entity_id": _text(binding.get("entity_id") or evidence.get("entity_id") or ""),
            "tenant_id": _text(binding.get("tenant_id") or evidence.get("tenant_id") or ""),
            "correlation_id": _text(binding.get("correlation_id") or evidence.get("correlation_id") or ""),
            "binding_confidence": binding.get("binding_confidence", evidence.get("binding_confidence", 0.0)),
        },
        "preconditions": [str(value)[:300] for value in (evidence.get("preconditions") or []) if value],
        "before_snapshot_ref": before,
        "action_evidence_ref": action,
        "after_snapshot_ref": after,
        "observer_refs": [str(value)[:300] for value in observer_refs if value],
        "violated_invariant": {
            "kind": _safe_kind(raw_kind),
            "definition": _text(row.get("expected"), 300),
            "result": _text(row.get("actual"), 300),
            "evidence_ref": _text(evidence.get("obligation_id") or evidence.get("invariant_evidence_ref")),
        },
        "business_impact": {
            "impact_type": _safe_kind(raw_kind),
            "scope": _text(row.get("severity"), 80),
            "reason": _text(row.get("actual"), 300),
        },
        "reproduction": {
            "flow_id": _text((evidence.get("reproduction") or {}).get("flow_id") or evidence.get("flow_id") or ""),
            "fixture_refs": [str(value)[:300] for value in ((evidence.get("reproduction") or {}).get("fixture_refs") or evidence.get("fixture_refs") or []) if value],
            "required_inputs": [str(value)[:300] for value in ((evidence.get("reproduction") or {}).get("required_inputs") or evidence.get("required_inputs") or []) if value],
            "expected_observation": _text((evidence.get("reproduction") or {}).get("expected_observation") or row.get("expected"), 300),
        },
        "cleanup": {
            "run_id": _text(cleanup.get("run_id") or evidence.get("run_id") or ""),
            "status": _text(cleanup.get("status") or "NOT_APPLICABLE", 80),
            "evidence_ref": _text(cleanup.get("evidence_ref") or ""),
        },
        "adversarial_validation": {
            "deterministic_result": "DETERMINISTIC_INSUFFICIENT_EVIDENCE",
            "disprover_result": "NOT_RUN",
            "counterarguments": [],
            "unresolved_questions": [],
            "disprover_source": "none",
        },
        "confidence": {
            "score": "high" if float(row.get("confidence") or 0) >= 0.8 else "medium" if float(row.get("confidence") or 0) >= 0.5 else "low",
            "reason": "Discovery verifier signal; independent gate required before human review.",
        },
        "evidence_refs": [str(value)[:300] for value in (evidence.get("evidence_refs") or []) if value] + ([str(evidence.get("obligation_id"))] if evidence.get("obligation_id") else []),
        "evidence_model": _text(evidence.get("evidence_model"), 120),
        "auth_boundary_matrix": evidence.get("auth_boundary_matrix") or {},
        "response_sensitivity": evidence.get("response_sensitivity") or {},
        "missing_requirements": evidence.get("missing_requirements", []),
        "state_history": evidence.get("state_history", []),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Phase92A+: Preserve calls for gate checking and downstream serialization.
        # Auth-boundary findings deliberately carry redacted call bodies.
        "calls": _redact_calls_for_contract(evidence.get("calls") or []) if evidence.get("evidence_model") == "read_only_auth_boundary" else (evidence.get("calls") or []),
    }


def gate_discovery_findings(
    findings: Iterable[Any],
    *,
    project_id: str | None = None,
    policy_version: str = "",
    context_artifact_id: str = "",
    enable_llm_disprover: bool | None = None,
    write_human_review_ledger: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Route *all* discovery output through two-layer gate.

    Phase92A: Runtime Gate → Business Gate → Adversarial → Schema.
    Semantic verdict is ALWAYS preserved through all gates.
    """
    from .business_finding_registry import register_in_ledger, validate_and_register_findings

    resolved_project = project_id or os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
    if enable_llm_disprover is None:
        enable_llm_disprover = os.environ.get("QUALIBUG_ENABLE_LLM_DISPROVER", "0").strip().lower() in {"1", "true", "yes"}

    contracts = [
        discovery_finding_to_contract(
            item,
            project_id=resolved_project,
            policy_version=policy_version,
            context_artifact_id=context_artifact_id,
        )
        for item in findings
    ]

    # ── Phase92A: Two-layer gate ──
    runtime_gate = RuntimeEvidenceGate()
    business_gate = BusinessEvidenceGate()

    for contract in contracts:
        # Phase92A: Build evidence dict from contract fields
        # Contract has calls at top level, not nested under evidence
        original_calls = contract.get("calls") or []
        gate_evidence = {
            "calls": original_calls,
            "entity_binding": contract.get("entity_binding", {}),
            "before_snapshot_ref": contract.get("before_snapshot_ref", ""),
            "after_snapshot_ref": contract.get("after_snapshot_ref", ""),
            "cleanup": contract.get("cleanup", {}),
            "observer_refs": contract.get("observer_refs", []),
            "raw_runtime_verdict": contract.get("raw_runtime_verdict", ""),
            "semantic_verdict": contract.get("semantic_verdict", ""),
            "semantic_evidence_ref": contract.get("violated_invariant", {}).get("evidence_ref", ""),
            "missing_requirements": contract.get("missing_requirements", []),
            "raw_evidence_refs": contract.get("evidence_refs", []),
            "evidence_model": contract.get("evidence_model", ""),
            "auth_boundary_matrix": contract.get("auth_boundary_matrix", {}),
            "response_sensitivity": contract.get("response_sensitivity", {}),
            "action_evidence_ref": contract.get("action_evidence_ref", ""),
            "method": {"value": "GET"},  # Default, will be extracted from calls if available
        }
        # Extract method from first call if available
        if original_calls and isinstance(original_calls, list) and len(original_calls) > 0:
            first_call = original_calls[0].get("call", "") if isinstance(original_calls[0], dict) else ""
            if first_call:
                method = first_call.split()[0] if first_call.split() else "GET"
                gate_evidence["method"] = {"value": method}
        
        contract["evidence"] = gate_evidence  # Attach for gate use

        # ── Runtime Gate ──
        rt_status, rt_reasons = runtime_gate.check(contract)
        contract["runtime_gate_status"] = rt_status

        # ── Business Gate ──
        if rt_status == "PASSED":
            biz_status, biz_missing = business_gate.check(contract, contract.get("semantic_verdict", ""))
            contract["business_gate_status"] = biz_status
            contract["business_gate_missing"] = biz_missing
        else:
            contract["business_gate_status"] = "NOT_RUN"
            contract["business_gate_missing"] = rt_reasons

        # ── Determine verdict based on gate results ──
        semantic_verdict = contract.get("semantic_verdict", "SEMANTIC_INCONCLUSIVE")

        if rt_status != "PASSED":
            # Runtime gate failed — BLOCKED but semantic verdict preserved
            contract["verdict"] = "NEEDS_MORE_EVIDENCE"
            contract["business_evidence_status"] = "BLOCKED_BY_RUNTIME_EVIDENCE"
            contract["final_review_status"] = "BLOCKED"
        elif contract.get("business_gate_status") == "PASSED":
            # Both gates passed — can become VALIDATED_CANDIDATE
            contract["verdict"] = "VALIDATED_CANDIDATE"
            contract["business_evidence_status"] = "VALIDATED"
            contract["final_review_status"] = "PENDING_REVIEW"
        else:
            # Runtime passed but business evidence incomplete
            # CRITICAL: preserve semantic verdict, mark as pending
            contract["verdict"] = "NEEDS_MORE_EVIDENCE"
            if semantic_verdict == "SEMANTIC_CONFIRMED":
                contract["business_evidence_status"] = _pending_status_from_missing(
                    contract.get("business_gate_missing", [])
                )
                contract["final_review_status"] = "NEEDS_MORE_EVIDENCE"
            else:
                contract["business_evidence_status"] = _pending_status_from_missing(
                    contract.get("business_gate_missing", [])
                )
                contract["final_review_status"] = "NEEDS_MORE_EVIDENCE"

    # ── Registry validation (existing) ──
    registry = validate_and_register_findings(
        contracts,
        project_id=resolved_project,
        enable_llm_disprover=bool(enable_llm_disprover),
    )
    if write_human_review_ledger and registry.get("validated_candidates"):
        registry["ledger_registered"] = register_in_ledger(registry, project_id=resolved_project)
    else:
        registry["ledger_registered"] = 0

    # ── Merge registry results with contract four-layer state ──
    by_id: dict[str, dict[str, Any]] = {}
    for bucket in ("validated_candidates", "rejected", "needs_more_evidence", "blocked"):
        for row in registry.get(bucket, []):
            by_id[str(row.get("hypothesis_id") or row.get("finding_id"))] = row

    ordered = []
    for contract in contracts:
        gated = by_id.get(str(contract.get("hypothesis_id") or contract.get("finding_id")), contract)
        if gated is contract:
            # No registry result — preserve our gate verdict
            gated = dict(contract)
            # Keep the verdict as-is (VALIDATED_CANDIDATE or NEEDS_MORE_EVIDENCE)
            # Do NOT change case - tests expect uppercase
        else:
            # Merge four-layer state from contract into registry result
            gated = dict(gated)
            gated["raw_runtime_verdict"] = contract.get("raw_runtime_verdict", "")
            gated["semantic_verdict"] = contract.get("semantic_verdict", "")
            gated["business_evidence_status"] = contract.get("business_evidence_status", "")
            gated["final_review_status"] = contract.get("final_review_status", "")
            gated["runtime_gate_status"] = contract.get("runtime_gate_status", "")
            gated["business_gate_status"] = contract.get("business_gate_status", "")
            gated["missing_requirements"] = contract.get("missing_requirements", [])

            # CRITICAL: Registry must not downgrade semantic confirmed to rejected
            if contract.get("semantic_verdict") == "SEMANTIC_CONFIRMED":
                registry_verdict = str(gated.get("verdict", ""))
                if registry_verdict in ("rejected", "falsified", "inconclusive"):
                    gated["verdict"] = "needs_more_evidence"
                    gated["business_evidence_status"] = "PENDING_ENTITY_BINDING"
                    gated["final_review_status"] = "NEEDS_MORE_EVIDENCE"

        ordered.append(gated)

    return ordered, registry


def _pending_status_from_missing(missing: list[str]) -> str:
    """Convert missing requirements to a specific PENDING status."""
    if not missing:
        return "PENDING_ENTITY_BINDING"
    for req in missing:
        if "ENTITY_BINDING" in req:
            return "PENDING_ENTITY_BINDING"
        if "BEFORE_SNAPSHOT" in req:
            return "PENDING_BEFORE_SNAPSHOT"
        if "AFTER_SNAPSHOT" in req:
            return "PENDING_AFTER_SNAPSHOT"
        if "CLEANUP" in req:
            return "PENDING_CLEANUP_EVIDENCE"
        if "OBSERVER" in req:
            return "PENDING_OBSERVER_CONSENSUS"
        if "ASYNC" in req:
            return "PENDING_ASYNC_WINDOW"
    return "PENDING_ENTITY_BINDING"
