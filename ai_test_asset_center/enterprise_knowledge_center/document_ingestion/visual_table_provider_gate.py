"""Enforce provider-level geometry decisions before visual-table adapter formalization."""
from __future__ import annotations

from typing import Any

from .contract import text
from .page_rendering import RenderedPage
from .visual_table_adapter import VisualTableProvider


class GeometryFormalEnforcingVisualTableProvider:
    """Prevent downstream thresholds from overriding an explicit provider rejection.

    Legacy providers that do not emit ``geometry_formal`` remain compatible. When a provider
    explicitly sets it to false, every returned cell is marked structurally incomplete so the
    existing adapter gate cannot promote that table merely because its generic confidence
    threshold is lower.
    """

    def __init__(self, provider: VisualTableProvider) -> None:
        self.provider = provider

    @property
    def name(self) -> str:
        return str(getattr(self.provider, "name", "geometry-formal-enforcing-provider"))

    @property
    def version(self) -> str:
        return str(getattr(self.provider, "version", "1"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    def available(self) -> bool:
        return self.provider.available()

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        rows = self.provider.detect(
            rendered_page,
            region_bbox=region_bbox,
            target_region_id=target_region_id,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            table = dict(row)
            contributing_provider = text(table.get("contributing_provider"))
            method = text(table.get("detection_method"))
            if contributing_provider and f"observed_by={contributing_provider}" not in method:
                table["detection_method"] = (
                    method + f"+observed_by={contributing_provider}"
                ).strip("+")
            if table.get("geometry_formal") is False:
                cells: list[dict[str, Any]] = []
                for value in table.get("cells") or []:
                    if not isinstance(value, dict):
                        continue
                    cell = dict(value)
                    cell["border_complete"] = False
                    cell["provider_geometry_gate_rejected"] = True
                    cells.append(cell)
                table["cells"] = cells
                table["provider_geometry_gate_enforced"] = True
            result.append(table)
        return result


__all__ = ["GeometryFormalEnforcingVisualTableProvider"]
