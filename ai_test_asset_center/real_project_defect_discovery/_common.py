from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any

from ..real_project_onboarding import (
    ROOT,
    _html_escape,
    _join_url,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    execution_safety_verdict,
    load_real_project_config,
    run_onboarding_check,
)
from ..business_adaptation_layer import build_business_adaptation_profile, generate_business_adaptive_probes
from ..universal_defect_mining import build_universal_defect_mining_profile, generate_universal_defect_probes, load_universal_defect_mining
from ..business_outcome_validation import build_business_outcome_profile, generate_business_outcome_probes, load_business_outcome_profile, run_business_outcome_validation
from ..business_reconciliation import build_business_reconciliation_profile, generate_business_reconciliation_probes, load_business_reconciliation_profile, run_business_reconciliation
from ..business_invariant_mining import build_business_invariant_profile, generate_business_invariant_probes, load_business_invariant_profile, run_business_invariant_mining
from ..multisource_reasoning import build_multi_source_reasoning_profile, generate_multi_source_reasoning_probes, load_multi_source_reasoning_profile, run_multi_source_reasoning
from ..business_lifecycle_reasoning import build_business_lifecycle_profile, generate_business_lifecycle_probes, load_business_lifecycle_profile, run_business_lifecycle_reasoning
from ..consistency_isolation_reasoning import build_consistency_isolation_profile, generate_consistency_isolation_probes, load_consistency_isolation_profile, run_consistency_isolation_reasoning
from ..metamorphic_differential_reasoning import build_metamorphic_differential_profile, generate_metamorphic_differential_probes, load_metamorphic_differential_profile, run_metamorphic_differential_reasoning
from ..temporal_data_regression_reasoning import build_temporal_data_regression_profile, generate_temporal_data_regression_probes, load_temporal_data_regression_profile, run_temporal_data_regression_reasoning
from ..business_causality_conservation import build_business_causality_profile, generate_business_causality_probes, load_business_causality_profile, run_business_causality_conservation
from ..business_population_constraints import build_business_population_constraint_profile, generate_business_population_constraint_probes, load_business_population_constraint_profile, run_business_population_constraints
from ..business_event_chain_reasoning import build_business_event_chain_profile, generate_business_event_chain_probes, load_business_event_chain_profile, run_business_event_chain_reasoning
from ..business_saga_compensation_reasoning import build_business_saga_compensation_profile, generate_business_saga_compensation_probes, load_business_saga_compensation_profile, run_business_saga_compensation_reasoning
from ..confirmed_bug_flywheel import build_confirmed_bug_flywheel, annotate_probes_with_confirmed_learning
from ..continuous_discovery_campaign import record_continuous_discovery_campaign_run
from ..business_assurance_coverage import build_business_assurance_coverage_profile, generate_business_assurance_coverage_probes, load_business_assurance_coverage_profile, run_business_assurance_coverage
from ..multi_industry_business_reasoning import build_multi_industry_business_profile, generate_multi_industry_business_probes, load_multi_industry_business_profile
from ..enterprise_knowledge_center import build_enterprise_business_knowledge_asset, build_enterprise_knowledge_evidence_bundle, generate_enterprise_business_knowledge_probes, load_enterprise_business_knowledge_asset
from ..enterprise_testops_control_plane import (
    build_enterprise_testops_control_plane,
    build_explainable_test_assets,
    build_issue_lifecycle_and_fix_plan,
    evaluate_defect_quality,
    generate_enterprise_testops_probes,
)
from ..api_contract_discovery_adapter import collect_api_contract_issues, generate_api_contract_probes
from ..browser_ui_replay_discovery_adapter import (
    browser_ui_capability_health,
    collect_browser_ui_replay_issues,
    generate_browser_ui_replay_probes,
)
from ..bug_family_coverage_report import build_bug_family_coverage_report
from ..compatibility_discovery_adapter import collect_compatibility_issues, generate_compatibility_probes
from ..defect_family_registry import resolve_defect_family
from ..discovery_accounting import classify_issue_accounting, enrich_issue_accounting
from ..document_contract_fuzzing_discovery_adapter import generate_document_contract_fuzzing_probes
from ..full_spectrum_capability_matrix import build_full_spectrum_capability_matrix
from ..frontend_runtime_discovery_adapter import collect_frontend_runtime_issues, generate_frontend_runtime_probes
from ..frontend_ux_discovery_adapter import collect_frontend_ux_issues, generate_frontend_ux_probes
from ..openapi_static_security_scan_adapter import (
    collect_openapi_static_security_issues,
    generate_openapi_static_security_probes,
)
from ..performance_stability_discovery_adapter import (
    collect_performance_stability_issues,
    generate_performance_stability_probes,
)
from ..privacy_compliance_discovery_adapter import (
    collect_privacy_compliance_issues,
    generate_privacy_compliance_probes,
)
from ..ui_design_oracle_signal_basis import (
    UI_DESIGN_ORACLE_SIGNAL_BASIS_BUCKETS,
    build_ui_design_oracle_signal_basis_legend,
    normalize_ui_design_oracle_signal_basis,
    build_ui_design_oracle_action_reasons,
    recommend_ui_design_oracle_next_actions,
)

DESTRUCTIVE_RISK_TYPES = {"payment", "refund", "delete", "idempotency", "duplicate_submit", "concurrency", "cancel_order"}
_BROWSER_UI_BLOCKED_SOURCES = {"browser_ui_replay", "frontend_execution_runtime", "frontend_ux_adapter", "compatibility_adapter"}


