"""Unit tests for runtime actor exploration (Steps 5, 8, 9, 12, 15).

Covers:
  - Actor candidate scoring and stable ordering
  - Exploration eligibility (can_explore_actor)
  - Response classification (classify_actor_attempt)
  - Oracle protection (exploration mode blocks authorization verdicts)
  - Confidence computation
"""

import pytest
from copy import deepcopy

from ai_test_asset_center.actor_exploration import (
    ActorAttemptOutcome,
    ActorCandidate,
    ActorExecutionPlan,
    ActorSelectionMode,
    ExplorationDecision,
    PermissionObservation,
)
from ai_test_asset_center.actor_exploration_runtime import (
    build_exploration_plan,
    build_executable_candidates,
    can_explore_actor,
    classify_actor_attempt,
    compute_observation_confidence,
    score_actor_candidate,
    clear_scan_observations,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _make_actor(
    actor_id: str,
    *,
    role: str = "buyer",
    account_ref: str = "",
    runtime_bound: bool = True,
    secret_ref: str = "secret_ref:test_accounts:buyer",
    tenant_id: str = "",
) -> dict:
    return {
        "id": actor_id,
        "role": role,
        "account_ref": account_ref,
        "runtime_bound": runtime_bound,
        "credential_secret_ref": secret_ref,
        "tenant_scope": tenant_id,
    }


def _make_op(
    op_id: str = "op-get-products",
    *,
    method: str = "GET",
    path: str = "/api/products",
    resource_type: str = "product",
) -> dict:
    return {
        "id": op_id,
        "method": method,
        "path": path,
        "resource_type": resource_type,
    }


# ══════════════════════════════════════════════════════════════════════
# ── Actor Scoring (Step 5) ──
# ══════════════════════════════════════════════════════════════════════

class TestActorCandidateScoring:
    """Validate the composite scoring model."""

    def test_resource_creator_gets_highest_score(self):
        actor = _make_actor("creator-1", account_ref="creator@t.com")
        ctx = {"resource_creator_actor_id": "creator-1"}
        candidate = score_actor_candidate(actor, runtime_context=ctx)
        assert candidate.score >= 100
        assert "resource_creator" in candidate.score_reasons

    def test_previous_step_actor_is_preferred(self):
        actor = _make_actor("step-actor-1", account_ref="prev@t.com")
        ctx = {"previous_step_actor_id": "step-actor-1"}
        candidate = score_actor_candidate(actor, runtime_context=ctx)
        assert candidate.score >= 80
        assert "previous_step_actor" in candidate.score_reasons

    def test_same_tenant_adds_score(self):
        actor = _make_actor("tenant-actor", tenant_id="tenant-a")
        ctx = {"resource_tenant_id": "tenant-a"}
        candidate = score_actor_candidate(actor, runtime_context=ctx)
        assert candidate.score >= 70
        assert "same_tenant" in candidate.score_reasons

    def test_observed_success_adds_score(self):
        clear_scan_observations()
        op = _make_op("op-1")
        actor = _make_actor("observed-actor", account_ref="obs@t.com")
        candidate = score_actor_candidate(
            actor,
            operation=op,
            permission_observations=[
                PermissionObservation(
                    actor_id="observed-actor",
                    role_ref="buyer",
                    operation_id="op-1",
                    evidence_ref="receipt-1",
                    outcome="OBSERVED_ALLOWED",
                    status_code=200,
                    confidence=0.60,
                )
            ],
        )
        assert candidate.score >= 60
        assert any("observed_operation_success" in r for r in candidate.score_reasons)

    def test_unexecutable_actor_is_filtered_in_ranking(self):
        actor_exec = _make_actor("exec-1", secret_ref="valid_secret")
        actor_blocked = _make_actor("blocked-1", secret_ref="secret_ref:actor:unknown")
        actors = {"exec-1": actor_exec, "blocked-1": actor_blocked}
        candidates = build_executable_candidates(actors)
        assert len(candidates) == 1
        assert candidates[0].actor_id == "exec-1"

    def test_sorting_is_stable_by_actor_ref(self):
        a1 = _make_actor("a", account_ref="x@t.com")
        a2 = _make_actor("b", account_ref="y@t.com")
        actors = {"a": a1, "b": a2}
        candidates = build_executable_candidates(actors)
        # Same score rules = stable by actor_ref
        actor_ids = [c.actor_id for c in candidates]
        assert actor_ids == sorted(actor_ids)  # a before b

    def test_does_not_depend_on_hardcoded_role_names(self):
        """Scoring must not hardcode 'admin', 'buyer', etc."""
        actor = _make_actor("some-random-role-id", role="zuberflorp")
        candidate = score_actor_candidate(actor)
        # Should still produce a valid candidate without crashing
        assert candidate.actor_id == "some-random-role-id"
        assert candidate.role_ref == "zuberflorp"


# ══════════════════════════════════════════════════════════════════════
# ── Exploration Eligibility (Step 4) ──
# ══════════════════════════════════════════════════════════════════════

class TestExplorationEligibility:
    """Validate can_explore_actor decisions."""

    def test_get_allows_3_attempts(self):
        op = _make_op(method="GET")
        decision = can_explore_actor(op, {})
        assert decision.allowed is True
        assert decision.max_attempts == 3

    def test_head_allows_3_attempts(self):
        op = _make_op(method="HEAD")
        decision = can_explore_actor(op, {})
        assert decision.allowed is True
        assert decision.max_attempts == 3

    def test_query_post_allows_exploration(self):
        op = _make_op(method="POST", path="/api/products/search")
        decision = can_explore_actor(op, {})
        assert decision.allowed is True

    def test_delete_is_blocked_by_default(self):
        op = _make_op(method="DELETE", path="/api/products/123")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False
        assert "destructive" in decision.reason

    def test_refund_is_blocked(self):
        op = _make_op(method="POST", path="/api/orders/refund")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False

    def test_compensated_write_allows_exploration(self):
        op = _make_op(method="POST", path="/api/products")
        obl = {"property": {"compensates": "delete-product-123"}}
        decision = can_explore_actor(op, obl)
        assert decision.allowed is True

    def test_irreversible_status_operation_is_cautious(self):
        op = _make_op(method="POST", path="/api/users/ban")
        decision = can_explore_actor(op, {})
        assert decision.allowed is False  # "ban" matches destructive pattern

    def test_state_transition_allows_1_attempt(self):
        op = _make_op(method="POST", path="/api/orders/approve")
        decision = can_explore_actor(op, {})
        assert decision.allowed is True
        assert decision.max_attempts == 1
        assert decision.requires_owner is True

    def test_general_patch_allows_2_attempts(self):
        op = _make_op(method="PATCH", path="/api/products/123")
        decision = can_explore_actor(op, {})
        assert decision.allowed is True
        assert decision.max_attempts == 2


# ══════════════════════════════════════════════════════════════════════
# ── Response Classification (Step 8) ──
# ══════════════════════════════════════════════════════════════════════

class TestResponseClassification:
    """Validate classify_actor_attempt."""

    def test_200_is_executable(self):
        assert classify_actor_attempt({"status_code": 200}) == ActorAttemptOutcome.OPERATION_EXECUTABLE

    def test_201_is_executable(self):
        assert classify_actor_attempt({"status_code": 201}) == ActorAttemptOutcome.OPERATION_EXECUTABLE

    def test_401_is_auth_failed(self):
        assert classify_actor_attempt({"status_code": 401}) == ActorAttemptOutcome.AUTHENTICATION_FAILED

    def test_403_is_permission_denied(self):
        assert classify_actor_attempt({"status_code": 403}) == ActorAttemptOutcome.PERMISSION_DENIED

    def test_404_is_resource_not_visible(self):
        assert classify_actor_attempt({"status_code": 404}) == ActorAttemptOutcome.RESOURCE_NOT_VISIBLE

    def test_422_is_business_rejected(self):
        assert classify_actor_attempt({"status_code": 422}) == ActorAttemptOutcome.BUSINESS_REJECTED

    def test_500_is_infrastructure_failed(self):
        assert classify_actor_attempt({"status_code": 500}) == ActorAttemptOutcome.INFRASTRUCTURE_FAILED

    def test_uses_status_key_as_fallback(self):
        # Some callers use "status" not "status_code"
        assert classify_actor_attempt({"status": 200}) == ActorAttemptOutcome.OPERATION_EXECUTABLE

    def test_unknown_status_is_inconclusive(self):
        assert classify_actor_attempt({"status_code": 302}) == ActorAttemptOutcome.INCONCLUSIVE


# ══════════════════════════════════════════════════════════════════════
# ── Oracle Protection (Step 9) ──
# ══════════════════════════════════════════════════════════════════════

class TestOracleProtection:
    """Validate that exploration mode does not generate authorization defects."""

    def test_exploration_plan_disables_oracle(self):
        op = _make_op(method="GET")
        actors = {"actor-1": _make_actor("actor-1", account_ref="a@t.com")}
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids=set(),  # no permits
        )
        assert plan is not None
        assert plan.mode == ActorSelectionMode.PERMISSION_EXPLORATION
        assert plan.authorization_oracle_enabled is False

    def test_explicit_permits_enables_oracle(self):
        op = _make_op(method="GET")
        actors = {"actor-1": _make_actor("actor-1", account_ref="a@t.com")}
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids={"actor-1"},  # permits exist
        )
        assert plan is not None
        assert plan.mode == ActorSelectionMode.EXPLICIT_PERMISSION
        assert plan.authorization_oracle_enabled is True

    def test_no_plan_returns_none_for_blocked_exploration(self):
        """When no permits and exploration is blocked, plan is None."""
        op = _make_op(method="DELETE", path="/api/products/123")
        actors = {"actor-1": _make_actor("actor-1", account_ref="a@t.com")}
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids=set(),
        )
        assert plan is None  # DELETE is blocked


