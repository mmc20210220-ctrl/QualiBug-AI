from __future__ import annotations

import ast
import inspect

from ai_test_asset_center import __main__ as scan_module


def test_scan_does_not_shadow_global_sys_for_fail_safe_error_reporting() -> None:
    source = inspect.getsource(scan_module.scan)
    tree = ast.parse(source)

    local_sys_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "sys" and alias.asname is None
    ]

    assert local_sys_imports == []
