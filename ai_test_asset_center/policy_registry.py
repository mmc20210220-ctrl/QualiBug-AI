"""
Phase81: Policy Registry — Machine-readable, versioned, rollback-able policies.

Central source of truth for all configurable strategy parameters.
Discovery Engine, Reasoner, Verifier, and Meta-Loop read from here.
"""

from __future__ import annotations

import json, hashlib, time, copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════════
# Policy Data Model
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ReasonerPolicy:
    enabled_engines: list[str] = field(default_factory=lambda: [
        "causality", "invariant", "reconciliation", "counterexample",
        "consistency", "population", "outcome", "temporal",
        "saga", "event_chain", "metamorphic",
        "business_outcome", "business_reconciliation", "business_invariant",
        "multi_source_reasoning", "business_lifecycle", "consistency_isolation",
    ])
    engine_weights: dict[str, float] = field(default_factory=dict)
    max_workers: int = 4
    retry_count: int = 1
    timeout_seconds: int = 300
    max_tokens: int = 32768
    max_hypotheses_per_engine: int = 15
    max_hypothesis_chars: int = 500
    retry_delay_seconds: float = 2.0
    prompt_truncation_chars: dict[str, int] = field(default_factory=lambda: {
        "prd_text": 2000, "api_schema": 3000,
        "observed_data": 2000, "heuristic_findings": 2000,
        "reader_json": 3000, "lifecycle_definition": 2000,
    })

    def __post_init__(self):
        # Normalize legacy persisted policies before they reach live execution.
        self.timeout_seconds = max(int(self.timeout_seconds or 0), 300)
        self.max_tokens = max(32768, min(int(self.max_tokens or 32768), 100000))
        self.max_workers = max(1, min(int(self.max_workers or 1), 4))
        self.retry_count = max(0, min(int(self.retry_count or 0), 1))
        self.max_hypotheses_per_engine = max(1, min(int(self.max_hypotheses_per_engine or 1), 15))
        self.max_hypothesis_chars = max(120, min(int(self.max_hypothesis_chars or 120), 500))
        canonical = [
            "causality", "invariant", "reconciliation", "counterexample",
            "consistency", "population", "outcome", "temporal",
            "saga", "event_chain", "metamorphic",
            "business_outcome", "business_reconciliation", "business_invariant",
            "multi_source_reasoning", "business_lifecycle", "consistency_isolation",
        ]
        legacy_default = canonical[:8]
        incoming = [str(name) for name in (self.enabled_engines or []) if str(name) in canonical]
        # Existing baseline files listed the first eight engines before Phase79
        # introduced the additional lenses.  Migrate that exact legacy default
        # without overriding a policy that intentionally disables an engine.
        if not incoming or incoming == legacy_default:
            incoming = list(canonical)
        self.enabled_engines = list(dict.fromkeys(incoming))
        self.engine_weights = {
            str(name): max(0.0, float(weight))
            for name, weight in (self.engine_weights or {}).items()
            if str(name) in canonical
        }


@dataclass
class DiscoveryPolicy:
    risk_budget: dict[str, int] = field(default_factory=dict)
    oracle_priority: dict[str, float] = field(default_factory=dict)
    flow_priority: dict[str, float] = field(default_factory=dict)
    dedicated_threshold: float = 0.7
    max_hypotheses_execute: int = 93
    max_rounds: int = 5
    stagnation_limit: int = 3


@dataclass
class VerificationPolicy:
    observer_priority: dict[str, float] = field(default_factory=dict)
    async_window_seconds: int = 0
    evidence_collection_order: list[str] = field(default_factory=list)
    convergence_threshold: float = 0.30  # below this = converged
    # Kept only to load legacy policy files.  It is forcibly disabled: no
    # evolution policy may lower a formal bug evidence threshold.
    verifier_relaxed: bool = False
    scenario_auto: bool = False

    def __post_init__(self):
        self.async_window_seconds = max(0, min(int(self.async_window_seconds or 0), 300))
        self.verifier_relaxed = False


