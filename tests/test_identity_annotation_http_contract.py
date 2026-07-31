from ai_test_asset_center.private_pilot_identity_benchmark_handlers import _route


def test_annotation_package_route_is_project_scoped() -> None:
    assert _route("/api/v1/projects/demo/identity-benchmark/annotation-package") == (
        "demo",
        "annotation-package",
    )


def test_annotation_compile_route_is_project_scoped() -> None:
    assert _route("/api/v1/projects/demo/identity-benchmark/annotation-compile") == (
        "demo",
        "annotation-compile",
    )


def test_unknown_identity_annotation_route_falls_through() -> None:
    assert _route("/api/v1/projects/demo/identity-benchmark/predicted-clusters") == (
        "",
        "",
    )
