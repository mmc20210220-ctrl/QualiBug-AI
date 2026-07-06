"""Evidence system audit unit tests.

Covers the 2026-07-05 deep-audit fixes in display_ready_formatter.py:
- Claim-evidence consistency checking (3 dimensions: status code, response body, status label)
- Evidence gate enforcement with consistency downgrade
- No fabricated reproduction steps / assertions
- No inflated runtime_proof / has_assertion / reproduction completeness
- Test summary never falsely claims "可复现"

These tests are generic (no hardcoded business concepts) and verify behaviour
across any industry domain.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable when run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.display_ready_formatter import (  # noqa: E402
    BUG_STATUS_META,
    _build_repro_steps_display,
    _build_test_summary,
    _check_claim_evidence_consistency,
    _compute_bug_status,
    _compute_evidence_completeness,
    _compute_evidence_quality,
    _compute_reproducibility_confidence,
    _enforce_evidence_gate,
    _extract_failed_assertions,
    _generate_default_repro_steps,
    _format_single_finding,
    _has_runtime_response,
    _path_mismatch_reasons,
    format_findings_display_ready,
    sanitize_customer_evidence_payload,
)



# ═══════════════════════════════════════════════════════════════════════
# A. Claim-evidence consistency (3 dimensions)
# ═══════════════════════════════════════════════════════════════════════

class TestClaimEvidenceConsistency:
    """Verify the 3 consistency dimensions are checked generically."""

    def test_claim_500_actual_201_detected(self):
        """Finding claims server 500 but HAR shows 201 success → contradiction."""
        finding = {
            "title": "超长email导致服务端500",
            "description": "POST /api/auth/register 返回500",
            "har_evidence": {"status_code": 201, "response_body": '{"id":"abc"}'},
        }
        contradictions = _check_claim_evidence_consistency(finding)
        assert len(contradictions) >= 1
        assert any("500" in c and "201" in c for c in contradictions)

    def test_claim_500_actual_500_no_contradiction(self):
        """Finding claims 500, actual 500 → no contradiction."""
        finding = {
            "title": "超长email导致服务端500",
            "har_evidence": {"status_code": 500, "response_body": "Internal Server Error"},
        }
        assert _check_claim_evidence_consistency(finding) == []

    def test_claim_error_response_body_no_error_flag_detected(self):
        """Finding claims error/fail but 2xx response body has no error field → contradiction."""
        finding = {
            "title": "创建用户失败",
            "description": "用户创建返回错误",
            "har_evidence": {"status_code": 200, "response_body": '{"id":"123","name":"test"}'},
        }
        contradictions = _check_claim_evidence_consistency(finding)
        assert len(contradictions) >= 1

    def test_claim_error_response_body_has_error_no_contradiction(self):
        """Finding claims error and response body has error field → no contradiction."""
        finding = {
            "title": "创建用户失败",
            "description": "返回错误",
            "har_evidence": {"status_code": 200, "response_body": '{"error":"validation failed"}'},
        }
        assert _check_claim_evidence_consistency(finding) == []

    def test_status_confirmed_no_anomaly_detected(self):
        """Status marked confirmed/reproduced but 2xx + no DB violation → contradiction."""
        finding = {
            "title": "权限越权",
            "status": "confirmed",
            "har_evidence": {"status_code": 200, "response_body": '{"data":"ok"}'},
        }
        contradictions = _check_claim_evidence_consistency(finding)
        assert len(contradictions) >= 1
        assert any("confirmed" in c for c in contradictions)

    def test_no_har_evidence_no_contradiction(self):
        """No HAR evidence → cannot judge, no contradiction."""
        finding = {"title": "某问题", "description": "某描述"}
        assert _check_claim_evidence_consistency(finding) == []

    def test_no_claimed_code_no_contradiction(self):
        """No claimed HTTP code in text → no contradiction (can't judge)."""
        finding = {
            "title": "缺少安全头",
            "har_evidence": {"status_code": 200},
        }
        assert _check_claim_evidence_consistency(finding) == []

    def test_generic_industry_not_hardcoded(self):
        """Works for any industry (healthcare, logistics, fintech) — no hardcoded concepts."""
        # Healthcare
        f1 = {
            "title": "预约接口返回500",
            "har_evidence": {"status_code": 200, "response_body": '{"appointment_id":"a1"}'},
        }
        assert len(_check_claim_evidence_consistency(f1)) >= 1
        # Logistics
        f2 = {
            "title": "运单创建失败",
            "description": "error",
            "har_evidence": {"status_code": 201, "response_body": '{"waybill":"w1"}'},
        }
        assert len(_check_claim_evidence_consistency(f2)) >= 1


# ═══════════════════════════════════════════════════════════════════════
# B. Evidence gate enforcement
# ═══════════════════════════════════════════════════════════════════════

class TestEvidenceGateEnforcement:
    """Verify gate downgrades on consistency contradiction."""

    @pytest.fixture
    def risk_clue_status(self):
        return {
            "status": "risk_clue",
            "label": BUG_STATUS_META["risk_clue"]["label"],
            "description": "test",
            "is_reproducible": False,
            "gate_passed": False,
        }

    def test_contradiction_downgrades_to_not_reproduced(self, risk_clue_status):
        """Claim 500 but actual 201 → not_reproduced."""
        finding = {
            "title": "超长email导致服务端500",
            "har_evidence": {"status_code": 201, "response_body": '{"id":"abc"}'},
        }
        result = _enforce_evidence_gate(finding, risk_clue_status, {})
        assert result["status"] == "not_reproduced"
        assert result["gate_passed"] is False
        assert len(result.get("gate_failures", [])) >= 1

    def test_no_contradiction_keeps_risk_clue(self, risk_clue_status):
        """No contradiction → stays risk_clue."""
        finding = {
            "title": "正常问题",
            "har_evidence": {"status_code": 500, "response_body": "error"},
        }
        result = _enforce_evidence_gate(finding, risk_clue_status, {})
        assert result["status"] == "risk_clue"

    def test_completeness_gate_downgrades_reproduced_to_suspected(self):
        """reproduced but incomplete evidence → suspected."""
        finding = {"title": "问题", "expected_behavior": "应该正确", "actual_behavior": "实际错误"}
        reproduced_status = {
            "status": "reproduced",
            "label": BUG_STATUS_META["reproduced"]["label"],
            "description": "test",
            "is_reproducible": True,
            "gate_passed": True,
        }
        # Low completeness (1/6, no API response or DB)
        completeness = {
            "present_count": 1,
            "dimensions": [
                {"key": "api_response", "present": False},
                {"key": "db_evidence", "present": False},
            ],
        }
        result = _enforce_evidence_gate(finding, reproduced_status, completeness)
        assert result["status"] == "suspected"


# ═══════════════════════════════════════════════════════════════════════
# C. No inflated evidence quality
# ═══════════════════════════════════════════════════════════════════════

class TestEvidenceQualityNoInflation:
    """Verify runtime_proof and has_assertion are not inflated by text-only fields."""

    def test_text_only_not_runtime_proof(self):
        """expected/actual text alone should NOT count as runtime proof."""
        finding = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "actual_behavior": "实际行为",
            "evidence": {"expected": "应该正确", "actual": "实际行为"},
        }
        q = _compute_evidence_quality(finding, "/api/test")
        assert q["can_reproduce"] is False

    def test_bare_status_code_without_request_trace_is_not_runtime_proof(self):
        """A bare status_code without request trace should not count as runtime proof."""
        finding = {
            "title": "某问题",
            "evidence": {"status_code": 500},
        }
        q = _compute_evidence_quality(finding, "/api/test")
        assert q["can_reproduce"] is False

    def test_text_only_not_assertion(self):
        """expected+actual text alone should NOT count as assertion."""
        finding = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "actual_behavior": "实际行为",
            "evidence": {"expected": "应该正确", "actual": "实际行为"},
        }
        q = _compute_evidence_quality(finding, "/api/test")
        verified_text = " ".join(q.get("verified", []))
        assert "已识别失败断言" not in verified_text

    def test_http_error_counts_as_assertion(self):
        """HTTP 4xx/5xx counts as a real assertion."""
        finding = {
            "title": "某问题",
            "har_evidence": {"status_code": 500, "response_body": "error"},
        }
        q = _compute_evidence_quality(finding, "/api/test")
        verified_text = " ".join(q.get("verified", []))
        assert "已识别失败断言" in verified_text


