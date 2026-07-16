"""端到端发现漏斗瓶颈分析器：读取 _funnel_runs/<mode>.json，定位真实 bug 挖掘的瓶颈环节。

只做只读分析，不注入任何 GT / 假数据。瓶颈判定依据：
  1) 阶段级漏斗掉量（input -> success/executed 的最大落差）
  2) pipeline_health 的 executed vs blocked 比例
  3) top_blocking_reasons 分布
  4) 各阶段 elapsed_ms（若主链已采集）
用法: python _analyze_funnel.py [mode]
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
MODE = (sys.argv[1] if len(sys.argv) > 1 else "full").strip()
path = ROOT / "_funnel_runs" / f"{MODE}.json"
if not path.exists():
    raise SystemExit(f"no such run file: {path}")

d = json.load(open(path, encoding="utf-8"))
s = d.get("summary", {}) or {}
print("=" * 72)
print(f"MODE={s.get('mode')}  (file: {path.name}, {path.stat().st_size/1024:.0f} KB)")
print("=" * 72)
print(f"elapsed_sec          : {s.get('elapsed_sec')}")
print(f"success / grade      : {s.get('success')} / {s.get('grade')}")
print(f"execution_status     : {s.get('execution_status')}")
print(f"total_findings       : {s.get('total_findings')}")
print(f"total_candidates     : {s.get('total_candidates')}")
print(f"formal_deliverable   : {s.get('formal_customer_deliverable_count')}")
print(f"input_gaps           : {s.get('input_gaps')}")

df = s.get("discovery_funnel") or (d.get("full_result", {}) or {}).get("discovery_funnel") or {}
print("\n--- discovery_funnel ---")
print("schema_version       :", df.get("schema_version"))
ph = df.get("pipeline_health") or {}
if ph:
    print("pipeline_health      :", json.dumps(ph, ensure_ascii=False))
print("validated_bug_count  :", df.get("validated_bug_count"))
print("canonical_defect_count:", df.get("canonical_defect_count"))
print("candidate_count      :", df.get("candidate_count"))

stages = df.get("stages") or []
if stages:
    print("\n--- 阶段级漏斗 (按 input 降序看掉量) ---")
    hdr = f"{'stage':28} {'input':>6} {'success':>7} {'blocked':>7} {'failed':>6}  elapsed(p50/p95)"
    print(hdr)
    print("-" * len(hdr))
    prev_input = None
    for st in sorted(stages, key=lambda x: x.get("input", 0), reverse=True):
        name = st.get("name", "?")
        inp = st.get("input", 0)
        suc = st.get("success", 0)
        blk = st.get("blocked", 0)
        fail = st.get("failed", 0)
        em = st.get("elapsed_ms") or {}
        p50 = em.get("p50")
        p95 = em.get("p95")
        print(f"{name:28} {inp:6} {suc:7} {blk:7} {fail:6}  {p50}/{p95}")
        if prev_input is not None and inp < prev_input:
            drop = prev_input - inp
            print(f"    ^ 上一阶段到本阶段掉量: {drop} ({(drop/prev_input*100):.0f}%)")
        prev_input = inp

print("\n--- top blocking reasons (义务被拦死的原因分布) ---")
for r in df.get("top_blocking_reasons") or []:
    print(f"  {r.get('reason'):32} x{r.get('count')}")

mrs = s.get("multi_round_summary")
if mrs:
    print("\n--- multi_round_summary ---")
    print(json.dumps(mrs, ensure_ascii=False)[:800])

print("\n--- 瓶颈结论 (自动判定) ---")
if ph:
    sel = int(ph.get("selected_obligation_count") or 0)
    exe = int(ph.get("executed_obligation_count") or 0)
    blk = int(ph.get("blocked_obligation_count") or 0)
    if sel:
        print(f"  选中义务 {sel} -> 执行 {exe} / 阻塞 {blk}")
        print(f"  执行率 = {exe/sel*100:.0f}%，阻塞率 = {blk/sel*100:.0f}%")
        if blk > exe:
            print("  => 瓶颈在 [执行/义务落地] 段：多数义务被治理/预算拦死，未进入验证。")
        elif exe and exe <= sel * 0.5:
            print("  => 执行率偏低，瓶颈偏向执行吞吐或证据/Oracle。")
if stages:
    # 找掉量最大的相邻阶段
    biggest = (None, 0)
    order = sorted(stages, key=lambda x: x.get("input", 0), reverse=True)
    for i in range(1, len(order)):
        drop = order[i-1].get("input", 0) - order[i].get("input", 0)
        if drop > biggest[1]:
            biggest = (order[i-1].get("name"), drop)
    if biggest[0]:
        print(f"  最大阶段掉量发生在 [{biggest[0]}] 之后，掉量 {biggest[1]}。")
print("=" * 72)
