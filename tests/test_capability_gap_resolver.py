"""Tests for capability_gap_resolver.py and gap_tracker.py."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ai_test_asset_center.capability_gap_resolver import (
    CapabilityGapResolver,
    GapRootCause,
    GapResolution,
    CapabilityGap,
    ConfigTask,
    ResolutionResult,
    CHECK_TO_ROOT_CAUSE,
    ROOT_CAUSE_RESOLUTION,
    CONFIG_TASK_TEMPLATES,
    FAMILY_REQUIRED_CAPABILITIES,
)
from ai_test_asset_center.gap_tracker import (
    GapTracker,
    GapState,
    GapRecord,
    GapSnapshot,
)


# ═════════════════════════════════════════════════════════════════════════════
# Gap Resolver Tests
# ═════════════════════════════════════════════════════════════════════════════

def _make_preflight(checks: list[dict]) -> dict:
    """Helper to create a preflight-like dict."""
    return {"checks": checks, "reasons": []}


def _make_check(name: str, ok: bool, severity: str = "blocking", message: str = "") -> dict:
    """Helper to create a check dict."""
    return {
        "name": name,
        "ok": ok,
        "status": "passed" if ok else "failed",
        "severity": severity,
        "message": message or f"Check {name}: {'ok' if ok else 'failed'}",
    }


def test_detect_from_preflight_no_gaps() -> None:
    """When all checks pass, no gaps should be detected."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", True),
        _make_check("auth_session_ready", True),
        _make_check("non_production_target", True),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    assert len(gaps) == 0


def test_detect_from_preflight_missing_base_url() -> None:
    """Missing base_url should produce a MISSING_BASE_URL gap."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", False, "blocking", "No base URL"),
        _make_check("auth_session_ready", True),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    assert len(gaps) == 1
    assert gaps[0].root_cause == GapRootCause.MISSING_BASE_URL
    assert gaps[0].resolution == GapResolution.NEEDS_CUSTOMER_CONFIG
    assert gaps[0].priority == "P0"


def test_detect_from_preflight_multiple_auth_checks_merge() -> None:
    """Multiple auth-related failing checks should merge into one gap."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", True),
        _make_check("auth_session_ready", False, "warning"),
        _make_check("token_cookie_or_session_acquired", False, "warning"),
        _make_check("session_health_verified", False, "warning"),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    auth_gaps = [g for g in gaps if g.root_cause == GapRootCause.MISSING_AUTH]
    assert len(auth_gaps) == 1
    # All three check names should be merged
    assert len(auth_gaps[0].preflight_check_names) == 3


def test_detect_from_preflight_production_target() -> None:
    """Production target should produce a PERMANENTLY_BLOCKED gap."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", True),
        _make_check("non_production_target", False, "blocking", "Production URL detected"),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    prod_gaps = [g for g in gaps if g.root_cause == GapRootCause.PRODUCTION_TARGET]
    assert len(prod_gaps) == 1
    assert prod_gaps[0].resolution == GapResolution.PERMANENTLY_BLOCKED


def test_detect_from_preflight_config_task_generated() -> None:
    """Each detected gap should have a valid config task."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", False, "blocking", "Missing URL"),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    assert len(gaps) == 1
    assert gaps[0].config_task is not None
    assert gaps[0].config_task.task_id == "CFG-missing_base_url"
    assert len(gaps[0].config_task.config_keys) > 0
    assert len(gaps[0].config_task.validation_steps) > 0


def test_detect_from_scan_preflight_reasons() -> None:
    """Scan-level preflight reasons should be detected."""
    resolver = CapabilityGapResolver("test")
    reasons = [
        {"code": "NO_CREDENTIALS", "message": "No credentials configured"},
        {"code": "NO_API_SPEC", "message": "No API spec uploaded"},
    ]
    gaps = resolver.detect_from_scan_preflight(reasons)
    assert len(gaps) == 2
    root_causes = {g.root_cause for g in gaps}
    assert GapRootCause.NO_CREDENTIALS in root_causes
    assert GapRootCause.NO_API_SPEC in root_causes


