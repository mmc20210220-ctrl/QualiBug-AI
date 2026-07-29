"""Minimized public projection for governed UI upload scenarios.

The registry stores the complete immutable formal contract. Project list endpoints only
need enough metadata to explain what will run; selectors, URLs and assertion text remain
inside the governed contract and are never copied into the public record.
"""
from __future__ import annotations

import copy
from typing import Any

from . import ui_upload_scenario_registry as _registry

_INSTALL_MARKER = "_qualibug_upload_scenario_public_projection_installed"
_ORIGINAL_MARKER = "_qualibug_upload_scenario_public_record_before_projection"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, *, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def install_ui_upload_scenario_public_projection() -> None:
    if getattr(_registry, _INSTALL_MARKER, False):
        return
    original = getattr(
        _registry,
        _ORIGINAL_MARKER,
        _registry._public_record,
    )
    setattr(_registry, _ORIGINAL_MARKER, original)

    def public_record_with_governance_summary(
        record: dict[str, Any],
    ) -> dict[str, Any]:
        public = copy.deepcopy(original(record))
        contract = _dict(_dict(record).get("contract"))
        request = _dict(contract.get("ui_request"))
        metadata = _dict(request.get("metadata"))
        submission = _dict(contract.get("submission_contract"))
        prerequisite = _dict(contract.get("safe_prerequisite_operation"))
        mode = _text(
            submission.get("mode") or metadata.get("upload_submission_mode"),
            limit=80,
        )
        if mode:
            public["submission_mode"] = mode
        public["business_cleanup_required"] = bool(
            submission.get("persistent_compensation_required") is True
            or metadata.get("upload_persistent_compensation_required") is True
        )
        cleanup_action = _text(submission.get("cleanup_action"), limit=40)
        if cleanup_action:
            public["cleanup_action"] = cleanup_action
        method = _text(
            prerequisite.get("method") or metadata.get("prerequisite_method"),
            limit=20,
        ).upper()
        if method:
            public["safe_prerequisite_method"] = method
        actor_role = _text(contract.get("actor_role") or request.get("actor_role"), limit=160)
        if actor_role:
            public["actor_role"] = actor_role
        public["raw_selectors_included"] = False
        public["raw_assertion_text_included"] = False
        public["raw_probe_urls_included"] = False
        return public

    _registry._public_record = public_record_with_governance_summary
    setattr(_registry, _INSTALL_MARKER, True)


__all__ = ["install_ui_upload_scenario_public_projection"]
