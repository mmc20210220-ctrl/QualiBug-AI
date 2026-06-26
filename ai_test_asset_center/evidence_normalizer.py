"""Phase92A · Evidence Normalizer — Raw Probe Evidence → Normalized Runtime Evidence.

Converts Discovery Engine / Executor raw probe calls into structured,
traceable evidence without losing fidelity.  Every extracted field records
its source path for auditability.

PHASE92A CRITICAL:
- Semantic verdict from Stage_verify MUST be preserved
- Normalizer NEVER changes semantic_verdict
- Missing evidence becomes PENDING_*, not REJECTED
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .evidence_models import (
    SourcedValue,
    RawProbeEvidence,
    NormalizedRuntimeEvidence,
    SemanticVerificationEvidence,
    MISSING_REQUIREMENTS,
    SEMANTIC_VERDICTS,
)


# ── Normalizer ──────────────────────────────────────────────────────────
class EvidenceNormalizer:
    """Convert raw Discovery Engine probe calls into NormalizedRuntimeEvidence."""

    ENTITY_PATTERNS = [
        (r'/api/\w+/([A-Z]{2,3}-?\d+|[A-Z]+\d+|[\w-]+-\d+)', "path_segment"),
        (r'"([A-Z]{2,3}-\d+)"', "response_body"),
        (r'"code":\s*"([^"]+)"', "body.code"),
        (r'"id":\s*"?(\d+)"?', "body.id"),
        (r'"orderNo":\s*"([^"]+)"', "body.orderNo"),
        (r'"materialCode":\s*"([^"]+)"', "body.materialCode"),
        (r'"workOrderNo":\s*"([^"]+)"', "body.workOrderNo"),
        (r'"inspectionNo":\s*"([^"]+)"', "body.inspectionNo"),
        (r'"transferNo":\s*"([^"]+)"', "body.transferNo"),
    ]

    @staticmethod
    def _extract_value(text: str, patterns: list[tuple[str, str]]) -> SourcedValue:
        for pat, src in patterns:
            m = re.search(pat, text)
            if m:
                return SourcedValue(m.group(1), src, "evidenced")
        return SourcedValue.missing("no_pattern_match")

    @staticmethod
    def _body_text(call_result: dict, role: str = "admin") -> str:
        try:
            body = call_result.get("results", {}).get(role, {}).get("body", {})
            return json.dumps(body, ensure_ascii=False) if body else ""
        except Exception:
            return ""

    @staticmethod
    def _body_dict(call_result: dict, role: str = "admin") -> dict[str, Any]:
        try:
            body = call_result.get("results", {}).get(role, {}).get("body", {})
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    # ── Map raw Stage_verify verdict to SEMANTIC_VERDICTS ────────────────
    VERDICT_TO_SEMANTIC = {
        "confirmed": "SEMANTIC_CONFIRMED",
        "falsified": "SEMANTIC_FALSIFIED",
        "inconclusive": "SEMANTIC_INCONCLUSIVE",
        "execution_error": "SEMANTIC_ERROR",
        "needs_more_evidence": "SEMANTIC_PENDING",
    }

    def build_raw_probe_evidences(
        self, calls: list[dict], run_id: str = "", hypothesis_id: str = "",
    ) -> list[RawProbeEvidence]:
        """Build RawProbeEvidence for each call in a finding's evidence."""
        raw_probes = []
        for c in calls:
            rp = RawProbeEvidence.from_call(c, run_id=run_id, hypothesis_id=hypothesis_id)
            raw_probes.append(rp)
        return raw_probes

    def normalize(self, finding: Any, calls: list[dict], hypothesis: dict = None) -> NormalizedRuntimeEvidence:
        """Normalize a single finding's runtime probe evidence.

        CRITICAL: Parse failures are recorded structurally; they never
        abort the entire round.  entity = None becomes PENDING_ENTITY_BINDING,
        never None.lower().
        """
        ev = NormalizedRuntimeEvidence()
        hp = hypothesis or {}
        hid = ""
        if hasattr(finding, "hypothesis_id"):
            hid = str(finding.hypothesis_id or "")
        elif isinstance(finding, dict):
            hid = str(finding.get("hypothesis_id", ""))
        ev.raw_evidence_refs = [c.get("call", "") for c in calls[:10]]

        # ── Method & resource path from first meaningful call ──
        for c in calls:
            call_str = c.get("call", "")
            parts = call_str.split(None, 1)
            if len(parts) >= 2:
                ev.method = SourcedValue(parts[0], "call.method", "evidenced")
                ev.resource_path = SourcedValue(parts[1], "call.path", "evidenced")
                break

        # ── Extract entity from path and response bodies ──
        all_text = " ".join(
            f"{c.get('call', '')} {self._body_text(c, 'admin')}"
            for c in calls
        )
        ev.entity_id = self._extract_value(all_text, self.ENTITY_PATTERNS)

        # Entity type from path segment — safe for None/empty
        resource = str(ev.resource_path.value or "")
        path_segments = resource.strip("/").split("/")
        if len(path_segments) >= 1 and path_segments[0]:
            et = path_segments[0].rstrip("s")
            if et:
                ev.entity_type = SourcedValue(et, "path.root_segment", "evidenced")

        # ── Tenant/owner from path or headers ──
        # MES is single-tenant — explicit guard
        ev.tenant_id = SourcedValue("single-tenant", "environment_config", "evidenced")

        # ── Actor from auth context ──
        for c in calls:
            admin_result = c.get("results", {}).get("admin", {})
            if admin_result:
                ev.actor_id = SourcedValue("admin", "call.admin_auth", "evidenced")
                break

        # ── Before/after candidates ──
        if len(calls) >= 1:
            ev.before_candidates = [{
                "call": calls[0].get("call", ""),
                "body_snapshot": self._body_dict(calls[0], "admin"),
            }]
        if len(calls) >= 3:
            ev.action_ref = SourcedValue(calls[1].get("call", ""), "calls[1]", "evidenced")
            ev.after_candidates = [{
                "call": calls[-1].get("call", ""),
                "body_snapshot": self._body_dict(calls[-1], "admin"),
            }]
        elif len(calls) >= 2:
            ev.action_ref = SourcedValue(calls[1].get("call", ""), "calls[1]", "evidenced")
            ev.after_candidates = [{
                "call": calls[-1].get("call", ""),
                "body_snapshot": self._body_dict(calls[-1], "admin"),
            }]

        # ── Observer refs ──
        for i, c in enumerate(calls):
            results = c.get("results", {})
            for role in ("admin", "viewer", "no_auth"):
                r = results.get(role, {})
                if r:
                    ev.observer_refs.append({
                        "index": i,
                        "role": role,
                        "status": r.get("status", 0),
                        "call": c.get("call", ""),
                    })

        # ── Call chain refs ──
        ev.call_chain_refs = [c.get("call", "") for c in calls]

        # ── Timing window ──
        ev.timing_window = {
            "total_calls": len(calls),
            "normalized_at": time.time(),
        }

        # ── Missing requirements — structured, never abortive ──
        if ev.entity_id.confidence == "missing":
            ev.missing_requirements.append("ENTITY_BINDING_MISSING")
        if not ev.before_candidates:
            ev.missing_requirements.append("BEFORE_SNAPSHOT_MISSING")
        if not ev.after_candidates:
            ev.missing_requirements.append("AFTER_SNAPSHOT_MISSING")

        return ev

    def normalize_semantic(self, finding: Any) -> SemanticVerificationEvidence:
        """Extract semantic verdict from a DiscoveryFinding.

        CRITICAL: Stage_verify verdict is NEVER changed by this method.
        We map the raw verdict to the SEMANTIC_VERDICTS namespace for
        traceability, but preserve the original in _original_verdict.
        The Gate may NOT overwrite semantic_verdict.
        """
        se = SemanticVerificationEvidence()

        raw_verdict = "inconclusive"
        if hasattr(finding, "verdict"):
            raw_verdict = str(finding.verdict or "inconclusive")
        elif isinstance(finding, dict):
            raw_verdict = str(finding.get("verdict", "inconclusive"))

        # ── Map to semantic namespace (preserves original) ──
        se.semantic_verdict = self.VERDICT_TO_SEMANTIC.get(raw_verdict, "SEMANTIC_INCONCLUSIVE")
        se._original_verdict = raw_verdict  # NEVER lost

        if hasattr(finding, "expected"):
            se.expected_behavior = str(finding.expected or "")
        elif isinstance(finding, dict):
            se.expected_behavior = str(finding.get("expected", finding.get("expected_behavior", "")))

        if hasattr(finding, "actual"):
            se.observed_behavior = str(finding.actual or "")
        elif isinstance(finding, dict):
            se.observed_behavior = str(finding.get("actual", ""))

        if hasattr(finding, "confidence"):
            se.semantic_confidence = float(finding.confidence or 0)
        elif isinstance(finding, dict):
            se.semantic_confidence = float(finding.get("confidence", 0))

        # ── Verifier rule from finding evidence ──
        evidence = {}
        if hasattr(finding, "evidence"):
            evidence = finding.evidence or {}
        elif isinstance(finding, dict):
            evidence = finding.get("evidence") or {}
        if isinstance(evidence, dict):
            se.verifier_rule = str(evidence.get("verifier_rule", ""))

        se.verifier_trace_ref = f"stage_verify:{time.time()}:{raw_verdict}"
        return se


# ── Bridge entry point ──────────────────────────────────────────────────
def normalize_finding_evidence(
    finding: Any,
    calls: list[dict] | None = None,
    hypothesis: dict | None = None,
) -> dict[str, Any]:
    """One-shot: normalize runtime + semantic evidence for a single finding.

    Returns both NormalizedRuntimeEvidence and SemanticVerificationEvidence.
    The semantic verdict is ALWAYS preserved; Gates must not overwrite it.
    """
    normalizer = EvidenceNormalizer()
    calls = calls or []
    runtime = normalizer.normalize(finding, calls, hypothesis)
    semantic = normalizer.normalize_semantic(finding)
    # Also build raw probe evidence for traceability
    hid = ""
    if hasattr(finding, "hypothesis_id"):
        hid = str(finding.hypothesis_id or "")
    elif isinstance(finding, dict):
        hid = str(finding.get("hypothesis_id", ""))
    raw_probes = normalizer.build_raw_probe_evidences(calls, hypothesis_id=hid)
    return {
        "runtime": runtime.to_dict(),
        "semantic": semantic.to_dict(),
        "raw_probes": [rp.to_dict() for rp in raw_probes],
        "normalized_at": time.time(),
    }
