from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.policy_registry import ReasonerPolicy
from ai_test_asset_center.stage_reason_all_v2 import SIDE_PATH_REASONER_ENGINES


def test_reasoner_policy_keeps_side_path_engines_enabled() -> None:
    policy = ReasonerPolicy()

    expected = {
        "business_outcome",
        "business_reconciliation",
        "business_invariant",
        "multi_source_reasoning",
        "business_lifecycle",
        "consistency_isolation",
    }

    assert len(SIDE_PATH_REASONER_ENGINES) == 6
    assert expected.issubset(set(policy.enabled_engines))
    assert policy.max_workers == 4
    assert policy.max_hypotheses_per_engine == 15
    assert policy.timeout_seconds >= 300
    assert policy.max_tokens == 32768


def test_reasoner_policy_clamps_max_tokens() -> None:
    low = ReasonerPolicy(max_tokens=4096)
    high = ReasonerPolicy(max_tokens=250000)

    assert low.max_tokens == 32768
    assert high.max_tokens == 100000


def test_pilot_runtime_real_project_discovery_bridges_side_path(monkeypatch, tmp_path: Path) -> None:
    from ai_test_asset_center import enterprise_pilot_runtime as runtime

    def fake_discovery(project: str, root: Path) -> dict:
        assert project == "demo_project"
        assert root == tmp_path
        return {
            "network_requests": 3,
            "issues": [{"issue_id": "I1"}],
            "probes": {"items": [{"probe_id": "P1"}, {"probe_id": "P2"}]},
            "business_outcome_finding_count": 1,
            "business_reconciliation_finding_count": 2,
            "business_invariant_finding_count": 3,
            "multi_source_reasoning_finding_count": 4,
            "business_lifecycle_finding_count": 5,
            "consistency_isolation_finding_count": 6,
            "output_dir": "out/demo_project/defect_discovery",
        }

    monkeypatch.setattr(runtime, "run_real_project_discovery", fake_discovery)
    monkeypatch.setattr(runtime, "_environment", lambda project, root, target: {"name": target, "type": "system_test"})
    monkeypatch.setattr(runtime, "_is_production", lambda environment: False)

    result = runtime._run_job(
        {"job_type": "real_project_discovery", "target_environment": "test"},
        "demo_project",
        tmp_path,
    )

    assert result["status"] == "succeeded"
    assert result["run_mode"] == "real_project_discovery"
    assert result["network_requests"] == 3
    assert result["issue_count"] == 1
    assert result["probe_count"] == 2
    assert result["business_outcome_finding_count"] == 1
    assert result["consistency_isolation_finding_count"] == 6


def test_reasoner_parser_salvages_python_literal_hypotheses() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import _parse_engine_content

    raw = (
        '{"choices":[{"message":{"content":'
        '"{\'hypotheses\': [{\'title\': \'reservation drift\', \'severity\': \'P1\'}]}"}'
        "}]}"
    )

    hypotheses, status, degradation = _parse_engine_content(raw)

    assert status == "degraded"
    assert degradation == "python_literal_json_salvaged"
    assert hypotheses == [{"title": "reservation drift", "severity": "P1"}]


def test_reasoner_parser_extracts_json_from_wrapped_content() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import _parse_engine_content

    raw = (
        '{"choices":[{"message":{"content":'
        '"Here is the JSON:\\n```json\\n{\\"hypotheses\\":[{\\"title\\":\\"wrapped\\",\\"severity\\":\\"P2\\"}]}\\n```"}'
        "}]}"
    )

    hypotheses, status, degradation = _parse_engine_content(raw)

    assert status == "degraded"
    assert degradation == "json_slice_extracted"
    assert hypotheses == [{"title": "wrapped", "severity": "P2"}]


def test_reasoner_parser_marks_code_fence_as_degraded() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import _parse_engine_content

    raw = (
        '{"choices":[{"message":{"content":'
        '"```json\\n{\\"hypotheses\\":[{\\"title\\":\\"fenced\\"}]}\\n```"'
        "}}]}"
    )

    hypotheses, status, degradation = _parse_engine_content(raw)

    assert status == "degraded"
    assert degradation == "code_fence_removed"
    assert hypotheses == [{"title": "fenced"}]


def test_reasoner_parser_normalizes_nested_and_alternate_roots() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import _parse_engine_content

    raw = (
        '{"choices":[{"message":{"content":'
        '"{\\"data\\":{\\"findings\\":[{\\"title\\":\\"nested\\",\\"severity\\":\\"P1\\"}]}}"'
        "}}]}"
    )

    hypotheses, status, degradation = _parse_engine_content(raw)

    assert status == "degraded"
    assert "nested_root:data" in degradation
    assert "alternate_root_key:findings" in degradation
    assert hypotheses == [{"title": "nested", "severity": "P1"}]


