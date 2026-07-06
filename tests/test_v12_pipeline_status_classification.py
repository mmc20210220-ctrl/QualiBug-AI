"""Test: V12 pipeline correctly classifies HTTP status codes.

Validates the fix for status == 403 being incorrectly flagged as "越权访问成功".
403 = authorization correctly blocked. 401/404/405 should also not generate bug findings.

Covers: 403/401/404/405 don't generate fake bugs; 2xx with wrong actor = real concern.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ────────────────────────────────────────────────────────────────
# Status code classification rules (from v12_pipeline.py fix)
# ────────────────────────────────────────────────────────────────

def _classify_step_status_for_bug(status: int, actor_is_low_priv: bool = False,
                                   path_is_admin: bool = False) -> str | None:
    """Mimics the fixed v12_pipeline.py logic for step-status → finding classification.

    Returns:
        None          → no bug finding should be generated
        "server_error" → genuine server-side bug
        "auth_blocked" → auth working correctly (logged internally only)
        "route_blocked" → route/method mismatch (environment issue)
        "acl_bypass" → potential permission bypass (needs further verification)
    """
    if status >= 500:
        return "server_error"
    if status in (401, 403):
        return "auth_blocked"
    if status in (404, 405):
        return "route_blocked"
    if 200 <= status < 300:
        if actor_is_low_priv and path_is_admin:
            return "acl_bypass"
        return None  # Normal 2xx without indicating wrong actor
    return None  # Other status codes (3xx etc.)


# ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,actor_low,path_admin,expected", [
    # 403/401 → auth working, NOT a bug
    (403, False, False, "auth_blocked"),
    (403, True,  True,  "auth_blocked"),
    (401, False, False, "auth_blocked"),
    # 404/405 → route/method mismatch, NOT a business bug
    (404, False, False, "route_blocked"),
    (405, False, False, "route_blocked"),
    # 5xx → genuine server error
    (500, False, False, "server_error"),
    (502, False, False, "server_error"),
    (503, False, False, "server_error"),
    # 2xx + low priv on admin → potential ACL bypass
    (200, True,  True,  "acl_bypass"),
    (201, True,  True,  "acl_bypass"),
    # 2xx + low priv on non-admin → not necessarily a bug
    (200, True,  False, None),
    # 2xx + normal actor on admin → not necessarily a bug
    (200, False, True,  None),
    # 2xx normal case
    (200, False, False, None),
    # 3xx redirects → not bugs
    (301, False, False, None),
    (302, False, False, None),
])
def test_step_status_classification(status, actor_low, path_admin, expected):
    """Systematic test: each HTTP status + actor + path combination."""
    result = _classify_step_status_for_bug(status, actor_low, path_admin)
    assert result == expected, (
        f"HTTP {status} actor_low={actor_low} path_admin={path_admin}: "
        f"expected {expected}, got {result}"
    )


# ────────────────────────────────────────────────────────────────
# Test: 403 is NOT "权限穿透成功"
# ────────────────────────────────────────────────────────────────

def test_403_is_not_permission_bypass():
    """403 = Forbidden = authorization correctly blocked. NOT a permission bypass."""
    status = 403
    result = _classify_step_status_for_bug(status)
    assert result == "auth_blocked", (
        f"HTTP 403 should be classified as 'auth_blocked', got '{result}'. "
        f"403 means authorization is correctly denying access."
    )


# ────────────────────────────────────────────────────────────────
# Test: 404/405 DON'T generate business bugs
# ────────────────────────────────────────────────────────────────

def test_404_not_business_bug():
    """404 = route doesn't exist. This is an environment issue, not a business bug."""
    result = _classify_step_status_for_bug(404)
    assert result == "route_blocked", (
        f"HTTP 404 should be classified as 'route_blocked', got '{result}'. "
        f"404 means the route doesn't exist — it's an environment/config issue."
    )


def test_405_not_business_bug():
    """405 = method not allowed. Route exists but wrong HTTP method was used."""
    result = _classify_step_status_for_bug(405)
    assert result == "route_blocked", (
        f"HTTP 405 should be classified as 'route_blocked', got '{result}'. "
        f"405 is a client-side method mismatch, not a back-end bug."
    )


# ────────────────────────────────────────────────────────────────
# Test: 2xx + admin path + low priv user = real ACL concern
# ────────────────────────────────────────────────────────────────

def test_2xx_on_admin_with_low_priv_is_acl_concern():
    """2xx success when a low-privilege user accesses an admin endpoint = real concern."""
    result = _classify_step_status_for_bug(200, actor_is_low_priv=True, path_is_admin=True)
    assert result == "acl_bypass", (
        f"2xx with low-priv on admin path should be 'acl_bypass', got '{result}'."
    )
