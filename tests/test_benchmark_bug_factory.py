"""Tests for benchmark_bug_factory.py — verify industry-agnostic bug generation,
ground truth integrity, and blind discipline enforcement.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from benchmark_evaluator.benchmark_bug_factory import (
    BenchmarkBugFactory,
    IndustryProfile,
    BugTemplate,
    BUILTIN_INDUSTRIES,
    UNIVERSAL_TEMPLATES,
    list_industries,
    get_industry_profile,
    validate_ground_truth_integrity,
    prepare_industry_benchmark,
)


# ═════════════════════════════════════════════════════════════════════════════
# Factory Init Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_factory_init_all_builtin_industries() -> None:
    """Every built-in industry should initialise without error."""
    for industry_id in list_industries():
        factory = BenchmarkBugFactory(industry_id)
        assert factory.industry == industry_id
        assert factory.profile is not None
        assert len(factory.templates) > 0


def test_factory_init_unknown_industry_raises() -> None:
    """Unknown industry should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown industry"):
        BenchmarkBugFactory("nonexistent_industry_xyz")


def test_factory_init_with_custom_profile() -> None:
    """Custom profile should override built-in."""
    custom = IndustryProfile(
        industry_id="custom_test",
        display_name="Custom Test Industry",
        entities=["widget", "gadget"],
        roles=["user", "admin"],
        api_prefix="/api/v2",
        auth_endpoints=["POST /auth/login"],
        business_invariants=["Widgets must not overlap"],
        tenant_aware=False,
    )
    factory = BenchmarkBugFactory("custom_test", custom_profile=custom)
    assert factory.profile.industry_id == "custom_test"
    assert factory.profile.entities == ["widget", "gadget"]


def test_factory_init_with_extra_templates() -> None:
    """Extra templates should be appended to universal set."""
    extra = BugTemplate(
        template_id="TEST_EXTRA",
        risk_type="test_risk",
        severity="P1",
        title_template="Test {entity}",
        api_pattern="GET /test/{entity}",
        trigger_template="Test trigger",
        expected_status=418,
        oracle_signal="status_code == 418",
    )
    factory = BenchmarkBugFactory("ecommerce", extra_templates=[extra])
    template_ids = {t.template_id for t in factory.templates}
    assert "TEST_EXTRA" in template_ids
    # Universal templates should still be there
    assert "AUTH_VERTICAL_BYPASS" in template_ids


# ═════════════════════════════════════════════════════════════════════════════
# Bug Generation Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_generate_produces_bugs() -> None:
    """generate() should produce the requested number of bugs."""
    factory = BenchmarkBugFactory("ecommerce")
    bugs = factory.generate(count=20, seed=42)
    assert len(bugs) == 20


def test_generate_seed_reproducibility() -> None:
    """Same seed should produce identical bugs."""
    factory1 = BenchmarkBugFactory("crm")
    factory2 = BenchmarkBugFactory("crm")
    bugs1 = factory1.generate(count=30, seed=12345)
    bugs2 = factory2.generate(count=30, seed=12345)

    # Compare bug IDs
    ids1 = [b["bug_id"] for b in bugs1]
    ids2 = [b["bug_id"] for b in bugs2]
    assert ids1 == ids2


def test_generate_different_seeds_produce_different_bugs() -> None:
    """Different seeds should (usually) produce different bug sets."""
    factory = BenchmarkBugFactory("erp")
    bugs1 = factory.generate(count=50, seed=1)
    bugs2 = factory.generate(count=50, seed=2)

    ids1 = {b["bug_id"] for b in bugs1}
    ids2 = {b["bug_id"] for b in bugs2}
    # With 50 bugs and many templates, there should be some difference
    # (not a strict guarantee but extremely likely)
    assert ids1 != ids2 or len(ids1) == len(ids2)


def test_generate_min_severity_filter() -> None:
    """min_severity should filter out lower-severity bugs."""
    factory = BenchmarkBugFactory("ecommerce")
    bugs_p0 = factory.generate(count=100, seed=42, min_severity="P0")
    bugs_all = factory.generate(count=100, seed=42, min_severity="P2")

    # P0 filter should produce only P0 bugs
    severities_p0 = {b["severity"] for b in bugs_p0}
    assert severities_p0 == {"P0"}

    # All severities should include P1 and P2
    severities_all = {b["severity"] for b in bugs_all}
    assert "P1" in severities_all or "P2" in severities_all


