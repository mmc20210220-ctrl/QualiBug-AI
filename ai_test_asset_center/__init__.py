"""AI Defect Discovery Platform core package.

Importing the package is intentionally side-effect free. Runtime composition is
owned by explicit entrypoints; package import must never monkeypatch discovery
functions or install evaluator behavior into the product process.
"""