# ══════════════════════════════════════════════════════════════════════
# ── Confidence Rules (Step 12) ──
# ══════════════════════════════════════════════════════════════════════

class TestConfidenceRules:
    """Validate confidence computation for permission observations."""

    def test_single_success_is_0_60(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_ALLOWED",
            same_context_successes=1,
        )
        assert conf == 0.60

    def test_two_successes_is_0_75(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_ALLOWED",
            same_context_successes=2,
        )
        assert conf == 0.75

    def test_multi_instance_success_is_0_85(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_ALLOWED",
            same_context_successes=1,
            different_instance_successes=1,
        )
        assert conf == 0.85

    def test_static_support_adds_0_10(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_ALLOWED",
            same_context_successes=1,
            has_static_support=True,
        )
        assert conf == 0.70  # 0.60 + 0.10

    def test_tenant_conflict_returns_zero(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_ALLOWED",
            same_context_successes=2,
            has_tenant_conflict=True,
        )
        assert conf == 0.0  # Cannot generalize

    def test_ownership_conflict_returns_zero(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_ALLOWED",
            same_context_successes=3,
            has_ownership_conflict=True,
        )
        assert conf == 0.0

    def test_auth_failed_is_zero_confidence(self):
        """Authentication failure must never be learned as permission denial."""
        conf = compute_observation_confidence(
            outcome="AUTHENTICATION_FAILED",
            same_context_successes=3,
        )
        assert conf == 0.0

    def test_denied_is_0_50(self):
        conf = compute_observation_confidence(
            outcome="OBSERVED_DENIED",
            same_context_successes=1,
        )
        assert conf == 0.50


