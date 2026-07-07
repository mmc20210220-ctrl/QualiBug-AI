from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_CREDENTIALS_TSX = ROOT / "frontend" / "src" / "pages" / "ServiceCredentials.tsx"


def test_service_credentials_do_not_mark_saved_secret_as_verified_without_health_check() -> None:
    source = SERVICE_CREDENTIALS_TSX.read_text(encoding="utf-8")

    assert "configured_unverified" in source
    assert "const verified = Boolean(result.auth_check?.all_ok)" in source
    assert "normalizeServiceConfig" in source
    assert "auth.bearer_token_configured" in source
    assert "db.password_configured" in source
    assert "serviceStatusFromRecord" in source
    assert "s.bearer_token || s.api_key || s.admin_user ? 'ok'" not in source
    assert "status: 'ok' as const" not in source
    assert "已配置待验证" in source