# ═══════════════════════════════════════════════════════════════════════
# D. Evidence completeness reproduction dimension
# ═══════════════════════════════════════════════════════════════════════

class TestEvidenceCompletenessReproduction:
    """Verify reproduction dimension is not inflated by 2xx success responses."""

    def test_har_200_not_reproduction(self):
        """HAR 200 success should NOT mark reproduction as present."""
        finding = {
            "title": "某问题",
            "har_evidence": {"status_code": 200, "response_body": '{"ok":true}'},
        }
        ec = _compute_evidence_completeness(finding)
        repro = [d for d in ec["dimensions"] if d["key"] == "reproduction"][0]
        assert repro["present"] is False

    def test_har_500_is_reproduction(self):
        """HAR 500 error marks reproduction as present."""
        finding = {
            "title": "某问题",
            "har_evidence": {"status_code": 500, "response_body": "error"},
        }
        ec = _compute_evidence_completeness(finding)
        repro = [d for d in ec["dimensions"] if d["key"] == "reproduction"][0]
        assert repro["present"] is True

    def test_explicit_reproducible_flag_without_runtime_trace_is_not_reproduction(self):
        """reproducibility.reproducible=True still needs a request/response trace."""
        finding = {
            "title": "某问题",
            "reproducibility": {"reproducible": True},
        }
        ec = _compute_evidence_completeness(finding)
        repro = [d for d in ec["dimensions"] if d["key"] == "reproduction"][0]
        assert repro["present"] is False