@dataclass
class ExecutionPolicy:
    max_requests: int = 200
    max_concurrency: int = 4
    http_timeout_seconds: int = 10
    fixture_strategy: str = "minimal"  # minimal | full | none
    max_tokens: int = 32768
    model: str = "deepseek-v4-pro"
    execution_budget_enabled: bool = True
    tier_a_max_hypotheses: int = 0
    tier_b_max_hypotheses: int = 0
    tier_c_max_hypotheses: int = 0
    tier_a_async_delay_seconds: float = 3.0
    tier_b_async_delay_seconds: float = 0.5
    tier_c_async_delay_seconds: float = 0.0
    tier_b_trim_steps_to: int = 3
    tier_c_trim_steps_to: int = 1
    deployment_mode: str = "private_deployment"  # private_deployment | public_saas | dedicated_cloud
    learning_sync_mode: str = "local_only"  # local_only | import_only | sanitized_export_import | sanitized_api_sync | customer_hub_sync
    deployment_scope_id: str = ""
    environment_class: str = "sandbox"

    def __post_init__(self):
        self.max_requests = max(1, min(int(self.max_requests or 1), 1000))
        self.max_concurrency = max(1, min(int(self.max_concurrency or 1), 8))
        self.http_timeout_seconds = max(1, min(int(self.http_timeout_seconds or 1), 120))
        self.max_tokens = max(32768, min(int(self.max_tokens or 32768), 100000))
        self.execution_budget_enabled = bool(self.execution_budget_enabled)
        # Migrate the previous hardcoded defaults (8/12/0) into uncapped dynamic mode.
        if (
            int(self.tier_a_max_hypotheses or 0) == 8
            and int(self.tier_b_max_hypotheses or 0) == 12
            and int(self.tier_c_max_hypotheses or 0) == 0
        ):
            self.tier_a_max_hypotheses = 0
            self.tier_b_max_hypotheses = 0
        self.tier_a_max_hypotheses = max(0, min(int(self.tier_a_max_hypotheses or 0), 200))
        self.tier_b_max_hypotheses = max(0, min(int(self.tier_b_max_hypotheses or 0), 200))
        self.tier_c_max_hypotheses = max(0, min(int(self.tier_c_max_hypotheses or 0), 200))
        self.tier_a_async_delay_seconds = max(0.0, min(float(self.tier_a_async_delay_seconds or 0.0), 30.0))
        self.tier_b_async_delay_seconds = max(0.0, min(float(self.tier_b_async_delay_seconds or 0.0), 10.0))
        self.tier_c_async_delay_seconds = max(0.0, min(float(self.tier_c_async_delay_seconds or 0.0), 5.0))
        self.tier_b_trim_steps_to = max(1, min(int(self.tier_b_trim_steps_to or 1), 10))
        self.tier_c_trim_steps_to = max(1, min(int(self.tier_c_trim_steps_to or 1), 10))
        deployment_mode = str(self.deployment_mode or "private_deployment").strip().lower()
        if deployment_mode not in {"private_deployment", "public_saas", "dedicated_cloud"}:
            deployment_mode = "private_deployment"
        self.deployment_mode = deployment_mode
        learning_sync_mode = str(self.learning_sync_mode or "local_only").strip().lower()
        if learning_sync_mode not in {"local_only", "import_only", "sanitized_export_import", "sanitized_api_sync", "customer_hub_sync"}:
            learning_sync_mode = "local_only"
        self.learning_sync_mode = learning_sync_mode
        self.deployment_scope_id = str(self.deployment_scope_id or "").strip()[:120]
        environment_class = str(self.environment_class or "sandbox").strip().lower()
        self.environment_class = environment_class or "sandbox"


@dataclass
class StrategyBundle:
    """Complete strategy configuration."""
    reasoner: ReasonerPolicy = field(default_factory=ReasonerPolicy)
    discovery: DiscoveryPolicy = field(default_factory=DiscoveryPolicy)
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)
    execution: ExecutionPolicy = field(default_factory=ExecutionPolicy)


