from ai_test_asset_center.actor_exploration_runtime import can_explore_actor


def test_compile_admits_source_declared_read_post_as_safe_read():
    decision = can_explore_actor(
        {
            "id": "op-evaluate",
            "method": "POST",
            "path": "/api/contracts/evaluate",
            "read_write": "read",
        },
        {},
    )

    assert decision.allowed is True
    assert decision.reason == "safe_read"
    assert decision.max_attempts == 3
    assert decision.requires_owner is False


def test_compile_admits_no_side_effect_post_as_safe_read():
    decision = can_explore_actor(
        {
            "id": "op-estimate",
            "method": "POST",
            "path": "/api/contracts/evaluate",
            "side_effect_class": "no_side_effect",
        },
        {},
    )

    assert decision.reason == "safe_read"
    assert decision.max_attempts == 3


def test_explicit_write_declaration_overrides_query_looking_path():
    decision = can_explore_actor(
        {
            "id": "op-search-rebuild",
            "method": "POST",
            "path": "/api/search/rebuild",
            "read_write": "write",
        },
        {},
    )

    assert decision.allowed is True
    assert decision.reason == "general_write"
    assert decision.max_attempts == 2


def test_shared_semantic_lexicon_still_recognizes_read_like_post():
    decision = can_explore_actor(
        {
            "id": "op-search",
            "method": "POST",
            "path": "/api/documents/search",
        },
        {},
    )

    assert decision.allowed is True
    assert decision.reason == "safe_read"
    assert decision.max_attempts == 3


def test_declared_write_with_compensation_remains_compensated_write():
    decision = can_explore_actor(
        {
            "id": "op-search-rebuild",
            "method": "POST",
            "path": "/api/search/rebuild",
            "read_write": "write",
        },
        {
            "cleanup_requirement": {
                "required": True,
                "operation_ref": "op-search-restore",
            }
        },
    )

    assert decision.allowed is True
    assert decision.reason == "compensated_write"
    assert decision.max_attempts == 2