# ═══════════════════════════════════════════════════════════════════════
# E. No fabricated assertions
# ═══════════════════════════════════════════════════════════════════════

class TestNoFabricatedAssertions:
    """Verify _extract_failed_assertions does not fabricate assertions."""

    def test_no_anomaly_signal_empty_assertions(self):
        """No HTTP error, no DB violation, no error field → empty list."""
        finding = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "actual_behavior": "实际行为",
            "har_evidence": {"status_code": 200, "response_body": '{"ok":true}'},
        }
        assertions = _extract_failed_assertions(finding, {})
        assert assertions == []

    def test_http_error_generates_assertion(self):
        """HTTP 500 generates an http_status_error assertion."""
        finding = {
            "title": "某问题",
            "har_evidence": {"status_code": 500, "response_body": "Internal Server Error"},
        }
        assertions = _extract_failed_assertions(finding, {})
        assert len(assertions) >= 1
        assert any(a["type"] == "http_status_error" for a in assertions)

    def test_response_error_field_generates_assertion(self):
        """Response body with error field generates assertion."""
        finding = {
            "title": "某问题",
            "har_evidence": {"status_code": 200, "response_body": '{"error":"validation failed"}'},
        }
        assertions = _extract_failed_assertions(finding, {})
        assert any(a["type"] == "response_error_field" for a in assertions)

    def test_behavior_mismatch_only_with_anomaly(self):
        """behavior_mismatch only added when there's already a real anomaly signal."""
        # No anomaly → no behavior_mismatch
        finding_ok = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "actual_behavior": "实际行为",
            "har_evidence": {"status_code": 200, "response_body": '{"ok":true}'},
        }
        assertions_ok = _extract_failed_assertions(finding_ok, {})
        assert not any(a["type"] == "behavior_mismatch" for a in assertions_ok)

        # With anomaly → behavior_mismatch added (has expected+actual)
        finding_err = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "actual_behavior": "实际行为",
            "har_evidence": {"status_code": 500, "response_body": "error"},
        }
        assertions_err = _extract_failed_assertions(finding_err, {})
        assert any(a["type"] == "behavior_mismatch" for a in assertions_err)


