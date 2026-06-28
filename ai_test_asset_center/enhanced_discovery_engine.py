from __future__ import annotations

"""
QualiBug AI - 增强的发现引擎 (阶段1-3)

整合所有优化分析器，提升bug发现能力。
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

# 导入分析器
from .analyzers.business_rules import BusinessRulesAnalyzer
from .analyzers.state_machine import StateMachineAnalyzer
from .analyzers.multi_tenant import MultiTenantAnalyzer
from .analyzers.conservation import ConservationAnalyzer

# 导入优化引擎
from .optimized_discovery_engine import OptimizedDiscoveryEngine

logger = logging.getLogger(__name__)


class EnhancedDiscoveryEngine(OptimizedDiscoveryEngine):
    """
    增强的发现引擎

    整合了所有新的分析器，提升bug发现能力：
    - 业务规则分析 (C01, C08, C09, C13)
    - 状态机分析 (C06, C07)
    - 多租户隔离分析 (C05)
    - 守恒规则分析 (C08)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000/api",
        enable_checkpoints: bool = True,
        enable_optimizations: bool = True
    ):
        """
        初始化增强引擎

        Args:
            base_url: 基础URL
            enable_checkpoints: 是否启用检查点
            enable_optimizations: 是否启用所有优化
        """
        super().__init__(
            base_url=base_url,
            enable_checkpoints=enable_checkpoints
        )

        # 初始化分析器
        self.business_rules_analyzer = BusinessRulesAnalyzer()
        self.state_machine_analyzer = StateMachineAnalyzer()
        self.multi_tenant_analyzer = MultiTenantAnalyzer()
        self.conservation_analyzer = ConservationAnalyzer()

        # 所有发现的bugs
        self.all_discoveries: List[Dict[str, Any]] = []

        logger.info("增强发现引擎初始化完成")

    def run_enhanced_discovery(
        self,
        prd_text: str,
        api_spec_text: str,
        project_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        运行增强的发现流程

        Args:
            prd_text: PRD文档
            api_spec_text: API规格文档
            project_context: 项目上下文

        Returns:
            发现结果
        """
        logger.info("开始增强发现流程...")

        results = {
            "phase": "enhanced_discovery",
            "status": "started",
            "discoveries": [],
            "analysis": {}
        }

        # 1. 运行业务规则分析
        logger.info("1. 运行业务规则分析...")
        rules = self.business_rules_analyzer.extract_rules_from_prd(prd_text)
        rule_violations = self.business_rules_analyzer.validate_rule_implementation(
            rules,
            self._parse_api_spec(api_spec_text)
        )
        results["analysis"]["business_rules"] = self.business_rules_analyzer.get_summary()
        results["discoveries"].extend([self._convert_to_discovery(v, "C01") for v in rule_violations])

        # 2. 运行状态机分析
        logger.info("2. 运行状态机分析...")
        sm = self.state_machine_analyzer.extract_state_machine(prd_text, self._parse_api_spec(api_spec_text))
        sm_bugs = self.state_machine_analyzer.validate_state_transitions(sm, [])
        results["analysis"]["state_machine"] = self.state_machine_analyzer.get_summary()
        results["discoveries"].extend([self._convert_to_discovery(b, "C06") for b in sm_bugs])

        # 3. 运行多租户隔离分析
        logger.info("3. 运行多租户隔离分析...")
        mt_bugs = self.multi_tenant_analyzer.analyze_api_endpoints(self._parse_api_spec(api_spec_text))
        results["analysis"]["multi_tenant"] = self.multi_tenant_analyzer.get_summary()
        results["discoveries"].extend([self._convert_to_discovery(b, "C05") for b in mt_bugs])

        # 4. 运行守恒规则分析
        logger.info("4. 运行守恒规则分析...")
        conservation_bugs = self.conservation_analyzer.analyze_conservation(
            prd_text, self._parse_api_spec(api_spec_text)
        )
        results["analysis"]["conservation"] = self.conservation_analyzer.get_summary()
        results["discoveries"].extend([self._convert_to_discovery(b, "C08") for b in conservation_bugs])

        # 收集所有发现
        self.all_discoveries.extend(results["discoveries"])

        # 完成
        results["status"] = "completed"
        logger.info(f"增强发现完成，共发现 {len(results['discoveries'])} 个潜在问题")

        return results

    def _parse_api_spec(self, spec_text: str) -> Dict[str, Any]:
        """解析API规格（简化）"""
        # 在真实实现中，这会解析OpenAPI yaml/json
        # 这里返回一个模拟值
        return {
            "paths": {
                "/api/orders": {
                    "get": {"summary": "获取订单列表"},
                    "post": {"summary": "创建订单"}
                }
            }
        }

    def _convert_to_discovery(self, obj: Any, category: str) -> Dict[str, Any]:
        """将各种对象转换为统一的发现格式"""
        # 提取通用字段
        discovery = {
            "category": category,
            "title": getattr(obj, "title", "未命名发现"),
            "description": getattr(obj, "description", ""),
            "severity": getattr(obj, "severity", "P2"),
            "evidence": getattr(obj, "evidence", {}),
            "reproduction_steps": getattr(obj, "reproduction_steps", []),
            "expected_behavior": getattr(obj, "expected_behavior", ""),
            "actual_behavior": getattr(obj, "actual_behavior", "")
        }
        return discovery

    def export_findings_to_json(self, output_path: Path) -> Path:
        """
        导出发现结果为JSON

        Args:
            output_path: 输出路径

        Returns:
            输出文件路径
        """
        import json

        output = {
            "total_findings": len(self.all_discoveries),
            "findings": self.all_discoveries,
            "summary": self.get_enhanced_summary()
        }

        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(f"发现结果已导出到: {output_path}")
        return output_path

    def export_findings_to_html(self, output_path: Path) -> Path:
        """
        导出发现结果为HTML

        Args:
            output_path: 输出路径

        Returns:
            输出文件路径
        """
        html_content = self._generate_html_report()
        output_path.write_text(html_content, encoding="utf-8")

        logger.info(f"HTML报告已导出到: {output_path}")
        return output_path

    def _generate_html_report(self) -> str:
        """生成HTML报告"""
        summary = self.get_enhanced_summary()

        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QualiBug AI 发现报告</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; }}
        h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; color: #2c3e50; }}
        h2 {{ color: #34495e; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
        .summary {{ background: #f8f9fa; padding: 15px; border-left: 4px solid #3498db; margin: 15px 0; }}
        .finding {{ border: 1px solid #ddd; margin: 10px 0; padding: 15px; border-radius: 4px; }}
        .severity-P0 {{ border-left: 4px solid #e74c3c; background: #fff5f5; }}
        .severity-P1 {{ border-left: 4px solid #e67e22; background: #fffbf5; }}
        .severity-P2 {{ border-left: 4px solid #f39c12; background: #fffef5; }}
        .tag {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; font-weight: bold; margin-right: 5px; }}
        .tag-P0 {{ background: #e74c3c; color: white; }}
        .tag-P1 {{ background: #e67e22; color: white; }}
        .tag-P2 {{ background: #f39c12; color: white; }}
        pre {{ background: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; }}
    </style>
</head>
<body>
    <h1>🔍 QualiBug AI 发现报告</h1>

    <div class="summary">
        <h2>摘要</h2>
        <p><strong>总发现数:</strong> {summary['total_findings']}</p>
        <p><strong>P0 严重:</strong> {summary['severity_count']['P0']}</p>
        <p><strong>P1 高:</strong> {summary['severity_count']['P1']}</p>
        <p><strong>P2 中:</strong> {summary['severity_count']['P2']}</p>
    </div>

    <h2>发现详情</h2>
"""

        for finding in self.all_discoveries:
            severity = finding.get('severity', 'P2')
            html += f"""
    <div class="finding severity-{severity}">
        <h3>
            <span class="tag tag-{severity}">{severity}</span>
            [{finding.get('category', 'N/A')}] {finding.get('title', '未命名')}
        </h3>
        <p><strong>描述:</strong> {finding.get('description', '')}</p>
        <p><strong>预期行为:</strong> {finding.get('expected_behavior', '')}</p>
        <p><strong>实际行为:</strong> {finding.get('actual_behavior', '')}</p>
        <p><strong>复现步骤:</strong></p>
        <ol>
"""
            for step in finding.get('reproduction_steps', []):
                html += f"<li>{step}</li>"

            html += f"""
        </ol>
    </div>
"""

        html += """
</body>
</html>
"""
        return html

    def get_enhanced_summary(self) -> Dict[str, Any]:
        """获取增强引擎的摘要"""
        # 统计按严重程度
        severity_count = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        for finding in self.all_discoveries:
            sev = finding.get('severity', 'P2')
            if sev in severity_count:
                severity_count[sev] += 1

        # 统计按分类
        category_count = {}
        for finding in self.all_discoveries:
            cat = finding.get('category', 'N/A')
            category_count[cat] = category_count.get(cat, 0) + 1

        return {
            "total_findings": len(self.all_discoveries),
            "severity_count": severity_count,
            "category_count": category_count,
            "analyzers": {
                "business_rules": self.business_rules_analyzer.get_summary(),
                "state_machine": self.state_machine_analyzer.get_summary(),
                "multi_tenant": self.multi_tenant_analyzer.get_summary(),
                "conservation": self.conservation_analyzer.get_summary()
            }
        }


# 便捷工厂函数
def create_enhanced_engine(
    base_url: str = "http://127.0.0.1:8000/api",
    enable_checkpoints: bool = True
) -> EnhancedDiscoveryEngine:
    """
    创建增强发现引擎

    Args:
        base_url: 基础URL
        enable_checkpoints: 是否启用检查点

    Returns:
        增强发现引擎实例
    """
    return EnhancedDiscoveryEngine(
        base_url=base_url,
        enable_checkpoints=enable_checkpoints
    )
