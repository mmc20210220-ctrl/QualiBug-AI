from __future__ import annotations

"""
Entity Catalog Builder — PRD + OpenAPI → Entity Extraction

Builds an entity catalog from PRD markdown text and OpenAPI 3.x schemas.
Extracts EntityCandidate objects with typed fields classified as identity,
state, amount, or quantity fields.  Supports merging multiple catalogs
with deduplication by entity_alias similarity.

Design goals
------------
- Zero hardcoded entity types — all entities are discovered from documents.
- Markdown-aware PRD parsing: headings, tables, keyword-triggered sections.
- OpenAPI-aware schema parsing: #/components/schemas with field classification.
- Merge: fuzzy entity_alias dedup, field union, averaged confidence.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Import EntityCandidate from project_context_compiler (with local fallback)
# ---------------------------------------------------------------------------

try:
    from .project_context_compiler import EntityCandidate  # noqa: F401
except ImportError:
    # Fallback: define locally when project_context_compiler is not yet available.
    @dataclass
    class EntityCandidate:
        """A discovered business entity from documentation or API schemas.

        ``entity_alias`` is the canonical snake_case identifier used for
        deduplication and cross-catalog merging.  ``confidence`` ranges from
        0.0 (low) to 1.0 (high) based on extraction quality signals.
        """

        entity_name: str
        entity_alias: str
        fields: list[EntityField] = field(default_factory=list)
        source: str = ""            # "prd", "openapi", or "merged"
        confidence: float = 0.0
        metadata: dict = field(default_factory=dict)
        catalog_id: str = ""

        def __post_init__(self) -> None:
            if not self.catalog_id:
                self.catalog_id = f"ent_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# EntityField — typed field descriptor
# ---------------------------------------------------------------------------

# Known OpenAPI type keywords mapped to a canonical type string.
_OPENAPI_TYPE_MAP: dict[str, str] = {
    "string":  "string",
    "integer": "integer",
    "number":  "number",
    "boolean": "boolean",
    "array":   "array",
    "object":  "object",
}

# Substring patterns for automatic field role classification.
_IDENTITY_PATTERNS: tuple[str, ...] = (
    "id", "code", "number", "key", "uid", "uuid", "guid", "identifier", "ref",
    "no", "pk", "sk",
)
_STATE_PATTERNS: tuple[str, ...] = (
    "status", "state", "lifecycle", "phase", "stage", "condition",
)
_AMOUNT_NAME_PATTERNS: tuple[str, ...] = (
    "amount", "total", "price", "sum", "cost", "fee", "revenue", "value",
    "balance", "grand_total", "subtotal", "net", "gross", "tax",
)
_QUANTITY_NAME_PATTERNS: tuple[str, ...] = (
    "qty", "quantity", "count", "num", "cnt", "pieces", "units",
)
_AMOUNT_QUANTITY_TYPES: frozenset[str] = frozenset({"integer", "number"})


@dataclass
class EntityField:
    """A single typed field belonging to an entity candidate.

    Role flags (``is_identity``, ``is_state``, ``is_amount``, ``is_quantity``)
    are set automatically during construction based on field name and type.
    Callers can override them after instantiation if needed.
    """

    field_name: str
    field_type: str = "string"   # canonical: string | integer | number | boolean | array | object
    required: bool = False
    description: str = ""

    # Auto-classified roles
    is_identity: bool = False
    is_state: bool = False
    is_amount: bool = False
    is_quantity: bool = False

    # Provenance
    source_path: str = ""        # e.g. "#/components/schemas/Order/properties/id"

    def __post_init__(self) -> None:
        # Canonicalise type
        self.field_type = _OPENAPI_TYPE_MAP.get(self.field_type.lower(), self.field_type.lower())

        # Auto-classify roles from field name + type
        name_lower = self.field_name.lower().replace("_", "").replace("-", "")

        # Identity check — substring match
        if not self.is_identity:
            for pat in _IDENTITY_PATTERNS:
                if pat in name_lower:
                    self.is_identity = True
                    break

        # State check — substring match (lower priority than identity)
        if not self.is_state:
            for pat in _STATE_PATTERNS:
                if pat in name_lower:
                    self.is_state = True
                    break

        # Amount / Quantity — require numeric type AND name match
        if self.field_type in _AMOUNT_QUANTITY_TYPES:
            if not self.is_amount:
                for pat in _AMOUNT_NAME_PATTERNS:
                    if pat in name_lower:
                        self.is_amount = True
                        break
            if not self.is_quantity:
                for pat in _QUANTITY_NAME_PATTERNS:
                    if pat in name_lower:
                        self.is_quantity = True
                        break

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "field_type": self.field_type,
            "required": self.required,
            "description": self.description,
            "is_identity": self.is_identity,
            "is_state": self.is_state,
            "is_amount": self.is_amount,
            "is_quantity": self.is_quantity,
            "source_path": self.source_path,
        }


# ---------------------------------------------------------------------------
# EntityCatalogBuilder
# ---------------------------------------------------------------------------

# PRD section heading keywords that signal entity/object definitions
_PRD_ENTITY_SECTION_KEYWORDS: tuple[str, ...] = (
    "object", "objects", "entity", "entities", "resource", "resources",
    "data model", "data-model", "domain model", "domain-model",
    "schema", "schemas", "model", "models",
    "aggregate", "aggregates", "root", "roots",
    "数据库表", "数据模型", "实体", "对象", "资源",
)

# Regex for extracting field definitions from markdown tables.
# Matches rows like: | field_name | type | required | description |
_MD_TABLE_ROW_RE: re.Pattern = re.compile(
    r"^\s*\|?\s*`?(\w[\w\s_-]*)`?\s*\|\s*(\w[\w<>\[\],\s]*?)\s*\|",
    re.MULTILINE,
)

# Regex for markdown heading extraction (###, ##, #, or bullet-styled).
_MD_HEADING_RE: re.Pattern = re.compile(
    r"^#{1,6}\s+(.+)$",
    re.MULTILINE,
)

# Heuristic: CapitalizedCamelCase or PascalCase words that look like entity names.
_ENTITY_NAME_RE: re.Pattern = re.compile(
    r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b",
)


class EntityCatalogBuilder:
    """Builds entity catalogs from PRD text and OpenAPI specifications.

    Usage::

        builder = EntityCatalogBuilder()
        prd_entities = builder.build_from_prd(prd_markdown)
        api_entities = builder.build_from_openapi(openapi_dict)
        merged = builder.merge_catalogs(prd_entities, api_entities)
    """

    # ------------------------------------------------------------------
    # PRD extraction
    # ------------------------------------------------------------------

    def build_from_prd(self, prd_text: str) -> list[EntityCandidate]:
        """Extract entity candidates from PRD markdown text.

        Strategy
        --------
        1. Scan markdown headings whose text contains entity-section keywords
           (e.g. "Objects", "Entities", "Data Model", "Resources").
        2. Within those sections, look for sub-headings or bold terms that
           resemble entity names (CamelCase, ALL_CAPS, or Capitalised nouns).
        3. Parse markdown tables for field definitions (name | type | ...).
        4. Fall back to scanning the whole document for CamelCase terms and
           nearby field-like lists if no structured sections are found.
        """
        candidates: list[EntityCandidate] = []
        seen_aliases: set[str] = set()

        # ── Phase 1: entity-section-triggered extraction ──────────────────
        sections = self._split_prd_sections(prd_text)
        for heading, body in sections:
            if not self._is_entity_section(heading):
                continue

            # Extract sub-headings as entity names
            sub_entities = self._extract_entities_from_section(heading, body, seen_aliases)
            candidates.extend(sub_entities)
            seen_aliases.update(e.entity_alias for e in sub_entities)

        # ── Phase 2: whole-document fallback for CamelCase terms ─────────
        if not candidates:
            candidates = self._extract_entities_heuristic(prd_text, seen_aliases)

        # ── Phase 3: boost confidence for well-structured extractions ────
        for c in candidates:
            if c.fields:
                c.confidence = min(1.0, c.confidence + 0.3)
            if c.metadata.get("from_table"):
                c.confidence = min(1.0, c.confidence + 0.2)

        return candidates

    def _split_prd_sections(self, text: str) -> list[tuple[str, str]]:
        """Split PRD markdown into (heading, body) pairs."""
        sections: list[tuple[str, str]] = []
        parts = _MD_HEADING_RE.split(text)
        # parts[0] is content before first heading (preamble)
        if parts and parts[0].strip():
            sections.append(("(preamble)", parts[0]))

        # Subsequent pairs: (heading, body)
        for i in range(1, len(parts) - 1, 2):
            heading = parts[i].strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""
            sections.append((heading, body))

        return sections

    def _is_entity_section(self, heading: str) -> bool:
        """Check if a heading signals an entity/object definition section."""
        h_lower = heading.lower()
        for kw in _PRD_ENTITY_SECTION_KEYWORDS:
            if kw in h_lower:
                return True
        return False

    def _extract_entities_from_section(
        self, section_heading: str, body: str, seen_aliases: set[str]
    ) -> list[EntityCandidate]:
        """Extract entities from a PRD section that is known to contain entity definitions."""
        candidates: list[EntityCandidate] = []

        # ── Sub-headings as entity names ──────────────────────────────────
        sub_headings = _MD_HEADING_RE.findall(body)
        entity_names: list[str] = []
        for sh in sub_headings:
            sh = sh.strip()
            # Filter: must look like an entity name (CamelCase or Capitalised)
            if _ENTITY_NAME_RE.match(sh) or (sh[0].isupper() and " " not in sh[:20]):
                entity_names.append(sh)
            # Also match bold markdown: **EntityName**
        bold_entities = re.findall(r"\*\*([A-Z][A-Za-z]+(?:\s*[A-Z][A-Za-z]+)*)\*\*", body)
        for be in bold_entities:
            be_compact = be.replace(" ", "")
            if _ENTITY_NAME_RE.match(be_compact) and be_compact not in entity_names:
                entity_names.append(be_compact)

        # ── Tables as field definitions ───────────────────────────────────
        tables = self._parse_markdown_tables(body)

        # Associate tables with entity names by proximity
        for ename in entity_names:
            alias = self._to_alias(ename)
            if alias in seen_aliases:
                continue
            seen_aliases.add(alias)

            fields = self._extract_fields_from_tables(tables, ename)
            candidates.append(EntityCandidate(
                entity_name=ename,
                entity_alias=alias,
                fields=fields,
                source="prd",
                confidence=0.5 if fields else 0.3,
                metadata={
                    "section": section_heading,
                    "from_table": bool(fields),
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                },
            ))

        # If no sub-headings found but tables exist, name entities from table context
        if not entity_names and tables:
            for table in tables:
                if table.get("caption"):
                    ename = table["caption"]
                    alias = self._to_alias(ename)
                    if alias in seen_aliases:
                        continue
                    seen_aliases.add(alias)
                    fields = self._rows_to_fields(table.get("rows", []))
                    candidates.append(EntityCandidate(
                        entity_name=ename,
                        entity_alias=alias,
                        fields=fields,
                        source="prd",
                        confidence=0.6,
                        metadata={
                            "section": section_heading,
                            "from_table": True,
                            "extracted_at": datetime.now(timezone.utc).isoformat(),
                        },
                    ))

        return candidates

    def _extract_entities_heuristic(
        self, text: str, seen_aliases: set[str]
    ) -> list[EntityCandidate]:
        """Heuristic fallback: find CamelCase terms and nearby field descriptions."""
        candidates: list[EntityCandidate] = []
        # Find all CamelCase terms
        camel_matches = _ENTITY_NAME_RE.findall(text)
        if not camel_matches:
            return candidates

        # Deduplicate and filter common false positives
        stop_words = {
            "The", "This", "That", "These", "Those", "Each", "Every", "Some",
            "Which", "What", "When", "Where", "After", "Before", "During",
            "However", "Therefore", "Because", "Although", "Though",
            "Chapter", "Section", "Appendix", "Figure", "Table",
        }
        unique_names: list[str] = []
        for m in camel_matches:
            if m not in stop_words and m not in unique_names:
                # Only keep terms that appear 2+ times (stronger signal)
                if len(re.findall(r"\b" + re.escape(m) + r"\b", text)) >= 2:
                    unique_names.append(m)

        # Parse tables globally for field hints
        tables = self._parse_markdown_tables(text)
        all_table_fields: dict[str, list[EntityField]] = {}
        for table in tables:
            rows = table.get("rows", [])
            fields = self._rows_to_fields(rows)
            if table.get("caption"):
                alias = self._to_alias(table["caption"])
                all_table_fields[alias] = fields

        for ename in unique_names[:20]:  # Cap at 20 heuristic entities
            alias = self._to_alias(ename)
            if alias in seen_aliases:
                continue
            seen_aliases.add(alias)

            # Try to match with a table
            fields = all_table_fields.get(alias, [])

            candidates.append(EntityCandidate(
                entity_name=ename,
                entity_alias=alias,
                fields=fields,
                source="prd",
                confidence=0.2 if fields else 0.1,
                metadata={
                    "method": "heuristic",
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                },
            ))

        return candidates

    def _parse_markdown_tables(self, text: str) -> list[dict[str, Any]]:
        """Parse markdown tables into structured row lists.

        Returns a list of dicts with keys: 'caption' (optional), 'headers',
        'rows' (list of tuples), and 'raw'.
        """
        tables: list[dict[str, Any]] = []
        # Split on table delimiter rows (| --- | --- |)
        table_blocks = re.split(r"\n\s*\|[\s\-:|]+\|\s*\n", text)
        # Reconstruct: the delimiter belongs between blocks
        if len(table_blocks) < 2:
            return tables

        for i in range(len(table_blocks) - 1):
            # The block BEFORE a delimiter is potential header + caption
            before = table_blocks[i]
            after = table_blocks[i + 1]

            # Try to find a caption line just before the header
            lines_before = before.strip().split("\n")
            caption = ""
            header_line = ""
            for line in reversed(lines_before):
                line = line.strip()
                if line.startswith("|") and "|" in line[1:]:
                    header_line = line
                    break
                elif line and not line.startswith("#") and len(line) < 120:
                    caption = line.strip().lstrip("#").strip()

            if not header_line:
                continue

            headers = [h.strip() for h in header_line.strip("|").split("|")]

            # Parse data rows from the block after the delimiter
            rows: list[tuple[str, ...]] = []
            after_lines = after.strip().split("\n")
            for line in after_lines:
                line = line.strip()
                if not line.startswith("|"):
                    break  # table ended
                cells = [c.strip() for c in line.strip("|").split("|")]
                if any(c for c in cells):  # non-empty row
                    rows.append(tuple(cells))

            if headers or rows:
                tables.append({
                    "caption": caption,
                    "headers": headers,
                    "rows": rows,
                })

        return tables

    def _extract_fields_from_tables(
        self, tables: list[dict[str, Any]], entity_name: str
    ) -> list[EntityField]:
        """Find tables whose caption/context matches *entity_name* and extract fields."""
        alias = self._to_alias(entity_name)
        for table in tables:
            caption_alias = self._to_alias(table.get("caption", ""))
            if caption_alias and (alias in caption_alias or caption_alias in alias):
                return self._rows_to_fields(table.get("rows", []))

        # No direct match — return fields from first table in the section
        if tables:
            return self._rows_to_fields(tables[0].get("rows", []))

        return []

    def _rows_to_fields(self, rows: list[tuple[str, ...]]) -> list[EntityField]:
        """Convert table rows (name, type, required?, description?) to EntityFields."""
        fields: list[EntityField] = []
        for row in rows:
            if len(row) < 1:
                continue
            name = row[0].strip("`*_ '\"")
            if not name or name.lower() in ("field", "字段", "name", "名称", "属性"):
                continue

            ftype = row[1].strip("`*_ '\"") if len(row) > 1 else "string"
            required = False
            if len(row) > 2:
                req_text = row[2].strip().lower()
                required = req_text in ("yes", "y", "true", "required", "是", "必须", "必填")
            description = row[3].strip() if len(row) > 3 else ""

            # Normalise type
            ftype_lower = ftype.lower()
            if ftype_lower in ("int", "integer", "bigint", "smallint", "tinyint", "long"):
                ftype = "integer"
            elif ftype_lower in ("float", "double", "decimal", "numeric", "number", "money"):
                ftype = "number"
            elif ftype_lower in ("bool", "boolean", "bit"):
                ftype = "boolean"
            elif ftype_lower in ("str", "text", "varchar", "char", "nvarchar", "string", "uuid"):
                ftype = "string"
            elif ftype_lower in ("list", "array", "[]"):
                ftype = "array"
            elif ftype_lower in ("object", "json", "dict", "map"):
                ftype = "object"
            else:
                ftype = "string"

            fields.append(EntityField(
                field_name=name,
                field_type=ftype,
                required=required,
                description=description,
            ))

        return fields

    # ------------------------------------------------------------------
    # OpenAPI extraction
    # ------------------------------------------------------------------

    def build_from_openapi(self, openapi_spec: dict) -> list[EntityCandidate]:
        """Extract entity candidates from an OpenAPI 3.x specification dict.

        Walks ``#/components/schemas``, extracting each schema as an entity.
        Properties are classified as identity / state / amount / quantity
        fields (see :class:`EntityField` for auto-classification rules).
        """
        candidates: list[EntityCandidate] = []

        schemas = openapi_spec.get("components", {}).get("schemas", {})
        if not schemas:
            # Fallback: also check definitions (Swagger 2.0)
            schemas = openapi_spec.get("definitions", {})

        for schema_name, schema_def in schemas.items():
            if not isinstance(schema_def, dict):
                continue

            # Derive a human-readable entity name
            entity_name = schema_def.get("title", schema_name)
            entity_alias = self._to_alias(entity_name)
            description = schema_def.get("description", "")

            # Extract fields from properties
            properties = schema_def.get("properties", {})
            required_list: list[str] = schema_def.get("required", [])

            fields: list[EntityField] = []
            for prop_name, prop_def in properties.items():
                if not isinstance(prop_def, dict):
                    continue

                prop_type = self._resolve_openapi_type(prop_def)
                prop_desc = prop_def.get("description", "")
                is_required = prop_name in required_list
                source_path = f"#/components/schemas/{schema_name}/properties/{prop_name}"

                ef = EntityField(
                    field_name=prop_name,
                    field_type=prop_type,
                    required=is_required,
                    description=prop_desc,
                    source_path=source_path,
                )
                fields.append(ef)

            # Also extract from allOf/oneOf/anyOf composed schemas
            composed_props = self._extract_composed_properties(schema_def, schema_name)
            existing_names = {f.field_name for f in fields}
            for cp in composed_props:
                if cp.field_name not in existing_names:
                    fields.append(cp)
                    existing_names.add(cp.field_name)

            # Compute confidence based on richness of extracted data
            confidence = 0.4  # base
            if fields:
                confidence += 0.2
            if description:
                confidence += 0.1
            if any(f.is_identity for f in fields):
                confidence += 0.1
            if required_list:
                confidence += 0.1
            if schema_def.get("type") == "object":
                confidence += 0.1

            candidates.append(EntityCandidate(
                entity_name=entity_name,
                entity_alias=entity_alias,
                fields=fields,
                source="openapi",
                confidence=min(1.0, confidence),
                metadata={
                    "schema_name": schema_name,
                    "description": description,
                    "field_count": len(fields),
                    "has_identity_field": any(f.is_identity for f in fields),
                    "has_state_field": any(f.is_state for f in fields),
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                },
            ))

        return candidates

    def _resolve_openapi_type(self, prop_def: dict) -> str:
        """Resolve an OpenAPI property definition to a canonical type string.

        Handles ``type``, ``$ref``, ``anyOf``/``oneOf``, and ``format`` hints.
        """
        # Direct type
        if "type" in prop_def:
            raw_type = prop_def["type"]
            if isinstance(raw_type, str):
                return _OPENAPI_TYPE_MAP.get(raw_type.lower(), raw_type.lower())
            if isinstance(raw_type, list):
                # e.g. ["string", "null"] — take the first non-null type
                for t in raw_type:
                    if t != "null":
                        return _OPENAPI_TYPE_MAP.get(t.lower(), t.lower())

        # $ref — resolve inline or mark as object
        if "$ref" in prop_def:
            ref = prop_def["$ref"]
            # Heuristic: if ref ends with a known type suffix, use it
            if any(ref.lower().endswith(t) for t in ("/string", "/integer", "/number")):
                for t in ("string", "integer", "number"):
                    if ref.lower().endswith(f"/{t}"):
                        return t
            return "object"  # referenced schemas are objects by default

        # anyOf / oneOf — take the first type
        for key in ("anyOf", "oneOf"):
            if key in prop_def and isinstance(prop_def[key], list) and prop_def[key]:
                first = prop_def[key][0]
                if isinstance(first, dict):
                    return self._resolve_openapi_type(first)

        # Format-based inference (when type is missing)
        fmt = prop_def.get("format", "").lower()
        if fmt in ("int32", "int64", "integer"):
            return "integer"
        if fmt in ("float", "double", "decimal", "number"):
            return "number"
        if fmt in ("date-time", "date", "time", "email", "uri", "uuid", "byte", "binary"):
            return "string"

        return "string"

    def _extract_composed_properties(
        self, schema_def: dict, schema_name: str
    ) -> list[EntityField]:
        """Extract fields from allOf/oneOf/anyOf composed schemas."""
        fields: list[EntityField] = []
        for key in ("allOf", "oneOf", "anyOf"):
            sub_schemas = schema_def.get(key, [])
            if not isinstance(sub_schemas, list):
                continue
            for sub in sub_schemas:
                if not isinstance(sub, dict):
                    continue
                # Inline properties
                props = sub.get("properties", {})
                required_list = sub.get("required", [])
                for pname, pdef in props.items():
                    if not isinstance(pdef, dict):
                        continue
                    ptype = self._resolve_openapi_type(pdef)
                    fields.append(EntityField(
                        field_name=pname,
                        field_type=ptype,
                        required=pname in required_list,
                        description=pdef.get("description", ""),
                        source_path=f"#/components/schemas/{schema_name}/{key}/properties/{pname}",
                    ))
                # Recursively resolve nested $ref compositions (one level)
                if "$ref" in sub:
                    # We can't resolve external refs here; skip
                    pass
        return fields

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_catalogs(self, *catalogs: list[EntityCandidate]) -> list[EntityCandidate]:
        """Merge multiple entity catalogs with deduplication by entity_alias.

        Rules
        -----
        - Entities with the same ``entity_alias`` are merged into one.
        - ``entity_alias`` similarity uses case-insensitive, punctuation-stripped
          exact match (fuzzy matching could be added later via Levenshtein).
        - Fields are unioned: fields with the same ``field_name`` keep the
          highest-confidence source's data.
        - ``confidence`` is the weighted average of sources, capped at 1.0.
        - ``source`` is set to ``"merged"``.
        """
        merged: dict[str, EntityCandidate] = {}
        source_count: dict[str, int] = {}  # how many catalogs contributed to each alias

        for catalog in catalogs:
            for candidate in catalog:
                alias = candidate.entity_alias.lower().replace("-", "_").strip("_")
                if not alias:
                    continue

                if alias in merged:
                    existing = merged[alias]
                    # Merge fields by name (union, keep first occurrence's details)
                    existing_names = {f.field_name.lower() for f in existing.fields}
                    for f in candidate.fields:
                        if f.field_name.lower() not in existing_names:
                            existing.fields.append(f)
                            existing_names.add(f.field_name.lower())
                    # Weighted confidence averaging
                    n = source_count[alias]
                    existing.confidence = (existing.confidence * n + candidate.confidence) / (n + 1)
                    source_count[alias] = n + 1
                    # Merge metadata
                    existing.metadata.setdefault("merged_sources", []).append(candidate.source)
                    existing.source = "merged"
                else:
                    merged[alias] = EntityCandidate(
                        entity_name=candidate.entity_name,
                        entity_alias=alias,
                        fields=list(candidate.fields),
                        source="merged",
                        confidence=candidate.confidence,
                        metadata={
                            "merged_sources": [candidate.source],
                            "original_entity_name": candidate.entity_name,
                            "merged_at": datetime.now(timezone.utc).isoformat(),
                            **candidate.metadata,
                        },
                    )
                    source_count[alias] = 1

        # Cap all confidences at 1.0
        for c in merged.values():
            c.confidence = min(1.0, c.confidence)

        return sorted(merged.values(), key=lambda c: c.confidence, reverse=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_alias(name: str) -> str:
        """Convert an entity name to a snake_case alias for dedup matching."""
        # Strip markdown formatting
        name = re.sub(r"[*_`]", "", name)
        # Insert underscore before capital letters (PascalCase → snake_case)
        name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
        # Collapse non-alphanumeric to underscore
        name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
        return name.strip("_").lower()
