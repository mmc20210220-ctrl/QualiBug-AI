"""Adapter registry and deterministic resolution."""
from __future__ import annotations

from typing import Iterable

from .contract import AdapterMatch, DocumentAdapter, DocumentSource, validate_adapter


class DocumentAdapterRegistry:
    def __init__(self, adapters: Iterable[DocumentAdapter] = ()) -> None:
        self._adapters: dict[str, DocumentAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: DocumentAdapter) -> None:
        violations = validate_adapter(adapter)
        if violations:
            raise ValueError(f"invalid document adapter {getattr(adapter, 'name', '')}: {violations}")
        name = str(adapter.name)
        if name in self._adapters:
            raise ValueError(f"document adapter already registered: {name}")
        self._adapters[name] = adapter

    def get(self, name: str) -> DocumentAdapter:
        try:
            return self._adapters[str(name)]
        except KeyError as exc:
            raise KeyError(f"document adapter not registered: {name}") from exc

    def all(self) -> list[DocumentAdapter]:
        return sorted(
            self._adapters.values(),
            key=lambda adapter: (-int(getattr(adapter, "priority", 0)), str(adapter.name)),
        )

    def matches(self, source: DocumentSource) -> list[tuple[DocumentAdapter, AdapterMatch]]:
        rows: list[tuple[DocumentAdapter, AdapterMatch]] = []
        for adapter in self.all():
            match = adapter.probe(source)
            if match is not None:
                rows.append((adapter, match))
        return sorted(
            rows,
            key=lambda row: (
                -int(row[1].score),
                -int(getattr(row[0], "priority", 0)),
                str(row[0].name),
            ),
        )


def build_default_registry() -> DocumentAdapterRegistry:
    from .builtin_adapters import (
        DocxDocumentAdapter,
        GenericTextDocumentAdapter,
        PdfDocumentAdapter,
        UnknownBinaryDocumentAdapter,
    )

    return DocumentAdapterRegistry(
        [
            DocxDocumentAdapter(),
            PdfDocumentAdapter(),
            GenericTextDocumentAdapter(),
            UnknownBinaryDocumentAdapter(),
        ]
    )


__all__ = ["DocumentAdapterRegistry", "build_default_registry"]
