"""Multi-service credential GET/SAVE handlers for PrivatePilotHandler.

Credential masking and encryption-key provisioning are first-class here.
``private_pilot_credentials_patch.install_service_credentials_patch`` only
records runtime-support status for health/compatibility surfaces.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .private_pilot_project_assets import _credential_update_value


class CredentialsHandlerMixin:
    """HTTP handlers for multi-service credential configuration."""

    def _handle_get_service_credentials(self, project: str, root: Path) -> None:
        """Return masked multi-service credential configuration (refs only)."""
        from .private_pilot_credentials_patch import (
            CredentialEncryptionUnavailableError,
            credential_storage_status,
            ensure_local_credential_encryption_key,
            load_services_config,
            mask_service_credentials_for_frontend,
        )

        try:
            key_source = ensure_local_credential_encryption_key(root)
        except CredentialEncryptionUnavailableError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CREDENTIAL_ENCRYPTION_UNAVAILABLE",
                    "message": str(exc),
                    "project": project,
                },
                503,
            )
        try:
            services = load_services_config(root, project)
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CREDENTIAL_CONFIG_INVALID",
                    "message": str(exc),
                    "project": project,
                },
                500,
            )
        return self._json(
            {
                "project": project,
                "services": mask_service_credentials_for_frontend(project, services),
                "credential_storage": credential_storage_status(root, key_source=key_source),
            }
        )

    def _handle_save_service_credentials(self, project: str, root: Path, body: dict) -> None:
        """Save credentials for a single service, merging into multi_service_config.json."""
        from .private_pilot_credentials_patch import (
            CredentialEncryptionUnavailableError,
            ensure_local_credential_encryption_key,
        )

        try:
            ensure_local_credential_encryption_key(root)
        except CredentialEncryptionUnavailableError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CREDENTIAL_ENCRYPTION_UNAVAILABLE",
                    "message": str(exc),
                    "project": project,
                },
                503,
            )

        service_data = body.get("service", {})
        if not isinstance(service_data, dict) or not str(service_data.get("name") or "").strip():
            return self._json({"ok": False, "error": "MISSING_NAME",
                              "message": "service.name is required"}, 400)
        previous_name = str(body.get("previous_name") or "").strip()
        config_path = root / "platform_workspace" / project / "multi_service_config.json"
        try:
            config = _read_json_object(config_path)
            services = config.get("services", [])
            if not isinstance(services, list) or any(not isinstance(item, dict) for item in services):
                raise ValueError(f"credential config services must be a list of objects: {config_path}")
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "CREDENTIAL_CONFIG_INVALID",
                    "message": str(exc),
                    "project": project,
                },
                500,
            )
        config.setdefault("services", [])
        config.setdefault("project_name", project)
        config.setdefault("cross_service_contracts", [])
        config.setdefault("external_integrations", [])

        # Upsert: update existing or append new
        name = str(service_data["name"]).strip()
        role_accounts = service_data.get("role_accounts") or []
        if not isinstance(role_accounts, list) or any(not isinstance(account, dict) for account in role_accounts):
            return self._json(
                {"ok": False, "error": "INVALID_ROLE_ACCOUNTS", "message": "service.role_accounts must be a list of objects"},
                400,
            )
        updated = False
        for i, svc in enumerate(config["services"]):
            if svc.get("name") in {name, previous_name}:
                existing = dict(svc)
                previous_auth = svc.get("auth") if isinstance(svc.get("auth"), dict) else {}
                existing["name"] = name
                existing["base_url"] = service_data.get("base_url", "")
                existing["enabled"] = bool(service_data.get("enabled", True))
                # Build auth section — include all role accounts
                auth = {
                    "type": service_data.get("auth_type", "password_login"),
                    "login_api": service_data.get("login_api", "/auth/login"),
                }
                # Which JSON key carries the identity in the login body. Settable
                # here so an email-authenticating target is configured through the
                # one entry point rather than by hand-editing the config file.
                _identity_field = str(service_data.get("username_field") or "").strip()
                if _identity_field:
                    auth["username_field"] = _identity_field
                elif isinstance(previous_auth.get("username_field"), str) and previous_auth["username_field"]:
                    auth["username_field"] = previous_auth["username_field"]
                # Multi-role accounts (new)
                for ra in role_accounts:
                    if isinstance(ra, dict) and ra.get("role") and ra.get("username"):
                        role = str(ra["role"])
                        previous_role = previous_auth.get(role) if isinstance(previous_auth.get(role), dict) else {}
                        password = _credential_update_value(ra.get("password"), previous_role.get("password"))
                        auth[role] = {"username": ra["username"]}
                        if password:
                            auth[role]["password"] = password
                # Legacy single admin (backward compat)
                if not role_accounts:
                    if service_data.get("admin_user"):
                        auth.setdefault("admin", {})
                        auth["admin"]["username"] = service_data["admin_user"]
                    previous_admin = previous_auth.get("admin") if isinstance(previous_auth.get("admin"), dict) else {}
                    admin_password = _credential_update_value(service_data.get("admin_pass"), previous_admin.get("password"))
                    if admin_password:
                        auth.setdefault("admin", {})
                        auth["admin"]["password"] = admin_password
                bearer_token = _credential_update_value(service_data.get("bearer_token"), previous_auth.get("bearer_token"))
                if bearer_token:
                    auth["bearer_token"] = bearer_token
                api_key = _credential_update_value(service_data.get("api_key"), previous_auth.get("api_key"))
                if api_key:
                    auth["api_key"] = api_key
                existing["auth"] = auth
                for legacy_key in ("login_api", "auth_type", "admin_user", "admin_pass", "bearer_token", "api_key"):
                    existing.pop(legacy_key, None)

                # Build db section
                if any(service_data.get(k) for k in ("db_host", "db_name")):
                    previous_db = svc.get("db") if isinstance(svc.get("db"), dict) else {}
                    existing["db"] = {
                        "host": service_data.get("db_host", ""),
                        "port": int(service_data.get("db_port", 3306)),
                        "name": service_data.get("db_name", ""),
                        "user": service_data.get("db_user", ""),
                        "password": _credential_update_value(service_data.get("db_pass"), previous_db.get("password")),
                    }
                else:
                    existing.pop("db", None)
                config["services"][i] = existing
                updated = True
                break

        if not updated:
            auth = {
                "type": service_data.get("auth_type", "password_login"),
                "login_api": service_data.get("login_api", "/auth/login"),
            }
            _identity_field = str(service_data.get("username_field") or "").strip()
            if _identity_field:
                auth["username_field"] = _identity_field
            # Multi-role accounts
            for ra in role_accounts:
                if isinstance(ra, dict) and ra.get("role") and ra.get("username"):
                    role = str(ra["role"])
                    auth[role] = {"username": ra["username"]}
                    password = _credential_update_value(ra.get("password"))
                    if password:
                        auth[role]["password"] = password
            # Legacy single admin fallback
            if not role_accounts and service_data.get("admin_user"):
                auth["admin"] = {
                    "username": service_data["admin_user"],
                    "password": service_data.get("admin_pass", ""),
                }
            bearer_token = _credential_update_value(service_data.get("bearer_token"))
            if bearer_token:
                auth["bearer_token"] = bearer_token
            api_key = _credential_update_value(service_data.get("api_key"))
            if api_key:
                auth["api_key"] = api_key
            svc = {
                "name": name, "base_url": service_data.get("base_url", ""),
                "enabled": service_data.get("enabled", True),
                "description": "", "depends_on": [], "exposes_to": [],
                "auth": auth,
            }
            if any(service_data.get(k) for k in ("db_host", "db_name")):
                svc["db"] = {
                    "host": service_data.get("db_host", ""),
                    "port": int(service_data.get("db_port", 3306)),
                    "name": service_data.get("db_name", ""),
                    "user": service_data.get("db_user", ""),
                    "password": _credential_update_value(service_data.get("db_pass")),
                }
            config["services"].append(svc)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Encrypt sensitive credential fields before writing to disk so that
        # secrets are not stored in plaintext in multi_service_config.json.
        from .credential_crypto import encrypt as _enc_secret, is_encrypted as _is_enc
        for _svc in config.get("services", []):
            if not isinstance(_svc, dict):
                continue
            _auth = _svc.get("auth")
            if isinstance(_auth, dict):
                for _role_cfg in _auth.values():
                    if isinstance(_role_cfg, dict):
                        _pw = _role_cfg.get("password")
                        if _pw and not _is_enc(_pw):
                            _role_cfg["password"] = _enc_secret(_pw)
                for _field in ("bearer_token", "api_key"):
                    _val = _auth.get(_field)
                    if _val and not _is_enc(_val):
                        _auth[_field] = _enc_secret(_val)
            _db_cfg = _svc.get("db")
            if isinstance(_db_cfg, dict):
                _db_pw = _db_cfg.get("password")
                if _db_pw and not _is_enc(_db_pw):
                    _db_cfg["password"] = _enc_secret(_db_pw)
        _write_json_object_atomic(config_path, config)

        # Reload credentials and perform a real password-login health check.
        # Static bearer/API-key configuration remains unverified because the
        # credential manager cannot prove it against a protected endpoint.
        try:
            from .enterprise_credential_manager import EnterpriseCredentialManager
            mgr = EnterpriseCredentialManager(project, root)
            mgr.load_from_file(config_path)
            mgr.load_from_env()
            login_results = mgr.login_all_services()
            if not isinstance(login_results, dict):
                raise TypeError("credential login results must be an object")
            auth_roles = login_results.get(name) or {}
            if not isinstance(auth_roles, dict) or any(type(ok) is not bool for ok in auth_roles.values()):
                raise ValueError("credential role health results must be boolean values")
            target_service = next(
                (item for item in config["services"] if isinstance(item, dict) and item.get("name") == name),
                None,
            )
            if target_service is None:
                raise ValueError(f"saved service missing from credential config: {name}")
            target_auth = target_service.get("auth") if isinstance(target_service.get("auth"), dict) else {}
            role_credentials = [
                value
                for value in target_auth.values()
                if isinstance(value, dict) and (value.get("username") or value.get("password"))
            ]
            static_token_only = bool(target_auth.get("bearer_token") or target_auth.get("api_key")) and not role_credentials
            verified = bool(auth_roles) and all(ok is True for ok in auth_roles.values()) and not static_token_only
            auth_check = {
                "service": name,
                "roles": auth_roles,
                "all_ok": verified,
                "status": "verified" if verified else "configured_unverified" if static_token_only else "failed",
            }
            if static_token_only:
                auth_check["reason"] = "static credential configured without a live protected-endpoint health check"
        except Exception as exc:
            auth_check = {
                "service": name,
                "roles": {},
                "all_ok": False,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        target_service = next(
            (item for item in config["services"] if isinstance(item, dict) and item.get("name") == name),
            None,
        )
        if target_service is None:
            raise ValueError(f"saved service missing from credential config: {name}")
        target_service["auth_check"] = auth_check
        _write_json_object_atomic(config_path, config)

        if not auth_check["all_ok"]:
            _write_json_object_atomic(
                root / "platform_outputs" / project / "credential_verification_last_error.json",
                {
                    "schema": "qualibug.credential-verification-failure.v1",
                    "project": project,
                    "service": name,
                    "status": auth_check["status"],
                    "error_type": auth_check.get("error_type", ""),
                    "error": auth_check.get("error") or auth_check.get("reason") or "credential health check failed",
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            return self._json({
                "ok": False,
                "saved": True,
                "error": "CREDENTIAL_VERIFICATION_FAILED",
                "service": name,
                "services_count": len(config["services"]),
                "auth_check": auth_check,
            }, 207)

        return self._json({
            "ok": True,
            "saved": True,
            "service": name,
            "services_count": len(config["services"]),
            "auth_check": auth_check,
        })
