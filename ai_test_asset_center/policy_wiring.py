"""Policy wiring with non-negotiable runtime safety guardrails."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
from typing import Any


# RUNTIME AUTHORITY for the per-engine hypothesis cap (AGENTS.md config floors).
# This value — not the literal in ``stage_reason_all_v2`` — is what production
# actually runs: ``_enforce_stage_reasoner_static_cap`` writes it onto that
# module and ``_clamp_reasoner_hypothesis_cap`` clamps every policy/env request
# to it.  Keep it equal to ``stage_reason_all_v2.MAX_HYPOTHESES``; the two are
# cross-checked by tests/test_reasoner_static_guardrails.py.
_REASONER_MAX_HYPOTHESES_PER_ENGINE = 64
_REASONER_HYPOTHESIS_CAP_ENV = "QUALIBUG_REASONER_MAX_HYPOTHESES_PER_ENGINE"
_BEHAVIOR_SLICE_MAX_PER_ROUND = 15
_INCREMENTAL_DISCOVERY_ROUND_MAX = 12
_BEHAVIOR_SLICE_EXECUTION_DEFAULTS: dict[str, int] = {
    "max_behavior_slices_per_round": _BEHAVIOR_SLICE_MAX_PER_ROUND,
    "incremental_discovery_round": 1,
    "incremental_discovery_round_limit": 3,
}
_BEHAVIOR_SLICE_EXECUTION_ENV: dict[str, str] = {
    "max_behavior_slices_per_round": "QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND",
    "incremental_discovery_round": "QUALIBUG_DISCOVERY_ROUND",
    "incremental_discovery_round_limit": "QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT",
}
_REASONER_GUARDRAIL_LIMIT_NAMES = ("MAX_HYPOTHESES", "MAX_HYPOTHESES_HARD_LIMIT")
_STRATEGY_OVERRIDE: ContextVar[Any | None] = ContextVar("qualibug_strategy_override", default=None)


@contextmanager
def policy_strategy_override(strategy: Any) -> Iterator[Any]:
    """Temporarily exercise a candidate without mutating the active registry.

    Champion/challenger evaluation is sequentially isolated by ContextVar. A
    malformed strategy fails before the first target request.
    """

    from .discovery_harness_proposer import validate_strategy_guardrails
    from .policy_registry import StrategyBundle

    if not isinstance(strategy, StrategyBundle):
        raise TypeError("policy_strategy_override requires StrategyBundle")
    guardrails = validate_strategy_guardrails(strategy)
    if not guardrails.get("passed"):
        failed = [item.get("name") for item in guardrails.get("checks") or [] if not item.get("passed")]
        raise ValueError(f"candidate policy violates runtime guardrails: {failed}")
    token = _STRATEGY_OVERRIDE.set(strategy)
    try:
        yield strategy
    finally:
        _STRATEGY_OVERRIDE.reset(token)


def get_effective_policy_strategy() -> Any:
    """Return the exact in-context strategy or fail if the registry is unavailable."""

    strategy = _STRATEGY_OVERRIDE.get()
    if strategy is not None:
        return strategy
    from .policy_registry import get_active_policy

    strategy = get_active_policy()
    if strategy is None:
        raise RuntimeError("active policy strategy is unavailable")
    return strategy


def _loaded_stage_reasoner_module() -> Any:
    """Return the stage Reasoner module or surface its import failure."""

    module_name = f"{__package__}.stage_reason_all_v2"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    from . import stage_reason_all_v2

    return stage_reason_all_v2


def _enforce_stage_reasoner_static_cap() -> None:
    """Harden the main Reasoner module against legacy widened defaults.

    ``stage_reason_all_v2`` historically exposed larger module-level defaults.
    Product calls resolve policy through this module, so enforce the canonical
    cap on the actual module before returning a reasoner budget.
    """
    module = _loaded_stage_reasoner_module()
    for name in _REASONER_GUARDRAIL_LIMIT_NAMES:
        if hasattr(module, name):
            setattr(module, name, _REASONER_MAX_HYPOTHESES_PER_ENGINE)


def assert_reasoner_guardrails() -> None:
    """Fail fast if runtime reasoner guardrails drift below/above policy.

    This is intentionally a runtime assertion rather than a health badge: it only
    validates in-process configuration after code is imported. It does not claim
    external LLM connectivity or product readiness.
    """
    _enforce_stage_reasoner_static_cap()
    module = _loaded_stage_reasoner_module()
    for name in _REASONER_GUARDRAIL_LIMIT_NAMES:
        if hasattr(module, name):
            value = getattr(module, name)
            assert value == _REASONER_MAX_HYPOTHESES_PER_ENGINE, (
                f"{name} must remain {_REASONER_MAX_HYPOTHESES_PER_ENGINE}, got {value}"
            )


def _clamp_reasoner_hypothesis_cap(value: Any, fallback: Any) -> int:
    """Keep policy or environment data from widening the product budget."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        try:
            requested = int(fallback)
        except (TypeError, ValueError):
            requested = _REASONER_MAX_HYPOTHESES_PER_ENGINE
    return max(1, min(requested, _REASONER_MAX_HYPOTHESES_PER_ENGINE))


def _reasoner_hypothesis_cap(value: Any, fallback: Any) -> int:
    """Apply the optional environment override through the same hard cap."""
    _enforce_stage_reasoner_static_cap()
    environment_value = os.environ.get(_REASONER_HYPOTHESIS_CAP_ENV)
    return _clamp_reasoner_hypothesis_cap(
        environment_value if environment_value not in (None, "") else value,
        fallback,
    )


