"""Public single-obligation compiler with explicit fixture-data authority.

The existing semantic compiler remains unchanged in
``experiment_compiler_obligation_core``.  This facade supplies a scoped Behavior
IR view declaring that final ``FlowDataRequirement`` freeze is the fixture/data
planning authority.  The legacy Disposable Fixture discovery therefore cannot
select a primary candidate or block compilation before the complete flow exists.
"""
from __future__ import annotations

from typing import Any

from . import experiment_compiler_obligation_core as _core


FLOW_DATA_AUTHORITY = "flow_data_requirement"


class _AuthorityScopedBehaviorIR(dict):
    """Dict-compatible compile context without adding fingerprinted IR keys."""

    fixture_data_authority: str

    def __init__(self, source: dict[str, Any], *, fixture_data_authority: str) -> None:
        super().__init__(source)
        self.fixture_data_authority = fixture_data_authority


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Compile with final-flow fixture/data planning as the only authority."""
    scoped_ir = _AuthorityScopedBehaviorIR(
        behavior_ir,
        fixture_data_authority=FLOW_DATA_AUTHORITY,
    )
    return _core.compile_experiment_for_obligation(
        obligation,
        behavior_ir=scoped_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