# ═══════════════════════════════════════════════════════════════════════
# F. No fabricated reproduction steps
# ═══════════════════════════════════════════════════════════════════════

class TestNoFabricatedReproSteps:
    """Verify _generate_default_repro_steps marks synthetic and returns empty when no path."""

    def test_no_path_returns_empty(self):
        """No API path → empty steps (cannot generate meaningful guidance)."""
        finding = {"title": "某问题", "expected_behavior": "应该正确"}
        steps = _generate_default_repro_steps(finding, "", "GET", {})
        assert steps == []

    def test_path_no_response_marked_guidance(self):
        """Path but no real response → steps marked with [指引]."""
        finding = {"title": "某问题", "expected_behavior": "应该正确"}
        steps = _generate_default_repro_steps(finding, "/api/test", "POST", {})
        assert len(steps) > 0
        assert all("[指引]" in s for s in steps)

    def test_path_with_response_mentions_actual_status(self):
        """Path + real HAR response → guidance mentions actual status code."""
        finding = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "har_evidence": {"status_code": 500, "response_body": "error"},
        }
        steps = _generate_default_repro_steps(finding, "/api/test", "POST", {})
        assert any("500" in s for s in steps)

    def test_build_repro_steps_is_synthetic_flag(self):
        """_build_repro_steps_display sets is_synthetic=True for generated steps."""
        finding = {
            "title": "某问题",
            "expected_behavior": "应该正确",
            "_api_path": "/api/test",
            "_api_method": "GET",
        }
        result = _build_repro_steps_display(finding)
        assert result["is_synthetic"] is True
        assert len(result["steps"]) > 0

    def test_build_repro_steps_real_steps_not_synthetic(self):
        """_build_repro_steps_display sets is_synthetic=False when real steps exist."""
        finding = {
            "title": "某问题",
            "reproduction_steps": ["step 1", "step 2"],
            "_api_path": "/api/test",
        }
        result = _build_repro_steps_display(finding)
        assert result["is_synthetic"] is False
        assert result["steps"] == ["step 1", "step 2"]


# ═══════════════════════════════════════════════════════════════════════
# G. Test summary never falsely claims "可复现"
# ═══════════════════════════════════════════════════════════════════════

class TestTestSummaryNoFalseReproducible:
    """Verify _build_test_summary does not falsely claim 可复现."""

    def test_synthetic_steps_risk_clue_no_reproducible(self):
        """Synthetic steps + risk_clue → never says 可复现."""
        finding = {"title": "某问题"}
        repro = {"method": "POST", "path": "/api/test", "steps": ["[指引] 建议执行"], "is_synthetic": True}
        bs = {"label": "风险线索", "is_reproducible": False}
        summary = _build_test_summary(finding, repro, bs)
        assert "可复现" not in summary

    def test_real_steps_reproduced_says_reproducible(self):
        """Real steps + reproduced → says 可复现."""
        finding = {"title": "某问题"}
        repro = {"method": "POST", "path": "/api/test", "steps": ["step1", "step2"], "is_synthetic": False}
        bs = {"label": "已复现", "is_reproducible": True}
        summary = _build_test_summary(finding, repro, bs)
        assert "可复现" in summary

    def test_real_steps_suspected_no_reproducible(self):
        """Real steps + suspected → never says 可复现."""
        finding = {"title": "某问题"}
        repro = {"method": "POST", "path": "/api/test", "steps": ["step1"], "is_synthetic": False}
        bs = {"label": "疑似", "is_reproducible": False}
        summary = _build_test_summary(finding, repro, bs)
        assert "可复现" not in summary

    def test_no_path_no_reproducible(self):
        """No path → never says 可复现."""
        finding = {"title": "某问题"}
        repro = {"method": "GET", "path": "", "steps": [], "is_synthetic": True}
        bs = {"label": "风险线索", "is_reproducible": False}
        summary = _build_test_summary(finding, repro, bs)
        assert "可复现" not in summary


# ═══════════════════════════════════════════════════════════════════════
# H. Bug status computation
# ═══════════════════════════════════════════════════════════════════════

class TestBugStatusComputation:
    """Verify _compute_bug_status assigns correct four-state status."""

    def test_falsified_status_becomes_not_reproduced(self):
        """Explicit falsified/rejected status → not_reproduced."""
        finding = {"title": "问题", "status": "falsified"}
        q = {"level": "needs_evidence", "can_reproduce": False}
        ec = {"present_count": 0, "dimensions": []}
        result = _compute_bug_status(finding, q, ec)
        assert result["status"] == "not_reproduced"

    def test_rule_only_becomes_risk_clue(self):
        """Only rule source, no runtime evidence → risk_clue."""
        finding = {"title": "问题", "_doc_refs": [{"display_name": "PRD.md"}]}
        q = {"level": "needs_evidence", "can_reproduce": False}
        ec = {
            "present_count": 1,
            "dimensions": [
                {"key": "rule_source", "present": True},
                {"key": "api_response", "present": False},
                {"key": "db_evidence", "present": False},
                {"key": "reproduction", "present": False},
            ],
        }
        result = _compute_bug_status(finding, q, ec)
        assert result["status"] == "risk_clue"

    def test_reproducibility_confidence_not_reproduced_is_zero(self):
        """not_reproduced → confidence 0.0."""
        finding = {}
        bs = {"status": "not_reproduced"}
        q = {"score": 90}
        assert _compute_reproducibility_confidence(finding, bs, q) == 0.0

    def test_reproducibility_confidence_risk_clue_capped_low(self):
        """risk_clue → confidence capped at 0.1."""
        finding = {}
        bs = {"status": "risk_clue"}
        q = {"score": 90}
        assert _compute_reproducibility_confidence(finding, bs, q) == 0.1

    def test_reproducibility_confidence_suspected_capped(self):
        """suspected → confidence capped at 0.69."""
        finding = {}
        bs = {"status": "suspected"}
        q = {"score": 100, "can_reproduce": True}
        conf = _compute_reproducibility_confidence(finding, bs, q)
        assert conf <= 0.69


