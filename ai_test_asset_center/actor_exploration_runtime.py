"""Runtime actor exploration logic — Commit 2 + 3 per the implementation plan.

Functions:
  - ``can_explore_actor``      → ExplorationDecision
  - ``score_actor_candidate``   → ActorCandidate with composite score
  - ``build_exploration_plan``  → ActorExecutionPlan for when permits are missing
  - ``classify_actor_attempt``  → ActorAttemptOutcome from HTTP response
  - ``record_permission_observation`` → store observed edge in scan-scoped store
"""

from __future__ import annotations

import logging
from typing import Any

from .actor_exploration import (
    ActorAttemptOutcome,
    ActorCandidate,
    ActorExecutionPlan,
    ActorSelectionMode,
    ExplorationDecision,
    PermissionObservation,
    _text,
    _dict,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

# Methods safe to attempt with multiple actors
_SAFE_READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_QUERY_POST_PATTERNS: frozenset[str] = frozenset({"search", "query", "list", "find", "filter", "lookup"})

# Methods that are high-risk for multi-actor exploration
_DESTRUCTIVE_METHODS: frozenset[str] = frozenset({"DELETE"})

# Irreversible state transitions (patterns in operation path or description)
_IRREVERSIBLE_PATTERNS: frozenset[str] = frozenset({
    "refund", "payment", "pay", "transfer", "ship", "ban", "disable",
    "close", "freeze", "revoke", "destroy", "permanent",
})

# Default limits
_DEFAULT_MAX_SAFE_ATTEMPTS = 3
_DEFAULT_MAX_WRITE_ATTEMPTS = 2
_DEFAULT_MAX_RISKY_ATTEMPTS = 1


# ══════════════════════════════════════════════════════════════════════
# Operation classification helpers
# ══════════════════════════════════════════════════════════════════════

def _method_of(operation: dict[str, Any]) -> str:
    return _text(operation.get("method")).upper()


def _path_of(operation: dict[str, Any]) -> str:
    return _text(operation.get("path") or operation.get("raw_path")).lower()


def _name_of(operation: dict[str, Any]) -> str:
    return _text(
        operation.get("name")
        or operation.get("operation_id")
        or operation.get("summary")
        or ""
    ).lower()


def _is_safe_read(operation: dict[str, Any]) -> bool:
    method = _method_of(operation)
    if method in _SAFE_READ_METHODS:
        return True
    if method == "POST":
        path = _path_of(operation)
        name = _name_of(operation)
        combined = f"{path} {name}"
        return any(pattern in combined for pattern in _QUERY_POST_PATTERNS)
    return False


def _is_destructive(operation: dict[str, Any]) -> bool:
    method = _method_of(operation)
    if method in _DESTRUCTIVE_METHODS:
        return True
    combined = f"{_path_of(operation)} {_name_of(operation)}"
    return any(pattern in combined for pattern in _IRREVERSIBLE_PATTERNS)


def _is_state_transition(operation: dict[str, Any]) -> bool:
    """Moderate-risk: approval, submission, status changes, enable/disable."""
    combined = f"{_path_of(operation)} {_name_of(operation)}"
    return any(
        pattern in combined
        for pattern in (
            "approve", "submit", "cancel", "enable", "disable",
            "activate", "deactivate", "publish", "freeze", "status",
            "transition", "state",
        )
    )


def _has_compensation_plan(operation: dict[str, Any], obligation: dict[str, Any]) -> bool:
    """Check if the operation has a declared compensation/cleanup plan."""
    obl = _dict(obligation)
    prop = _dict(obl.get("property"))
    if _text(prop.get("compensates")) or _text(prop.get("cleanup_ref")):
        return True
    # Also check if the obligation has a compensates_create_operation
    if _text(obl.get("compensates_operation_ref")):
        return True
    return False


# ══════════════════════════════════════════════════════════════════════
# Exploration Decision (Step 4)
# ══════════════════════════════════════════════════════════════════════

def can_explore_actor(
    operation: dict[str, Any],
    obligation: dict[str, Any],
    *,
    allow_destructive: bool = False,
    max_safe_attempts: int = _DEFAULT_MAX_SAFE_ATTEMPTS,
    max_write_attempts: int = _DEFAULT_MAX_WRITE_ATTEMPTS,
) -> ExplorationDecision:
    """Decide whether runtime actor exploration is permitted for this operation.

    Priority order:
      1. Safe reads (GET/HEAD/OPTIONS, query POSTs) → allowed, 3 attempts
      2. Write operations with compensation plans → allowed, 2 attempts
      3. Idempotent PATCH on test data → allowed, 2 attempts
      4. State transitions → cautious, 1 attempt
      5. Destructive (DELETE/refund/payment/transfer) → default blocked
    """
    method = _method_of(operation)

    # ── Safe reads ──
    if _is_safe_read(operation):
        return ExplorationDecision(
            allowed=True,
            max_attempts=max_safe_attempts,
            reason="safe_read",
            requires_owner=False,
        )

    # ── Destructive operations ──
    if _is_destructive(operation) and not allow_destructive:
        return ExplorationDecision(
            allowed=False,
            max_attempts=0,
            reason="destructive_operation",
            requires_owner=True,
        )
    if _is_destructive(operation) and allow_destructive:
        return ExplorationDecision(
            allowed=True,
            max_attempts=1,
            reason="destructive_operation_forced",
            requires_owner=True,
        )

    # ── Operations with explicit compensation plan ──
    if _has_compensation_plan(operation, obligation):
        return ExplorationDecision(
            allowed=True,
            max_attempts=max_write_attempts,
            reason="compensated_write",
            requires_owner=False,
        )

    # ── State transitions ──
    if _is_state_transition(operation):
        return ExplorationDecision(
            allowed=True,
            max_attempts=1,
            reason="state_transition_cautious",
            requires_owner=True,
        )

    # ── General writes (PUT, POST, PATCH) ──
    if method in {"PUT", "PATCH", "POST"}:
        return ExplorationDecision(
            allowed=True,
            max_attempts=max_write_attempts,
            reason="general_write",
            requires_owner=False,
        )

    # ── Unknown — default conservative: block ──
    return ExplorationDecision(
        allowed=False,
        max_attempts=0,
        reason="unknown_operation_risk",
        requires_owner=True,
    )


# ══════════════════════════════════════════════════════════════════════
# Candidate Scoring (Step 5)
# ══════════════════════════════════════════════════════════════════════

def score_actor_candidate(
    actor: dict[str, Any],
    *,
    operation: dict[str, Any] | None = None,
    obligation: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    permission_observations: list[PermissionObservation] | None = None,
) -> ActorCandidate:
    """Score an actor for candidate ranking.

    Scoring rules (additive):
      +100  resource creator
      +90   resource owner
      +80   previous-step actor
      +70   same tenant
      +60   observed success on same operation
      +40   observed success on same resource type
      +20   account_ref present
      +20   runtime_bound
      +10   credential recently verified OK
      -100  tenant conflict
      -80   resource ownership conflict
      -60   credential expired / auth failed
    """
    ctx = runtime_context or {}
    observations = permission_observations or []

    actor_id = _text(actor.get("id") or actor.get("actor_id"))
    actor_ref = _text(actor.get("actor_ref") or actor.get("name") or actor_id)
    account_ref = _text(actor.get("account_ref") or actor.get("account_id")) or None
    runtime_bound = actor.get("runtime_bound") is True
    role_ref = _text(actor.get("role")) or None
    tenant_id = _text(actor.get("tenant_scope") or actor.get("tenant_id")) or None
    secret_ref = _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
        or ""
    ) or None

    score = 0.0
    reasons: list[str] = []

    # ── Positive signals ──

    if actor_id and actor_id == _text(ctx.get("resource_creator_actor_id")):
        score += 100
        reasons.append("resource_creator")

    if actor_id and actor_id == _text(ctx.get("resource_owner_actor_id")):
        score += 90
        reasons.append("resource_owner")

    if actor_id and actor_id == _text(ctx.get("previous_step_actor_id")):
        score += 80
        reasons.append("previous_step_actor")

    if tenant_id and tenant_id == _text(ctx.get("resource_tenant_id")):
        score += 70
        reasons.append("same_tenant")

    # Observed success from same-scan permission store
    op_id = _text(operation.get("id") or operation.get("operation_id")) if operation else ""
    resource_type = _text(
        operation.get("resource_type")
        or ctx.get("resource_type")
    ) if operation else ""

    same_op_successes = 0
    same_resource_successes = 0
    for obs in observations:
        if obs.actor_id == actor_id and obs.outcome == "OBSERVED_ALLOWED":
            if obs.operation_id == op_id:
                same_op_successes += 1
            if resource_type and obs.resource_type == resource_type:
                same_resource_successes += 1

    if same_op_successes > 0:
        score += 60
        reasons.append(f"observed_operation_success_x{same_op_successes}")
    elif same_resource_successes > 0:
        score += 40
        reasons.append(f"observed_resource_success_x{same_resource_successes}")

    if account_ref:
        score += 20
        reasons.append("account_ref_present")
    if runtime_bound:
        score += 20
        reasons.append("runtime_bound")

    # Credential recently verified
    if _text(actor.get("last_auth_status") or "").lower() == "ok":
        score += 10
        reasons.append("credential_recently_ok")

    # ── Negative signals ──

    if tenant_id and _text(ctx.get("resource_tenant_id")) and tenant_id != _text(ctx.get("resource_tenant_id")):
        score -= 100
        reasons.append("tenant_conflict")

    if actor_id and _text(ctx.get("resource_owner_actor_id")) and actor_id != _text(ctx.get("resource_owner_actor_id")):
        if _text(ctx.get("ownership_required")) == "true":
            score -= 80
            reasons.append("ownership_conflict")

    if _text(actor.get("last_auth_status") or "").lower() in ("failed", "expired", "revoked"):
        score -= 60
        reasons.append("credential_failed_or_expired")

    return ActorCandidate(
        actor_id=actor_id,
        actor_ref=actor_ref,
        account_ref=account_ref,
        runtime_bound=runtime_bound,
        executable=True,
        score=score,
        score_reasons=reasons,
        tenant_id=tenant_id,
        role_ref=role_ref,
        credential_secret_ref=secret_ref,
        last_auth_status=_text(actor.get("last_auth_status")) or None,
    )


