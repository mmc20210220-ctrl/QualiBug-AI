from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine


def test_execute_verification_invokes_real_http_probe_for_each_role():
    engine = object.__new__(AutonomousDiscoveryEngine)
    engine.base = "http://example.test/api"
    engine._http_timeout = 1
    engine._production_blocked = False
    engine._service_tokens = {}
    engine._credential_manager = None
    engine._tokens = {}
    engine._har_entries = []
    engine._har_error_patterns = []
    engine._MAX_HAR_ENTRIES = 100

    observed = []

    def fake_http(method, path, data=None, no_auth=False, role="admin", service=""):
        observed.append({
            "method": method,
            "path": path,
            "no_auth": no_auth,
            "role": role,
            "data": data,
            "service": service,
        })
        return {
            "_http": 200,
            "_request": {"method": method, "path": path, "role": role, "no_auth": no_auth},
            "ok": True,
        }

    engine._http = fake_http

    route_map = {
        "GET /api/orders/{order_id}": {
            "method": "GET",
            "path_pattern": "/api/orders/{order_id}",
            "path_params": ["order_id"],
            "path_param_formats": {},
        }
    }
    engine._fetch_real_id = lambda param_name, resolved, route_map: "42"

    evidence = engine._execute_verification(
        {"step1": "GET /api/orders/{order_id}", "check": "order can be fetched"},
        route_map=route_map,
    )

    assert evidence["total_calls"] == 1
    assert evidence["route_map_used"] is True
    assert evidence["calls"][0]["call"] == "GET /api/orders/42"
    assert evidence["calls"][0]["resolved"] is True
    assert evidence["calls"][0]["synthetic_id"] is False

    assert observed == [
        {"method": "GET", "path": "/api/orders/42", "no_auth": False, "role": "admin", "data": None, "service": ""},
        {"method": "GET", "path": "/api/orders/42", "no_auth": False, "role": "viewer", "data": None, "service": ""},
        {"method": "GET", "path": "/api/orders/42", "no_auth": True, "role": "admin", "data": None, "service": ""},
    ]
