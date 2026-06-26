from __future__ import annotations

from typing import Any, Dict, List


class SchemaValidationError(ValueError):
    pass


class AssetSchemaValidator:
    """Strict-enough schema checks for AI-generated testing assets.

    Enterprise principle: do not let model text directly become executable code.
    The LLM must produce structured assets, and those assets must pass validation.
    """

    VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
    VALID_TYPES = {"api", "ui", "security", "contract", "data", "permission", "regression"}
    VALID_ACTIONS = {"create_user", "login", "login_wrong_password", "check_access"}
    VALID_OPERATORS = {"equals"}

    def validate_analysis(self, analysis: Dict[str, Any]) -> None:
        self._require_dict(analysis, "analysis")
        self._require_list(analysis.get("business_rules"), "analysis.business_rules")
        risks = self._require_list(analysis.get("risks"), "analysis.risks")
        for index, risk in enumerate(risks):
            path = f"analysis.risks[{index}]"
            self._require_dict(risk, path)
            self._require_str(risk.get("risk_id"), f"{path}.risk_id")
            self._require_str(risk.get("risk"), f"{path}.risk")
            self._require_priority(risk.get("priority"), f"{path}.priority")
            self._require_str(risk.get("reason"), f"{path}.reason")
            self._require_list(risk.get("recommended_test_type"), f"{path}.recommended_test_type")

    def validate_test_cases(self, test_cases: List[Dict[str, Any]]) -> None:
        cases = self._require_list(test_cases, "test_cases")
        for index, case in enumerate(cases):
            path = f"test_cases[{index}]"
            self._require_dict(case, path)
            self._require_str(case.get("case_id"), f"{path}.case_id")
            self._require_str(case.get("title"), f"{path}.title")
            self._require_priority(case.get("priority"), f"{path}.priority")
            self._require_type(case.get("type"), f"{path}.type")
            self._require_str(case.get("data_profile"), f"{path}.data_profile")
            self._require_list(case.get("steps"), f"{path}.steps")
            self._require_list(case.get("expected"), f"{path}.expected")
            if "automation_candidate" in case and not isinstance(case["automation_candidate"], bool):
                raise SchemaValidationError(f"{path}.automation_candidate must be boolean")

    def validate_data_profiles(self, data_profiles: Dict[str, Any]) -> None:
        profiles = self._require_dict(data_profiles, "test_data_profiles")
        for name, profile in profiles.items():
            path = f"test_data_profiles.{name}"
            self._require_dict(profile, path)
            self._require_str(profile.get("entity"), f"{path}.entity")
            self._require_str(profile.get("create_strategy"), f"{path}.create_strategy")
            cleanup = profile.get("cleanup")
            if cleanup not in {"auto", "manual", "none"}:
                raise SchemaValidationError(f"{path}.cleanup must be one of auto/manual/none")
            if profile.get("privacy_level") != "synthetic_only":
                raise SchemaValidationError(f"{path}.privacy_level must be synthetic_only")

    def validate_dsl(self, dsl_cases: List[Dict[str, Any]]) -> None:
        cases = self._require_list(dsl_cases, "test_dsl")
        for index, case in enumerate(cases):
            path = f"test_dsl[{index}]"
            self._require_dict(case, path)
            self._require_str(case.get("case_id"), f"{path}.case_id")
            self._require_str(case.get("title"), f"{path}.title")
            self._require_type(case.get("type"), f"{path}.type")
            self._require_priority(case.get("priority"), f"{path}.priority")
            self._require_str(case.get("data_profile"), f"{path}.data_profile")
            actions = self._require_list(case.get("actions"), f"{path}.actions")
            assertions = self._require_list(case.get("assertions"), f"{path}.assertions")

            for action_index, action in enumerate(actions):
                action_path = f"{path}.actions[{action_index}]"
                self._require_dict(action, action_path)
                action_type = self._require_str(action.get("action"), f"{action_path}.action")
                if action_type not in self.VALID_ACTIONS:
                    raise SchemaValidationError(f"{action_path}.action unsupported: {action_type}")
                if "as" in action:
                    self._require_str(action.get("as"), f"{action_path}.as")

            for assertion_index, assertion in enumerate(assertions):
                assertion_path = f"{path}.assertions[{assertion_index}]"
                self._require_dict(assertion, assertion_path)
                self._require_str(assertion.get("target"), f"{assertion_path}.target")
                operator = self._require_str(assertion.get("operator"), f"{assertion_path}.operator")
                if operator not in self.VALID_OPERATORS:
                    raise SchemaValidationError(f"{assertion_path}.operator unsupported: {operator}")
                if "value" not in assertion:
                    raise SchemaValidationError(f"{assertion_path}.value is required")

    def validate_all(self, analysis: Dict[str, Any], test_cases: List[Dict[str, Any]], data_profiles: Dict[str, Any], dsl_cases: List[Dict[str, Any]]) -> None:
        self.validate_analysis(analysis)
        self.validate_test_cases(test_cases)
        self.validate_data_profiles(data_profiles)
        self.validate_dsl(dsl_cases)

        profile_names = set(data_profiles.keys())
        for case in test_cases:
            if case["data_profile"] not in profile_names:
                raise SchemaValidationError(f"test case {case['case_id']} references missing data profile {case['data_profile']}")
        for case in dsl_cases:
            if case["data_profile"] not in profile_names:
                raise SchemaValidationError(f"DSL case {case['case_id']} references missing data profile {case['data_profile']}")

    def _require_dict(self, value: Any, path: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise SchemaValidationError(f"{path} must be object")
        return value

    def _require_list(self, value: Any, path: str) -> List[Any]:
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path} must be array")
        return value

    def _require_str(self, value: Any, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SchemaValidationError(f"{path} must be non-empty string")
        return value

    def _require_priority(self, value: Any, path: str) -> str:
        value = self._require_str(value, path)
        if value not in self.VALID_PRIORITIES:
            raise SchemaValidationError(f"{path} must be one of {sorted(self.VALID_PRIORITIES)}")
        return value

    def _require_type(self, value: Any, path: str) -> str:
        value = self._require_str(value, path)
        if value not in self.VALID_TYPES:
            raise SchemaValidationError(f"{path} unsupported type: {value}")
        return value
