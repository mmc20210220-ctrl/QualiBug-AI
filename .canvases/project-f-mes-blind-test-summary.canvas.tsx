import {
  Stack, Row, Grid, H1, H2, H3, Text, Divider, Stat, Table, Callout, Tag, Pill, Card, CardHeader, CardBody, Progress, useHostTheme
} from 'qoder/canvas';

export default function ProjectFMESBlindTestSummary() {
  const { tokens } = useHostTheme();

  return (
    <Stack gap={24}>
      <Stack gap={8}>
        <H1>Project F 离散制造MES盲测 - 执行总结</H1>
        <Text tone="secondary">QualiBug 第三次完全陌生系统盲测 | 基线: b3471f7 | Tag: qualibug-project-f-blind-rc1</Text>
      </Stack>

      <Callout tone="warning" title="最终判定: NOT_PASSED">
        盲测基础设施完全正常运行。MES领域复杂性（16实体、5+状态机）超出当前auto-fixture能力。Precision 100%，但Recall仅3.1%。
      </Callout>

      <Divider />

      <H2>验收指标对比</H2>
      <Grid columns={4} gap={16}>
        <Stat label="Formal Findings" value="1 / 8" tone="danger" />
        <Stat label="Unique TP" value="1 / 6" tone="danger" />
        <Stat label="Deep TP" value="1 / 5" tone="danger" />
        <Stat label="Precision" value="100%" tone="success" />
      </Grid>

      <Table
        headers={['指标', '阈值', '实际', '状态']}
        rows={[
          ['Formal Finding', '≥ 8', '1', '❌ FAIL'],
          ['Unique TP', '≥ 6', '1', '❌ FAIL'],
          ['Deep TP', '≥ 5', '1', '❌ FAIL'],
          ['Precision', '≥ 75%', '100%', '✅ PASS'],
          ['Recall', '-', '3.1%', '-'],
          ['F1 Score', '-', '6.0%', '-'],
        ]}
        rowTone={['danger', 'danger', 'danger', 'success', undefined, undefined]}
      />

      <Divider />

      <H2>执行阶段完成状态</H2>
      <Table
        headers={['阶段', '内容', '交付物', '状态']}
        rows={[
          ['Phase 1.1', 'MES Mock Server', 'mock_server.py (1017行, 32 bugs)', '✅'],
          ['Phase 1.3', '企业资料', '9个文档 (PRD/API/Rules/Schema等)', '✅'],
          ['Phase 2.1', 'Benchmark注入', 'ground_truth.json (32 bugs, 12机制)', '✅'],
          ['Phase 2.2', 'Benchmark隔离', 'benchmark_isolation.json', '✅'],
          ['Phase 3', '基线验证', 'Release Tag + Frozen Manifests', '✅'],
          ['Phase 4', '环境准备', 'SUT Manifest + Blind Start Manifest', '✅'],
          ['Phase 5', '盲测执行', '2次扫描, blind_test_result.json', '✅'],
          ['Phase 6', '结果评估', 'Seal + Reveal + Diagnosis + Report', '✅'],
        ]}
      />

      <Divider />

      <H2>Benchmark机制分布 (32 Bugs)</H2>
      <Grid columns={3} gap={12}>
        <Card>
          <CardBody>
            <Stack gap={4}>
              <Text weight="bold">Actor/Scope/Ownership</Text>
              <Text tone="secondary">8 bugs | 检出 1</Text>
              <Progress value={12.5} max={100} />
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stack gap={4}>
              <Text weight="bold">State Transition</Text>
              <Text tone="secondary">4 bugs | 检出 0</Text>
              <Progress value={0} max={100} />
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stack gap={4}>
              <Text weight="bold">Cross-Entity</Text>
              <Text tone="secondary">6 bugs | 检出 0</Text>
              <Progress value={0} max={100} />
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stack gap={4}>
              <Text weight="bold">Conservation</Text>
              <Text tone="secondary">4 bugs | 检出 0</Text>
              <Progress value={0} max={100} />
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stack gap={4}>
              <Text weight="bold">Temporal/Idempotency</Text>
              <Text tone="secondary">5 bugs | 检出 0</Text>
              <Progress value={0} max={100} />
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stack gap={4}>
              <Text weight="bold">Concurrency/Batch/Comp</Text>
              <Text tone="secondary">6 bugs | 检出 0</Text>
              <Progress value={0} max={100} />
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <Divider />

      <H2>阻塞原因分析</H2>
      <Callout tone="danger" title="BLOCKED_MISSING_BINDING (90%+ 实验)">
        MES领域需要复杂多实体fixture（工单+BOM+工艺路线+预留），当前系统无法自动创建。路径占位符无法解析。
      </Callout>

      <Table
        headers={['阻塞类型', '数量', '影响机制']}
        rows={[
          ['BLOCKED_MISSING_BINDING', '28', 'State/CrossEntity/Conservation/Temporal/Concurrency/Batch'],
          ['BLOCKED_MISSING_OBSERVER', '2', 'State Transition (需特定状态实体)'],
          ['NOT_ATTEMPTED', '1', 'Scope Isolation'],
        ]}
        rowTone={['danger', 'warning', undefined]}
      />

      <Divider />

      <H2>唯一检出Finding</H2>
      <Card>
        <CardHeader>
          <Row gap={8}>
            <Pill tone="success">TRUE POSITIVE</Pill>
            <Tag tone="info">P1</Tag>
            <Tag>RESOURCE_OWNERSHIP</Tag>
          </Row>
        </CardHeader>
        <CardBody>
          <Stack gap={8}>
            <Text weight="bold">[ContractOracle] owner_tenant_visibility: PLANNER POST /work-orders</Text>
            <Text tone="secondary">匹配Benchmark: BUG-MES-006</Text>
            <Text size="small">globex组织的planner可以在acme工厂创建工单，缺少组织边界检查。</Text>
          </Stack>
        </CardBody>
      </Card>

      <Divider />

      <H2>能力迁移评估</H2>
      <Table
        headers={['能力维度', '评估', '说明']}
        rows={[
          ['领域理解', 'PARTIAL', '正确解析MES领域概念'],
          ['规则生成', 'PARTIAL', '业务规则提取成功但执行被阻塞'],
          ['实验规划', 'COMPLETE', '实验计划正确生成'],
          ['Fixture管理', 'INSUFFICIENT', '无法创建复杂MES fixture'],
          ['Actor矩阵', 'PARTIAL', 'Actor绑定解析但路径占位符未解决'],
        ]}
        rowTone={['warning', 'warning', 'success', 'danger', 'warning']}
      />

      <Divider />

      <H2>交付物清单</H2>
      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>SUT & 资料</H3>
          <Text size="small">• projects/mes_f/mock_server.py</Text>
          <Text size="small">• platform_inputs/mes_f/ (9 files)</Text>
          <Text size="small">• projects/mes_f/benchmark/ground_truth.json</Text>
          <Text size="small">• projects/mes_f/benchmark/benchmark_isolation.json</Text>
        </Stack>
        <Stack gap={8}>
          <H3>评估文档</H3>
          <Text size="small">• project_f_finding_seal.json</Text>
          <Text size="small">• project_f_truth_reveal.json</Text>
          <Text size="small">• project_f_miss_diagnosis.json</Text>
          <Text size="small">• project_f_blind_test_final_report.json</Text>
        </Stack>
      </Grid>

      <Divider />

      <H2>改进建议 (优先级排序)</H2>
      <Table
        headers={['优先级', '建议']}
        rows={[
          ['P0', '实现实体发现 - 调用GET列表端点获取现有实体ID'],
          ['P0', '添加种子数据引导 - 预填充SUT各种状态的实体'],
          ['P1', '实现状态推进链 - 创建实体后通过状态转换推进'],
          ['P1', '添加跨实体fixture图 - 自动解析实体依赖关系'],
          ['P2', '支持数值/时间fixture参数'],
          ['P2', '添加混合状态批量fixture'],
        ]}
        rowTone={['danger', 'danger', 'warning', 'warning', undefined, undefined]}
      />

      <Divider />

      <Text tone="secondary" size="small">
        协议合规: 零语义干预 ✓ | 零代码修改 ✓ | 零Benchmark泄漏 ✓ | 单次正式盲测 ✓ | Finding封存先于Truth Reveal ✓
      </Text>
    </Stack>
  );
}
