from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple


class AssetSemanticGuard:
    """Normalize and validate AI-generated test DSL at the semantic layer.

    JSON Schema can only prove the model returned fields with the right shapes.
    It cannot prove that the model used aliases, resources, and assertion targets
    supported by the execution engine. This guard handles common LLM variations
    before code generation.
    """

    USER_ALIAS_SYNONYMS = {
        "active_user",
        "normal_user",
        "active_normal_user",
        "locked",
        "locked_user",
        "locked_normal_user",
        "user_profile",
        "test_user",
    }

    ACCESS_BOOL_SUFFIXES = {"allowed", "is_allowed", "can_access", "success", "result", "value"}
    LOCKED_INDICATORS = {"locked", "is_locked", "account_locked", "locked_status"}

    def normalize_dsl(self, data_profiles: Dict[str, Any], dsl_cases: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
        normalized = deepcopy(dsl_cases)
        notes: List[str] = []

        for case in normalized:
            case_id = case.get("case_id", "UNKNOWN")
            actions = case.get("actions", [])
            assertions = case.get("assertions", [])
            data_profile_name = case.get("data_profile")
            profile = data_profiles.get(data_profile_name, {}) if isinstance(data_profiles, dict) else {}

            known_aliases = {a.get("as") for a in actions if isinstance(a, dict) and a.get("as")}
            first_user_alias = self._find_first_user_alias(actions) or "user"

            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_type = action.get("action")

                if action_type == "create_user":
                    # Fill missing values from the data profile. This makes the DSL
                    # less brittle when the LLM only names a profile.
                    if "role" not in action and profile.get("role"):
                        action["role"] = profile["role"]
                        notes.append(f"{case_id}: filled create_user.role from data_profile")
                    if "status" not in action and profile.get("status"):
                        action["status"] = profile["status"]
                        notes.append(f"{case_id}: filled create_user.status from data_profile")
                    first_user_alias = action.get("as") or first_user_alias
                    known_aliases.add(first_user_alias)

                if action_type in {"login", "check_access"}:
                    for key in ("username", "password"):
                        if key in action:
                            new_value, changed = self._normalize_template_alias(str(action[key]), known_aliases, first_user_alias)
                            if changed:
                                action[key] = new_value
                                notes.append(f"{case_id}: normalized template alias in {action_type}.{key}")

                if action_type == "login_wrong_password":
                    # The renderer uses user_alias for this action. If the model did
                    # not provide it, use the first created user.
                    action.setdefault("user_alias", first_user_alias)

                if action_type == "check_access" and "resource" in action:
                    original = str(action["resource"])
                    action["resource"] = self._normalize_resource(original)
                    if action["resource"] != original:
                        notes.append(f"{case_id}: normalized resource {original!r} -> {action['resource']!r}")

            for assertion in assertions:
                if not isinstance(assertion, dict):
                    continue
                target = str(assertion.get("target", ""))
                value = assertion.get("value")
                new_target, new_value, changed = self._normalize_assertion(target, value, known_aliases, first_user_alias)
                if changed:
                    assertion["target"] = new_target
                    assertion["value"] = new_value
                    notes.append(f"{case_id}: normalized assertion {target!r} -> {new_target!r}")

        return normalized, notes

    def _find_first_user_alias(self, actions: List[Dict[str, Any]]) -> str | None:
        for action in actions:
            if isinstance(action, dict) and action.get("action") == "create_user" and action.get("as"):
                return str(action["as"])
        return None

    def _normalize_template_alias(self, value: str, known_aliases: set[str], default_alias: str) -> tuple[str, bool]:
        if not (value.startswith("{{") and value.endswith("}}")):
            return value, False
        path = value[2:-2].strip()
        if "." not in path:
            return value, False
        root, rest = path.split(".", 1)
        if root in known_aliases:
            return value, False
        if root in self.USER_ALIAS_SYNONYMS:
            return "{{" + default_alias + "." + rest + "}}", True
        return value, False

    def _normalize_resource(self, resource: str) -> str:
        lowered = resource.strip().lower()
        if "admin" in lowered or "管理" in lowered:
            return "admin_page"
        return resource

    def _normalize_assertion(self, target: str, value: Any, known_aliases: set[str], default_alias: str) -> tuple[str, Any, bool]:
        changed = False
        new_target = target
        new_value = value

        # Boolean access results are returned directly by AuthService.can_access.
        # Models often write access_result.allowed; normalize it to access_result.
        if "." in new_target:
            root, suffix = new_target.split(".", 1)
            if root == "access_result" and suffix in self.ACCESS_BOOL_SUFFIXES:
                new_target = "access_result"
                changed = True

        # Model may assert locked == true. Convert it to the executable login result.
        normalized_tail = new_target.split(".")[-1]
        if normalized_tail in self.LOCKED_INDICATORS and value is True:
            if "login_after_locked" in known_aliases:
                new_target = "login_after_locked.error_code"
                new_value = "ACCOUNT_LOCKED"
            elif "last_wrong_result" in known_aliases:
                new_target = "last_wrong_result.error_code"
                new_value = "ACCOUNT_LOCKED"
            else:
                new_target = "user.status"
                new_value = "locked"
            changed = True

        # Models sometimes assert user.status == locked after wrong-password flow.
        # In real systems this should come from a fresh query/status API. The demo
        # AuthService returns a user snapshot, so the execution generator syncs this
        # snapshot after ACCOUNT_LOCKED. We still normalize the assertion to the
        # authoritative lock result when possible.
        if new_target.endswith(".status") and value == "locked":
            if "last_wrong_result" in known_aliases:
                new_target = "last_wrong_result.error_code"
                new_value = "ACCOUNT_LOCKED"
                changed = True
            elif "login_after_locked" in known_aliases:
                new_target = "login_after_locked.error_code"
                new_value = "ACCOUNT_LOCKED"
                changed = True

        # Normalize missing root aliases such as locked.username -> user.username.
        if "." in new_target:
            root, rest = new_target.split(".", 1)
            if root not in known_aliases and root in self.USER_ALIAS_SYNONYMS:
                new_target = default_alias + "." + rest
                changed = True

        return new_target, new_value, changed
