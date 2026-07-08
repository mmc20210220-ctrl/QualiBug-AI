from __future__ import annotations

import os

import pytest

from ai_test_asset_center.enterprise_project_config import (
    PROJECT_INDUSTRY_KEY,
    PROJECT_MODULE_SCOPE_KEY,
    PROJECT_PRODUCTION_DATA_EXCLUSION_KEY,
    MultiServiceProject,
    match_production_data_exclusion,
)
from ai_test_asset_center.grounded_probe_executor import _decide_probe


# ---------------------------------------------------------------------------
# match_production_data_exclusion unit tests
# ---------------------------------------------------------------------------

def test_match_fragment():
    cfg = {PROJECT_PRODUCTION_DATA_EXCLUSION_KEY: ["/api/admin/users"]}
    assert match_production_data_exclusion(cfg, "http://x/api/admin/users?q=1") is not None
    assert match_production_data_exclusion(cfg, "http://x/api/orders") is None


def test_match_regex():
    cfg = {PROJECT_PRODUCTION_DATA_EXCLUSION_KEY: ["re:/api/admin/.*"]}
    hit = match_production_data_exclusion(cfg, "http://x/api/admin/settlement")
    assert hit is not None and hit.startswith("production_data_exclusion_matched:")


def test_no_exclusions_is_none():
    assert match_production_data_exclusion({}, "http://x/anything") is None
    assert match_production_data_exclusion({PROJECT_PRODUCTION_DATA_EXCLUSION_KEY: []}, "http://x/anything") is None


def test_invalid_regex_treated_as_literal():
    cfg = {PROJECT_PRODUCTION_DATA_EXCLUSION_KEY: ["re:(unclosed"]}
    # Falls back to literal substring of the pattern body -> won't match, returns None
    assert match_production_data_exclusion(cfg, "http://x/open") is None


# ---------------------------------------------------------------------------
# MultiServiceProject metadata round-trip
# ---------------------------------------------------------------------------

def test_metadata_roundtrip(tmp_path):
    proj = MultiServiceProject("demo", tmp_path)
    proj.init_from_example()
    proj.set_project_metadata(
        industry="ecommerce",
        module_scope=["order", "payment"],
        production_data_exclusion=["/api/admin/users", "re:/settlement/.*"],
    )
    meta = proj.project_metadata()
    assert meta["industry"] == "ecommerce"
    assert meta["module_scope"] == ["order", "payment"]
    assert "/api/admin/users" in meta["production_data_exclusion"]
    # Reload from disk to prove it persisted (not just in-memory)
    reloaded = MultiServiceProject("demo", tmp_path)
    assert reloaded.project_metadata()["industry"] == "ecommerce"
    assert reloaded.get_execution_safety_boundary() == ["/api/admin/users", "re:/settlement/.*"]


def test_metadata_requires_list(tmp_path):
    proj = MultiServiceProject("demo2", tmp_path)
    proj.init_from_example()
    with pytest.raises(ValueError):
        proj.set_project_metadata(module_scope="not-a-list")


# ---------------------------------------------------------------------------
# Executor honors production_data_exclusion as a hard block
# ---------------------------------------------------------------------------

def _read_probe(path: str, *, risk_type: str = "audit_privacy_probe") -> dict:
    return {
        "candidate_id": "P1",
        "risk_type": risk_type,
        "execution_policy": "read_only_safe",
        "endpoint": {"method": "GET", "path": path},
    }


def test_executor_blocks_excluded_read_probe(monkeypatch):
    # Disable strict grounding so the control case would otherwise EXECUTE.
    monkeypatch.setenv("QUALIBUG_STRICT_PROBE_GROUNDING", "0")
    base_url = "http://test.local"
    options = {"execute_readonly": True}

    # Control: no exclusion -> read-only probe is eligible to execute.
    control = _decide_probe(
        _read_probe("/api/orders"),
        base_url=base_url,
        config={},
        options=options,
    )
    assert control.decision == "execute_readonly", control.reason

    # Exclusion set: matching path must be HARD-blocked.
    excluded = _decide_probe(
        _read_probe("/api/admin/users"),
        base_url=base_url,
        config={PROJECT_PRODUCTION_DATA_EXCLUSION_KEY: ["/api/admin/users"]},
        options=options,
    )
    assert excluded.decision == "blocked"
    assert excluded.reason.startswith("production_data_exclusion_matched:")


def test_executor_block_is_additive_only(monkeypatch):
    """The boundary only ever blocks; it never enables execution."""
    monkeypatch.setenv("QUALIBUG_STRICT_PROBE_GROUNDING", "0")
    # Without base_url the probe would be dry_run_only even without exclusion;
    # an exclusion must still report an exclusion reason (blocking path wins).
    d = _decide_probe(
        _read_probe("/api/admin/users"),
        base_url="",
        config={PROJECT_PRODUCTION_DATA_EXCLUSION_KEY: ["/api/admin/users"]},
        options={"execute_readonly": True},
    )
    assert d.decision == "blocked"
    assert d.reason.startswith("production_data_exclusion_matched:")
