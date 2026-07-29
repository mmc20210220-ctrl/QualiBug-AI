"""enterprise_knowledge_center package - backward-compatible facade.

All public and private symbols are re-exported so that existing
``from .enterprise_knowledge_center import X`` continues to work.
"""
from ._common import *  # noqa: F401,F403
from ._utils import *  # noqa: F401,F403
from ._utils import _csv_rows  # noqa: F401
from ._parsing import *  # noqa: F401,F403
from ._crud import *  # noqa: F401,F403
from ._linking import *  # noqa: F401,F403
from ._api import *  # noqa: F401,F403
from ._chinese_business_comprehension import (  # noqa: F401
    analyze_chinese_business_source,
    build_chinese_first_comprehension,
    install_chinese_first_business_comprehension,
)
from ._chinese_business_conflicts import (  # noqa: F401
    reconcile_chinese_business_fact_conflicts,
    install_chinese_business_conflict_reconciliation,
)
from ._chinese_business_authority_decision import (  # noqa: F401
    ACTION_LEAVE_UNRESOLVED,
    ACTION_SELECT_FACT,
    apply_authority_decisions_to_conflicts,
    list_operator_authority_decisions,
    record_operator_authority_decision,
)
from .enterprise_understanding import (  # noqa: F401
    assess_understanding_model,
    build_enterprise_understanding_model,
    build_lifecycles,
    build_object_graph,
    enrich_asset_with_enterprise_understanding,
    install_enterprise_understanding_model,
)
from ._chinese_business_downstream import (  # noqa: F401
    refresh_chinese_business_downstream,
    install_chinese_business_downstream_refresh,
)
from ._job_assets import (  # noqa: F401
    discover_job_definitions_from_text,
    enrich_job_assets,
    install_job_asset_enrichment,
)
from .job_asset_governance import (  # noqa: F401
    install_job_asset_governance,
    normalize_job_definition_with_governance,
    to_async_operation_with_governance,
)
from .job_behavior_projection import (  # noqa: F401
    install_job_behavior_projection,
    project_job_behaviors,
    refresh_job_behavior_projection,
)
from ..job_async_protocol import (  # noqa: F401
    TEMPLATE_ASYNC_JOB_EXECUTION,
    compile_async_job_protocol,
    register_job_async_protocol,
)
from ..job_async_runtime import (  # noqa: F401
    execute_async_job_plan,
    execute_registered_job_operation,
    install_job_async_execution_adapter,
)
from ._formal_ui_contracts import (  # noqa: F401
    extract_formal_ui_contracts,
    install_formal_ui_contract_parser,
)
from ._formal_ui_contract_guard import (  # noqa: F401
    install_formal_ui_root_array_guard,
)
from ._formal_ui_persistent_probe_guard import (  # noqa: F401
    install_formal_ui_persistent_probe_guard,
)
from ._formal_ui_visual_baseline_guard import (  # noqa: F401
    install_formal_ui_visual_baseline_guard,
)
from ._formal_ui_visual_viewport_guard import (  # noqa: F401
    install_formal_ui_visual_viewport_guard,
)

# Additive parser registration only. It opens no files, browser or target connection.
install_formal_ui_root_array_guard()
install_formal_ui_contract_parser()
install_formal_ui_persistent_probe_guard()
install_formal_ui_visual_baseline_guard()
install_formal_ui_visual_viewport_guard()

# The Job capability is installed on the existing protocol and plan-execution
# authorities. Non-Job plans delegate to the original executor unchanged.
JOB_ASYNC_PROTOCOL_ID = register_job_async_protocol()
execute_non_barrier_job_adapter = install_job_async_execution_adapter()

# Install on the existing knowledge-center build authority. Chinese source spans
# become governed facts first; unresolved contradictions are removed from formal
# rule input; the enterprise understanding model then compiles objects, actors,
# operations, relations, lifecycles, processes and unknowns. Job normalization
# preserves only source-declared execution identities. Job enrichment appends the
# ASYNC_JOB operations and the final projection adds governed Job behaviors to the
# SAME Business Behavior IR collection. No parallel behavior authority is created.
install_job_asset_governance()
build_enterprise_business_knowledge_asset = install_chinese_first_business_comprehension()
build_enterprise_business_knowledge_asset = install_chinese_business_conflict_reconciliation()
build_enterprise_business_knowledge_asset = install_enterprise_understanding_model()
build_enterprise_business_knowledge_asset = install_chinese_business_downstream_refresh()
build_enterprise_business_knowledge_asset = install_job_asset_enrichment()
build_enterprise_business_knowledge_asset = install_job_behavior_projection()

# Existing projects may already have a persisted knowledge asset. Enrich and refresh
# the loaded asset in memory as well, so customers do not rebuild or re-upload sources.
from . import _api as _api_module  # noqa: E402

_original_load_enterprise_business_knowledge_asset = (
    _api_module.load_enterprise_business_knowledge_asset
)


def _load_enterprise_business_knowledge_asset_with_jobs(
    project_id: str = "real_project_demo",
    root=None,
):
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    loaded = _original_load_enterprise_business_knowledge_asset(project, resolved_root)
    if not loaded:
        return None
    enriched = enrich_job_assets(
        loaded,
        project_id=project,
        root=resolved_root,
        options={},
    )
    return refresh_job_behavior_projection(enriched)


