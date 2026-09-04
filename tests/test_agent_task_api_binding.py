from ai_test_asset_center.agent_task_api_binding import resolve_source_backed_api_binding


def _candidate(
    operation_ref: str = "catalog.list-products",
    *,
    method: str = "GET",
    path: str = "/products",
    source_id: str = "source-openapi",
    locator: str = "paths./products.get",
) -> dict:
    return {
        "operation_ref": operation_ref,
        "method": method,
        "path": path,
        "source_kind": "openapi",
        "evidence": [
            {
                "source_id": source_id,
                "source_revision": "rev-7",
                "source_locator": locator,
            }
        ],
    }


def test_unique_source_backed_binding_is_executable() -> None:
    result = resolve_source_backed_api_binding(
        operation_ref="catalog.list-products",
        operation_candidates=[_candidate()],
    )

    assert result["ok"] is True
    assert result["status"] == "BOUND"
    assert result["binding"]["method"] == "GET"
    assert result["binding"]["path"] == "/products"
    assert result["binding"]["evidence"][0]["source_id"] == "source-openapi"


def test_missing_binding_remains_blocked_instead_of_guessing_from_operation_ref() -> None:
    result = resolve_source_backed_api_binding(
        operation_ref="catalog.list-products",
        operation_candidates=[],
    )

    assert result["ok"] is False
    assert result["status"] == "BLOCKED"
    assert result["blocking_codes"] == ["API_BINDING_NOT_FOUND"]
    assert result["binding"] is None


def test_ambiguous_binding_remains_blocked() -> None:
    result = resolve_source_backed_api_binding(
        operation_ref="catalog.list-products",
        operation_candidates=[
            _candidate(),
            _candidate(path="/v2/products", locator="paths./v2/products.get"),
        ],
    )

    assert result["ok"] is False
    assert result["blocking_codes"] == ["API_BINDING_AMBIGUOUS"]
    assert result["match_count"] == 2


def test_binding_without_source_evidence_remains_blocked() -> None:
    candidate = _candidate()
    candidate["evidence"] = []

    result = resolve_source_backed_api_binding(
        operation_ref="catalog.list-products",
        operation_candidates=[candidate],
    )

    assert result["ok"] is False
    assert result["blocking_codes"] == ["API_BINDING_EVIDENCE_INCOMPLETE"]


def test_exact_duplicate_source_rows_do_not_create_false_ambiguity() -> None:
    candidate = _candidate()
    result = resolve_source_backed_api_binding(
        operation_ref="catalog.list-products",
        operation_candidates=[candidate, dict(candidate)],
    )

    assert result["ok"] is True
    assert result["binding"]["method"] == "GET"
    assert result["binding"]["path"] == "/products"
    assert result["match_count"] == 2
