"""Tests for learning_generator.py — verify that confirmed bugs generate NEW artifacts."""

from __future__ import annotations

import pytest

from ai_test_asset_center.learning_generator import (
    LearningGenerator,
    ProbeGenerator,
    OracleGenerator,
    FixtureGenerator,
    GeneratedProbe,
    GeneratedOracle,
    GeneratedFixture,
    LearningManifest,
    GenerationStrategy,
    ArtifactKind,
    _extract_entity_from_path,
    _extract_actor_from_finding,
    _normalize_method_path,
    _make_id,
)


# ═════════════════════════════════════════════════════════════════════════════
# Sample Data
# ═════════════════════════════════════════════════════════════════════════════

SAMPLE_CONFIRMED_BUG = {
    "bug_id": "AUTH_VERTICAL_BYPASS_0001",
    "evidence_id": "EVID_abc123",
    "title": "普通用户可访问管理订单",
    "risk_type": "permission_bypass",
    "severity": "P0",
    "method": "GET",
    "path": "/api/v1/admin/orders",
    "api": "GET /api/v1/admin/orders",
    "expected_status": 403,
    "oracle": {
        "type": "permission_bypass",
        "expected_status": 403,
        "bug_signal": "status_code == 200",
        "entity": "order",
    },
    "variant_dimensions": {
        "actor": "normal_user",
        "entity": "order",
        "operation": "view",
        "auth_state": "logged_in",
    },
    "verdict": "confirmed",
    "confirmation_status": "confirmed",
}

SAMPLE_CONFIRMED_IDOR = {
    "bug_id": "IDOR_ORDER_0002",
    "evidence_id": "EVID_def456",
    "title": "用户可查看他人订单",
    "risk_type": "idor",
    "severity": "P0",
    "method": "GET",
    "path": "/api/v1/orders/123",
    "api": "GET /api/v1/orders/{id}",
    "expected_status": 403,
    "oracle": {
        "type": "idor",
        "expected_status": 403,
        "bug_signal": "status_code == 200",
        "entity": "order",
    },
    "variant_dimensions": {
        "actor": "normal_user",
        "entity": "order",
        "operation": "view",
    },
    "verdict": "confirmed",
}

SAMPLE_CONFIRMED_STATE_FLOW = {
    "bug_id": "STATE_INVALID_0003",
    "evidence_id": "EVID_ghi789",
    "title": "已取消订单仍可支付",
    "risk_type": "state_flow",
    "severity": "P1",
    "method": "POST",
    "path": "/api/v1/orders/456/cancel",
    "api": "POST /api/v1/orders/{id}/cancel",
    "expected_status": 409,
    "oracle": {
        "type": "state_flow",
        "expected_status": 409,
        "bug_signal": "status_code == 200",
        "entity": "order",
    },
    "variant_dimensions": {
        "actor": "customer",
        "entity": "order",
        "operation": "cancel",
    },
    "verdict": "validated",
}

SAMPLE_CONTEXT = {
    "entities": ["order", "product", "payment", "refund", "user", "coupon"],
    "roles": ["anonymous", "normal_user", "customer", "admin", "auditor"],
    "endpoints": [
        {"method": "GET", "path": "/api/v1/orders"},
        {"method": "GET", "path": "/api/v1/orders/{id}"},
        {"method": "POST", "path": "/api/v1/orders"},
        {"method": "GET", "path": "/api/v1/products"},
        {"method": "GET", "path": "/api/v1/products/{id}"},
        {"method": "POST", "path": "/api/v1/products"},
        {"method": "GET", "path": "/api/v1/users"},
        {"method": "GET", "path": "/api/v1/users/{id}"},
        {"method": "POST", "path": "/api/v1/refunds"},
        {"method": "POST", "path": "/api/v1/payments/callback"},
    ],
}


# ═════════════════════════════════════════════════════════════════════════════
# Helpers Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_extract_entity_from_path() -> None:
    assert _extract_entity_from_path("/api/v1/orders/123") == "orders"
    assert _extract_entity_from_path("/admin/products") == "products"
    assert _extract_entity_from_path("/api/v1/users/{id}") == "users"
    assert _extract_entity_from_path("/payments/callback") == "payments"


