from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_test_asset_center.adaptive_probe_optimizer import build_learned_probe_policy


PRIVATE_BLOCKLIST = ("private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "bug_set")


PROBE_POLICY_PROFILES = {
    "baseline": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "journey_auto"},
    "feedback": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "journey_auto"},
    "rag": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "rag_enhanced", "journey_auto"},
    "rag_enhanced": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "rag_enhanced", "journey_auto"},
    "feedback_adjusted": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "feedback_adjusted", "journey_auto"},
    "adaptive": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "journey_auto"},
    "conservative": {"pattern_library"},
    "full_blind": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "rag_enhanced", "journey_auto"},
    "demo": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "rag_enhanced", "journey_auto"},
}

def normalize_probe_policy_profile(profile: str | None = None, discovery_mode: str = "blind") -> str:
    from ._probes import normalize_discovery_mode  # lazy: avoid circular
    raw = (profile or os.environ.get("PROBE_POLICY_PROFILE") or "adaptive").strip().lower()
    aliases = {
        "base": "baseline",
        "baseline_policy": "baseline",
        "feedback_learning": "feedback",
        "feedback_policy": "feedback",
        "adaptive_policy": "adaptive",
        "adaptive_v1": "adaptive",
        "rag_policy": "rag_enhanced",
        "rag_enhanced_policy": "rag_enhanced",
        "feedback_adjusted_policy": "feedback_adjusted",
        "human_feedback_policy": "feedback_adjusted",
        "qa_feedback": "feedback_adjusted",
        "rag_plus": "rag_enhanced",
        "full": "full_blind",
        "blind_full": "full_blind",
        "safe": "conservative",
    }
    raw = aliases.get(raw, raw)
    if normalize_discovery_mode(discovery_mode) == "demo" and raw not in {"baseline", "feedback", "adaptive", "conservative", "full_blind"}:
        return "demo"
    if raw not in PROBE_POLICY_PROFILES:
        return "adaptive"
    if raw == "demo" and normalize_discovery_mode(discovery_mode) != "demo":
        return "adaptive"
    return raw

def allowed_sources_for_policy(profile: str, discovery_mode: str = "blind") -> set[str]:
    normalized = normalize_probe_policy_profile(profile, discovery_mode)
    return set(PROBE_POLICY_PROFILES[normalized])

def filter_probes_by_policy(probes: list[dict], profile: str, discovery_mode: str = "blind") -> list[dict]:
    allowed = allowed_sources_for_policy(profile, discovery_mode)
    return [p for p in probes if p.get("source") in allowed]

ENTERPRISE_RISK_TAXONOMY = {
    "access_control": ["permission_bypass", "auth_bypass", "idor", "tenant_isolation", "role_escalation", "field_level_permission"],
    "workflow": ["state_flow", "approval_bypass", "step_skip", "rollback_consistency", "sla_timeout", "terminal_state_mutation"],
    "financial": ["money_consistency", "fee_calculation", "tax_consistency", "settlement_reconciliation", "rounding_precision", "credit_limit"],
    "quantity_asset": ["quantity_consistency", "quota_limit", "inventory_consistency", "capacity_limit", "negative_balance"],
    "data_quality": ["required_field_bypass", "enum_constraint", "duplicate_record", "referential_integrity", "soft_delete_visibility", "stale_cache"],
    "integration": ["callback_trust", "webhook_replay", "third_party_status_mapping", "message_ordering", "eventual_consistency"],
    "batch_import": ["file_upload_validation", "bulk_operation_partial_failure", "duplicate_import", "async_job_status", "large_payload_limit"],
    "audit_compliance": ["audit_log_missing", "sensitive_data_exposure", "privacy_scope", "data_retention", "export_permission"],
    "configuration": ["feature_flag_scope", "tenant_config_isolation", "pricing_config", "workflow_config", "default_value_risk"],
    "notification": ["notification_wrong_recipient", "notification_duplicate", "notification_missing", "template_variable_leak"],
    "search_report": ["search_scope_leak", "report_aggregation_error", "pagination_consistency", "sorting_filter_consistency", "export_consistency"],
    "time_concurrency": ["idempotency", "race_condition", "concurrent_update_lost", "time_window_boundary", "timezone_boundary"],
}


