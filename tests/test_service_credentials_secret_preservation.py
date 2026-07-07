import importlib


def test_masked_frontend_secret_values_preserve_existing_credentials(monkeypatch):
    monkeypatch.setenv("QUALIBUG_JWT_SECRET", "dev-mode-only")
    service = importlib.import_module("ai_test_asset_center.private_pilot_service")

    assert service._credential_update_value("********", "encrypted-existing-secret") == "encrypted-existing-secret"
    assert service._credential_update_value("", "encrypted-existing-secret") == "encrypted-existing-secret"
    assert service._credential_update_value("new-secret", "encrypted-existing-secret") == "new-secret"
