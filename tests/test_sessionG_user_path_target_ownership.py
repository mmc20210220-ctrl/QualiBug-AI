"""Session G: path-target ownership + formal execution phase root fixes.

Covers the generic mechanisms added in session G:

1. Full discovery scans declare ``validation_phase=formal`` so the per-batch
   execution budget is the formal budget (≤100), not the silent small-scale
   default (≤20) that previously starved compiled obligations
   (``OBLIGATION_BUDGET_REACHED`` on 539 of 859 in the 131-bug baseline).
2. Identity-addressed path reads (``profile/{id}`` documented as 本人或管理
   员) derive source-grounded ``owns`` relations, producing owner/viewer
   isolation obligations instead of permit-only single-arm checks.
3. The isolation family compiles those reads as a two-arm owned-resource
   read (owner reads own identity-addressed resource, viewer reads the
   owner's resource) using runtime-observed account ids — no create fixture
   required for entities that cannot be created (profiles, accounts).
4. Path-target writes on an owned collection (DELETE /api/users/addresses/
   {id}) propagate the owns relation through the collection identity.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.experiment_protocols_base import compile_family_protocol
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.product_scan_mainline import _apply_scan_execution_defaults


def _relation(
    relation_id: str,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    *,
    operation_ref: str = "",
    actor_ref: str = "",
) -> dict:
    return {
        "id": relation_id,
        "relation_type": relation_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "operation_ref": operation_ref or to_ref,
        "actor_ref": actor_ref or from_ref,
        "preconditions": [],
        "effects": [],
        "status": "accepted",
        "confidence": 0.9,
        "source_refs": [{"source_id": "test", "locator": relation_id, "kind": "relation"}],
    }


def _user_ir() -> dict:
    """IR mirroring the run6 user module: owned collection + identity reads."""
    ir = empty_behavior_ir(project_id="user-path-target")
    ir.update({
        "operations": [
            {
                "id": "op-addresses-get",
                "method": "GET",
                "path": "/api/users/addresses",
                "read_write": "read",
                "summary": "查询用户地址（应校验归属）",
                "description": "userId 目标用户 ID；调用方只能查询自己的地址，跨用户查询应返回 403/404。",
                "tags": ["api", "userId"],
                "parameters": [
                    {"name": "userId", "in": "query", "description": "目标用户 ID；调用方只能查询自己的地址"}
                ],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/users/addresses", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-addresses-delete",
                "method": "DELETE",
                "path": "/api/users/addresses/{id}",
                "read_write": "write",
                "summary": "删除用户地址",
                "description": "身份绑定清理；创建后必须可逆。",
                "parameters": [
                    {"name": "id", "in": "path", "description": "资源 ID", "schema": {"type": "string"}}
                ],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "DELETE /api/users/addresses/{id}", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-addresses-post",
                "method": "POST",
                "path": "/api/users/addresses",
                "read_write": "write",
                "summary": "创建用户地址",
                "request_example": {"receiver": "张三", "phone": "13800000000"},
                "source_refs": [
                    {"source_id": "api_spec", "locator": "POST /api/users/addresses", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-profile",
                "method": "GET",
                "path": "/api/users/profile/{id}",
                "read_write": "read",
                "summary": "查询用户资料",
                "description": "权限：本人或管理员。",
                "parameters": [
                    {"name": "id", "in": "path", "description": "资源 ID", "schema": {"type": "string", "format": "uuid"}}
                ],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/users/profile/{id}", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-admin-search",
                "method": "GET",
                "path": "/api/users/admin/search",
                "read_write": "read",
                "summary": "管理员搜索用户",
                "description": "权限：仅限管理员。",
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/users/admin/search", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-products-get",
                "method": "GET",
                "path": "/api/products/{sku}",
                "read_write": "read",
                "summary": "商品详情",
                "description": "查询商品详情。",
                "parameters": [
                    {"name": "sku", "in": "path", "description": "商品 SKU", "schema": {"type": "string"}}
                ],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/products/{sku}", "kind": "api_operation"}
                ],
            },
        ],
        "entities": [
            {
                "id": "ent-users",
                "name": "users",
                "fields": ["id", "email", "password", "phone", "balance"],
                "status": "accepted",
            },
            {
                "id": "ent-addresses",
                "name": "addresses",
                "fields": ["id", "user_id", "receiver", "phone"],
                "status": "accepted",
            },
        ],
        "actors": [
            {
                "id": "actor-buyer-a",
                "role": "buyer",
                "account_ref": "buyer_a",
                "account_id": "00000000-0000-0000-0000-00000000000a",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:buyer_a",
            },
            {
                "id": "actor-buyer-b",
                "role": "buyer",
                "account_ref": "buyer_b",
                "account_id": "00000000-0000-0000-0000-00000000000b",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:buyer_b",
            },
            {
                "id": "actor-finance",
                "role": "finance",
                "account_ref": "finance01",
                "account_id": "00000000-0000-0000-0000-00000000000c",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:finance01",
            },
        ],
        "relations": [
            _relation("obs-addresses", "observes", "op-addresses-get", "ent-addresses", operation_ref="op-addresses-get"),
            _relation("prod-addresses", "produces", "op-addresses-post", "ent-addresses", operation_ref="op-addresses-post"),
            _relation("comp-delete", "compensates", "op-addresses-delete", "op-addresses-post", operation_ref="op-addresses-delete"),
            _relation("obs-profile", "observes", "op-profile", "ent-users", operation_ref="op-profile"),
            _relation("obs-search", "observes", "op-admin-search", "ent-users", operation_ref="op-admin-search"),
            # create-fixture actor authority (permission matrix declares the
            # role may create addresses — the fixture's owner-actor proof
            # resolves through these permits relations)
            _relation("perm-a-post", "permits", "actor-buyer-a", "op-addresses-post", operation_ref="op-addresses-post", actor_ref="actor-buyer-a"),
            _relation("perm-b-post", "permits", "actor-buyer-b", "op-addresses-post", operation_ref="op-addresses-post", actor_ref="actor-buyer-b"),
        ],
    })
    return ir


# ── Fix 1: formal execution phase default ──


def test_scan_defaults_declare_formal_phase_when_undeclared() -> None:
    context = _apply_scan_execution_defaults(
        {"target_id": "t", "environment_ref": "http://localhost:8080"},
        "http://localhost:8080",
    )
    assert context.get("validation_phase") == "formal"


def test_scan_defaults_preserve_operator_phase_override() -> None:
    context = _apply_scan_execution_defaults(
        {"validation_phase": "small_scale", "base_url": "http://localhost:8080"},
        "http://localhost:8080",
    )
    assert context.get("validation_phase") == "small_scale"


# ── Fix 2a: path-target owns relations ──


def test_path_target_read_derives_owns_and_isolation() -> None:
    compiled = compile_obligations_from_behavior_ir(_user_ir())
    profile_isolation = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-profile"
    ]
    assert profile_isolation, "identity-addressed path read must emit isolation"
    prop = profile_isolation[0]["property"]
    assert prop.get("require_ownership_evidence") is True
    assert "owned_resource" in profile_isolation[0].get("required_fixtures", [])
    # buyer pair only: finance has no same-role peer in this IR
    actors = {row["property"].get("owner_actor_ref") for row in profile_isolation}
    assert "actor-buyer-a" in actors


def test_non_identity_path_read_gets_no_owns() -> None:
    ir = _user_ir()
    compiled = compile_obligations_from_behavior_ir(ir)
    product_isolation = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-products-get"
    ]
    assert not product_isolation, "public non-identity read must not become isolation"
    product_owns = [
        rel
        for rel in ir["relations"]
        if rel.get("relation_type") == "owns"
        and rel.get("operation_ref") == "op-products-get"
    ]
    assert not product_owns


def test_path_target_write_propagates_owns_via_collection() -> None:
    ir = _user_ir()
    compiled = compile_obligations_from_behavior_ir(ir)
    delete_owns = [
        rel
        for rel in ir["relations"]
        if rel.get("relation_type") == "owns"
        and rel.get("operation_ref") == "op-addresses-delete"
    ]
    assert delete_owns, "DELETE on an owned collection must propagate owns"
    assert delete_owns[0].get("preconditions", [{}])[0].get("path_target") is True
    delete_isolation = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-addresses-delete"
    ]
    assert delete_isolation, "owned path-target write must emit isolation"


# ── Fix 2b: two-arm identity-addressed read protocol ──


def test_identity_addressed_read_compiles_two_arm_protocol() -> None:
    ir = _user_ir()
    obligation = next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-profile"
        and row["property"].get("owner_actor_ref") == "actor-buyer-a"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment.get("compile_receipt")
    control = experiment["control_plan"][0]
    treatment = experiment["treatment_plan"][0]
    owner_id = "00000000-0000-0000-0000-00000000000a"
    assert control["path"] == f"/api/users/profile/{owner_id}"
    assert treatment["path"] == f"/api/users/profile/{owner_id}"
    assert control["actor_ref"] == "actor-buyer-a"
    assert treatment["actor_ref"] == "actor-buyer-b"
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == "owner_tenant_visibility"
    # the fixture-based proof must not be required for the two-arm shape
    assert "owned_resource" not in experiment.get("required_fixtures", [])


def test_identity_addressed_read_without_peer_returns_none() -> None:
    ir = _user_ir()
    # only one buyer actor: no peer to aim at
    ir["actors"] = [
        actor
        for actor in ir["actors"]
        if actor["id"] != "actor-buyer-b"
    ]
    compiled = compile_obligations_from_behavior_ir(ir)
    profile_isolation = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-profile"
    ]
    # no buyer pair -> no isolation obligation; the permit-only channel stays
    assert not profile_isolation


def test_family_protocol_direct_shape() -> None:
    ir = _user_ir()
    compiled = compile_obligations_from_behavior_ir(ir)
    obligation = next(
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-profile"
        and row["property"].get("owner_actor_ref") == "actor-buyer-a"
    )
    prop = obligation["property"]
    protocol = compile_family_protocol(
        risk_family="isolation",
        operation={
            "id": "op-profile",
            "method": "GET",
            "path": "/api/users/profile/{id}",
            "parameters": [{"name": "id", "in": "path", "description": "资源 ID"}],
            "description": "权限：本人或管理员。",
        },
        operation_ref="op-profile",
        control_actor_ref=prop.get("control_actor_ref") or prop.get("owner_actor_ref"),
        treatment_actor_ref=prop.get("treatment_actor_ref") or prop.get("viewer_actor_ref"),
        property_spec=prop,
        behavior_ir=ir,
    )
    assert protocol["status"] == "COMPILED"
    assert protocol.get("_identity_addressed_read") is True
    assert protocol["assertion"]["kind"] == "owner_tenant_visibility"


def test_path_target_write_isolation_gets_owned_resource_fixture_proof() -> None:
    """Path-target write isolation (DELETE /api/users/addresses/{id}) must
    materialize the owned-resource proof from the collection create even when
    the placeholder binding resolves through a collection GET read — otherwise
    the ownership boundary never compiles (BLOCKED_MISSING_FIXTURE)."""
    from ai_test_asset_center.runtime_binding_graph import build_binding_plan

    ir = _user_ir()
    # give the create operation a request example so the fixture is buildable
    for op in ir["operations"]:
        if op.get("id") == "op-addresses-post":
            op["request_example"] = {"receiver": "张三", "phone": "13800000000"}
    compiled = compile_obligations_from_behavior_ir(ir)
    delete_iso = next(
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-addresses-delete"
    )
    del_op = next(op for op in ir["operations"] if op.get("id") == "op-addresses-delete")
    plan = build_binding_plan(
        operation=del_op,
        obligation=delete_iso,
        actors=[a for a in ir["actors"] if a["id"] in delete_iso.get("required_actors", [])],
        behavior_ir=ir,
    )
    proofs = [
        row
        for row in plan
        if row.get("fixture_id") == "owned_resource"
        and row.get("status") == "fixture_proof"
    ]
    assert proofs, "owned_resource must compile to a fixture_proof from the collection create"
    assert proofs[0].get("create_operation_ref") == "op-addresses-post"
    owner = delete_iso["property"].get("owner_actor_ref")
    assert proofs[0].get("owner_actor_ref") == owner


def test_path_target_write_without_create_fixture_stays_required() -> None:
    """Without a buildable create fixture the owned_resource proof stays a
    visible 'required' entry (the compile blocks with BLOCKED_MISSING_FIXTURE
    instead of silently degrading the ownership test)."""
    from ai_test_asset_center.runtime_binding_graph import build_binding_plan

    ir = _user_ir()
    # strip the create example: fixture cannot be built
    for op in ir["operations"]:
        if op.get("id") == "op-addresses-post":
            op.pop("request_example", None)
    compiled = compile_obligations_from_behavior_ir(ir)
    delete_iso = next(
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-addresses-delete"
    )
    del_op = next(op for op in ir["operations"] if op.get("id") == "op-addresses-delete")
    plan = build_binding_plan(
        operation=del_op,
        obligation=delete_iso,
        actors=[a for a in ir["actors"] if a["id"] in delete_iso.get("required_actors", [])],
        behavior_ir=ir,
    )
    required = [
        row
        for row in plan
        if row.get("fixture_id") == "owned_resource"
        and row.get("status") == "required"
    ]
    assert required, "unbuildable fixture must stay a visible required entry"


# ── USER-008: response-side privacy rule survives actor pairing ──


def test_response_side_privacy_obligation_survives_pairing() -> None:
    """A privacy rule constraining RESPONSE content (导出结果禁止包含
    password) is a single-arm field check: the permits/denies actor pairing
    must keep the obligation (not discard it as BLOCKED_MISSING_ACTOR_PAIR)
    and stamp the field-policy shape from the rule's own text."""
    from ai_test_asset_center.obligation_compiler_privacy_pair_base import (
        _pair_obligations,
    )
    from ai_test_asset_center.test_obligation import make_obligation

    ir = empty_behavior_ir(project_id="pair-privacy")
    ir["actors"] = [{
        "id": "actor-admin", "role": "admin", "account_ref": "admin",
        "account_status": "active", "credential_secret_ref": "secret_ref:test_accounts:admin",
    }]
    ir["operations"] = [{
        "id": "op-export", "method": "GET", "path": "/api/users/admin/export",
        "read_write": "read",
        "source_refs": [{"source_id": "api_spec", "locator": "GET /api/users/admin/export", "kind": "api_operation"}],
    }]
    obl = make_obligation(
        risk_family="privacy",
        subject_refs=["inv-1", "op-export"],
        property_spec={
            "template": "invariant_privacy",
            "invariant_ref": "inv-1",
            "operation_ref": "op-export",
            "expression": {
                "kind": "privacy", "operator": "must_hold", "operands": [],
                "raw": "导出结果禁止包含 password 或其他认证凭据",
            },
        },
        required_actors=[],
        required_operations=["op-export"],
        required_observers=["http_response"],
        source_refs=[{"source_id": "api_spec", "locator": "GET /api/users/admin/export", "kind": "api_operation"}],
    )
    result = {"obligations": [obl], "coverage_gaps": [], "by_family": {"privacy": 1}, "obligation_count": 1}
    out = _pair_obligations(result, ir)
    kept = [
        row
        for row in out["obligations"]
        if row["risk_family"] == "privacy"
    ]
    assert kept, "response-side privacy obligation must survive pairing"
    prop = kept[0]["property"]
    assert prop.get("privacy_test_mode") == "field_policy"
    assert prop.get("privacy_policy") == "absent"
    assert "password" in prop.get("field_tokens", [])
    assert "credential" in prop.get("field_tokens", [])


