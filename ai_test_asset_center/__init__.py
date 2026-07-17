"""AI Defect Discovery Platform core package.

Importing the package is intentionally side-effect free. Runtime composition is
owned by explicit entrypoints; package import must never monkeypatch discovery
functions or install evaluator behavior into the product process.

Python's normal package import machinery resolves explicit submodule imports.
The package root intentionally defines no dynamic import facade, so a plain
``import ai_test_asset_center`` cannot trigger cascade imports, runtime side
effects, or JWT/environment checks.
"""