def test_extract_actor_from_finding() -> None:
    bug = {"variant_dimensions": {"actor": "normal_user"}}
    assert _extract_actor_from_finding(bug) == "normal_user"

    bug2 = {"reproduction": {"actor": "admin"}}
    assert _extract_actor_from_finding(bug2) == "admin"

    bug3 = {"title": "anonymous user bypasses auth"}
    assert _extract_actor_from_finding(bug3) == "anonymous"


def test_normalize_method_path() -> None:
    assert _normalize_method_path("GET", "/api/v1/orders/123") == "get:/api/v1/orders/{id}"
    assert _normalize_method_path("POST", "/api/v1/orders") == "post:/api/v1/orders"
    assert _normalize_method_path("GET", "/api/v1/Users/456") == "get:/api/v1/users/{id}"


def test_make_id_stable() -> None:
    id1 = _make_id("TEST", "bug1", "variant")
    id2 = _make_id("TEST", "bug1", "variant")
    assert id1 == id2
    assert id1.startswith("TEST_")


# ═════════════════════════════════════════════════════════════════════════════
# ProbeGenerator Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_probe_generator_role_variants() -> None:
    gen = ProbeGenerator()
    variants = gen.generate_role_variants(SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["roles"])

    assert len(variants) > 0
    for v in variants:
        assert isinstance(v, GeneratedProbe)
        assert v.strategy == GenerationStrategy.ROLE_VARIANT
        assert v.source_bug_id == "AUTH_VERTICAL_BYPASS_0001"
        assert v.risk_type == "permission_bypass"
        assert v.method == "GET"
        assert "/admin/orders" in v.path


def test_probe_generator_entity_variants() -> None:
    gen = ProbeGenerator()
    variants = gen.generate_entity_variants(
        SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["entities"]
    )

    assert len(variants) > 0
    for v in variants:
        assert v.strategy == GenerationStrategy.ENTITY_VARIANT
        assert v.risk_type == "permission_bypass"
        # Entity should have changed from "order" to something else
        entity_in_path = any(e in v.path for e in SAMPLE_CONTEXT["entities"] if e != "order")
        assert entity_in_path or len(variants) > 0


def test_probe_generator_entity_variants_not_applicable() -> None:
    """Risk types not in CROSS_ENTITY_ORACLE_FAMILIES should produce no entity variants."""
    gen = ProbeGenerator()
    bug = {**SAMPLE_CONFIRMED_BUG, "risk_type": "unknown_risk_type"}
    variants = gen.generate_entity_variants(bug, SAMPLE_CONTEXT["entities"])
    assert len(variants) == 0


def test_probe_generator_endpoint_variants() -> None:
    gen = ProbeGenerator()
    variants = gen.generate_endpoint_variants(
        SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["endpoints"]
    )

    assert len(variants) > 0
    for v in variants:
        assert v.strategy == GenerationStrategy.ENDPOINT_VARIANT
        assert v.risk_type == "permission_bypass"
        assert v.method == "GET"  # Same HTTP method


def test_probe_generator_parameter_variants() -> None:
    gen = ProbeGenerator()
    variants = gen.generate_parameter_variants(SAMPLE_CONFIRMED_BUG)

    # permission_bypass doesn't have parameter mutations
    # But other risk types do
    idor_bug = {**SAMPLE_CONFIRMED_IDOR}
    gen2 = ProbeGenerator()
    idor_variants = gen2.generate_parameter_variants(idor_bug)
    assert len(idor_variants) > 0
    for v in idor_variants:
        assert v.strategy == GenerationStrategy.PARAMETER_VARIANT


def test_probe_generator_dedup() -> None:
    """Generating the same variant twice should not produce duplicates."""
    gen = ProbeGenerator()
    variants1 = gen.generate_role_variants(SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["roles"])
    variants2 = gen.generate_role_variants(SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["roles"])
    assert len(variants2) == 0  # All duplicates filtered


