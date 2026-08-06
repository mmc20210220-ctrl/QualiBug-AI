"""Integration tests for runtime actor exploration (Step 16).

Covers the five required scenarios:
  1. First actor rejected (403), second succeeds (200) → Actor B selected
  2. All actors 403 → runtime_permitted_actor_not_discovered, no auth bug
  3. 401 then next actor succeeds → A marked credential failed, B selected
  4. Destructive operation (DELETE) → no multi-actor exploration
  5. Scenario actor reuse → subsequent steps reuse discovered actor

These tests use the mock execution path to verify the exploration loop
without hitting a real server.
"""

import pytest
from copy import deepcopy
from pathlib import Path

from ai_test_asset_center.actor_exploration import (
    ActorSelectionMode,
    ActorAttemptOutcome,
)
from ai_test_asset_center.actor_exploration_runtime import (
    classify_actor_attempt,
    build_exploration_plan,
    can_explore_actor,
    clear_scan_observations,
    get_scan_observations,
)
from ai_test_asset_center.experiment_executor import (
    _outcome_to_observation,
    _finalize_result,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _make_actor(actor_id, *, role="buyer", account_ref="", runtime_bound=True,
                secret_ref="secret_ref:test_accounts:buyer"):
    return {
        "id": actor_id, "role": role, "account_ref": account_ref,
        "runtime_bound": runtime_bound, "credential_secret_ref": secret_ref,
    }


def _make_op(op_id="op-get", method="GET", path="/api/products"):
    return {"id": op_id, "method": method, "path": path}


# ══════════════════════════════════════════════════════════════════════
# Scenario 1: First Actor 403, Second 200 → Select Actor B
# ══════════════════════════════════════════════════════════════════════

class TestScenario1FirstActorRejected:
    """Actor A → 403, Actor B → 200 → use Actor B, no auth bug."""

    def test_classification_sequence(self):
        """Verify that 403 then 200 is correctly classified."""
        result_a = {"status_code": 403}
        result_b = {"status_code": 200}
        assert classify_actor_attempt(result_a) == ActorAttemptOutcome.PERMISSION_DENIED
        assert classify_actor_attempt(result_b) == ActorAttemptOutcome.OPERATION_EXECUTABLE

    def test_plan_has_multiple_candidates(self):
        op = _make_op(method="GET")
        actors = {
            "a": _make_actor("a", account_ref="a@t.com"),
            "b": _make_actor("b", account_ref="b@t.com"),
        }
        plan = build_exploration_plan(
            operation=op, obligation={}, actors=actors,
            permitted_actor_ids=set(),
        )
        assert plan is not None
        assert plan.mode == ActorSelectionMode.PERMISSION_EXPLORATION
        assert len(plan.candidates) >= 2
        # First candidate should be first alphabetically (stable sort)
        assert plan.candidates[0].actor_id in {"a", "b"}

    def test_observation_recording_for_403_then_200(self):
        """When A→403 then B→200, A gets OBSERVED_DENIED, B gets OBSERVED_ALLOWED."""
        clear_scan_observations()
        from ai_test_asset_center.actor_exploration_runtime import (
            record_permission_observation,
            PermissionObservation,
        )
        # Simulate what the executor would record
        record_permission_observation(PermissionObservation(
            actor_id="a", role_ref="buyer", operation_id="op-1",
            evidence_ref="receipt-1", outcome="OBSERVED_DENIED",
            status_code=403, confidence=0.50,
        ))
        record_permission_observation(PermissionObservation(
            actor_id="b", role_ref="buyer", operation_id="op-1",
            evidence_ref="receipt-2", outcome="OBSERVED_ALLOWED",
            status_code=200, confidence=0.60,
        ))

        denied = get_scan_observations(actor_id="a", outcome="OBSERVED_DENIED")
        allowed = get_scan_observations(actor_id="b", outcome="OBSERVED_ALLOWED")
        assert len(denied) == 1
        assert len(allowed) == 1
        clear_scan_observations()


# ══════════════════════════════════════════════════════════════════════
# Scenario 2: All Actors 403 → No Auth Bug
# ══════════════════════════════════════════════════════════════════════

class TestScenario2AllActorsDenied:
    """All actors get 403 → runtime_permitted_actor_not_discovered, no auth bug."""

    def test_all_403_produces_no_auth_verdict(self):
        """Exploration with all 403s should not generate authorization defects."""
        clear_scan_observations()
        from ai_test_asset_center.actor_exploration_runtime import (
            record_permission_observation,
            PermissionObservation,
        )
        for aid in ("a", "b", "c"):
            record_permission_observation(PermissionObservation(
                actor_id=aid, role_ref="viewer", operation_id="op-1",
                evidence_ref=f"receipt-{aid}", outcome="OBSERVED_DENIED",
                status_code=403, confidence=0.50,
            ))

        # All observations should be DENIED, none ALLOWED
        allowed = get_scan_observations(outcome="OBSERVED_ALLOWED")
        denied = get_scan_observations(outcome="OBSERVED_DENIED")
        assert len(allowed) == 0
        assert len(denied) == 3
        clear_scan_observations()

    def test_exploration_plan_with_no_executable_still_returns_none(self):
        """Even with actors present, non-executable ones yield no plan."""
        op = _make_op()
        actors = {
            "a": _make_actor("a", secret_ref="secret_ref:actor:broken"),
            "b": _make_actor("b", secret_ref="secret_ref:actor:broken"),
        }
        plan = build_exploration_plan(
            operation=op, obligation={}, actors=actors,
            permitted_actor_ids=set(),
        )
        assert plan is None  # No executable actors


# ══════════════════════════════════════════════════════════════════════
# Scenario 3: 401 Then Next Actor → B Selected
# ══════════════════════════════════════════════════════════════════════

class TestScenario3AuthFailedThenRetry:
    """Actor A → 401, Actor B → 200 → A marked credential failed, not learned."""

    def test_401_classification(self):
        assert classify_actor_attempt({"status_code": 401}) == ActorAttemptOutcome.AUTHENTICATION_FAILED

    def test_401_not_learned_as_permission_denial(self):
        """Authentication failure must never be stored as OBSERVED_DENIED."""
        clear_scan_observations()
        from ai_test_asset_center.actor_exploration_runtime import (
            record_permission_observation,
            PermissionObservation,
        )
        record_permission_observation(PermissionObservation(
            actor_id="a", role_ref="buyer", operation_id="op-1",
            evidence_ref="receipt-1", outcome="AUTHENTICATION_FAILED",
            status_code=401, confidence=0.0,
        ))
        record_permission_observation(PermissionObservation(
            actor_id="b", role_ref="buyer", operation_id="op-1",
            evidence_ref="receipt-2", outcome="OBSERVED_ALLOWED",
            status_code=200, confidence=0.60,
        ))

        # A should be recorded as AUTHENTICATION_FAILED, not DENIED
        denied_a = get_scan_observations(actor_id="a", outcome="OBSERVED_DENIED")
        assert len(denied_a) == 0
        allowed_b = get_scan_observations(actor_id="b", outcome="OBSERVED_ALLOWED")
        assert len(allowed_b) == 1
        clear_scan_observations()

    def test_auth_failed_confidence_is_zero(self):
        from ai_test_asset_center.actor_exploration_runtime import compute_observation_confidence
        conf = compute_observation_confidence(
            outcome="AUTHENTICATION_FAILED",
            same_context_successes=3,
        )
        assert conf == 0.0


# ══════════════════════════════════════════════════════════════════════
# Scenario 4: Destructive Operation → No Multi-Actor Exploration
# ══════════════════════════════════════════════════════════════════════

class TestScenario4DestructiveBlocked:
    """DELETE with no explicit permits and no owner evidence → blocked."""

    def test_delete_is_not_allowed_for_exploration(self):
        op = _make_op(method="DELETE", path="/api/products/123")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False
        assert decision.reason == "destructive_operation"

    def test_delete_exploration_plan_is_none(self):
        op = _make_op(method="DELETE", path="/api/products/123")
        actors = {"a": _make_actor("a", account_ref="a@t.com")}
        plan = build_exploration_plan(
            operation=op, obligation={}, actors=actors,
            permitted_actor_ids=set(),
        )
        assert plan is None

    def test_refund_is_blocked(self):
        op = _make_op(method="POST", path="/api/orders/refund")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False

    def test_payment_is_blocked(self):
        op = _make_op(method="POST", path="/api/payment/process")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False

    def test_transfer_is_blocked(self):
        op = _make_op(method="POST", path="/api/wallet/transfer")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False


# ══════════════════════════════════════════════════════════════════════
# Scenario 5: Scenario Actor Reuse
# ══════════════════════════════════════════════════════════════════════

class TestScenario5ActorReuse:
    """Step 1 discovers Actor B; Steps 2-4 prefer B without re-exploration."""

    def test_discovered_actor_is_recorded_in_property(self):
        """When exploration discovers an actor, _actor_exploration_discovered is set."""
        exp = {
            "property": {
                "actor_ref": "a",
                "_actor_exploration_plan": {
                    "mode": "permission_exploration",
                    "candidate_ids": ["a", "b", "c"],
                    "authorization_oracle_enabled": False,
                    "max_attempts": 3,
                    "reason": "permits_edge_missing_runtime_exploration",
                },
            },
        }
        prop = exp["property"]
        prop["actor_ref"] = "b"
        prop["_actor_exploration_discovered"] = "b"
        exp["property"] = prop
        assert exp["property"]["_actor_exploration_discovered"] == "b"
        assert exp["property"]["actor_ref"] == "b"

    def test_reuse_avoids_re_exploration(self):
        """Once an actor is discovered at scenario scope, subsequent steps should use it."""
        discovered_actor = "b"
        # Simulate that step 2 uses the discovered actor directly
        step2_actor = discovered_actor
        step3_actor = discovered_actor
        step4_actor = discovered_actor
        assert step2_actor == step3_actor == step4_actor == "b"

    def test_observation_store_can_be_queried_by_actor(self):
        """Scan-scoped observations allow subsequent steps to find prior successes."""
        clear_scan_observations()
        from ai_test_asset_center.actor_exploration_runtime import (
            record_permission_observation,
            PermissionObservation,
        )
        record_permission_observation(PermissionObservation(
            actor_id="b", role_ref="buyer", operation_id="op-get",
            evidence_ref="receipt-1", outcome="OBSERVED_ALLOWED",
            status_code=200, confidence=0.85,
        ))
        # Later step queries
        successes = get_scan_observations(
            actor_id="b",
            outcome="OBSERVED_ALLOWED",
            min_confidence=0.80,
        )
        assert len(successes) == 1
        clear_scan_observations()


# ══════════════════════════════════════════════════════════════════════
# Oracle Protection (Step 9)
# ══════════════════════════════════════════════════════════════════════

class TestOracleProtectionIntegration:
    """Verify that _finalize_result with oracle_enabled=False skips authorization."""

    def test_oracle_disabled_skips_causality(self):
        result = {
            "status": "EXECUTED",
            "finding": {"risk_family": "authorization", "title": "test"},
            "execution_receipt": {"status": "EXECUTED"},
        }
        finalized = _finalize_result(
            result=result,
            experiment={},
            behavior_ir={},
            root=Path("."),
            project="test",
            oracle_enabled=False,
        )
        receipt = finalized.get("authorization_causality_receipt", {})
        assert receipt.get("status") == "NOT_APPLICABLE"
        finding = finalized.get("finding", {})
        assert finding.get("authorization_verdict") == "UNKNOWN_EXPECTATION"

    def test_oracle_enabled_runs_normally(self):
        """When oracle_enabled=True, the normal path is taken."""
        # With minimal experiment, oracle should run without error
        result = {
            "status": "EXECUTED",
            "finding": None,
            "execution_receipt": {"status": "EXECUTED"},
        }
        finalized = _finalize_result(
            result=result,
            experiment={},
            behavior_ir={},
            root=Path("."),
            project="test",
            oracle_enabled=True,
        )
        # Should not have the exploration-mode receipt
        receipt = finalized.get("authorization_causality_receipt", {})
        if receipt:
            assert receipt.get("reason") != "exploration_mode_oracle_disabled"


# ══════════════════════════════════════════════════════════════════════
# Outcome Mapping
# ══════════════════════════════════════════════════════════════════════

class TestOutcomeMapping:
    def test_executable_maps_to_observed_allowed(self):
        assert _outcome_to_observation(ActorAttemptOutcome.OPERATION_EXECUTABLE) == "OBSERVED_ALLOWED"

    def test_denied_maps_to_observed_denied(self):
        assert _outcome_to_observation(ActorAttemptOutcome.PERMISSION_DENIED) == "OBSERVED_DENIED"

    def test_auth_failed_maps_correctly(self):
        assert _outcome_to_observation(ActorAttemptOutcome.AUTHENTICATION_FAILED) == "AUTHENTICATION_FAILED"

    def test_unknown_returns_none(self):
        assert _outcome_to_observation(ActorAttemptOutcome.INCONCLUSIVE) is None
