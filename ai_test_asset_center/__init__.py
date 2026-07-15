"""AI Defect Discovery Platform core package.

Importing the package is intentionally side-effect free. Runtime composition is
owned by explicit entrypoints; package import must never monkeypatch discovery
functions or install evaluator behavior into the product process.
"""

# Top-level product modules are importable from the package root so that
# `from ai_test_asset_center import <module>` works. This only binds submodule
# references; it does not install any runtime behavior into the process.
from . import db_persistence
from . import display_ready_formatter
from . import enterprise_pilot_runtime_with_chain
from . import private_pilot_service
from . import real_project_discovery_with_chain
from . import risk_based_probe_planner
