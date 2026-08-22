"""
QualiBug Self-Improving Loop v2 — with heartbeat, progress tracking, incremental save
"""

from __future__ import annotations

import json, time, sys, os, traceback, logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import hashlib
from .v12_pipeline import run_v12_pipeline
from .target_endpoint import resolve_target_base_url
from .loop_runtime import LoopBusyError, LoopRuntimeError, LoopRuntimeSession
from .console_output import safe_print


HEARTBEAT_FILE_TEMPLATE = "platform_outputs/{project_id}/.loop_heartbeat.json"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT_ID = os.environ.get("QUALIBUG_DEFAULT_PROJECT_ID", "default_project")
_ACTIVE_RUNTIME: LoopRuntimeSession | None = None


class DiscoveryRunError(RuntimeError):
    """A discovery failure that must never be converted into a convergence result."""


def _console(message: object = "") -> None:
    """Best-effort operational logging that never aborts a supervised loop."""
    safe_print(str(message), flush=True)


def _resolve_default_project_doc_path(
    project_id: str,
    candidates: list[str],
    *,
    search_root: Path | None = None,
) -> str:
    """Resolve project input artifacts from common workspace locations."""
    root = Path(search_root or REPO_ROOT)
    clean_project = str(project_id or DEFAULT_PROJECT_ID).strip() or DEFAULT_PROJECT_ID
    search_dirs = [
        root / "platform_workspace" / clean_project / "input",
        root / "platform_inputs" / clean_project,
        root / "projects" / clean_project / "input",
        root / "input",
    ]
    for directory in search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for candidate in candidates:
            path = directory / candidate
            if path.exists() and path.is_file():
                return str(path)
    return ""


def _read_optional_text(file_path: str | Path | None) -> str:
    if not file_path:
        return ""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _fallback_heartbeat_path(project_id: str = DEFAULT_PROJECT_ID) -> Path:
    return Path(HEARTBEAT_FILE_TEMPLATE.format(project_id=project_id))


