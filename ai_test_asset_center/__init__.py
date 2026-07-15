"""AI Defect Discovery Platform core package.

Importing the package is intentionally side-effect free. Runtime composition is
owned by explicit entrypoints; package import must never monkeypatch discovery
functions or install evaluator behavior into the product process.

Submodule references are resolved lazily via ``__getattr__`` so that package-
level ``import ai_test_asset_center`` never triggers cascade imports, runtime
side effects, or JWT/environment checks.
"""

import importlib as _importlib

_LAZY_MODULES: set[str] = {
    "db_persistence",
    "display_ready_formatter",
    "enterprise_pilot_runtime_with_chain",
    "private_pilot_service",
    "real_project_discovery_with_chain",
    "risk_based_probe_planner",
}


def __getattr__(name: str):
    if name in _LAZY_MODULES:
        mod = _importlib.import_module(f".{name}", __name__)
        # Bind into module globals so subsequent accesses avoid __getattr__.
        globals()[name] = mod
        return mod
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
