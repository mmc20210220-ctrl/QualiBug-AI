from __future__ import annotations

"""
QualiBug Autonomous Discovery Engine — 自主发现引擎

连接三层 prompt + 运行时验证：
  Reader  → 从 PRD/OpenAPI 提取业务事实
  Reasoner → 从事实生成可验证假设
  Executor → 解析假设中的 verification_method 并执行 API 调用
  Verifier → 对比 API 结果与假设预期，判定 confirmed/falsified

这是 QualiBug 真正的壁垒：不靠手动写探针，从文档到 Bug 全自动。
"""

import base64
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ..budget_feedback_store import (
    load_budget_feedback_profile,
    persist_budget_feedback_profile,
    resolve_budget_learning_context,
)
from ..console_output import safe_print as print
from ..deployment_config_resolver import (
    build_deployment_config_snapshot,
    detect_deployment_config_drift,
    evaluate_deployment_drift_unlock,
    load_deployment_config_snapshot,
    persist_deployment_config_snapshot,
)
from ..llm_reasoning import _get_client, ReasoningClient, ReasoningClientError
from ..real_id_resolver import (
    QUALIBUG_UNRESOLVED_ID,
    extract_first_entity_id,
    resolve_real_id_from_documented_list,
)


@dataclass
class DiscoveryFinding:
    hypothesis_id: str
    title: str
    severity: str
    verdict: str  # confirmed | falsified | inconclusive | execution_error
    expected: str
    actual: str
    evidence: dict = field(default_factory=dict)
    confidence: float = 0.0
