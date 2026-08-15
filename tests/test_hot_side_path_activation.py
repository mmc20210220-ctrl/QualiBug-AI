from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.policy_registry import ReasonerPolicy
from ai_test_asset_center.policy_wiring import _REASONER_MAX_HYPOTHESES_PER_ENGINE
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
    assert policy.max_hypotheses_per_engine == _REASONER_MAX_HYPOTHESES_PER_ENGINE
    assert policy.timeout_seconds >= 300
    assert policy.max_tokens == 32768


def test_reasoner_policy_upgrades_legacy_persisted_hypothesis_cap() -> None:
    policy = ReasonerPolicy(max_hypotheses_per_engine=15)

    assert policy.max_hypotheses_per_engine == _REASONER_MAX_HYPOTHESES_PER_ENGINE


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


def test_reasoner_parser_rejects_outer_and_choice_shape_with_stable_codes() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import _parse_engine_content

    cases = [
        ('[{"unexpected":"list-root"}]', "outer_response_not_object"),
        ('{"choices":"not-a-list"}', "invalid_choices_shape"),
        ('{"choices":["not-an-object"]}', "invalid_choice_item"),
        ('{"choices":[{"message":"not-an-object"}]}', "invalid_message_shape"),
    ]

    for raw, expected_code in cases:
        hypotheses, status, degradation = _parse_engine_content(raw)
        assert hypotheses is None
        assert status == "failed"
        assert degradation == expected_code


def test_reasoner_worker_defaults_to_json_object_response_format(monkeypatch) -> None:
    from ai_test_asset_center.llm_reasoning import ReasoningConfig
    from ai_test_asset_center.stage_reason_all_v2 import _run_reasoner_engine

    captured: dict[str, str | int] = {}

    class FakeReasoningClient:
        def __init__(self, config: ReasoningConfig) -> None:
            captured["response_format"] = config.response_format
            captured["max_tokens"] = config.max_tokens

        def _chat(self, prompt: str, *, system_prompt: str | None = None, call_point: str | None = None) -> str:
            return '{"choices":[{"message":{"content":"{\\"hypotheses\\":[{\\"title\\":\\"ok\\"}]}"}}]}'

        def usage_snapshot(self) -> dict[str, float]:
            return {
                "request_count": 1,
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
                "cost_usd": 0.02,
                "responses_with_cost": 1,
            }

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
    assert result["model_usage"]["request_count"] == 1
    assert result["model_usage"]["total_tokens"] == 17


def test_reasoner_worker_allows_enterprise_max_tokens(monkeypatch) -> None:
    from ai_test_asset_center.llm_reasoning import ReasoningConfig
    from ai_test_asset_center.stage_reason_all_v2 import _run_reasoner_engine

    captured: dict[str, int] = {}

    class FakeReasoningClient:
        def __init__(self, config: ReasoningConfig) -> None:
            captured["max_tokens"] = config.max_tokens

        def _chat(self, prompt: str, *, system_prompt: str | None = None, call_point: str | None = None) -> str:
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


def test_reasoner_worker_retries_tls_eof_and_emits_secret_free_attempt_events(monkeypatch) -> None:
    import json

    import ai_test_asset_center.llm_reasoning as llm_reasoning
    import ai_test_asset_center.stage_reason_all_v2 as stage
    from ai_test_asset_center.llm_reasoning import ReasoningConfig

    class FakeReasoningClient:
        calls = 0

        def __init__(self, config: ReasoningConfig) -> None:
            self.config = config

        def _chat(self, prompt: str, *, system_prompt: str | None = None, call_point: str | None = None) -> str:
            type(self).calls += 1
            if type(self).calls == 1:
                raise RuntimeError("TLS unexpected EOF while using sk-do-not-persist")
            return '{"choices":[{"message":{"content":"{\\"hypotheses\\":[{\\"title\\":\\"ok\\"}]}"}}]}'

    monkeypatch.setattr(llm_reasoning, "ReasoningClient", FakeReasoningClient)
    monkeypatch.setattr(stage.time, "sleep", lambda _: None)
    result = stage._run_reasoner_engine(
        "smoke",
        "",
        "prompt",
        "system",
        ReasoningConfig(base_url="https://example.invalid/v1", api_key="test", model="test-model"),
        retry_count=1,
        retry_delay_seconds=0,
    )

    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert result["model_attempt_count"] == 2
    assert result["model_response_count"] == 1
    first_event = result["attempt_events"][0]
    assert first_event["attempt"] == 1
    assert first_event["status"] == "failed"
    assert first_event["category"] == "network"
    assert first_event["code"] == "tls_eof"
    assert first_event["retry_scheduled"] is True
    assert first_event["duration_seconds"] >= 0
    assert "sk-do-not-persist" not in json.dumps(result["attempt_events"])


