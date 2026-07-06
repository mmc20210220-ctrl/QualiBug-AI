from ai_test_asset_center.reasoner_prompt import REASONER_SYSTEM_PROMPT
from ai_test_asset_center.stage_reason_all_v2 import OUTPUT_HARD_LIMITS


def test_reasoner_prompt_requires_executable_verification_method():
    assert "verification_method" in REASONER_SYSTEM_PROMPT
    assert "具体 API 调用" in REASONER_SYSTEM_PROMPT
    assert "参数" in REASONER_SYSTEM_PROMPT
    assert "断言" in REASONER_SYSTEM_PROMPT


def test_reasoner_prompt_forbids_fabricated_api_paths():
    assert "编造 API 路径" in REASONER_SYSTEM_PROMPT
    assert "path 必须来自输入的 api_schema" in REASONER_SYSTEM_PROMPT
    assert "不要编一个" in REASONER_SYSTEM_PROMPT


def test_reasoner_prompt_rejects_vague_narrative_output():
    assert "空洞描述" in REASONER_SYSTEM_PROMPT
    assert "每条假设必须包含具体的症状" in REASONER_SYSTEM_PROMPT
    assert "具体的验证方法" in REASONER_SYSTEM_PROMPT
    assert "具体的误报场景" in REASONER_SYSTEM_PROMPT


def test_reasoner_output_hard_limits_keep_json_and_count_contract():
    assert "Return exactly one top-level JSON object" in OUTPUT_HARD_LIMITS
    assert "Return at most 15 hypotheses" in OUTPUT_HARD_LIMITS
    assert "Return JSON only" in OUTPUT_HARD_LIMITS
    assert "Do not include analysis, markdown, commentary, or code fences" in OUTPUT_HARD_LIMITS
