"""Keep exact Job source conflicts visible in the existing Business Behavior IR.

The base Job projection already refuses confirmation when evidence authority is conflicted.
This compatibility policy makes the refusal explicit: cron, handler, connector, actor,
terminal-state or other exact source disagreements become ``CONFLICTED`` behavior with a
named reason rather than a generic candidate.  Display-name variants are handled separately
as non-authoritative presentation aliases.
"""
from __future__ import annotations

import sys
from typing import Any

_INSTALL_MARKER = "_qualibug_job_behavior_conflict_policy"
_PROJECTION_MODULE = (
    "ai_test_asset_center.enterprise_knowledge_center.job_behavior_projection"
)
_REASON = "ASYNC_JOB_SOURCE_FACT_CONFLICT"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def install_job_behavior_conflict_policy() -> bool:
    """Patch only the already-loaded existing Job behavior projector."""
    projection = sys.modules.get(_PROJECTION_MODULE)
    if projection is None:
        return False
    current = getattr(projection, "_job_behavior", None)
    if not callable(current):
        return False
    if getattr(current, _INSTALL_MARKER, False):
        return True
    original = current

    def project_with_explicit_conflicts(*args: Any, **kwargs: Any):
        behavior, unresolved, primary_reason = original(*args, **kwargs)
        asset = args[0] if args else kwargs.get("asset")
        source_conflicts = [
            dict(row)
            for row in _list(
                asset.get("source_fact_conflicts")
                if isinstance(asset, dict)
                else []
            )
            if isinstance(row, dict)
            and _text(row.get("field")) != "display_name"
        ]
        if not source_conflicts:
            return behavior, unresolved, primary_reason

        reasons = [
            _text(value) for value in _list(unresolved) if _text(value)
        ]
        if _REASON not in reasons:
            reasons.append(_REASON)
        updated = dict(behavior)
        updated["status"] = "CONFLICTED"
        updated["candidate_only"] = True
        updated["formal_business_rule"] = False
        updated["formal_business_finding_eligible"] = False
        updated["permission_decision"] = "UNSPECIFIED"
        updated["permission_authority"] = "CONFLICTED_SOURCE_EVIDENCE"
        updated["unresolved_semantics"] = reasons
        updated["source_fact_conflicts"] = source_conflicts
        return updated, reasons, _REASON

    setattr(project_with_explicit_conflicts, _INSTALL_MARKER, True)
    project_with_explicit_conflicts._qualibug_original_projection = original  # type: ignore[attr-defined]
    setattr(projection, "_job_behavior", project_with_explicit_conflicts)
    return True


__all__ = ["install_job_behavior_conflict_policy"]