def _tick(step: str, detail: str = "", round_num: int = 0):
    """Persist progress without hiding heartbeat failures.

    Long Reader/Reasoner stages are kept alive by LoopRuntimeSession's background
    heartbeat pump.  This function records semantic stage transitions.
    """
    runtime = _ACTIVE_RUNTIME
    if runtime is not None:
        runtime.assert_healthy()
        runtime.heartbeat(step=step, detail=detail, round_num=round_num)
    else:
        # Backward-compatible fallback for direct unit calls.  Never silently
        # swallow a write failure because that would make watchdog data untrustworthy.
        path = _fallback_heartbeat_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(), "step": step, "detail": detail[:500],
            "round": round_num, "pid": os.getpid(), "status": "RUNNING",
            "last_progress_at": time.time(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    marker = "[IMP]" if step == "improve" else ("[BUG]" if step == "verify" else "[...]")
    _console(f"  {marker} [{step}] {detail[:120]}")


@dataclass
class ImprovementAction:
    target: str
    problem: str
    change: str
    expected_effect: str


@dataclass
class ImproveRound:
    round_num: int
    bugs_found: int
    inconclusive_rate: float
    actions: list[ImprovementAction]
    after_verdict: str


class SelfImprovingSweep:
    MAX_ROUNDS = 5
    CONVERGED_THRESHOLD = 0.30

    def __init__(self, prd_path=None, api_path=None, base_url: str | None = None, *, project_id: str = DEFAULT_PROJECT_ID, output_dir: Path | str | None = None):
        if prd_path is None:
            prd_path = _resolve_default_project_doc_path(
                project_id,
                ["PRD.md", "prd.md", "requirements.md", "business_requirements.md"],
            )
        if api_path is None:
            api_path = _resolve_default_project_doc_path(
                project_id,
                ["openapi.json", "openapi.yaml", "openapi.yml", "API_SPEC.md", "API.md"],
            )

        self.prd = _read_optional_text(prd_path)
        self.api = _read_optional_text(api_path)
        self.base_url = resolve_target_base_url(base_url)
        self.project_id = str(project_id or DEFAULT_PROJECT_ID).strip() or DEFAULT_PROJECT_ID
        self.output_dir = Path(output_dir or Path("platform_outputs") / self.project_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rounds: list[ImproveRound] = []
        self._prior_inconclusive_rate = 1.0
        self._last_discovery_result: dict[str, Any] = {}
        # Phase91: discovery remains the only executor; this is a read-only
        # observation of the graph/frontier decision that selected the round.
        self._cognitive_graph_observation: dict[str, Any] = {}
        
        # Phase79+: Auto-compile project context on init
        self._context = self._compile_context()

    def _compile_context(self) -> dict:
        """Phase79+: Auto-compile project context from PRD+OpenAPI before discovery."""
        try:
            from .project_context_compiler import ProjectContextCompiler
            from .api_capability_mapper import APICapabilityMapper
            from .onboarding_modules import (
                ObserverCandidateBuilder, BindingCandidateBuilder,
                FixtureReadinessAnalyzer, VerificationCoverageAnalyzer,
            )
            
            compiler = ProjectContextCompiler()
            openapi_spec = {}
            try: openapi_spec = json.loads(self.api) if self.api.strip().startswith("{") else {}
            except Exception:
                pass
            
            ctx = compiler.compile(self.prd[:5000], openapi_spec, "")
            
            mapper = APICapabilityMapper()
            apis = mapper.map_from_openapi(openapi_spec) if openapi_spec else []
            
            obs = ObserverCandidateBuilder().build(apis, ctx.entities)
            bindings = BindingCandidateBuilder().build(apis, ctx.entities)
            readiness = FixtureReadinessAnalyzer().analyze(bindings, obs, apis)
            coverage = VerificationCoverageAnalyzer().analyze(ctx)
            
            return {
                "entities": len(ctx.entities),
                "apis": len(apis),
                "observers": len(obs),
                "bindings": len(bindings),
                "ready_flows": sum(1 for r in readiness if r.readiness == "READY"),
                "coverage": coverage.get("entity_coverage", {}).get("rate", 0),
            }
        except Exception:
            return {}

    def _observe(self, existing_findings: list[dict] | None = None) -> tuple[list[dict], int, float]:
        _tick("observe", "Starting unified mainline discovery...")
        last_error: Exception | None = None
        result: dict[str, Any] | None = None
        api_hash = hashlib.sha256((self.api or "").encode("utf-8")).hexdigest()
        ctx = {
            "mainline_authority": "experiment_candidate",
            "run_id": f"self-improving-{self.project_id}",
            "target_id": self.project_id,
            "environment_id": "self_improving_env",
            "policy_version": "v1.0.0-baseline",
            "evaluation_mode": "local_sweep",
            "scope_id": self.project_id,
            "environment_ref": self.project_id,
            "environment_kind": "test",
            "approved_base_url": self.base_url,
            "execution_mode": "safe_read_only",
            "source_manifest": {
                "source_id": "self_improving_loop",
                "source_hash": api_hash,
                "source_origin": "declared_manifest",
                "source_version_id": "1",
            },
        }
        for attempt in range(2):
            try:
                result = run_v12_pipeline(
                    self.project_id,
                    REPO_ROOT,
                    self.prd,
                    self.api,
                    base_url=self.base_url,
                    campaign_context=ctx,
                    existing_findings=list(existing_findings or []),
                )
                if not isinstance(result, dict):
                    raise DiscoveryRunError("run_v12_pipeline returned a non-dict result")
                # Fail-fast (principle 1): a non-approved / non-production target
                # must surface loudly, never silently yield zero findings.
                rc = result.get("runtime_contract") or {}
                if str(rc.get("status")) != "approved":
                    raise DiscoveryRunError(
                        f"runtime contract not approved: status={rc.get('status')} "
                        f"reason={rc.get('reason')} missing={rc.get('missing_requirements')}"
                    )
                executed = int((result.get("phases") or {}).get("execution", {}).get("executed") or 0)
                if executed <= 0:
                    raise DiscoveryRunError(
                        "v12 mainline executed 0 experiments; target not approved/non-production"
                    )
                break
            except Exception as exc:
                last_error = exc
                _console(f"  [FAIL] discovery failed (attempt {attempt + 1}): {exc}")
                if attempt == 0:
                    import gc
                    gc.collect()
                    time.sleep(2)
        if result is None:
            raise DiscoveryRunError("discovery failed after retry: %s" % (last_error or "unknown failure"))

        self._last_discovery_result = result
        fp = result.get("formal_count_projection") or {}
        self._last_raw_confirmed_signals = int(fp.get("executed_clue_count") or 0)
        self._last_validated_candidates = len(result.get("candidate_findings") or [])
        self._last_needs_more_evidence = 0
        self._cognitive_graph_observation = {
            "available": False,
            "mode": "off",
            "frontier": None,
            "context_refs": [],
            "ab": {},
            "reason": "mainline_observation_disabled",
        }
        findings = list(result.get("delivery_occurrences") or [])
        total = len(findings) or 1
        validated_candidates = 0
        unresolved = len(result.get("candidate_findings") or [])
        inconclusive_rate = unresolved / total
        import gc
        gc.collect()
        _tick("observed", (
            f"{validated_candidates} validated candidates, "
            f"{getattr(self, '_last_raw_confirmed_signals', 0)} raw confirmed signals, "
            f"{inconclusive_rate:.0%} unresolved (total: {total})"
        ))
        return findings, validated_candidates, inconclusive_rate

    def _diagnose(self, findings: list[dict]) -> list[ImprovementAction]:
        _tick("diagnose", f"Analyzing {len(findings)} findings...")
        actions = []

        # Level 0 engine-health diagnostics are no longer available: the unified
        # mainline does not expose a per-engine report. Contract-approval and
        # execution failures are surfaced via the fail-fast check in _observe.

        # ── Level 1: Finding-level patterns ──
        inconclusives = [f for f in findings if str(f.get("verdict", "")) == "inconclusive"]
        evidence_texts = [str((f.get("evidence") or {}).get("actual", "")) for f in inconclusives]

        route_404_count = sum(1 for t in evidence_texts if "404" in t)
        if route_404_count > len(inconclusives) * 0.3:
            actions.append(ImprovementAction("route_map",
                f"{route_404_count}/{len(inconclusives)} inconclusive due to 404",
                "Expand fuzzy matching + inject OpenAPI paths into Reasoner prompt",
                f"Reduce ~{route_404_count} inconclusive"))

        side_effect_count = sum(1 for t in evidence_texts if "副作用" in t or "需验证" in t or "编排" in t)
        if side_effect_count > len(inconclusives) * 0.2:
            actions.append(ImprovementAction("scenario",
                f"{side_effect_count} need state orchestration",
                "Auto-attach scenario_runner after Verifier",
                f"Confirm ~{side_effect_count} orchestration-needed hypotheses"))

        fake_terms = ["租户", "tenant", "金额字段", "用户列表与用户详情"]
        fake_count = sum(1 for f in inconclusives if any(t in str(f.get("title", "")).lower() for t in fake_terms))
        if fake_count > 0:
            actions.append(ImprovementAction("prompt",
                f"{fake_count} hallucinated (multi-tenant / financial on single-tenant MES)",
                "Inject MES context: 'single-tenant, no financial fields, no payment'",
                f"Reduce ~{fake_count} hallucinations"))

        if len(inconclusives) > len(findings) * 0.5:
            actions.append(ImprovementAction("evidence_plan",
                f"Inconclusive rate {100*len(inconclusives)//max(len(findings),1)}% > 50%",
                "Add missing observers, bindings, and async evidence before re-running",
                "Increase determinacy without lowering the confirmed-bug evidence bar"))

        raw_confirmed = int(getattr(self, "_last_raw_confirmed_signals", 0) or 0)
        needs_more = int(getattr(self, "_last_needs_more_evidence", 0) or 0)
        validated = int(getattr(self, "_last_validated_candidates", 0) or 0)
        # Trigger the bridge evolution only when the gate is broadly blocking
        # runtime-confirmed signals.  Once validated candidates are flowing, the
        # remaining pending items should be handled by targeted evidence work, not
        # repeated generic bridge candidates that create noisy STUCK loops.
        if raw_confirmed > 0 and validated == 0 and needs_more >= max(1, raw_confirmed // 2):
            actions.append(ImprovementAction("evidence_bridge",
                f"{raw_confirmed} runtime-confirmed signals were held as needs_more_evidence by the evidence gate",
                "Generate auth-boundary business-evidence contracts: request/response refs, role matrix, sensitivity tags, and reviewer-ready reproduction",
                "Convert strong runtime signals into validated candidates without relaxing the gate"))

        _tick("diagnose", f"Found {len(actions)} improvements" if actions else "No improvements needed")
        return actions

    def _improve(self, actions: list[ImprovementAction]) -> int:
        applied = 0
        for a in actions:
            try:
                if a.target in {"route_map", "engine", "prompt", "scenario", "evidence_plan", "evidence_bridge"}:
                    # Runtime actions are recorded as a candidate policy only.
                    # Do not mutate a live client/global prompt in the middle of a
                    # run; Champion/Challenger evaluation decides later whether a
                    # candidate can become active.
                    self._evidence_plan_required = True
                applied += 1
                _tick("improve", f"{a.target}: {a.change[:80]}")
            except Exception as e:
                _console(f"    ✗ {a.target}: {e}")
        
        # Phase81: Persist improvements to Policy Registry for versioned evolution
        self._save_to_policy_registry(actions)
        
        return applied

    def _save_to_policy_registry(self, actions: list[ImprovementAction]):
        """Record a candidate only; never auto-promote or relax evidence gates."""
        if not actions:
            return
        try:
            from .policy_registry import PolicyRecord, get_policy_registry
            import copy
            reg = get_policy_registry()
            active_record = reg.get_active()
            if active_record is None:
                return
            candidate = copy.deepcopy(active_record.strategy)
            for a in actions:
                if a.target in ("engine", "route_map"):
                    candidate.execution.max_tokens = max(candidate.execution.max_tokens, 32768)
                elif a.target == "scenario":
                    candidate.verification.scenario_auto = True
                elif a.target == "evidence_bridge":
                    order = list(candidate.verification.evidence_collection_order or [])
                    for required in ("auth_boundary_matrix", "response_sensitivity", "reproduction_trace"):
                        if required not in order:
                            order.append(required)
                    candidate.verification.evidence_collection_order = order
                elif a.target == "prompt":
                    candidate.discovery.dedicated_threshold = min(candidate.discovery.dedicated_threshold + 0.05, 0.95)
                # evidence_plan intentionally changes no evidence threshold.
            pid = f"policy-{int(time.time())}"
            reg.register(PolicyRecord(
                policy_id=pid,
                policy_version=f"v{int(time.time())}",
                parent_policy_version=active_record.policy_version,
                project_scope="global",
                status="candidate",
                created_reason=f"Runtime candidate: {len(actions)} actions; independent evaluation required",
                strategy=candidate,
            ))
            _console(f"  [POLICY] Policy candidate recorded: {pid} (not auto-promoted)")
        except Exception as exc:
            # Candidate persistence is non-critical; report it but do not hide it.
            _console(f"  [WARN] Policy candidate not recorded: {exc}")

    def _inject_context(self):
        """Deprecated compatibility hook: use only explicit project context guards.

        The self-improving loop must not mutate global prompts with MES-specific
        assumptions.  DiscoveryEngine reads the reviewed guard, if any, from
        ``QUALIBUG_PROJECT_CONTEXT_GUARD`` and dynamic Project Context Artifacts.
        """
        return None

    def _verify_improvement(self, before_rate: float, after_rate: float,
                            before_bugs: int, after_bugs: int) -> str:
        # A local sweep cannot prove a strategy improvement.  It may only report
        # an observation and queue a candidate for independent evaluation.
        if after_rate < before_rate * 0.7 and after_bugs >= before_bugs:
            return "CANDIDATE_REQUIRES_EVALUATION"
        if after_rate < self.CONVERGED_THRESHOLD:
            return "NO_FURTHER_LOCAL_ACTION"
        return "STUCK"

    def _save_progress(self):
        data = {
            "rounds": [{"round": r.round_num, "bugs": r.bugs_found,
                        "inconclusive_rate": r.inconclusive_rate,
                        "verdict": r.after_verdict,
                        "actions": [a.target for a in r.actions]}
                       for r in self.rounds],
            "updated_at": time.time(),
        }
        try:
            with open(str(self.output_dir / ".loop_progress.json"), "w") as f:
                json.dump(data, f, indent=2)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Failed to save loop progress: %s", exc)

    def _load_state(self) -> dict:
        """Load previous round progress for resumable execution."""
        try:
            data = json.loads(Path(str(self.output_dir / ".loop_progress.json")).read_text())
            rounds = data.get("rounds", [])
            if rounds:
                return {"round": rounds[-1]["round"], "actions": rounds[-1].get("actions", [])}
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.debug("No resumable loop state: %s", exc)
        return {}

    def _write_report(self, result: dict) -> None:
        path = self.output_dir / "self_improving_report.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def _failure_result(self, exc: BaseException) -> dict:
        result = {
            "rounds": len(self.rounds),
            "total_improvements": 0,
            "total_bugs": 0,
            "terminal": "FAILED_RETRYABLE",
            "execution_status": "FAILED_RETRYABLE",
            "failure_stage": getattr(_ACTIVE_RUNTIME, "_step", "unknown"),
            "error": str(exc),
            "error_type": type(exc).__name__,
            "actions": [],
            "findings": [],
        }
        self._write_report(result)
        return result

    def _run_once(self) -> dict:
        _console("=" * 60)
        _console("QualiBug Self-Improving Loop v3 (supervised 1-round mode)")
        _console("=" * 60)
        self._scenario_auto = False
        self._evidence_plan_required = False
        all_actions: list[ImprovementAction] = []

        prev_state = self._load_state()
        start_round = prev_state.get("round", 0) + 1
        rd = min(start_round, self.MAX_ROUNDS)
        _tick("round_start", f"Round {rd}", rd)

        findings, bugs, inconclusive_rate = self._observe(existing_findings=self._prior_findings)
        _tick("observed", f"Round {rd}: {bugs} bugs, {inconclusive_rate:.0%} inconclusive", rd)
        actions = self._diagnose(findings)
        applied = self._improve(actions) if actions else 0

        if applied > 0:
            _console(f"\n  [RUN] Re-running after {applied} improvements...")
            _tick("re_observe", f"Re-running after {applied} improvements", rd)
            findings2, bugs2, rate2 = self._observe(existing_findings=self._prior_findings)
            verdict = self._verify_improvement(inconclusive_rate, rate2, bugs, bugs2)
            _tick("verified", f"Before: {inconclusive_rate:.0%} → After: {rate2:.0%}, Verdict: {verdict}", rd)
            _console(f"  [STATS] {inconclusive_rate:.0%} -> {rate2:.0%} inconclusive | {bugs} -> {bugs2} bugs | {verdict}")
            self._prior_inconclusive_rate = rate2
            all_actions.extend(actions)
            self.rounds.append(ImproveRound(rd, max(bugs, bugs2), rate2, actions, verdict))
            final_rate = rate2
        else:
            if bugs > 0 and inconclusive_rate < 0.50:
                verdict = "COMPLETED_WITH_FINDINGS"
            else:
                verdict = "CONVERGED" if inconclusive_rate < self.CONVERGED_THRESHOLD else "STUCK"
            self.rounds.append(ImproveRound(rd, bugs, inconclusive_rate, actions, verdict))
            _tick("done", f"No improvements possible. Inconclusive rate: {inconclusive_rate:.0%}", rd)
            final_rate = inconclusive_rate

        self._save_progress()
        # G1 closed loop: carry this round's confirmed findings into the next
        # round so the mainline can mark them prior_known instead of rediscovering.
        self._accumulate_prior(findings)
        # Do not sum the same rediscovered findings across local rounds.
        # A run's business value is the unique/latest validated candidate count,
        # while repeated rounds are only attempts to improve evidence quality.
        total_bugs = max((r.bugs_found for r in self.rounds), default=0)
        engine_health = result.get("pipeline_health") or result.get("runtime_contract") or {}
        result = {
            "rounds": len(self.rounds),
            "total_improvements": len(all_actions),
            "total_bugs": total_bugs,
            "confirmed_bugs": total_bugs,
            "raw_confirmed_signals": int(getattr(self, "_last_raw_confirmed_signals", 0) or 0),
            "needs_more_evidence": int(getattr(self, "_last_needs_more_evidence", 0) or 0),
            "validated_candidates": int(getattr(self, "_last_validated_candidates", 0) or 0),
            "inconclusive_rate": final_rate,
            "terminal": verdict,
            "execution_status": "COMPLETED",
            "actions": [{"target": a.target, "problem": a.problem, "change": a.change} for a in all_actions],
            "findings": [
                {"id": f.get("finding_id") or f.get("id"), "title": f.get("title"),
                 "verdict": f.get("verdict") or "confirmed", "severity": f.get("severity") or "P2",
                 "expected": f.get("expected") or (f.get("evidence") or {}).get("expected", ""),
                 "actual": f.get("actual") or (f.get("evidence") or {}).get("actual", ""),
                 "confidence": f.get("confidence") or f.get("confidence_score") or 0.0,
                 "evidence": f.get("evidence") or {}}
                for f in findings
            ],
            "engine_health": engine_health,
            "cognitive_graph": self._cognitive_graph_observation,
            "discovery": self._last_discovery_result,
        }
        self._write_report(result)
        # Only successful runs may train runtime memory.
        _save_to_memory(result, all_actions, self.output_dir)
        _console(f"\n=== COMPLETE: {result['terminal']} ===")
        _console(f"Rounds: {result['rounds']} | Improvements: {result['total_improvements']} | Bugs: {result['total_bugs']}")
        return result

    def run(self) -> dict:
        global _ACTIVE_RUNTIME
        runtime = LoopRuntimeSession(self.project_id, self.output_dir)
        self._prior_findings: list[dict] = []
        try:
            runtime.acquire()
        except LoopBusyError as exc:
            result = {
                "rounds": 0, "total_improvements": 0, "total_bugs": 0,
                "terminal": "SKIPPED_ALREADY_RUNNING", "execution_status": "SKIPPED_ALREADY_RUNNING",
                "error": str(exc), "actions": [], "findings": [],
            }
            self._write_report(result)
            _console(f"  [SKIP] {result['terminal']}: {exc}")
            return result
        except Exception as exc:
            result = {
                "rounds": 0, "total_improvements": 0, "total_bugs": 0,
                "terminal": "FAILED_TERMINAL", "execution_status": "FAILED_TERMINAL",
                "error": f"Could not acquire runtime lease: {exc}", "actions": [], "findings": [],
            }
            self._write_report(result)
            return result

        _ACTIVE_RUNTIME = runtime
        try:
            _tick("starting", "Runtime lease acquired")
            all_results = []
            terminal = "RUNNING"
            total_bugs = 0
            total_improvements = 0
            
            for round_num in range(1, self.MAX_ROUNDS + 1):
                result = self._run_once()
                all_results.append(result)
                total_bugs = max(total_bugs, result.get("total_bugs", 0))
                total_improvements += result.get("total_improvements", 0)
                terminal = result.get("terminal", "COMPLETED")
                
                if terminal == "STUCK":
                    _console("\\n  [STUCK] Terminal: STUCK — no more improvements possible")
                    break
                elif terminal in ("COMPLETED_WITH_FINDINGS", "CONVERGED", "NO_FURTHER_LOCAL_ACTION"):
                    _console(f"\\n  [DONE] Terminal: {terminal}")
                    break
                elif terminal in ("FAILED_RETRYABLE", "FAILED_TERMINAL"):
                    _console(f"\\n  [FAIL] Terminal: {terminal}")
                    break
                elif round_num < self.MAX_ROUNDS:
                    _console(f"\\n  [NEXT] Continuing to round {round_num + 1}...")
            
            # Preserve the final run payload rather than throwing away engine
            # health, stage failures, artifact status, and error evidence.
            merged = dict(all_results[-1]) if all_results else {}
            merged.update({
                "rounds": len(all_results),
                "total_improvements": total_improvements,
                "total_bugs": total_bugs,
                "terminal": terminal,
                "execution_status": terminal,
                "actions": [a for r in all_results for a in r.get("actions", [])],
            })
            runtime.assert_healthy()
            runtime.complete(terminal)
            return merged
        except Exception as exc:
            try:
                runtime.fail(exc, retryable=True)
            except Exception as heartbeat_exc:
                _console(f"  [WARN] Failed to persist runtime failure: {heartbeat_exc}")
            result = self._failure_result(exc)
            _console(f"\n=== FAILED_RETRYABLE: {exc} ===")
            return result
        finally:
            _ACTIVE_RUNTIME = None
            runtime.release()


def _finding_fingerprint(f: dict) -> str:
    fid = f.get("finding_id") or f.get("id")
    if fid:
        return f"id:{fid}"
    title = str(f.get("title") or "")
    expected = str(f.get("expected") or (f.get("evidence") or {}).get("expected", ""))
    actual = str(f.get("actual") or (f.get("evidence") or {}).get("actual", ""))
    return "h:" + hashlib.sha256(f"{title}|{expected}|{actual}".encode("utf-8")).hexdigest()


def _accumulate_prior(self, findings) -> None:
    seen = {_finding_fingerprint(f) for f in self._prior_findings}
    for f in findings:
        if not isinstance(f, dict):
            continue
        fp = _finding_fingerprint(f)
        if fp not in seen:
            seen.add(fp)
            self._prior_findings.append(dict(f))


def _save_to_memory(result: dict, actions: list, output_dir: Path | str | None = None):
    """P4: Append loop results to persistent cumulative memory (bug_memory.jsonl)."""
    import hashlib
    mem_path = Path(output_dir or Path("platform_outputs") / DEFAULT_PROJECT_ID) / "bug_memory.jsonl"
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing
    existing = []
    if mem_path.exists():
        for line in mem_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                existing.append(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("Skipping malformed bug_memory line: %s", exc)
    
    # Dedup by title hash
    seen = {hashlib.md5(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest() for e in existing}
    
    new_entries = 0
    for a in actions:
        entry = {
            "ts": time.time(),
            "type": "improvement",
            "target": a.target,
            "problem": a.problem[:200],
            "change": a.change[:200],
            "rounds": result["rounds"],
            "bugs": result["total_bugs"],
            "terminal": result["terminal"],
        }
        h = hashlib.md5(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            existing.append(entry)
            new_entries += 1
    
    # Add summary entry
    summary = {
        "ts": time.time(),
        "type": "loop_summary",
        "rounds": result["rounds"],
        "bugs": result["total_bugs"],
        "improvements": result["total_improvements"],
        "terminal": result["terminal"],
    }
    h = hashlib.md5(json.dumps(summary, sort_keys=True, default=str).encode()).hexdigest()
    if h not in seen:
        existing.append(summary)
        new_entries += 1
    
    if new_entries:
        mem_path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in existing) + "\n",
            encoding="utf-8",
        )
        _console(f"  [MEM] Memory: +{new_entries} entries (total: {len(existing)})")


def run_self_improving(prd_path=None, api_path=None, base_url: str | None = None, *, project_id: str = DEFAULT_PROJECT_ID, output_dir: Path | str | None = None):
    sweeper = SelfImprovingSweep(prd_path, api_path, base_url, project_id=project_id, output_dir=output_dir)
    return sweeper.run()


if __name__ == "__main__":
    result = run_self_improving()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