class TestRuntimeEvidenceTraceability:
    """Regression coverage for customer-facing evidence traceability."""

    def test_path_mismatch_downgrades_to_not_reproduced(self):
        finding = {
            "title": "HTTP 500 on documented endpoint",
            "status": "confirmed",
            "_api_method": "POST",
            "_api_path": "/api/orders/create",
            "expected_behavior": "request should fail with a business validation error",
            "actual_behavior": "HTTP 500 was reported",
            "har_evidence": {
                "method": "POST",
                "path": "/api/users/create",
                "status_code": 500,
                "response_body": '{"error":"boom"}',
            },
        }

        formatted = _format_single_finding(finding)

        assert formatted["bug_status"] == "not_reproduced"
        assert formatted["verdict"] == "pending"
        assert formatted["reproduction"]["har_evidence"] is None
        assert any("does not match observed runtime path" in item for item in formatted["gate_failures"])

    def test_unrelated_har_response_does_not_prove_business_state_claim(self):
        finding = {
            "title": "workflow: cancelled -> pay -> paid",
            "status": "confirmed",
            "_api_method": "POST",
            "_api_path": "/api/orders",
            "source_entity": "orders",
            "source_value": "POST /api/orders",
            "expected_behavior": "cancelled resources must reject pay action",
            "actual_behavior": "workflow: cancelled -> pay -> paid",
            "har_evidence": {
                "method": "POST",
                "path": "/api/orders",
                "status_code": 500,
                "response_body": '{"error":"invalid uuid syntax: \\"test-addr\\""}',
                "actor": "admin",
                "duration_ms": 73,
            },
        }

        formatted = _format_single_finding(finding)

        assert formatted["bug_status"] == "not_reproduced"
        assert formatted["gate_passed"] is False
        assert formatted["evidence_quality"]["can_reproduce"] is False
        assert formatted["raw_evidence"]["response_raw"] == {}
        assert formatted["reproduction"]["har_evidence"] is None
        assert any("不匹配" in item for item in formatted["gate_failures"])

    def test_response_sanitizer_downgrades_cached_mismatched_display_risk(self):
        payload = {
            "ok": True,
            "data": {
                "risks": [
                    {
                        "title": "workflow: cancelled -> pay -> paid",
                        "expected": "cancelled resources must reject pay action",
                        "actual": "workflow: cancelled -> pay -> paid",
                        "bug_status": "reproduced",
                        "verdict": "confirmed",
                        "gate_passed": True,
                        "is_reproducible": True,
                        "repro_method": "POST",
                        "repro_path": "/api/orders",
                        "evidence_quality": {"score": 82, "can_reproduce": True, "verified": ["已捕获真实接口响应（状态码/响应体）"], "missing": []},
                        "raw_evidence": {
                            "request_raw": {"method": "POST", "path": "/api/orders", "actor": "admin"},
                            "response_raw": {"status_code": 500, "body": '{"error":"invalid uuid syntax: \\"test-addr\\""}', "duration_ms": 73},
                            "has_real_evidence": True,
                        },
                        "reproduction": {"har_evidence": {"status_code": 500, "response_body": '{"error":"invalid uuid syntax: \\"test-addr\\""}'}, "steps": ["old polluted step"]},
                        "proof": {"repro_rate": 100},
                    }
                ]
            },
        }

        sanitized = sanitize_customer_evidence_payload(payload)
        risk = sanitized["data"]["risks"][0]

        assert risk["bug_status"] == "not_reproduced"
        assert risk["gate_passed"] is False
        assert risk["raw_evidence"]["response_raw"] == {}
        assert risk["reproduction"]["har_evidence"] is None
        assert risk["proof"]["repro_rate"] == 0

    def test_response_sanitizer_does_not_treat_content_type_as_uuid_context(self):
        payload = {
            "title": "missing security header: X-Content-Type-Options",
            "expected": "response should include X-Content-Type-Options",
            "actual": "HTTP response missed X-Content-Type-Options",
            "bug_status": "reproduced",
            "verdict": "confirmed",
            "gate_passed": True,
            "is_reproducible": True,
            "evidence_quality": {"score": 82, "can_reproduce": True, "verified": [], "missing": []},
            "raw_evidence": {
                "request_raw": {"method": "POST", "path": "/api/orders"},
                "response_raw": {"status_code": 500, "body": '{"error":"invalid uuid syntax: \\"test-addr\\""}'},
                "has_real_evidence": True,
            },
            "reproduction": {"har_evidence": {"status_code": 500}},
            "proof": {"repro_rate": 100},
        }

        sanitized = sanitize_customer_evidence_payload(payload)

        assert sanitized["bug_status"] == "not_reproduced"
        assert sanitized["raw_evidence"]["response_raw"] == {}

    def test_response_sanitizer_removes_unbound_test_placeholder_response(self):
        payload = {
            "title": "POST parameter mutation orderId=-1",
            "expected": "request should return a controlled validation error",
            "actual": "server returned 500",
            "bug_status": "not_reproduced",
            "verdict": "pending",
            "gate_passed": False,
            "evidence_quality": {"score": 40, "can_reproduce": False, "verified": [], "missing": []},
            "raw_evidence": {
                "request_raw": {"method": "POST", "path": "/api/payments/pay"},
                "response_raw": {"status_code": 500, "body": '{"error":"invalid uuid syntax: \\"test-addr\\""}'},
                "has_real_evidence": True,
            },
            "reproduction": {"har_evidence": {"status_code": 500}},
            "proof": {"repro_rate": 0},
        }

        sanitized = sanitize_customer_evidence_payload(payload)

        assert sanitized["raw_evidence"]["response_raw"] == {}
        assert sanitized["reproduction"]["har_evidence"] is None

    def test_placeholder_path_does_not_count_as_api_evidence(self):
        finding = {
            "title": "unbound id route",
            "_api_path": "/api/resources/QUALIBUG_UNRESOLVED_ID",
            "expected_behavior": "resource should be validated",
            "actual_behavior": "candidate risk only",
            "evidence": {"source_file": "engine-output.json"},
        }

        quality = _compute_evidence_quality(finding, finding["_api_path"])
        completeness = _compute_evidence_completeness(finding)
        formatted = _format_single_finding(finding)

        assert quality["can_reproduce"] is False
        assert {d["key"]: d["present"] for d in completeness["dimensions"]}["api_request"] is False
        assert formatted["repro_path"] == ""
        assert formatted["bug_status"] in {"risk_clue", "not_reproduced"}

    def test_confirmed_text_and_source_file_are_not_runtime_evidence(self):
        finding = {
            "title": "text-only candidate",
            "status": "confirmed",
            "_api_path": "/api/customers",
            "expected_behavior": "expected text",
            "actual_behavior": "actual text",
            "evidence": {"source_file": "reasoner-output.json"},
        }

        assert _has_runtime_response(finding) is False
        quality = _compute_evidence_quality(finding, "/api/customers")
        formatted = _format_single_finding(finding)

        assert quality["can_reproduce"] is False
        assert formatted["bug_status"] != "reproduced"
        assert formatted["raw_evidence"]["has_real_evidence"] is False

    def test_runtime_call_status_string_counts_as_observed_response(self):
        finding = {
            "title": "runtime call evidence",
            "_api_path": "/api/accounts",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/accounts",
                        "results": {"admin": {"status": "500", "body": {"error": "failed"}}},
                    }
                ]
            },
        }

        assert _has_runtime_response(finding) is True
        assert _path_mismatch_reasons(finding) == []



