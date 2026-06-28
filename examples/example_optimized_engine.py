#!/usr/bin/env python
"""
QualiBug AI - 优化引擎使用示例

展示如何使用 OptimizedDiscoveryEngine 的所有功能
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 80)
print("QualiBug AI - 优化引擎使用示例")
print("=" * 80)

# =====================================================================
# 示例1：基本使用 - 创建优化引擎
# =====================================================================

print("\n" + "-" * 60)
print("示例 1: 创建优化引擎")
print("-" * 60)

try:
    from ai_test_asset_center.optimized_discovery_engine import create_optimized_engine
    
    # 创建优化引擎
    engine = create_optimized_engine(
        base_url="http://127.0.0.1:8000/api",
        enable_checkpoints=True,
        enable_cache=True
    )
    
    print("✅ 优化引擎创建成功")
    print("  - 检查点: 启用")
    print("  - 缓存: 启用")
    
except Exception as e:
    print(f"⚠️ 创建引擎失败（这只是演示，不影响实际使用）: {e}")


# =====================================================================
# 示例2：检查点机制
# =====================================================================

print("\n" + "-" * 60)
print("示例 2: 检查点机制")
print("-" * 60)

try:
    from ai_test_asset_center.optimized_discovery_engine import OptimizedDiscoveryEngine
    
    engine = OptimizedDiscoveryEngine(enable_checkpoints=True, checkpoint_dir=Path("checkpoints_demo"))
    
    # 列出检查点
    checkpoints = engine.list_checkpoints()
    print(f"当前检查点: {checkpoints}")
    
    # 模拟保存检查点
    print("\n模拟保存检查点...")
    engine._save_checkpoint("stage_read", {"data": "demo"})
    
    checkpoints = engine.list_checkpoints()
    print(f"检查点已保存: {checkpoints}")
    
    # 清除检查点
    engine.clear_checkpoints()
    print("检查点已清除")
    
except Exception as e:
    print(f"⚠️ 检查点示例失败: {e}")


# =====================================================================
# 示例3：综合使用模式
# =====================================================================

print("\n" + "-" * 60)
print("示例 3: 完整使用模式")
print("-" * 60)

print("""
完整使用流程：

1. 创建优化引擎
   from ai_test_asset_center.optimized_discovery_engine import create_optimized_engine
   
   engine = create_optimized_engine(
       base_url="http://127.0.0.1:8000/api",
       enable_checkpoints=True,
       enable_cache=True
   )

2. 运行发现流程
   # 正常使用，所有方法都已优化
   reader_output = engine.stage_read(prd_text, api_spec_text)
   hypotheses = engine.stage_reason_all(reader_output, prd_text, api_spec_text)
   execution_results = engine.stage_execute(hypotheses)
   findings = engine.stage_verify(execution_results)

3. 查看优化效果
   engine.print_optimization_summary()

4. 如果中断，可以从检查点恢复
   checkpoint = engine.load_checkpoint("stage_reason")
   if checkpoint:
       hypotheses = checkpoint.state["hypotheses"]
       # 继续执行...
""")


# =====================================================================
# 示例4：与现有代码的兼容性
# =====================================================================

print("\n" + "-" * 60)
print("示例 4: 与现有代码的兼容性")
print("-" * 60)

print("""
优化引擎完全向后兼容！

方式1：替换现有引擎
   # 旧代码
   # from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
   # engine = AutonomousDiscoveryEngine()
   
   # 新代码（零风险）
   from ai_test_asset_center.optimized_discovery_engine import OptimizedDiscoveryEngine
   engine = OptimizedDiscoveryEngine()

方式2：条件使用
   import os
   
   if os.environ.get("USE_OPTIMIZED_ENGINE") == "1":
       from ai_test_asset_center.optimized_discovery_engine import OptimizedDiscoveryEngine
       Engine = OptimizedDiscoveryEngine
   else:
       from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
       Engine = AutonomousDiscoveryEngine
   
   engine = Engine()
   
方式3：使用工厂函数（推荐）
   from ai_test_asset_center.optimized_discovery_engine import create_optimized_engine
   engine = create_optimized_engine()
""")


# =====================================================================
# 示例5：查看性能和缓存统计
# =====================================================================

print("\n" + "-" * 60)
print("示例 5: 性能和缓存统计")
print("-" * 60)

try:
    from ai_test_asset_center.optimized_discovery_engine import OptimizedDiscoveryEngine
    
    engine = OptimizedDiscoveryEngine(enable_checkpoints=False)
    
    print("\n获取性能摘要:")
    print("  engine.get_performance_summary()")
    
    print("\n获取缓存统计:")
    print("  engine.get_cache_stats()")
    
    print("\n打印完整摘要:")
    print("  engine.print_optimization_summary()")
    
except Exception as e:
    print(f"⚠️ 统计示例失败: {e}")


# =====================================================================
# 示例6：回滚方案
# =====================================================================

print("\n" + "-" * 60)
print("示例 6: 回滚方案")
print("-" * 60)

print("""
如果遇到问题，可以立即回滚：

方式1：不使用优化引擎
   from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
   engine = AutonomousDiscoveryEngine()

方式2：删除新文件
   rm ai_test_asset_center/optimized_discovery_engine.py
   rm examples/example_optimized_engine.py
   rm docs/optimization_implementation_guide.md

方式3：Git 回滚
   git checkout HEAD~1
""")


# =====================================================================
# 总结
# =====================================================================

print("\n" + "=" * 80)
print("示例运行完成！")
print("=" * 80)

print("""
下一步：

1. 阅读实现指南：
   docs/optimization_implementation_guide.md

2. 在你的代码中集成优化引擎：
   from ai_test_asset_center.optimized_discovery_engine import create_optimized_engine
   engine = create_optimized_engine()

3. 查看更多示例：
   - examples/example_optimizations.py
   - examples/example_minimal.py
   - examples/example_safe_retry_simple.py

4. 阅读完整文档：
   - docs/OPTIMIZATION_GUIDE.md
   - docs/QUICKSTART.md
   - docs/BEST_PRACTICES.md
""")