def test_generate_config_tasks() -> None:
    """generate_config_tasks should produce sorted, deduplicated tasks."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", False, "blocking"),
        _make_check("auth_session_ready", False, "warning"),
        _make_check("non_production_target", False, "blocking"),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    tasks = resolver.generate_config_tasks(gaps)
    assert len(tasks) >= 2
    # P0 tasks should come first
    priorities = [t.priority for t in tasks]
    assert priorities == sorted(priorities, key=lambda p: {"P0": 0, "P1": 1, "P2": 2}.get(p, 2))


def test_generate_config_tasks_dedup() -> None:
    """Duplicate root causes should not produce duplicate tasks."""
    resolver = CapabilityGapResolver("test")
    preflight = _make_preflight([
        _make_check("base_url_configured", False),
        _make_check("url_parse_ok", False),  # Same root cause
    ])
    gaps = resolver.detect_from_preflight(preflight)
    tasks = resolver.generate_config_tasks(gaps)
    task_ids = [t.task_id for t in tasks]
    assert len(task_ids) == len(set(task_ids)), "Duplicate task IDs found"


def test_try_auto_resolve_non_auto_gap() -> None:
    """Non-auto-resolvable gaps should not be resolved."""
    resolver = CapabilityGapResolver("test")
    gap = CapabilityGap(
        gap_id="GAP-test-1", root_cause=GapRootCause.MISSING_BASE_URL,
        resolution=GapResolution.NEEDS_CUSTOMER_CONFIG, priority="P0",
        summary="Missing base URL",
    )
    preflight = _make_preflight([_make_check("base_url_configured", True)])
    result = resolver.try_auto_resolve(gap, preflight)
    assert result.resolved is False
    assert "cannot auto-resolve" in result.reason


def test_try_auto_resolve_document_grounding() -> None:
    """DOCUMENT_GROUNDING is auto-resolvable when checks pass."""
    resolver = CapabilityGapResolver("test")
    gap = CapabilityGap(
        gap_id="GAP-test-2", root_cause=GapRootCause.DOCUMENT_GROUNDING,
        resolution=GapResolution.AUTO_RESOLVABLE, priority="P1",
        summary="Document grounding needed",
        preflight_check_names=["probe_plan_grounded"],
    )
    # Check now passes
    preflight = _make_preflight([_make_check("probe_plan_grounded", True)])
    result = resolver.try_auto_resolve(gap, preflight)
    assert result.resolved is True


def test_build_gap_report() -> None:
    """build_gap_report should produce a valid JSON-safe dict."""
    resolver = CapabilityGapResolver("test_proj")
    preflight = _make_preflight([
        _make_check("base_url_configured", False, "blocking"),
        _make_check("auth_session_ready", False, "warning"),
    ])
    gaps = resolver.detect_from_preflight(preflight)
    report = resolver.build_gap_report(gaps)

    assert report["schema_version"] == "capability_gap_report.v1"
    assert report["total_gaps"] == 2
    assert "by_resolution_type" in report
    assert "by_root_cause" in report
    assert "config_tasks" in report
    assert len(report["config_tasks"]) >= 1


def test_all_root_causes_have_resolution() -> None:
    """Every GapRootCause should have a corresponding GapResolution."""
    for cause in GapRootCause:
        assert cause in ROOT_CAUSE_RESOLUTION, f"Missing resolution for {cause}"


def test_all_check_names_have_root_cause() -> None:
    """All known preflight check names should map to a root cause."""
    known_checks = [
        "base_url_configured", "url_parse_ok", "url_host_resolves",
        "base_url_reachable", "non_production_target", "probe_plan_grounded",
        "auth_session_ready", "service_credentials_verified",
        "interactive_auth_not_blocked", "token_cookie_or_session_acquired",
        "session_health_verified", "authenticated_api_smoke_verified",
        "auth_session_refresh_ready", "role_coverage",
        "auto_fixture_create_permission", "cleanup_health_declared",
        "snapshot_observer_ready", "config_placeholders_resolved",
    ]
    for check_name in known_checks:
        assert check_name in CHECK_TO_ROOT_CAUSE, f"Missing root cause mapping for check: {check_name}"


def test_all_root_causes_have_config_task() -> None:
    """Every root cause that can be configured should have a config task template."""
    needs_task = {
        GapRootCause.MISSING_BASE_URL, GapRootCause.MISSING_AUTH,
        GapRootCause.MISSING_OPENAPI, GapRootCause.MISSING_TEST_ACCOUNTS,
        GapRootCause.PRODUCTION_TARGET, GapRootCause.WRITE_SANDBOX_MISSING,
        GapRootCause.CLEANUP_MISSING, GapRootCause.SNAPSHOT_MISSING,
        GapRootCause.CONFIG_PLACEHOLDERS, GapRootCause.BROWSER_DISABLED,
        GapRootCause.TESTOPS_OFFLINE, GapRootCause.NO_CREDENTIALS,
        GapRootCause.NO_SOURCE, GapRootCause.NO_API_SPEC,
    }
    for cause in needs_task:
        assert cause in CONFIG_TASK_TEMPLATES, f"Missing config task template for {cause}"


def test_family_required_capabilities_non_empty() -> None:
    """Every defect family should have at least one required capability."""
    for family, caps in FAMILY_REQUIRED_CAPABILITIES.items():
        assert len(caps) > 0, f"Family '{family}' has no required capabilities"


def test_get_auto_resolvable_gaps() -> None:
    """get_auto_resolvable_gaps should filter correctly."""
    resolver = CapabilityGapResolver("test")
    gaps = [
        CapabilityGap("G1", GapRootCause.DOCUMENT_GROUNDING, GapResolution.AUTO_RESOLVABLE, "P1", ""),
        CapabilityGap("G2", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", ""),
    ]
    auto = resolver.get_auto_resolvable_gaps(gaps)
    assert len(auto) == 1
    assert auto[0].root_cause == GapRootCause.DOCUMENT_GROUNDING


def test_get_blocking_gaps() -> None:
    """get_blocking_gaps should filter P0 gaps."""
    resolver = CapabilityGapResolver("test")
    gaps = [
        CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", ""),
        CapabilityGap("G2", GapRootCause.MISSING_AUTH, GapResolution.NEEDS_CUSTOMER_CONFIG, "P1", ""),
    ]
    blocking = resolver.get_blocking_gaps(gaps)
    assert len(blocking) == 1
    assert blocking[0].priority == "P0"


# ═════════════════════════════════════════════════════════════════════════════
# Gap Tracker Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_tracker_init_empty() -> None:
    """New tracker should have no gaps."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        snapshot = tracker.current_snapshot()
        assert snapshot.total_gaps == 0
        assert snapshot.open_count == 0