def _bounded_int(value: Any, fallback: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        try:
            parsed = int(fallback)
        except (TypeError, ValueError):
            parsed = minimum
    return max(minimum, min(parsed, maximum))


def _behavior_slice_execution_value(key: str, value: Any, fallback: Any) -> int:
    """Apply one canonical guardrail to policy and environment inputs."""
    environment_name = _BEHAVIOR_SLICE_EXECUTION_ENV[key]
    environment_value = os.environ.get(environment_name)
    effective = environment_value if environment_value not in (None, "") else value
    if key == "max_behavior_slices_per_round":
        return _bounded_int(effective, fallback, 1, _BEHAVIOR_SLICE_MAX_PER_ROUND)
    return _bounded_int(effective, fallback, 1, _INCREMENTAL_DISCOVERY_ROUND_MAX)


def bind_product_installed_mainline_authority() -> dict[str, Any]:
    """Align the active registry with the product-installed discovery runner.

    Private-pilot product surfaces install only ``experiment_candidate``.
    Persisted registries created under the former ``legacy_champion`` default
    would otherwise fail closed before campaign creation. This bind runs at
    process start, never mid-campaign.

    ``legacy_champion`` is compatibility-only. PRODUCT mode must execute the
    exact runner whose capability authorities are declared by the product
    manifest; allowing a legacy runner here would make the report and runtime
    disagree about who owns the product mainline.
    """

    # ``private_pilot_entrypoint.run_server`` invokes this before binding the
    # service socket. PRODUCT is strict; COMPATIBILITY must be explicitly
    # selected. BENCHMARK/TEST modes are not allowed to launch the product
    # entrypoint, keeping evaluator/test authorities isolated from customers.
    from .authority_manifest import validate_resolved_authorities_for_startup

    authority_report = validate_resolved_authorities_for_startup()
    authority_mode = str(authority_report.get("authority_mode") or "").upper()
    if authority_mode not in {"PRODUCT", "COMPATIBILITY"}:
        raise RuntimeError(
            f"non_product_authority_mode_for_product_entrypoint:{authority_mode}"
        )

    from .policy_registry import get_policy_registry

    requested = str(os.environ.get("QUALIBUG_MAINLINE_AUTHORITY") or "").strip()
    target = requested or "experiment_candidate"
    if target not in {"legacy_champion", "experiment_candidate"}:
        raise ValueError(f"invalid QUALIBUG_MAINLINE_AUTHORITY: {requested}")
    if authority_mode == "PRODUCT" and target != "experiment_candidate":
        raise RuntimeError(
            f"legacy_mainline_forbidden_in_product_mode:{target}"
        )

    registry = get_policy_registry()
    active = registry.get_active()
    if active is None:
        raise RuntimeError("active policy is unavailable for mainline bind")
    previous = str(active.strategy.execution.mainline_authority or "").strip()
    if previous == target:
        return {
            "changed": False,
            "mainline_authority": target,
            "policy_id": active.policy_id,
            "source": "env" if requested else "active_policy",
            "authority_mode": authority_mode,
            "authority_manifest": authority_report,
        }

    active.strategy.execution.mainline_authority = target
    active.signature = active._compute_signature()
    registry._events.append(
        registry._event(
            "bind_product_mainline_authority",
            active,
            f"previous={previous}:target={target}:source="
            f"{'env' if requested else 'product_installed_runner'}",
        )
    )
    registry._save()
    return {
        "changed": True,
        "mainline_authority": target,
        "previous_mainline_authority": previous,
        "policy_id": active.policy_id,
        "source": "env" if requested else "product_installed_runner",
        "authority_mode": authority_mode,
        "authority_manifest": authority_report,
    }


def get_policy_value(section: str, key: str, default: Any) -> Any:
    """Read an active policy value while enforcing product-level guardrails."""
    value = default
    strategy = _STRATEGY_OVERRIDE.get()
    if strategy is None:
        try:
            from .policy_registry import get_active_policy

            strategy = get_active_policy()
        except Exception:
            strategy = None
    section_obj = getattr(strategy, section, None) if strategy is not None else None
    if section_obj and hasattr(section_obj, key):
        value = getattr(section_obj, key)

    if section == "reasoner" and key == "max_hypotheses_per_engine":
        return _reasoner_hypothesis_cap(value, default)
    if section == "execution" and key in _BEHAVIOR_SLICE_EXECUTION_DEFAULTS:
        return _behavior_slice_execution_value(key, value, default)
    return value


def get_policy_dict(section: str) -> dict[str, Any]:
    """Get active policy values, including guardrail-normalized values."""
    try:
        strategy = _STRATEGY_OVERRIDE.get()
        if strategy is None:
            from .policy_registry import get_active_policy

            strategy = get_active_policy()
        section_obj = getattr(strategy, section, None)
        if section_obj:
            payload = {key: value for key, value in section_obj.__dict__.items() if not key.startswith("_")}
            if section == "reasoner" and "max_hypotheses_per_engine" in payload:
                payload["max_hypotheses_per_engine"] = _reasoner_hypothesis_cap(
                    payload["max_hypotheses_per_engine"],
                    _REASONER_MAX_HYPOTHESES_PER_ENGINE,
                )
            if section == "execution":
                for key, default in _BEHAVIOR_SLICE_EXECUTION_DEFAULTS.items():
                    payload[key] = _behavior_slice_execution_value(key, payload.get(key, default), default)
            return payload
    except Exception:
        pass
    return {}
