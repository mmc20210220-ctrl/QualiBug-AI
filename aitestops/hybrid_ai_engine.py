from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal

from aitestops.llm_client import LLMClientError, OpenAICompatibleLLMClient
from aitestops.local_ai_engine import LocalAIEngine
from aitestops.prompt_templates import ASSET_GENERATION_PROMPT, SYSTEM_PROMPT
from aitestops.schema_validator import AssetSchemaValidator, SchemaValidationError

EngineMode = Literal["auto", "local", "llm"]


class HybridAIEngine:
    """AI engine with enterprise controls.

    - local: deterministic rules, no API key needed
    - llm: real LLM only, fail if unavailable or invalid
    - auto: try LLM when configured, otherwise fallback to local
    """

    def __init__(self, mode: EngineMode = "auto", audit_dir: Path | None = None):
        self.mode = mode
        self.audit_dir = audit_dir
        self.local_engine = LocalAIEngine()
        self.llm_client = OpenAICompatibleLLMClient()
        self.validator = AssetSchemaValidator()
        self._last_bundle: Dict[str, Any] | None = None
        self.last_engine_used = "unknown"
        self.last_error: str | None = None

    def generate_bundle(self, requirement_text: str) -> Dict[str, Any]:
        if self.mode == "local":
            return self._generate_local(requirement_text)

        if self.mode in {"auto", "llm"}:
            try:
                if not self.llm_client.config.enabled:
                    raise LLMClientError("LLM config is incomplete")
                bundle = self._generate_llm(requirement_text)
                self.last_engine_used = "llm"
                self.last_error = None
                self._last_bundle = bundle
                return bundle
            except (LLMClientError, SchemaValidationError, KeyError, TypeError, ValueError) as exc:
                self.last_error = str(exc)
                if self.mode == "llm":
                    raise
                return self._generate_local(requirement_text)

        raise ValueError(f"Unsupported engine mode: {self.mode}")

    def analyze_requirement(self, requirement_text: str) -> Dict[str, Any]:
        return self.generate_bundle(requirement_text)["analysis"]

    def generate_test_cases(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self._last_bundle:
            return self._last_bundle["test_cases"]
        return self.local_engine.generate_test_cases(analysis)

    def generate_data_profiles(self, test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        if self._last_bundle:
            return self._last_bundle["test_data_profiles"]
        return self.local_engine.generate_data_profiles(test_cases)

    def generate_dsl(self, test_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._last_bundle:
            return self._last_bundle["test_dsl"]
        return self.local_engine.generate_dsl(test_cases)

    def _generate_llm(self, requirement_text: str) -> Dict[str, Any]:
        prompt = ASSET_GENERATION_PROMPT.replace("{requirement_text}", requirement_text)
        bundle = self.llm_client.complete_json(SYSTEM_PROMPT, prompt, self.audit_dir)
        self._validate_bundle(bundle)
        return bundle

    def _generate_local(self, requirement_text: str) -> Dict[str, Any]:
        analysis = self.local_engine.analyze_requirement(requirement_text)
        test_cases = self.local_engine.generate_test_cases(analysis)
        data_profiles = self.local_engine.generate_data_profiles(test_cases)
        dsl_cases = self.local_engine.generate_dsl(test_cases)
        bundle = {
            "analysis": analysis,
            "test_cases": test_cases,
            "test_data_profiles": data_profiles,
            "test_dsl": dsl_cases,
        }
        self._validate_bundle(bundle)
        self.last_engine_used = "local"
        self._last_bundle = bundle
        self._write_fallback_audit(bundle)
        return bundle

    def _validate_bundle(self, bundle: Dict[str, Any]) -> None:
        self.validator.validate_all(
            analysis=bundle["analysis"],
            test_cases=bundle["test_cases"],
            data_profiles=bundle["test_data_profiles"],
            dsl_cases=bundle["test_dsl"],
        )

    def _write_fallback_audit(self, bundle: Dict[str, Any]) -> None:
        if self.audit_dir is None:
            return
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        (self.audit_dir / "local_fallback_bundle.json").write_text(
            json.dumps({"reason": self.last_error, "bundle": bundle}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
