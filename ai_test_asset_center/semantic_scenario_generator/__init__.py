"""semantic_scenario_generator package - backward-compatible facade.

All public and private symbols are re-exported so that existing
``from .semantic_scenario_generator import X`` continues to work.
"""
from ._common import *  # noqa: F401,F403
from ._generator import *  # noqa: F401,F403
from ._helpers import *  # noqa: F401,F403

# Explicit re-exports for underscore-prefixed symbols
from ._common import _SCENARIO_ENRICHER  # noqa: F401
from ._helpers import _adjacent_read_for_entity, _documented_observation_read_candidates, _observation_read_candidates  # noqa: F401

__all__ = [
    "ScenarioEnricher",
    "_SCENARIO_ENRICHER",
    "register_scenario_enricher",
    "clear_scenario_enricher",
    "ScenarioStep",
    "ExecutableScenario",
    "SemanticScenarioGenerator",
    "_adjacent_read_for_entity",
    "_documented_observation_read_candidates",
    "_observation_read_candidates",
]