_api_module.load_enterprise_business_knowledge_asset = (
    _load_enterprise_business_knowledge_asset_with_jobs
)
load_enterprise_business_knowledge_asset = (
    _load_enterprise_business_knowledge_asset_with_jobs
)

# Explicit re-exports for underscore-prefixed symbols
from ._common import _SEMANTIC_LEXICON_CACHE  # noqa: F401
from ._utils import _semantic_lexicon, _lexicon_dict, _lexicon_list, _now, _detected_source_format, _parser_receipt, _hash_bytes, _short_hash, _norm, _tokens, _normalize_state_token, _safe_slug, _redact_text, _safe_actor, _require_manage_actor, _paths, _registry_default, _load_registry, _save_registry, _decode_docx, _decode_pdf, _read_source_bytes, _json_or_none, _contains_markdown_api_sections, _looks_like_field_dictionary, _looks_like_uiux_spec, _clean_markup_text  # noqa: F401
from ._parsing import _doc_bool, _classify_source, _openapi_operations, _postman_operations, _json_blocks, _flatten_json_field_names, _markdown_table_blocks, _pick_first, _infer_field_rows_from_markdown, _field_dictionary_entries, _field_dictionary_tables, _markdown_api_operations, _sql_tables, _json_schema_tables, _uiux_specs_from_text, _markdown_table_rows, _permission_field, _permission_decision, _permission_action_values, _permission_resource_aliases, _permission_action_aliases, _permission_scope, _negative_permission_clause, _permission_entries, _ticket_rows, _rule_type_from_text, _risk_type_from_text, _typed_validation_constraint, _rules_from_text, _roles_from_text, _state_machines_from_text, _parse_source  # noqa: F401
from ._crud import _record_parse, _logical_key  # noqa: F401
from ._linking import _merge_openapi, _dedupe_by_id, _NON_AUTHORITATIVE_RELATION_STATUSES, _relationship_is_authoritative, _links_by_overlap, _links_by_exact_source_section, _WRITE_METHODS, _CONTRACT_FIELD_RE, _JSON_KEY_RE, _SNAKE_FIELD_RE, _CAMEL_FIELD_RE, _CLEANUP_ACTION_RE, _EXCLUDED_CONTRACT_FIELD_TOKENS, _is_plausible_contract_field, _path_module_prefix, _normalize_contract_field, _contract_fields_for_interface, _rule_mentioned_contract_fields, _interface_path_terminal, _is_cleanup_action_interface, _interface_parent_path, _looks_inverse_delta_capable, _interface_text_blob, _cleanup_documents_primary_action, _has_documented_sibling_compensation, _prefer_reversible_write_targets, _MODULE_NEIGHBOR_RISK_TYPES, _module_field_universe, _reversible_module_write_targets, _links_by_exclusive_contract_fields, _links_by_same_source_exclusive_module_neighbors, _authoritative_rule_to_interface_edges, _module_tree, _risk_domains, _oracle_family, _oracle_dsl_pack_from_recognized_industries, _oracle_library, _probes_from_asset, _evidence_bundle, _declared_project_source_files, _sync_declared_project_sources  # noqa: F401
from ._api import _extract_entity_relations, _detect_cross_document_conflicts, _structurize_rule_causal_chains, _cli  # noqa: F401

