"""Enterprise knowledge-center public facade.

The facade only exports symbols. Importing it no longer assembles the product by stacking
wrappers around a shared builder or loader. Public ingestion and lifecycle use one
source-occurrence identity root, which delegates archive atomicity to ``atomic_ingestion`` and
leaf activation/parsing to ``_crud``.
"""
from ._common import *  # noqa: F401,F403
from ._utils import *  # noqa: F401,F403
from ._utils import _csv_rows  # noqa: F401
from ._parsing import *  # noqa: F401,F403
from . import _parsing as _parsing_authority
from .semantic_rule_frame_guard import install_semantic_rule_frame_guard

install_semantic_rule_frame_guard(_parsing_authority)
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
from .database_mapping_authority import (  # noqa: F401
    ACTION_APPROVE_READ_ONLY_OBSERVER,
    ACTION_REJECT_MAPPING,
    apply_database_mapping_authority_decisions,
    database_mapping_candidate_fingerprint,
    list_database_mapping_authority_decisions,
    load_database_mapping_authority_ledger,
    record_database_mapping_authority_decision,
)
from .database_observer_contract_projection import (  # noqa: F401
    enrich_asset_with_database_observer_contracts,
)
from .enterprise_understanding import (  # noqa: F401
    ACTION_CONFIRM_ALIAS,
    ACTION_REJECT_CANDIDATE,
    IDENTITY_STRUCTURAL_REVIEW_DECISION_KIND,
    IDENTITY_STRUCTURAL_REVIEW_QUEUE_SCHEMA,
    IDENTITY_STRUCTURAL_REVIEW_RECEIPT_SCHEMA,
    assess_understanding_model,
    build_enterprise_understanding_model,
    build_gated_probes,
    build_lifecycles,
    build_object_graph,
    enrich_asset_with_enterprise_understanding,
    get_identity_structural_review_queue,
    install_enterprise_understanding_model,
    probe_generation_allowed,
    probe_generation_block_reason,
    record_identity_structural_review_decision,
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
    merge_cross_source_job_assets,
    normalize_job_definition_with_governance,
    to_async_operation_with_governance,
)
from .job_asset_pipeline import enrich_job_assets_with_governance  # noqa: F401
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
from ._formal_ui_contract_guard import install_formal_ui_root_array_guard  # noqa: F401
from ._formal_ui_persistent_probe_guard import (  # noqa: F401
    install_formal_ui_persistent_probe_guard,
)
from ._formal_ui_visual_baseline_guard import (  # noqa: F401
    install_formal_ui_visual_baseline_guard,
)
from ._formal_ui_visual_viewport_guard import (  # noqa: F401
    install_formal_ui_visual_viewport_guard,
)

# The old import-time registration values are deliberately absent. Call the explicit
# registration functions only in the execution subsystem that needs them.
JOB_ASYNC_PROTOCOL_ID = None
execute_non_barrier_job_adapter = None

# Public composition authorities. These names intentionally shadow extraction primitives
# imported from ``_api`` without modifying that module's globals.
from .composition import (  # noqa: E402,F401
    build_enterprise_business_knowledge_asset,
    configure_source_parser_extensions,
    generate_enterprise_business_knowledge_probes,
    load_enterprise_business_knowledge_asset,
    refresh_enterprise_business_knowledge_asset_incremental,
)

# Public ingestion authority. It records content, interpretation and occurrence identities,
# while reusing the existing archive transaction and canonical CRUD/parser authorities.
from .source_occurrence_authority import (  # noqa: E402,F401
    SourceOccurrenceIngestionError,
    ingest_enterprise_knowledge_documents,
    ingest_enterprise_knowledge_files,
)

# Public lifecycle authority. List/update/delete operate on source occurrences and only retire
# canonical bytes/runtime state when the final active occurrence disappears.
from .source_occurrence_lifecycle import (  # noqa: E402,F401
    delete_enterprise_knowledge_source,
    list_enterprise_knowledge_sources,
    operate_enterprise_knowledge_center,
    update_enterprise_knowledge_source,
)

