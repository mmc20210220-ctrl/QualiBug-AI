"""Public project-scoped visual baseline governance API.

This is the supported import surface for registering, approving, listing,
revoking and resolving formal UI visual baselines. Import installs the registry
integrity guard before public function aliases are bound, so callers cannot
bypass lifecycle hardening through an early-captured function reference.
"""
from __future__ import annotations

from .enterprise_knowledge_center import _visual_baselines as _registry
from .enterprise_knowledge_center._visual_baseline_registry_guard import (
    install_visual_baseline_registry_guard,
)
from .private_pilot_visual_baseline_health_patch import (
    install_visual_baseline_health_patch,
)

install_visual_baseline_registry_guard()
install_visual_baseline_health_patch()

APPROVED_PREFIX = _registry.APPROVED_PREFIX
FONT_READINESS = _registry.FONT_READINESS
INPUT_PREFIX = _registry.INPUT_PREFIX
RENDERER_PROFILE = _registry.RENDERER_PROFILE
SCHEMA_VERSION = _registry.SCHEMA_VERSION
SCROLL_ORIGIN = _registry.SCROLL_ORIGIN
active_visual_baseline_record = _registry.active_visual_baseline_record
approve_visual_baseline = _registry.approve_visual_baseline
list_visual_baselines = _registry.list_visual_baselines
operate_visual_baseline_registry = _registry.operate_visual_baseline_registry
register_visual_baseline = _registry.register_visual_baseline
revoke_visual_baseline = _registry.revoke_visual_baseline

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