class TestDisplayReadyCommercialContract:
    """Commercial delivery counts must come from display-ready findings, not raw totals."""

    def test_contract_separates_raw_candidates_from_materialized_display_risks(self):
        risks = [
            {
                "title": "text-only candidate should not be sold as reproduced bug",
                "status": "confirmed",
                "_api_path": "/api/orders",
                "expected_behavior": "expected state",
                "actual_behavior": "actual state",
                "evidence": {"source_file": "reasoner-output.json"},
            }
        ]
        raw_report = {"total_findings": 9, "value_metrics": {"evidence_trust_score": 98}}

        display_risks, metrics = format_findings_display_ready(risks, {}, raw_report)
        contract = metrics["display_contract"]

        assert len(display_risks) == 1
        assert contract["materialized_risk_count"] == 1
        assert contract["raw_candidate_risk_count"] == 9
        assert contract["raw_to_display_delta"] == 8
        assert contract["ready_bug_count"] == 0
        assert contract["needs_validation_count"] == 1
        assert metrics["scores"]["evidence_trust_score"] < 98

    def test_contract_counts_only_gate_passed_reproduced_as_ready_bug(self):
        risks = [
            {
                "title": "GET /api/orders returns 500 when list orders",
                "status": "confirmed",
                "_api_path": "/api/orders",
                "_api_method": "GET",
                "expected_behavior": "orders endpoint should return a stable list response",
                "actual_behavior": "GET /api/orders returned HTTP 500 with error body",
                "har_evidence": {
                    "method": "GET",
                    "path": "/api/orders",
                    "status_code": 500,
                    "response_body": '{"error":"database failed"}',
                },
                "evidence": {"method": "GET", "path": "/api/orders"},
            }
        ]

        display_risks, metrics = format_findings_display_ready(risks, {}, {"raw_total": 1})
        contract = metrics["display_contract"]

        assert display_risks[0]["bug_status"] == "reproduced"
        assert display_risks[0]["gate_passed"] is True
        assert contract["ready_bug_count"] == 1
        assert contract["needs_validation_count"] == 0