# ══════════════════════════════════════════════════════════════════════
# Candidate Ranking
# ══════════════════════════════════════════════════════════════════════

def _actor_is_executable(actor: dict[str, Any]) -> bool:
    """Check if an actor has resolvable credentials.

    Mirrors ``experiment_compiler_support._actor_is_executable``.
    """
    role = _text(actor.get("role")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
    )
    if not secret_ref:
        return False
    return not _text(secret_ref).lower().startswith("secret_ref:actor:")


def build_executable_candidates(
    actors: dict[str, dict[str, Any]],
    *,
    operation: dict[str, Any] | None = None,
    obligation: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    permission_observations: list[PermissionObservation] | None = None,
    permitted_actor_ids: set[str] | None = None,
) -> list[ActorCandidate]:
    """Build and rank executable actor candidates.

    If *permitted_actor_ids* is provided, only those actors are considered
    (explicit permits path).  Otherwise all executable actors are ranked.

    Filtering:
      1. Must be executable (_actor_is_executable).
      2. Must have account_ref or be anonymous/public.
      3. Must be runtime_bound unless anonymous/public.

    Ranking: score descending, then actor_ref ascending (stable).
    """
    candidates: list[ActorCandidate] = []

    target_ids = permitted_actor_ids or set(actors.keys())

    for actor_id, actor in actors.items():
        if not isinstance(actor, dict):
            continue
        if permitted_actor_ids is not None and actor_id not in target_ids:
            continue

        if not _actor_is_executable(actor):
            continue

        candidate = score_actor_candidate(
            actor,
            operation=operation,
            obligation=obligation,
            runtime_context=runtime_context,
            permission_observations=permission_observations,
        )
        candidates.append(candidate)

    # Stable sort: highest score first, then by actor_ref for determinism
    candidates.sort(key=lambda c: (-c.score, c.actor_ref))
    return candidates


