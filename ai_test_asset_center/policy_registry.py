"""Machine-readable, versioned and rollback-able runtime policies.

All live strategy values are persisted through the same registry. Runtime
consumers may apply stricter guards, but no policy may widen product-level
safety caps for hypothesis or behavior-slice discovery.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


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
        "prd_text": 45000,
        "api_schema": 50000,
        "observed_data": 12000,
        "heuristic_findings": 12000,
        "reader_json": 20000,
        "lifecycle_definition": 12000,
        "requirement_context": 45000,
        "api_context": 50000,
        "database_context": 25000,
        "bug_history_context": 25000,
    })

    def __post_init__(self) -> None:
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
        self.enabled_engines = list(dict.fromkeys(canonical if not incoming or incoming == legacy_default else incoming))
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
    convergence_threshold: float = 0.30
    verifier_relaxed: bool = False
    scenario_auto: bool = False

    def __post_init__(self) -> None:
        self.async_window_seconds = max(0, min(int(self.async_window_seconds or 0), 300))
        self.verifier_relaxed = False


@dataclass
class ExecutionPolicy:
    max_requests: int = 200
    max_concurrency: int = 4
    http_timeout_seconds: int = 10
    fixture_strategy: str = "minimal"
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
    deployment_mode: str = "private_deployment"
    learning_sync_mode: str = "local_only"
    deployment_scope_id: str = ""
    environment_class: str = "sandbox"
    # V12 source-bound incremental behavior discovery.
    max_behavior_slices_per_round: int = 15
    incremental_discovery_round: int = 1
    # Default raised 3 -> 8 so a single customer scan converges on a rich behavior
    # model (8 rounds x 15 slices/round = 120 slice capacity, comfortably covering
    # models like benchmark_mall's 73 slices incl. all permission/isolation checks).
    # The campaign still stops early at natural convergence (no unattempted
    # source-executable slices), so small models are unaffected.
    incremental_discovery_round_limit: int = 8
    require_runtime_receipt_for_slice_confirmation: bool = True

    def __post_init__(self) -> None:
        self.max_requests = max(1, min(int(self.max_requests or 1), 1000))
        self.max_concurrency = max(1, min(int(self.max_concurrency or 1), 8))
        self.http_timeout_seconds = max(1, min(int(self.http_timeout_seconds or 1), 120))
        self.max_tokens = max(32768, min(int(self.max_tokens or 32768), 100000))
        self.execution_budget_enabled = bool(self.execution_budget_enabled)
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
        self.max_behavior_slices_per_round = max(1, min(int(self.max_behavior_slices_per_round or 1), 15))
        self.incremental_discovery_round = max(1, min(int(self.incremental_discovery_round or 1), 12))
        self.incremental_discovery_round_limit = max(1, min(int(self.incremental_discovery_round_limit or 1), 12))
        self.require_runtime_receipt_for_slice_confirmation = bool(self.require_runtime_receipt_for_slice_confirmation)
        deployment_mode = str(self.deployment_mode or "private_deployment").strip().lower()
        self.deployment_mode = deployment_mode if deployment_mode in {"private_deployment", "public_saas", "dedicated_cloud"} else "private_deployment"
        learning_sync_mode = str(self.learning_sync_mode or "local_only").strip().lower()
        self.learning_sync_mode = learning_sync_mode if learning_sync_mode in {
            "local_only", "import_only", "sanitized_export_import", "sanitized_api_sync", "customer_hub_sync"
        } else "local_only"
        self.deployment_scope_id = str(self.deployment_scope_id or "").strip()[:120]
        self.environment_class = str(self.environment_class or "sandbox").strip().lower() or "sandbox"


@dataclass
class StrategyBundle:
    reasoner: ReasonerPolicy = field(default_factory=ReasonerPolicy)
    discovery: DiscoveryPolicy = field(default_factory=DiscoveryPolicy)
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)
    execution: ExecutionPolicy = field(default_factory=ExecutionPolicy)


@dataclass
class PolicyRecord:
    policy_id: str
    policy_version: str
    parent_policy_version: str
    project_scope: str
    status: str
    created_reason: str
    strategy: StrategyBundle
    evaluation_summary: dict[str, Any] = field(default_factory=dict)
    promotion_reason: str = ""
    rollback_reason: str = ""
    effective_from: str = ""
    effective_to: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.signature:
            self.signature = self._compute_signature()

    def _compute_signature(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self.strategy), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


class PolicyRegistry:
    """Persist, promote, compare and roll back complete strategy bundles."""

    def __init__(self, registry_path: Path | str | None = None):
        self._path = Path(registry_path) if registry_path else Path("platform_outputs/policy_registry.json")
        self._policies: dict[str, PolicyRecord] = {}
        self._active_policy_id: str | None = None
        if self._path.exists():
            self._load()
        else:
            self._bootstrap()

    def _bootstrap(self) -> None:
        baseline = PolicyRecord(
            policy_id="policy-baseline-001",
            policy_version="v1.0.0-baseline",
            parent_policy_version="",
            project_scope="global",
            status="active",
            created_reason="Auto-created baseline policy on first run",
            strategy=StrategyBundle(),
        )
        baseline.effective_from = baseline.created_at
        self._policies[baseline.policy_id] = baseline
        self._active_policy_id = baseline.policy_id
        self._save()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._bootstrap()
            return
        for raw in data.get("policies", []):
            if isinstance(raw, dict):
                record = self._dict_to_record(raw)
                if record.policy_id:
                    self._policies[record.policy_id] = record
        self._active_policy_id = data.get("active_policy_id")
        if not self._policies:
            self._bootstrap()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_policy_id": self._active_policy_id,
            "policies": [self._record_to_dict(item) for item in self._policies.values()],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def register(self, policy: PolicyRecord) -> PolicyRecord:
        if policy.policy_id in self._policies:
            raise ValueError(f"Policy {policy.policy_id} already exists")
        self._policies[policy.policy_id] = policy
        self._save()
        return policy

    def _resolve_policy(self, reference: str | None) -> PolicyRecord | None:
        if not reference:
            return None
        return self._policies.get(reference) or next(
            (item for item in self._policies.values() if item.policy_version == reference),
            None,
        )

    def promote(self, policy_id: str, reason: str) -> PolicyRecord:
        policy = self._policies.get(policy_id)
        if policy is None:
            raise ValueError(f"Policy {policy_id} not found")
        if policy.status == "candidate":
            policy.status = "champion"
        elif policy.status == "champion":
            previous = self.get_active()
            if previous and previous.policy_id != policy_id:
                previous.status = "retired"
                previous.effective_to = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            policy.status = "active"
            policy.effective_from = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._active_policy_id = policy_id
        policy.promotion_reason = reason
        self._save()
        return policy

    def rollback(self, policy_id: str, reason: str) -> PolicyRecord:
        policy = self._policies.get(policy_id)
        if policy is None:
            raise ValueError(f"Policy {policy_id} not found")
        parent = self._resolve_policy(policy.parent_policy_version)
        if parent is None:
            raise ValueError(f"Cannot roll back {policy_id}: parent {policy.parent_policy_version!r} not found")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        policy.status = "rolled_back"
        policy.rollback_reason = reason
        policy.effective_to = now
        active = self.get_active()
        if active and active.policy_id not in {policy.policy_id, parent.policy_id}:
            active.status = "retired"
            active.effective_to = now
        parent.status = "active"
        parent.effective_from = now
        parent.effective_to = ""
        parent.promotion_reason = f"Rollback from {policy_id}: {reason}"
        self._active_policy_id = parent.policy_version
        self._save()
        return policy

    def get_active(self) -> PolicyRecord | None:
        return self._resolve_policy(self._active_policy_id)

    def get_active_strategy(self) -> StrategyBundle:
        active = self.get_active()
        return active.strategy if active else StrategyBundle()

    def get_history(self, limit: int = 20) -> list[PolicyRecord]:
        return sorted(self._policies.values(), key=lambda item: item.created_at, reverse=True)[:max(1, int(limit or 1))]

    def compare(self, pid1: str, pid2: str) -> dict[str, Any]:
        first, second = self._policies.get(pid1), self._policies.get(pid2)
        if not first or not second:
            return {"error": "Policy not found"}
        return {
            "policy_1": {"id": pid1, "version": first.policy_version, "status": first.status},
            "policy_2": {"id": pid2, "version": second.policy_version, "status": second.status},
            "diff": self._diff_strategies(first.strategy, second.strategy),
        }

    @staticmethod
    def _diff_strategies(first: StrategyBundle, second: StrategyBundle) -> dict[str, Any]:
        old, new = asdict(first), asdict(second)
        diff: dict[str, Any] = {}
        for section in sorted(set(old) | set(new)):
            if old.get(section) == new.get(section):
                continue
            first_section = old.get(section) if isinstance(old.get(section), dict) else {}
            second_section = new.get(section) if isinstance(new.get(section), dict) else {}
            diff[section] = {
                key: {"old": first_section.get(key), "new": second_section.get(key)}
                for key in sorted(set(first_section) | set(second_section))
                if first_section.get(key) != second_section.get(key)
            }
        return diff

    @staticmethod
    def _record_to_dict(policy: PolicyRecord) -> dict[str, Any]:
        return {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "parent_policy_version": policy.parent_policy_version,
            "project_scope": policy.project_scope,
            "status": policy.status,
            "created_reason": policy.created_reason,
            "strategy": asdict(policy.strategy),
            "evaluation_summary": policy.evaluation_summary,
            "promotion_reason": policy.promotion_reason,
            "rollback_reason": policy.rollback_reason,
            "effective_from": policy.effective_from,
            "effective_to": policy.effective_to,
            "created_at": policy.created_at,
            "signature": policy.signature,
        }

    @staticmethod
    def _allowed(cls: type[Any], value: Any) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        names = {item.name for item in fields(cls)}
        return {key: raw[key] for key in raw if key in names}

    def _dict_to_record(self, data: dict[str, Any]) -> PolicyRecord:
        strategy = data.get("strategy") if isinstance(data.get("strategy"), dict) else {}
        return PolicyRecord(
            policy_id=str(data.get("policy_id") or ""),
            policy_version=str(data.get("policy_version") or ""),
            parent_policy_version=str(data.get("parent_policy_version") or ""),
            project_scope=str(data.get("project_scope") or "global"),
            status=str(data.get("status") or "candidate"),
            created_reason=str(data.get("created_reason") or ""),
            strategy=StrategyBundle(
                reasoner=ReasonerPolicy(**self._allowed(ReasonerPolicy, strategy.get("reasoner"))),
                discovery=DiscoveryPolicy(**self._allowed(DiscoveryPolicy, strategy.get("discovery"))),
                verification=VerificationPolicy(**self._allowed(VerificationPolicy, strategy.get("verification"))),
                execution=ExecutionPolicy(**self._allowed(ExecutionPolicy, strategy.get("execution"))),
            ),
            evaluation_summary=data.get("evaluation_summary") if isinstance(data.get("evaluation_summary"), dict) else {},
            promotion_reason=str(data.get("promotion_reason") or ""),
            rollback_reason=str(data.get("rollback_reason") or ""),
            effective_from=str(data.get("effective_from") or ""),
            effective_to=str(data.get("effective_to") or ""),
            created_at=str(data.get("created_at") or "") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            signature=str(data.get("signature") or ""),
        )


_registry: PolicyRegistry | None = None


def get_policy_registry(path: Path | str | None = None) -> PolicyRegistry:
    global _registry
    if _registry is None:
        _registry = PolicyRegistry(path)
    return _registry


def get_active_policy() -> StrategyBundle:
    return get_policy_registry().get_active_strategy()
