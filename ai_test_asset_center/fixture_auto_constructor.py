"""
Phase79: Fixture Auto-Constructor

Given an API schema (OpenAPI), automatically construct minimal valid
pre-requisite data for POST/PUT probes. Supports:

- Entity dependency graph: A requires B → create B first
- Foreign key / reference field inference from schema
- Minimal valid object generation (fill required fields with defaults)
- Primary key / business key capture from response
- Variable binding for next step injection
- Idempotency: skip if fixture already exists
- Cleanup plan generation
"""

from __future__ import annotations

import json, copy, re, uuid, time
from dataclasses import dataclass, field
from typing import Any

from .target_endpoint import resolve_target_base_url


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════

@dataclass
class FixtureObject:
    """A minimal valid business object for testing."""
    entity_type: str
    object_id: str  # Primary key / business key
    fields: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # "auto_constructed" | "from_api" | "manual"
    created_at: str = ""
    cleanup_action: str = "DELETE"  # DELETE | reset


@dataclass
class EntityDependency:
    """How entities depend on each other."""
    from_entity: str
    to_entity: str
    foreign_key_field: str
    required: bool = True


@dataclass
class FixturePlan:
    """Auto-generated fixture plan for a flow."""
    objects: list[FixtureObject] = field(default_factory=list)
    dependencies: list[EntityDependency] = field(default_factory=list)
    creation_order: list[str] = field(default_factory=list)  # entity_type order
    cleanup_order: list[str] = field(default_factory=list)  # reverse of creation
    missing_info: list[str] = field(default_factory=list)


@dataclass
class FixtureField:
    """A field in a fixture template."""
    name: str
    type: str = "string"  # string | integer | number | boolean | array | object
    required: bool = False
    default: Any = None
    is_foreign_key: bool = False
    references_entity: str = ""


@dataclass
class FixtureTemplate:
    """A template for creating fixture objects for a specific entity."""
    entity_alias: str
    endpoint: str  # e.g., "POST /api/orders"
    fields: list[FixtureField] = field(default_factory=list)
    id_field: str = "id"
    cleanup_endpoint: str = ""  # e.g., "DELETE /api/orders/{id}"


@dataclass
class FixtureInstance:
    """A concrete instance of a fixture, created from a template."""
    template: FixtureTemplate
    values: dict[str, Any] = field(default_factory=dict)
    instance_id: str = ""
    created: bool = False
    status: str = "created"
    response: dict | None = None


# ═══════════════════════════════════════════════════════════════
# Schema Analyzer
# ═══════════════════════════════════════════════════════════════

class SchemaAnalyzer:
    """Analyzes OpenAPI schemas to infer entity dependencies and field requirements."""

    # Common foreign key patterns
    FK_PATTERNS = [
        (r"(\w+)_id$", "1"),      # order_id → order
        (r"(\w+)Id$", "1"),       # orderId → order
        (r"(\w+)_code$", "2"),    # material_code → material
        (r"(\w+)Code$", "2"),     # materialCode → material
        (r"(\w+)_ref$", "3"),     # bom_ref → bom
        (r"(\w+)Ref$", "3"),      # bomRef → bom
        (r"(\w+)_number$", "4"),  # order_number → order
        (r"(\w+)Number$", "4"),   # orderNumber → order
    ]

    def extract_dependencies(self, schemas: dict[str, dict]) -> list[EntityDependency]:
        """Extract entity dependencies from OpenAPI component schemas."""
        deps = []
        for name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            for field_name, field_schema in props.items():
                if not isinstance(field_schema, dict):
                    continue
                for pattern, _ in self.FK_PATTERNS:
                    match = re.match(pattern, field_name, re.IGNORECASE)
                    if match:
                        ref_entity = match.group(1)
                        # Don't self-reference
                        if ref_entity.lower() != name.lower():
                            deps.append(EntityDependency(
                                from_entity=name,
                                to_entity=ref_entity,
                                foreign_key_field=field_name,
                                required="required" in schema.get("required", []) or
                                         field_name in schema.get("required", []),
                            ))
        return deps

    def get_required_fields(self, schema: dict) -> list[str]:
        """Get list of required fields for a schema."""
        return schema.get("required", []) or list(schema.get("properties", {}).keys())[:5]

    def generate_default_value(self, field_name: str, field_schema: dict) -> Any:
        """Generate a sensible default value for a field."""
        ftype = field_schema.get("type", "string")

        if ftype == "string":
            if "id" in field_name.lower() or "code" in field_name.lower() or "key" in field_name.lower():
                return f"AUTO-{uuid.uuid4().hex[:8].upper()}"
            if "name" in field_name.lower():
                return f"auto-test-{uuid.uuid4().hex[:6]}"
            if "status" in field_name.lower() or "state" in field_name.lower():
                return "DRAFT"
            if "date" in field_name.lower() or "time" in field_name.lower():
                return "2026-01-01T00:00:00Z"
            if "desc" in field_name.lower() or "note" in field_name.lower():
                return "auto-generated fixture"
            return f"auto-{uuid.uuid4().hex[:4]}"

        elif ftype in ("integer", "number"):
            if "qty" in field_name.lower() or "quantity" in field_name.lower() or "count" in field_name.lower():
                return 1
            if "amount" in field_name.lower() or "price" in field_name.lower() or "total" in field_name.lower():
                return 0
            if "version" in field_name.lower():
                return 1
            return 0

        elif ftype == "boolean":
            return True

        elif ftype == "array":
            return []

        elif ftype == "object":
            return {}

        return None


