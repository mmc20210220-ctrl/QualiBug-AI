from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_python_distribution_contains_formal_document_runtime() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        str(value).split(";", 1)[0].strip().lower()
        for value in project["project"]["dependencies"]
    }

    required_prefixes = {
        "openpyxl",
        "pillow",
        "pypdf",
        "pypdfium2",
        "pytesseract",
        "python-docx",
        "python-pptx",
    }
    for package in required_prefixes:
        assert any(value.startswith(package) for value in dependencies), package


def test_customer_runtime_image_provisions_office_rendering_and_chinese_ocr() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for package in (
        "libreoffice-calc",
        "libreoffice-impress",
        "libreoffice-writer",
        "tesseract-ocr-chi-sim",
        "tesseract-ocr-eng",
        "fonts-noto-cjk",
    ):
        assert package in dockerfile

    assert "libreoffice --headless --version" in dockerfile
    assert "tesseract --version" in dockerfile
    assert "import openpyxl, pptx, pypdfium2, pytesseract" in dockerfile