__all__ = [
    "logger",
    "PHASE",
    "PARSER_RECEIPT_SCHEMA",
    "SOURCE_TYPES",
    "TEXT_SUFFIXES",
    "MAX_SOURCE_BYTES",
    "SAFE_METHODS",
    "WRITE_METHODS",
    "MARKDOWN_API_ENDPOINT_RE",
    "SVG_TEXT_RE",
    "SVG_TAG_ATTR_RE",
    "SVG_TITLE_RE",
    "SVG_DESC_RE",
    "ROLE_WORDS",
    "RISK_TERMS",
    "SECRET_PATTERNS",
    "SEMANTIC_LEXICON_PATH",
    "_SEMANTIC_LEXICON_CACHE",
    "_semantic_lexicon",
    "_lexicon_dict",
    "_lexicon_list",
    "_now",
    "_detected_source_format",
    "_parser_receipt",
    "_hash_bytes",
    "_short_hash",
    "_norm",
    "_tokens",
    "ENGLISH_STATE_TOKENS",
    "CHINESE_STATE_HINTS",
    "_normalize_state_token",
    "_safe_slug",
    "_redact_text",
    "_safe_actor",
    "_require_manage_actor",
    "_paths",
    "_registry_default",
    "_load_registry",
    "_save_registry",
    "_decode_docx",
    "_decode_pdf",
    "_read_source_bytes",
    "_json_or_none",
    "_contains_markdown_api_sections",
    "_looks_like_field_dictionary",
    "_looks_like_uiux_spec",
    "_clean_markup_text",
    "_doc_bool",
    "_classify_source",
    "_openapi_operations",
    "_postman_operations",
    "_json_blocks",
    "_flatten_json_field_names",
    "_markdown_table_blocks",
    "_pick_first",
    "_infer_field_rows_from_markdown",
    "_field_dictionary_entries",
    "_field_dictionary_tables",
    "_markdown_api_operations",
    "_sql_tables",
    "_json_schema_tables",
    "_uiux_specs_from_text",
    "_csv_rows",
    "_markdown_table_rows",
    "_permission_field",
    "_permission_decision",
    "_permission_action_values",
    "_permission_resource_aliases",
    "_permission_action_aliases",
    "_permission_scope",
    "_negative_permission_clause",
    "_permission_entries",
    "_ticket_rows",
    "_rule_type_from_text",
    "_risk_type_from_text",
    "_typed_validation_constraint",
    "_rules_from_text",
    "_roles_from_text",
    "_state_machines_from_text",
    "_parse_source",
    "_record_parse",
    "_logical_key",
    "ingest_enterprise_knowledge_documents",
    "ingest_enterprise_knowledge_files",
    "list_enterprise_knowledge_sources",
    "update_enterprise_knowledge_source",
    "delete_enterprise_knowledge_source",
    "operate_enterprise_knowledge_center",
    "_merge_openapi",
    "_dedupe_by_id",
    "TOKEN_OVERLAP_RELATION_GATE",
    "_NON_AUTHORITATIVE_RELATION_STATUSES",
    "_relationship_is_authoritative",
    "_links_by_overlap",
    "_links_by_exact_source_section",
    "_WRITE_METHODS",
    "_CONTRACT_FIELD_RE",
    "_JSON_KEY_RE",
    "_SNAKE_FIELD_RE",
    "_CAMEL_FIELD_RE",
    "_CLEANUP_ACTION_RE",
    "_EXCLUDED_CONTRACT_FIELD_TOKENS",
    "_is_plausible_contract_field",
    "_path_module_prefix",
    "_normalize_contract_field",
    "_contract_fields_for_interface",
    "_rule_mentioned_contract_fields",
    "_interface_path_terminal",
    "_is_cleanup_action_interface",
    "_interface_parent_path",
    "_looks_inverse_delta_capable",
    "_interface_text_blob",
    "_cleanup_documents_primary_action",
    "_has_documented_sibling_compensation",
    "_prefer_reversible_write_targets",
    "_MODULE_NEIGHBOR_RISK_TYPES",
    "_module_field_universe",
    "_reversible_module_write_targets",
    "_links_by_exclusive_contract_fields",
    "_links_by_same_source_exclusive_module_neighbors",
    "_authoritative_rule_to_interface_edges",
    "_module_tree",
    "_risk_domains",
    "_oracle_family",
    "_oracle_dsl_pack_from_recognized_industries",
    "_oracle_library",
    "_probes_from_asset",
    "_evidence_bundle",
    "_declared_project_source_files",
    "_sync_declared_project_sources",
    "build_runtime_source_knowledge_overlay",
    "merge_knowledge_asset_overlay",
    "_extract_entity_relations",
    "_detect_cross_document_conflicts",
    "_structurize_rule_causal_chains",
    "analyze_chinese_business_source",
    "build_chinese_first_comprehension",
    "install_chinese_first_business_comprehension",
    "reconcile_chinese_business_fact_conflicts",
    "install_chinese_business_conflict_reconciliation",
    "ACTION_LEAVE_UNRESOLVED",
    "ACTION_SELECT_FACT",
    "apply_authority_decisions_to_conflicts",
    "list_operator_authority_decisions",
    "record_operator_authority_decision",
    "assess_understanding_model",
    "build_enterprise_understanding_model",
    "build_lifecycles",
    "build_object_graph",
    "enrich_asset_with_enterprise_understanding",
    "install_enterprise_understanding_model",
    "refresh_chinese_business_downstream",
    "install_chinese_business_downstream_refresh",
    "discover_job_definitions_from_text",
    "enrich_job_assets",
    "install_job_asset_enrichment",
    "normalize_job_definition_with_governance",
    "to_async_operation_with_governance",
    "install_job_asset_governance",
    "project_job_behaviors",
    "refresh_job_behavior_projection",
    "install_job_behavior_projection",
    "TEMPLATE_ASYNC_JOB_EXECUTION",
    "compile_async_job_protocol",
    "register_job_async_protocol",
    "execute_registered_job_operation",
    "execute_async_job_plan",
    "install_job_async_execution_adapter",
    "JOB_ASYNC_PROTOCOL_ID",
    "build_enterprise_business_knowledge_asset",
    "load_enterprise_business_knowledge_asset",
    "generate_enterprise_business_knowledge_probes",
    "build_enterprise_knowledge_evidence_bundle",
    "render_enterprise_business_knowledge_report",
    "render_enterprise_business_knowledge_center",
    "run_enterprise_knowledge_demo",
    "extract_formal_ui_contracts",
    "install_formal_ui_contract_parser",
    "install_formal_ui_root_array_guard",
    "install_formal_ui_persistent_probe_guard",
    "install_formal_ui_visual_baseline_guard",
    "install_formal_ui_visual_viewport_guard",
    "_cli",
]
