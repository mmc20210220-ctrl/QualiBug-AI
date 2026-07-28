"""Public project-scoped visual baseline governance API.

This is the supported import surface for registering, approving, listing,
revoking and resolving formal UI visual baselines. Import installs the registry
integrity guard; callers never need to reach into the knowledge-center private
modules.
"""
from __future__ import annotations

from .enterprise_knowledge_center._visual_baseline_registry_guard import (
    install_visual_baseline_registry_guard,
)
from .enterprise_knowledge_center._visual_baselines import (
    APPROVED_PREFIX,
    FONT_READINESS,
    INPUT_PREFIX,
    RENDERER_PROFILE,
    SCHEMA_VERSION,
    SCROLL_ORIGIN,
    active_visual_baseline_record,
    approve_visual_baseline,
    list_visual_baselines,
    operate_visual_baseline_registry,
    register_visual_baseline,
    revoke_visual_baseline,
)

install_visual_baseline_registry_guard()

__all__ = [
    "APPROVED_PREFIX",
    "FONT_READINESS",
    "INPUT_PREFIX",
    "RENDERER_PROFILE",
    "SCHEMA_VERSION",
    "SCROLL_ORIGIN",
    "active_visual_baseline_record",
    "approve_visual_baseline",
    "list_visual_baselines",
    "operate_visual_baseline_registry",
    "register_visual_baseline",
    "revoke_visual_baseline",
]
