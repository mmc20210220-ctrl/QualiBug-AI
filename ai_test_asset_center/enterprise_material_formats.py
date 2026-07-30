"""Canonical transport-format families for enterprise material ingestion.

This module answers one narrow question: what kind of transport container reached the
system? It does not claim semantic fidelity. Document adapters, archive providers and HTTP
boundaries import the same immutable sets so a ZIP-based Office document cannot be routed as
an archive merely because both containers begin with the PK signature.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

ENTERPRISE_TEXT_DOCUMENT_SUFFIXES = frozenset(
    {
        ".md", ".markdown", ".txt", ".rst", ".html", ".htm",
        ".yaml", ".yml", ".json", ".jsonl", ".ndjson", ".csv", ".tsv",
        ".sql", ".xml", ".svg", ".har", ".log", ".toml", ".ini",
        ".conf", ".properties", ".env", ".feature", ".jmx", ".wsdl",
        ".xsd", ".proto", ".graphql", ".gql", ".raml", ".http",
        ".rest", ".mmd", ".bpmn", ".drawio",
    }
)

ENTERPRISE_WORD_CONTAINER_SUFFIXES = frozenset(
    {
        ".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm",
        ".rtf", ".odt", ".wps", ".wpt",
    }
)
ENTERPRISE_SPREADSHEET_CONTAINER_SUFFIXES = frozenset(
    {
        ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm",
        ".ods", ".et", ".ett",
    }
)
ENTERPRISE_PRESENTATION_CONTAINER_SUFFIXES = frozenset(
    {
        ".ppt", ".pptx", ".pptm", ".pot", ".potx", ".potm",
        ".pps", ".ppsx", ".ppsm", ".odp", ".dps", ".dpt",
    }
)
ENTERPRISE_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
)
ENTERPRISE_OTHER_BINARY_DOCUMENT_SUFFIXES = frozenset({".pdf"})

ENTERPRISE_OFFICE_CONTAINER_SUFFIXES = frozenset(
    ENTERPRISE_WORD_CONTAINER_SUFFIXES
    | ENTERPRISE_SPREADSHEET_CONTAINER_SUFFIXES
    | ENTERPRISE_PRESENTATION_CONTAINER_SUFFIXES
)
ENTERPRISE_BINARY_DOCUMENT_SUFFIXES = frozenset(
    ENTERPRISE_OFFICE_CONTAINER_SUFFIXES
    | ENTERPRISE_IMAGE_SUFFIXES
    | ENTERPRISE_OTHER_BINARY_DOCUMENT_SUFFIXES
)

ENTERPRISE_ARCHIVE_TRANSPORT_SUFFIXES = frozenset(
    {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}
)
ENTERPRISE_COMPOUND_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.gzip")

ENTERPRISE_MATERIAL_SUFFIXES = frozenset(
    ENTERPRISE_TEXT_DOCUMENT_SUFFIXES
    | ENTERPRISE_BINARY_DOCUMENT_SUFFIXES
    | ENTERPRISE_ARCHIVE_TRANSPORT_SUFFIXES
)

# These document families may themselves be ZIP containers and therefore must win over a
# generic PK-signature archive probe whenever a filename or package structure identifies them.
ZIP_BASED_DOCUMENT_SUFFIXES = frozenset(
    {
        ".docx", ".docm", ".dotx", ".dotm",
        ".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm",
        ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm",
        ".odt", ".ods", ".odp", ".wps", ".wpt", ".et", ".ett", ".dps", ".dpt",
    }
)


def normalized_suffix(filename: Any) -> str:
    return Path(str(filename or "").lower()).suffix


def is_declared_archive_transport(filename: Any) -> bool:
    value = str(filename or "").lower()
    return normalized_suffix(value) in ENTERPRISE_ARCHIVE_TRANSPORT_SUFFIXES or value.endswith(
        ENTERPRISE_COMPOUND_ARCHIVE_SUFFIXES
    )


def is_declared_document_container(filename: Any) -> bool:
    return normalized_suffix(filename) in ENTERPRISE_BINARY_DOCUMENT_SUFFIXES


def is_declared_zip_based_document(filename: Any) -> bool:
    return normalized_suffix(filename) in ZIP_BASED_DOCUMENT_SUFFIXES


def inspect_pk_document_container(data: bytes, *, max_members: int = 20_000) -> str:
    """Return a known ZIP-based document family without extracting member content.

    The check is intentionally structural and bounded. It prevents extensionless or renamed
    OOXML/ODF packages from being recursively expanded as user archive transports.
    """

    value = bytes(data or b"")
    if not value.startswith(b"PK"):
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(value)) as archive:
            infos = archive.infolist()
            if len(infos) > max_members:
                return ""
            names = {row.filename.replace("\\", "/") for row in infos}
            if "[Content_Types].xml" in names:
                if "word/document.xml" in names:
                    return "ooxml_word"
                if "xl/workbook.xml" in names:
                    return "ooxml_spreadsheet"
                if "xl/workbook.bin" in names:
                    return "ooxml_binary_spreadsheet"
                if "ppt/presentation.xml" in names:
                    return "ooxml_presentation"
            # ODF packages contain content.xml and META-INF/manifest.xml. Several WPS-family
            # packages also expose a document manifest; the declared suffix remains primary.
            if "content.xml" in names and "META-INF/manifest.xml" in names:
                return "odf_document"
    except (OSError, ValueError, zipfile.BadZipFile):
        return ""
    return ""


__all__ = [
    "ENTERPRISE_TEXT_DOCUMENT_SUFFIXES",
    "ENTERPRISE_WORD_CONTAINER_SUFFIXES",
    "ENTERPRISE_SPREADSHEET_CONTAINER_SUFFIXES",
    "ENTERPRISE_PRESENTATION_CONTAINER_SUFFIXES",
    "ENTERPRISE_IMAGE_SUFFIXES",
    "ENTERPRISE_OTHER_BINARY_DOCUMENT_SUFFIXES",
    "ENTERPRISE_OFFICE_CONTAINER_SUFFIXES",
    "ENTERPRISE_BINARY_DOCUMENT_SUFFIXES",
    "ENTERPRISE_ARCHIVE_TRANSPORT_SUFFIXES",
    "ENTERPRISE_COMPOUND_ARCHIVE_SUFFIXES",
    "ENTERPRISE_MATERIAL_SUFFIXES",
    "ZIP_BASED_DOCUMENT_SUFFIXES",
    "normalized_suffix",
    "is_declared_archive_transport",
    "is_declared_document_container",
    "is_declared_zip_based_document",
    "inspect_pk_document_container",
]
