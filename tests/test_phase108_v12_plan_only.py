from hashlib import sha256
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
        base_url="",
        campaign_context={
            "mainline_authority": "legacy_champion",
            "run_id": "run_plan_only_case",
            "target_id": "target_plan_only_case",
            "environment_id": "env_plan_only_case",
            "policy_version": "policy_v1",
            "evaluation_mode": "operational",
            "source_manifest": {
                "source_id": "api-contract",
                "source_hash": sha256(API.encode("utf-8")).hexdigest(),
                "source_origin": "declared_manifest",
            },
        },
    )

    execution = result["phases"]["execution"]
    assert execution["status"] == "plan_only"
    assert execution["executed"] == 0
    assert result["auto_har"]["status"] == "no_traffic"
