#!/usr/bin/env python3
"""
QualiBug AI - 增强发现引擎演示

展示如何使用新增的分析器提升bug发现能力。
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

print("=" * 60)
print("🔍 QualiBug AI - 增强发现引擎演示")
print("=" * 60)

# 模拟的PRD文档
prd_text = """
# 电商订单支付库存系统

## 业务规则

1. **订单状态转换规则**
   - 订单必须遵循状态转换: draft -> pending_payment -> paid -> allocated -> shipped -> completed
   - 终态 cancelled/refunded/completed 的订单不允许修改
   - 不允许直接从 draft 跳到 completed

2. **多租户隔离**
   - 所有接口必须包含 tenant_id 参数
   - 缓存key必须包含tenant_id
   - 导出功能必须按租户隔离

3. **库存守恒规则**
   - 下单扣库存，取消/退款加库存
   - 库存数量必须保持一致
   - 不允许超卖

4. **金额守恒规则**
   - 支付时扣款，退款时加款
   - 金额计算必须精确，使用Decimal
   - 支持并发安全操作
"""

# 模拟的API规格
api_spec = {
    "paths": {
        "/api/v1/orders": {
            "get": {"summary": "获取订单列表"},
            "post": {"summary": "创建订单"}
        },
        "/api/v1/orders/{id}": {
            "get": {"summary": "获取订单详情"},
            "put": {"summary": "修改订单"}
        },
        "/api/v1/orders/{id}/cancel": {
            "post": {"summary": "取消订单"}
        },
        "/api/v1/inventory/deduct": {
            "post": {"summary": "扣库存"}
        },
        "/api/v1/inventory/add": {
            "post": {"summary": "加库存"}
        },
        "/api/v1/export/orders": {
            "get": {"summary": "导出订单"}
        }
    }
}

print("\n📄 模拟的PRD和API规格已准备就绪")
print(f"   PRD长度: {len(prd_text)} 字符")
print(f"   API端点数: {len(api_spec['paths'])}")

print("\n" + "=" * 60)
print("1️⃣  测试业务规则分析器")
print("=" * 60)

from ai_test_asset_center.analyzers.business_rules import analyze_prd_rules

br_results = analyze_prd_rules(prd_text)
print(f"\n✅ 提取到 {br_results['summary']['total_rules']} 条业务规则")
print(f"   按类型: {br_results['summary']['rules_by_type']}")

print("\n前3条规则:")
for rule in br_results['rules'][:3]:
    print(f"   - [{rule.rule_type.value}] {rule.name}: {rule.description[:50]}...")

print("\n" + "=" * 60)
print("2️⃣  测试状态机分析器")
print("=" * 60)

from ai_test_asset_center.analyzers.state_machine import analyze_state_machine

sm_results = analyze_state_machine(prd_text, api_spec)
print(f"\n✅ 提取到 {sm_results['summary']['total_states']} 个状态")
print(f"   发现 {sm_results['summary']['total_bugs']} 个潜在问题")

print("\n发现的bug:")
for bug in sm_results['bugs'][:3]:
    print(f"   - [{bug.severity}] {bug.title}")

print("\n" + "=" * 60)
print("3️⃣  测试多租户分析器")
print("=" * 60)

from ai_test_asset_center.analyzers.multi_tenant import analyze_multi_tenant_isolation

mt_results = analyze_multi_tenant_isolation(api_spec)
print(f"\n✅ 分析了 {mt_results['summary']['total_endpoints']} 个端点")
print(f"   发现 {mt_results['summary']['total_bugs']} 个潜在隔离问题")

print("\n发现的bug:")
for bug in mt_results['bugs'][:3]:
    print(f"   - [{bug.severity}] {bug.title}")

print("\n" + "=" * 60)
print("4️⃣  测试守恒规则分析器")
print("=" * 60)

from ai_test_asset_center.analyzers.conservation import analyze_conservation_rules

cv_results = analyze_conservation_rules(prd_text, api_spec)
print(f"\n✅ 提取到 {cv_results['summary']['total_rules']} 条守恒规则")
print(f"   发现 {cv_results['summary']['total_bugs']} 个潜在守恒问题")

print("\n发现的bug:")
for bug in cv_results['bugs'][:3]:
    print(f"   - [{bug.severity}] {bug.title}")

print("\n" + "=" * 60)
print("5️⃣  测试增强发现引擎")
print("=" * 60)

from ai_test_asset_center.enhanced_discovery_engine import create_enhanced_engine

engine = create_enhanced_engine()
discovery_results = engine.run_enhanced_discovery(prd_text, str(api_spec))

print(f"\n✅ 增强发现完成")
print(f"   总发现数: {discovery_results['analysis']['business_rules']['total_rules']} (业务规则)")
print(f"              {discovery_results['analysis']['state_machine']['total_bugs']} (状态机)")
print(f"              {discovery_results['analysis']['multi_tenant']['total_bugs']} (多租户)")
print(f"              {discovery_results['analysis']['conservation']['total_bugs']} (守恒)")

print("\n" + "=" * 60)
print("6️⃣  导出HTML报告")
print("=" * 60)

report_path = repo_root / "enhanced_discovery_report.html"
engine.export_findings_to_html(report_path)
print(f"\n✅ HTML报告已导出到: {report_path}")

print("\n" + "=" * 60)
print("📊 最终统计")
print("=" * 60)

summary = engine.get_enhanced_summary()
print(f"\n📌 总发现数: {summary['total_findings']}")
print(f"   P0 严重: {summary['severity_count']['P0']}")
print(f"   P1 高: {summary['severity_count']['P1']}")
print(f"   P2 中: {summary['severity_count']['P2']}")
print(f"\n📌 按分类:")
for cat, count in summary['category_count'].items():
    print(f"   {cat}: {count}")

print("\n" + "=" * 60)
print("🎯 演示完成！")
print("=" * 60)
print("\n💡 下一步:")
print("   1. 查看生成的HTML报告")
print("   2. 在真实项目上测试")
print("   3. 根据需要调整分析器")
print("   4. 实施更多第2、3阶段的优化")
