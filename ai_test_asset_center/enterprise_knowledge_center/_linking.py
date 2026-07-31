"""Stable public facade for relationship linking and Probe compilation.

The implementation is kept in ``_linking_impl``. This facade owns public boundary
semantics: a non-positive Probe budget produces no Probe, and a Probe may leave the
knowledge layer only when its rule-to-interface identity is source-backed. No
import-time replacement or process-global patch is involved.
"""
from __future__ import annotations

from typing import Any

from . import _linking_impl as _impl
from ._linking_impl import *  # noqa: F401,F403

__all__ = list(_impl.__all__)


def _authoritative_probe_pairs(asset: dict[str, Any]) -> set[tuple[str, str]]:
    """Return exact rule/interface identities admitted by existing relationship authority."""
    pairs: set[tuple[str, str]] = set()
    for edge in asset.get("relationships") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("relation") or "") != "rule_to_interface":
            continue
        if not _impl._relationship_is_authoritative(edge):
            continue
        rule_id = str(edge.get("from") or "").strip()
        interface_id = str(edge.get("to") or "").strip()
        if rule_id and interface_id:
            pairs.add((rule_id, interface_id))
    return pairs


def _runtime_plan_interface_ids(asset: dict[str, Any]) -> set[str] | None:
    """Return governed Runtime Plan interfaces when that formal stage exists.

    ``None`` means the enterprise-understanding Runtime Plan stage is not present and
    therefore cannot add an extra restriction. An empty set means the stage exists but
    is closed or contains no formal authoritative action, so no Probe may pass.
    """
    gate = asset.get("runtime_plan_gate")
    if not isinstance(gate, dict):
        return None
    if not bool(gate.get("entry_allowed")):
        return set()

    result: set[str] = set()
    for plan in asset.get("runtime_plans") or []:
        if not isinstance(plan, dict):
            continue
        if not bool(plan.get("formal_runtime_plan")):
            continue
        action = plan.get("action_entry") if isinstance(plan.get("action_entry"), dict) else {}
        if not bool(action.get("authoritative")):
            continue
        interface_id = str(action.get("interface_id") or "").strip()
        if interface_id:
            result.add(interface_id)
    return result


def _probes_from_asset(
    asset: dict[str, Any], max_count: int = 140
) -> list[dict[str, Any]]:
    """Compile only source-backed, runtime-plan-compatible Probes.

    The legacy implementation may emit a first-interface fallback when a risk rule has
    no accepted interface relationship. That candidate is intentionally discarded at
    this public authority boundary. The facade does not select a replacement endpoint,
    infer a nearest path or upgrade token overlap.
    """
    limit = int(max_count)
    if limit <= 0:
        return []

    authoritative_pairs = _authoritative_probe_pairs(asset)
    runtime_interface_ids = _runtime_plan_interface_ids(asset)
    admitted: list[dict[str, Any]] = []
    for raw in _impl._probes_from_asset(asset, limit):
        if not isinstance(raw, dict):
            continue
        probe = dict(raw)
        lineage = (
            dict(probe.get("knowledge_lineage"))
            if isinstance(probe.get("knowledge_lineage"), dict)
            else {}
        )
        rule_id = str(lineage.get("rule_id") or "").strip()
        interface_id = str(lineage.get("interface_id") or "").strip()
        if (rule_id, interface_id) not in authoritative_pairs:
            continue
        if runtime_interface_ids is not None and interface_id not in runtime_interface_ids:
            continue

        lineage["binding_authority"] = "accepted_rule_to_interface_relationship"
        lineage["arbitrary_endpoint_fallback_used"] = False
        lineage["token_overlap_is_authoritative"] = False
        if runtime_interface_ids is not None:
            lineage["runtime_plan_interface_admitted"] = True
        probe["knowledge_lineage"] = lineage
        admitted.append(probe)
        if len(admitted) >= limit:
            break
    return admitted


def __getattr__(name: str) -> Any:
    """Preserve direct private-symbol compatibility during the module split."""
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
