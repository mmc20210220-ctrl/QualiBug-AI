from hashlib import sha256
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_mainline_contract import MainlineContractError
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


def test_retired_legacy_authority_fails_before_campaign_or_candidate_runner(tmp_path):
    with pytest.raises(
        MainlineContractError,
        match="mainline_runner_unavailable:legacy_champion",
    ):
        run_v12_pipeline(
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
                "policy_id": "policy_legacy",
                "policy_version": "policy_v1",
                "strategy_fingerprint": "a" * 64,
                "evaluation_mode": "operational",
                "source_manifest": {
                    "source_id": "api-contract",
                    "source_hash": sha256(API.encode("utf-8")).hexdigest(),
                    "source_origin": "declared_manifest",
                },
            },
        )

    assert not (tmp_path / "platform_workspace").exists()