def test_reasoner_parser_handles_content_parts_and_string_items() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import _parse_engine_content

    raw = (
        '{"choices":[{"message":{"content":['
        '{"type":"text","text":"{\\"hypotheses\\":[\\"string risk\\"]}"}'
        "]}}]}"
    )

    hypotheses, status, degradation = _parse_engine_content(raw)

    assert status == "degraded"
    assert degradation == "string_hypothesis_items"
    assert hypotheses == [{"title": "string risk", "source_format": "string_hypothesis"}]


def test_reasoner_worker_defaults_to_json_object_response_format(monkeypatch) -> None:
    from ai_test_asset_center.llm_reasoning import ReasoningConfig
    from ai_test_asset_center.stage_reason_all_v2 import _run_reasoner_engine

    captured: dict[str, str | int] = {}

    class FakeReasoningClient:
        def __init__(self, config: ReasoningConfig) -> None:
            captured["response_format"] = config.response_format
            captured["max_tokens"] = config.max_tokens

        def _chat(self, prompt: str, *, system_prompt: str | None = None) -> str:
            return '{"choices":[{"message":{"content":"{\\"hypotheses\\":[{\\"title\\":\\"ok\\"}]}"}}]}'

    import ai_test_asset_center.llm_reasoning as llm_reasoning

    monkeypatch.delenv("QUALIBUG_DISABLE_REASONER_JSON_MODE", raising=False)
    monkeypatch.setattr(llm_reasoning, "ReasoningClient", FakeReasoningClient)

    config = ReasoningConfig(
        base_url="https://example.invalid/v1",
        api_key="test",
        model="test-model",
        max_tokens=4096,
        response_format="",
    )
    result = _run_reasoner_engine(
        "smoke",
        "",
        "prompt",
        "system",
        config,
        retry_count=0,
    )

    assert captured["response_format"] == "json_object"
    assert captured["max_tokens"] == 32768
    assert result["status"] == "success"
    assert len(result["hypotheses"]) == 1


def test_reasoner_worker_allows_enterprise_max_tokens(monkeypatch) -> None:
    from ai_test_asset_center.llm_reasoning import ReasoningConfig
    from ai_test_asset_center.stage_reason_all_v2 import _run_reasoner_engine

    captured: dict[str, int] = {}

    class FakeReasoningClient:
        def __init__(self, config: ReasoningConfig) -> None:
            captured["max_tokens"] = config.max_tokens

        def _chat(self, prompt: str, *, system_prompt: str | None = None) -> str:
            return '{"choices":[{"message":{"content":"{\\"hypotheses\\":[{\\"title\\":\\"ok\\"}]}"}}]}'

    import ai_test_asset_center.llm_reasoning as llm_reasoning

    monkeypatch.setattr(llm_reasoning, "ReasoningClient", FakeReasoningClient)

    config = ReasoningConfig(
        base_url="https://example.invalid/v1",
        api_key="test",
        model="test-model",
        max_tokens=100000,
    )
    result = _run_reasoner_engine(
        "smoke",
        "",
        "prompt",
        "system",
        config,
        retry_count=0,
    )

    assert captured["max_tokens"] == 100000
    assert result["status"] == "success"


def test_stage_reasoner_reports_env_max_tokens(monkeypatch) -> None:
    import ai_test_asset_center.policy_wiring as policy_wiring
    import ai_test_asset_center.stage_reason_all_v2 as stage
    from ai_test_asset_center.llm_reasoning import ReasoningClient

    class Dummy:
        def __init__(self) -> None:
            self.client = ReasoningClient()
            self._last_engine_report = {}

        def _fill_template(self, template: str, **kwargs: object) -> str:
            return "{}"

    def fake_run(engine_name, template, prompt, system_prompt, client_config, **kwargs):
        return {
            "engine_name": engine_name,
            "hypotheses": [{"title": engine_name}],
            "status": "success",
            "attempts": 1,
            "retry_used": False,
            "raw_chars": 1,
            "content_chars": 1,
            "duration_seconds": 0.0,
            "error": "",
            "degradation_reason": "",
        }

    def patched_policy(section: str, key: str, default=None):
        if section == "reasoner" and key == "enabled_engines":
            return ["causality"]
        if section == "reasoner" and key == "retry_count":
            return 0
        return default

    monkeypatch.setenv("QUALIBUG_REASONER_MAX_TOKENS", "100000")
    monkeypatch.setattr(stage, "_run_reasoner_engine", fake_run)
    monkeypatch.setattr(policy_wiring, "get_policy_value", patched_policy)

    dummy = Dummy()
    hypotheses = stage._stage_reason_all_v2(dummy, "prd", "api", {}, prior_findings=[])

    assert len(hypotheses) == 1
    assert dummy._last_engine_report["max_tokens"] == 100000
