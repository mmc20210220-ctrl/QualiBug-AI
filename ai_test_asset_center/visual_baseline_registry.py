"""Public project-scoped visual baseline governance API.

This is the supported import surface for registering, approving, listing,
revoking and resolving formal UI visual baselines. Import installs registry
integrity and projection guards before public function aliases are bound, so
callers cannot bypass lifecycle hardening or receive history-dependent counter
semantics through an early-captured function reference.

The private-pilot service imports this surface while installing its governed UI
routes. That same safe bootstrap point installs additive visual, browser-matrix
and accessibility health metadata without launching or downloading a browser.
"""
from __future__ import annotations

from .enterprise_knowledge_center import _visual_baselines as _registry
from .enterprise_knowledge_center._visual_baseline_registry_guard import (
    install_visual_baseline_registry_guard,
)
from .enterprise_knowledge_center._visual_baseline_registry_projection_guard import (
    install_visual_baseline_registry_projection_guard,
)
from .private_pilot_accessibility_health_patch import (
    install_accessibility_health_patch,
)
from .private_pilot_browser_matrix_health_patch import (
    install_browser_matrix_health_patch,
)
from .private_pilot_visual_baseline_health_patch import (
    install_visual_baseline_health_patch,
)

install_visual_baseline_registry_guard()
install_visual_baseline_registry_projection_guard()
install_visual_baseline_health_patch()
install_browser_matrix_health_patch()
install_accessibility_health_patch()

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