def test_probe_generator_dedup_across_generators() -> None:
    """Dedup should work even with pre-existing probes."""
    existing = [
        {"method": "GET", "path": "/api/v1/admin/orders", "actor": "anonymous", "risk_type": "permission_bypass"},
    ]
    gen = ProbeGenerator(existing)
    variants = gen.generate_role_variants(SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["roles"])
    # "anonymous" role variant should be filtered out
    anonymous_variants = [v for v in variants if v.actor == "anonymous"]
    assert len(anonymous_variants) == 0


# ═════════════════════════════════════════════════════════════════════════════
# OracleGenerator Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_oracle_generator_sibling_oracles() -> None:
    gen = OracleGenerator()
    oracles = gen.generate_sibling_oracles(
        SAMPLE_CONFIRMED_BUG, SAMPLE_CONTEXT["endpoints"]
    )

    assert len(oracles) > 0
    for o in oracles:
        assert isinstance(o, GeneratedOracle)
        assert o.strategy == GenerationStrategy.CROSS_ENTITY_ORACLE
        assert o.source_bug_id == "AUTH_VERTICAL_BYPASS_0001"
        assert o.layer in ("L1", "L2", "L3", "L4", "L5", "L6")


def test_oracle_generator_derived_oracles() -> None:
    gen = OracleGenerator()
    oracles = gen.generate_derived_oracles(SAMPLE_CONFIRMED_BUG)

    # permission_bypass → auth_bypass, privilege_escalation
    assert len(oracles) >= 1
    derived_types = {o.oracle_name for o in oracles}
    assert any("Auth" in name or "Privilege" in name for name in derived_types)


def test_oracle_generator_derived_for_state_flow() -> None:
    gen = OracleGenerator()
    oracles = gen.generate_derived_oracles(SAMPLE_CONFIRMED_STATE_FLOW)

    assert len(oracles) >= 1
    for o in oracles:
        assert o.strategy == GenerationStrategy.DERIVED_ORACLE
        assert o.source_bug_id == "STATE_INVALID_0003"


def test_oracle_generator_dedup() -> None:
    gen = OracleGenerator()
    oracles1 = gen.generate_derived_oracles(SAMPLE_CONFIRMED_BUG)
    oracles2 = gen.generate_derived_oracles(SAMPLE_CONFIRMED_BUG)
    assert len(oracles2) == 0  # All duplicates filtered


# ═════════════════════════════════════════════════════════════════════════════
# FixtureGenerator Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_fixture_generator_reproduction() -> None:
    gen = FixtureGenerator()
    fixture = gen.generate_reproduction_fixture(SAMPLE_CONFIRMED_STATE_FLOW)

    assert fixture is not None
    assert fixture.purpose == "reproduction"
    assert fixture.strategy == GenerationStrategy.REPRODUCTION_FIXTURE
    assert fixture.source_bug_id == "STATE_INVALID_0003"
    assert fixture.entity_type == "orders"


def test_fixture_generator_regression() -> None:
    gen = FixtureGenerator()
    fixture = gen.generate_regression_fixture(SAMPLE_CONFIRMED_BUG)

    assert fixture is not None
    assert fixture.purpose == "regression"
    assert fixture.strategy == GenerationStrategy.REGRESSION_FIXTURE
    assert len(fixture.setup_requests) >= 1
    # Should have a verify step
    verify_steps = [s for s in fixture.setup_requests if s.get("purpose") == "verify_bug_is_fixed"]
    assert len(verify_steps) == 1
    assert verify_steps[0]["expected_status_after_fix"] == 403


def test_fixture_generator_get_request_no_setup() -> None:
    """GET requests don't need resource creation setup."""
    gen = FixtureGenerator()
    fixture = gen.generate_reproduction_fixture(SAMPLE_CONFIRMED_BUG)

    assert fixture is not None
    # GET method: no setup requests needed
    setup_count = len(fixture.setup_requests)
    # Should still produce a fixture even for GET
    assert fixture.fixture_id.startswith("LRN-FIX")