def test_tracker_record_new_gaps() -> None:
    """Recording new gaps should add them to state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gaps = [
            CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL",
                          config_task=ConfigTask("CFG-1", "P0", "Fix URL", "", [], {}, [], "minutes")),
        ]
        result = tracker.record_gaps(gaps)
        assert result["new_gaps"] == 1
        assert result["total_tracked"] == 1


def test_tracker_record_same_gap_again() -> None:
    """Recording the same gap again should not duplicate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL")
        tracker.record_gaps([gap])
        result = tracker.record_gaps([gap])
        assert result["new_gaps"] == 0
        assert result["updated_gaps"] == 1


def test_tracker_gap_resolved_when_no_longer_detected() -> None:
    """When a gap is no longer detected, it should auto-resolve (with force_resolve=True)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL")
        tracker.record_gaps([gap])

        # Record empty list with force_resolve — gap should resolve
        result = tracker.record_gaps([], force_resolve=True)
        assert result["resolved_gaps"] == 1

        open_gaps = tracker.get_open_gaps()
        assert len(open_gaps) == 0


def test_tracker_gap_reopened() -> None:
    """A resolved gap that reappears should be marked reopened."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL")

        # Record, then resolve with force_resolve
        tracker.record_gaps([gap])
        tracker.record_gaps([], force_resolve=True)
        assert len(tracker.get_open_gaps()) == 0

        # Record again — should reopen
        result = tracker.record_gaps([gap])
        assert result["updated_gaps"] >= 1
        reopened = tracker.get_reopened_gaps()
        assert len(reopened) == 1


def test_tracker_mark_resolved() -> None:
    """Manual mark_resolved should work."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL")
        tracker.record_gaps([gap])
        assert tracker.mark_resolved("G1") is True
        resolved = tracker.get_resolved_gaps()
        assert len(resolved) == 1


def test_tracker_mark_blocked() -> None:
    """Marking a gap as blocked should work."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.PRODUCTION_TARGET, GapResolution.PERMANENTLY_BLOCKED, "P0", "Production")
        tracker.record_gaps([gap])
        assert tracker.mark_blocked("G1", "Cannot use production") is True
        snapshot = tracker.current_snapshot()
        assert snapshot.blocked_count == 1


def test_tracker_state_persists() -> None:
    """Gap state should persist across tracker instances."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker1 = GapTracker("test_proj", root=tmpdir)
        gap = CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL")
        tracker1.record_gaps([gap])

        # New instance should load persisted state
        tracker2 = GapTracker("test_proj", root=tmpdir)
        snapshot = tracker2.current_snapshot()
        assert snapshot.total_gaps == 1
        assert snapshot.open_count == 1


def test_tracker_build_summary() -> None:
    """build_summary should produce a complete JSON-safe dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = GapTracker("test_proj", root=tmpdir)
        gaps = [
            CapabilityGap("G1", GapRootCause.MISSING_BASE_URL, GapResolution.NEEDS_CUSTOMER_CONFIG, "P0", "Missing URL"),
            CapabilityGap("G2", GapRootCause.MISSING_AUTH, GapResolution.NEEDS_CUSTOMER_CONFIG, "P1", "Missing auth"),
        ]
        tracker.record_gaps(gaps)
        summary = tracker.build_summary()

        assert summary["currently_open"] == 2
        assert summary["total_gaps_ever"] == 2
        assert "open_gaps" in summary
        assert "by_root_cause" in summary
        assert len(summary["open_gaps"]) == 2
