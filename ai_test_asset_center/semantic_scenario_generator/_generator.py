"""SemanticScenarioGenerator: source-grounded scenario planning."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403
from ..auto_test_data_factory import _markdown_request_example
from ..business_state_graph import BusinessStateGraph, StateEdge, StateTransition, _api_facts, behavior_slice_id
from ..real_id_resolver import (
    alternate_collection_paths,
    body_field_collection_paths,
    extract_body_binding_fields,
    extract_fields_for_path,
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
    collection_path,
)
from ._common import _SCENARIO_ENRICHER  # noqa: F401
from ._helpers import _adjacent_read_for_entity, _documented_observation_read_candidates, _observation_read_candidates  # noqa: F401


class SemanticScenarioGenerator:
    """Plan only source-backed obligations selected by the incremental scheduler."""

    def generate(
        self,
        graphs: dict[str, BusinessStateGraph],
        api_doc: str = "",
        active_slice_ids: set[str] | None = None,
        discovery_round: int = 1,
        active_slices: list[dict[str, Any]] | None = None,
        allow_source_runtime: bool = False,
        root: Any = None,
        project: str = "",
    ) -> list[ExecutableScenario]:
        round_number = max(1, int(discovery_round or 1))
        active_slice_map = {
            str(item.get("slice_id") or ""): dict(item)
            for item in active_slices or []
            if isinstance(item, dict) and str(item.get("slice_id") or "")
        }
        results: list[ExecutableScenario] = []
        for entity, graph in sorted((graphs or {}).items()):
            if not isinstance(graph, BusinessStateGraph):
                continue
            for transition in graph.transitions:
                item = self._transition(entity, transition, graph, round_number, api_doc, root, project)
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for edge in graph.edges:
                slice_id = behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation)
                item = self._dependency(
                    entity,
                    edge,
                    round_number,
                    slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                    api_doc=api_doc if allow_source_runtime else "",
                )
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for state, node in graph.states.items():
                for invariant in node.invariants:
                    slice_id = behavior_slice_id("invariant", entity, state, invariant)
                    items = self._invariant(
                        entity,
                        state,
                        invariant,
                        node.source_refs,
                        round_number,
                        slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                        api_doc=api_doc if allow_source_runtime else "",
                        root=root,
                        project=project,
                    )
                    for item in items:
                        if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                            results.append(item)
        if allow_source_runtime:
            for item in self._source_observations(api_doc, round_number):
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
        emitted_slice_ids = {item.behavior_slice_id for item in results if str(item.behavior_slice_id or "").strip()}
        for slice_id, slice_meta in active_slice_map.items():
            if slice_id in emitted_slice_ids:
                continue
            item = self._fallback_active_slice(
                slice_meta,
                round_number,
                api_doc if allow_source_runtime else "",
                allow_source_runtime=allow_source_runtime,
                root=root,
                project=project,
            )
            if item is None:
                continue
            if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                results.append(item)
        deduped: list[ExecutableScenario] = []
        seen: set[str] = set()
        for item in results:
            fingerprint = f"{item.behavior_slice_id}|{item.entity}|{item.title}|{item.expected_state}"
            if fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(item)
        return deduped

    def _source_observations(self, api_doc: str, discovery_round: int) -> list[ExecutableScenario]:
        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in endpoints:
            if str(item.get("method") or "").upper() not in {"GET", "HEAD", "OPTIONS"}:
                continue
            entity = str(item.get("entity") or "")
            path = str(item.get("path") or "")
            if entity and path.startswith("/"):
                grouped.setdefault(entity, []).append(item)
        results: list[ExecutableScenario] = []
        for entity, items in sorted(grouped.items()):
            paths = list(dict.fromkeys(str(item.get("path") or "") for item in items if str(item.get("path") or "")))
            if not paths:
                continue
            first = paths[0]
            results.append(ExecutableScenario(
                id=self._id(entity, "source_observation", first),
                title=f"[Source observation] {entity}: {first}",
                description="Read-only source-bound endpoint observation for runtime evidence capture.",
                category="source_observation",
                severity="P2",
                entity=entity,
                steps=[ScenarioStep(order=1, action="observe_source_endpoint", api_method="GET", api_path=first, expected_status=200, actor="readonly")],
                oracle_rules=["RuntimeObservation.source_endpoint_reachable"],
                confidence=0.5,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=[{"source_type": "openapi", "locator": first, "quote": first}],
                behavior_slice_id=behavior_slice_id("source_observation", entity, ",".join(paths)),
                behavior_slice_kind="source_observation",
                discovery_round=discovery_round,
            ))
        return results

    def _fallback_active_slice(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
        allow_source_runtime: bool = True,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        kind = str(slice_meta.get("kind") or "").strip().lower()
        if kind == "source_observation":
            item = self._source_observation_from_meta(slice_meta, discovery_round)
        elif kind == "invariant":
            item = self._invariant_from_meta(slice_meta, discovery_round, api_doc, root=root, project=project)
        elif kind == "permission":
            item = self._permission_slice(
                slice_meta,
                discovery_round,
                api_doc,
                root=root,
                project=project,
            )
        elif kind == "isolation":
            item = self._isolation_slice(
                slice_meta,
                discovery_round,
                api_doc,
                root=root,
                project=project,
            )
        elif kind == "concurrency":
            item = self._concurrency_slice(slice_meta, discovery_round, api_doc, root=root, project=project)
        elif kind == "money":
            item = self._money_slice(slice_meta, discovery_round, api_doc, root=root, project=project)
        elif kind == "inventory":
            item = self._inventory_slice(
                slice_meta,
                discovery_round,
                api_doc,
                root=root,
                project=project,
            )
        elif kind == "account_status":
            item = self._account_status_slice(slice_meta, discovery_round)
        else:
            return None
        if item is None or allow_source_runtime:
            return item
        # Plan-only intent: the runtime contract is not approved, so the
        # source-grounded coverage metadata is preserved but the executable
        # steps are stripped — otherwise the scenario would be miscounted as an
        # executed probe. This keeps the planning/execution boundary honest.
        #
        # Exception: system behavior space scenarios carry an authoritative
        # execution_policy determined by the slice metadata (safe_read_only when
        # a source-bound GET/HEAD/OPTIONS route exists, plan_only otherwise).
        # The enrichment already strips steps for plan_only promises, so
        # overriding it here would lose the safe_read_only decision.
        if getattr(item, "selection_origin", "") == "system_behavior_space":
            return item
        item.steps = []
        item.cleanup_steps = []
        item.execution_policy = "plan_only_requires_fixture"
        item.actor_token = ""
        if "RUNTIME_CONTRACT_NOT_APPROVED" not in item.evidence_gaps:
            item.evidence_gaps = list(item.evidence_gaps) + ["RUNTIME_CONTRACT_NOT_APPROVED"]
        return item

    def _source_observation_from_meta(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
    ) -> ExecutableScenario | None:
        entity = str(slice_meta.get("entity") or "").strip()
        endpoints = [str(item or "").strip() for item in (slice_meta.get("endpoints") or []) if str(item or "").strip()]
        if not entity or not endpoints:
            return None
        first = self._preferred_read_endpoint(endpoints) or endpoints[0]
        if not first.startswith("/"):
            return None
        return ExecutableScenario(
            id=self._id(entity, "source_observation", first),
            title=f"[Source observation] {entity}: {first}",
            description="Read-only source-bound endpoint observation for runtime evidence capture.",
            category="source_observation",
            severity="P2",
            entity=entity,
            steps=[ScenarioStep(order=1, action="observe_source_endpoint", api_method="GET", api_path=first, expected_status=200, actor="readonly")],
            oracle_rules=["RuntimeObservation.source_endpoint_reachable"],
            confidence=float(slice_meta.get("priority") or 0.45),
            execution_policy="safe_read_only",
            evidence_gaps=[str(item) for item in (slice_meta.get("evidence_gaps") or []) if str(item).strip()],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or []) if isinstance(item, dict)],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="source_observation",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    def _invariant_from_meta(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        def _finish(item: ExecutableScenario | None) -> ExecutableScenario | None:
            if _SCENARIO_ENRICHER is None:
                return item
            return _SCENARIO_ENRICHER(item, slice_meta, discovery_round, api_doc=api_doc)

        entity = str(slice_meta.get("entity") or "").strip()
        refs = [dict(item) for item in (slice_meta.get("source_refs") or []) if isinstance(item, dict)]
        states = [str(item or "").strip() for item in (slice_meta.get("states") or []) if str(item or "").strip()]
        invariant = self._slice_meta_invariant_text(slice_meta)
        observation_path = self._preferred_read_endpoint(list(slice_meta.get("endpoints") or []))
        if not entity:
            return _finish(None)

        # ── System Behavior Space slice: generate a dimension-aware verification
        # scenario instead of a generic single-GET observation. The slice carries
        # structured dimensions (authorization_access_control, tenant_isolation,
        # money_quantity_conservation, state_machine, audit_traceability, etc.)
        # and surface plans that the generic invariant path ignores.
        is_system_behavior = str(slice_meta.get("_selection_origin") or "") == "system_behavior_space"
        sb_dimensions: list[str] = []
        sb_raw = slice_meta.get("_system_behavior_dimensions")
        if isinstance(sb_raw, list):
            sb_dimensions = [str(d) for d in sb_raw if str(d)]
        if (is_system_behavior or sb_dimensions) and observation_path:
            return _finish(self._build_system_promise_invariant_scenario(
                entity=entity,
                invariant=invariant,
                slice_meta=slice_meta,
                discovery_round=discovery_round,
                api_doc=api_doc,
                observation_path=observation_path,
                refs=refs,
                states=states,
            ))

        runtime_upgrade = self._invariant_runtime_upgrade(
            entity,
            states[0] if states else "",
            invariant,
            refs,
            discovery_round,
            slice_id=str(slice_meta.get("slice_id") or ""),
            observation_path=observation_path,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        if runtime_upgrade is not None:
            return _finish(runtime_upgrade)
        bound_method = str(slice_meta.get("_bound_method") or "").strip().upper()
        bound_path = str(slice_meta.get("_bound_path") or "").strip()
        if (
            bound_method in {"POST", "PUT", "PATCH", "DELETE"}
            and bound_path.startswith("/")
            # A separate documented GET observation can still support a
            # source-bound mutation scenario (for example a dependency write
            # whose state is observed through /orders).  The dangerous case is
            # specifically when the generic fallback has no read surface and
            # would turn the mutation path itself into GET.
            and str(observation_path or "").rstrip("/") == bound_path.rstrip("/")
        ):
            family = str(slice_meta.get("_hypothesis_family") or "").strip().lower()
            if family in {"idempotency", "idempotent", "duplicate_submit"}:
                bound_scenario = self._bound_idempotency_scenario(
                    slice_meta=slice_meta,
                    entity=entity,
                    invariant=invariant,
                    refs=refs,
                    discovery_round=discovery_round,
                    api_doc=api_doc,
                    root=root,
                    project=project,
                    method=bound_method,
                    path=bound_path,
                )
                if bound_scenario is not None:
                    return _finish(bound_scenario)
            # Other source-bound mutation hypotheses may still be executable
            # when their family does not claim a lifecycle precondition. Build
            # the documented method/body directly instead of silently turning
            # the route into GET.
            #
            # SPC reach fix: previously state/lifecycle families skipped
            # `_bound_write_scenario` and always fell through to empty
            # plan_only. That left selected POST routes (e.g. password/reset,
            # admin user status) never HTTP-executed even when the documented
            # method/body could be materialized. Prefer documented-method
            # execution when materialization succeeds; only keep plan_only
            # when `_bound_write_scenario` cannot build steps (never degrade
            # to GET of the mutation path).
            bound_scenario = self._bound_write_scenario(
                slice_meta=slice_meta,
                entity=entity,
                invariant=invariant,
                refs=refs,
                discovery_round=discovery_round,
                api_doc=api_doc,
                root=root,
                project=project,
                method=bound_method,
                path=bound_path,
            )
            if bound_scenario is not None:
                return _finish(bound_scenario)
            # Never convert a source-bound mutation hypothesis into a GET of the
            # same action path. Without a materialized write/precondition
            # contract that changes both the method and the tested behavior and
            # can produce false customer findings (for example Cannot GET 404).
            return _finish(ExecutableScenario(
                id=self._id(entity, "bound_write_precondition_missing", bound_method, bound_path),
                title=f"[Bound write plan gap] {entity}: {bound_method} {bound_path}",
                description=invariant[:300],
                category="invariant",
                severity="P1",
                entity=entity,
                preconditions=["source-bound mutation requires executable state/body/actor prerequisites"],
                actors=[],
                steps=[],
                oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
                confidence=max(float(slice_meta.get("priority") or 0.0), 0.4),
                execution_policy="plan_only_requires_fixture",
                evidence_gaps=["BOUND_WRITE_PRECONDITION_CONTRACT_MISSING"],
                source_refs=refs,
                behavior_slice_id=str(slice_meta.get("slice_id") or ""),
                behavior_slice_kind="invariant",
                discovery_round=discovery_round,
                selection_origin="active_slice_fallback_materialized",
            ))
        state_or_rule = states[0] if states else invariant[:120]
        steps: list[ScenarioStep] = []
        observation_path = str(observation_path or "")
        if observation_path and path_has_placeholders(normalize_path_placeholders(observation_path)):
            resolve_steps, observation_path = self._resolve_entity_steps(
                observation_path,
                actor="readonly",
                start_order=1,
                api_doc=api_doc,
                root=root,
                project=project,
            )
            steps.extend(resolve_steps)
        if observation_path:
            steps.append(ScenarioStep(
                order=len(steps) + 1,
                action="observe_bound_entity",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor="readonly",
            ))
            return _finish(ExecutableScenario(
                id=self._id(entity, state_or_rule or "invariant"),
                title=f"[来源约束不变量] {entity}: {state_or_rule}",
                description=invariant[:300],
                category="invariant",
                severity="P1",
                entity=entity,
                preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
                actors=["readonly"],
                steps=steps,
                expected_state=states[0] if states else "",
                oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
                confidence=max(float(slice_meta.get("priority") or 0.0), 0.55 if refs else 0.3),
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=refs,
                behavior_slice_id=str(slice_meta.get("slice_id") or ""),
                behavior_slice_kind="invariant",
                discovery_round=discovery_round,
                selection_origin="active_slice_fallback_materialized",
            ))
        return _finish(None)

    # ── Section: 幂等 & 写操作场景 ──

    def _bound_idempotency_scenario(
        self,
        *,
        slice_meta: dict[str, Any],
        entity: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        api_doc: str,
        root: Any = None,
        project: str = "",
        method: str,
        path: str,
    ) -> ExecutableScenario | None:
        """Materialize the exact documented mutation twice for idempotency.

        The hypothesis bridge already bound method+path to the source catalog.
        Revalidate that binding here so scenario generation cannot execute a
        path introduced only by hypothesis text.
        """
        normalized_path = normalize_path_placeholders(path)
        _, _, endpoints = _api_facts(
            api_doc,
            re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I),
        )

        def shape(value: str) -> str:
            normalized = normalize_path_placeholders(value)
            return re.sub(r"\{[A-Za-z_]\w*\}", "{id}", normalized).rstrip("/")

        source_endpoint = next(
            (
                item
                for item in endpoints
                if str(item.get("method") or "").strip().upper() == method
                and shape(str(item.get("path") or "")) == shape(normalized_path)
            ),
            None,
        )
        if source_endpoint is None:
            return None
        source_path = normalize_path_placeholders(str(source_endpoint.get("path") or normalized_path))
        actor = str(
            slice_meta.get("_permission_actor")
            or slice_meta.get("_default_actor")
            or "authenticated"
        ).strip()
        steps, resolved_path = self._resolve_entity_steps(
            source_path,
            actor=actor,
            start_order=1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        if method == "POST":
            body, body_provenance = self._bootstrap_create_body_with_provenance(
                api_doc,
                source_path,
                root=root,
                project=project,
            )
        else:
            body, body_provenance = self._runtime_body_template_with_provenance(
                api_doc,
                method,
                source_path,
                root=root,
                project=project,
            )
        if not body and (
            method in {"PUT", "PATCH"}
            or (method == "POST" and not path_has_placeholders(source_path))
        ):
            return None
        body_steps, _ = self._body_binding_resolve_steps(
            body,
            actor=actor,
            start_order=len(steps) + 1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.extend(body_steps)
        first_order = len(steps) + 1
        steps.append(ScenarioStep(
            order=first_order,
            action="execute_bound_idempotency_write",
            api_method=method,
            api_path=resolved_path,
            body_template=dict(body),
            body_provenance=body_provenance,
            expected_status=200,
            actor=actor,
        ))
        steps.append(ScenarioStep(
            order=first_order + 1,
            action="repeat_bound_idempotency_write",
            api_method=method,
            api_path=resolved_path,
            body_template=dict(body),
            body_provenance=body_provenance,
            expected_status=200,
            actor=actor,
        ))
        observation_path = next(
            (
                str(step.api_path)
                for step in steps
                if step.api_method == "GET" and str(step.api_path).startswith("/")
            ),
            "",
        )
        if observation_path:
            steps.append(ScenarioStep(
                order=len(steps) + 1,
                action="observe_after_bound_idempotency_write",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor=actor,
            ))
        return ExecutableScenario(
            id=self._id(entity, "bound_idempotency", method, source_path, invariant),
            title=f"[Source-bound idempotency] {entity}: repeat {method} {source_path}",
            description=invariant[:300],
            category="concurrency",
            severity="P1",
            entity=entity,
            preconditions=[f"bind source prerequisites for {method} {source_path}"],
            actors=[actor],
            steps=steps,
            oracle_rules=[
                "IdempotencyOracle.duplicate_submit",
                "ConsistencyOracle.source_grounded_invariant",
                invariant[:300],
            ],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.65),
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=refs,
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    def _bound_write_scenario(
        self,
        *,
        slice_meta: dict[str, Any],
        entity: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        api_doc: str,
        root: Any = None,
        project: str = "",
        method: str,
        path: str,
    ) -> ExecutableScenario | None:
        """Materialize a source-bound mutation once.

        This path is deliberately conservative about semantics but exact about
        the source contract: method, route shape and request body come from the
        documented endpoint.  It is used for cache, tenant, audit, generic
        consistency, and lifecycle-tagged hypotheses when a dedicated state
        fixture upgrade is unavailable.  If the documented endpoint cannot be
        resolved from api_doc, callers must keep the slice plan-only rather
        than degrading the mutation into a GET of the same path.
        """
        normalized_path = normalize_path_placeholders(path)
        _, _, endpoints = _api_facts(
            api_doc,
            re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I),
        )

        def shape(value: str) -> str:
            normalized = normalize_path_placeholders(value)
            return re.sub(r"\{[A-Za-z_]\w*\}", "{id}", normalized).rstrip("/")

        source_endpoint = next(
            (
                item
                for item in endpoints
                if str(item.get("method") or "").strip().upper() == method
                and shape(str(item.get("path") or "")) == shape(normalized_path)
            ),
            None,
        )
        if source_endpoint is None:
            return None
        source_path = normalize_path_placeholders(str(source_endpoint.get("path") or normalized_path))
        actor = str(
            slice_meta.get("_default_actor")
            or slice_meta.get("_permission_actor")
            or "readonly"
        ).strip()
        resolve_steps, resolved_path = self._resolve_entity_steps(
            source_path,
            actor=actor,
            start_order=1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        if method == "POST":
            body, body_provenance = self._bootstrap_create_body_with_provenance(
                api_doc,
                source_path,
                root=root,
                project=project,
            )
        else:
            body, body_provenance = self._runtime_body_template_with_provenance(
                api_doc,
                method,
                source_path,
                root=root,
                project=project,
            )
        if not body and (
            method in {"PUT", "PATCH"}
            or (method == "POST" and not path_has_placeholders(source_path))
        ):
            return None
        body_steps, _ = self._body_binding_resolve_steps(
            body,
            actor=actor,
            start_order=len(resolve_steps) + 1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps = [*resolve_steps, *body_steps]
        steps.append(ScenarioStep(
            order=len(steps) + 1,
            action="execute_bound_write",
            api_method=method,
            api_path=resolved_path,
            body_template=dict(body),
            body_provenance=body_provenance,
            expected_status=200,
            actor=actor,
        ))
        observation_path = next(
            (
                str(step.api_path)
                for step in resolve_steps
                if step.api_method in {"GET", "HEAD"} and str(step.api_path).startswith("/")
            ),
            "",
        )
        if observation_path and observation_path != resolved_path:
            steps.append(ScenarioStep(
                order=len(steps) + 1,
                action="observe_after_bound_write",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor=actor,
            ))
        return ExecutableScenario(
            id=self._id(entity, "bound_write", method, source_path, invariant),
            title=f"[Source-bound write] {entity}: {method} {source_path}",
            description=invariant[:300],
            category="invariant",
            severity="P1",
            entity=entity,
            preconditions=[f"source-bound mutation contract for {method} {source_path}"],
            actors=[actor],
            steps=steps,
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.55),
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=refs,
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    # ── Section: 系统承诺不变量 ──

    def _build_system_promise_invariant_scenario(
        self,
        *,
        entity: str,
        invariant: str,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
        observation_path: str,
        refs: list[dict[str, str]],
        states: list[str],
    ) -> ExecutableScenario:
        """Build a dimension-aware, multi-step verification scenario for a system promise.

        Unlike the generic invariant path which produces a single ``GET /path`` step,
        this method translates each system-behavior dimension into concrete
        verification steps that embody the business risk. A promise with
        ``authorization_access_control`` gets an authorization-boundary step;
        one with ``money_quantity_conservation`` gets pre/post observation bookends;
        one with ``audit_traceability`` gets trace_id extraction.

        No hardcoded domain logic — every decision is driven by the dimension
        tokens already present in the slice metadata.
        """
        sb_dimensions: list[str] = []
        sb_raw = slice_meta.get("_system_behavior_dimensions")
        if isinstance(sb_raw, list):
            sb_dimensions = [str(d).lower().replace("-", "_").replace(" ", "_") for d in sb_raw if str(d)]

        sb_surfaces: list[str] = []
        sb_surfaces_raw = slice_meta.get("_system_behavior_surface_plan")
        if isinstance(sb_surfaces_raw, list):
            sb_surfaces = [str(s).lower() for s in sb_surfaces_raw if str(s)]

        api_routes: list[dict[str, str]] = []
        sb_routes = slice_meta.get("_system_behavior_api_routes")
        if isinstance(sb_routes, list):
            api_routes = [dict(r) for r in sb_routes if isinstance(r, dict)]

        slices = [str(s).strip().upper() for s in states if str(s).strip()]

        steps: list[ScenarioStep] = []
        oracle_rules: list[str] = ["SystemPromiseOracle.open_ended_promise_violation"]
        preconditions: list[str] = [f"系统行为承诺：{invariant[:200]}"]
        evidence_gaps: list[str] = []
        extract_fields: list[str] = ["id", "status", "state", "total_amount", "amount", "tenant_id", "trace_id"]
        order = 1

        # ── For each dimension, add verification structure ──

        # 1. Authorization / role boundary: the primary step is an auth-aware observe
        auth_related = any(d in sb_dimensions for d in (
            "authorization_access_control", "permission_boundary", "role", "authorization",
        ))
        tenant_related = any(d in sb_dimensions for d in (
            "tenant_isolation", "tenant",
        ))
        money_related = any(d in sb_dimensions for d in (
            "money_quantity_conservation", "money", "quantity", "conservation", "data_conservation",
        ))
        state_related = any(d in sb_dimensions for d in (
            "state_machine", "lifecycle", "state", "transition",
        ))
        audit_related = any(d in sb_dimensions for d in (
            "audit_traceability", "audit", "traceability",
        ))

        # ── Step 1: Primary observation (dimension-aware) ──
        if auth_related:
            action = "verify_authorization_boundary"
            preconditions.append("验证目标：非授权角色不能访问受限资源，授权角色应返回正确数据")
            oracle_rules.append(f"SystemPromiseOracle.dimension:authorization_access_control")
            # For auth-bound scenarios, we mark the step as expecting either 200 (authorized)
            # or 401/403 (unauthorized), letting the oracle decide based on actor context
            expected_status = 200
        elif tenant_related:
            action = "verify_tenant_isolation_boundary"
            preconditions.append("验证目标：租户A不能看到租户B的数据")
            oracle_rules.append("SystemPromiseOracle.dimension:tenant_isolation")
            expected_status = 200
        elif money_related:
            action = "observe_conservation_baseline"
            preconditions.append("验证目标：金额/库存必须在操作前后保持守恒，不可出现负值")
            oracle_rules.append("SystemPromiseOracle.dimension:money_quantity_conservation")
            expected_status = 200
        elif state_related:
            action = "verify_state_transition_legality"
            preconditions.append("验证目标：状态流转必须合法，终态不可逆，非法流转必须被拒绝")
            oracle_rules.append("SystemPromiseOracle.dimension:state_machine")
            expected_status = 200
        elif audit_related:
            action = "verify_audit_trail_presence"
            preconditions.append("验证目标：业务变更必须有审计日志，trace_id 不能缺失")
            oracle_rules.append("SystemPromiseOracle.dimension:audit_traceability")
            expected_status = 200
        else:
            action = "observe_system_promise_surface"
            expected_status = 200

        # Always add the primary observe step
        steps.append(ScenarioStep(
            order=order,
            action=action,
            api_method="GET",
            api_path=observation_path,
            expected_status=expected_status,
            actor="readonly",
            extract_from_response=list(extract_fields),
        ))
        order += 1

        # ── Step 2: Cross-surface evidence collection (when surface_plan demands it) ──
        if "log" in sb_surfaces and audit_related:
            # Try to find an audit/log endpoint from the API doc
            from ai_test_asset_center.business_state_graph import _api_facts as _sb_api_facts
            import re as _sb_re
            _, _, all_endpoints = _sb_api_facts(
                api_doc,
                _sb_re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", _sb_re.I),
            )
            audit_endpoints = [
                ep for ep in all_endpoints
                if str(ep.get("path") or "").startswith("/")
                and str(ep.get("method") or "").upper() == "GET"
                and any(tok in str(ep.get("path") or "").lower() for tok in ("audit", "log", "trace", "journal"))
            ]
            if audit_endpoints:
                audit_ep = audit_endpoints[0]
                steps.append(ScenarioStep(
                    order=order,
                    action="verify_audit_log_evidence",
                    api_method=audit_ep.get("method", "GET"),
                    api_path=audit_ep.get("path", ""),
                    expected_status=200,
                    actor="readonly",
                    extract_from_response=["trace_id", "correlation_id", "operation", "timestamp"],
                ))
                order += 1
                oracle_rules.append("SystemPromiseOracle.dimension:cross_surface_consistency")
            else:
                evidence_gaps.append("AUDIT_LOG_ENDPOINT_NOT_SOURCE_BOUND")

        # ── Step 2b: DB evidence surface (demand, not execute) ──
        if "db" in sb_surfaces:
            preconditions.append("证据面要求：需要数据库快照进行跨面一致性对比")
            evidence_gaps.append("DB_SNAPSHOT_REQUIRED_FOR_CROSS_SURFACE_CONSISTENCY")

        # ── Step 2c: UI evidence surface (demand, not execute) ──
        if "ui" in sb_surfaces:
            preconditions.append("证据面要求：需要 UI 页面截图进行 UI/API 一致性对比")
            evidence_gaps.append("UI_SCREENSHOT_REQUIRED_FOR_CROSS_SURFACE_CONSISTENCY")

        # ── Step 3: Pre-write observation for money/conservation scenarios ──
        # When we have write routes and money dimensions, we want a before/after pattern.
        # But since this is safe_read_only by default, we record the intent rather
        # than generating write steps (the patch's _enrich_system_behavior_scenario
        # handles write upgrades when QUALIBUG_ALLOW_TEST_WRITE is set).
        if money_related and observation_path:
            # Add a note that write operations need before/after observation
            preconditions.append("守恒验证需要写操作前后的对比观察（需要 QUALIBUG_ALLOW_TEST_WRITE 开启写权限）")
            # Add a second observe step pointing to a related entity if available
            related_endpoints = [
                ep.get("path", "") for ep in api_routes
                if isinstance(ep, dict) and str(ep.get("path") or "").startswith("/")
                and str(ep.get("method") or "").upper() == "GET"
                and str(ep.get("path") or "") != observation_path
            ]
            if related_endpoints:
                steps.append(ScenarioStep(
                    order=order,
                    action="observe_related_entity_for_conservation",
                    api_method="GET",
                    api_path=related_endpoints[0],
                    expected_status=200,
                    actor="readonly",
                    extract_from_response=["id", "amount", "total_amount", "status"],
                ))
                order += 1

        # ── Build the title ──
        dim_label_map = {
            "authorization_access_control": "角色权限",
            "permission_boundary": "权限边界",
            "tenant_isolation": "租户隔离",
            "tenant": "租户隔离",
            "money_quantity_conservation": "金额守恒",
            "money": "金额守恒",
            "quantity": "库存守恒",
            "conservation": "守恒约束",
            "data_conservation": "守恒约束",
            "state_machine": "状态流转",
            "lifecycle": "状态流转",
            "state": "状态流转",
            "audit_traceability": "审计追溯",
            "audit": "审计追溯",
            "cross_surface_consistency": "跨面一致",
            "data_consistency": "数据一致",
            "ui_api_contract": "UI/API契约",
            "idempotency": "幂等性",
            "async_eventual_consistency": "异步一致",
            "async_event": "异步一致",
            "visibility_disclosure": "可见性",
            "visibility": "可见性",
        }
        dim_labels: list[str] = []
        for d in sb_dimensions:
            label = dim_label_map.get(d, "")
            if label and label not in dim_labels:
                dim_labels.append(label)
        dim_suffix = (" [" + " | ".join(dim_labels[:4]) + "]") if dim_labels else ""

        title_entity = entity.replace("_", " ").title() if "_" in entity else entity
        title = f"[System Promise 验证] {title_entity}{dim_suffix}"

        # ── Build description ──
        desc_parts: list[str] = [f"验证对象：{entity}"]
        if auth_related:
            desc_parts.append("验证方向：反向验证 — 确认非授权角色被正确拒绝")
        elif tenant_related:
            desc_parts.append("验证方向：反向验证 — 确认跨租户数据隔离")
        else:
            desc_parts.append("验证方向：正向验证 — 确认系统遵守业务承诺")
        if money_related:
            desc_parts.append("约束：金额/库存必须守恒、非负")
        if state_related:
            desc_parts.append("约束：状态流转必须合法")
        if audit_related:
            desc_parts.append("约束：业务操作必须有审计追溯")
        if sb_surfaces:
            desc_parts.append("证据面：" + " + ".join(sb_surfaces))

        # ── Build verification intent struct for downstream consumption ──
        vi_direction = "反向验证 — 确认非授权角色被正确拒绝" if auth_related else (
            "正向验证 — 确认系统遵守业务承诺"
        )
        vi_roles = ["non_privileged_actor", "privileged_actor"] if auth_related else ["readonly"]
        vi_tenant = "跨租户访问必须被隔离" if tenant_related else None

        verification_intent = {
            "verification_direction": vi_direction,
            "roles_involved": vi_roles,
            "tenant_boundary": vi_tenant,
            "conservation_constraints": (
                ["金额/库存必须在操作前后保持守恒", "不能出现负金额/负库存"]
                if money_related else []
            ),
            "state_constraints": (
                ["终态不可逆", "非法状态流转必须被拒绝"]
                if state_related else []
            ),
            "audit_constraints": (
                ["业务变更必须产生审计记录", "缺少 trace_id / correlation_id 视为审计缺失"]
                if audit_related else []
            ),
            "cross_surface_checks": [
                "API 和 DB 之间的状态必须一致",
                *(("UI 可见数据必须与 API 授权结果一致",) if "ui" in sb_surfaces else ()),
            ],
            "async_constraints": [],
            "evidence_surfaces": [
                *(("API 响应体（状态码、字段值）",) if "api" in sb_surfaces else ()),
                *(("数据库快照（表数据一致性）",) if "db" in sb_surfaces else ()),
                *(("UI 页面（按钮可见性、数据显示）",) if "ui" in sb_surfaces else ()),
                *(("鉴权结果（401/403 vs 200）",) if "auth" in sb_surfaces else ()),
                *(("审计日志（trace_id、操作记录）",) if "log" in sb_surfaces else ()),
            ],
            "verification_steps": [f"#{s.order} {s.action}: {s.api_method} {s.api_path}" for s in steps],
        }

        return ExecutableScenario(
            id=self._id(entity, "system_promise", discovery_round, *states),
            title=title,
            description="；".join(desc_parts),
            category="system_promise",
            severity="P1",
            entity=entity,
            preconditions=preconditions,
            actors=["readonly"],
            steps=steps,
            expected_state=states[0] if states else "",
            oracle_rules=oracle_rules,
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.60 if refs else 0.40),
            execution_policy="safe_read_only",
            evidence_gaps=evidence_gaps,
            source_refs=refs,
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="system_promise",
            discovery_round=discovery_round,
            selection_origin="system_behavior_space",
            runtime_hints={"system_promise_verification_intent": verification_intent},
        )

    @staticmethod
    def _slice_meta_invariant_text(slice_meta: dict[str, Any]) -> str:
        for ref in slice_meta.get("source_refs") or []:
            if not isinstance(ref, dict):
                continue
            quote = str(ref.get("quote") or "").strip()
            if quote:
                return quote
        states = [str(item or "").strip() for item in (slice_meta.get("states") or []) if str(item or "").strip()]
        if states:
            return states[0]
        return str(slice_meta.get("entity") or "source_invariant")

    # ── Section: 状态转换 & 认证 ──

    def _transition(
        self,
        entity: str,
        transition: StateTransition,
        graph: "BusinessStateGraph | None",
        discovery_round: int,
        api_doc: str = "",
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario:
        """Turn a source-bound state transition into an EXECUTABLE scenario.

        Previously this emitted a step-less "plan only" scenario, so routed
        transitions never ran and the StateOracle short-circuited to pass. Now we
        build real steps: login (generic account) -> create an entity in its
        initial state -> drive it toward ``from_state`` when needed -> apply the
        transition's action endpoint -> observe the resulting state. No hardcoded
        role, path, body field or entity name; everything is derived from the
        endpoint catalog and the documented request examples.
        """
        forbidden = bool(transition.is_forbidden)
        kind = "禁止流转" if forbidden else ("边界流转" if transition.is_boundary else "状态流转")
        slice_id = transition.behavior_slice_id or behavior_slice_id(
            "transition",
            entity,
            transition.from_state,
            transition.to_state,
            transition.action,
            transition.api_endpoint,
            "forbidden" if forbidden else "normal",
        )
        # Unroutable transition: honest plan-only coverage gap. Never pretends to
        # execute (that would be a symptom patch on top of a structural miss).
        if not transition.action or not transition.api_endpoint:
            return ExecutableScenario(
                id=self._id(entity, transition.from_state, transition.to_state, transition.action),
                title=f"[来源约束{kind}] {entity}: {transition.from_state} -> {transition.to_state}",
                description="未解析到可执行端点：仅记录覆盖缺口，不自动发起请求。",
                category="state_machine",
                severity="P0" if forbidden else "P2",
                entity=entity,
                preconditions=[f"已通过可追溯数据证明 {entity} 处于 {transition.from_state}"],
                expected_state=transition.from_state if forbidden else transition.to_state,
                oracle_rules=["StateOracle.source_grounded_transition", f"{transition.from_state}->{transition.to_state}"],
                is_forbidden_path=forbidden,
                is_boundary_path=bool(transition.is_boundary),
                confidence=0.2,
                execution_policy="plan_only_requires_fixture",
                evidence_gaps=["ACTION_ROUTE_NOT_SOURCE_BOUND"],
                source_refs=list(transition.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="transition",
                discovery_round=discovery_round,
            )
        _, _, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        declared_get_paths = list(dict.fromkeys(
            normalize_path_placeholders(str(item.get("path") or ""))
            for item in endpoints
            if str(item.get("method") or "").upper() == "GET"
            and str(item.get("path") or "").startswith("/")
        ))
        steps: list[ScenarioStep] = []
        order = 1
        gaps: list[str] = []
        # 1) Authenticate with a generic account (settings -> test_accounts).
        role, email, password, login_path = self._generic_transition_auth(api_doc, root, project)
        if login_path and email and password:
            login_body = _markdown_request_example(api_doc, "POST", login_path)
            if not isinstance(login_body, dict) or not login_body:
                login_body = {"email": email, "password": password}
            ls = self._build_login_step(login_path, login_body, email, password, order=order)
            if ls is not None:
                steps.append(ls)
                order += 1
        # 2) Create an entity instance (lands in its initial state).
        create_ep = self._entity_create_endpoint(entity, endpoints)
        if create_ep:
            cpath, cmethod = create_ep
            if cmethod == "POST":
                cbody, cbody_provenance = self._bootstrap_create_body_with_provenance(
                    api_doc,
                    cpath,
                    root=root,
                    project=project,
                )
            else:
                cbody, cbody_provenance = self._runtime_body_template_with_provenance(
                    api_doc,
                    cmethod,
                    cpath,
                    root=root,
                    project=project,
                )
            bind_steps, _ = self._body_binding_resolve_steps(
                cbody,
                actor=role or "readonly",
                start_order=order,
                api_doc=api_doc,
                root=root,
                project=project,
            )
            steps.extend(bind_steps)
            order += len(bind_steps)
            steps.append(ScenarioStep(
                order=order,
                action="create_entity",
                api_method=cmethod,
                api_path=cpath,
                body_template=cbody if isinstance(cbody, dict) else {},
                body_provenance=cbody_provenance,
                expected_status=200,
                actor=role,
                extract_from_response=["id"],  # generic: runtime discovers entity-specific identity fields
            ))
            order += 1
        else:
            gaps.append("CREATE_ENDPOINT_NOT_SOURCE_BOUND")
            # No create route — try list-resolve so later {id} paths can still bind.
            if path_has_placeholders(normalize_path_placeholders(transition.api_endpoint)):
                resolve_steps, _ = self._resolve_entity_steps(
                    transition.api_endpoint, actor=role or "readonly", start_order=order, api_doc=api_doc,
                )
                steps.extend(resolve_steps)
                order += len(resolve_steps)
        # 3) Drive the entity toward from_state when it is not the initial state.
        if graph is not None and transition.from_state:
            order = self._drive_to_state(
                entity,
                transition.from_state,
                graph,
                endpoints,
                role,
                api_doc,
                steps,
                order,
                gaps,
                root=root,
                project=project,
            )
        # 4) Apply the transition's action endpoint.
        method = self._endpoint_method(transition.api_endpoint, endpoints) or "POST"
        action_path = normalize_path_placeholders(transition.api_endpoint) if path_has_placeholders(normalize_path_placeholders(transition.api_endpoint)) else transition.api_endpoint
        action_body = self._action_body_for(
            transition.api_endpoint,
            method,
            endpoints,
            api_doc,
            root=root,
            project=project,
        )
        if action_body:
            bind_steps, _ = self._body_binding_resolve_steps(
                action_body,
                actor=role or "readonly",
                start_order=order,
                api_doc=api_doc,
                root=root,
                project=project,
            )
            steps.extend(bind_steps)
            order += len(bind_steps)
        # If action path still needs an id and create was missing, resolve now.
        if path_has_placeholders(normalize_path_placeholders(action_path)) and not any(
            str(getattr(s, "action", "")).startswith("resolve_") or str(getattr(s, "action", "")) == "create_entity"
            for s in steps
        ):
            resolve_steps, action_path = self._resolve_entity_steps(
                action_path, actor=role or "readonly", start_order=order, api_doc=api_doc,
            )
            steps.extend(resolve_steps)
            order += len(resolve_steps)
        steps.append(ScenarioStep(
            order=order,
            action=f"transition_{transition.action or 'mutate'}",
            api_method=method,
            api_path=action_path,
            body_template=action_body,
            expected_status=(200 if not forbidden else 409),
            actor=role,
            extract_from_response=["id", "status", "state"],
        ))
        order += 1
        # 5) Observe the resulting state.
        read_ep = self._entity_read_endpoint(entity, endpoints)
        if read_ep:
            obs_path = normalize_path_placeholders(read_ep) if path_has_placeholders(normalize_path_placeholders(read_ep)) else read_ep
            steps.append(ScenarioStep(
                order=order,
                action="observe_transition_result",
                api_method="GET",
                api_path=obs_path,
                expected_status=200,
                actor=role,
                extract_from_response=["status", "state"],
            ))
        else:
            gaps.append("READ_ENDPOINT_NOT_SOURCE_BOUND")
        requires_path_binding = any(
            path_has_placeholders(normalize_path_placeholders(str(step.api_path or "")))
            for step in steps
        )
        has_path_binding_source = any(
            str(step.action or "").startswith(("resolve_", "bootstrap_create_"))
            or str(step.action or "") == "create_entity"
            for step in steps
        )
        binding_ready = not requires_path_binding or has_path_binding_source
        if not binding_ready:
            gaps.append("RUNTIME_PATH_BINDING_SOURCE_NOT_DECLARED")
        return ExecutableScenario(
            id=self._id(entity, transition.from_state, transition.to_state, transition.action),
            title=f"[来源约束{kind}] {entity}: {transition.from_state} -> {transition.to_state}",
            description="依据源约束（PRD/API）驱动实体经历状态流转，并以 StateOracle 校验流转是否被正确执行或拒绝。",
            category="state_machine",
            severity="P0" if forbidden else "P2",
            entity=entity,
            preconditions=[f"已通过可追溯数据证明 {entity} 处于 {transition.from_state}"],
            expected_state=transition.to_state,
            oracle_rules=["StateOracle.source_grounded_transition", f"{transition.from_state}->{transition.to_state}"],
            is_forbidden_path=forbidden,
            is_boundary_path=bool(transition.is_boundary),
            confidence=0.55 if transition.source_refs else 0.35,
            execution_policy="approved_sandbox_write" if binding_ready else "plan_only_requires_fixture",
            steps=steps if binding_ready else [],
            evidence_gaps=list(dict.fromkeys(gaps)),
            source_refs=list(transition.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="transition",
            discovery_round=discovery_round,
            runtime_hints={
                "declared_get_paths": declared_get_paths,
                "source_state": transition.from_state,
                "target_state": transition.to_state,
                "source_state_proof_required": True,
            },
        )

    # ── Generic helpers for state-transition scenarios (no hardcoding) ──

    def _generic_transition_auth(self, api_doc: str, root: Any, project: str) -> tuple[str, str, str, str]:
        accounts: list[dict[str, str]] = []
        login_path = ""
        try:
            from pathlib import Path
            from ai_test_asset_center.supplementary_behavior_slices import load_settings_accounts
            if root is not None and project:
                accounts, login_path = load_settings_accounts(Path(str(root)), str(project))
        except Exception:
            accounts, login_path = [], ""
        if not accounts and root is not None and project:
            try:
                from pathlib import Path
                p = Path(str(root)) / "platform_workspace" / str(project) / "input" / "test_accounts.json"
                if p.exists():
                    accounts = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                accounts = []
        acct = accounts[0] if accounts else {}
        role = str(acct.get("role") or "user").strip()
        email = str(acct.get("email") or acct.get("username") or "").strip()
        password = str(acct.get("password") or "").strip()
        if not login_path:
            login_path = self._discover_login_endpoint(api_doc)
        return role, email, password, login_path

    @staticmethod
    def _discover_login_endpoint(api_doc: str) -> str:
        _, _, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        for item in endpoints:
            p = str(item.get("path") or "").lower()
            if str(item.get("method") or "").upper() == "POST" and ("login" in p or "auth" in p or "signin" in p):
                return str(item.get("path") or "")
        return "/api/auth/login"

    @staticmethod
    def _entity_create_endpoint(entity: str, endpoints: list[dict[str, str]]) -> tuple[str, str] | None:
        for item in endpoints:
            if str(item.get("entity") or "") == entity and str(item.get("method") or "").upper() == "POST" and not path_has_placeholders(str(item.get("path") or "")):
                return str(item.get("path") or ""), "POST"
        return None

    @staticmethod
    def _entity_read_endpoint(entity: str, endpoints: list[dict[str, str]]) -> str:
        cands = [
            str(item.get("path") or "")
            for item in endpoints
            if str(item.get("entity") or "") == entity
            and str(item.get("method") or "").upper() == "GET"
            and str(item.get("path") or "").startswith("/")
        ]
        with_ph = [p for p in cands if path_has_placeholders(p)]
        no_ph = [p for p in cands if not path_has_placeholders(p)]
        # Prefer source-declared collection reads when available. The runtime
        # observer projects the bound entity out of the list, so post-transition
        # evidence does not depend on detail routes that may be documented but
        # unavailable in a specific non-production target.
        if no_ph:
            return no_ph[0]
        if with_ph:
            return with_ph[0]
        return ""

    @staticmethod
    def _endpoint_method(path: str, endpoints: list[dict[str, str]]) -> str:
        for item in endpoints:
            if str(item.get("path") or "") == str(path):
                return str(item.get("method") or "POST").upper()
        return "POST"

    @staticmethod
    def _initial_state(graph: "BusinessStateGraph") -> str:
        # A true initial state is one that is NEVER the TARGET of a normal
        # transition (nothing leads INTO it).  Using from_state here is wrong:
        # it treats any transition's source as "having an incoming edge" and so
        # mis-selects sink states (e.g. REFUNDED) as the initial state, which
        # then breaks path_to_state and prevents driving to real source states.
        incoming = {t.to_state for t in graph.transitions if t.is_normal and not t.is_forbidden}
        initials = [s for s in graph.states if s not in incoming]
        return initials[0] if initials else ""

    def _drive_to_state(
        self,
        entity: str,
        target_state: str,
        graph: "BusinessStateGraph",
        endpoints: list[dict[str, str]],
        actor: str,
        api_doc: str,
        steps: list[ScenarioStep],
        order: int,
        gaps: list[str],
        *,
        root: Any = None,
        project: str = "",
    ) -> int:
        """Emit generic steps to drive an entity from its initial state to target_state."""
        initial = self._initial_state(graph)
        if not initial or target_state == initial:
            return order
        path = graph.path_to_state(initial, target_state)
        if not path:
            gaps.append("DRIVE_TO_SOURCE_STATE_NOT_ROUTABLE")
            return order
        for t in path:
            if not t.action or not t.api_endpoint:
                gaps.append("DRIVE_STEP_UNROUTED")
                continue
            method = self._endpoint_method(t.api_endpoint, endpoints) or "POST"
            ep = normalize_path_placeholders(t.api_endpoint) if path_has_placeholders(normalize_path_placeholders(t.api_endpoint)) else t.api_endpoint
            body = self._action_body_for(
                t.api_endpoint,
                method,
                endpoints,
                api_doc,
                root=root,
                project=project,
            )
            steps.append(ScenarioStep(
                order=order,
                action=f"drive_{t.action or 'mutate'}",
                api_method=method,
                api_path=ep,
                body_template=body,
                expected_status=200,
                actor=actor,
                extract_from_response=["id", "status", "state"],
            ))
            order += 1
        return order

    @staticmethod
    def _action_body_for(
        path: str,
        method: str,
        endpoints: list[dict[str, str]],
        api_doc: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> dict[str, Any]:
        body, _ = SemanticScenarioGenerator._runtime_body_template_with_provenance(
            api_doc,
            method,
            path,
            root=root,
            project=project,
        )
        return body

    @staticmethod
    def _convert_doc_body_to_bindings(value: Any) -> Any:
        """Turn API-doc angle-bracket placeholders into runtime ``{field}`` bindings."""
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, child in value.items():
                converted = SemanticScenarioGenerator._convert_doc_body_to_bindings(child)
                # Prefer the JSON key's own spelling for identity placeholders so
                # ``{"orderId":"<order_id>"}`` becomes ``{orderId}`` (not
                # ``{order_id}``). Runtime extract/bootstrap actions then share
                # one binding name with the request field.
                if (
                    isinstance(converted, str)
                    and converted.startswith("{")
                    and converted.endswith("}")
                    and len(converted) > 2
                ):
                    placeholder = converted[1:-1]
                    key_name = str(key or "").strip()
                    if key_name and placeholder.lower().replace("_", "") == key_name.lower().replace("_", ""):
                        converted = "{" + key_name + "}"
                    elif key_name and (
                        key_name.lower().endswith("id")
                        or key_name.lower() in {"sku", "code", "uuid"}
                    ):
                        # Doc used a sibling spelling (order_id vs orderId).
                        from ai_test_asset_center.real_id_resolver import param_field_candidates

                        aliases = {c.lower() for c in param_field_candidates(placeholder)}
                        if key_name.lower() in aliases or "id" in aliases:
                            converted = "{" + key_name + "}"
                out[str(key)] = converted
            return out
        if isinstance(value, list):
            return [SemanticScenarioGenerator._convert_doc_body_to_bindings(child) for child in value]
        if isinstance(value, str):
            stripped = value.strip()
            angle = re.fullmatch(r"<([A-Za-z_]\w*)>", stripped)
            if angle:
                return "{" + str(angle.group(1) or "").strip() + "}"
            return value
        return value

    @staticmethod
    def _runtime_body_template(api_doc: str, method: str, path: str) -> dict[str, Any]:
        """Build a write-probe body from the API doc with bindable ``{field}`` placeholders."""
        body, _provenance = SemanticScenarioGenerator._runtime_body_template_with_provenance(
            api_doc, method, path,
        )
        return body

    @staticmethod
    def _runtime_body_template_with_provenance(
        api_doc: str,
        method: str,
        path: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> tuple[dict[str, Any], str]:
        from ..auto_test_data_factory import build_source_grounded_request_body

        normalized_path = normalize_path_placeholders(path)

        def _from_doc(doc_text: str) -> tuple[dict[str, Any], str]:
            if not str(doc_text or "").strip():
                return {}, "not_available"
            result = build_source_grounded_request_body(
                doc_text,
                method,
                normalized_path,
            )
            example = result.get("body") if isinstance(result, dict) else {}
            provenance = str((result or {}).get("provenance") or "not_available")
            if not isinstance(example, dict) or not example:
                return {}, provenance
            bindable = SemanticScenarioGenerator._convert_doc_body_to_bindings(example)
            return (bindable if isinstance(bindable, dict) else {}), provenance

        body, provenance = _from_doc(api_doc)
        if body:
            return body, provenance

        # The runtime catalog can be a compact endpoint-only projection while
        # the project input directory still contains the source API document
        # with request examples. Use those declared project sources before
        # giving up; never invent an industry-specific body.
        seen_hashes = {
            hashlib.sha256(str(api_doc or "").encode("utf-8")).hexdigest()
        }
        for source_doc in SemanticScenarioGenerator._project_api_doc_texts(root, project):
            source_hash = hashlib.sha256(source_doc.encode("utf-8")).hexdigest()
            if source_hash in seen_hashes:
                continue
            seen_hashes.add(source_hash)
            body, source_provenance = _from_doc(source_doc)
            if body:
                return body, source_provenance

        return {}, provenance

    @staticmethod
    def _project_api_doc_texts(root: Any, project: str) -> list[str]:
        if root in (None, "") or not str(project or "").strip():
            return []
        root_path = Path(root)
        project_name = str(project).strip()
        bases = [
            root_path / "projects" / project_name / "input",
            root_path / "platform_inputs" / project_name,
        ]
        preferred_names = {
            "api_spec.md",
            "apispec.md",
            "api.md",
            "openapi.json",
            "openapi.yaml",
            "openapi.yml",
            "swagger.json",
            "swagger.yaml",
            "swagger.yml",
        }
        docs: list[str] = []
        seen_paths: set[Path] = set()
        for base in bases:
            if not base.exists() or not base.is_dir():
                continue
            candidates = [
                item
                for item in sorted(base.iterdir(), key=lambda p: p.name.lower())
                if item.is_file()
                and (
                    item.name.lower() in preferred_names
                    or (
                        item.suffix.lower() in {".md", ".json", ".yaml", ".yml"}
                        and any(token in item.name.lower() for token in ("api", "openapi", "swagger"))
                    )
                )
            ]
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                docs.append(candidate.read_text(encoding="utf-8"))
        return docs

    @staticmethod
    def _bootstrap_create_body(
        api_doc: str,
        create_path: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> dict[str, Any]:
        body, _ = SemanticScenarioGenerator._bootstrap_create_body_with_provenance(
            api_doc,
            create_path,
            root=root,
            project=project,
        )
        return body

    @staticmethod
    def _bootstrap_create_body_with_provenance(
        api_doc: str,
        create_path: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Build a create body for identity bootstrap.

        Preserve source-documented scalar strings by default. They are often
        required business keys (SKU, material code, username, etc.) needed to
        materialize a real runtime ID. Only drop top-level promotional/discount
        references because those are frequently optional, one-time, or exhausted
        demo values that make otherwise valid bootstrap creates fail closed.
        """
        raw, provenance = SemanticScenarioGenerator._runtime_body_template_with_provenance(
            api_doc,
            "POST",
            create_path,
            root=root,
            project=project,
        )
        if not isinstance(raw, dict) or not raw:
            return {}, provenance
        minimized: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("{") and stripped.endswith("}") and len(stripped) > 2:
                    minimized[key] = value
                    continue
                key_l = re.sub(r"[^a-z0-9]+", "", str(key).lower())
                if any(token in key_l for token in ("coupon", "promo", "voucher", "discount")):
                    continue
                minimized[key] = value
                continue
            minimized[key] = value
        return (minimized if minimized else dict(raw)), provenance

    # ── Section: 请求体绑定 ──

    @staticmethod
    def _body_binding_resolve_steps(
        body: dict[str, Any],
        *,
        actor: str,
        start_order: int,
        api_prefix: str = "/api",
        api_doc: str = "",
        root: Any = None,
        project: str = "",
        bootstrap_depth: int = 0,
    ) -> tuple[list[ScenarioStep], int]:
        """Insert list-resolve steps for body placeholders not satisfied by path resolve.

        Uses documented/derived collection paths directly (e.g. ``/api/users/addresses``)
        instead of expanding nested admin/search alternates that crowd out the real list.
        When the API doc is available, also append a bootstrap POST create so empty
        freshly-seeded databases can still materialize identity bindings.
        """
        from ai_test_asset_center.real_id_resolver import param_field_candidates

        steps: list[ScenarioStep] = []
        order = start_order
        seen_paths: set[str] = set()
        for field in extract_body_binding_fields(body):
            extract_fields = list(dict.fromkeys([
                field,
                *param_field_candidates(field),
                "id", "uuid", "code", "key", "ref",
                "amount", "total", "balance", "quantity",
            ]))
            collections = body_field_collection_paths(field, api_prefix=api_prefix)
            for collection in collections:
                if not collection.startswith("/") or collection in seen_paths:
                    continue
                seen_paths.add(collection)
                steps.append(ScenarioStep(
                    order=order,
                    action=f"resolve_body_{field}",
                    api_method="GET",
                    api_path=collection,
                    extract_from_response=list(extract_fields),
                    expected_status=200,
                    actor=actor,
                ))
                order += 1
                if len(steps) >= 3:
                    break
            # Bootstrap create when a collection POST body is documented and does
            # not recursively require the same field (e.g. orderId → POST /orders
            # needs addressId, not orderId). GET resolve and POST create share the
            # same path; do not gate create on seen_paths (that only dedupes GETs).
            if api_doc and collections and bootstrap_depth < 2:
                create_path = collections[0]
                create_body = SemanticScenarioGenerator._bootstrap_create_body(
                    api_doc,
                    create_path,
                    root=root,
                    project=project,
                )
                nested_fields = set(extract_body_binding_fields(create_body))
                if (
                    isinstance(create_body, dict)
                    and create_body
                    and field not in nested_fields
                    and not any(
                        getattr(step, "action", "") == f"bootstrap_create_{field}"
                        for step in steps
                    )
                ):
                    nested_steps, order = SemanticScenarioGenerator._body_binding_resolve_steps(
                        create_body,
                        actor=actor,
                        start_order=order,
                        api_prefix=api_prefix,
                        api_doc=api_doc,
                        root=root,
                        project=project,
                        bootstrap_depth=bootstrap_depth + 1,
                    )
                    steps.extend(nested_steps)
                    steps.append(ScenarioStep(
                        order=order,
                        action=f"bootstrap_create_{field}",
                        api_method="POST",
                        api_path=create_path,
                        body_template=dict(create_body),
                        extract_from_response=list(extract_fields),
                        expected_status=200,
                        actor=actor,
                    ))
                    order += 1
            if order - start_order >= 5:
                break
        return steps, order

    @staticmethod
    def _is_identity_body_key(key: str) -> bool:
        token = re.sub(r"[^a-z0-9_]+", "", str(key or "").lower())
        if not token:
            return False
        if token in {"id", "uuid", "sku", "code", "pk"}:
            return True
        return bool(
            token.endswith("id")
            or token.endswith("uuid")
            or token.endswith("sku")
            or token.endswith("code")
        )

    @staticmethod
    def _sibling_identity_body_bindings(
        api_doc: str,
        path: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Inherit bindable identity keys from sibling writes under the same prefix.

        Action-style admin routes often omit a request example while a nearby
        documented write in the same service already names the entity binder
        (``entityId``, ``resourceId``, …). Copy only identity-shaped keys so the
        probe can reach an authorization decision instead of a missing-payload
        transport error. Never invent keys or copy non-identity business fields.
        """
        normalized = normalize_path_placeholders(str(path or "")).split("?", 1)[0]
        parts = [part for part in normalized.strip("/").split("/") if part]
        if len(parts) < 2:
            return {}, ""
        prefixes = ["/" + "/".join(parts[:depth]) for depth in range(len(parts) - 1, 0, -1)]
        try:
            _, _, endpoints = _api_facts(api_doc, re.compile(r"", re.I))
        except Exception:
            endpoints = []
        write_methods = {"POST", "PUT", "PATCH"}
        best_body: dict[str, Any] = {}
        best_provenance = ""
        best_score = -1
        for prefix in prefixes:
            for endpoint in endpoints:
                method = str(endpoint.get("method") or "").strip().upper()
                if method not in write_methods:
                    continue
                sibling_path = normalize_path_placeholders(str(endpoint.get("path") or ""))
                if not sibling_path.startswith("/") or sibling_path == normalized:
                    continue
                if not (
                    sibling_path == prefix
                    or sibling_path.startswith(prefix + "/")
                ):
                    continue
                sibling_body, provenance = SemanticScenarioGenerator._runtime_body_template_with_provenance(
                    api_doc,
                    method,
                    sibling_path,
                    root=root,
                    project=project,
                )
                if not isinstance(sibling_body, dict) or not sibling_body:
                    continue
                identity_body: dict[str, Any] = {}
                for key, value in sibling_body.items():
                    key_text = str(key)
                    if not SemanticScenarioGenerator._is_identity_body_key(key_text):
                        continue
                    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                        identity_body[key_text] = value
                    else:
                        identity_body[key_text] = "{" + key_text + "}"
                if not identity_body:
                    continue
                shared = len(set(parts) & set(sibling_path.strip("/").split("/")))
                score = (len(identity_body) * 10) + shared
                if score > best_score:
                    best_score = score
                    best_body = identity_body
                    best_provenance = (
                        f"sibling_identity_binding:{method} {sibling_path}"
                        if provenance
                        else "sibling_identity_binding"
                    )
            if best_body:
                return best_body, best_provenance
        return {}, ""

    @staticmethod
    # ── Section: 写操作探针 & 测试夹具 ──

    def _append_write_probe_step(
        steps: list[ScenarioStep],
        *,
        action: str,
        method: str,
        path: str,
        actor: str,
        expected_status: int,
        api_doc: str,
        root: Any = None,
        project: str = "",
    ) -> None:
        if method == "POST":
            body, body_provenance = SemanticScenarioGenerator._bootstrap_create_body_with_provenance(
                api_doc,
                path,
                root=root,
                project=project,
            )
        else:
            body, body_provenance = SemanticScenarioGenerator._runtime_body_template_with_provenance(
                api_doc,
                method,
                path,
                root=root,
                project=project,
            )
        # Action-style admin writes often document no body (e.g. "mark success")
        # while sibling routes in the same service document the entity binder
        # (orderId / resourceId). Pull those bindable id fields so the probe can
        # reach the role-boundary decision instead of a missing-payload 500.
        # Only action-style paths qualify: generic creates must remain
        # source-documented (never invent body fields from siblings).
        if (not body) and method in {"POST", "PUT", "PATCH"}:
            from ai_test_asset_center.sandbox_write_executor_base import _is_action_style_write_path

            if _is_action_style_write_path(path):
                sibling_body, sibling_provenance = (
                    SemanticScenarioGenerator._sibling_identity_body_bindings(
                        api_doc,
                        path,
                        root=root,
                        project=project,
                    )
                )
                if sibling_body:
                    body = sibling_body
                    body_provenance = sibling_provenance or "sibling_identity_binding"
        # Drop optional top-level promo/demo string literals that commonly
        # exhaust or FK-fail on fresh DBs. Keep placeholders, numbers, arrays.
        if isinstance(body, dict) and body:
            promo_tokens = ("coupon", "promo", "voucher", "discountcode", "discount_code")
            cleaned: dict[str, Any] = {}
            for key, value in body.items():
                key_l = re.sub(r"[^a-z0-9]+", "", str(key).lower())
                if (
                    isinstance(value, str)
                    and not (value.startswith("{") and value.endswith("}"))
                    and any(tok in key_l for tok in promo_tokens)
                ):
                    continue
                cleaned[key] = value
            if cleaned:
                body = cleaned
        binding_steps, _ = SemanticScenarioGenerator._body_binding_resolve_steps(
            body,
            actor=actor,
            start_order=len(steps) + 1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.extend(binding_steps)
        extract_fields = ["id", "status", "state", "amount"]
        steps.append(ScenarioStep(
            order=len(steps) + 1,
            action=action,
            api_method=method,
            api_path=path,
            body_template=body,
            body_provenance=body_provenance,
            expected_status=expected_status,
            actor=actor,
            extract_from_response=extract_fields,
        ))

    @staticmethod
    def _identity_create_fixture_candidate(
        api_doc: str,
        target_path: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> tuple[str, dict[str, Any], str] | None:
        """Find a documented identity-create endpoint usable as a disposable fixture."""
        normalized_target = normalize_path_placeholders(target_path)
        target_tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", normalized_target.lower())
            if token
        }
        identity_tokens = {
            "auth",
            "identity",
            "user",
            "users",
            "account",
            "accounts",
            "member",
            "members",
            "customer",
            "customers",
            "profile",
            "profiles",
            "patient",
            "patients",
            "employee",
            "employees",
        }
        mutation_tokens = {
            "status",
            "state",
            "password",
            "credential",
            "credentials",
            "secret",
            "role",
            "roles",
            "permission",
            "permissions",
            "balance",
            "disable",
            "disabled",
            "deactivate",
            "suspend",
            "lock",
            "locked",
            "unlock",
            "reset",
            "freeze",
            "unfreeze",
            "activate",
            "enable",
        }
        if not (target_tokens & identity_tokens and target_tokens & mutation_tokens):
            return None

        try:
            _, _, declared_endpoints = _api_facts(
                api_doc,
                re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I),
            )
        except Exception:
            declared_endpoints = []

        creation_tokens = {
            "register",
            "signup",
            "sign",
            "create",
            "invite",
            "enroll",
            "users",
            "accounts",
            "members",
            "customers",
            "patients",
            "employees",
        }
        identity_field_tokens = {
            "email",
            "username",
            "user",
            "userid",
            "user_id",
            "account",
            "accountid",
            "account_id",
            "login",
            "phone",
            "mobile",
            "password",
            "name",
            "displayname",
            "display_name",
        }

        def _body_key_tokens(value: Any) -> set[str]:
            tokens: set[str] = set()
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized_key = re.sub(r"[^a-z0-9_]+", "", str(key or "").lower())
                    if normalized_key:
                        tokens.add(normalized_key)
                    tokens.update(_body_key_tokens(child))
            elif isinstance(value, list):
                for child in value:
                    tokens.update(_body_key_tokens(child))
            return tokens

        def _summary_json_body(text: str) -> dict[str, Any]:
            decoder = json.JSONDecoder()
            raw = str(text or "")
            for index, char in enumerate(raw):
                if char != "{":
                    continue
                try:
                    parsed, _end = decoder.raw_decode(raw[index:])
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    converted = SemanticScenarioGenerator._convert_doc_body_to_bindings(parsed)
                    return converted if isinstance(converted, dict) else {}
            return {}

        ranked: list[tuple[int, str, dict[str, Any], str]] = []
        for endpoint in declared_endpoints:
            if str(endpoint.get("method") or "").strip().upper() != "POST":
                continue
            path = normalize_path_placeholders(str(endpoint.get("path") or ""))
            if not path.startswith("/") or path == normalized_target:
                continue
            body, provenance = SemanticScenarioGenerator._bootstrap_create_body_with_provenance(
                api_doc,
                path,
                root=root,
                project=project,
            )
            if not body:
                body = _summary_json_body(str(endpoint.get("summary") or ""))
                if body:
                    provenance = "endpoint_summary_json"
            if not isinstance(body, dict) or not body:
                continue
            path_tokens = {
                token
                for token in re.split(r"[^a-z0-9]+", path.lower())
                if token
            }
            body_tokens = _body_key_tokens(body)
            has_identity_body = bool(body_tokens & identity_field_tokens)
            has_creation_path = bool(path_tokens & creation_tokens)
            has_identity_path = bool(path_tokens & identity_tokens)
            has_credential_pair = bool(
                body_tokens & {"password"}
                and body_tokens & {"email", "username", "login", "phone", "mobile"}
            )
            if not has_identity_body or not (has_creation_path or has_identity_path):
                continue
            score = 0
            score += 40 if has_credential_pair else 0
            score += 30 if has_creation_path else 0
            score += 20 if has_identity_path else 0
            score += 10 * len(path_tokens & target_tokens)
            score += 5 * len(body_tokens & identity_field_tokens)
            ranked.append((score, path, body, provenance))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1]))
        _, path, body, provenance = ranked[0]
        return path, body, provenance

    # ── Section: 实体依赖关系 ──

    def _dependency(
        self,
        entity: str,
        edge: StateEdge,
        discovery_round: int,
        *,
        slice_meta: dict[str, Any] | None = None,
        api_doc: str = "",
    ) -> ExecutableScenario:
        slice_id = behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        title = f"[跨实体依赖] {entity} -> {edge.target_entity}"
        write_path = self._preferred_write_endpoint(api_doc, entity)
        write_body = self._dependency_write_body(api_doc, entity, edge.target_entity)
        if observation_path and write_path and isinstance(write_body, dict) and write_body:
            extract_fields = ["id", "status", "state", "amount", "total", "balance", "quantity"]
            return ExecutableScenario(
                id=self._id(entity, edge.target_entity, edge.relation, write_path),
                title=title,
                description=f"先观察 {edge.target_entity} 的真实对象，再执行 {entity} 的来源写入口，验证跨实体依赖链是否可执行。",
                category="dependency",
                severity="P1",
                entity=entity,
                preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
                actors=["readonly"],
                steps=[
                    ScenarioStep(order=1, action="observe_dependency_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=["id"]),
                    ScenarioStep(order=2, action="execute_dependency_write", api_method="POST", api_path=write_path, expected_status=200, actor="readonly", body_template=write_body),
                    ScenarioStep(order=3, action="verify_dependency_effect_after_write", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=extract_fields),
                ],
                expected_state=edge.target_state,
                oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
                confidence=0.6 if edge.source_refs else 0.35,
                execution_policy="approved_sandbox_write",
                evidence_gaps=[],
                source_refs=list(edge.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="dependency",
                discovery_round=discovery_round,
            )
        if observation_path:
            return ExecutableScenario(
                id=self._id(entity, edge.target_entity, edge.relation, observation_path),
                title=title,
                description=f"观察 {edge.target_entity} 的来源绑定运行时路径，验证 {entity} 的跨实体依赖前置是否可达。",
                category="dependency",
                severity="P1",
                entity=entity,
                preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
                actors=["readonly"],
                steps=[ScenarioStep(order=1, action="observe_dependency_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
                expected_state=edge.target_state,
                oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
                confidence=0.5 if edge.source_refs else 0.3,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=list(edge.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="dependency",
                discovery_round=discovery_round,
            )
        return ExecutableScenario(
            id=self._id(entity, edge.target_entity, edge.relation),
            title=title,
            description=f"当前资料缺少 {entity} -> {edge.target_entity} 的可观察运行时路由。",
            category="dependency",
            severity="P1",
            entity=entity,
            preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
            expected_state=edge.target_state,
            oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
            confidence=0.4 if edge.source_refs else 0.2,
            evidence_gaps=["CROSS_ENTITY_OBSERVATION_CONTRACT_MISSING", "ACTOR_BINDING_MISSING"],
            source_refs=list(edge.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="dependency",
            discovery_round=discovery_round,
        )

    def _invariant(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        *,
        slice_meta: dict[str, Any] | None = None,
        api_doc: str = "",
        root: Any = None,
        project: str = "",
    ) -> list[ExecutableScenario]:
        slice_id = behavior_slice_id("invariant", entity, state, invariant)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        bound_method = str((slice_meta or {}).get("_bound_method") or "").strip().upper()
        bound_path = str((slice_meta or {}).get("_bound_path") or "").strip()
        if bound_method in {"POST", "PUT", "PATCH", "DELETE"} and bound_path.startswith("/"):
            bound_item = self._invariant_from_meta(
                dict(slice_meta or {}),
                discovery_round,
                api_doc,
                root=root,
                project=project,
            )
            if bound_item is not None:
                return [bound_item]
        runtime_upgrade = self._invariant_runtime_upgrade(
            entity,
            state,
            invariant,
            refs,
            discovery_round,
            slice_id=slice_id,
            observation_path=observation_path,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        if runtime_upgrade is not None:
            return [runtime_upgrade]
        if observation_path:
            steps: list[ScenarioStep] = []
            observation_path = str(observation_path)
            if path_has_placeholders(normalize_path_placeholders(observation_path)):
                resolve_steps, observation_path = self._resolve_entity_steps(
                    observation_path,
                    actor="readonly",
                    start_order=1,
                    api_doc=api_doc,
                    root=root,
                    project=project,
                )
                steps.extend(resolve_steps)
            steps.append(ScenarioStep(
                order=len(steps) + 1,
                action="observe_bound_entity",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor="readonly",
            ))
            return [ExecutableScenario(
                id=self._id(entity, state, invariant),
                title=f"[来源约束不变量] {entity}: {state}",
                description=invariant[:300],
                category="invariant",
                severity="P1",
                entity=entity,
                preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
                actors=["readonly"],
                steps=steps,
                expected_state=state,
                oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
                confidence=0.55 if refs else 0.3,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=list(refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="invariant",
                discovery_round=discovery_round,
            )]
        return [ExecutableScenario(
            id=self._id(entity, state, invariant),
            title=f"[来源约束不变量] {entity}: {state}",
            description=invariant[:300],
            category="invariant",
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
            expected_state=state,
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=0.45 if refs else 0.2,
            evidence_gaps=["OBSERVATION_ROUTE_NOT_SOURCE_BOUND", "ACTOR_BINDING_MISSING"],
            source_refs=list(refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
        )]

    def _invariant_runtime_upgrade(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        *,
        slice_id: str,
        observation_path: str,
        api_doc: str,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        if not str(api_doc or "").strip():
            return None
        action_plan = self._match_invariant_action(api_doc, entity, invariant, refs, state=state)
        if not action_plan:
            return None
        extract_fields = ["id", "status", "state", "amount", "total", "balance", "quantity"]
        validation_only = bool(action_plan.get("validation_only"))
        # A validation-only route (e.g. POST /validate) is itself the probe and
        # needs no separate read endpoint to bind a state prerequisite. Only
        # state-precondition drivers (forbidden/duplicate writes) require a
        # source-bound observation path, so only block those when it is missing.
        if not validation_only and not observation_path:
            return None
        action_method = str(action_plan.get("method") or "POST")
        action_path = str(action_plan.get("path") or "")
        action_body = action_plan.get("body") if isinstance(action_plan.get("body"), dict) else {}
        action_body_provenance = "documented_example" if action_body else "not_available"
        if not action_body:
            action_body, action_body_provenance = self._runtime_body_template_with_provenance(
                api_doc,
                action_method,
                action_path,
                root=root,
                project=project,
            )
        write_step = ScenarioStep(
            order=1 if validation_only else 2,
            action=str(action_plan.get("scenario_action") or "execute_invariant_write"),
            api_method=action_method,
            api_path=action_path,
            expected_status=int(action_plan.get("expected_status") or 200),
            actor="readonly",
            body_template=dict(action_body),
            body_provenance=action_body_provenance,
        )
        title_suffix = str(action_plan.get("title_suffix") or str(action_plan.get("path") or "")).strip()
        oracle_rules = ["ConsistencyOracle.source_grounded_invariant", invariant[:300]]
        rule_key = str(action_plan.get("rule_key") or "").strip()
        if rule_key:
            oracle_rules.insert(0, f"CouponOracle.{rule_key}")
        body = write_step.body_template if isinstance(write_step.body_template, dict) else {}
        bind_steps, _ = self._body_binding_resolve_steps(
            body,
            actor="readonly",
            start_order=1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        if validation_only:
            for index, step in enumerate(bind_steps):
                step.order = index + 1
            write_step.order = len(bind_steps) + 1
            steps = [*bind_steps, write_step]
        else:
            # State precondition driver: when the invariant is anchored to a
            # concrete lifecycle status (e.g. PAID / CANCELLED), bind an entity
            # that is genuinely in that state via a filtered extraction.  If no
            # such entity exists at runtime the executor marks the trace
            # precondition_not_met and the finding cannot be confirmed — so we
            # never confirm a transition from a state the system never reached.
            observe_where: dict[str, Any] = {}
            state_token = str(state or "").strip()
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", state_token):
                observe_where = {"status": state_token}
            steps = []
            observation_path = str(observation_path)
            if path_has_placeholders(normalize_path_placeholders(observation_path)):
                resolve_steps, observation_path = self._resolve_entity_steps(
                    observation_path,
                    actor="readonly",
                    start_order=1,
                    api_doc=api_doc,
                    root=root,
                    project=project,
                )
                steps.extend(resolve_steps)
            steps.append(ScenarioStep(
                order=len(steps) + 1,
                action="observe_bound_entity",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor="readonly",
                extract_from_response=extract_fields,
                extract_where=observe_where,
            ))
            for index, step in enumerate(bind_steps):
                step.order = len(steps) + 1
                steps.append(step)
            write_step.order = len(steps) + 1
            steps.append(write_step)
        if not validation_only and str(action_plan.get("mode") or "") == "duplicate_write":
            steps.append(ScenarioStep(
                order=len(steps) + 1,
                action=str(action_plan.get("scenario_action") or "repeat_invariant_write"),
                api_method=str(action_plan.get("method") or "POST"),
                api_path=str(action_plan.get("path") or ""),
                expected_status=int(action_plan.get("expected_status") or 200),
                actor="readonly",
                body_template=dict(action_body),
                body_provenance=action_body_provenance,
            ))
        if not validation_only:
            verify_order = len(steps) + 1
            steps.append(ScenarioStep(
                order=verify_order,
                action="verify_bound_entity_after_write",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor="readonly",
                extract_from_response=extract_fields,
            ))
        return ExecutableScenario(
            id=self._id(entity, state, invariant, title_suffix),
            title=f"[来源约束不变量] {entity}: {state} -> {title_suffix}",
            description=invariant[:300],
            category=str(action_plan.get("category") or "invariant"),
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本", f"约束: {invariant[:120]}"],
            actors=["readonly"],
            steps=steps,
            expected_state=state,
            oracle_rules=oracle_rules,
            confidence=0.7 if refs else 0.4,
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=list(refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            is_forbidden_path=bool(action_plan.get("forbidden")),
            runtime_hints=dict(action_plan.get("runtime_hints") or {}),
        )

    @staticmethod
    def _locator_line(locator: Any) -> int | None:
        match = re.search(r"line:(\d+)", str(locator or ""), re.I)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _invariant_action_contexts(cls, invariant: str, refs: list[dict[str, str]] | None) -> list[str]:
        anchor_text = str(invariant or "").strip()
        anchor_line: int | None = None
        normalized_refs = [item for item in (refs or []) if isinstance(item, dict)]
        for ref in normalized_refs:
            if str(ref.get("quote") or "").strip() == anchor_text:
                anchor_line = cls._locator_line(ref.get("locator"))
                break

        ranked: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        if anchor_text:
            ranked.append((-1, 0, anchor_text))
            seen.add(anchor_text)
        for index, ref in enumerate(normalized_refs, start=1):
            quote = str(ref.get("quote") or "").strip()
            if not quote or quote in seen:
                continue
            line = cls._locator_line(ref.get("locator"))
            distance = abs(line - anchor_line) if line is not None and anchor_line is not None else 10_000 + index
            ranked.append((distance, index, quote))
            seen.add(quote)
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [text for _, _, text in ranked]

    def _match_invariant_action(self, api_doc: str, entity: str, invariant: str, refs: list[dict[str, str]] | None = None, state: str = "") -> dict[str, Any]:
        contexts = self._invariant_action_contexts(invariant, refs)
        # Action-verb detection must not be driven by database schema DDL. A CHECK
        # enum such as status IN ('ACTIVE', 'DISABLED') is a structural declaration,
        # not an action instruction; letting "DISABLED" substring-match the
        # "disable" action profile hijacks the classification and drops the real
        # source-bound validation route. So detect the action from non-DDL context.
        action_refs = [
            ref for ref in (refs or [])
            if isinstance(ref, dict) and str(ref.get("source_type") or "") != "database_schema"
        ]
        action_contexts = self._invariant_action_contexts(invariant, action_refs)
        mode = ""
        forbidden = False
        for text in action_contexts:
            lowered = text.lower()
            if any(token in text for token in ("不能", "禁止", "不应", "不可", "不得")) or any(token in lowered for token in ("must not", "forbidden", "cannot", "should not")):
                mode, forbidden = "forbidden_write", True
                break
            if any(token in text for token in ("只能成功一次", "重复成功", "不能重复", "只能成功支付一次")) or any(token in lowered for token in ("only once", "duplicate", "idempotent")):
                mode = "duplicate_write"
                break
        action_profiles: list[dict[str, Any]] = [
            {"tokens": ["取消", "cancel"], "endpoint_tokens": ["cancel"]},
            {"tokens": ["支付", "pay", "payment"], "endpoint_tokens": ["pay", "payment"]},
            {"tokens": ["退款", "refund"], "endpoint_tokens": ["refund"]},
            {"tokens": ["审批", "approve", "approval"], "endpoint_tokens": ["approve", "approval"]},
            {"tokens": ["驳回", "reject"], "endpoint_tokens": ["reject"]},
            {"tokens": ["关闭", "close"], "endpoint_tokens": ["close"]},
            {"tokens": ["撤销", "revoke"], "endpoint_tokens": ["revoke"]},
            {"tokens": ["回滚", "rollback"], "endpoint_tokens": ["rollback"]},
            {"tokens": ["释放", "release"], "endpoint_tokens": ["release"]},
            {"tokens": ["归档", "archive"], "endpoint_tokens": ["archive"]},
            {"tokens": ["禁用", "disable"], "endpoint_tokens": ["disable"]},
            {"tokens": ["恢复", "restore", "reopen"], "endpoint_tokens": ["restore", "reopen"]},
            {"tokens": ["校验", "验证", "validate", "apply", "redeem"], "endpoint_tokens": ["validate", "apply", "redeem"]},
        ]
        profile = None
        for text in action_contexts:
            lowered = text.lower()
            profile = next((item for item in action_profiles if any(token.lower() in lowered for token in item["tokens"])), None)
            if profile:
                break
        if not profile:
            # Endpoint-grounded fallback: the invariant text may only assert a
            # state constraint (e.g. "必须处于 ACTIVE 状态") without an explicit
            # action verb, while the source API exposes a validation-style route
            # (validate/apply/redeem). When the invariant is an affirmative
            # constraint and such a source-bound route exists, treat it as a
            # validation-only assertion. This stays industry-agnostic: the verbs
            # come from the real API doc, not hardcoded business rules.
            validate_profile = next(
                (item for item in action_profiles if "validate" in item["endpoint_tokens"]),
                None,
            )
            affirmative_tokens = (
                "必须", "应当", "应", "需要", "must", "should", "required",
                "within", "active", "有效期", "类目", "状态",
            )
            invariant_is_affirmative = any(
                any(token.lower() in str(text or "").lower() for token in affirmative_tokens)
                for text in contexts
            )
            _, _, _fallback_endpoints = _api_facts(api_doc, re.compile(r"", re.I))
            has_validation_route = any(
                str(ep.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
                and any(
                    tok in " ".join(
                        str(part or "").lower()
                        for part in (ep.get("path"), ep.get("action"), ep.get("summary"))
                    )
                    for tok in ("validate", "apply", "redeem")
                )
                for ep in (_fallback_endpoints or [])
            )
            if validate_profile and invariant_is_affirmative and has_validation_route:
                # Only bind a validate/apply route when it belongs to this entity
                # (or the invariant itself is promo/validation scoped). Otherwise
                # cart/inventory invariants steal POST /coupons/validate.
                entity_token = re.sub(r"[^a-z0-9]+", "", str(entity or "").lower())
                entity_has_own_validate = any(
                    (
                        entity_token
                        and entity_token in re.sub(r"[^a-z0-9]+", "", str(ep.get("path") or "").lower())
                    )
                    or str(ep.get("entity") or "").strip().lower() == str(entity or "").strip().lower()
                    for ep in (_fallback_endpoints or [])
                    if str(ep.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
                    and any(
                        tok in str(ep.get("path") or "").lower()
                        for tok in ("validate", "apply", "redeem")
                    )
                )
                promo_scoped = bool(self._coupon_rule_key(entity, "", invariant, contexts))
                if entity_has_own_validate or promo_scoped:
                    profile = validate_profile
                else:
                    return {}
            else:
                return {}
        if not mode and any(token in profile["endpoint_tokens"] for token in ("validate", "apply", "redeem")):
            affirmative_tokens = ("必须", "应当", "应", "需要", "must", "should", "required", "within", "active", "有效期", "类目")
            if any(any(token.lower() in str(text or "").lower() for token in affirmative_tokens) for text in contexts):
                mode = "validation_only"
        if not mode:
            return {}

        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        for endpoint in endpoints:
            method = str(endpoint.get("method") or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                continue
            path = str(endpoint.get("path") or "")
            haystack = " ".join(
                str(part or "").lower()
                for part in (endpoint.get("path"), endpoint.get("action"), endpoint.get("summary"), endpoint.get("entity"))
                if str(part or "").strip()
            )
            if not any(token.lower() in haystack for token in profile["endpoint_tokens"]):
                continue
            normalized_path = normalize_path_placeholders(path)
            endpoint_entity = str(endpoint.get("entity") or "").strip().lower()
            entity_token = re.sub(r"[^a-z0-9]+", "", str(entity or "").lower())
            path_token = re.sub(r"[^a-z0-9]+", "", normalized_path.lower())
            if endpoint_entity and entity and endpoint_entity != str(entity or "").strip().lower():
                if not (entity_token and entity_token in path_token):
                    continue
            if path_has_placeholders(normalized_path) and endpoint_entity and endpoint_entity != str(entity or "").strip().lower():
                continue
            body = self._invariant_write_body(api_doc, method, path, entity)
            if body is None:
                continue
            rule_key = self._coupon_rule_key(entity, state, invariant, contexts)
            return {
                "mode": mode,
                "validation_only": mode == "validation_only",
                "forbidden": forbidden,
                "method": method,
                "path": normalized_path,
                "body": body,
                "expected_status": 409 if forbidden else 200,
                "scenario_action": f"invariant_{str(profile['endpoint_tokens'][0])}_write",
                "title_suffix": f"{normalized_path}#{rule_key}" if rule_key else normalized_path,
                "category": "state_machine" if forbidden else ("concurrency" if mode == "duplicate_write" else "invariant"),
                "rule_key": rule_key,
                "runtime_hints": {"coupon_validation_rule": rule_key} if rule_key else {},
            }
        return {}

    @staticmethod
    def _coupon_rule_key(entity: str, state: str, invariant: str, contexts: list[str]) -> str:
        entity_token = re.sub(r"[^a-z0-9]+", "", str(entity or "").strip().lower())
        promo_aliases = {
            "coupon", "coupons", "promotion", "promotions", "promo", "promocode", "promocodes",
            "voucher", "vouchers", "discount", "discounts", "subsidy", "subsidies",
            "feewaiver", "feewaivers", "rebate", "rebates",
        }
        # Also accept CJK entity labels commonly used in PRDs.
        entity_raw = str(entity or "").strip().lower()
        if entity_token not in promo_aliases and not any(
            token in entity_raw for token in ("优惠券", "促销", "代金券", "补贴", "折扣", "减免")
        ):
            return ""

        def has_any(text: str, tokens: tuple[str, ...]) -> bool:
            lowered = str(text or "").lower()
            return any(token in lowered for token in tokens)

        invariant_text = str(invariant or "").lower()
        state_text = str(state or "").strip().upper()
        merged = " ".join(str(text or "").lower() for text in contexts)

        if has_any(invariant_text, ("类目", "category", "scope")):
            return "coupon_category_scope_must_match"
        if has_any(invariant_text, ("最低订单金额", "min order", "minimum order", "门槛")):
            return "coupon_min_threshold_must_match"
        if has_any(invariant_text, ("有效期", "过期", "expire", "expired")):
            return "expired_coupon_must_be_invalid"
        if has_any(invariant_text, ("active", "停用", "禁用", "disabled", "状态")):
            return "inactive_coupon_must_be_invalid"

        if state_text == "DISABLED":
            return "inactive_coupon_must_be_invalid"

        if has_any(merged, ("类目", "category", "scope")):
            return "coupon_category_scope_must_match"
        if has_any(merged, ("最低订单金额", "min order", "minimum order", "门槛")):
            return "coupon_min_threshold_must_match"
        if has_any(merged, ("active", "停用", "禁用", "disabled", "状态")):
            return "inactive_coupon_must_be_invalid"
        if has_any(merged, ("有效期", "过期", "expire", "expired")):
            return "expired_coupon_must_be_invalid"
        return "coupon_validation_rule_must_be_enforced"

    @staticmethod
    def _invariant_write_body(api_doc: str, method: str, path: str, entity: str) -> dict[str, Any] | None:
        # Prefer bindable runtime templates from the API doc so write probes are
        # not executed with an empty body when the spec documents a request schema.
        rendered = SemanticScenarioGenerator._runtime_body_template(api_doc, method, path)
        if not rendered:
            example = _markdown_request_example(api_doc, method, path)
            if not example:
                return {}
            if not isinstance(example, dict):
                return None
            rendered = SemanticScenarioGenerator._bind_dependency_placeholders(example, entity)
        else:
            rendered = SemanticScenarioGenerator._bind_dependency_placeholders(rendered, entity)
        if SemanticScenarioGenerator._has_unresolved_dependency_placeholder(rendered):
            # Keep bindable {field} placeholders — runtime resolve fills them.
            # Only reject angle-bracket leftovers that were not converted.
            if isinstance(rendered, dict) and not SemanticScenarioGenerator._has_angle_placeholders(rendered):
                return rendered if isinstance(rendered, dict) else None
            return None
        return rendered if isinstance(rendered, dict) else None

    @staticmethod
    def _has_angle_placeholders(value: Any) -> bool:
        if isinstance(value, dict):
            return any(SemanticScenarioGenerator._has_angle_placeholders(v) for v in value.values())
        if isinstance(value, list):
            return any(SemanticScenarioGenerator._has_angle_placeholders(v) for v in value)
        return isinstance(value, str) and bool(re.search(r"<[A-Za-z_]\w*>", value))

    @staticmethod
    def _preferred_read_endpoint(endpoints: list[Any]) -> str:
        candidates = [str(item or "").strip() for item in endpoints if str(item or "").strip().startswith("/")]
        for path in candidates:
            if not path_has_placeholders(path):
                return path
        return candidates[0] if candidates else ""

    @staticmethod
    def _preferred_write_endpoint(api_doc: str, entity: str) -> str:
        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        candidates = [
            str(item.get("path") or "")
            for item in endpoints
            if str(item.get("entity") or "") == entity
            and str(item.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
            and str(item.get("path") or "").startswith("/")
            and not path_has_placeholders(str(item.get("path") or ""))
        ]
        return candidates[0] if candidates else ""

    @staticmethod
    def _dependency_write_body(api_doc: str, entity: str, target_entity: str) -> dict[str, Any]:
        write_path = SemanticScenarioGenerator._preferred_write_endpoint(api_doc, entity)
        if not write_path:
            return {}
        # Runtime scenario generation consumes the normalized OpenAPI view.
        # Read its requestBody first; retain the Markdown extractor only for
        # direct callers that have not passed through document normalization.
        example = SemanticScenarioGenerator._runtime_body_template(
            api_doc,
            "POST",
            write_path,
        )
        if not example:
            example = _markdown_request_example(api_doc, "POST", write_path)
        if not isinstance(example, dict) or not example:
            return {}
        rendered = SemanticScenarioGenerator._bind_dependency_placeholders(example, target_entity)
        if SemanticScenarioGenerator._has_unresolved_dependency_placeholder(rendered):
            return {}
        return rendered

    @staticmethod
    def _bind_dependency_placeholders(value: Any, target_entity: str, field_name: str = "") -> Any:
        if isinstance(value, dict):
            return {
                str(key): SemanticScenarioGenerator._bind_dependency_placeholders(child, target_entity, str(key))
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [SemanticScenarioGenerator._bind_dependency_placeholders(child, target_entity, field_name) for child in value]
        if not isinstance(value, str):
            return value
        normalized_target = re.sub(r"[^a-z0-9]+", "", str(target_entity or "").lower())
        normalized_field = re.sub(r"[^a-z0-9]+", "", str(field_name or "").lower())

        def repl(match: re.Match[str]) -> str:
            placeholder = re.sub(r"[^a-z0-9]+", "", str(match.group(1) or "").lower())
            if normalized_target and normalized_target in placeholder and "id" in placeholder:
                return "{id}"
            return match.group(0)

        rendered = re.sub(r"<([A-Za-z_]\w*)>", repl, value)
        if normalized_target and normalized_target in normalized_field and "id" in normalized_field and rendered == value:
            return "{id}"
        return rendered

    @staticmethod
    def _has_unresolved_dependency_placeholder(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, dict):
            return any(
                SemanticScenarioGenerator._has_unresolved_dependency_placeholder(key)
                or SemanticScenarioGenerator._has_unresolved_dependency_placeholder(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(SemanticScenarioGenerator._has_unresolved_dependency_placeholder(child) for child in value)
        if not isinstance(value, str):
            return False
        normalized = normalize_path_placeholders(value)
        placeholders = re.findall(r"\{([A-Za-z_]\w*)\}", normalized)
        return any(str(name or "").strip().lower() != "id" for name in placeholders)

    @staticmethod
    def _id(*parts: Any) -> str:
        return "SCN_SRC_" + hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    # ── Section: 认证 & 登录模拟 ──

    def _resolve_entity_steps(
        path: str,
        *,
        actor: str,
        start_order: int = 1,
        api_doc: str = "",
        root: Any = None,
        project: str = "",
    ) -> tuple[list[ScenarioStep], str]:
        """Insert list-resolve steps so path placeholders can bind at runtime.

        Uses structural collection path first, then sibling catalog fallbacks
        (e.g. inventory/{sku} → products) when the collection itself is not
        listable. Nested admin write collections are deprioritized in favor of
        ``search`` / identity (``/me``) siblings. Field extraction follows the
        path parameter name. When ``api_doc`` is provided, also bootstrap-create
        missing entities so freshly reset databases can still bind identities.
        """
        normalized = normalize_path_placeholders(path)
        if not path_has_placeholders(normalized):
            return [], path
        extract_fields = extract_fields_for_path(normalized)
        candidates: list[str] = []
        primary = collection_path(normalized)
        if primary and primary != "/" and not primary.endswith("/api"):
            candidates.append(primary)
        candidates.extend(alternate_collection_paths(normalized))
        candidates = [item for item in dict.fromkeys(candidates) if item.startswith("/")]
        try:
            _, _, declared_endpoints = _api_facts(
                api_doc,
                re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I),
            )
        except Exception:
            declared_endpoints = []
        declared_read_paths = {
            normalize_path_placeholders(str(item.get("path") or ""))
            for item in declared_endpoints
            if str(item.get("method") or "").upper() in {"GET", "HEAD"}
        }
        declared_post_paths = {
            normalize_path_placeholders(str(item.get("path") or ""))
            for item in declared_endpoints
            if str(item.get("method") or "").upper() == "POST"
        }
        candidates = [
            item for item in candidates
            if normalize_path_placeholders(item) in declared_read_paths
        ]

        def _resolve_rank(candidate: str) -> tuple[int, int, int, int, int]:
            low = candidate.lower().split("?", 1)[0]
            is_search = low.endswith("/search")
            is_me = low.endswith("/me")
            prefer = 0 if (is_search or is_me) else 1
            # Prefer candidates that share resource tokens with the target path.
            target_tokens = {p.lower() for p in normalized.strip("/").split("/") if p and "{" not in p}
            cand_tokens = {p.lower() for p in low.strip("/").split("/") if p}
            shared = len(target_tokens & cand_tokens)
            # Nested admin write collections without search are last-resort.
            admin_write_penalty = 1 if ("/admin/" in low and not is_search) else 0
            # Param-stem collections (orderId → /api/orders) beat structural
            # parents that don't list the entity (/api/payments/order).
            params = infer_path_params(normalized)
            stem_hit = 0
            for param in params:
                key = re.sub(r"[^a-z0-9_]+", "", str(param or "").lower())
                stem = key[:-2].strip("_") if key.endswith("id") else key
                if not stem or stem in {"id", "uuid"}:
                    continue
                last = low.rstrip("/").rsplit("/", 1)[-1]
                depth = low.count("/")
                plural = stem + "s"
                if stem.endswith("y") and len(stem) > 1 and stem[-2] not in "aeiou":
                    plural = stem[:-1] + "ies"
                # Catalog-like identity (sku/code): prefer products/materials lists
                # over invented /api/sku which is rarely a real collection.
                if key in {"sku", "productsku", "materialcode", "itemcode", "partnumber"} or stem in {"sku", "code"}:
                    if last in {"products", "product", "materials", "material", "items", "goods", "skus", "catalog"}:
                        stem_hit = max(stem_hit, 3)
                    elif last == stem and depth <= 2:
                        stem_hit = max(stem_hit, 1)
                    continue
                # Strong match: /api/orders for orderId — not nested /api/payments/order.
                if last == plural:
                    stem_hit = max(stem_hit, 3)
                elif last == stem and depth <= 2:
                    stem_hit = max(stem_hit, 2)
                elif last in {stem, plural}:
                    stem_hit = max(stem_hit, 1)
            # Param-stem collections (orderId → /api/orders) beat invented
            # /search under structural parents that do not list the entity.
            return (-stem_hit, prefer, admin_write_penalty, -shared, low.count("/"))

        ranked = sorted(candidates, key=_resolve_rank)
        # Keep a true structural collection in the attempt set (inventory/{sku} →
        # /api/inventory) even when catalog siblings score higher. Nested false
        # stems (payments/order/{orderId}) stay demoted by ranking alone.
        if primary and primary in candidates:
            primary_last = primary.rstrip("/").rsplit("/", 1)[-1].lower()
            params = infer_path_params(normalized)
            param_stems: set[str] = set()
            for param in params:
                key = re.sub(r"[^a-z0-9_]+", "", str(param or "").lower())
                stem = key[:-2].strip("_") if key.endswith("id") else key
                if stem and stem not in {"sku", "code", "id", "uuid", "guid", "no", "key", "pk"}:
                    param_stems.add(stem)
                    param_stems.add(f"{stem}s")
            if primary_last not in param_stems and "/admin/" not in primary.lower():
                ranked = [primary] + [item for item in ranked if item != primary]
        from ai_test_asset_center.policy_wiring import get_policy_value

        attempt_limit = int(get_policy_value("execution", "precondition_resolution_attempts", 2) or 2)
        attempt_limit = max(1, min(attempt_limit, 5))
        steps: list[ScenarioStep] = []
        for index, candidate in enumerate(ranked[:attempt_limit]):
            steps.append(ScenarioStep(
                order=start_order + index,
                action="resolve_entity_id" if index == 0 else f"resolve_entity_id_alt_{index}",
                api_method="GET",
                api_path=candidate,
                extract_from_response=list(extract_fields),
                expected_status=200,
                actor=actor,
            ))
        order = start_order + len(steps)
        # Bootstrap-create for path params when lists may be empty after DB reset.
        bootstrap_added = False
        if api_doc:
            for param in infer_path_params(normalized):
                collections = body_field_collection_paths(param)
                if not collections:
                    # Generic ``{id}`` has no body-field collection mapping; use
                    # the structural parent (``/api/orders/{id}/cancel`` → POST
                    # ``/api/orders``) so freshly reset DBs can still bind.
                    primary = collection_path(normalized)
                    if primary and primary.startswith("/") and primary != "/":
                        collections = [primary]
                if not collections:
                    continue
                create_path = collections[0]
                if normalize_path_placeholders(create_path) not in declared_post_paths:
                    continue
                create_body = SemanticScenarioGenerator._bootstrap_create_body(
                    api_doc,
                    create_path,
                    root=root,
                    project=project,
                )
                nested = set(extract_body_binding_fields(create_body))
                if not isinstance(create_body, dict) or not create_body:
                    continue
                if param in nested or any(p.lower() == param.lower() for p in nested):
                    continue
                bind_steps, order = SemanticScenarioGenerator._body_binding_resolve_steps(
                    create_body,
                    actor=actor,
                    start_order=order,
                    api_doc=api_doc,
                    root=root,
                    project=project,
                )
                steps.extend(bind_steps)
                steps.append(ScenarioStep(
                    order=order,
                    action=f"bootstrap_create_{param}",
                    api_method="POST",
                    api_path=create_path,
                    body_template=dict(create_body),
                    extract_from_response=list(extract_fields),
                    expected_status=200,
                    actor=actor,
                ))
                order += 1
                bootstrap_added = True
                break
            if not bootstrap_added:
                for param in infer_path_params(normalized):
                    candidate = SemanticScenarioGenerator._identity_create_fixture_candidate(
                        api_doc,
                        normalized,
                        root=root,
                        project=project,
                    )
                    if candidate is None:
                        continue
                    create_path, create_body, body_provenance = candidate
                    bind_steps, order = SemanticScenarioGenerator._body_binding_resolve_steps(
                        create_body,
                        actor=actor,
                        start_order=order,
                        api_doc=api_doc,
                        root=root,
                        project=project,
                    )
                    steps.extend(bind_steps)
                    steps.append(ScenarioStep(
                        order=order,
                        action=f"bootstrap_create_{param}",
                        api_method="POST",
                        api_path=create_path,
                        body_template=dict(create_body),
                        body_provenance=body_provenance,
                        extract_from_response=list(extract_fields),
                        expected_status=200,
                        actor=actor,
                    ))
                    order += 1
                    bootstrap_added = True
                    break
        return steps, normalized

    # ── Supplementary scenario builders for non-state-machine slice kinds ──

    @staticmethod
    def _fill_login_body(template: dict[str, Any], identifier: str, password: str) -> dict[str, Any]:
        """Fill a login request body using the endpoint's documented field names.

        No hardcoded {email,password} assumption. Maps by field-name semantics:
          - a key that looks password-like        → the password value
          - the remaining identity key(s)          → the account identifier
        Falls back to {email, password} only when the API declares no schema.
        """
        tpl = dict(template or {})
        if not tpl:
            return {"email": identifier, "password": password}
        pass_tokens = ("pass", "pwd", "secret", "credential", "token", "密码")
        out: dict[str, Any] = {}
        for key in tpl:
            kl = str(key).lower()
            if any(tok in kl for tok in pass_tokens):
                out[key] = password
            else:
                out[key] = identifier
        return out

    @staticmethod
    def _build_login_step(
        login_path: str,
        body_template: dict[str, Any],
        identifier: str,
        password: str,
        order: int = 1,
        *,
        actor: str = "readonly",
    ) -> ScenarioStep | None:
        """Build a login step using the project-documented field names, not hardcoded {email,password}."""
        if not login_path.startswith("/") or not identifier:
            return None
        return ScenarioStep(
            order=order, action="login",
            api_method="POST", api_path=login_path,
            body_template=SemanticScenarioGenerator._fill_login_body(body_template, identifier, password),
            extract_from_response=["token"],
            expected_status=200, actor=actor or "readonly",
        )

    @staticmethod
    # ── Section: 切片生成器 (权限/账号/隔离/并发/资金/库存) ──

    def _permission_slice(
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str = "",
        *,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        """Build an actor-permission scenario from a permission slice.

        The scenario logs in as the declared actor, hits the target write
        endpoint, and expects a 401/403.  The PermissionOracle flags a
        200 as a privilege-escalation defect.
        """
        entity = str(slice_meta.get("entity") or "").strip()
        if not entity:
            return None
        actor_label = str(slice_meta.get("_permission_actor") or "").strip()
        email = str(slice_meta.get("_permission_email") or "").strip()
        password = str(slice_meta.get("_permission_password") or "").strip()
        method = str(slice_meta.get("_permission_method") or "").upper()
        path = str(slice_meta.get("_permission_path") or "")
        expected_permitted = slice_meta.get("_permission_expected_permitted") or []
        denied = "*" not in expected_permitted and method not in expected_permitted
        if not method or not path.startswith("/"):
            return None
        actor_binding = "declared_actor" if actor_label else "runtime_role_sweep"
        probe_actor = actor_label or "runtime_role"
        steps: list[ScenarioStep] = []
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        if actor_label:
            step = SemanticScenarioGenerator._build_login_step(
                login_path, login_body, email, password, order=1, actor=actor_label)
            if step:
                steps.append(step)
        # If the target path contains a :param placeholder, insert a pre-step
        # that list-observes the collection endpoint to bind a real id at
        # runtime — otherwise the probe would send a literal :id to the server.
        probe_path = path
        resolve_steps, probe_path = SemanticScenarioGenerator._resolve_entity_steps(
            path, actor=probe_actor, start_order=len(steps) + 1, api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.extend(resolve_steps)
        expected_status = (200 if not denied else 403) if actor_label else 0
        SemanticScenarioGenerator._append_write_probe_step(
            steps,
            action=f"permission_probe_{probe_actor}",
            method=method,
            path=probe_path,
            actor=probe_actor,
            expected_status=expected_status,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        execution_policy = (
            "approved_sandbox_write"
            if method in {"POST", "PUT", "PATCH", "DELETE"}
            else "safe_read_only"
        )
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "permission", probe_actor, method, path),
            title=f"[Actor permission probe] {actor_label} → {method} {path}",
            description=f"验证角色 {actor_label} 是否被允许执行 {method} {path}",
            category="permission",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label] if actor_label else [],
            steps=steps,
            oracle_rules=[
                "PermissionOracle.role_boundary_check",
                f"expected_permitted={','.join(expected_permitted) if expected_permitted else 'runtime_observed'}",
                f"permission_actor_binding={actor_binding}",
            ],
            confidence=float(slice_meta.get("priority") or 0.85),
            execution_policy=execution_policy,
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="permission",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
            runtime_hints={"permission_actor_binding": actor_binding},
        )

    @staticmethod
    def _account_status_slice(
        slice_meta: dict[str, Any], discovery_round: int,
    ) -> ExecutableScenario | None:
        """Fresh login for DISABLED/LOCKED accounts — must be rejected (no token)."""
        login_path = str(slice_meta.get("_login_path") or "").strip()
        email = str(slice_meta.get("_account_status_email") or "").strip()
        password = str(slice_meta.get("_account_status_password") or "").strip()
        role = str(slice_meta.get("_account_status_role") or "").strip()
        status = str(slice_meta.get("_account_status") or "").strip().upper()
        if not login_path.startswith("/") or not email or not password:
            return None
        login_body = dict(slice_meta.get("_login_body") or {})
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1,
        )
        if step is None:
            return None
        step.expected_status = 403
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id("auth", "account_status", role, "POST", login_path),
            title=f"[Account status probe] {status} account {email} must not login",
            description=f"验证 {status} 账号 {email} 调用登录接口应被拒绝，不得返回有效 token",
            category="authorization_access_control",
            severity="P0",
            entity="auth",
            preconditions=[],
            actors=[role],
            steps=[step],
            oracle_rules=[
                "PermissionOracle.role_boundary_check",
                f"account_status={status}",
                "login_must_reject_disabled_or_locked",
            ],
            confidence=float(slice_meta.get("priority") or 0.95),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="account_status",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _isolation_slice(
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
        *,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        """Build a cross-user isolation scenario.

        Owner authenticates and seeds an entity id; viewer then attempts to read
        the owner's resource (path or ownership query param). TenantIsolationOracle
        flags cross-user data leakage.
        """
        entity = str(slice_meta.get("entity") or "").strip()
        if not entity:
            return None
        viewer_label = str(slice_meta.get("_isolation_viewer_role") or "").strip()
        viewer_email = str(slice_meta.get("_isolation_viewer_email") or "").strip()
        viewer_password = str(slice_meta.get("_isolation_viewer_password") or "").strip()
        owner_label = str(slice_meta.get("_isolation_owner_role") or "").strip()
        owner_email = str(slice_meta.get("_isolation_owner_email") or "").strip()
        owner_password = str(slice_meta.get("_isolation_owner_password") or "").strip()
        path = str(slice_meta.get("_isolation_path") or "")
        mode = str(slice_meta.get("_isolation_mode") or "path").strip().lower()
        query_param = str(slice_meta.get("_isolation_query_param") or "").strip()
        if not viewer_label or not path.startswith("/"):
            return None
        # Isolation without a concrete foreign identity binder is a owned-collection
        # probe: the viewer may list their own resources (HTTP 200) while
        # TenantIsolationOracle checks for foreign-id leakage.
        if mode in {"", "path"} and not path_has_placeholders(normalize_path_placeholders(path)) and not query_param:
            mode = "owned_collection"
        steps: list[ScenarioStep] = []
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        order = 1
        if owner_email and owner_password and login_path:
            owner_login = SemanticScenarioGenerator._build_login_step(
                login_path, login_body, owner_email, owner_password, order=order, actor=owner_label or owner_email)
            if owner_login:
                owner_login.actor = owner_label or owner_email
                steps.append(owner_login)
                order += 1
            if mode == "query_param" and query_param:
                identity_path = str(slice_meta.get("_isolation_identity_path") or "").strip()
                if identity_path.startswith("/"):
                    steps.append(ScenarioStep(
                        order=order,
                        action="resolve_owner_identity",
                        api_method="GET",
                        api_path=identity_path,
                        extract_from_response=["id", "userId", "user_id"],
                        expected_status=200,
                        actor=owner_label or owner_email,
                    ))
                    order += 1
            elif mode == "owned_collection":
                steps.append(ScenarioStep(
                    order=order,
                    action="resolve_owner_collection_ids",
                    api_method="GET",
                    api_path=path,
                    extract_from_response=["id", "status", "state"],
                    expected_status=200,
                    actor=owner_label or owner_email,
                ))
                order += 1
            elif path_has_placeholders(normalize_path_placeholders(path)):
                resolve_steps, _ = SemanticScenarioGenerator._resolve_entity_steps(
                    path, actor=owner_label or owner_email, start_order=order, api_doc=api_doc,
                    root=root,
                    project=project,
                )
                for resolve_step in resolve_steps:
                    if str(resolve_step.action or "").startswith("resolve_entity_id"):
                        resolve_step.action = str(resolve_step.action).replace(
                            "resolve_entity_id",
                            "resolve_owner_entity_id",
                            1,
                        )
                    steps.append(resolve_step)
                    order += 1
        viewer_login = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, viewer_email, viewer_password, order=order, actor=viewer_label)
        if viewer_login:
            viewer_login.actor = viewer_label
            steps.append(viewer_login)
            order += 1
        probe_path = path
        # Owned collection lists are allowed for the viewer; leakage is decided
        # by TenantIsolationOracle against owner-seeded identities. Cross-user
        # path/query probes still expect an authz denial.
        expected_status = 200 if mode == "owned_collection" else 403
        if mode == "query_param" and query_param:
            owner_binding = "id"
            probe_path = f"{path}?{query_param}={{{owner_binding}}}"
        elif mode == "owned_collection":
            probe_path = path
        elif path_has_placeholders(normalize_path_placeholders(path)):
            probe_path = normalize_path_placeholders(path)
        steps.append(ScenarioStep(
            order=order,
            action=f"isolation_probe_{viewer_label}",
            api_method="GET",
            api_path=probe_path,
            expected_status=expected_status,
            actor=viewer_label,
        ))
        actors = [label for label in (viewer_label, owner_label) if label]
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "isolation", viewer_label, path),
            title=f"[Data isolation probe] {viewer_label} → GET {probe_path}",
            description=f"验证用户 {viewer_label} 不应访问其他用户的私有数据",
            category="isolation",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=actors,
            steps=steps,
            oracle_rules=["TenantIsolationOracle.cross_user_isolation"],
            confidence=float(slice_meta.get("priority") or 0.88),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="isolation",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _concurrency_slice(
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str = "",
        *,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        """Build a double-write scenario to probe concurrency/mutual exclusion."""
        entity = str(slice_meta.get("entity") or "").strip()
        method = str(slice_meta.get("_concurrency_method") or "").upper()
        path = str(slice_meta.get("_concurrency_path") or "")
        if not entity or not method or not path.startswith("/"):
            return None
        actor_label = str(slice_meta.get("_default_actor") or "readonly").strip() or "readonly"
        email = str(slice_meta.get("_default_email") or "").strip()
        password = str(slice_meta.get("_default_password") or "").strip()
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        steps: list[ScenarioStep] = []
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1, actor=actor_label)
        if step:
            steps.append(step)
        probe_path = path
        resolve_steps, probe_path = SemanticScenarioGenerator._resolve_entity_steps(
            path,
            actor=actor_label,
            start_order=len(steps) + 1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.extend(resolve_steps)
        if method == "POST":
            probe_body, body_provenance = SemanticScenarioGenerator._bootstrap_create_body_with_provenance(
                api_doc,
                probe_path,
                root=root,
                project=project,
            )
        else:
            probe_body, body_provenance = SemanticScenarioGenerator._runtime_body_template_with_provenance(
                api_doc,
                method,
                probe_path,
                root=root,
                project=project,
            )
        binding_steps, _ = SemanticScenarioGenerator._body_binding_resolve_steps(
            probe_body,
            actor=actor_label,
            start_order=len(steps) + 1,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.extend(binding_steps)
        base = len(steps)
        for i in (1, 2):
            steps.append(ScenarioStep(
                order=base + i,
                action=f"concurrent_{method}_{i}",
                api_method=method, api_path=probe_path,
                body_template=dict(probe_body),
                body_provenance=body_provenance,
                expected_status=(200 if i == 1 else 409),
                actor=actor_label,
            ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "concurrency", method, path),
            title=f"[Concurrency probe] double {method} {path}",
            description=f"并发双发 {method} {path} 验证互斥或幂等行为",
            category="concurrency",
            is_concurrent=True,
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label],
            steps=steps,
            oracle_rules=["ConcurrencyOracle.race_condition_check"],
            confidence=float(slice_meta.get("priority") or 0.78),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="concurrency",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _money_slice(
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str = "",
        *,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        """Build a financial-integrity observation scenario.

        Hits a write endpoint and lets MoneyOracle detect negative amounts,
        double-refund patterns, and balance anomalies in the responses. No
        assumption about which endpoints are financial — the oracle decides.
        """
        entity = str(slice_meta.get("entity") or "").strip()
        method = str(slice_meta.get("_money_method") or "").upper()
        path = str(slice_meta.get("_money_path") or "")
        if not entity or not method or not path.startswith("/"):
            return None
        actor_label = str(slice_meta.get("_default_actor") or "readonly").strip() or "readonly"
        email = str(slice_meta.get("_default_email") or "").strip()
        password = str(slice_meta.get("_default_password") or "").strip()
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        # Observation endpoint = documented GET near the write path when
        # available; fall back to structural candidates only when the source
        # catalog has no read surface for that resource.
        documented_reads = _documented_observation_read_candidates(path, api_doc)
        read_path = documented_reads[0] if documented_reads else _adjacent_read_for_entity(entity, path)
        probe_read = read_path
        probe_write = path
        steps: list[ScenarioStep] = []
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1, actor=actor_label)
        if step:
            step.actor = actor_label
            steps.append(step)
        path_needs_binding = path_has_placeholders(normalize_path_placeholders(path))
        read_is_concrete = not path_has_placeholders(normalize_path_placeholders(read_path))
        resolve_target = path if path_needs_binding else (read_path if read_is_concrete else "")
        if resolve_target:
            resolve_steps, normalized_target = SemanticScenarioGenerator._resolve_entity_steps(
                resolve_target,
                actor=actor_label,
                start_order=len(steps) + 1,
                api_doc=api_doc,
                root=root,
                project=project,
            )
            steps.extend(resolve_steps)
            if path_needs_binding:
                probe_write = normalized_target
        observe_candidates = documented_reads or _observation_read_candidates(path)
        if observe_candidates:
            probe_read = observe_candidates[0]
        if path_has_placeholders(normalize_path_placeholders(probe_read)):
            probe_read = normalize_path_placeholders(probe_read)
        can_observe_before = (
            not path_has_placeholders(normalize_path_placeholders(probe_read))
            or path_has_placeholders(normalize_path_placeholders(path))
        )
        if can_observe_before:
            steps.append(ScenarioStep(
                order=len(steps) + 1, action="observe_money_endpoint",
                api_method="GET", api_path=probe_read, expected_status=200, actor=actor_label,
            ))
        SemanticScenarioGenerator._append_write_probe_step(
            steps,
            action=f"money_probe_{method}",
            method=method,
            path=probe_write,
            actor=actor_label,
            expected_status=200,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.append(ScenarioStep(
            order=len(steps) + 1, action="observe_money_after",
            api_method="GET", api_path=probe_read, expected_status=200, actor=actor_label,
        ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "money", method, path),
            title=f"[Financial integrity probe] {method} {path}",
            description="验证资金操作的余额一致性、金额非负、无重复扣款",
            category="money_quantity_conservation",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label],
            steps=steps,
            oracle_rules=[
                "MoneyOracle.financial_integrity",
                "SystemPromiseOracle.dimension:money_quantity_conservation",
            ],
            confidence=float(slice_meta.get("priority") or 0.82),
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="money",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _inventory_slice(
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str = "",
        *,
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario | None:
        """Build an inventory-integrity observation scenario."""
        entity = str(slice_meta.get("entity") or "").strip()
        method = str(slice_meta.get("_inventory_method") or "").upper()
        path = str(slice_meta.get("_inventory_path") or "")
        if not entity or not method or not path.startswith("/"):
            return None
        actor_label = str(slice_meta.get("_default_actor") or "readonly").strip() or "readonly"
        email = str(slice_meta.get("_default_email") or "").strip()
        password = str(slice_meta.get("_default_password") or "").strip()
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        read_path = _adjacent_read_for_entity(entity, path)
        probe_read = read_path
        probe_write = path
        steps: list[ScenarioStep] = []
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1, actor=actor_label)
        if step:
            step.actor = actor_label
            steps.append(step)
        resolve_target = path if path_has_placeholders(normalize_path_placeholders(path)) else read_path
        resolve_steps, normalized_target = SemanticScenarioGenerator._resolve_entity_steps(
            resolve_target, actor=actor_label, start_order=len(steps) + 1, api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.extend(resolve_steps)
        if path_has_placeholders(normalize_path_placeholders(path)):
            probe_write = normalized_target
        observe_candidates = _observation_read_candidates(path)
        if observe_candidates:
            probe_read = observe_candidates[0]
        if path_has_placeholders(normalize_path_placeholders(probe_read)):
            probe_read = normalize_path_placeholders(probe_read)
        steps.append(ScenarioStep(
            order=len(steps) + 1, action="observe_inventory_endpoint",
            api_method="GET", api_path=probe_read, expected_status=200, actor=actor_label,
        ))
        SemanticScenarioGenerator._append_write_probe_step(
            steps,
            action=f"inventory_probe_{method}",
            method=method,
            path=probe_write,
            actor=actor_label,
            expected_status=200,
            api_doc=api_doc,
            root=root,
            project=project,
        )
        steps.append(ScenarioStep(
            order=len(steps) + 1, action="observe_inventory_after",
            api_method="GET", api_path=probe_read, expected_status=200, actor=actor_label,
        ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "inventory", method, path),
            title=f"[Inventory integrity probe] {method} {path}",
            description="验证库存预占/扣减/释放的非负性与一致性",
            category="inventory",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label],
            steps=steps,
            oracle_rules=["InventoryOracle.stock_integrity"],
            confidence=float(slice_meta.get("priority") or 0.84),
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="inventory",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )


