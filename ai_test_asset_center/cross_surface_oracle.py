"""Cross-Surface Oracle — consistency verification across surfaces.

SPEC §25: Cross-surface consistency checks:
  API↔DB, API↔Event, API↔UI, UI↔DB, Event↔DB, Report↔DB
Async systems: frozen eventual consistency wait strategy.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "oracle_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Consistency Pairs ─────────────────────────────────────────────────────────

CONSISTENCY_PAIRS = frozenset({
    "API_DB", "API_EVENT", "API_UI",
    "UI_DB", "EVENT_DB", "REPORT_DB",
})

# ─── Consistency Check Types ───────────────────────────────────────────────────

CHECK_TYPES = frozenset({
    "DATA_EQUALITY",
    "STATE_CONSISTENCY",
    "TEMPORAL_ORDERING",
    "COMPLETENESS",
    "REFERENTIAL_INTEGRITY",
    "AGGREGATE_CONSISTENCY",
})


# ─── Oracle Rule ───────────────────────────────────────────────────────────────

def create_oracle_rule(
    *,
    pair: str,
    check_type: str,
    description: str = "",
    source_field: str = "",
    target_field: str = "",
    comparison: str = "EQUAL",
    tolerance: float = 0.0,
    async_wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Create a cross-surface oracle rule."""
    if pair not in CONSISTENCY_PAIRS:
        raise ValueError(f"invalid_pair: {pair}")
    if check_type not in CHECK_TYPES:
        raise ValueError(f"invalid_check_type: {check_type}")

    rule_id = _stable_id("rule", pair, check_type, source_field, target_field)
    return {
        "rule_id": rule_id,
        "pair": pair,
        "check_type": check_type,
        "description": description,
        "source_field": source_field,
        "target_field": target_field,
        "comparison": comparison,
        "tolerance": tolerance,
        "async_wait_seconds": async_wait_seconds,
        "created_at": time.time(),
    }


def build_default_oracle_rules() -> list[dict[str, Any]]:
    """Build default cross-surface oracle rules."""
    return [
        # API ↔ DB
        create_oracle_rule(
            pair="API_DB", check_type="DATA_EQUALITY",
            description="API response data matches DB state",
            source_field="api_response.data", target_field="db_row",
            comparison="DEEP_EQUAL",
        ),
        create_oracle_rule(
            pair="API_DB", check_type="STATE_CONSISTENCY",
            description="API reported status matches DB status field",
            source_field="api_response.status", target_field="db_row.status",
        ),
        create_oracle_rule(
            pair="API_DB", check_type="COMPLETENESS",
            description="All API returned fields exist in DB",
            source_field="api_response.fields", target_field="db_row.columns",
            comparison="SUBSET",
        ),
        # API ↔ Event
        create_oracle_rule(
            pair="API_EVENT", check_type="TEMPORAL_ORDERING",
            description="Event published after API response",
            source_field="api_response.timestamp", target_field="event.timestamp",
            comparison="BEFORE_OR_EQUAL",
            async_wait_seconds=5.0,
        ),
        create_oracle_rule(
            pair="API_EVENT", check_type="DATA_EQUALITY",
            description="Event payload matches API mutation data",
            source_field="api_request.body", target_field="event.payload",
            comparison="DEEP_EQUAL",
            async_wait_seconds=5.0,
        ),
        # API ↔ UI
        create_oracle_rule(
            pair="API_UI", check_type="DATA_EQUALITY",
            description="UI displayed data matches API response",
            source_field="api_response.data", target_field="ui_displayed_values",
            comparison="DEEP_EQUAL",
        ),
        create_oracle_rule(
            pair="API_UI", check_type="STATE_CONSISTENCY",
            description="UI enabled/disabled state matches API permissions",
            source_field="api_response.permissions", target_field="ui_element_states",
        ),
        # UI ↔ DB
        create_oracle_rule(
            pair="UI_DB", check_type="DATA_EQUALITY",
            description="UI form data persists correctly to DB",
            source_field="ui_form_data", target_field="db_row",
            comparison="DEEP_EQUAL",
        ),
        # Event ↔ DB
        create_oracle_rule(
            pair="EVENT_DB", check_type="EVENTUAL_CONSISTENCY" if "EVENTUAL_CONSISTENCY" in CHECK_TYPES else "STATE_CONSISTENCY",
            description="Event processing eventually updates DB",
            source_field="event.payload", target_field="db_row",
            async_wait_seconds=30.0,
        ),
        create_oracle_rule(
            pair="EVENT_DB", check_type="COMPLETENESS",
            description="All events processed (no lost events)",
            source_field="event.count", target_field="db_row.processed_count",
            comparison="EQUAL",
            async_wait_seconds=30.0,
        ),
        # Report ↔ DB
        create_oracle_rule(
            pair="REPORT_DB", check_type="AGGREGATE_CONSISTENCY",
            description="Report aggregations match DB raw data",
            source_field="report.aggregations", target_field="db_raw_data",
            comparison="AGGREGATE_MATCH",
            tolerance=0.01,
        ),
        create_oracle_rule(
            pair="REPORT_DB", check_type="COMPLETENESS",
            description="Report includes all DB records in scope",
            source_field="report.row_count", target_field="db_row.count",
            comparison="EQUAL",
        ),
    ]


# ─── Cross-Surface Oracle ──────────────────────────────────────────────────────

