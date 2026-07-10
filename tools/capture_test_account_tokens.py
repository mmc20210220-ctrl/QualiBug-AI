from __future__ import annotations

"""Authenticate declared non-production test accounts into a local token artifact."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_test_asset_center.sandbox_write_executor import is_test_or_sandbox_environment  # noqa: E402


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            raise RuntimeError(f"login response missing token path: {path}")
        current = current[part]
    return current


def capture_tokens(
    *,
    config_path: Path,
    output_path: Path,
    base_url: str,
    environment_type: str,
) -> dict[str, Any]:
    if not is_test_or_sandbox_environment(environment_type):
        raise RuntimeError(f"token capture requires explicit non-production environment: {environment_type!r}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError("account config must be an object")
    auth = config.get("auth_flow") if isinstance(config.get("auth_flow"), dict) else {}
    accounts = config.get("accounts") if isinstance(config.get("accounts"), dict) else {}
    login_path = str(auth.get("login_path") or "").strip()
    token_path = str(auth.get("token_json_path") or "").strip()
    if not login_path.startswith("/") or not token_path or not accounts:
        raise RuntimeError("account config requires auth_flow.login_path, token_json_path, and accounts")
    captured: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for name, raw in accounts.items():
        account = raw if isinstance(raw, dict) else {}
        username = str(account.get("username") or account.get("email") or "").strip()
        password = str(account.get("password") or "")
        if not username or not password:
            failures.append({"account": str(name), "reason": "credentials_missing"})
            continue
        body = {
            "username": username,
            "email": username,
            "password": password,
        }
        tenant_field = str(auth.get("tenant_field") or "").strip()
        if tenant_field and account.get("tenant_id") not in (None, ""):
            body[tenant_field] = account["tenant_id"]
        request = urllib.request.Request(
            base_url.rstrip("/") + login_path,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            token = str(_json_path(payload, token_path) or "").strip()
            if not token:
                raise RuntimeError("empty token")
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
            failures.append({"account": str(name), "reason": f"{type(exc).__name__}:{str(exc)[:160]}"})
            continue
        captured[str(name)] = {
            "role": str(account.get("role") or name),
            "status": str(account.get("status") or "ACTIVE"),
            "tenant_id": str(account.get("tenant_id") or ""),
            "token": token,
        }
    if failures or len(captured) != len(accounts):
        raise RuntimeError(
            f"test account token capture incomplete: captured={len(captured)} expected={len(accounts)} failures={failures}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output_path)
    return {"output_path": str(output_path), "captured_account_count": len(captured)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--environment-type", required=True)
    args = parser.parse_args()
    result = capture_tokens(
        config_path=Path(args.config).resolve(),
        output_path=Path(args.output).resolve(),
        base_url=args.base_url,
        environment_type=args.environment_type,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