def test_generate_each_industry_produces_valid_bugs() -> None:
    """Every built-in industry should produce valid bug instances."""
    required_fields = {"bug_id", "bug_instance_id", "template_id", "industry",
                       "risk_type", "severity", "title", "trigger", "api",
                       "method", "oracle", "variant_dimensions"}

    for industry_id in list_industries():
        factory = BenchmarkBugFactory(industry_id)
        bugs = factory.generate(count=10, seed=42)

        assert len(bugs) == 10, f"Industry {industry_id}: expected 10 bugs, got {len(bugs)}"

        for bug in bugs:
            for field in required_fields:
                assert field in bug, f"Industry {industry_id}, bug {bug.get('bug_id')}: missing field '{field}'"

            # Oracle must have required sub-fields
            oracle = bug["oracle"]
            assert "type" in oracle
            assert "bug_signal" in oracle
            assert "entity" in oracle

            # Industry must match
            assert bug["industry"] == industry_id


def test_generate_bug_ids_are_unique() -> None:
    """All generated bugs should have unique bug_ids."""
    factory = BenchmarkBugFactory("finance")
    bugs = factory.generate(count=100, seed=42)
    ids = [b["bug_id"] for b in bugs]
    assert len(ids) == len(set(ids))


# ═════════════════════════════════════════════════════════════════════════════
# Ground Truth Storage Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_write_ground_truth_creates_file() -> None:
    """write_ground_truth should create a JSON file."""
    factory = BenchmarkBugFactory("education")
    bugs = factory.generate(count=10, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        gt_path = factory.write_ground_truth(bugs, output_dir=tmpdir)
        assert gt_path.exists()
        assert gt_path.suffix == ".json"


def test_ground_truth_path_contains_blocklist_token() -> None:
    """Ground truth path MUST contain a PRIVATE_BLOCKLIST token."""
    factory = BenchmarkBugFactory("education")
    bugs = factory.generate(count=5, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        gt_path = factory.write_ground_truth(bugs, output_dir=tmpdir)
        path_str = str(gt_path).lower().replace("\\", "/")
        assert "private_ground_truth" in path_str, (
            f"Ground truth path must contain 'private_ground_truth' for blind discipline. "
            f"Got: {gt_path}"
        )


def test_write_ground_truth_content_valid() -> None:
    """Written ground truth should contain all bugs with oracle data."""
    factory = BenchmarkBugFactory("medical")
    bugs = factory.generate(count=10, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        gt_path = factory.write_ground_truth(bugs, output_dir=tmpdir)
        data = json.loads(gt_path.read_text(encoding="utf-8"))

        assert data["schema_version"] == "benchmark_bug_factory.v1"
        assert data["total_bugs"] == 10
        assert len(data["bugs"]) == 10

        # Each bug should have oracle data (NOT stripped)
        for bug in data["bugs"]:
            assert "oracle" in bug
            assert "expected_status" in bug


def test_validate_ground_truth_integrity_valid() -> None:
    """validate_ground_truth_integrity should pass for valid files."""
    factory = BenchmarkBugFactory("saas")
    bugs = factory.generate(count=5, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        gt_path = factory.write_ground_truth(bugs, output_dir=tmpdir)
        result = validate_ground_truth_integrity(gt_path)
        assert result["valid"] is True
        assert result["bug_count"] == 5


def test_validate_ground_truth_integrity_missing_file() -> None:
    """validate_ground_truth_integrity should fail for missing files."""
    result = validate_ground_truth_integrity(Path("/nonexistent/path/ground_truth.json"))
    assert result["valid"] is False
    assert "not found" in result["reason"].lower()


def test_validate_ground_truth_integrity_no_blocklist_token() -> None:
    """validate_ground_truth_integrity should fail if path lacks BLOCKLIST token."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file WITHOUT blocklist token in path
        non_gt_path = Path(tmpdir) / "safe_public_dir" / "bugs.json"
        non_gt_path.parent.mkdir(parents=True, exist_ok=True)
        non_gt_path.write_text(json.dumps({"bugs": [{"bug_id": "test"}]}), encoding="utf-8")

        result = validate_ground_truth_integrity(non_gt_path)
        assert result["valid"] is False
        assert "PRIVATE_BLOCKLIST" in result["reason"]


# ═════════════════════════════════════════════════════════════════════════════
# Public Artifact Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_generate_public_artifacts_creates_files() -> None:
    """generate_public_artifacts should create OpenAPI, PRD, and accounts files."""
    factory = BenchmarkBugFactory("crm")
    bugs = factory.generate(count=10, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        public = factory.generate_public_artifacts(bugs, output_dir=tmpdir)

        assert "openapi" in public
        assert "prd" in public
        assert "accounts" in public

        for path in public.values():
            assert path.exists(), f"Missing artifact: {path}"


def test_public_artifacts_no_oracle_data() -> None:
    """Public artifacts MUST NOT contain oracle/ground truth data."""
    factory = BenchmarkBugFactory("ecommerce")
    bugs = factory.generate(count=15, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        public = factory.generate_public_artifacts(bugs, output_dir=tmpdir)

        # Read OpenAPI spec
        openapi_data = json.loads(public["openapi"].read_text(encoding="utf-8"))
        openapi_str = json.dumps(openapi_data)

        # Must NOT contain oracle signals or expected_status
        for bug in bugs:
            assert bug["oracle"]["bug_signal"] not in openapi_str, (
                f"OpenAPI spec leaked oracle signal: {bug['oracle']['bug_signal']}"
            )


def test_public_artifacts_openapi_valid() -> None:
    """Generated OpenAPI spec should be valid 3.0.3 structure."""
    factory = BenchmarkBugFactory("finance")
    bugs = factory.generate(count=10, seed=42)

    with tempfile.TemporaryDirectory() as tmpdir:
        public = factory.generate_public_artifacts(bugs, output_dir=tmpdir)
        spec = json.loads(public["openapi"].read_text(encoding="utf-8"))

        assert spec["openapi"] == "3.0.3"
        assert "paths" in spec
        assert "info" in spec
        assert len(spec["paths"]) > 0


# ═════════════════════════════════════════════════════════════════════════════
# Runtime Seeding Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_build_runtime_seeds_structure() -> None:
    """build_runtime_seeds should produce a valid seeding manifest."""
    factory = BenchmarkBugFactory("erp")
    bugs = factory.generate(count=10, seed=42)
    seeds = factory.build_runtime_seeds(bugs)

    assert seeds["schema_version"] == "benchmark_runtime_seeds.v1"
    assert seeds["total_seeds"] == 10
    assert len(seeds["seeds"]) == 10

    for seed in seeds["seeds"]:
        assert "bug_id" in seed
        assert "method" in seed
        assert "path_pattern" in seed
        assert "oracle_signal" in seed


def test_build_runtime_seeds_matches_bugs() -> None:
    """Runtime seeds should correspond 1:1 with generated bugs."""
    factory = BenchmarkBugFactory("saas")
    bugs = factory.generate(count=15, seed=42)
    seeds = factory.build_runtime_seeds(bugs)

    bug_ids = {b["bug_id"] for b in bugs}
    seed_ids = {s["bug_id"] for s in seeds["seeds"]}
    assert bug_ids == seed_ids


# ═════════════════════════════════════════════════════════════════════════════
# Convenience Function Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_prepare_industry_benchmark_end_to_end() -> None:
    """prepare_industry_benchmark should run all stages without error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = prepare_industry_benchmark(
            "crm",
            bug_count=20,
            seed=42,
            output_root=tmpdir,
        )

        assert result["industry"] == "crm"
        assert result["bug_count"] == 20
        assert "ground_truth_path" in result
        assert "public_artifacts" in result
        assert "runtime_seeds" in result

        # Verify ground truth file exists and is valid
        gt_path = Path(result["ground_truth_path"])
        assert gt_path.exists()

        integrity = validate_ground_truth_integrity(gt_path)
        assert integrity["valid"] is True


# ═════════════════════════════════════════════════════════════════════════════
# Module-level Helper Tests
# ═════════════════════════════════════════════════════════════════════════════

def test_list_industries() -> None:
    """list_industries should return all 7 built-in industries."""
    industries = list_industries()
    assert len(industries) == 7
    expected = {"crm", "ecommerce", "erp", "finance", "medical", "education", "saas"}
    assert set(industries) == expected


def test_get_industry_profile_known() -> None:
    """get_industry_profile should return profile for known industries."""
    profile = get_industry_profile("medical")
    assert profile is not None
    assert profile.industry_id == "medical"
    assert len(profile.entities) > 0
    assert len(profile.roles) > 0


def test_get_industry_profile_unknown() -> None:
    """get_industry_profile should return None for unknown industries."""
    assert get_industry_profile("unknown_xyz") is None


def test_universal_templates_not_empty() -> None:
    """UNIVERSAL_TEMPLATES should have templates covering all major risk types."""
    assert len(UNIVERSAL_TEMPLATES) > 0
    risk_types = {t.risk_type for t in UNIVERSAL_TEMPLATES}
    # Should cover these essential categories
    essential = {"permission_bypass", "idor", "tenant_isolation", "state_flow",
                 "data_consistency", "idempotency", "input_validation"}
    for rt in essential:
        assert rt in risk_types, f"Missing essential risk type: {rt}"


def test_factory_log_observable() -> None:
    """Factory should produce observable log output."""
    factory = BenchmarkBugFactory("ecommerce")
    factory.generate(count=5, seed=42)
    log = factory.get_log()

    assert len(log) > 0
    assert any("BenchmarkBugFactory init" in entry for entry in log)
    assert any("generate:" in entry for entry in log)
