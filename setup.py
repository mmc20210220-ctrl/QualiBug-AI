"""Legacy setuptools compatibility shim.

`pyproject.toml` is the single dependency source of truth. This file only
supports older editable-install workflows by reading the same metadata instead
of maintaining a divergent dependency list.
"""
from __future__ import annotations

from pathlib import Path
import tomllib

from setuptools import find_packages, setup


ROOT = Path(__file__).parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

setup(
    name=PROJECT["name"],
    version=PROJECT["version"],
    author="QualiBug Team",
    author_email="team@qualibug.com",
    description=PROJECT["description"],
    long_description=(ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else "",
    long_description_content_type="text/markdown",
    license=PROJECT["license"]["text"],
    packages=find_packages(where=".", exclude=["tests*", "docs*", "examples*"]),
    include_package_data=True,
    package_data={
        "ai_test_asset_center": ["*.json", "*.yaml", "*.html"],
        "aitestops": ["*.json", "*.yaml"],
    },
    python_requires=PROJECT["requires-python"],
    install_requires=PROJECT.get("dependencies", []),
    extras_require=PROJECT.get("optional-dependencies", {}),
    entry_points={
        "console_scripts": [
            "qualibug=aitestops.cli:main",
            "qualibug-server=ai_test_asset_center.private_pilot_service:run_server",
        ],
    },
    classifiers=PROJECT.get("classifiers", []),
)
