"""Public runtime-binding facade with source-backed safety extensions."""
from __future__ import annotations
from typing import Any

from . import runtime_binding_graph_mainline_base as _base
from .runtime_read_resolver_authority import install_runtime_read_resolver_authority
from .effect_observer_source_authority import install_effect_observer_source_authority

_target = _base._target
install_runtime_read_resolver_authority(_target)
install_effect_observer_source_authority(_target)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)
# The base facade copied these names before the extension was installed.
declared_runtime_read_resolvers = _target.declared_runtime_read_resolvers
declared_effect_observers = _target.declared_effect_observers


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
