"""QualiBug End-to-End Live Discovery Integration Test.

This test:
1. Starts a live test target server with known bugs
2. Runs the QualiBug discovery pipeline against it
3. Verifies that ready_bugs are found with complete evidence
4. Verifies that internal clues are separated from customer-facing data.risks

This is the definitive integration test proving that QualiBug can:
- Find real bugs
- Reproduce them (real HTTP requests with HAR evidence)
- Generate complete evidence chains
- Gate correctly (only ready_bug → data.risks)
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import test target and pipeline
from tests.live_target.test_target_server import serve as _serve_target
from tests.live_target.run_live_discovery import run_discovery_pipeline


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def live_target():
    """Start the test target server on a random port."""
    import random
    port = random.randint(10000, 20000)
    t = threading.Thread(target=_serve_target, args=(port, ":memory:"), daemon=True)
    t.start()
    time.sleep(1)  # Wait for server to start
    url = f"http://127.0.0.1:{port}"
    yield url
    # Thread is daemon, will exit when test ends


@pytest.fixture(scope="module")
def discovery_report(live_target):
    """Run discovery pipeline and return the report."""
    return run_discovery_pipeline(live_target)


# ══════════════════════════════════════════════════════════════
# Tests: Pipeline produces valid output
# ══════════════════════════════════════════════════════════════

class TestPipelineProducesReadyBugs:
    """QualiBug must find real bugs with complete evidence chains."""

    def test_pipeline_finds_at_least_2_ready_bugs(self, discovery_report):
        contract = discovery_report["data_contract"]
        assert contract["ready_bug_count"] >= 2, \
            f"Expected at least 2 ready bugs, got {contract['ready_bug_count']}"

    def test_all_ready_bugs_have_gate_passed_true(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert bug["gate_passed"] is True, \
                f"Bug '{bug['title']}' has gate_passed=False"

    def test_all_ready_bugs_have_is_reproducible_true(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert bug["is_reproducible"] is True, \
                f"Bug '{bug['title']}' has is_reproducible=False"

    def test_all_ready_bugs_have_failed_assertions(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert len(bug["failed_assertions"]) >= 1, \
                f"Bug '{bug['title']}' has no failed_assertions"

    def test_all_ready_bugs_have_reproduction_steps(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert len(bug["reproduction_steps"]) >= 1, \
                f"Bug '{bug['title']}' has no reproduction_steps"

    def test_all_ready_bugs_have_evidence_refs(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert len(bug.get("evidence_refs", [])) >= 1, \
                f"Bug '{bug['title']}' has no evidence_refs"

    def test_all_ready_bugs_have_har_evidence(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert bug["has_har_evidence"] is True, \
                f"Bug '{bug['title']}' has no HAR evidence"

    def test_all_ready_bugs_have_request_details(self, discovery_report):
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert bug.get("request_method"), f"Bug '{bug['title']}' missing method"
            assert bug.get("request_path"), f"Bug '{bug['title']}' missing path"
            assert bug.get("response_status"), f"Bug '{bug['title']}' missing status"

    def test_disabled_user_login_bug_found(self, discovery_report):
        """AUTH-001: disabled user login bug must be found."""
        titles = [b["title"] for b in discovery_report["data_contract"]["ready_bugs"]]
        assert any("禁用" in t and "登录" in t for t in titles), \
            f"AUTH-001 (disabled user login) not found. Titles: {titles}"

    def test_cancelled_order_payment_bug_found(self, discovery_report):
        """ORDER-001: cancelled order payment bug must be found."""
        titles = [b["title"] for b in discovery_report["data_contract"]["ready_bugs"]]
        assert any("取消" in t and "支付" in t for t in titles), \
            f"ORDER-001 (cancelled order pay) not found. Titles: {titles}"

    def test_light_password_bug_found(self, discovery_report):
        """AUTH-004: weak password bug must be found."""
        titles = [b["title"] for b in discovery_report["data_contract"]["ready_bugs"]]
        assert any("弱密码" in t or "密码" in t for t in titles), \
            f"AUTH-004 (weak password) not found. Titles: {titles}"

    def test_negative_quantity_bug_found(self, discovery_report):
        """PARAM-001: negative quantity bug must be found."""
        titles = [b["title"] for b in discovery_report["data_contract"]["ready_bugs"]]
        assert any("负数" in t or "负" in t for t in titles), \
            f"PARAM-001 (negative quantity) not found. Titles: {titles}"


class TestPipelineSeparatesClues:
    """Internal clues must NOT be in customer-facing data.risks."""

    def test_internal_clues_not_in_ready_bugs(self, discovery_report):
        ready_titles = {b["title"] for b in discovery_report["data_contract"]["ready_bugs"]}
        for clue in discovery_report["data_contract"].get("internal_clues", []):
            assert clue["title"] not in ready_titles, \
                f"Clue '{clue['title']}' also appears in ready_bugs!"

    def test_internal_clues_have_failed_gates(self, discovery_report):
        for clue in discovery_report["data_contract"].get("internal_clues", []):
            assert clue.get("verifier_verdict") != "validated_bug", \
                f"Clue '{clue['title']}' has validated_bug but is in internal_clues!"

    def test_total_issues_exceed_ready_bugs(self, discovery_report):
        contract = discovery_report["data_contract"]
        assert contract["materialized_risk_count"] > contract["ready_bug_count"], \
            "Total issues should exceed ready_bugs (some should be filtered)"


class TestHAREvidence:
    """HAR evidence must be real and traceable."""

    def test_har_entries_exist(self, discovery_report):
        assert len(discovery_report["har_entries"]) > 0, \
            "No HAR entries in the discovery report"

    def test_har_entries_have_request_and_response(self, discovery_report):
        for entry in discovery_report["har_entries"]:
            assert "request" in entry, "HAR entry missing request"
            assert "response" in entry, "HAR entry missing response"
            assert entry["response"].get("status") is not None, "HAR entry missing status"

    def test_har_entries_cover_all_ready_bugs(self, discovery_report):
        """Every ready_bug must reference at least one HAR entry."""
        # All ready_bugs should have has_har_evidence = True (validated above)
        for bug in discovery_report["data_contract"]["ready_bugs"]:
            assert bug["has_har_evidence"] is True
