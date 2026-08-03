"""P0-1: Observer evidence truthfulness — no fabricated observation values.

Every observer that cannot produce real before/after evidence must return
INDETERMINATE or BLOCKED with a specific reason_code. Fabricating
before=0/after=1/effect_count=2 from write responses is forbidden.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.observer_contracts_base import (
    _effect_window,
    _observe_business_effect,
    _observe_state_snapshot,
    _state_snapshot_evidence,
    build_observer_receipt,
)
from ai_test_asset_center.assertion_dsl_privacy_base import (
    evaluate_assertion as evaluate_validation_assertion,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _governed_step(*, phase: str, before_status: int, before_body,
                   after_status: int, after_body, write_status: int = 200,
                   write_body=None) -> dict:
    return {
        "phase": phase,
        "method": "POST",
        "step_id": f"step-{phase}",
        "operation_ref": f"op-{phase}",
        "governance_receipt": {
            "before": {"status_code": before_status, "body": before_body},
            "after": {"status_code": after_status, "body": after_body},
            "write": {"status_code": write_status, "body": write_body or {}},
        },
    }


# ── _effect_window ───────────────────────────────────────────────────────

def test_effect_window_before_get_fails_returns_indeterminate():
    """before GET 500 → INDETERMINATE, no fabricated evidence."""
    step = _governed_step(
        phase="control", before_status=500, before_body=None,
        after_status=200, after_body={"id": 1},
    )
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_OBSERVATION_FAILED"


def test_effect_window_after_get_fails_returns_indeterminate():
    """after GET 500 → INDETERMINATE, no fabricated evidence."""
    step = _governed_step(
        phase="control", before_status=200, before_body={"id": 1},
        after_status=500, after_body=None,
    )
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_OBSERVATION_FAILED"


def test_effect_window_both_gets_fail_returns_indeterminate():
    """Both GETs fail → INDETERMINATE."""
    step = _governed_step(
        phase="control", before_status=0, before_body=None,
        after_status=0, after_body=None,
    )
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_OBSERVATION_FAILED"


def test_effect_window_non_dict_body_returns_indeterminate():
    """before body is not dict/list → INDETERMINATE, no fabricated count."""
    step = _governed_step(
        phase="control", before_status=200, before_body="plain text",
        after_status=200, after_body={"id": 1},
    )
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_BODY_UNSUPPORTED"


def test_effect_window_empty_steps_returns_indeterminate():
    """No governed steps → missing reason."""
    evidence, reason = _effect_window([])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_WRITE_MISSING"


def test_effect_window_async_202_no_body_returns_indeterminate():
    """202 Accepted with no real body → INDETERMINATE."""
    step = _governed_step(
        phase="control", before_status=200, before_body={"id": 1},
        after_status=202, after_body=None,
    )
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_BODY_UNSUPPORTED"


def test_effect_window_no_entity_identity_change_reported_honestly():
    """Same entity before and after → honest zero effect, not fabricated."""
    step = _governed_step(
        phase="control",
        before_status=200, before_body=[{"id": 1, "name": "same"}],
        after_status=200, after_body=[{"id": 1, "name": "same"}],
    )
    evidence, reason = _effect_window([step])
    assert reason == ""
    assert evidence["identity_effect_count"] == 0
    assert evidence["business_field_change_count"] == 0
    assert evidence["effect_count"] == 0


def test_effect_window_real_entity_added_reported_honestly():
    """New entity appears → honest count, not fabricated."""
    step = _governed_step(
        phase="control",
        before_status=200, before_body=[],
        after_status=200, after_body=[{"id": 1, "name": "new"}],
    )
    evidence, reason = _effect_window([step])
    assert reason == ""
    assert evidence["identity_effect_count"] == 1
    assert evidence["business_field_change_count"] == 0  # no shared entity
    assert evidence["effect_count"] == 1


def test_effect_window_real_field_change_reported_honestly():
    """Same entity, different field → honest count."""
    step = _governed_step(
        phase="control",
        before_status=200, before_body=[{"id": 1, "name": "old"}],
        after_status=200, after_body=[{"id": 1, "name": "new"}],
    )
    evidence, reason = _effect_window([step])
    assert reason == ""
    assert evidence["business_field_change_count"] == 1
    assert evidence["effect_count"] >= 1


def test_effect_window_no_write_response_effect_fabrication():
    """Write response 200 does NOT fabricate before=0/after=1/effect=2."""
    # Only the write succeeded; observation GETs failed.
    step = _governed_step(
        phase="control", before_status=500, before_body=None,
        after_status=500, after_body=None, write_status=200,
        write_body={"id": 99},
    )
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_OBSERVATION_FAILED"
    # The fabricated values must not appear:
    assert evidence.get("before_identity_count") != 0
    assert evidence.get("after_identity_count") != 1
    assert evidence.get("effect_count") != 2


# ── _observe_business_effect ─────────────────────────────────────────────

def test_business_effect_no_write_steps_returns_indeterminate():
    """No governed write steps → INDETERMINATE."""
    receipt = _observe_business_effect([])
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "BUSINESS_EFFECT_WRITE_MISSING"


def test_business_effect_observation_failed_returns_indeterminate():
    """Observation GET fails → INDETERMINATE, not OBSERVED with fake counts."""
    step = _governed_step(
        phase="control", before_status=0, before_body=None,
        after_status=0, after_body=None,
    )
    receipt = _observe_business_effect([step])
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "BUSINESS_EFFECT_OBSERVATION_FAILED"
    assert receipt["evidence"].get("business_effect_observed") is False


def test_business_effect_real_observation_produces_observed():
    """Real before/after → OBSERVED with real data."""
    step = _governed_step(
        phase="control",
        before_status=200, before_body=[],
        after_status=200, after_body=[{"id": 1, "name": "x"}],
    )
    receipt = _observe_business_effect([step])
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["business_effect_observed"] is True
    assert "effect_count" in receipt["evidence"]


# ── _state_snapshot_evidence ─────────────────────────────────────────────

def test_state_snapshot_body_unsupported_returns_indeterminate():
    """Non-dict/list body → INDETERMINATE, not HTTP status fallback."""
    step = _governed_step(
        phase="control", before_status=200, before_body="text",
        after_status=200, after_body={"id": 1},
    )
    evidence, reason = _state_snapshot_evidence(
        step, snapshot_key="before", state_key="before_state",
    )
    assert reason == "STATE_SNAPSHOT_BODY_UNSUPPORTED"
    # Must NOT contain http_status_fallback or http_200
    assert evidence.get("state_field") != "http_status_fallback"
    assert evidence.get("before_state") != "http_200"


def test_state_snapshot_observation_failed_returns_indeterminate():
    """Observation GET failed → INDETERMINATE."""
    step = _governed_step(
        phase="control", before_status=500, before_body=None,
        after_status=200, after_body={"id": 1},
    )
    evidence, reason = _state_snapshot_evidence(
        step, snapshot_key="before", state_key="before_state",
    )
    assert reason == "STATE_SNAPSHOT_OBSERVATION_FAILED"


def test_state_snapshot_value_missing_returns_indeterminate():
    """Body has structure but no state field → INDETERMINATE."""
    step = _governed_step(
        phase="control", before_status=200,
        before_body={"foo": 1, "bar": 2, "baz": 3},
        after_status=200, after_body={"id": 1},
    )
    evidence, reason = _state_snapshot_evidence(
        step, snapshot_key="before", state_key="before_state",
    )
    assert reason == "STATE_SNAPSHOT_VALUE_MISSING"
    # Must NOT contain http_status_fallback
    assert evidence.get("state_field") != "http_status_fallback"


def test_final_state_accepts_entity_numeric_snapshot_without_lifecycle_field():
    """final_state may observe quantity-bearing entities without status fields."""
    step = _governed_step(
        phase="treatment",
        before_status=200,
        before_body={"sku": "SKU-1", "available_qty": 2, "locked_qty": 0},
        after_status=200,
        after_body={"sku": "SKU-1", "available_qty": -1, "locked_qty": 3},
    )
    evidence, reason = _state_snapshot_evidence(
        step, snapshot_key="after", state_key="final_state",
    )
    assert reason == ""
    assert evidence["final_state_kind"] == "entity_snapshot"
    assert evidence["state_field"] == "entity_numeric_snapshot"
    assert evidence["final_state"]
    assert evidence["before_values"]["available_qty"] == 2
    assert evidence["after_values"]["available_qty"] == -1


def test_state_snapshot_real_state_value_returns_success():
    """Real state field found → success with real value."""
    step = _governed_step(
        phase="control", before_status=200,
        before_body={"state": "active", "other": 1},
        after_status=200, after_body={"id": 1},
    )
    evidence, reason = _state_snapshot_evidence(
        step, snapshot_key="before", state_key="before_state",
    )
    assert reason == ""
    assert evidence["state_field"] == "state"
    assert evidence["before_state"] == "active"


# ── _observe_state_snapshot ──────────────────────────────────────────────

def test_observe_state_snapshot_no_write_steps_returns_indeterminate():
    """No governed write steps → INDETERMINATE."""
    receipt = _observe_state_snapshot("before_state", [])
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "STATE_SNAPSHOT_WRITE_MISSING"


def test_observe_before_state_real_observation_returns_observed():
    """Real before/after → OBSERVED."""
    step = _governed_step(
        phase="control", before_status=200,
        before_body=[{"state": "pending"}],
        after_status=200, after_body=[{"id": 1}],
    )
    receipt = _observe_state_snapshot("before_state", [step])
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"].get("before_state") == "pending"


# ── assertion_dsl_privacy_base (validation control effect gate) ──────────

import ai_test_asset_center.assertion_dsl_privacy_base as _priv
import ai_test_asset_center.assertion_dsl_validation_base as _vbase


def _mock_base_pass(**kwargs):
    """Return a synthetic PASS from the validation base layer."""
    return {
        "status": "PASS",
        "reason_code": "",
        "expected": kwargs.get("expected", {}),
        "actual": kwargs.get("actual", {}),
        "harness_error": False,
        "error": "",
    }


def test_privacy_gate_missing_business_effect_observed(monkeypatch):
    """business_effect_observed is not True → INDETERMINATE."""
    monkeypatch.setattr(_vbase, "evaluate_assertion", lambda *a, **kw: _mock_base_pass())
    assertion = {
        "kind": "validation_rejection",
        "expected_control_effect_min": 1,
        "property": {},
    }
    observations = {
        "business_effect_observed": False,
        "control_effect_count": None,
    }
    result = _priv.evaluate_assertion(assertion, observations=observations)
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_CONTROL_EFFECT_MISSING"


def test_privacy_gate_control_effect_none(monkeypatch):
    """control_effect_count is None → INDETERMINATE."""
    monkeypatch.setattr(_vbase, "evaluate_assertion", lambda *a, **kw: _mock_base_pass())
    assertion = {
        "kind": "validation_rejection",
        "expected_control_effect_min": 1,
        "property": {},
    }
    observations = {
        "business_effect_observed": True,
        "control_effect_count": None,
    }
    result = _priv.evaluate_assertion(assertion, observations=observations)
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_CONTROL_EFFECT_MISSING"


def test_privacy_gate_insufficient_control_effect(monkeypatch):
    """control_effect < expected_min → INDETERMINATE, not forced."""
    monkeypatch.setattr(_vbase, "evaluate_assertion", lambda *a, **kw: _mock_base_pass())
    assertion = {
        "kind": "validation_rejection",
        "expected_control_effect_min": 2,
        "property": {},
    }
    observations = {
        "business_effect_observed": True,
        "control_effect_count": 1,
    }
    result = _priv.evaluate_assertion(assertion, observations=observations)
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_CONTROL_EFFECT_MISSING"


def test_privacy_gate_sufficient_control_effect_passes(monkeypatch):
    """control_effect >= expected_min → PASS."""
    monkeypatch.setattr(_vbase, "evaluate_assertion", lambda *a, **kw: _mock_base_pass())
    assertion = {
        "kind": "validation_rejection",
        "expected_control_effect_min": 1,
        "property": {},
    }
    observations = {
        "business_effect_observed": True,
        "control_effect_count": 2,
    }
    result = _priv.evaluate_assertion(assertion, observations=observations)
    assert result["status"] == "PASS"


def test_privacy_gate_http_success_never_replaces_business_effect(monkeypatch):
    """A 2xx control response is transport evidence, not business-effect proof."""
    monkeypatch.setattr(_vbase, "evaluate_assertion", lambda *a, **kw: _mock_base_pass())
    assertion = {
        "kind": "validation_rejection",
        "expected_control_effect_min": 1,
        "property": {},
    }
    observations = {
        "business_effect_observed": False,
        "control_effect_count": None,
        "control_succeeded": True,
        "authorized_control": True,
    }

    result = _priv.evaluate_assertion(assertion, observations=observations)

    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_CONTROL_EFFECT_MISSING"


# ── no Oracle PASS from fabricated evidence ──────────────────────────────

def test_business_effect_not_observed_prevents_oracle_pass():
    """When business_effect_receipt is INDETERMINATE, it must not contribute
    business_effect_evidence to authorization_comparison (which would
    incorrectly fabricate an OBSERVED verdict).

    The treatment write here was NOT accepted (500): status alone cannot prove
    a leak, and missing business-effect evidence must yield INDETERMINATE.
    (Accepted 2xx writes are governed by the strengthened dual-accepted rule,
    which is proven by the status comparison itself.)
    """
    from ai_test_asset_center.observer_contracts_base import (
        observe_authorization_comparison,
    )
    # Even when control succeeded via HTTP, a non-accepted treatment write plus
    # no business_effect evidence means the comparison must not fabricate a
    # leak verdict.
    receipt = observe_authorization_comparison(
        control={"method": "POST", "path": "/r", "status_code": 200, "body": {"id": 1}},
        treatment={"method": "POST", "path": "/r", "status_code": 500, "body": {}},
        require_same_resource=True,
        business_effect={"business_effect_observed": False, "control_effect_count": None},
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "WRITE_EFFECT_EVIDENCE_REQUIRED"