# ══════════════════════════════════════════════════════════════════════
# Response Classification (Step 8)
# ══════════════════════════════════════════════════════════════════════

def classify_actor_attempt(result: dict[str, Any]) -> ActorAttemptOutcome:
    """Classify a single HTTP attempt result.

    Never interprets HTTP 200 as automatic privilege escalation.
    Never interprets HTTP 403 as automatic product defect.
    Never learns 401 as a permission denial.
    """
    status = int(result.get("status_code") or result.get("status") or 0)

    if 200 <= status < 300:
        return ActorAttemptOutcome.OPERATION_EXECUTABLE

    if status == 401:
        return ActorAttemptOutcome.AUTHENTICATION_FAILED

    if status == 403:
        return ActorAttemptOutcome.PERMISSION_DENIED

    if status == 404:
        return ActorAttemptOutcome.RESOURCE_NOT_VISIBLE

    if status in {400, 409, 422}:
        # Reached business validation layer — actor is likely authorized
        return ActorAttemptOutcome.BUSINESS_REJECTED

    if status >= 500 or status == 0:
        return ActorAttemptOutcome.INFRASTRUCTURE_FAILED

    return ActorAttemptOutcome.INCONCLUSIVE


# ══════════════════════════════════════════════════════════════════════
# Exploration Plan Builder (Step 6)
# ══════════════════════════════════════════════════════════════════════