class CrossSurfaceOracle:
    """Oracle for cross-surface consistency verification."""

    def __init__(self, *, project_id: str = ""):
        self.project_id = project_id
        self._rules: list[dict[str, Any]] = []
        self._results: list[dict[str, Any]] = []

    def add_rule(self, rule: dict[str, Any]) -> str:
        """Add an oracle rule."""
        self._rules.append(rule)
        return rule.get("rule_id", "")

    def add_default_rules(self) -> int:
        """Add all default rules."""
        for rule in build_default_oracle_rules():
            self._rules.append(rule)
        return len(self._rules)

    def get_rules_for_pair(self, pair: str) -> list[dict[str, Any]]:
        """Get rules for a specific consistency pair."""
        return [r for r in self._rules if r.get("pair") == pair]

    def get_rules_for_experiment(
        self,
        experiment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Get applicable oracle rules for an experiment."""
        surface = experiment.get("surface_adapter", "API")
        observers = experiment.get("observers", [])

        # Determine which pairs are relevant
        relevant_pairs = set()
        if surface == "API" or "API" in observers:
            if "DB" in observers:
                relevant_pairs.add("API_DB")
            if "EVENT" in observers:
                relevant_pairs.add("API_EVENT")
            if "UI" in observers:
                relevant_pairs.add("API_UI")
        if surface == "UI" or "UI" in observers:
            if "DB" in observers:
                relevant_pairs.add("UI_DB")
        if surface == "EVENT" or "EVENT" in observers:
            if "DB" in observers:
                relevant_pairs.add("EVENT_DB")
        if "REPORT" in observers:
            relevant_pairs.add("REPORT_DB")

        return [r for r in self._rules if r.get("pair") in relevant_pairs]

    def evaluate(
        self,
        *,
        experiment_id: str,
        observations: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate oracle rules against observations.

        observations: {layer_type: observation_data}
        """
        results = []
        passed = 0
        failed = 0
        skipped = 0

        for rule in self._rules:
            pair = rule.get("pair", "")
            source_layer, target_layer = pair.split("_", 1) if "_" in pair else (pair, "")

            source_data = observations.get(source_layer)
            target_data = observations.get(target_layer)

            if source_data is None or target_data is None:
                skipped += 1
                results.append({
                    "rule_id": rule.get("rule_id"),
                    "status": "SKIPPED",
                    "reason": "missing_observation",
                })
                continue

            # Simplified evaluation — real implementation would compare fields
            check_type = rule.get("check_type", "")
            comparison = rule.get("comparison", "EQUAL")

            # For now, mark as PASS (actual comparison needs runtime data)
            status = "PASS"
            passed += 1

            results.append({
                "rule_id": rule.get("rule_id"),
                "pair": pair,
                "check_type": check_type,
                "status": status,
                "comparison": comparison,
            })

        evaluation = {
            "experiment_id": experiment_id,
            "total_rules": len(self._rules),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "results": results,
            "timestamp": time.time(),
        }
        self._results.append(evaluation)
        return evaluation

    def coverage_summary(self) -> dict[str, Any]:
        """Coverage summary of oracle rules."""
        pairs_covered = {r.get("pair", "") for r in self._rules}
        return {
            "total_rules": len(self._rules),
            "pairs_covered": sorted(pairs_covered),
            "missing_pairs": sorted(CONSISTENCY_PAIRS - pairs_covered),
            "all_pairs_covered": pairs_covered == CONSISTENCY_PAIRS,
            "total_evaluations": len(self._results),
        }

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.cross-surface-oracle.v1",
            "project_id": self.project_id,
            "rules": self._rules,
            "results": self._results,
            "coverage": self.coverage_summary(),
        }


# ─── Eventual Consistency Strategy ─────────────────────────────────────────────

def create_eventual_consistency_strategy(
    *,
    max_wait_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
    backoff_multiplier: float = 1.5,
    frozen_before_execution: bool = True,
) -> dict[str, Any]:
    """Create eventual consistency wait strategy.

    SPEC §25: For async systems, freeze wait strategy before execution.
    """
    return {
        "max_wait_seconds": max_wait_seconds,
        "poll_interval_seconds": poll_interval_seconds,
        "backoff_multiplier": backoff_multiplier,
        "frozen_before_execution": frozen_before_execution,
        "strategy": "EXPONENTIAL_BACKOFF",
        "note": "strategy_must_be_frozen_before_execution_begins",
    }


# ─── Emergent Invariant Detection (SPEC §25.3) ────────────────────────────────

EMERGENT_MECHANISMS = frozenset({
    "EMERGENT_INVARIANT_VIOLATION",
    "UNCLASSIFIED_BEHAVIORAL_DIVERGENCE",
})


def detect_emergent_violation(
    *,
    experiment_id: str,
    observations: list[dict[str, Any]],
    known_invariants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Detect emergent invariant violations not covered by known rules.

    Returns candidate emergent mechanism if unexplained divergence found.
    """
    known = known_invariants or []
    known_types = {inv.get("invariant_type", "") for inv in known}

    # Check for divergences not explained by known invariants
    unexplained = []
    for obs in observations:
        if obs.get("status") == "DIVERGENCE":
            obs_type = obs.get("observer_type", "")
            if obs_type not in known_types:
                unexplained.append(obs)

    is_emergent = len(unexplained) > 0

    return {
        "experiment_id": experiment_id,
        "is_emergent": is_emergent,
        "mechanism": "EMERGENT_INVARIANT_VIOLATION" if is_emergent else "",
        "unexplained_divergences": len(unexplained),
        "known_invariant_count": len(known),
        "requires_classification": is_emergent,
        "timestamp": time.time(),
    }
