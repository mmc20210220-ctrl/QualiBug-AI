"""Machine-readable, versioned and rollback-able runtime policies.

All live strategy values are persisted through the same registry. Runtime
consumers may apply stricter guards, but no policy may widen product-level
safety caps for hypothesis or behavior-slice discovery.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .discovery_evaluation_contract import (
    EvaluationContractError,
    POLICY_COMPARISON_AUTHENTICATION_FIELD,
    POLICY_COMPARISON_FINGERPRINT_FIELD,
    POLICY_COMPARISON_SCHEMA,
    validate_authenticated_policy_comparison,
)
from .policy_wiring import _REASONER_MAX_HYPOTHESES_PER_ENGINE


POLICY_REGISTRY_SCHEMA = "qualibug.policy-registry.v2"
OBSERVED_COMPARISON_SCHEMA = POLICY_COMPARISON_SCHEMA
OBSERVED_COMPARISON_FINGERPRINT_FIELD = POLICY_COMPARISON_FINGERPRINT_FIELD
OBSERVED_COMPARISON_AUTHENTICATION_FIELD = (
    POLICY_COMPARISON_AUTHENTICATION_FIELD
)


class PolicyRegistryError(RuntimeError):
    """Policy lineage or persistence is invalid and cannot be used safely."""


def _full_strategy_signature(strategy: "StrategyBundle") -> str:
    return hashlib.sha256(
        json.dumps(
            asdict(strategy),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def strategy_fingerprint(strategy: "StrategyBundle") -> str:
    """Return the canonical full fingerprint used in policy/run receipts."""

    return _full_strategy_signature(strategy)


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
    max_hypotheses_per_engine: int = _REASONER_MAX_HYPOTHESES_PER_ENGINE
    max_hypothesis_chars: int = 500
    retry_delay_seconds: float = 2.0
    prompt_truncation_chars: dict[str, int] = field(default_factory=lambda: {
        # Token-safe input bounds (CJK-aware). The prior 45000/50000-char caps
        # were per-slot *character* ceilings that, multiplied across several
        # placeholder slots re-using the same corpus and through CJK ~1 token
        # per char, pushed a single reasoner prompt past the provider context
        # window. These floors stay well above the reader's own 8000-char
        # bound and remain operator-overridable via the policy registry.
        "prd_text": 12000,
        "api_schema": 12000,
        "observed_data": 6000,
        "heuristic_findings": 6000,
        "reader_json": 8000,
        "lifecycle_definition": 6000,
        "requirement_context": 12000,
        "api_context": 12000,
        "database_context": 12000,
        "bug_history_context": 12000,
    })

    def __post_init__(self) -> None:
        self.timeout_seconds = max(int(self.timeout_seconds or 0), 300)
        self.max_tokens = max(32768, min(int(self.max_tokens or 32768), 100000))
        self.max_workers = max(1, min(int(self.max_workers or 1), 4))
        self.retry_count = max(0, min(int(self.retry_count or 0), 1))
        # Persisted policies created under the legacy default of 15 must not
        # silently keep production below the package guardrail of 40. A lower
        # per-run operator budget remains available through the explicit,
        # receipted environment override in policy_wiring.
        self.max_hypotheses_per_engine = max(
            _REASONER_MAX_HYPOTHESES_PER_ENGINE,
            min(
                int(
                    self.max_hypotheses_per_engine
                    or _REASONER_MAX_HYPOTHESES_PER_ENGINE
                ),
                _REASONER_MAX_HYPOTHESES_PER_ENGINE,
            ),
        )
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
    candidate_ranking_signals: list[str] = field(default_factory=lambda: [
        "source_strength",
        "endpoint_executability",
        "evidence_gap",
        "historical_yield",
    ])
    endpoint_binding_strategy: list[str] = field(default_factory=lambda: [
        "source_operation_id",
        "method_path_shape",
        "schema_parameter_compatibility",
        "documented_example_binding",
    ])
    endpoint_binding_diagnostic_sample_limit: int = 20
    min_source_refs_for_execution: int = 1
    require_documented_endpoint: bool = True

    def __post_init__(self) -> None:
        self.max_hypotheses_execute = max(1, min(int(self.max_hypotheses_execute or 1), 200))
        self.max_rounds = max(1, min(int(self.max_rounds or 1), 24))
        self.stagnation_limit = max(1, min(int(self.stagnation_limit or 1), 12))
        self.endpoint_binding_diagnostic_sample_limit = max(
            1, min(int(self.endpoint_binding_diagnostic_sample_limit or 1), 100)
        )
        self.min_source_refs_for_execution = max(1, min(int(self.min_source_refs_for_execution or 1), 5))
        self.require_documented_endpoint = True
        self.candidate_ranking_signals = list(dict.fromkeys(
            str(item).strip() for item in (self.candidate_ranking_signals or []) if str(item).strip()
        ))
        self.endpoint_binding_strategy = list(dict.fromkeys(
            str(item).strip() for item in (self.endpoint_binding_strategy or []) if str(item).strip()
        ))


@dataclass
class VerificationPolicy:
    observer_priority: dict[str, float] = field(default_factory=dict)
    async_window_seconds: int = 0
    evidence_collection_order: list[str] = field(default_factory=list)
    convergence_threshold: float = 0.30
    verifier_relaxed: bool = False
    scenario_auto: bool = False
    reject_non_execution_oracle_votes: bool = True
    require_valid_success_control_for_5xx: bool = True
    require_joinable_execution_trace: bool = True
    require_cleanup_success_for_customer_delivery: bool = True

    def __post_init__(self) -> None:
        self.async_window_seconds = max(0, min(int(self.async_window_seconds or 0), 300))
        self.verifier_relaxed = False
        self.reject_non_execution_oracle_votes = True
        self.require_valid_success_control_for_5xx = True
        self.require_joinable_execution_trace = True
        self.require_cleanup_success_for_customer_delivery = True


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
    runtime_binding_sources: list[str] = field(default_factory=lambda: [
        "prior_step_extract",
        "documented_list_response",
        "fixture_receipt",
    ])
    precondition_resolution_attempts: int = 4
    cleanup_retry_count: int = 1
    cleanup_created_resource_id_sources: list[str] = field(default_factory=lambda: [
        "response_body_id",
        "location_header",
        "audit_receipt",
    ])
    trace_join_key_order: list[str] = field(default_factory=lambda: [
        "evidence_id",
        "scenario_and_slice_id",
    ])
    require_cleanup_receipt: bool = True
    persist_cross_round_traces: bool = True
    # Frozen before a run. Runtime errors must never switch this authority.
    # Product installs only experiment_candidate; legacy_champion remains an
    # explicit select-before-run choice when a gate-verifiable runner exists.
    mainline_authority: str = "experiment_candidate"

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
        self.runtime_binding_sources = list(dict.fromkeys(
            str(item).strip() for item in (self.runtime_binding_sources or []) if str(item).strip()
        ))
        self.precondition_resolution_attempts = max(1, min(int(self.precondition_resolution_attempts or 1), 5))
        self.cleanup_retry_count = max(0, min(int(self.cleanup_retry_count or 0), 3))
        self.cleanup_created_resource_id_sources = list(dict.fromkeys(
            str(item).strip()
            for item in (self.cleanup_created_resource_id_sources or [])
            if str(item).strip()
        ))
        self.trace_join_key_order = list(dict.fromkeys(
            str(item).strip() for item in (self.trace_join_key_order or []) if str(item).strip()
        ))
        self.require_cleanup_receipt = True
        self.persist_cross_round_traces = True
        self.mainline_authority = str(self.mainline_authority or "").strip()
        if self.mainline_authority not in {"legacy_champion", "experiment_candidate"}:
            raise ValueError(f"invalid mainline_authority: {self.mainline_authority}")
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
        self._events: list[dict[str, Any]] = []
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
        self._events.append(self._event("bootstrap", baseline, "registry_initialized"))
        self._save()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyRegistryError(f"policy registry is unreadable or corrupt: {self._path}: {exc}") from exc
        if not isinstance(data, dict):
            raise PolicyRegistryError(f"policy registry root must be an object: {self._path}")
        for raw in data.get("policies", []):
            if isinstance(raw, dict):
                record = self._dict_to_record(raw)
                if record.policy_id:
                    self._policies[record.policy_id] = record
        self._active_policy_id = data.get("active_policy_id")
        self._events = [dict(item) for item in (data.get("events") or []) if isinstance(item, dict)]
        if not self._policies:
            raise PolicyRegistryError(f"policy registry contains no policies: {self._path}")
        active = self.get_active()
        if active is None:
            raise PolicyRegistryError(
                f"policy registry active reference is missing or dangling: {self._active_policy_id!r}"
            )
        if active.status != "active":
            raise PolicyRegistryError(
                f"policy registry active reference points to status {active.status!r}: {active.policy_id}"
            )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": POLICY_REGISTRY_SCHEMA,
            "active_policy_id": self._active_policy_id,
            "policies": [self._record_to_dict(item) for item in self._policies.values()],
            "events": list(self._events),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        temporary = self._path.with_suffix(self._path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temporary, self._path)

    def register(self, policy: PolicyRecord) -> PolicyRecord:
        if policy.policy_id in self._policies:
            raise ValueError(f"Policy {policy.policy_id} already exists")
        if policy.status == "candidate":
            parent = self._resolve_policy(policy.parent_policy_version)
            if parent is None:
                raise PolicyRegistryError(
                    f"candidate parent policy is missing: {policy.parent_policy_version!r}"
                )
            if parent.status != "active":
                raise PolicyRegistryError(
                    f"candidate parent must be the active champion, got status {parent.status!r}"
                )
            if _full_strategy_signature(policy.strategy) == _full_strategy_signature(parent.strategy):
                raise PolicyRegistryError("candidate strategy must differ from its parent")
        policy.signature = policy._compute_signature()
        self._policies[policy.policy_id] = policy
        self._events.append(self._event("register", policy, policy.created_reason))
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
        reason = str(reason or "").strip()
        if not reason:
            raise PolicyRegistryError("policy promotion requires a non-empty reason")
        self._validate_observed_evaluation(policy)
        parent = self._resolve_policy(policy.parent_policy_version)
        if parent is None:
            raise PolicyRegistryError("candidate parent policy is missing during promotion")
        if policy.status == "candidate":
            if self.get_active() is not parent:
                raise PolicyRegistryError("candidate parent is no longer the active champion")
            policy.status = "champion"
            self._events.append(self._event("approve_challenger", policy, reason))
        elif policy.status == "champion":
            previous = self.get_active()
            if previous is not parent:
                raise PolicyRegistryError("champion parent is no longer active; activation is stale")
            previous.status = "retired"
            previous.effective_to = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            policy.status = "active"
            policy.effective_from = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._active_policy_id = policy_id
            self._events.append(self._event("activate", policy, reason))
        else:
            raise PolicyRegistryError(
                f"policy status {policy.status!r} cannot be promoted; expected candidate or champion"
            )
        policy.promotion_reason = reason
        self._save()
        return policy

    def _validate_observed_evaluation(
        self, policy: PolicyRecord
    ) -> dict[str, Any]:
        evaluation = policy.evaluation_summary if isinstance(policy.evaluation_summary, dict) else {}
        if evaluation.get("schema_version") != OBSERVED_COMPARISON_SCHEMA:
            raise PolicyRegistryError("candidate evaluation summary does not use the observed comparison schema")
        if evaluation.get("promote") is not True:
            raise PolicyRegistryError("candidate cannot advance without a passing evaluation summary")
        if evaluation.get("observed_execution") is not True or evaluation.get("estimated_metrics_used") is not False:
            raise PolicyRegistryError("candidate cannot advance without observed, non-estimated evaluation evidence")
        comparison_ref = Path(str(evaluation.get("comparison_ref") or "").strip())
        if not comparison_ref.is_file():
            raise PolicyRegistryError("candidate comparison reference is missing or not a file")
        comparison_bytes = comparison_ref.read_bytes()
        claimed_file_hash = str(evaluation.get("comparison_file_sha256") or "").strip()
        actual_file_hash = hashlib.sha256(comparison_bytes).hexdigest()
        if not claimed_file_hash or claimed_file_hash != actual_file_hash:
            raise PolicyRegistryError("candidate comparison artifact fingerprint mismatch")
        try:
            comparison = json.loads(comparison_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyRegistryError("candidate comparison artifact is invalid JSON") from exc
        if not isinstance(comparison, dict) or comparison.get("schema_version") != OBSERVED_COMPARISON_SCHEMA:
            raise PolicyRegistryError("candidate comparison artifact schema is invalid")
        challenger = comparison.get("challenger") if isinstance(comparison.get("challenger"), dict) else {}
        current_strategy_fingerprint = _full_strategy_signature(policy.strategy)
        if challenger.get("strategy_fingerprint") != current_strategy_fingerprint:
            raise PolicyRegistryError("candidate strategy changed after observed evaluation")
        if evaluation.get("strategy_fingerprint") != current_strategy_fingerprint:
            raise PolicyRegistryError("candidate evaluation summary strategy fingerprint mismatch")
        parent = self._resolve_policy(policy.parent_policy_version)
        if parent is None:
            raise PolicyRegistryError(
                "candidate parent policy is missing during evaluation validation"
            )
        expected_champion_identity = {
            "policy_id": parent.policy_id,
            "policy_version": parent.policy_version,
            "parent_policy_version": parent.parent_policy_version,
            "strategy_fingerprint": _full_strategy_signature(parent.strategy),
        }
        expected_challenger_identity = {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "parent_policy_version": policy.parent_policy_version,
            "strategy_fingerprint": _full_strategy_signature(policy.strategy),
        }
        try:
            comparison = validate_authenticated_policy_comparison(
                comparison,
                expected_champion_identity=expected_champion_identity,
                expected_challenger_identity=expected_challenger_identity,
            )
        except EvaluationContractError as exc:
            raise PolicyRegistryError(
                f"candidate comparison strict validation failed: {exc}"
            ) from exc
        challenger = comparison.get("challenger") if isinstance(comparison.get("challenger"), dict) else {}
        if challenger.get("policy_id") != policy.policy_id:
            raise PolicyRegistryError("candidate comparison artifact policy identity mismatch")
        if challenger.get("policy_version") != policy.policy_version:
            raise PolicyRegistryError("candidate comparison artifact policy version mismatch")
        if comparison.get("observed_execution") is not True or comparison.get("estimated_metrics_used") is not False:
            raise PolicyRegistryError("candidate comparison artifact is not observed execution evidence")
        decision = comparison.get("promotion_decision") if isinstance(comparison.get("promotion_decision"), dict) else {}
        if decision.get("promote") is not True:
            raise PolicyRegistryError("candidate comparison artifact does not authorize promotion")
        if comparison.get("activation_performed") is not False:
            raise PolicyRegistryError("candidate comparison artifact must precede activation")
        if evaluation.get("dataset_manifest_fingerprint") != comparison.get("dataset_manifest_fingerprint"):
            raise PolicyRegistryError("candidate evaluation dataset fingerprint mismatch")
        return evaluation

    def rollback(self, policy_id: str, reason: str) -> PolicyRecord:
        policy = self._policies.get(policy_id)
        if policy is None:
            raise ValueError(f"Policy {policy_id} not found")
        parent = self._resolve_policy(policy.parent_policy_version)
        if parent is None:
            raise ValueError(f"Cannot roll back {policy_id}: parent {policy.parent_policy_version!r} not found")
        reason = str(reason or "").strip()
        if not reason:
            raise PolicyRegistryError("rollback requires a non-empty reason")
        if self.get_active() is not policy or policy.status != "active":
            raise PolicyRegistryError("only the currently active child policy can be rolled back")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        policy.status = "rolled_back"
        policy.rollback_reason = reason
        policy.effective_to = now
        parent.status = "active"
        parent.effective_from = now
        parent.effective_to = ""
        parent.promotion_reason = f"Rollback from {policy_id}: {reason}"
        self._active_policy_id = parent.policy_id
        self._events.append(self._event("rollback", policy, reason, parent_policy_id=parent.policy_id))
        self._save()
        return policy

    @staticmethod
    def _event(
        event_type: str,
        policy: PolicyRecord,
        reason: str,
        *,
        parent_policy_id: str = "",
    ) -> dict[str, Any]:
        return {
            "event_id": hashlib.sha256(
                f"{time.time_ns()}:{event_type}:{policy.policy_id}".encode("utf-8")
            ).hexdigest()[:20],
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event_type": event_type,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "parent_policy_version": policy.parent_policy_version,
            "parent_policy_id": parent_policy_id,
            "strategy_signature": policy.signature,
            "reason": str(reason or ""),
            "comparison_ref": str((policy.evaluation_summary or {}).get("comparison_ref") or ""),
        }

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