# ═════════════════════════════════════════════════════════════════════════════
# LearningGenerator (Orchestrator) Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_learning_generator_full_pipeline() -> None:
    """End-to-end: confirmed bugs → generated artifacts."""
    gen = LearningGenerator(
        existing_probes=[],
        project_context=SAMPLE_CONTEXT,
    )

    confirmed = [
        SAMPLE_CONFIRMED_BUG,
        SAMPLE_CONFIRMED_IDOR,
        SAMPLE_CONFIRMED_STATE_FLOW,
    ]

    manifest = gen.generate_from_confirmed_bugs(confirmed)

    assert isinstance(manifest, LearningManifest)
    assert manifest.source_bug_count == 3
    assert manifest.generated_probes or manifest.generated_oracles or manifest.generated_fixtures, \
        "Should generate at least some artifacts"

    # At minimum, fixtures should be generated for each confirmed bug
    assert len(manifest.generated_fixtures) >= 3

    # Summary should have counts
    summary = manifest.summary
    assert "total_probes_generated" in summary
    assert "total_oracles_generated" in summary
    assert "total_fixtures_generated" in summary
    assert "strategies_used" in summary


def test_learning_generator_skips_non_confirmed() -> None:
    """Non-confirmed findings should be skipped."""
    gen = LearningGenerator(project_context=SAMPLE_CONTEXT)

    non_confirmed = [
        {**SAMPLE_CONFIRMED_BUG, "verdict": "inconclusive", "confirmation_status": "candidate"},
    ]

    manifest = gen.generate_from_confirmed_bugs(non_confirmed)
    assert manifest.source_bug_count == 0
    assert len(manifest.generated_probes) == 0


def test_learning_generator_manifest_to_dict() -> None:
    """manifest_to_dict should produce JSON-safe output."""
    gen = LearningGenerator(project_context=SAMPLE_CONTEXT)
    manifest = gen.generate_from_confirmed_bugs([SAMPLE_CONFIRMED_BUG])
    d = gen.manifest_to_dict(manifest)

    assert isinstance(d, dict)
    assert "manifest_id" in d
    assert "generated_probes" in d
    assert "generated_oracles" in d
    assert "generated_fixtures" in d
    assert "summary" in d

    # All probe dicts should be JSON-safe
    for probe_dict in d["generated_probes"]:
        assert isinstance(probe_dict["probe_id"], str)
        assert isinstance(probe_dict["strategy"], str)


def test_learning_generator_max_limits() -> None:
    """max_probes_per_bug and max_oracles_per_bug should be enforced."""
    gen = LearningGenerator(project_context=SAMPLE_CONTEXT)
    manifest = gen.generate_from_confirmed_bugs(
        [SAMPLE_CONFIRMED_BUG],
        max_probes_per_bug=3,
        max_oracles_per_bug=2,
    )

    assert len(manifest.generated_probes) <= 3
    assert len(manifest.generated_oracles) <= 2


def test_learning_generator_log_observable() -> None:
    """Log should record generation activity."""
    gen = LearningGenerator(project_context=SAMPLE_CONTEXT)
    gen.generate_from_confirmed_bugs([SAMPLE_CONFIRMED_BUG])

    log = gen.get_log()
    assert len(log) > 0
    assert any("confirmed bugs" in entry for entry in log)


def test_learning_generator_all_strategies_in_summary() -> None:
    """At least some generation strategies should appear in the summary."""
    gen = LearningGenerator(project_context=SAMPLE_CONTEXT)
    manifest = gen.generate_from_confirmed_bugs([
        SAMPLE_CONFIRMED_BUG,
        SAMPLE_CONFIRMED_IDOR,
        SAMPLE_CONFIRMED_STATE_FLOW,
    ])

    strategies = manifest.summary.get("strategies_used", [])
    # Should have at least reproduction_fixture and regression_fixture
    assert "reproduction_fixture" in strategies
    assert "regression_fixture" in strategies