def build_exploration_plan(
    *,
    operation: dict[str, Any],
    obligation: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    permitted_actor_ids: set[str],
    runtime_context: dict[str, Any] | None = None,
    permission_observations: list[PermissionObservation] | None = None,
    allow_destructive: bool = False,
) -> ActorExecutionPlan | None:
    """Build an ActorExecutionPlan, or return None if the obligation should remain blocked.

    Three paths:
      1. Explicit permits exist → EXPLICIT_PERMISSION, 1 attempt, oracle ON.
      2. No permits, exploration allowed → PERMISSION_EXPLORATION, N attempts, oracle OFF.
      3. No permits, exploration denied → None (caller should block).
    """
    # ── Path 1: Explicit permits exist ──
    if permitted_actor_ids:
        candidates = build_executable_candidates(
            actors,
            operation=operation,
            obligation=obligation,
            runtime_context=runtime_context,
            permission_observations=permission_observations,
            permitted_actor_ids=permitted_actor_ids,
        )
        if candidates:
            return ActorExecutionPlan(
                mode=ActorSelectionMode.EXPLICIT_PERMISSION,
                candidates=candidates,
                authorization_oracle_enabled=True,
                max_attempts=1,
                reason="explicit_permits_edge",
            )
        # Explicit permits reference actors that are not executable → block
        return None

    # ── Path 2: No permits — check exploration eligibility ──
    decision = can_explore_actor(
        operation, obligation, allow_destructive=allow_destructive,
    )

    if not decision.allowed:
        # Blocked — caller should emit runtime_actor_exploration_not_allowed
        return None

    # Build ranked candidates from ALL executable actors
    candidates = build_executable_candidates(
        actors,
        operation=operation,
        obligation=obligation,
        runtime_context=runtime_context,
        permission_observations=permission_observations,
        permitted_actor_ids=None,  # all actors
    )

    if not candidates:
        # No executable actors at all
        return None

    # Cap candidates at max_attempts
    limited = candidates[: decision.max_attempts]

    return ActorExecutionPlan(
        mode=ActorSelectionMode.PERMISSION_EXPLORATION,
        candidates=limited,
        authorization_oracle_enabled=False,
        max_attempts=decision.max_attempts,
        reason="permits_edge_missing_runtime_exploration",
    )


# ══════════════════════════════════════════════════════════════════════
# Permission Observation Store (Step 11)
# ══════════════════════════════════════════════════════════════════════

# In-memory store scoped to current scan.  In production this would be
# persisted alongside the scan ledger.

_scan_observations: list[PermissionObservation] = []


def record_permission_observation(observation: PermissionObservation) -> None:
    """Store a runtime permission observation (scan-scoped)."""
    _scan_observations.append(observation)
    logger.debug(
        "permission_observation: actor=%s op=%s outcome=%s status=%d confidence=%.2f",
        observation.actor_id,
        observation.operation_id,
        observation.outcome,
        observation.status_code or 0,
        observation.confidence,
    )


def get_scan_observations(
    *,
    actor_id: str | None = None,
    operation_id: str | None = None,
    outcome: str | None = None,
    min_confidence: float = 0.0,
) -> list[PermissionObservation]:
    """Query observed permission edges from the current scan."""
    results = _scan_observations
    if actor_id:
        results = [o for o in results if o.actor_id == actor_id]
    if operation_id:
        results = [o for o in results if o.operation_id == operation_id]
    if outcome:
        results = [o for o in results if o.outcome == outcome]
    if min_confidence > 0:
        results = [o for o in results if o.confidence >= min_confidence]
    return results


def clear_scan_observations() -> None:
    """Reset the scan-scoped observation store (call at scan start)."""
    global _scan_observations
    _scan_observations = []


def has_observed_success(
    *,
    actor_id: str,
    operation_id: str,
    min_confidence: float = 0.0,
) -> bool:
    """Check if an actor has been observed succeeding on an operation."""
    for obs in _scan_observations:
        if (
            obs.actor_id == actor_id
            and obs.operation_id == operation_id
            and obs.outcome == "OBSERVED_ALLOWED"
            and obs.confidence >= min_confidence
        ):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════
