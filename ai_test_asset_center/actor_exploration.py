"""Runtime actor exploration data structures.

Defines types for actor selection modes, candidate ranking, exploration
decisions, and permission observations.  These are pure data structures —
no behavior change is introduced here (Commit 1 per the implementation plan).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ──────────────────────────────────────────────────────────────────────
# Actor Selection Mode
# ──────────────────────────────────────────────────────────────────────

class ActorSelectionMode(str, enum.Enum):
    """How the actor execution plan was constructed."""

    EXPLICIT_PERMISSION = "explicit_permission"
    """A source-declared ``permits`` edge exists; the actor is explicitly authorised."""

    OBSERVED_PERMISSION = "observed_permission"
    """A previously-observed permission edge (same-scan) was reused with sufficient confidence."""

    PERMISSION_EXPLORATION = "permission_exploration"
    """No ``permits`` edge exists; the runtime will attempt candidates and classify responses."""


# ──────────────────────────────────────────────────────────────────────
# Actor Candidate
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ActorCandidate:
    """A single candidate actor with scoring information."""

    actor_id: str
    """Canonical actor id from the behaviour IR."""

    actor_ref: str
    """Human-readable actor reference (role label or account identity)."""

    account_ref: str | None
    """Resolved account reference, or None if unbound."""

    runtime_bound: bool
    """Whether the actor already has runtime credentials configured."""

    executable: bool
    """Whether ``_actor_is_executable`` returned True for this actor."""

    score: float = 0.0
    """Composite score used for stable ordering (higher = preferred)."""

    score_reasons: list[str] = field(default_factory=list)
    """Human-readable reasons that contributed to the score (e.g. 'resource_owner')."""

    tenant_id: str | None = None
    """Tenant scope identifier, if available from the IR or runtime context."""

    role_ref: str | None = None
    """Normalised role label (e.g. 'admin', 'buyer')."""

    credential_secret_ref: str | None = None
    """Secret reference for token resolution."""

    last_auth_status: str | None = None
    """Most recent authentication verification outcome (if cached)."""


# ──────────────────────────────────────────────────────────────────────
# Actor Execution Plan
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ActorExecutionPlan:
    """Structured actor selection result returned by the compiler.

    Replaces the previous single-actor return so that the executor can
    iterate candidates when no explicit ``permits`` edge exists.
    """

    mode: ActorSelectionMode
    """How this plan was constructed."""

    candidates: list[ActorCandidate]
    """Ordered candidate actors. The executor iterates from first to last."""

    authorization_oracle_enabled: bool
    """Whether the authorization oracle may issue verdicts for this plan."""

    max_attempts: int
    """Hard cap on how many candidates may be tried before giving up."""

    reason: str
    """Human-readable reason for this plan (e.g. 'explicit_permits_edge')."""


# ──────────────────────────────────────────────────────────────────────
# Exploration Decision
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ExplorationDecision:
    """Result of ``can_explore_actor(operation, obligation)``."""

    allowed: bool
    """Whether runtime actor exploration is permitted for this operation."""

    max_attempts: int
    """Maximum number of candidate actors to try."""

    reason: str
    """Short category (e.g. 'safe_read', 'destructive_operation')."""

    requires_owner: bool
    """If True, only the resource owner/creator may be tried (not any candidate)."""


# ──────────────────────────────────────────────────────────────────────
# Actor Attempt Outcome (response classification)
# ──────────────────────────────────────────────────────────────────────

class ActorAttemptOutcome(str, enum.Enum):
    """Classification of a single HTTP attempt by a candidate actor."""

    OPERATION_EXECUTABLE = "operation_executable"
    """2xx — the operation completed; the actor is usable for this context."""

    AUTHENTICATION_FAILED = "authentication_failed"
    """401 — credentials are invalid, expired, or misconfigured."""

    PERMISSION_DENIED = "permission_denied"
    """403 — the server explicitly denied access."""

    RESOURCE_NOT_VISIBLE = "resource_not_visible"
    """404 — resource may not exist, may be hidden, or may be a 403-in-disguise."""

    BUSINESS_REJECTED = "business_rejected"
    """400/409/422 — the request reached business validation (actor is likely allowed)."""

    INFRASTRUCTURE_FAILED = "infrastructure_failed"
    """5xx or transport error — cannot determine whether the actor is permitted."""

    INCONCLUSIVE = "inconclusive"
    """Any other status or ambiguous response that cannot be classified."""


# ──────────────────────────────────────────────────────────────────────
# Permission Observation
# ──────────────────────────────────────────────────────────────────────

@dataclass
class PermissionObservation:
    """A recorded runtime permission event bound to the current scan / scenario."""

    actor_id: str
    """Canonical actor id."""

    role_ref: str | None
    """Role label from the IR."""

    operation_id: str
    """Operation id from the behaviour IR."""

    evidence_ref: str
    """Reference to the raw response evidence (receipt id or path)."""

    outcome: str
    """One of OBSERVED_ALLOWED, OBSERVED_DENIED, AUTHENTICATION_FAILED, INCONCLUSIVE."""

    resource_type: str | None = None
    """Resource type (e.g. 'product', 'order') inferred from the operation."""

    tenant_id: str | None = None
    """Tenant scope at observation time."""

    ownership: str | None = None
    """Ownership relation (e.g. 'owner', 'creator', 'viewer', None)."""

    resource_state: str | None = None
    """State of the target resource at observation time (if known)."""

    status_code: int | None = None
    """HTTP status code of the probe request."""

    confidence: float = 0.0
    """Confidence score [0.0–1.0] based on repetition and corroboration."""

    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """UTC timestamp when the observation was made."""

    scope: str = "scan"
    """Binding scope: 'scan' (current scan only), 'scenario', or 'environment'."""


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