def test_reasoner_worker_retries_response_shape_failure(monkeypatch) -> None:
    import ai_test_asset_center.llm_reasoning as llm_reasoning
    import ai_test_asset_center.stage_reason_all_v2 as stage
    from ai_test_asset_center.llm_reasoning import ReasoningConfig

    class FakeReasoningClient:
        calls = 0

        def __init__(self, config: ReasoningConfig) -> None:
            self.config = config

        def _chat(self, prompt: str, *, system_prompt: str | None = None, call_point: str | None = None) -> str:
            type(self).calls += 1
            if type(self).calls == 1:
                return '{"choices":[{"message":{"content":"{\\"unexpected\\":[]}"}}]}'
            return '{"choices":[{"message":{"content":"{\\"hypotheses\\":[{\\"title\\":\\"ok\\"}]}"}}]}'

    monkeypatch.setattr(llm_reasoning, "ReasoningClient", FakeReasoningClient)
    monkeypatch.setattr(stage.time, "sleep", lambda _: None)
    result = stage._run_reasoner_engine(
        "smoke",
        "",
        "prompt",
        "system",
        ReasoningConfig(base_url="https://example.invalid/v1", api_key="test", model="test-model"),
        retry_count=1,
        retry_delay_seconds=0,
    )

    assert result["status"] == "success"
    assert result["model_attempt_count"] == 2
    assert result["model_response_count"] == 2
    assert result["attempt_events"][0]["category"] == "response_parse"
    assert result["attempt_events"][0]["code"] == "missing_hypotheses_array"
    assert result["attempt_events"][0]["retry_scheduled"] is True


def test_reasoner_worker_does_not_retry_authentication_failure(monkeypatch) -> None:
    import ai_test_asset_center.llm_reasoning as llm_reasoning
    import ai_test_asset_center.stage_reason_all_v2 as stage
    from ai_test_asset_center.llm_reasoning import ReasoningConfig

    class FakeReasoningClient:
        calls = 0

        def __init__(self, config: ReasoningConfig) -> None:
            self.config = config

        def _chat(self, prompt: str, *, system_prompt: str | None = None, call_point: str | None = None) -> str:
            type(self).calls += 1
            raise RuntimeError("HTTP 401 Unauthorized sk-do-not-persist")

    monkeypatch.setattr(llm_reasoning, "ReasoningClient", FakeReasoningClient)
    result = stage._run_reasoner_engine(
        "smoke",
        "",
        "prompt",
        "system",
        ReasoningConfig(base_url="https://example.invalid/v1", api_key="test", model="test-model"),
        retry_count=1,
        retry_delay_seconds=0,
    )

    assert FakeReasoningClient.calls == 1
    assert result["model_attempt_count"] == 1
    assert result["model_response_count"] == 0
    assert result["error_class"] == "authentication_or_authorization"
    assert result["error_code"] == "http_401"
    assert result["attempt_events"][0]["retry_scheduled"] is False


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
            "model_usage": {
                "request_count": 1,
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cost_usd": 0.01,
                "responses_with_cost": 1,
            },
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

    assert any(str(item.get("title") or "") == "causality" for item in hypotheses)
    assert dummy._last_engine_report["max_tokens"] == 100000
    assert dummy._last_engine_report["model_usage"]["request_count"] == 1
    assert dummy._last_engine_report["model_usage"]["total_tokens"] == 14


