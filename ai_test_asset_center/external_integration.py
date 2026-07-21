from __future__ import annotations

"""
QualiBug AI - External System Integration Module

Week 3 optimization: Issue tracker integration and third-party connectors

Features:
- JIRA integration
- GitHub Issues integration
- Custom issue tracker adapters
- Bulk issue creation and sync
"""

import json
import os
import logging
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass
from pathlib import Path
import base64

logger = logging.getLogger(__name__)


@dataclass
class Issue:
    """Represents an issue to be created in an external tracker"""
    title: str
    description: str
    severity: str
    labels: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class TrackerIntegration:
    """Base class for issue tracker integrations
    
    Subclass this to add support for new trackers.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    def create_issue(self, issue: Issue) -> Dict[str, Any]:
        """Create a new issue in the tracker
        
        Args:
            issue: Issue to create
            
        Returns:
            Dictionary with created issue information (e.g., issue ID)
        """
        raise NotImplementedError("Subclasses must implement create_issue")
    
    def create_issues_bulk(self, issues: List[Issue]) -> List[Dict[str, Any]]:
        """Create multiple issues in bulk
        
        Args:
            issues: List of issues to create
            
        Returns:
            List of created issue information dictionaries
        """
        results = []
        for i, issue in enumerate(issues):
            try:
                logger.info(f"[TrackerIntegration] Creating issue {i+1}/{len(issues)}")
                result = self.create_issue(issue)
                results.append(result)
            except Exception as e:
                logger.error(f"[TrackerIntegration] Failed to create issue {i+1}: {e}")
                results.append({"error": str(e), "issue": issue})
        return results
    
    def sync_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sync findings with the tracker
        
        Converts findings to issues and creates them in the tracker.
        
        Args:
            findings: List of findings to sync
            
        Returns:
            List of created issue information
        """
        issues = []
        for f in findings:
            issues.append(Issue(
                title=f.get("title", "Untitled Finding"),
                description=f.get("description", "No description provided"),
                severity=f.get("severity", "P2"),
                labels=[f"severity:{f.get('severity', 'P2')}", "qualibug-ai"],
                metadata=f
            ))
        
        return self.create_issues_bulk(issues)