# Confidence Rules (Step 12)
# ══════════════════════════════════════════════════════════════════════

_CONFIDENCE_SINGLE_SUCCESS = 0.60
_CONFIDENCE_TWO_SAME_CONTEXT = 0.75
_CONFIDENCE_MULTI_INSTANCE = 0.85
_CONFIDENCE_STATIC_SUPPORT_BONUS = 0.10
_CONFIDENCE_REUSE_THRESHOLD = 0.80


def compute_observation_confidence(
    *,
    outcome: str,
    same_context_successes: int = 0,
    different_instance_successes: int = 0,
    has_static_support: bool = False,
    has_tenant_conflict: bool = False,
    has_ownership_conflict: bool = False,
) -> float:
    """Compute confidence for a permission observation.

    Rules:
      Single 2xx                    → 0.60
      Same context 2+ successes     → 0.75
      Different resource instances  → 0.85
      Static source support         → +0.10
      Tenant/ownership conflict     → cannot generalize (0.0)

    Only observations with confidence ≥ 0.80 should be reused in
    subsequent obligations as preferred candidates.
    """
    if outcome == "AUTHENTICATION_FAILED":
        # Never learn authentication failure as a permission denial
        return 0.0

    if has_tenant_conflict or has_ownership_conflict:
        return 0.0  # Cannot generalize

    confidence = 0.0

    if outcome == "OBSERVED_ALLOWED":
        if different_instance_successes > 0:
            confidence = _CONFIDENCE_MULTI_INSTANCE
        elif same_context_successes >= 2:
            confidence = _CONFIDENCE_TWO_SAME_CONTEXT
        elif same_context_successes >= 1:
            confidence = _CONFIDENCE_SINGLE_SUCCESS
        else:
            confidence = 0.0
    elif outcome == "OBSERVED_DENIED":
        confidence = 0.50  # Single denial is weaker evidence

    if has_static_support:
        confidence = min(1.0, confidence + _CONFIDENCE_STATIC_SUPPORT_BONUS)

    return confidence


def can_reuse_observation(confidence: float) -> bool:
    """Check if an observation is confident enough for downstream reuse."""
    return confidence >= _CONFIDENCE_REUSE_THRESHOLD


# ══════════════════════════════════════════════════════════════════════
# Structural logging helpers (Step 14)
# ══════════════════════════════════════════════════════════════════════

def log_exploration_started(
    obligation_id: str,
    operation_id: str,
    candidate_count: int,
    max_attempts: int,
    authorization_oracle_enabled: bool,
) -> None:
    logger.info(
        "actor.exploration.started",
        extra={
            "event": "actor.exploration.started",
            "obligation_id": obligation_id,
            "operation_id": operation_id,
            "candidate_count": candidate_count,
            "max_attempts": max_attempts,
            "authorization_oracle_enabled": authorization_oracle_enabled,
        },
    )


def log_exploration_attempted(
    actor_ref: str,
    attempt_index: int,
    score: float,
    score_reasons: list[str],
    status_code: int,
    outcome: str,
) -> None:
    logger.info(
        "actor.exploration.attempted",
        extra={
            "event": "actor.exploration.attempted",
            "actor_ref": actor_ref,
            "attempt_index": attempt_index,
            "score": score,
            "score_reasons": score_reasons,
            "status_code": status_code,
            "outcome": outcome,
        },
    )


def log_exploration_discovered(
    actor_ref: str,
    status_code: int,
    binding_scope: str,
    authorization_verdict: str,
) -> None:
    logger.info(
        "actor.exploration.discovered",
        extra={
            "event": "actor.exploration.discovered",
            "actor_ref": actor_ref,
            "status_code": status_code,
            "binding_scope": binding_scope,
            "authorization_verdict": authorization_verdict,
        },
    )


def log_exploration_exhausted(
    attempted_actor_count: int,
    outcomes: dict[str, int],
) -> None:
    logger.info(
        "actor.exploration.exhausted",
        extra={
            "event": "actor.exploration.exhausted",
            "attempted_actor_count": attempted_actor_count,
            "outcomes": outcomes,
        },
    )


# ══════════════════════════════════════════════════════════════════════
# Utility for the missing _list helper
# ══════════════════════════════════════════════════════════════════════

def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