@dataclass
class PolicyRecord:
    """Versioned, immutable policy record."""
    policy_id: str
    policy_version: str
    parent_policy_version: str
    project_scope: str  # global | project | environment
    status: str  # candidate | champion | active | rolled_back | retired
    created_reason: str
    strategy: StrategyBundle
    evaluation_summary: dict = field(default_factory=dict)
    promotion_reason: str = ""
    rollback_reason: str = ""
    effective_from: str = ""
    effective_to: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    signature: str = ""

    def __post_init__(self):
        if not self.signature:
            self.signature = self._compute_signature()

    def _compute_signature(self) -> str:
        """Hash of strategy contents for tamper detection."""
        raw = json.dumps(self.strategy.__dict__, default=str, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# Policy Registry
# ═══════════════════════════════════════════════════════════════════

class PolicyRegistry:
    """Central registry for all policy versions.

    Loads/stores policies as JSON. Supports:
    - Activate/deactivate policies
    - Rollback to a previous version
    - List history
    - Compare two policies
    """

    def __init__(self, registry_path: Path | str | None = None):
        self._path = Path(registry_path) if registry_path else Path(
            "platform_outputs/policy_registry.json"
        )
        self._policies: dict[str, PolicyRecord] = {}
        self._active_policy_id: str | None = None
        if self._path.exists():
            self._load()
        else:
            self._bootstrap()

    def _bootstrap(self):
        """First-run: create baseline policy and activate it."""
        baseline = PolicyRecord(
            policy_id="policy-baseline-001",
            policy_version="v1.0.0-baseline",
            parent_policy_version="",
            project_scope="global",
            status="active",
            created_reason="Auto-created baseline policy on first run",
            strategy=StrategyBundle(),
        )
        self._policies[baseline.policy_id] = baseline
        self._active_policy_id = baseline.policy_id
        baseline.effective_from = baseline.created_at
        self._save()

    def _load(self):
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for pdata in data.get("policies", []):
                p = self._dict_to_record(pdata)
                self._policies[p.policy_id] = p
            self._active_policy_id = data.get("active_policy_id")

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_policy_id": self._active_policy_id,
            "policies": [self._record_to_dict(p) for p in self._policies.values()],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def register(self, policy: PolicyRecord) -> PolicyRecord:
        """Add a new policy (candidate)."""
        if policy.policy_id in self._policies:
            raise ValueError(f"Policy {policy.policy_id} already exists")
        self._policies[policy.policy_id] = policy
        self._save()
        return policy

    def _resolve_policy(self, reference: str | None) -> PolicyRecord | None:
        """Resolve either a policy id or a legacy policy-version reference."""
        if not reference:
            return None
        direct = self._policies.get(reference)
        if direct is not None:
            return direct
        return next((p for p in self._policies.values() if p.policy_version == reference), None)

    def promote(self, policy_id: str, reason: str) -> PolicyRecord:
        """Promote a candidate → champion → active."""
        policy = self._policies.get(policy_id)
        if not policy:
            raise ValueError(f"Policy {policy_id} not found")
        if policy.status == "candidate":
            policy.status = "champion"
        elif policy.status == "champion":
            # Deactivate current active
            if self._active_policy_id and self._active_policy_id != policy_id:
                old = self.get_active()
                if old:
                    old.status = "retired"
                    old.effective_to = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            policy.status = "active"
            self._active_policy_id = policy_id
            policy.effective_from = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        policy.promotion_reason = reason
        self._save()
        return policy

    def rollback(self, policy_id: str, reason: str) -> PolicyRecord:
        """Roll back an active policy to its parent id *or* parent version.

        Historical records use parent_policy_version, while registry keys are
        policy ids.  Resolve both forms and activate the parent directly so a
        retired baseline can be restored safely.
        """
        policy = self._policies.get(policy_id)
        if not policy:
            raise ValueError(f"Policy {policy_id} not found")
        parent = self._resolve_policy(policy.parent_policy_version)
        if parent is None:
            raise ValueError(
                f"Cannot roll back {policy_id}: parent {policy.parent_policy_version!r} not found"
            )

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        policy.status = "rolled_back"
        policy.rollback_reason = reason
        policy.effective_to = now
        if self._active_policy_id and self._active_policy_id != parent.policy_id:
            active = self._policies.get(self._active_policy_id)
            if active and active.policy_id != policy_id:
                active.status = "retired"
                active.effective_to = now
        parent.status = "active"
        parent.effective_from = now
        parent.effective_to = ""
        parent.promotion_reason = f"Rollback from {policy_id}: {reason}"
        # Keep the legacy parent-version reference for backward-compatible
        # persisted registries; get_active() resolves both ids and versions.
        self._active_policy_id = parent.policy_version
        self._save()
        return policy

    def get_active(self) -> PolicyRecord | None:
        """Get the currently active policy (id or legacy version reference)."""
        return self._resolve_policy(self._active_policy_id)

    def get_active_strategy(self) -> StrategyBundle:
        """Get the active strategy, or default if none active."""
        active = self.get_active()
        if active:
            return active.strategy
        return StrategyBundle()  # Default

    def get_history(self, limit: int = 20) -> list[PolicyRecord]:
        """Get policy history, newest first."""
        return sorted(
            self._policies.values(),
            key=lambda p: p.created_at, reverse=True,
        )[:limit]

    def compare(self, pid1: str, pid2: str) -> dict:
        """Compare two policies and return diff."""
        p1 = self._policies.get(pid1)
        p2 = self._policies.get(pid2)
        if not p1 or not p2:
            return {"error": "Policy not found"}
        return {
            "policy_1": {"id": pid1, "version": p1.policy_version, "status": p1.status},
            "policy_2": {"id": pid2, "version": p2.policy_version, "status": p2.status},
            "diff": self._diff_strategies(p1.strategy, p2.strategy),
        }

    def _diff_strategies(self, s1: StrategyBundle, s2: StrategyBundle) -> dict:
        """Compute structural diff between two strategies."""
        d1, d2 = s1.__dict__, s2.__dict__
        diffs = {}
        for key in d1:
            if key not in d2:
                diffs[key] = {"old": str(d1[key]), "new": "MISSING"}
            elif d1[key] != d2[key]:
                sub1, sub2 = d1[key].__dict__ if hasattr(d1[key], '__dict__') else {}, d2[key].__dict__ if hasattr(d2[key], '__dict__') else {}
                if sub1 and sub2:
                    sub_diffs = {k: {"old": sub1.get(k), "new": sub2.get(k)}
                                for k in set(sub1) | set(sub2) if sub1.get(k) != sub2.get(k)}
                    if sub_diffs:
                        diffs[key] = sub_diffs
                else:
                    diffs[key] = {"old": str(d1[key]), "new": str(d2[key])}
        return diffs

    def _record_to_dict(self, p: PolicyRecord) -> dict:
        return {
            "policy_id": p.policy_id, "policy_version": p.policy_version,
            "parent_policy_version": p.parent_policy_version,
            "project_scope": p.project_scope, "status": p.status,
            "created_reason": p.created_reason,
            "strategy": {
                "reasoner": p.strategy.reasoner.__dict__,
                "discovery": p.strategy.discovery.__dict__,
                "verification": p.strategy.verification.__dict__,
                "execution": p.strategy.execution.__dict__,
            },
            "evaluation_summary": p.evaluation_summary,
            "promotion_reason": p.promotion_reason,
            "rollback_reason": p.rollback_reason,
            "effective_from": p.effective_from, "effective_to": p.effective_to,
            "created_at": p.created_at, "signature": p.signature,
        }

    def _dict_to_record(self, d: dict) -> PolicyRecord:
        s = d.get("strategy", {})
        return PolicyRecord(
            policy_id=d.get("policy_id", ""),
            policy_version=d.get("policy_version", ""),
            parent_policy_version=d.get("parent_policy_version", ""),
            project_scope=d.get("project_scope", "global"),
            status=d.get("status", "candidate"),
            created_reason=d.get("created_reason", ""),
            strategy=StrategyBundle(
                reasoner=ReasonerPolicy(**s.get("reasoner", {})),
                discovery=DiscoveryPolicy(**s.get("discovery", {})),
                verification=VerificationPolicy(**s.get("verification", {})),
                execution=ExecutionPolicy(**s.get("execution", {})),
            ),
            evaluation_summary=d.get("evaluation_summary", {}),
            promotion_reason=d.get("promotion_reason", ""),
            rollback_reason=d.get("rollback_reason", ""),
            effective_from=d.get("effective_from", ""),
            effective_to=d.get("effective_to", ""),
            created_at=d.get("created_at", ""),
            signature=d.get("signature", ""),
        )


# ═══════════════════════════════════════════════════════════════════
# Global singleton
# ═══════════════════════════════════════════════════════════════════

_registry: PolicyRegistry | None = None


def get_policy_registry(path: Path | str | None = None) -> PolicyRegistry:
    global _registry
    if _registry is None:
        _registry = PolicyRegistry(path)
    return _registry


def get_active_policy() -> StrategyBundle:
    return get_policy_registry().get_active_strategy()
