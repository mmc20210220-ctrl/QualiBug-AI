"""Runtime actor exploration data structures.

Defines types for actor selection modes, candidate ranking, exploration
decisions, and permission observations. These are pure data structures; the
runtime modules own selection, transport and observation policy.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class ActorSelectionMode(str, enum.Enum):
    """How the actor execution plan was constructed."""

    EXPLICIT_PERMISSION = "explicit_permission"
    """A source-declared ``permits`` edge exists."""

    OBSERVED_PERMISSION = "observed_permission"
    """A context-compatible runtime observation is being reused."""

    PERMISSION_EXPLORATION = "permission_exploration"
    """No permission expectation exists; bounded candidates will be observed."""


@dataclass
class ActorCandidate:
    """A single candidate actor with auditable scoring information."""

    actor_id: str
    actor_ref: str
    account_ref: str | None
    runtime_bound: bool
    executable: bool
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    role_ref: str | None = None
    credential_secret_ref: str | None = None
    last_auth_status: str | None = None


@dataclass
class ActorExecutionPlan:
    """Structured actor-selection result returned by the compiler."""

    mode: ActorSelectionMode
    candidates: list[ActorCandidate]
    authorization_oracle_enabled: bool
    max_attempts: int
    reason: str


@dataclass
class ExplorationDecision:
    """Result of the operation-safety eligibility decision."""

    allowed: bool
    max_attempts: int
    reason: str
    requires_owner: bool


class ActorAttemptOutcome(str, enum.Enum):
    """Classification of one candidate's real target response."""

    OPERATION_EXECUTABLE = "operation_executable"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_NOT_VISIBLE = "resource_not_visible"
    BUSINESS_REJECTED = "business_rejected"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"
    INCONCLUSIVE = "inconclusive"


@dataclass
class PermissionObservation:
    """One contextual permission behavior observed during a governed execution.

    Campaign and project coordinates are mandatory on the production path so
    observations can never bias another concurrent scan. Empty coordinates are
    retained only for backward-compatible unit construction and are stored in a
    separate legacy scope.
    """

    actor_id: str
    role_ref: str | None
    operation_id: str
    evidence_ref: str
    outcome: str
    campaign_id: str = ""
    project_id: str = ""
    environment_ref: str = ""
    context_fingerprint: str = ""
    resource_identity_fingerprint: str | None = None
    resource_type: str | None = None
    tenant_id: str | None = None
    ownership: str | None = None
    resource_state: str | None = None
    status_code: int | None = None
    confidence: float = 0.0
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scope: str = "scan"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