# Explicit re-exports for underscore-prefixed compatibility symbols.
from ._common import _SEMANTIC_LEXICON_CACHE  # noqa: F401
from ._utils import (  # noqa: F401
    _clean_markup_text,
    _contains_markdown_api_sections,
    _decode_docx,
    _decode_pdf,
    _detected_source_format,
    _hash_bytes,
    _json_or_none,
    _lexicon_dict,
    _lexicon_list,
    _looks_like_field_dictionary,
    _looks_like_uiux_spec,
    _norm,
    _normalize_state_token,
    _now,
    _parser_receipt,
    _paths,
    _read_source_bytes,
    _redact_text,
    _registry_default,
    _require_manage_actor,
    _safe_actor,
    _safe_slug,
    _save_registry,
    _semantic_lexicon,
    _short_hash,
    _tokens,
    _load_registry,
)
from ._parsing import (  # noqa: F401
    _classify_source,
    _doc_bool,
    _field_dictionary_entries,
    _field_dictionary_tables,
    _flatten_json_field_names,
    _infer_field_rows_from_markdown,
    _json_blocks,
    _markdown_api_operations,
    _markdown_table_blocks,
    _markdown_table_rows,
    _negative_permission_clause,
    _openapi_operations,
    _parse_source,
    _permission_action_aliases,
    _permission_action_values,
    _permission_decision,
    _permission_entries,
    _permission_field,
    _permission_resource_aliases,
    _permission_scope,
    _pick_first,
    _postman_operations,
    _risk_type_from_text,
    _roles_from_text,
    _rule_type_from_text,
    _rules_from_text,
    _sql_tables,
    _state_machines_from_text,
    _ticket_rows,
    _typed_validation_constraint,
    _uiux_specs_from_text,
    _json_schema_tables,
)
from ._crud import _logical_key, _record_parse  # noqa: F401
from ._linking import (  # noqa: F401
    _CAMEL_FIELD_RE,
    _CLEANUP_ACTION_RE,
    _CONTRACT_FIELD_RE,
    _EXCLUDED_CONTRACT_FIELD_TOKENS,
    _JSON_KEY_RE,
    _MODULE_NEIGHBOR_RISK_TYPES,
    _NON_AUTHORITATIVE_RELATION_STATUSES,
    _SNAKE_FIELD_RE,
    _WRITE_METHODS,
    _authoritative_rule_to_interface_edges,
    _cleanup_documents_primary_action,
    _contract_fields_for_interface,
    _declared_project_source_files,
    _evidence_bundle,
    _has_documented_sibling_compensation,
    _interface_parent_path,
    _interface_path_terminal,
    _interface_text_blob,
    _is_cleanup_action_interface,
    _is_plausible_contract_field,
    _links_by_exact_source_section,
    _links_by_exclusive_contract_fields,
    _links_by_overlap,
    _links_by_same_source_exclusive_module_neighbors,
    _looks_inverse_delta_capable,
    _merge_openapi,
    _module_field_universe,
    _module_tree,
    _normalize_contract_field,
    _oracle_dsl_pack_from_recognized_industries,
    _oracle_family,
    _oracle_library,
    _path_module_prefix,
    _prefer_reversible_write_targets,
    _probes_from_asset,
    _relationship_is_authoritative,
    _reversible_module_write_targets,
    _risk_domains,
    _rule_mentioned_contract_fields,
    _sync_declared_project_sources,
    _dedupe_by_id,
)
from ._api import (  # noqa: F401
    _cli,
    _detect_cross_document_conflicts,
    _extract_entity_relations,
    _structurize_rule_causal_chains,
)

# Public API alias for external consumers (private_pilot_project_assets, tests).
classify_enterprise_knowledge_source = _classify_source

# Preserve broad historical star-import compatibility, including explicitly imported
# underscore symbols, without maintaining another hand-written authority list.
__all__ = sorted(name for name in globals() if not name.startswith("__"))
