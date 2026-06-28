#!/usr/bin/env python3
"""
简单测试脚本 - 验证导入
"""

import sys
from pathlib import Path

repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

print("Testing imports...")

# 测试1: 导入分析器
try:
    from ai_test_asset_center.analyzers import (
        BusinessRulesAnalyzer,
        StateMachineAnalyzer,
        MultiTenantAnalyzer,
        ConservationAnalyzer
    )
    print("OK: All analyzers imported")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

# 测试2: 导入增强引擎
try:
    from ai_test_asset_center.enhanced_discovery_engine import create_enhanced_engine
    print("OK: Enhanced engine imported")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

# 测试3: 单独导入分析器
try:
    from ai_test_asset_center.analyzers.business_rules import analyze_prd_rules
    print("OK: business_rules module OK")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\nAll tests completed!")
