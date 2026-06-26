"""
QualiBug Full Discovery Sweep — adapted from Loop Library #010

六步循环:
  Observe → Choose → Act → Verify → Record → Repeat/Stop

终端状态:
  - SUCCESS: all executable hypotheses confirmed or falsified
  - STAGNATED: 3 consecutive rounds with no new confirmed bugs
  - BLOCKED: target unreachable (MES server down, auth failed)
  - EXHAUSTED: 5 rounds run (safety cap)
"""

from __future__ import annotations

import json, os, time, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discovery_engine import AutonomousDiscoveryEngine, DiscoveryFinding
from .scenario_runner import ScenarioRunner
from .db_verifier import MESDBVerifier

# ── 心跳 (供 Loop Watchdog 读取) ──────────────────────────
HEARTBEAT_FILE = "platform_outputs/real_project_demo/.loop_heartbeat.json"

def _tick(step: str, detail: str = "", round_num: int = 0):
    """写心跳文件，让 watchdog 知道 sweep loop 还活着"""
    hb = {
        "ts": time.time(),
        "step": step,
        "detail": detail[:200],
        "round": round_num,
        "pid": os.getpid(),
    }
    try:
        Path(HEARTBEAT_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(hb, f)
    except Exception:
        pass
    emoji = {"discover": "\U0001F50D", "scenario": "\U0001F3AC", "db": "\U0001F5C4\uFE0F",
             "round_done": "\U0001F4CA", "stagnated": "\U0001F6D1",
             "success": "\U0001F3C1", "exhausted": "\u23F0"}.get(step, "\u23F3")
    print(f"  {emoji} [{step}] {detail[:120]}", flush=True)


@dataclass
class SweepRound:
    round_num: int
    hypotheses_total: int
    executed: int
    confirmed: int
    falsified: int
    inconclusive: int
    scenario_bugs: int = 0
    db_bugs: int = 0
    duration_s: float = 0
    new_findings: list[str] = field(default_factory=list)


@dataclass
class SweepResult:
    terminal_state: str  # SUCCESS | STAGNATED | BLOCKED | EXHAUSTED
    rounds: list[SweepRound] = field(default_factory=list)
    total_confirmed: int = 0
    total_duration_s: float = 0
    evidence_summary: str = ""


class DiscoverySweep:
    """Full product evaluation loop for QualiBug"""

    MAX_ROUNDS = 5
    STAGNATION_LIMIT = 3

    def __init__(self, prd_path: str = None, api_path: str = None,
                 base_url: str = "http://127.0.0.1:8000/api"):
        # Load inputs
        if prd_path is None:
            prd_path = str(Path(__file__).resolve().parents[1] / "mes_target/mes-buglab-target/docs/PRD.md")
        if api_path is None:
            api_path = str(Path(__file__).resolve().parents[1] / "mes_target/mes-buglab-target/docs/API.md")

        self.prd = Path(prd_path).read_text(encoding="utf-8") if Path(prd_path).exists() else ""
        self.api = Path(api_path).read_text(encoding="utf-8") if Path(api_path).exists() else ""
        self.base_url = base_url
        self.engine = AutonomousDiscoveryEngine(base_url=base_url)
        self.scenarios = ScenarioRunner(base_url=base_url)
        self.db = MESDBVerifier()
        self.rounds: list[SweepRound] = []

    def _run_round(self, round_num: int, prior_findings: list[dict] = None) -> SweepRound:
        """Execute one complete discovery round with closed-loop feedback"""
        t0 = time.time()

        _tick("discover", f"Round {round_num}: LLM discovery starting...", round_num)

        # Stage 1-4: LLM-powered discovery with prior findings injected
        result = self.engine.discover(self.prd, self.api,
            prior_findings=prior_findings  # ← CLOSED LOOP: feed prior bugs to Reasoner
        )

        _tick("discover", f"Round {round_num}: {result.get('stages', {}).get('verifier', {}).get('total', 0)} probes executed", round_num)

        v = result.get("stages", {}).get("verifier", {})
        hypotheses = result.get("stages", {}).get("reasoner", {}).get("hypotheses", 0)

        # Stage 4.5: Augment with scenario runner for inconclusive
        scenario_bugs = 0
        for f in self.engine.findings:
            if f.verdict == "inconclusive" and f.severity in ("P0", "P1"):
                _tick("scenario", f"Round {round_num}: running scenario runner...", round_num)
                # Try scenario runner for high-severity inconclusive
                try:
                    self.scenarios.run_all()
                    s = self.scenarios.summary()
                    scenario_bugs = s.get("total_bugs_found", 0)
                    _tick("scenario", f"Round {round_num}: scenario runner found {scenario_bugs} bugs", round_num)
                except Exception:
                    _tick("scenario", f"Round {round_num}: scenario runner failed", round_num)
                    pass
                break  # Only run once per round

        # Stage 4.6: DB verification
        db_bugs = 0
        try:
            _tick("db", f"Round {round_num}: running DB verification...", round_num)
            self.db.run_all()
            db_bugs = self.db.summary().get("confirmed", 0)
            _tick("db", f"Round {round_num}: DB found {db_bugs} bugs", round_num)
        except Exception:
            _tick("db", f"Round {round_num}: DB verification failed", round_num)
            pass

        elapsed = time.time() - t0

        new_findings = [
            f.title for f in self.engine.findings
            if f.verdict == "confirmed" and (prior_findings is None or f.title not in prior_findings)
        ]

        r = SweepRound(
            round_num=round_num,
            hypotheses_total=hypotheses,
            executed=v.get("total", 0),
            confirmed=v.get("confirmed", 0),
            falsified=v.get("falsified", 0),
            inconclusive=v.get("inconclusive", 0),
            scenario_bugs=scenario_bugs,
            db_bugs=db_bugs,
            duration_s=elapsed,
            new_findings=new_findings,
        )

        # Print round summary
        print(f"\n  Round {round_num}: {r.confirmed} LLM-confirmed, {r.scenario_bugs} scenario, {r.db_bugs} DB")
        print(f"    New: {len(r.new_findings)} bugs | {r.executed}/{r.hypotheses_total} hypotheses executed")
        _tick("round_done", f"R{round_num}: {r.confirmed}+{r.scenario_bugs}+{r.db_bugs} bugs, {r.inconclusive} inconclusive", round_num)

        return r

    def sweep(self) -> SweepResult:
        """Full discovery sweep with feedback loop"""
        t0 = time.time()
        all_confirmed: list[dict] = []  # Track findings as dicts for closed-loop injection
        stagnation_count = 0

        print("=" * 60)
        print("QualiBug Full Discovery Sweep (Loop Library #010)")
        print("=" * 60)

        # Round 1: Initial discovery
        print(f"\n--- Round 1: Initial Discovery ---")
        _tick("round_start", "Round 1: Initial discovery", 1)
        r = self._run_round(1)
        self.rounds.append(r)
        all_confirmed.extend([{"title": t, "severity": "P1", "verdict": "confirmed"}
                             for t in r.new_findings])

        if r.confirmed > 0 or r.scenario_bugs > 0:
            stagnation_count = 0
        else:
            stagnation_count = 1

        # Rounds 2-5: Feedback loop
        for rd in range(2, self.MAX_ROUNDS + 1):
            # Check termination
            if stagnation_count >= self.STAGNATION_LIMIT:
                print(f"\n  STAGNATED: {stagnation_count} consecutive rounds with no new bugs")
                _tick("stagnated", f"STAGNATED after {stagnation_count} rounds no new bugs", rd)
                break

            if r.executed >= r.hypotheses_total and r.inconclusive == 0:
                print(f"\n  SUCCESS: all executable hypotheses exhausted")
                _tick("success", "All executable hypotheses exhausted", rd)
                break

            print(f"\n--- Round {rd}: Feedback Loop ---")
            _tick("round_start", f"Round {rd}: Feedback loop", rd)
            r = self._run_round(rd, all_confirmed)  # ← CLOSED LOOP
            self.rounds.append(r)

            if r.confirmed > 0 or r.scenario_bugs > 0:
                new_count = len(r.new_findings)
                all_confirmed.extend([{"title": t, "severity": "P1", "verdict": "confirmed"}
                                     for t in r.new_findings])
                if new_count > 0:
                    stagnation_count = 0
                    print(f"    ✓ Found {new_count} new bugs, resetting stagnation counter")
                else:
                    stagnation_count += 1
                    print(f"    Stagnation: {stagnation_count}/{self.STAGNATION_LIMIT}")
            else:
                stagnation_count += 1
                print(f"    Stagnation: {stagnation_count}/{self.STAGNATION_LIMIT}")

        # Determine terminal state
        if stagnation_count >= self.STAGNATION_LIMIT:
            state = "STAGNATED"
        elif rd >= self.MAX_ROUNDS:
            state = "EXHAUSTED"
        else:
            state = "SUCCESS"

        total_confirmed = sum(
            rnd.confirmed + rnd.scenario_bugs + rnd.db_bugs
            for rnd in self.rounds
        )
        elapsed = time.time() - t0

        _tick(state.lower(), f"Sweep complete: {state}, {total_confirmed} bugs, {elapsed:.0f}s", len(self.rounds))

        result = SweepResult(
            terminal_state=state,
            rounds=self.rounds,
            total_confirmed=total_confirmed,
            total_duration_s=elapsed,
            evidence_summary=f"{len(all_confirmed)} unique bugs found across {len(self.rounds)} rounds",
        )

        return result


def run_sweep(prd_path: str = None, api_path: str = None,
              base_url: str = "http://127.0.0.1:8000/api") -> SweepResult:
    """Convenience entry point"""
    sweeper = DiscoverySweep(prd_path, api_path, base_url)
    return sweeper.sweep()


if __name__ == "__main__":
    result = run_sweep()
    print(f"\n{'='*60}")
    print(f"Sweep complete: {result.terminal_state}")
    print(f"Rounds: {len(result.rounds)}")
    print(f"Total confirmed bugs: {result.total_confirmed}")
    print(f"Duration: {result.total_duration_s:.0f}s")
    for rnd in result.rounds:
        print(f"  Round {rnd.round_num}: {rnd.confirmed} LLM + {rnd.scenario_bugs} scenario + {rnd.db_bugs} DB")