# ══════════════════════════════════════════════════════════════════════
# ── Build Exploration Plan Integration ──
# ══════════════════════════════════════════════════════════════════════

class TestBuildExplorationPlan:
    """Validate end-to-end plan construction."""

    def test_explicit_permits_returns_single_candidate_plan(self):
        op = _make_op()
        actors = {
            "creator-1": _make_actor("creator-1", account_ref="c@t.com"),
            "other-1": _make_actor("other-1", account_ref="o@t.com"),
        }
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids={"creator-1"},
        )
        assert plan is not None
        assert plan.mode == ActorSelectionMode.EXPLICIT_PERMISSION
        assert plan.max_attempts == 1
        assert len(plan.candidates) == 1
        assert plan.candidates[0].actor_id == "creator-1"

    def test_no_permits_exploration_returns_ranked_candidates(self):
        op = _make_op(method="GET", path="/api/products")
        actors = {
            "a1": _make_actor("a1", account_ref="a@t.com"),
            "a2": _make_actor("a2", account_ref="b@t.com"),
        }
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids=set(),
        )
        assert plan is not None
        assert plan.mode == ActorSelectionMode.PERMISSION_EXPLORATION
        assert plan.authorization_oracle_enabled is False
        assert len(plan.candidates) <= plan.max_attempts

    def test_no_executable_actors_returns_none(self):
        op = _make_op(method="GET")
        actors = {
            "bad-1": _make_actor("bad-1", secret_ref="secret_ref:actor:missing"),
        }
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids=set(),
        )
        assert plan is None  # no executable actors

    def test_explicit_permits_with_non_executable_actor_returns_none(self):
        """When permits reference actors that aren't executable, plan is None."""
        op = _make_op()
        actors = {
            "bad-1": _make_actor("bad-1", secret_ref="secret_ref:actor:missing"),
        }
        plan = build_exploration_plan(
            operation=op,
            obligation={},
            actors=actors,
            permitted_actor_ids={"bad-1"},
        )
        assert plan is None


# ══════════════════════════════════════════════════════════════════════
# ── Actor Selection Mode Enum ──
# ══════════════════════════════════════════════════════════════════════

class TestActorSelectionMode:
    def test_string_values(self):
        assert ActorSelectionMode.EXPLICIT_PERMISSION.value == "explicit_permission"
        assert ActorSelectionMode.OBSERVED_PERMISSION.value == "observed_permission"
        assert ActorSelectionMode.PERMISSION_EXPLORATION.value == "permission_exploration"

    def test_is_string_enum(self):
        assert isinstance(ActorSelectionMode.EXPLICIT_PERMISSION, str)