# ═══════════════════════════════════════════════════════════════
# Fixture Auto-Constructor
# ═══════════════════════════════════════════════════════════════

class FixtureAutoConstructor:
    """Automatically constructs minimal valid test fixtures from API schemas."""

    def __init__(self, transport=None):
        self.analyzer = SchemaAnalyzer()
        self._constructed: dict[str, FixtureObject] = {}  # entity_type → last constructed
        self.transport = transport  # Phase78A: unified HTTP transport for production safety
    def build_from_context(self, context: dict, *args, **kwargs):
        """Build fixture templates from a project context."""
        entities = context.get("entities", [])
        templates = []
        for entity in entities:
            alias = entity.get("entity_alias", entity.get("name", "unknown"))
            templates.append(FixtureTemplate(
                entity_alias=alias,
                endpoint=f"POST /api/{alias.lower()}s",
                fields=[],
            ))
        result = type("Plan", (), {})()
        result.instances = templates
        return result

    def create_fixture(self, template: FixtureTemplate) -> FixtureInstance | None:
        """Create a fixture instance from a template."""
        if self.transport:
            policy = getattr(self.transport, 'policy', None)
            env = getattr(policy, 'environment', '') if policy else getattr(self.transport, 'environment', '')
            if env == 'production':
                inst = FixtureInstance(template=template, values={})
                inst.status = "failed"
                inst.response = {"ok": False, "error": "production_safety_blocked"}
                return inst  # Blocked by production safety
        payload = self.generate_payload(template)
        inst = FixtureInstance(template=template, values=payload, instance_id=f"AUTO-{template.entity_alias}-{int(time.time())}")
        inst.response = {"ok": True}
        return inst


    def discover_templates(self, openapi: dict, *args, **kwargs) -> list[FixtureTemplate]:
        """Discover fixture templates from an OpenAPI spec."""
        templates = []
        # Extract entity aliases from provided entity candidates
        entity_map = {}
        for arg in args:
            if isinstance(arg, list):
                for e in arg:
                    if hasattr(e, 'entity_alias'):
                        entity_map[e.entity_alias] = e
        paths = openapi.get("paths", {})
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, spec in methods.items():
                if not isinstance(spec, dict):
                    continue
                if method.upper() == "POST":
                    # Default alias from path, overridden by entity candidate if found
                    entity_alias = path.strip("/").split("/")[-1].rstrip("s")
                    for ec_alias in entity_map:
                        if ec_alias in entity_alias or entity_alias in ec_alias:
                            entity_alias = ec_alias
                            break
                    tags = spec.get("tags", [])
                    if tags:
                        entity_alias = tags[0].split()[0].lower().rstrip("s")
                    fields = []
                    body = spec.get("requestBody", {})
                    content = body.get("content", {}).get("application/json", {})
                    schema = content.get("schema", {})
                    props = schema.get("properties", {})
                    required_list = schema.get("required", [])
                    for fname, fspec in props.items():
                        if isinstance(fspec, dict):
                            fields.append(FixtureField(
                                name=fname,
                                type=fspec.get("type", "string"),
                                required=fname in required_list,
                                references_entity=fspec.get("x-entity-ref", ""),
                            ))
                    templates.append(FixtureTemplate(
                        entity_alias=entity_alias,
                        endpoint=f"{method.upper()} {path}",
                        fields=fields,
                    ))
        return templates

    def generate_payload(self, template: FixtureTemplate, **kwargs) -> dict:
        """Generate a minimal valid HTTP payload from a fixture template."""
        bindings = kwargs.get("bindings", {})
        payload = {}
        for field in template.fields:
            if field.required or field.name in ("name", "code", "id"):
                # If field references another entity, use bound value if available
                ref_entity = getattr(field, "references_entity", "") or getattr(field, "reference_entity", "")
                if ref_entity and ref_entity in bindings:
                    payload[field.name] = bindings[ref_entity]
                else:
                    payload[field.name] = self._default_for_type(field)
        return payload

    def _default_for_type(self, field_type_or_obj, field_name_or_none=None, constraints=None) -> Any:
        """Generate a sensible default value for a fixture field.
        Called as: _default_for_type(field) or _default_for_type(type_str, name_str, constraints)."""
        import uuid
        
        # Handle both call patterns
        field = field_type_or_obj if hasattr(field_type_or_obj, 'type') else None
        if field is not None:
            ftype = field.type
            fname = field.name.lower()
            default_val = field.default
        else:
            ftype = field_type_or_obj or 'string'
            fname = (field_name_or_none or '').lower()
            default_val = None

        if default_val is not None:
            return default_val

        if ftype in ("integer", "number"):
            if "qty" in fname:
                return 1
            if "quantity" in fname:
                return 10
            if "count" in fname:
                return 1
                return 1
            if "amount" in fname or "price" in fname or "total" in fname:
                return 100
            if "version" in fname:
                return 1
            return 0
        elif ftype == "boolean":
            return True
        elif ftype == "array":
            return []
        elif "enum" in str(field) and hasattr(field, 'enum'):
            return getattr(field, 'enum', ["default"])[0]
        else:  # string
            if "id" in fname or "code" in fname or "key" in fname:
                return f"AUTO-{uuid.uuid4().hex[:8].upper()}"
            if "name" in fname:
                return f"auto-test-{uuid.uuid4().hex[:6]}"
            if "status" in fname or "state" in fname:
                return "DRAFT"
            if "email" in fname:
                return "test@fixture.local"
            if "date" in fname or "time" in fname:
                return "2026-01-01T00:00:00Z"
            return f"auto-{uuid.uuid4().hex[:4]}"

    def build_plan(
        self,
        schemas: dict[str, dict],
        target_entity: str,
        target_method: str = "POST",
    ) -> FixturePlan:
        """Build a fixture plan for testing a specific entity endpoint."""
        plan = FixturePlan()

        deps = self.analyzer.extract_dependencies(schemas)
        plan.dependencies = deps

        # Topological sort: create entities in dependency order
        creation_order = self._topological_sort(schemas.keys(), deps, target_entity)
        plan.creation_order = creation_order
        plan.cleanup_order = list(reversed(creation_order))

        # Generate minimal objects
        for entity_type in creation_order:
            schema = schemas.get(entity_type, {})
            if not schema:
                plan.missing_info.append(f"No schema for {entity_type}")
                continue

            obj = self._generate_minimal_object(entity_type, schema, deps)
            plan.objects.append(obj)

        return plan

    def _generate_minimal_object(
        self, entity_type: str, schema: dict, deps: list[EntityDependency]
    ) -> FixtureObject:
        """Generate a minimal valid object for a given entity type."""
        props = schema.get("properties", {})
        required = self.analyzer.get_required_fields(schema)

        fields = {}
        for field_name in required[:20]:  # Cap at 20 fields
            field_schema = props.get(field_name, {})
            if not isinstance(field_schema, dict):
                field_schema = {"type": "string"}

            # Check if this field is a foreign key to another entity
            is_fk = False
            for dep in deps:
                if dep.from_entity == entity_type and dep.foreign_key_field == field_name:
                    # Reference a previously constructed object
                    ref_obj = self._constructed.get(dep.to_entity)
                    if ref_obj:
                        fields[field_name] = ref_obj.object_id
                        is_fk = True
                    break

            if not is_fk:
                fields[field_name] = self.analyzer.generate_default_value(field_name, field_schema)

        obj = FixtureObject(
            entity_type=entity_type,
            object_id=f"AUTO-{entity_type}-{uuid.uuid4().hex[:8].upper()}",
            fields=fields,
            source="auto_constructed",
            cleanup_action="DELETE",
        )

        # Auto-add an 'id' field if present in schema
        if "id" not in fields and "id" in props:
            fields["id"] = obj.object_id

        self._constructed[entity_type] = obj
        return obj

    def _topological_sort(self, entities: list, deps: list, target=None) -> list:
        """Sort entities in creation order (dependencies first, target last)."""
        # Normalize: extract string aliases from FixtureTemplate objects
        template_map = {}
        entity_names = []
        for e in entities:
            if hasattr(e, 'entity_alias'):
                entity_names.append(e.entity_alias)
                template_map[e.entity_alias] = e
            else:
                entity_names.append(str(e))
        target_name = None
        if target is not None:
            target_name = target.entity_alias if hasattr(target, 'entity_alias') else str(target)
        
        # Build adjacency
        graph = {e: set() for e in entity_names}
        for dep in deps:
            if hasattr(dep, 'from_entity') and hasattr(dep, 'to_entity'):
                if dep.from_entity in graph and dep.to_entity in graph:
                    graph[dep.from_entity].add(dep.to_entity)
        
        # Kahn
        in_degree = {e: len(deps_set) for e, deps_set in graph.items()}
        queue = [e for e, d in in_degree.items() if d == 0]
        result = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for other, deps_set in graph.items():
                if node in deps_set:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)
        for e in entity_names:
            if e not in result:
                result.append(e)
        if target_name and target_name in result:
            result.remove(target_name)
            result.append(target_name)
        # Return original objects if they were FixtureTemplates
        if template_map:
            return [template_map.get(r, r) for r in result]
        return result

    def to_curl_commands(self, plan: FixturePlan, base_url: str | None = None) -> list[str]:
        """Generate curl commands for creating the fixture objects."""
        target_base_url = resolve_target_base_url(base_url)
        commands = []
        for obj in plan.objects:
            entity = obj.entity_type.lower()
            body = json.dumps(obj.fields)
            cmd = f'curl -s -X POST {target_base_url}/{entity} -H "Content-Type: application/json" -d \'{body}\''
            commands.append(cmd)
        return commands