def test_collect_reasoner_hypotheses_exposes_redacted_failure_classes(monkeypatch) -> None:
    import ai_test_asset_center.stage_reason_all_v2 as stage

    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def fake_stage(host, prd_text, api_spec, reader_output, prior_findings):
        host._last_engine_report = {
            "total_engines": 3,
            "successful_engines": ["invariant"],
            "failed_engines": ["causality"],
            "degraded_engines": ["static_analyzer"],
            "engine_attempts": {"causality": 2, "invariant": 1, "static_analyzer": 1},
            "model_attempt_count": 3,
            "model_response_count": 2,
            "engine_errors": {
                "causality": "LLM network error: TLS handshake failed with sk-sensitive-value"
            },
            "engine_error_codes": {"causality": "tls_error"},
            "engine_error_classes": {"causality": "network"},
            "model_usage": {"request_count": 1, "total_tokens": 9},
        }
        return [{"title": "source-grounded hypothesis"}]

    monkeypatch.setattr(stage, "_stage_reason_all_v2", fake_stage)

    _, meta = stage.collect_reasoner_hypotheses("prd", "api")

    assert meta["failed_engine_names"] == ["causality"]
    assert meta["engine_error_classes"] == {"causality": "network"}
    assert meta["engine_error_class_counts"] == {"network": 1}
    assert meta["engine_error_codes"] == {"causality": "tls_error"}
    assert meta["observed_model_request_count"] == 3
    assert meta["observed_model_response_count"] == 2
    assert "engine_errors" not in meta


def test_reasoner_campaign_aggregate_preserves_all_round_failures_and_usage() -> None:
    from ai_test_asset_center.stage_reason_all_v2 import aggregate_reasoner_round_meta

    aggregate = aggregate_reasoner_round_meta([
        {
            "status": "degraded",
            "observed_model_request_count": 3,
            "observed_model_response_count": 2,
            "model_usage": {
                "request_count": 2,
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost_usd": 0.01,
                "responses_with_cost": 1,
            },
            "successful_engine_names": ["invariant"],
            "failed_engine_names": ["causality"],
            "degraded_engine_names": [],
            "engine_error_classes": {"causality": "network"},
            "engine_error_codes": {"causality": "tls_eof"},
            "engine_error_class_counts": {"network": 1},
            "input": 5,
            "bound": 2,
        },
        {
            "status": "ok",
            "observed_model_request_count": 2,
            "observed_model_response_count": 2,
            "model_usage": {
                "request_count": 2,
                "prompt_tokens": 80,
                "completion_tokens": 15,
                "total_tokens": 95,
                "cost_usd": 0.02,
                "responses_with_cost": 2,
            },
            "successful_engine_names": ["causality", "invariant"],
            "failed_engine_names": [],
            "degraded_engine_names": [],
            "engine_error_classes": {},
            "engine_error_codes": {},
            "engine_error_class_counts": {},
            "input": 7,
            "bound": 4,
        },
    ])

    assert aggregate["status"] == "degraded"
    assert aggregate["round_count"] == 2
    assert aggregate["observed_model_request_count"] == 5
    assert aggregate["observed_model_response_count"] == 4
    assert aggregate["model_usage"] == {
        "request_count": 4,
        "prompt_tokens": 180,
        "completion_tokens": 35,
        "total_tokens": 215,
        "cost_usd": 0.03,
        "responses_with_cost": 3,
    }
    assert aggregate["failed_engine_names"] == ["causality"]
    assert aggregate["failed_engine_round_occurrences"] == 1
    assert aggregate["engine_error_class_counts"] == {"network": 1}
    assert aggregate["engine_error_code_counts"] == {"tls_eof": 1}
    assert aggregate["latest_round"]["status"] == "ok"
    assert aggregate["input"] == 7
    assert aggregate["bound"] == 4
