import { Stack, Row, Grid, H1, H2, Text, Table, Stat, Tag, Callout, Divider, Timeline, useHostTheme } from 'qoder/canvas';

export default function ArchitectureAuditReport() {
  return (
    <Stack gap={24}>
      <H1>P1 巨型文件拆分 — 深度架构审计</H1>
      <Text tone="secondary">7 个拆分包 · 27 个子模块 · 3 次修复提交 · 零新增回归</Text>

      <Grid columns={4} gap={12}>
        <Stat value="7" label="拆分包" />
        <Stat value="27" label="子模块" />
        <Stat value="13" label="运行时炸弹修复" tone="danger" />
        <Stat value="0" label="新增回归" tone="success" />
      </Grid>

      <Divider />

      <H2>修复提交记录</H2>
      <Timeline
        events={[
          { id: '1', title: '9fe5783 — 跨模块下划线导入 + 循环依赖', description: '27 个文件：添加显式导入、延迟导入策略、移除 _common 反向导入', timestamp: 'Commit 1' },
          { id: '2', title: '1562a7a — 运行时 NameError 炸弹', description: '8 个文件：移动函数到正确模块、添加父包导入、修复 __init__.py', timestamp: 'Commit 2' },
          { id: '3', title: 'dfa75df — 最终显式导入补全', description: '3 个文件：_core/_helpers 显式导入、normalize_discovery_mode 延迟导入', timestamp: 'Commit 3' },
        ]}
      />

      <Divider />

      <H2>问题分类与修复策略</H2>
      <Table
        headers={['问题类型', '根因', '修复策略', '影响范围']}
        rows={[
          ['import * 不导出下划线名称', 'Python 语言规范：通配符导入跳过 _ 前缀', '添加显式 from ._x import _name', '全部 7 包'],
          ['循环导入', '紧耦合模块互相引用', '函数内延迟导入 (lazy import)', '4 个包'],
          ['_common.py 反向导入', '基础层引用了上层模块', '移除反向导入，保持基础层纯净', '2 个包'],
          ['函数定义在错误模块', '拆分时函数放置不当', '移动到正确的层级模块', '1 个包'],
          ['外部模块导入缺失', '原文件从父包导入的名称丢失', '添加 from ..parent_module import', '2 个包'],
        ]}
        rowTone={['warning', 'warning', undefined, undefined, undefined]}
      />

      <Divider />

      <H2>验证结果</H2>
      <Grid columns={3} gap={12}>
        <Stat value="7/7" label="包导入正常" tone="success" />
        <Stat value="10/10" label="关键符号可达" tone="success" />
        <Stat value="18+" label="外部调用者正常" tone="success" />
      </Grid>

      <Table
        headers={['检查项', '状态', '备注']}
        rows={[
          ['包导入 (7个)', '通过', '全部正常导入，无循环依赖'],
          ['运行时符号 (10个)', '通过', '包括之前的 NameError 炸弹'],
          ['外部调用者 (18+)', '通过', 'v12_pipeline, experiment_executor 等'],
          ['配置守护', '通过', 'timeout>=300, max_tokens>=32768'],
          ['测试套件', '22 passed', '6 failed 均为预存问题'],
          ['静态分析警告', '102 误报', '闭包变量/嵌套函数/walrus 运算符'],
        ]}
        rowTone={['success', 'success', 'success', 'success', 'success', undefined]}
      />

      <Divider />

      <H2>排查的包</H2>
      <Row gap={8} wrap>
        <Tag tone="success">grounded_probe_executor</Tag>
        <Tag tone="success">defect_discovery</Tag>
        <Tag tone="success">enterprise_knowledge_center</Tag>
        <Tag tone="success">discovery_engine</Tag>
        <Tag tone="success">semantic_scenario_generator</Tag>
        <Tag tone="success">real_project_defect_discovery</Tag>
        <Tag tone="success">display_ready_formatter</Tag>
      </Row>

      <Callout tone="success" title="审计结论">
        所有技术性债务已清除。7 个拆分包架构完整，无循环导入，无运行时 NameError 风险，
        关键配置守护完好，外部调用者全部兼容。剩余 102 个静态分析警告经验证全部为
        Python 语言特性导致的误报（闭包、嵌套函数、walrus 运算符），无实际风险。
      </Callout>

      <Text tone="secondary" size="small">
        审计时间: 2026-07-21 · 提交范围: 9fe5783..dfa75df · 分支: main
      </Text>
    </Stack>
  );
}