def test_response_side_privacy_obligation_compiles_single_arm_read() -> None:
    """The kept obligation compiles as a single-arm read with the
    privacy_field_policy assertion (no treatment actor required)."""
    from ai_test_asset_center.obligation_compiler_privacy_pair_base import (
        _pair_obligations,
    )
    from ai_test_asset_center.test_obligation import make_obligation

    ir = empty_behavior_ir(project_id="pair-privacy-compile")
    ir["actors"] = [{
        "id": "actor-admin", "role": "admin", "account_ref": "admin",
        "account_status": "active", "credential_secret_ref": "secret_ref:test_accounts:admin",
    }]
    ir["operations"] = [{
        "id": "op-export", "method": "GET", "path": "/api/users/admin/export",
        "read_write": "read",
        "source_refs": [{"source_id": "api_spec", "locator": "GET /api/users/admin/export", "kind": "api_operation"}],
    }]
    obl = make_obligation(
        risk_family="privacy",
        subject_refs=["inv-1", "op-export"],
        property_spec={
            "template": "invariant_privacy",
            "invariant_ref": "inv-1",
            "operation_ref": "op-export",
            "expression": {
                "kind": "privacy", "operator": "must_hold", "operands": [],
                "raw": "导出结果禁止包含 password 或其他认证凭据",
            },
        },
        required_actors=["actor-admin"],
        required_operations=["op-export"],
        required_observers=["http_response"],
        source_refs=[{"source_id": "api_spec", "locator": "GET /api/users/admin/export", "kind": "api_operation"}],
    )
    result = {"obligations": [obl], "coverage_gaps": [], "by_family": {"privacy": 1}, "obligation_count": 1}
    kept = _pair_obligations(result, ir)["obligations"][0]
    experiment = compile_experiment_for_obligation(
        kept, behavior_ir=ir, environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment.get("compile_receipt")
    kinds = [a.get("kind") for a in experiment.get("assertions") or []]
    assert "privacy_field_policy" in kinds
    treatment = experiment["treatment_plan"][0]
    assert treatment["actor_ref"] == "actor-admin"
    assert treatment["intent"] == "privacy_field_observation"


def test_privacy_field_policy_absent_assertion_violates_on_present_field() -> None:
    """The absent policy must VIOLATE when the observed body carries a
    forbidden token (export response containing password)."""
    from ai_test_asset_center._assertion_dsl_privacy_mechanics import (
        evaluate_assertion as _privacy_evaluate,
    )

    receipt = _privacy_evaluate(
        {
            "kind": "privacy_field_policy",
            "privacy_policy": "absent",
            "field_tokens": ["password", "credential"],
            "match_field_names": True,
        },
        observations={
            "status_code": 200,
            "body": {"rows": [{"id": "u1", "email": "a@b.c", "password": "secret", "role": "seller"}]},
        },
        source_refs=[{"source_id": "api_spec", "locator": "GET /api/users/admin/export", "kind": "api_operation"}],
    )
    assert receipt["status"] == "VIOLATION", receipt.get("reason_code")
    # compliant target: no forbidden token -> PASS
    clean = _privacy_evaluate(
        {
            "kind": "privacy_field_policy",
            "privacy_policy": "absent",
            "field_tokens": ["password", "credential"],
            "match_field_names": True,
        },
        observations={
            "status_code": 200,
            "body": {"rows": [{"id": "u1", "email": "a@b.c", "role": "seller"}]},
        },
        source_refs=[{"source_id": "api_spec", "locator": "GET /api/users/admin/export", "kind": "api_operation"}],
    )
    assert clean["status"] == "PASS", clean.get("reason_code")
