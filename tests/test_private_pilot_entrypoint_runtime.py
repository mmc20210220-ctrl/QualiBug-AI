from __future__ import annotations


def test_entrypoint_run_server_starts_private_pilot_service(monkeypatch) -> None:
    from ai_test_asset_center import private_pilot_entrypoint as entrypoint

    calls: list[str] = []

    class DummyServer:
        def serve_forever(self) -> None:
            calls.append("serve_forever")

        def server_close(self) -> None:
            calls.append("server_close")

    monkeypatch.setattr(entrypoint, "install_runtime_components", lambda: calls.append("install_runtime_components"))
    monkeypatch.setattr(
        entrypoint._service,
        "run_private_pilot_service",
        lambda root=None, host=None, port=None: calls.append("run_private_pilot_service") or DummyServer(),
    )

    entrypoint.run_server()

    assert calls == [
        "install_runtime_components",
        "run_private_pilot_service",
        "serve_forever",
        "server_close",
    ]
