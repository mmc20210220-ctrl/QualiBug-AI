from pathlib import Path

from ai_test_asset_center.v12_pipeline import run_v12_pipeline


API = """
### GET /api/cases/:id
### PATCH /api/cases/:id
"""

SCHEMA = """
CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED'))
);
"""

RULES = """
# Case lifecycle
OPEN -> CLOSED

Forbidden transitions:
- CLOSED -> OPEN
"""


def test_source_plans_are_not_counted_as_execution_without_runtime_contract(tmp_path):
    result = run_v12_pipeline(
        "plan_only_case",
        Path(tmp_path),
        prd_text=RULES,
        api_spec_text=API,
        db_schema_text=SCHEMA,
        base_url="http://127.0.0.1:9",
    )

    execution = result["phases"]["execution"]
    assert execution["status"] == "plan_only"
    assert execution["executed"] == 0
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["phases"]["scenario_generation"]["plan_only_scenarios"] > 0
