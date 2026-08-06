"""Context-aware runtime actor exploration on the existing compiler/executor path.

This module owns candidate scoring, operation safety, response classification
and the campaign-scoped permission observation store. Runtime observations are
selection evidence only; they never become authorization defects by themselves.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .actor_exploration import (
    ActorAttemptOutcome,
    ActorCandidate,
    ActorExecutionPlan,
    ActorSelectionMode,
    ExplorationDecision,
    PermissionObservation,
    _dict,
    _text,
)
from .behavior_ir_core import (
    _infer_operation_effect,
    _operation_has_semantic_marker,
)
from .experiment_compiler_support import _actor_is_executable


logger = logging.getLogger(__name__)

_DESTRUCTIVE_METHODS: frozenset[str] = frozenset({"DELETE"})
_IRREVERSIBLE_PATTERNS: frozenset[str] = frozenset({
    "refund", "payment", "pay", "transfer", "ship", "ban", "disable",
    "close", "freeze", "revoke", "destroy", "permanent",
})
_DEFAULT_MAX_SAFE_ATTEMPTS = 3
_DEFAULT_MAX_WRITE_ATTEMPTS = 2
# Compile seals a bounded candidate pool, not the attempt list. Context such as
# owner, tenant and prior runtime evidence exists only at execution time, so
# truncating to two or three candidates before that context is known permanently
# discards valid actors. Runtime still enforces the smaller attempt budget.
_MAX_SEALED_CANDIDATE_POOL = 16
_CONFIDENCE_SINGLE_SUCCESS = 0.60
_CONFIDENCE_TWO_SAME_CONTEXT = 0.75
_CONFIDENCE_MULTI_INSTANCE = 0.85
_CONFIDENCE_STATIC_SUPPORT_BONUS = 0.10
_CONFIDENCE_REUSE_THRESHOLD = 0.80
_MAX_OBSERVATIONS_PER_SCOPE = 512
_MAX_OBSERVATION_SCOPES = 32
_LEGACY_SCOPE = ("__legacy_project__", "__legacy_campaign__")


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def permission_context_fingerprint(runtime_context: dict[str, Any] | None) -> str:
    """Hash only permission-relevant context; never raw business payloads."""

    context = _dict(runtime_context)
    payload = {
        key: _text(context.get(key))
        for key in (
            "resource_type",
            "resource_tenant_id",
            "resource_owner_actor_id",
            "resource_creator_actor_id",
            "ownership",
            "resource_state",
        )
        if _text(context.get(key))
    }
    if not payload:
        return ""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def observation_context_compatible(
    observation: PermissionObservation,
    runtime_context: dict[str, Any] | None,
) -> bool:
    """Return whether an observation may influence this candidate ranking.

    Context fingerprints fail closed. A historical observation with no context
    cannot be generalized into a context-bearing selection merely because its
    actor and operation happen to match.
    """

    context = _dict(runtime_context)
    current_fingerprint = permission_context_fingerprint(context)
    if current_fingerprint:
        if observation.context_fingerprint != current_fingerprint:
            return False
    elif observation.context_fingerprint:
        return False
    current_tenant = _text(context.get("resource_tenant_id"))
    if observation.tenant_id and current_tenant and observation.tenant_id != current_tenant:
        return False
    current_ownership = _text(context.get("ownership"))
    if (
        observation.ownership
        and current_ownership
        and observation.ownership != current_ownership
    ):
        return False
    current_state = _text(context.get("resource_state"))
    if (
        observation.resource_state
        and current_state
        and observation.resource_state != current_state
    ):
        return False
    current_resource = _text(context.get("resource_type"))
    if (
        observation.resource_type
        and current_resource
        and observation.resource_type != current_resource
    ):
        return False
    return True


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
    """Use the Behavior IR effect classifier as the sole read/write authority."""

    return _infer_operation_effect(operation, _method_of(operation)) == "read"


def _is_destructive(operation: dict[str, Any]) -> bool:
    if _method_of(operation) in _DESTRUCTIVE_METHODS:
        return True
    return _operation_has_semantic_marker(operation, _IRREVERSIBLE_PATTERNS)


def _is_state_transition(operation: dict[str, Any]) -> bool:
    return _operation_has_semantic_marker(
        operation,
        frozenset({
            "approve", "submit", "cancel", "enable", "disable",
            "activate", "deactivate", "publish", "freeze", "status",
            "transition", "state",
        }),
    )


def _has_compensation_plan(
    operation: dict[str, Any],
    obligation: dict[str, Any],
) -> bool:
    del operation
    obligation = _dict(obligation)
    prop = _dict(obligation.get("property"))
    # A ``cleanup_requirement`` with a bound operation OR a bare ``required``
    # flag is the obligation compiler's declaration that this write is
    # governed by a reverse-order compensation plan. Both forms are
    # source-declared compensation evidence, equivalent to an explicit
    # ``compensates``/``cleanup_ref`` field.
    cleanup_requirement = _dict(obligation.get("cleanup_requirement"))
    return bool(
        _text(prop.get("compensates") or prop.get("cleanup_ref"))
        or _text(obligation.get("compensates_operation_ref"))
        or _text(cleanup_requirement.get("operation_ref"))
        or cleanup_requirement.get("required") is True
    )


def can_explore_actor(
    operation: dict[str, Any],
    obligation: dict[str, Any],
    *,
    allow_destructive: bool = False,
    max_safe_attempts: int = _DEFAULT_MAX_SAFE_ATTEMPTS,
    max_write_attempts: int = _DEFAULT_MAX_WRITE_ATTEMPTS,
) -> ExplorationDecision:
    """Decide whether bounded actor exploration is safe for an operation."""

    method = _method_of(operation)
    if _is_safe_read(operation):
        return ExplorationDecision(True, max_safe_attempts, "safe_read", False)
    # A governed write with a mandated cleanup path is not an uncontrolled
    # destructive action: every write runs through the sandbox executor with
    # before/after observation and cleanup receipts. Evaluate compensation
    # before the destructive gate so declared-reversible writes are not
    # silently misclassified as irreversible (matches the documented priority:
    # compensation plans rank above the destructive default).
    if _has_compensation_plan(operation, obligation):
        return ExplorationDecision(True, max_write_attempts, "compensated_write", False)
    if _is_destructive(operation) and not allow_destructive:
        return ExplorationDecision(False, 0, "destructive_operation", True)
    if _is_destructive(operation):
        return ExplorationDecision(True, 1, "destructive_operation_forced", True)
    if _is_state_transition(operation):
        return ExplorationDecision(True, 1, "state_transition_cautious", True)
    if method in {"PUT", "PATCH", "POST"}:
        # Final runtime policy verifies the compiled cleanup contract before
        # transport; compile cannot see the completed cleanup plan yet.
        return ExplorationDecision(True, max_write_attempts, "general_write", False)
    return ExplorationDecision(False, 0, "unknown_operation_risk", True)


def score_actor_candidate(
    actor: dict[str, Any],
    *,
    operation: dict[str, Any] | None = None,
    obligation: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    permission_observations: list[PermissionObservation] | None = None,
) -> ActorCandidate:
    """Score one executable actor using context before credential convenience."""

    del obligation
    context = _dict(runtime_context)
    observations = list(permission_observations or [])
    actor_id = _text(actor.get("id") or actor.get("actor_id"))
    actor_ref = _text(actor.get("actor_ref") or actor.get("name") or actor_id)
    account_ref = _text(actor.get("account_ref") or actor.get("account_id")) or None
    runtime_bound = actor.get("runtime_bound") is True
    role_ref = _text(actor.get("role")) or None
    tenant_id = _text(actor.get("tenant_scope") or actor.get("tenant_id")) or None
    secret_ref = _text(
        actor.get("credential_secret_ref") or actor.get("secret_ref")
    ) or None
    score = 0.0
    reasons: list[str] = []

    if actor_id and actor_id == _text(context.get("resource_creator_actor_id")):
        score += 100
        reasons.append("resource_creator")
    if actor_id and actor_id == _text(context.get("resource_owner_actor_id")):
        score += 90
        reasons.append("resource_owner")
    if actor_id and actor_id == _text(context.get("previous_step_actor_id")):
        score += 80
        reasons.append("previous_step_actor")

    current_tenant = _text(context.get("resource_tenant_id"))
    if tenant_id and current_tenant:
        if tenant_id == current_tenant:
            score += 70
            reasons.append("same_tenant")
        else:
            score -= 100
            reasons.append("tenant_conflict")

    operation_id = _text(
        _dict(operation).get("id") or _dict(operation).get("operation_id")
    )
    resource_type = _text(
        _dict(operation).get("resource_type") or context.get("resource_type")
    )
    compatible_allowed = [
        observation
        for observation in observations
        if observation.actor_id == actor_id
        and observation.outcome == "OBSERVED_ALLOWED"
        and observation.confidence >= _CONFIDENCE_SINGLE_SUCCESS
        and observation_context_compatible(observation, context)
    ]
    same_operation = [
        observation
        for observation in compatible_allowed
        if observation.operation_id == operation_id
    ]
    same_resource = [
        observation
        for observation in compatible_allowed
        if resource_type and observation.resource_type == resource_type
    ]
    if same_operation:
        best = max(observation.confidence for observation in same_operation)
        score += 60
        reasons.append(
            f"observed_operation_success_x{len(same_operation)}_confidence_{best:.2f}"
        )
    elif same_resource:
        best = max(observation.confidence for observation in same_resource)
        score += 40
        reasons.append(
            f"observed_resource_success_x{len(same_resource)}_confidence_{best:.2f}"
        )

    compatible_denials = [
        observation
        for observation in observations
        if observation.actor_id == actor_id
        and observation.operation_id == operation_id
        and observation.outcome == "OBSERVED_DENIED"
        and observation.confidence >= 0.50
        and observation_context_compatible(observation, context)
    ]
    if compatible_denials:
        score -= 25
        reasons.append(f"observed_operation_denial_x{len(compatible_denials)}")

    if account_ref:
        score += 20
        reasons.append("account_ref_present")
    if runtime_bound:
        score += 20
        reasons.append("runtime_bound")
    auth_status = _text(actor.get("last_auth_status")).lower()
    if auth_status == "ok":
        score += 10
        reasons.append("credential_recently_ok")
    elif auth_status in {"failed", "expired", "revoked"}:
        score -= 60
        reasons.append("credential_failed_or_expired")

    owner = _text(context.get("resource_owner_actor_id"))
    if owner and actor_id != owner and _text(context.get("ownership_required")) == "true":
        score -= 80
        reasons.append("ownership_conflict")

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


def build_executable_candidates(
    actors: dict[str, dict[str, Any]],
    *,
    operation: dict[str, Any] | None = None,
    obligation: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    permission_observations: list[PermissionObservation] | None = None,
    permitted_actor_ids: set[str] | None = None,
) -> list[ActorCandidate]:
    """Filter through the shared executability authority and rank candidates."""

    candidates: list[ActorCandidate] = []
    target_ids = (
        set(permitted_actor_ids)
        if permitted_actor_ids is not None
        else set(actors)
    )
    for actor_id, actor in actors.items():
        if not isinstance(actor, dict) or actor_id not in target_ids:
            continue
        if not _actor_is_executable(actor):
            continue
        candidates.append(score_actor_candidate(
            actor,
            operation=operation,
            obligation=obligation,
            runtime_context=runtime_context,
            permission_observations=permission_observations,
        ))
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.actor_ref))
    return candidates


def classify_actor_attempt(result: dict[str, Any]) -> ActorAttemptOutcome:
    """Classify behavior without claiming an expected authorization verdict."""

    try:
        status = int(result.get("status_code") or result.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if 200 <= status < 300:
        return ActorAttemptOutcome.OPERATION_EXECUTABLE
    if status == 401:
        return ActorAttemptOutcome.AUTHENTICATION_FAILED
    if status == 403:
        return ActorAttemptOutcome.PERMISSION_DENIED
    if status == 404:
        return ActorAttemptOutcome.RESOURCE_NOT_VISIBLE
    if status in {400, 409, 422}:
        return ActorAttemptOutcome.BUSINESS_REJECTED
    if status >= 500 or status == 0:
        return ActorAttemptOutcome.INFRASTRUCTURE_FAILED
    return ActorAttemptOutcome.INCONCLUSIVE


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
    """Seal a bounded candidate pool; runtime owns contextual attempt ordering."""

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
        # Permits reference only placeholder/non-executable actors (role
        # declarations without account credentials). That is not evidence
        # that the operation is untestable — executable accounts declared in
        # runtime test data may still exercise it. Fall through to the
        # exploration path instead of blocking, so a source-declared permit
        # on a role is honored with a real account that holds it.

    decision = can_explore_actor(
        operation,
        obligation,
        allow_destructive=allow_destructive,
    )
    if not decision.allowed:
        return None
    candidates = build_executable_candidates(
        actors,
        operation=operation,
        obligation=obligation,
        runtime_context=runtime_context,
        permission_observations=permission_observations,
    )
    if not candidates:
        return None
    sealed_pool = candidates[:_MAX_SEALED_CANDIDATE_POOL]
    reusable = any(
        observation.actor_id == sealed_pool[0].actor_id
        and observation.operation_id
        == _text(operation.get("id") or operation.get("operation_id"))
        and observation.outcome == "OBSERVED_ALLOWED"
        and can_reuse_observation(observation.confidence)
        and observation_context_compatible(observation, runtime_context)
        for observation in list(permission_observations or [])
    )
    return ActorExecutionPlan(
        mode=(
            ActorSelectionMode.OBSERVED_PERMISSION
            if reusable
            else ActorSelectionMode.PERMISSION_EXPLORATION
        ),
        candidates=sealed_pool,
        authorization_oracle_enabled=False,
        max_attempts=min(decision.max_attempts, len(sealed_pool)),
        reason=(
            "context_compatible_observed_permission"
            if reusable
            else "permits_edge_missing_runtime_exploration"
        ),
    )


# Keyed by (project, campaign). The legacy bucket exists only for historical
# tests/stored calls that cannot supply a real scan coordinate.
_scan_observations: dict[tuple[str, str], list[PermissionObservation]] = {}


def _scope_key(
    campaign_id: str | None,
    project_id: str | None,
) -> tuple[str, str]:
    campaign = _text(campaign_id)
    project = _text(project_id)
    return (project, campaign) if project or campaign else _LEGACY_SCOPE


def record_permission_observation(observation: PermissionObservation) -> None:
    """Store one observation inside its exact campaign/project boundary."""

    key = _scope_key(observation.campaign_id, observation.project_id)
    if key not in _scan_observations and len(_scan_observations) >= _MAX_OBSERVATION_SCOPES:
        oldest = next(iter(_scan_observations))
        _scan_observations.pop(oldest, None)
    rows = _scan_observations.setdefault(key, [])
    rows.append(observation)
    if len(rows) > _MAX_OBSERVATIONS_PER_SCOPE:
        del rows[:-_MAX_OBSERVATIONS_PER_SCOPE]
    logger.debug(
        "permission_observation: scope=%s actor=%s op=%s outcome=%s status=%d confidence=%.2f",
        key,
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
    campaign_id: str | None = None,
    project_id: str | None = None,
    environment_ref: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> list[PermissionObservation]:
    """Query observations, optionally confined to one production scan scope."""

    if campaign_id is not None or project_id is not None:
        results = list(_scan_observations.get(
            _scope_key(campaign_id, project_id), []
        ))
    else:
        results = [
            observation
            for rows in _scan_observations.values()
            for observation in rows
        ]
    if actor_id:
        results = [row for row in results if row.actor_id == actor_id]
    if operation_id:
        results = [row for row in results if row.operation_id == operation_id]
    if outcome:
        results = [row for row in results if row.outcome == outcome]
    if min_confidence > 0:
        results = [row for row in results if row.confidence >= min_confidence]
    if environment_ref:
        results = [
            row for row in results
            if not row.environment_ref or row.environment_ref == environment_ref
        ]
    if runtime_context:
        results = [
            row for row in results
            if observation_context_compatible(row, runtime_context)
        ]
    return results


def clear_scan_observations(
    *,
    campaign_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """Clear one scan scope, or all scopes for compatibility/test reset."""

    if campaign_id is None and project_id is None:
        _scan_observations.clear()
        return
    _scan_observations.pop(_scope_key(campaign_id, project_id), None)


def has_observed_success(
    *,
    actor_id: str,
    operation_id: str,
    min_confidence: float = 0.0,
    campaign_id: str | None = None,
    project_id: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> bool:
    return bool(get_scan_observations(
        actor_id=actor_id,
        operation_id=operation_id,
        outcome="OBSERVED_ALLOWED",
        min_confidence=min_confidence,
        campaign_id=campaign_id,
        project_id=project_id,
        runtime_context=runtime_context,
    ))


def observation_success_counts(
    *,
    observations: list[PermissionObservation],
    actor_id: str,
    operation_id: str,
    context_fingerprint: str = "",
    resource_identity_fingerprint: str = "",
) -> tuple[int, int]:
    """Count corroboration for one exact actor/operation/context."""

    successful = [
        row for row in observations
        if row.actor_id == actor_id
        and row.operation_id == operation_id
        and row.outcome == "OBSERVED_ALLOWED"
        and (
            row.context_fingerprint == context_fingerprint
            if context_fingerprint
            else not row.context_fingerprint
        )
    ]
    same_context = len(successful)
    different_instances = len({
        _text(row.resource_identity_fingerprint)
        for row in successful
        if row.resource_identity_fingerprint
        and resource_identity_fingerprint
        and row.resource_identity_fingerprint != resource_identity_fingerprint
    })
    return same_context, different_instances


def compute_observation_confidence(
    *,
    outcome: str,
    same_context_successes: int = 0,
    different_instance_successes: int = 0,
    has_static_support: bool = False,
    has_tenant_conflict: bool = False,
    has_ownership_conflict: bool = False,
) -> float:
    if outcome == "AUTHENTICATION_FAILED":
        return 0.0
    if has_tenant_conflict or has_ownership_conflict:
        return 0.0
    confidence = 0.0
    if outcome == "OBSERVED_ALLOWED":
        if different_instance_successes > 0:
            confidence = _CONFIDENCE_MULTI_INSTANCE
        elif same_context_successes >= 2:
            confidence = _CONFIDENCE_TWO_SAME_CONTEXT
        elif same_context_successes >= 1:
            confidence = _CONFIDENCE_SINGLE_SUCCESS
    elif outcome == "OBSERVED_DENIED":
        confidence = 0.50
    if has_static_support:
        confidence = min(1.0, confidence + _CONFIDENCE_STATIC_SUPPORT_BONUS)
    return confidence


def can_reuse_observation(confidence: float) -> bool:
    return confidence >= _CONFIDENCE_REUSE_THRESHOLD


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
