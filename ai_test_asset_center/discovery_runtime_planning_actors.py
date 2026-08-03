"""Planning-stage actor and API-operation projection.

Extracted from ``discovery_runtime_planning`` to restore that module's
architecture budget. Symbols are re-exported from ``discovery_runtime_planning``
so existing import paths and symbol identity stay stable.

Both projections stay source-bound: actor rows come from the declared test
account catalog, the configured credential authority, or the registered
TEST_ACCOUNTS corpus; API operations come from the universal API parser over
submitted source text. Nothing is invented from display labels.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .credential_crypto import CredentialDecryptionError
from .discovery_mainline_contract import MainlineContractError
from .experiment_runtime_support import (
    _parse_test_accounts_md,
    configured_runtime_accounts,
)


_planning_logger = logging.getLogger("qualibug.discovery_planning")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _api_operations(
    api_spec_text: str,
    *,
    submitted_source_text: str = "",
) -> list[dict[str, Any]]:
    from .universal_api_parser import build_api_operations_from_text

    try:
        return build_api_operations_from_text(
            api_spec_text,
            submitted_source_text=submitted_source_text,
        )
    except ValueError as exc:
        raise MainlineContractError(str(exc)) from exc


def _runtime_actors(root: Path, project: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    accounts_path = root / "platform_inputs" / project / "test_accounts.json"
    payload: Any = {}
    if accounts_path.exists():
        try:
            payload = json.loads(accounts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MainlineContractError(
                f"test_actor_catalog_invalid:{type(exc).__name__}"
            ) from exc
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        raw_rows = payload.get("accounts") or payload.get("actors") or payload.get("users")
        if raw_rows is None:
            rows = [
                {**value, "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict)
                and key not in {"schema", "schema_version", "meta"}
            ]
        elif isinstance(raw_rows, list):
            rows = raw_rows
        else:
            raise MainlineContractError("test_actor_catalog_rows_invalid")
    else:
        raise MainlineContractError("test_actor_catalog_root_invalid")
    if not rows:
        # Credentials saved through the enterprise settings route are the
        # authoritative role-to-login binding when no legacy JSON catalog is
        # present. This keeps the account identity exact and avoids translating
        # display labels from TEST_ACCOUNTS.md into source role identities.
        try:
            rows = configured_runtime_accounts(root, project)
        except CredentialDecryptionError as exc:
            # A project may carry an encrypted credential snapshot produced by a
            # different control-plane key.  Do not turn a usable, source-declared
            # TEST_ACCOUNTS corpus into an anonymous run, but also do not hide a
            # credential failure: the fallback is allowed only when it has an
            # exact role, login identity, and password from the source corpus.
            _planning_logger.error(
                "runtime_credential_config_decryption_failed project=%s "
                "source_fallback=registered_test_data_or_TEST_ACCOUNTS.md "
                "error_type=%s",
                project,
                type(exc).__name__,
                exc_info=True,
            )
            source_rows = _parse_test_accounts_md(root, project)
            rows = [
                row
                for row in source_rows
                if isinstance(row, dict)
                and _text(row.get("role") or row.get("name") or row.get("id"))
                and _text(
                    row.get("email")
                    or row.get("username")
                    or row.get("account")
                    or row.get("mobile")
                    or row.get("phone")
                )
                and _text(row.get("password") or row.get("pass"))
            ]
            if not rows:
                raise
            context["runtime_credential_resolution"] = {
                "status": "source_backed_fallback",
                "configured_status": "decryption_failed",
                "source": "registered_test_data_or_TEST_ACCOUNTS.md",
                "error_type": type(exc).__name__,
                "account_count": len(rows),
            }
    if not rows:
        # Keep the existing Markdown parser as a source-backed fallback for
        # projects that have not configured a service credential manager. A
        # localized display label remains exactly as declared; it is never
        # guessed to be an English role.
        rows = _parse_test_accounts_md(root, project)
    for row in rows:
        if not isinstance(row, dict):
            raise MainlineContractError("test_actor_catalog_row_invalid")
        # Prefer the role observed from the authenticated identity over a
        # display/localized role label.  Permission relations are keyed by the
        # source role identity; using only a translated display label severs
        # the source-permitted actor -> runtime credential lineage and leaves
        # otherwise executable obligations blocked on a missing actor.
        role = _text(
            row.get("authenticated_role")
            or row.get("role")
            or row.get("name")
            or row.get("id")
        )
        if not role:
            raise MainlineContractError("test_actor_role_missing")
        account_ref = _text(
            row.get("account_ref")
            or row.get("email")
            or row.get("username")
            or row.get("id")
            or role
        )
        actors.append({
            "role": role,
            "account_ref": account_ref,
            "tenant": row.get("tenant") or row.get("scope"),
            "secret_ref": f"secret_ref:test_accounts:{account_ref}",
            "status": _text(row.get("status") or "active"),
        })
    scenario_actor = _dict(_dict(context.get("runtime_scenario_contract")).get("actor"))
    declared_role = _text(
        scenario_actor.get("role")
        or scenario_actor.get("name")
        or scenario_actor.get("id")
    )
    if declared_role and not any(_text(row.get("role")) == declared_role for row in actors):
        actors.append({
            "role": declared_role,
            "secret_ref": f"secret_ref:context:{declared_role}",
            "status": "active",
        })
    return actors
