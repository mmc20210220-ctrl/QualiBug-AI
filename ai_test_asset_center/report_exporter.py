from __future__ import annotations

"""
QualiBug AI - Report Exporter Module

Week 3 optimization: Enhanced reporting and export capabilities

Features:
- Multiple export formats (JSON, CSV, Markdown, HTML)
- Customizable report templates
- Issue tracker integration (JIRA, GitHub)
- Summary statistics and visualization data
"""

import json
import csv
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    """Represents a single bug finding for reporting"""
    hypothesis_id: str
    title: str
    severity: str
    verdict: str
    confidence: float
    description: str
    reproduction_steps: Optional[List[str]] = None
    evidence: Optional[Dict[str, Any]] = None
    discovered_at: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReportExporter:
    """Report exporter for QualiBug AI findings
    
    Supports multiple export formats and custom templates.
    """
    
    def __init__(self, findings: List[Finding], 
                 project_name: str = "Unknown Project"):
        self.findings = findings
        self.project_name = project_name
        self.generated_at = datetime.now().isoformat()
    
    def export_json(self, output_path: Path) -> Path:
        """Export findings to JSON format
        
        Args:
            output_path: Path to save the JSON file
            
        Returns:
            Path to the exported file
        """
        report_data = {
            "project_name": self.project_name,
            "generated_at": self.generated_at,
            "total_findings": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "summary": self._generate_summary()
        }
        
        output_path.write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        logger.info(f"[ReportExporter] Exported {len(self.findings)} findings to {output_path}")
        return output_path
    
    def export_csv(self, output_path: Path) -> Path:
        """Export findings to CSV format
        
        Args:
            output_path: Path to save the CSV file
            
        Returns:
            Path to the exported file
        """
        if not self.findings:
            logger.warning("[ReportExporter] No findings to export to CSV")
            return output_path
        
        # Get all field names
        fieldnames = list(self.findings[0].to_dict().keys())
        
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for finding in self.findings:
                writer.writerow(finding.to_dict())
        
        logger.info(f"[ReportExporter] Exported {len(self.findings)} findings to {output_path}")
        return output_path
    
    def export_markdown(self, output_path: Path) -> Path:
        """Export findings to Markdown format
        
        Args:
            output_path: Path to save the Markdown file
            
        Returns:
            Path to the exported file
        """
        lines = []
        
        # Header
        lines.append(f"# QualiBug AI - {self.project_name}")
        lines.append(f"")
        lines.append(f"Generated: {self.generated_at}")
        lines.append(f"")
        
        # Summary
        lines.append("## Summary")
        lines.append("")
        summary = self._generate_summary()
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        
        # Findings
        lines.append("## Findings")
        lines.append("")
        
        for i, finding in enumerate(self.findings, 1):
            lines.append(f"### Finding {i}: {finding.title}")
            lines.append("")
            lines.append(f"- Hypothesis ID: {finding.hypothesis_id}")
            lines.append(f"- Severity: {finding.severity}")
            lines.append(f"- Verdict: {finding.verdict}")
            lines.append(f"- Confidence: {finding.confidence:.2%}")
            lines.append("")
            lines.append(f"Description:")
            lines.append(f"> {finding.description}")
            lines.append("")
        
        output_path.write_text("\n".join(lines), encoding="utf-8")
        
        logger.info(f"[ReportExporter] Exported {len(self.findings)} findings to {output_path}")
        return output_path
    
    def export_html(self, output_path: Path) -> Path:
        """Export findings to HTML format
        
        Args:
            output_path: Path to save the HTML file
            
        Returns:
            Path to the exported file
        """
        html_content = self._generate_html_report()
        output_path.write_text(html_content, encoding="utf-8")
        
        logger.info(f"[ReportExporter] Exported {len(self.findings)} findings to {output_path}")
        return output_path
    
    def export_all(self, output_dir: Path, 
                   formats: List[str] = ["json", "csv", "md", "html"]) -> Dict[str, Path]:
        """Export findings in multiple formats
        
        Args:
            output_dir: Directory to save the exported files
            formats: List of formats to export
            
        Returns:
            Dictionary mapping format names to exported file paths
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        results = {}
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for fmt in formats:
            filename = f"qualibug_report_{timestamp}.{fmt}"
            output_path = output_dir / filename
            
            try:
                if fmt == "json":
                    results[fmt] = self.export_json(output_path)
                elif fmt == "csv":
                    results[fmt] = self.export_csv(output_path)
                elif fmt == "md":
                    results[fmt] = self.export_markdown(output_path)
                elif fmt == "html":
                    results[fmt] = self.export_html(output_path)
                else:
                    logger.warning(f"[ReportExporter] Unknown format: {fmt}")
            except Exception as e:
                logger.error(f"[ReportExporter] Failed to export {fmt}: {e}")
        
        return results
    
    def _generate_summary(self) -> Dict[str, Any]:
        """Generate summary statistics for the report"""
        if not self.findings:
            return {
                "total_findings": 0,
                "confirmed_findings": 0,
                "critical_findings": 0
            }
        
        confirmed = sum(1 for f in self.findings if f.verdict == "confirmed")
        critical = sum(1 for f in self.findings if f.severity in ["P0", "P1"])
        
        return {
            "total_findings": len(self.findings),
            "confirmed_findings": confirmed,
            "critical_findings": critical,
            "average_confidence": sum(f.confidence for f in self.findings) / len(self.findings)
        }
    
    def _generate_html_report(self) -> str:
        """Generate HTML report content"""
        summary = self._generate_summary()
        
        # Start HTML
        html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QualiBug AI Report</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        h1 {
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }
        h2 {
            color: #34495e;
            border-bottom: 1px solid #ddd;
            padding-bottom: 5px;
        }
        h3 {
            color: #7f8c8d;
        }
        .summary {
            background: #f8f9fa;
            border-left: 4px solid #3498db;
            padding: 20px;
            margin: 20px 0;
            border-radius: 4px;
        }
        .finding {
            border: 1px solid #ddd;
            border-left: 4px solid #e74c3c;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }
        .severity-P0 { border-left-color: #e74c3c; }
        .severity-P1 { border-left-color: #e67e22; }
        .severity-P2 { border-left-color: #f39c12; }
        .severity-P3 { border-left-color: #3498db; }
        .confirmed { background: #d4edda; }
        .inconclusive { background: #fff3cd; }
        .tag {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            font-weight: bold;
            margin-right: 5px;
        }
        .tag-severity-P0 { background: #e74c3c; color: white; }
        .tag-severity-P1 { background: #e67e22; color: white; }
        .tag-severity-P2 { background: #f39c12; color: white; }
        .tag-severity-P3 { background: #3498db; color: white; }
        .tag-verdict-confirmed { background: #27ae60; color: white; }
        .tag-verdict-inconclusive { background: #f1c40f; color: #333; }
        pre {
            background: #f8f9fa;
            padding: 10px;
            border-radius: 3px;
            overflow-x: auto;
        }
    </style>
</head>
<body>
"""
        
        # Title
        html += f"<h1>QualiBug AI - {self.project_name}</h1>\n"
        html += f"<p><strong>Generated:</strong> {self.generated_at}</p>\n"
        
        # Summary
        html += "<div class='summary'>\n"
        html += "<h2>Summary</h2>\n"
        html += "<ul>\n"
        for key, value in summary.items():
            display_key = key.replace('_', ' ').title()
            if isinstance(value, float):
                html += f"  <li><strong>{display_key}:</strong> {value:.2f}</li>\n"
            else:
                html += f"  <li><strong>{display_key}:</strong> {value}</li>\n"
        html += "</ul>\n"
        html += "</div>\n"
        
        # Findings
        html += "<h2>Findings</h2>\n"
        
        for i, finding in enumerate(self.findings, 1):
            severity_class = f"severity-{finding.severity}"
            verdict_class = finding.verdict.lower()
            
            html += f"<div class='finding {severity_class} {verdict_class}'>\n"
            html += f"<h3>Finding {i}: {finding.title}</h3>\n"
            
            # Tags
            html += "<p>\n"
            html += f"<span class='tag tag-severity-{finding.severity}'>{finding.severity}</span>\n"
            html += f"<span class='tag tag-verdict-{finding.verdict}'>{finding.verdict}</span>\n"
            html += f"<span>Confidence: {finding.confidence:.1%}</span>\n"
            html += "</p>\n"
            
            # Details
            html += f"<p><strong>Hypothesis ID:</strong> {finding.hypothesis_id}</p>\n"
            html += f"<p><strong>Description:</strong></p>\n"
            html += f"<blockquote>{finding.description}</blockquote>\n"
            
            # Steps
            if finding.reproduction_steps:
                html += f"<p><strong>Reproduction Steps:</strong></p>\n"
                html += "<ol>\n"
                for step in finding.reproduction_steps:
                    html += f"  <li>{step}</li>\n"
                html += "</ol>\n"
            
            html += "</div>\n"
        
        # Close HTML
        html += """
</body>
</html>
"""
        
        return html


# Convenience functions
def create_exporter_from_results(results: List[Dict[str, Any]], 
                                 project_name: str = "Unknown Project") -> ReportExporter:
    """Create a ReportExporter from raw results data
    
    Args:
        results: List of result dictionaries
        project_name: Name of the project
        
    Returns:
        Configured ReportExporter instance
    """
    findings = []
    for r in results:
        findings.append(Finding(
            hypothesis_id=r.get("hypothesis_id", "unknown"),
            title=r.get("title", "Untitled Finding"),
            severity=r.get("severity", "P2"),
            verdict=r.get("verdict", "inconclusive"),
            confidence=r.get("confidence", 0.0),
            description=r.get("description", "No description provided"),
            reproduction_steps=r.get("reproduction_steps"),
            evidence=r.get("evidence"),
            discovered_at=r.get("discovered_at")
        ))
    
    return ReportExporter(findings, project_name)
