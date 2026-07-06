from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator


SCHEMA = """
CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('CREATED', 'CLOSED', 'ARCHIVED'))
);
"""

API = """
### GET /api/cases/:id
### POST /api/cases/:id/close
"""

RULES = """
# Case lifecycle
CREATED -> CLOSED

Forbidden transitions:
- CLOSED -> CREATED
- ARCHIVED -> CREATED

# Case rules
A closed case must not be reopened.
"""


def test_behavior_graph_uses_only_entities_and_states_in_current_project_sources():
    graphs = BusinessStateGraphBuilder().build(RULES, API, SCHEMA)

    assert set(graphs) == {"case"}
    assert {"CREATED", "CLOSED", "ARCHIVED"}.issubset(graphs["case"].states)
    assert any(item.is_forbidden and item.from_state == "CLOSED" for item in graphs["case"].transitions)
    assert any("must not be reopened" in rule for node in graphs["case"].states.values() for rule in node.invariants)
    assert "order" not in graphs
    assert "payment" not in graphs


def test_source_scenarios_do_not_invent_admin_or_fallback_routes():
    graphs = BusinessStateGraphBuilder().build(RULES, API, SCHEMA)
    scenarios = SemanticScenarioGenerator().generate(graphs, API)

    assert scenarios
    assert all(item.execution_policy.startswith("plan_only") for item in scenarios)
    assert all(not item.actors for item in scenarios)
    assert all(not item.steps for item in scenarios)
    assert all("admin" not in str(item.to_dict()).lower() for item in scenarios)