class JIRAIntegration(TrackerIntegration):
    """JIRA issue tracker integration"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.server = config.get("server", "")
        self.project_key = config.get("project_key", "")
        self.email = config.get("email", "")
        self.api_token = config.get("api_token", "")
        logger.info(f"[JIRAIntegration] Initialized for project {self.project_key}")
    
    def create_issue(self, issue: Issue) -> Dict[str, Any]:
        """Create an issue in JIRA via REST API.
        
        Requires QUALIBUG_JIRA_SERVER, QUALIBUG_JIRA_PROJECT, QUALIBUG_JIRA_EMAIL,
        and QUALIBUG_JIRA_TOKEN environment variables.
        """
        if not self.server or not self.project_key:
            raise NotImplementedError(
                "JIRA integration not configured. Set QUALIBUG_JIRA_SERVER and "
                "QUALIBUG_JIRA_PROJECT environment variables."
            )
        
        logger.info(f"[JIRAIntegration] Creating issue: {issue.title}")
        
        # Use JIRA REST API v3
        import urllib.request, json as _json
        url = f"{self.server.rstrip('/')}/rest/api/3/issue"
        payload = _json.dumps({
            "fields": {
                "project": {"key": self.project_key},
                "summary": issue.title,
                "description": issue.description or f"Severity: {issue.severity}\nConfidence: {issue.confidence}",
                "issuetype": {"name": "Bug"},
                "priority": {"name": "Highest" if issue.severity == "P0" else "High" if issue.severity == "P1" else "Medium"},
            }
        }).encode()
        req = urllib.request.Request(url, data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Basic {base64.b64encode(f'{self.email}:{self.api_token}'.encode()).decode()}"},
            method="POST")
        resp = urllib.request.urlopen(req, timeout=30)
        data = _json.loads(resp.read())
        return {
            "tracker": "jira",
            "issue_key": data.get("key", ""),
            "project": self.project_key,
            "title": issue.title,
            "severity": issue.severity,
            "created": True,
            "url": f"{self.server}/browse/{data.get('key', '')}"
        }


class GitHubIntegration(TrackerIntegration):
    """GitHub Issues integration"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.repo_owner = config.get("repo_owner", "")
        self.repo_name = config.get("repo_name", "")
        self.api_token = config.get("api_token", "")
        logger.info(f"[GitHubIntegration] Initialized for repo {self.repo_owner}/{self.repo_name}")
    
    def create_issue(self, issue: Issue) -> Dict[str, Any]:
        """Create an issue in GitHub via REST API.
        
        Requires QUALIBUG_GITHUB_OWNER, QUALIBUG_GITHUB_REPO, and
        QUALIBUG_GITHUB_TOKEN environment variables.
        """
        if not self.repo_owner or not self.repo_name:
            raise NotImplementedError(
                "GitHub integration not configured. Set QUALIBUG_GITHUB_OWNER and "
                "QUALIBUG_GITHUB_REPO environment variables."
            )
        
        logger.info(f"[GitHubIntegration] Creating issue: {issue.title}")
        
        import urllib.request, json as _json
        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues"
        payload = _json.dumps({
            "title": f"[QualiBug] {issue.title}",
            "body": f"**Severity:** {issue.severity}\n**Confidence:** {issue.confidence}\n\n{issue.description or ''}",
            "labels": ["bug", "qualibug", issue.severity.lower()],
        }).encode()
        headers = {"Content-Type": "application/json",
                   "Accept": "application/vnd.github.v3+json",
                   "User-Agent": "QualiBug/1.0"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=30)
        data = _json.loads(resp.read())
        return {
            "tracker": "github",
            "issue_number": data.get("number", 0),
            "repo": f"{self.repo_owner}/{self.repo_name}",
            "title": issue.title,
            "severity": issue.severity,
            "created": True,
            "url": data.get("html_url", "")
        }


def load_tracker_config_from_env() -> Dict[str, Any]:
    """Load tracker configuration from environment variables
    
    Returns:
        Configuration dictionary
    """
    config = {
        "type": os.environ.get("QUALIBUG_TRACKER_TYPE", ""),
        "jira": {
            "server": os.environ.get("QUALIBUG_JIRA_SERVER", ""),
            "project_key": os.environ.get("QUALIBUG_JIRA_PROJECT", ""),
            "email": os.environ.get("QUALIBUG_JIRA_EMAIL", ""),
            "api_token": os.environ.get("QUALIBUG_JIRA_TOKEN", "")
        },
        "github": {
            "repo_owner": os.environ.get("QUALIBUG_GITHUB_OWNER", ""),
            "repo_name": os.environ.get("QUALIBUG_GITHUB_REPO", ""),
            "api_token": os.environ.get("QUALIBUG_GITHUB_TOKEN", "")
        }
    }
    return config


def create_tracker_integration(tracker_type: str, 
                               config: Optional[Dict[str, Any]] = None) -> TrackerIntegration:
    """Create a tracker integration instance
    
    Args:
        tracker_type: Type of tracker ("jira", "github")
        config: Configuration dictionary (optional, uses env if not provided)
        
    Returns:
        Configured tracker integration instance
    """
    if config is None:
        config = load_tracker_config_from_env()
    
    if tracker_type == "jira":
        return JIRAIntegration(config.get("jira", config))
    elif tracker_type == "github":
        return GitHubIntegration(config.get("github", config))
    else:
        raise ValueError(f"Unknown tracker type: {tracker_type}")


# Convenience function
def sync_findings_to_tracker(findings: List[Dict[str, Any]], 
                              tracker_type: str = "github",
                              config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Sync findings to an external tracker
    
    Args:
        findings: List of findings to sync
        tracker_type: Type of tracker ("jira", "github")
        config: Optional configuration
        
    Returns:
        List of created issue information
    """
    tracker = create_tracker_integration(tracker_type, config)
    return tracker.sync_findings(findings)
