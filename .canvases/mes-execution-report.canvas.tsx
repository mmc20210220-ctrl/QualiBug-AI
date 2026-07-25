import { Stack, Row, Grid, H1, H2, H3, Text, Stat, Table, Divider, Tag, Progress, Callout, Card, CardHeader, CardBody } from 'qoder/canvas';

export default function MesExecutionReport() {
  return (
    <Stack gap={24}>
      <H1>真实 MES 执行引擎 - 完成报告</H1>
      <Text tone="secondary">Runtime Effect Validation — Real HTTP Execution against Discrete Manufacturing MES</Text>

      {/* Key Metrics */}
      <Grid columns={4} gap={16}>
        <Stat value="34" label="Total Experiments" />
        <Stat value="28" label="Findings" tone="warning" />
        <Stat value="11" label="Unique Root Causes" />
        <Stat value="LEVEL_B" label="Final Result" tone="danger" />
      </Grid>

      <Grid columns={4} gap={16}>
        <Stat value="82.4%" label="Violation Rate" />
        <Stat value="216.91s" label="Duration" />
        <Stat value="24/28" label="Reproduced" />
        <Stat value="10/10" label="Mechanism Coverage" />
      </Grid>

      <Divider />

      {/* Execution Summary */}
      <H2>执行概要</H2>
      <Table
        headers={['指标', '值', '说明']}
        rows={[
          ['SUT', 'Discrete Manufacturing MES', 'projects/mes_f/mock_server.py, port 8020'],
          ['执行模式', 'REAL_HTTP', '真实 HTTP 请求/响应，非模拟'],
          ['实验总数', '34', '覆盖 10 种探测机制'],
          ['Findings', '28', 'Oracle 判定的 VIOLATION'],
          ['Passes', '6', 'Oracle 判定 PASS'],
          ['独立复现', '24/28', '重新 reset + 执行验证'],
          ['根因去重', '11 unique', '按 mechanism:oracle_type 分组'],
          ['Deep Findings', '21', '非 Authorization/Scope 的深层业务逻辑'],
        ]}
      />

      <Divider />

      {/* Mechanism Distribution */}
      <H2>机制分布 (10/10 覆盖)</H2>
      <Table
        headers={['Mechanism', 'Findings', '占比']}
        rows={[
          ['Cross-Entity', '6', '21.4%'],
          ['State Transition', '5', '17.9%'],
          ['Authorization', '4', '14.3%'],
          ['Conservation', '2', '7.1%'],
          ['Idempotency', '2', '7.1%'],
          ['Compensation', '2', '7.1%'],
          ['Temporal', '2', '7.1%'],
          ['Concurrency', '2', '7.1%'],
          ['Batch Operation', '2', '7.1%'],
          ['Scope Isolation', '1', '3.6%'],
        ]}
      />

      <Divider />

      {/* Level Assessment */}
      <H2>LEVEL 判定</H2>
      <Callout tone="warning" title="LEVEL_B — Project G Entry Not Allowed">
        <Stack gap={8}>
          <Text>unique_tp = 11 (需要 ≥ 15)</Text>
          <Text>deep_unique_tp = 8 (需要 ≥ 10)</Text>
          <Text>formal_findings = 28 (需要 ≥ 18) ✓</Text>
          <Text>mechanism_types = 10 (需要 ≥ 8) ✓</Text>
          <Text tone="secondary">Next breakpoint: UNIQUE_TP_BELOW_15</Text>
        </Stack>
      </Callout>

      <Divider />

      {/* Constraints */}
      <H2>约束满足</H2>
      <Table
        headers={['约束', '状态']}
        rows={[
          ['Oracle 判定仅引用 API_SPEC.md 文档约束', 'PASS'],
          ['不引用 BUG-MES-xxx 注释 (benchmark 答案)', 'PASS'],
          ['Finding 证据 = 真实 request + response', 'PASS'],
          ['Benchmark 隔离 (0 benchmark inputs)', 'PASS'],
          ['每条 Finding 独立复现', '24/28 stable'],
        ]}
      />

      <Divider />

      {/* Deliverables */}
      <H2>交付物</H2>
      <H3>执行引擎 (runtime_execution/)</H3>
      <Table
        headers={['文件', '说明']}
        rows={[
          ['mes_client.py', 'HTTP 客户端 + 12 账号 Bearer Token + 证据记录'],
          ['mes_oracles.py', '10 种 Oracle 判定逻辑'],
          ['mes_experiments.py', '34 个实验定义 (957 行)'],
          ['run_mes_formal.py', '主运行器 (268 行)'],
          ['update_runtime_deliverables.py', 'Phase 5 指标计算 + 交付物更新'],
        ]}
      />

      <H3>执行结果</H3>
      <Table
        headers={['文件', '说明']}
        rows={[
          ['mes_execution_ledger.json', '1792 行完整执行记录'],
          ['mes_findings.json', '28 个 Findings + 证据'],
          ['mes_reproduction.json', '独立复现结果'],
          ['mes_root_causes.json', '11 unique root causes'],
        ]}
      />

      <H3>Runtime 交付物 (根目录, 已更新为真实数据)</H3>
      <Table
        headers={['文件', '关键数据']}
        rows={[
          ['project_f_runtime_effect_final_report.json', 'SUT=MES, REAL_HTTP, 28 findings'],
          ['project_f_runtime_precision_metrics.json', 'precision=1.0, 0 false positives'],
          ['project_f_runtime_recall_metrics.json', 'recall=0.875 (28/32)'],
          ['project_f_runtime_mechanism_contribution.json', '10 mechanisms'],
          ['project_f_runtime_combination_contribution.json', '10 oracle types'],
          ['project_f_runtime_result_classification.json', 'LEVEL_B'],
          ['project_g_entry_gate.json', 'entry=false'],
          ['project_f_runtime_execution_ledger.json', '34 experiments summary'],
        ]}
      />

      <Divider />

      {/* Git */}
      <H2>Git 提交</H2>
      <Table
        headers={['Commit', '说明']}
        rows={[
          ['0b5da3f', 'Real MES execution engine + 28 findings'],
          ['f50ab48', 'Phase 5: Update runtime deliverables with real data'],
        ]}
      />

      <Text tone="secondary" size="small">Generated from real MES HTTP execution. All findings carry complete request/response evidence.</Text>
    </Stack>
  );
}
