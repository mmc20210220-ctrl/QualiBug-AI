"""Enterprise-understanding integration with downstream identity closure.

The existing integration remains the source-ingestion and semantic-closure authority.
This package reuses it and performs one final stable-identity projection before the
asset is persisted.
"""
from __future__ import annotations

import importlib.util
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from ..business_world_model import project_business_world_model
from ..identity_downstream_projection import project_identity_to_downstream
from ..schema import as_dict

_PACKAGE = __package__.rsplit(".integration", 1)[0]
_LEGACY_NAME = f"{_PACKAGE}._enterprise_understanding_integration_v1"
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "integration_legacy_v1.py"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError(f"cannot load enterprise-understanding integration: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules.setdefault(_LEGACY_NAME, _legacy)
_spec.loader.exec_module(_legacy)

for _name, _value in vars(_legacy).items():
    if _name.startswith("__") or _name in {
        "enrich_asset_with_enterprise_understanding",
        "install_enterprise_understanding_model",
    }:
        continue
    globals().setdefault(_name, _value)


def enrich_asset_with_enterprise_understanding(
    asset: dict[str, Any],
    *,
    parsed_sources: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = _legacy.enrich_asset_with_enterprise_understanding(
        asset,
        parsed_sources=parsed_sources,
    )
    model = as_dict(enriched.get("enterprise_understanding_model"))
    model = project_identity_to_downstream(enriched, model)
    model = project_business_world_model(enriched, model)
    _legacy.project_final_scenario_planning_gate(enriched, model)
    enriched["enterprise_understanding_model"] = model

    admission = as_dict(model.get("identity_execution_admission"))
    summary = as_dict(enriched.get("summary"))
    summary.update(
        {
            "enterprise_identity_execution_status": admission.get("status"),
            "enterprise_identity_execution_ready": bool(admission.get("entry_allowed")),
            "enterprise_identity_binding_count": int(admission.get("identity_binding_count") or 0),
            "enterprise_identity_unresolved_behavior_count": len(
                admission.get("unresolved_behavior_refs") or []
            ),
            "business_world_model_id": as_dict(
                model.get("business_world_model")
            ).get("world_model_id"),
            "business_world_model_status": as_dict(
                as_dict(model.get("business_world_model")).get("gate")
            ).get("status"),
            "business_world_model_ready": bool(
                as_dict(
                    as_dict(model.get("business_world_model")).get("gate")
                ).get("world_model_ready")
            ),
        }
    )
    enriched["summary"] = summary
    governance = as_dict(enriched.get("governance"))
    governance.update(
        {
            "stable_enterprise_entity_id_projects_to_behavior_ir": True,
            "stable_enterprise_entity_id_projects_to_scenario_ir": True,
            "stable_enterprise_entity_id_projects_to_runtime_plan": True,
            "governed_implementation_bindings_project_identity_bindings": True,
            "name_only_execution_allowed": False,
            "business_world_model_reuses_enterprise_understanding_authority": True,
            "business_world_model_semantic_payload_duplication_allowed": False,
            "business_world_model_automatic_entity_union_allowed": False,
        }
    )
    enriched["governance"] = governance
    return enriched


def install_enterprise_understanding_model():
    """Install the existing integration with downstream identity closure."""
    from ... import _api
    from ..._common import ROOT, _safe_project_id

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, "_qualibug_enterprise_understanding_identity_closed", False):
        return current
    original = current

    @wraps(original)
    def wrapped(
        project_id: str = "real_project_demo",
        root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        asset = original(project, resolved_root, options or {})
        parsed_sources = _legacy._parsed_sources_for_context(asset, resolved_root)
        enriched = enrich_asset_with_enterprise_understanding(
            asset,
            parsed_sources=parsed_sources,
        )
        _legacy._persist(enriched, project_id=project, root=resolved_root)
        return enriched

    wrapped._qualibug_enterprise_understanding_model = True  # type: ignore[attr-defined]
    wrapped._qualibug_enterprise_understanding_identity_closed = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "enrich_asset_with_enterprise_understanding",
    "install_enterprise_understanding_model",
]