class TestCanonicalRuntimeObservationEvidence:
    """Deep evidence correctness: every display layer must use the same runtime row."""

    def test_runtime_call_evidence_populates_raw_evidence_and_assertions(self):
        finding = {
            "title": "GET /api/accounts returns server error",
            "status": "confirmed",
            "_api_method": "GET",
            "_api_path": "/api/accounts",
            "expected_behavior": "accounts endpoint should return a stable list response",
            "actual_behavior": "GET /api/accounts returned HTTP 500 with an error body",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/accounts",
                        "results": {
                            "admin": {
                                "status": "500",
                                "body": {"error": "database failed", "traceId": "tr-123"},
                                "duration_ms": 41,
                                "_request": {"url": "http://local.test/api/accounts"},
                            }
                        },
                    }
                ]
            },
        }

        formatted = _format_single_finding(finding)

        assert formatted["bug_status"] == "reproduced"
        assert formatted["raw_evidence"]["response_raw"]["status_code"] == 500
        assert formatted["raw_evidence"]["response_raw"]["source"] == "runtime_call"
        assert formatted["reproduction"]["har_evidence"]["source"] == "runtime_call"
        assert any(a["type"] == "http_status_error" for a in formatted["failed_assertions"])
        assert formatted["expected_actual_comparison"]["api_comparison"]["source"] == "runtime_call"

    def test_method_mismatch_downgrades_runtime_call_to_not_reproduced(self):
        finding = {
            "title": "POST /api/accounts returns server error",
            "status": "confirmed",
            "_api_method": "POST",
            "_api_path": "/api/accounts",
            "expected_behavior": "POST should create account successfully",
            "actual_behavior": "POST /api/accounts returned HTTP 500",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/accounts",
                        "results": {"admin": {"status": "500", "body": {"error": "boom"}}},
                    }
                ]
            },
        }

        formatted = _format_single_finding(finding)

        assert formatted["bug_status"] == "not_reproduced"
        assert formatted["raw_evidence"]["response_raw"] == {}
        assert formatted["reproduction"]["har_evidence"] is None
        assert any("method" in item for item in formatted["gate_failures"])

    def test_response_sanitizer_rejects_cached_method_mismatch(self):
        payload = {
            "title": "POST /api/accounts returns server error",
            "expected": "POST should create account successfully",
            "actual": "POST returned HTTP 500",
            "bug_status": "reproduced",
            "verdict": "confirmed",
            "gate_passed": True,
            "is_reproducible": True,
            "repro_method": "POST",
            "repro_path": "/api/accounts",
            "evidence_quality": {"score": 90, "can_reproduce": True, "verified": ["已捕获真实接口响应（状态码/响应体）"], "missing": []},
            "raw_evidence": {
                "request_raw": {"method": "GET", "path": "/api/accounts"},
                "response_raw": {"status_code": 500, "body": '{"error":"boom"}'},
                "has_real_evidence": True,
            },
            "reproduction": {"har_evidence": {"status_code": 500}},
            "proof": {"repro_rate": 100},
        }

        sanitized = sanitize_customer_evidence_payload(payload)

        assert sanitized["bug_status"] == "not_reproduced"
        assert sanitized["raw_evidence"]["response_raw"] == {}
        assert sanitized["reproduction"]["har_evidence"] is None
        assert sanitized["proof"]["repro_rate"] == 0
