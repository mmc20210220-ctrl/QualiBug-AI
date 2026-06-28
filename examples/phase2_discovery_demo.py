#!/usr/bin/env python3
"""
QualiBug AI - 第2阶段发现引擎完整演示

展示第1阶段 + 第2阶段的所有分析器，提升bug发现能力。
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

print("=" * 80)
print("🚀 QualiBug AI - 第2阶段发现引擎完整演示")
print("=" * 80)

# 模拟的完整PRD文档
prd_text = """
# 电商订单支付库存系统 - 完整文档

## 1. 业务规则
- 订单状态必须遵循: draft -> pending_payment -> paid -> allocated -> shipped -> completed
- 库存扣减必须与订单一一对应，支持并发操作
- 金额计算必须精确，使用Decimal类型
- 多租户数据完全隔离，tenant_id必须在所有查询中

## 2. 多租户与权限
- 系统支持多租户架构
- 每个租户只能看到和操作自己的数据
- API必须包含tenant_id参数
- 管理员角色可以管理所有租户（仅用于演示）

## 3. 异步任务
- 订单支付成功后，异步发送通知邮件
- 库存变更后，异步更新缓存
- 定时任务：每天凌晨3点生成销售报表

## 4. 缓存策略
- 商品信息使用Redis缓存，TTL=30分钟
- 库存信息实时更新，缓存失效
- 读写分离：读操作走从库，写操作走主库

## 5. 并发处理
- 库存扣减使用乐观锁或数据库行锁
- 同一订单并发支付需要幂等性保证
- 消息队列处理异步任务重试

## 6. 敏感操作
- 删除订单需要管理员权限
- 修改价格需要审计日志
- 多租户数据访问严格验证
"""

# 模拟的完整API规格
api_spec = {
    "paths": {
        "/api/v1/orders": {
            "get": {"summary": "获取订单列表"},
            "post": {"summary": "创建订单"}
        },
        "/api/v1/orders/{order_id}": {
            "get": {"summary": "获取订单详情"},
            "put": {"summary": "更新订单"},
            "delete": {"summary": "删除订单"}
        },
        "/api/v1/orders/{order_id}/pay": {
            "post": {"summary": "支付订单"}
        },
        "/api/v1/inventory/deduct": {
            "post": {"summary": "扣减库存"}
        },
        "/api/v1/inventory/add": {
            "post": {"summary": "增加库存"}
        },
        "/api/v1/tenants/{tenant_id}/products": {
            "get": {"summary": "获取租户商品列表"}
        },
        "/api/v1/users/{user_id}": {
            "get": {"summary": "获取用户信息"},
            "put": {"summary": "更新用户信息"}
        },
        "/api/v1/admin/export": {
            "get": {"summary": "管理员导出数据"}
        },
        "/api/v1/cache/invalidate": {
            "post": {"summary": "缓存失效"}
        },
        "/api/v1/tasks/send_email": {
            "post": {"summary": "异步发送邮件"}
        }
    }
}

print("\n📄 准备完成，开始分析...")
print("=" * 80)

# 导入并运行
from ai_test_asset_center.enhanced_discovery_engine import create_enhanced_engine

print("\n" + "=" * 80)
print("1️⃣  初始化增强发现引擎（包含第2阶段）")
print("=" * 80)

engine = create_enhanced_engine(enable_phase2=True)

print("\n" + "=" * 80)
print("2️⃣  运行增强发现流程")
print("=" * 80)

results = engine.run_enhanced_discovery(prd_text, str(api_spec))

print("\n" + "=" * 80)
print("3️⃣  分析摘要")
print("=" * 80)

summary = engine.get_enhanced_summary()

print(f"\n📊 总发现数: {summary['total_findings']}")
print(f"\n🗂️ 按严重程度:")
for severity, count in summary['severity_count'].items():
    if count > 0:
        print(f"   {severity}: {count}")

print(f"\n🏷️  按分类:")
for category, count in summary['category_count'].items():
    print(f"   {category}: {count}")

print(f"\n📊 各分析器摘要:")
if 'phase1' in summary['analyzers']:
    print("\n   第1阶段:")
    for analyzer_name, analyzer_summary in summary['analyzers']['phase1'].items():
        print(f"   - {analyzer_name}: {analyzer_summary}")

if 'phase2' in summary['analyzers']:
    print("\n   第2阶段:")
    for analyzer_name, analyzer_summary in summary['analyzers']['phase2'].items():
        print(f"   - {analyzer_name}: {analyzer_summary}")

print("\n" + "=" * 80)
print("4️⃣  发现详情（前10个）")
print("=" * 80)

for i, finding in enumerate(summary['total_findings'][:10]):
    if i < len(engine.all_discoveries):
        f = engine.all_discoveries[i]
        print(f"\n{i+1}. [{f['severity']}] [{f['category']}] {f['title']}")
        print(f"   {f['description']}")

print("\n" + "=" * 80)
print("5️⃣  导出HTML报告")
print("=" * 80)

report_path = repo_root / "phase2_discovery_report.html"
engine.export_findings_to_html(report_path)
print(f"\n✅ HTML报告已导出到: {report_path}")

print("\n" + "=" * 80)
print("🎯 演示完成！")
print("=" * 80)

print("\n💡 覆盖的Bug分类:")
print("   第1阶段: C01, C05, C06, C07, C08, C09, C13")
print("   第2阶段: C03, C04, C11, C20, C21")
print("\n🎉 发现能力大幅提升！")
