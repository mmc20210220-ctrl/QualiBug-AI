"""
QualiBug AI Enterprise Edition - Setup Script

Usage:
    pip install -e .          # Development install
    pip install .             # Production install
    pip install .[dev]        # Install with dev dependencies
    pip install .[llm]        # Install with LLM dependencies
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_path = Path("README.md")
long_description = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

setup(
    name="qualibug-ai",
    version="95.0.0",
    author="QualiBug Team",
    author_email="team@qualibug.com",
    description="Enterprise Business-Quality Assurance Platform - AI-powered bug discovery",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://qualibug.com",
    license="Proprietary",
    
    # Package discovery
    packages=find_packages(where=".", exclude=["tests*", "docs*", "examples*"]),
    
    # Include package data
    include_package_data=True,
    package_data={
        "ai_test_asset_center": ["*.json", "*.yaml", "*.html"],
        "aitestops": ["*.json", "*.yaml"],
    },
    
    # Python version requirement
    python_requires=">=3.11",
    
    # Core dependencies
    install_requires=[
        "flask>=2.3.0",
        "werkzeug>=2.3.0",
        "jsonschema>=4.0.0",
        "pyyaml>=6.0",
        "requests>=2.28.0",
        "python-dotenv>=1.0.0",
        "click>=8.0.0",
        "rich>=13.0.0",
    ],
    
    # Optional dependencies
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "pytest-html>=4.1.0",
            "pytest-metadata>=3.1.0",
        ],
        "llm": [
            "openai>=1.0.0",
            "anthropic>=0.18.0",
        ],
        "pdf": [
            "pypdf>=4.0.0",
            "python-docx>=0.8.11",
        ],
        "browser": [
            "playwright>=1.40.0",
        ],
        "all": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "openai>=1.0.0",
            "anthropic>=0.18.0",
            "pypdf>=4.0.0",
            "playwright>=1.40.0",
        ],
    },
    
    # Entry points
    entry_points={
        "console_scripts": [
            "qualibug=aitestops.cli:main",
            "qualibug-server=ai_test_asset_center.private_pilot_service:run_server",
        ],
    },
    
    # Classifiers
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Quality Assurance",
        "Topic :: Software Development :: Testing",
    ],
)
