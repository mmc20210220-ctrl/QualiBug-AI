"""Adapter registry and deterministic resolution."""
from __future__ import annotations

from typing import Iterable

from .contract import (
    AdapterMatch,
    DocumentAdapter,
    DocumentSource,
    MODE_SUPPLEMENTAL,
    SupplementalContext,
    validate_adapter,
)


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

    def supplemental_matches(
        self,
        source: DocumentSource,
        context: SupplementalContext,
        *,
        excluded_names: Iterable[str] = (),
    ) -> list[tuple[DocumentAdapter, AdapterMatch]]:
        excluded = {str(value) for value in excluded_names}
        rows: list[tuple[DocumentAdapter, AdapterMatch]] = []
        for adapter in self.all():
            if adapter.name in excluded or adapter.mode != MODE_SUPPLEMENTAL:
                continue
            match = adapter.probe_supplemental(source, context)
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
    from .advanced_visual_table_providers import build_default_advanced_visual_table_provider
    from .builtin_adapters import (
        GenericTextDocumentAdapter,
        PdfDocumentAdapter,
        UnknownBinaryDocumentAdapter,
    )
    from .database_model_adapter import DatabaseModelDocumentAdapter
    from .guarded_api_artifact_adapter import GuardedApiArtifactDocumentAdapter
    from .guarded_compatible_office_adapter import GuardedCompatibleOfficeDocumentAdapter
    from .html_document_adapter import HtmlDocumentAdapter
    from .native_office_policy_adapters import (
        MacroAwareDocxDocumentAdapter,
        MacroAwarePresentationDocumentAdapter,
        MacroAwareSpreadsheetDocumentAdapter,
    )
    from .rendered_ocr_adapter import OcrSupplementalAdapter
    from .visual_table_adapter import VisualTableSupplementalAdapter
    from .visual_table_provider_gate import GeometryFormalEnforcingVisualTableProvider

    advanced_table_provider = GeometryFormalEnforcingVisualTableProvider(
        build_default_advanced_visual_table_provider()
    )
    return DocumentAdapterRegistry(
        [
            MacroAwareDocxDocumentAdapter(),
            PdfDocumentAdapter(),
            MacroAwareSpreadsheetDocumentAdapter(),
            MacroAwarePresentationDocumentAdapter(),
            DatabaseModelDocumentAdapter(),
            GuardedCompatibleOfficeDocumentAdapter(),
            GuardedApiArtifactDocumentAdapter(),
            HtmlDocumentAdapter(),
            OcrSupplementalAdapter(),
            VisualTableSupplementalAdapter(provider=advanced_table_provider),
            GenericTextDocumentAdapter(),
            UnknownBinaryDocumentAdapter(),
        ]
    )


__all__ = ["DocumentAdapterRegistry", "build_default_registry"]
