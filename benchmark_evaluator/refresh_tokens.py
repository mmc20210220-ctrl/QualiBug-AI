"""Refresh declared non-production test-account tokens from an explicit profile."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for token refresh")
    return value


def load_refresh_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if env is None else env
    base_url = _required(env, "QUALIBUG_TARGET_BASE_URL").rstrip("/")
    login_path = _required(env, "QUALIBUG_LOGIN_PATH")
    source = Path(_required(env, "QUALIBUG_TEST_ACCOUNTS_SOURCE")).resolve()
    output = Path(_required(env, "QUALIBUG_TEST_ACCOUNTS_PATH")).resolve()
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("QUALIBUG_TARGET_BASE_URL must be an absolute HTTP(S) URL")
    if not login_path.startswith("/"):
        raise RuntimeError("QUALIBUG_LOGIN_PATH must be an absolute target path")
    if not source.is_file():
        raise FileNotFoundError(f"test account source missing: {source}")
    return {
        "base_url": base_url,
        "login_path": login_path,
        "source": source,
        "output": output,
    }


def load_declared_accounts(path: Path) -> dict[str, dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("test account JSON must be an object")
        return {
            str(key): dict(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }
    accounts: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "|" not in line or re.fullmatch(r"[|\s:-]+", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "@" not in cells[1]:
            continue
        role, email, password = cells[:3]
        note = cells[3] if len(cells) > 3 else ""
        status = "DISABLED" if "DISABLED" in " ".join(cells).upper() else "ACTIVE"
        key = re.sub(r"[^a-z0-9_.-]+", "_", role.lower()).strip("_") or email
        accounts[key] = {
            "role": role,
            "email": email,
            "password": password,
            "status": status,
            "note": note,
        }
    if not accounts:
        raise ValueError(f"no declared accounts parsed from {path}")
    return accounts


def _login_identity(payload: Any) -> tuple[str, str]:
    """Extract the authenticated principal identity from common response envelopes."""

    queue: list[tuple[Any, int]] = [(payload, 0)]
    principal_keys = {"user", "account", "principal", "profile", "identity", "data", "result"}
    while queue:
        value, depth = queue.pop(0)
        if depth > 5 or not isinstance(value, dict):
            continue
        role = str(
            value.get("role")
            or value.get("role_id")
            or value.get("roleId")
            or ""
        ).strip()
        status = str(
            value.get("status")
            or value.get("account_status")
            or value.get("accountStatus")
            or ""
        ).strip()
        if role:
            return role, status
        for key, child in value.items():
            if str(key).strip().lower() in principal_keys and isinstance(child, dict):
                queue.append((child, depth + 1))
    return "", ""


def _login_token(payload: Any) -> str:
    queue: list[tuple[Any, int]] = [(payload, 0)]
    envelope_keys = {"auth", "data", "result", "session"}
    token_keys = {"token", "access_token", "accesstoken", "jwt", "id_token"}
    while queue:
        value, depth = queue.pop(0)
        if depth > 4 or not isinstance(value, dict):
            continue
        for key, child in value.items():
            key_l = str(key).strip().lower()
            if key_l in token_keys and isinstance(child, str) and child.strip():
                return child.strip()
            if key_l in envelope_keys and isinstance(child, dict):
                queue.append((child, depth + 1))
    return ""


def login(base_url: str, login_path: str, email: str, password: str) -> dict[str, Any]:
    body = json.dumps({"email": email, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        urljoin(base_url + "/", login_path.lstrip("/")),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            role, principal_status = _login_identity(payload)
            return {
                "token": _login_token(payload),
                "http_status": int(response.status),
                "principal_role": role,
                "principal_status": principal_status,
            }
    except urllib.error.HTTPError as exc:
        return {
            "token": "",
            "http_status": int(exc.code),
            "principal_role": "",
            "principal_status": "",
        }
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"test account login transport failed for {urlsplit(base_url).hostname}:"
            f"{type(exc).__name__}"
        ) from exc


def refresh_tokens(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    config = load_refresh_config(env)
    accounts = load_declared_accounts(config["source"])
    refreshed = 0
    active_failures: list[str] = []
    for name, account in accounts.items():
        observation = login(
            config["base_url"],
            config["login_path"],
            str(account.get("email") or ""),
            str(account.get("password") or ""),
        )
        if not isinstance(observation, dict):
            raise TypeError("login observation must be an object")
        token = str(observation.get("token") or "")
        status = int(observation.get("http_status") or 0)
        principal_role = str(observation.get("principal_role") or "").strip()
        principal_status = str(observation.get("principal_status") or "").strip()
        account["token"] = token
        if principal_role:
            account["authenticated_role"] = principal_role
        if principal_status:
            account["authenticated_status"] = principal_status
        if principal_role or principal_status:
            account["identity_observation_source"] = "login_response"
        if token:
            refreshed += 1
            print(f"{name:24s} refreshed HTTP {status} token_len={len(token)}")
        elif str(account.get("status") or "").upper() != "DISABLED":
            active_failures.append(f"{name}:HTTP_{status}")
            print(f"{name:24s} login failed HTTP {status}")
        else:
            print(f"{name:24s} disabled account rejected HTTP {status}")
    if active_failures:
        print(f"WARNING: active test account token refresh failed: {','.join(active_failures)}")
        # Non-fatal: continue with the tokens we have for other accounts
    config["output"].parent.mkdir(parents=True, exist_ok=True)
    config["output"].write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"REFRESHED {refreshed}/{len(accounts)} tokens -> {config['output']}")
    return {"refreshed": refreshed, "total": len(accounts), "output": str(config["output"])}


if __name__ == "__main__":
    refresh_tokens()
