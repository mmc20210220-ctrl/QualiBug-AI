"""Single explicit composition root for enterprise knowledge construction.

The module owns one call graph:
base source asset -> API artifact projection -> database-model fact projection ->
enterprise understanding -> downstream binding -> governed Jobs -> final Probe
admission -> one final persistence receipt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _api as _base_api
from . import _chinese_business_downstream as _downstream
from ._common import ROOT, _safe_project_id
from ._formal_ui_contract_guard import install_formal_ui_root_array_guard
from ._formal_ui_contracts import install_formal_ui_contract_parser
from ._formal_ui_persistent_probe_guard import install_formal_ui_persistent_probe_guard
from ._formal_ui_visual_baseline_guard import install_formal_ui_visual_baseline_guard
from ._formal_ui_visual_viewport_guard import install_formal_ui_visual_viewport_guard
from ._utils import _load_registry, _now, _paths, _save_registry
from .api_artifact_asset_projection import enrich_asset_with_api_artifact_semantics
from .database_model_asset_projection import enrich_asset_with_database_model_facts
from .database_model_index_reconciliation import (
    reconcile_database_model_index_assets,
)
from .database_model_semantic_bridge import install_database_model_semantic_bridge
from .database_table_source_alignment import (
    enrich_asset_with_database_table_alignment_candidates,
)
from .enterprise_understanding.integration import (
    _parsed_sources_for_context,
    enrich_asset_with_enterprise_understanding,
)
from .enterprise_understanding.interface_runtime_contracts import (
    install_interface_runtime_contract_parser,
)
from .enterprise_understanding.probe_policy import (
    build_gated_probes,
    probe_generation_block_reason,
)
from .job_asset_pipeline import enrich_job_assets_with_governance
from .job_behavior_projection import refresh_job_behavior_projection


def _probe_limit(value: Any, *, default: int = 140) -> int:
    """Resolve a Probe budget without treating an explicit zero as missing."""
    if value is None or value == "":
        return default
    return max(0, int(value))


def configure_source_parser_extensions() -> None:
    """Explicit compatibility boundary for legacy parser plugins.

    Parser extension registration remains idempotent, but it is no longer performed
    merely by importing the knowledge package and it never replaces the build
    authority. New parser work should move into a registry rather than add wrappers.
    """
    install_formal_ui_root_array_guard()
    install_formal_ui_contract_parser()
    install_formal_ui_persistent_probe_guard()
    install_formal_ui_visual_baseline_guard()
    install_formal_ui_visual_viewport_guard()
    install_interface_runtime_contract_parser()
    install_database_model_semantic_bridge()


def _finalize_probe_relationships(
    asset: dict[str, Any], probes: list[dict[str, Any]]
) -> None:
    ready_rules = {
        str(row.get("rule_id") or "").strip()
        for row in asset.get("rule_library") or []
        if isinstance(row, dict)
        and str(row.get("downstream_binding_status") or "").strip()
        == "READY_AUTHORITATIVE_OPERATION_BOUND"
    }
    implementation_status = str(
        (asset.get("implementation_binding_gate") or {}).get("status") or "NOT_BUILT"
    )
    scenario_status = str(
        (asset.get("scenario_planning_gate") or {}).get("status") or "NOT_BUILT"
    )
    for probe in probes:
        lineage = dict(probe.get("knowledge_lineage") or {})
        rule_id = str(lineage.get("rule_id") or "").strip()
        if rule_id in ready_rules:
            lineage.update(
                {
                    "business_comprehension_gate": "READY_AUTHORITATIVE_OPERATION_BOUND",
                    "implementation_binding_gate": implementation_status,
                    "scenario_planning_gate": scenario_status,
                    "fact_authority": "original_chinese_source_span",
                }
            )
            probe["knowledge_lineage"] = lineage

    relationships = [
        dict(row)
        for row in asset.get("relationships") or []
        if isinstance(row, dict)
        and not (
            str(row.get("relation") or "") == "risk_to_probe"
            and str(row.get("to") or "").startswith("probe:")
        )
    ]
    for probe in probes:
        lineage = dict(probe.get("knowledge_lineage") or {})
        risk_id = str(lineage.get("risk_id") or "").strip()
        probe_id = str(probe.get("probe_id") or "").strip()
        if not risk_id or not probe_id:
            continue
        relationships.append(
            {
                "edge_id": f"edge:risk-probe:{risk_id}:{probe_id}",
                "from": risk_id,
                "to": f"probe:{probe_id}",
                "relation": "risk_to_probe",
                "confidence": 1.0,
                "status": "accepted",
                "derivation": "final_knowledge_composition",
                "evidence": {
                    "execution_policy": probe.get("execution_policy"),
                    "scenario_planning_gate": scenario_status,
                    "runtime_materialization_gate": (
                        asset.get("runtime_materialization_gate") or {}
                    ).get("status"),
                },
            }
        )
    asset["relationships"] = _base_api._dedupe_by_id(relationships, "edge_id")


def _persist_final(
    asset: dict[str, Any],
    probes: list[dict[str, Any]],
    *,
    project_id: str,
    root: Path,
) -> None:
    _downstream._persist(asset, probes, project_id=project_id, root=root)
    registry = _load_registry(project_id, root)
    registry["audit_events"].append(
        {
            "event": "finalize_enterprise_knowledge_composition",
            "at_utc": _now(),
            "actor": {"name": "system", "role": "knowledge_composition_root"},
            "asset_id": asset.get("asset_id"),
            "probe_count": len(probes),
            "probe_generation_status": asset.get("probe_generation_gate", {}).get(
                "status"
            ),
        }
    )
    _save_registry(project_id, root, registry)


def build_enterprise_business_knowledge_asset(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic final asset without wrapper-installed authorities."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    resolved_options = dict(options or {})
    final_probe_limit = _probe_limit(resolved_options.get("probe_limit"))

    configure_source_parser_extensions()

    # The base compiler is an extraction primitive in this composition. It is not
    # allowed to publish Probes before semantic, implementation and runtime gates.
    base_options = {**resolved_options, "probe_limit": 0}
    asset = _base_api.build_enterprise_business_knowledge_asset(
        project, resolved_root, base_options
    )
    parsed_sources = _parsed_sources_for_context(asset, resolved_root)

    # Technical declarations must be projected before enterprise cognition. Neither
    # stage invents a business sequence from document or diagram order.
    asset = enrich_asset_with_api_artifact_semantics(asset, parsed_sources)
    asset = enrich_asset_with_database_model_facts(asset, parsed_sources)
    asset = reconcile_database_model_index_assets(asset)
    asset = enrich_asset_with_database_table_alignment_candidates(asset)
    asset = enrich_asset_with_enterprise_understanding(
        asset, parsed_sources=parsed_sources
    )

    # Downstream rule/oracle projection is still needed before Job projection, but
    # Probe compilation remains deferred to the final stage.
    asset, _discarded = _downstream.refresh_chinese_business_downstream(
        asset, max_probe_count=0
    )
    asset = enrich_job_assets_with_governance(
        asset,
        project_id=project,
        root=resolved_root,
        options=resolved_options,
    )
    asset = refresh_job_behavior_projection(asset)

    block_reason = probe_generation_block_reason(asset)
    probes = build_gated_probes(
        asset,
        final_probe_limit,
        compiler=_base_api._probes_from_asset,
    )
    _finalize_probe_relationships(asset, probes)

    asset["probe_generation_gate"] = {
        "schema": "qualibug.enterprise-probe-generation-gate.v1",
        "status": "PASS" if not block_reason else "BLOCKED",
        "entry_allowed": not bool(block_reason),
        "block_reason": block_reason,
        "probe_count": len(probes),
        "probe_limit": final_probe_limit,
        "build_authority": "explicit_enterprise_knowledge_composition",
    }
    summary = dict(asset.get("summary") or {})
    summary.update(
        {
            "generated_probe_count": len(probes),
            "relationship_count": len(asset.get("relationships") or []),
            "probe_generation_status": asset["probe_generation_gate"]["status"],
            "knowledge_composition_authority": "explicit_single_call_graph",
        }
    )
    asset["summary"] = summary
    governance = dict(asset.get("governance") or {})
    governance.update(
        {
            "knowledge_builder_uses_explicit_composition_root": True,
            "knowledge_builder_wrapper_chain_enabled": False,
            "probe_generation_occurs_after_final_gates": True,
            "zero_probe_budget_is_strict": True,
            "job_governance_uses_direct_function_calls": True,
            "package_import_replaces_build_authority": False,
            "parser_extension_registration_is_explicit_compatibility_boundary": True,
            "api_artifact_projection_precedes_enterprise_understanding": True,
            "database_model_projection_precedes_enterprise_understanding": True,
            "database_model_semantic_bridge_installed_explicitly": True,
            "database_model_index_reconciliation_precedes_enterprise_understanding": True,
            "database_table_alignment_precedes_enterprise_understanding": True,
        }
    )
    asset["governance"] = governance
    _persist_final(asset, probes, project_id=project, root=resolved_root)
    return asset


def load_enterprise_business_knowledge_asset(
    project_id: str = "real_project_demo", root: Path | None = None
) -> dict[str, Any] | None:
    """Load the already-finalized asset; loading never enriches or rewrites it."""
    return _base_api.load_enterprise_business_knowledge_asset(project_id, root)


def generate_enterprise_business_knowledge_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    project_id: str = "real_project_demo",
    root: Path | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Return the final governed catalog, rebuilding through the composition root if absent."""
    del openapi, cfg
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    asset = load_enterprise_business_knowledge_asset(project, resolved_root)
    if asset is None:
        asset = build_enterprise_business_knowledge_asset(
            project,
            resolved_root,
            {"probe_limit": _probe_limit(max_count)},
        )
    catalog = _base_api._load_json(_paths(project, resolved_root)["probe_catalog"], {})
    rows = [
        dict(row)
        for row in (catalog.get("items") if isinstance(catalog, dict) else []) or []
        if isinstance(row, dict)
    ]
    return (
        rows[: _probe_limit(max_count, default=len(rows))]
        if max_count is not None
        else rows
    )


__all__ = [
    "configure_source_parser_extensions",
    "build_enterprise_business_knowledge_asset",
    "load_enterprise_business_knowledge_asset",
    "generate_enterprise_business_knowledge_probes",
]
