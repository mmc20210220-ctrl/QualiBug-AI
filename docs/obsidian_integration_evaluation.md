# QualiBug + Obsidian 融合评估报告

> 评估目标：将 Obsidian 知识管理能力融入 QualiBug AI 自主漏洞发现平台，是否可行、价值多大、如何实施。

---

## 1. 产品背景

**QualiBug** 是一个 AI 驱动的自主 bug 发现平台，核心流程为四阶段循环：

```
Stage 1 Reader   → 从 PRD/OpenAPI 提取业务实体和规则（调用 DeepSeek LLM）
Stage 2 Reasoner → 11 个推理引擎并行生成 bug 假设（因果/不变性/一致性/时序等）
Stage 3 Executor → 对目标系统执行 API 探针，收集证据
Stage 4 Verifier → 比对 API 响应与假设预期，判定 confirmed/falsified/inconclusive
      ↓
Self-Improving Loop → Observe → Diagnose → Improve → Verify → 下一轮
```

当前痛点：
- Reader 阶段需要将完整 PRD（~8000 字符）塞进 LLM prompt，单次 API 耗时 150-200 秒
- 每轮发现的 bug 假设存储在 JSON 文件中，下一轮无法有效复用历史知识
- 跨轮次的知识图谱（哪些实体相关、哪些引擎命中率高、哪些改进有效）完全不可见
- 产品仅面向单人使用，缺乏团队协作的知识层

---

## 2. 集成架构

```
┌─────────────────────────────────────────────────┐
│              QualiBug 自主发现循环                  │
│  Reader → Reasoner(11引擎) → Executor → Verifier   │
│              ↓↑                    ↓               │
│        [RAG检索]            [导出发现]              │
│              ↓                    ↓               │
│  ┌─────────────────────────────────────────────┐  │
│  │           Obsidian 知识层（.md vault）         │  │
│  │                                              │  │
│  │  Bug知识图谱    项目上下文仓库    进化记忆库     │  │
│  │  实体↔假设↔证据   PRD/API/规则/模板  每轮改进     │  │
│  │       ↓              ↓               ↓        │  │
│  │       └──────────────┼───────────────┘        │  │
│  │                      ↓                        │  │
│  │         Graph View（双向链接可视化）              │  │
│  └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘

核心数据流：
  下行：QualiBug 发现 → 自动生成 .md 文件 → Obsidian 展示
  上行：Obsidian vault → RAG 检索相关上下文 → 注入 Reasoner prompt
```

---

## 3. 集成价值评估

| 维度 | 当前状态 | Obsidian 集成后 | 提升幅度 |
|------|---------|----------------|---------|
| Reader API 耗时 | 150-200s（全量 PRD） | 15-30s（分层知识压缩） | 5-10x |
| 跨轮次知识复用 | 仅 18 实体 cache | 全量图谱 + 历史模式 | 从零到完整 |
| Reasoner 命中率 | 当前 Verifier 不稳定，0 bugs confirmed | 上下文增强后更精准 | 预估 2-3x |
| 人工可读性 | JSON 文件 | Obsidian 双向链接图谱 | 质的飞跃 |
| 团队协作 | 本地文件 | 共享 vault（Git 同步） | 单人→团队 |
| 自进化可视化 | 日志文件 | Graph View 展示进化轨迹 | 从不可见到直观 |

### 三个核心增益

1. **持久化知识图谱**：将碎片化 JSON 发现转为 `[[Material]] → [[BOM]] → [[Bug#P0-001]]` 双向链接，形成活的领域地图
2. **Prompt 上下文压缩**：不再全量塞 PRD，改为按需检索相关实体卡片 + 历史 bug 模式，Reasoner prompt 从 8000 字符压缩到 2000-3000 字符
3. **可视化自进化轨迹**：Graph View 直观展示哪个 bug 被哪个引擎发现 → 哪轮改进消除了它 → 有没有回归

---

## 4. 实施分层

### Level 1：单向导出（1-2 天，低投入高回报）

每个 round 结束后，自动将 findings 生成为 Obsidian 兼容的 `.md` 文件。

```python
def export_to_obsidian_vault(findings: list[DiscoveryFinding], vault_dir: Path):
    """将 QualiBug 发现导出为 Obsidian vault"""
    vault_dir.mkdir(parents=True, exist_ok=True)
    
    for f in findings:
        md_content = f"""---
severity: {f.severity}
verdict: {f.verdict}
engine: {f.engine_name}
entities: [{', '.join(f.entities)}]
round: {f.round_num}
---

# {f.title}

## 假设
{f.description}

## 预期行为
{f.expected_behavior}

## 实际行为
{f.actual_behavior}

## 关联实体
{chr(10).join(f'- [[{e}]]' for e in f.entities)}

## 证据
- 探针数: {len(f.evidence_calls)}
- 置信度: {f.confidence}
"""
        (vault_dir / f"{f.id}.md").write_text(md_content, encoding='utf-8')
    
    # 生成实体索引页
    entity_index = build_entity_index(findings)
    (vault_dir / "_index.md").write_text(entity_index, encoding='utf-8')
```

用户只需用 Obsidian 打开这个 vault 目录，即可看到完整的 Graph View。

### Level 2：双向 RAG 增强（1-2 周，核心收益）

在 Reasoner prompt 构造阶段，从 Obsidian vault 检索相关上下文。

```python
def enrich_prompt_with_vault(prompt: str, entity_names: list[str], 
                              vault_dir: Path) -> str:
    """从 Obsidian vault 检索相关上下文，注入 Reasoner prompt"""
    context_parts = []
    
    for entity in entity_names:
        entity_note = vault_dir / f"{entity}.md"
        if entity_note.exists():
            # 提取实体核心信息（前 300 字符）
            content = entity_note.read_text(encoding='utf-8')
            context_parts.append(f"## {entity}\n{content[:300]}")
        
        # 检索反向链接（哪些 bug 涉及此实体）
        backlinks = search_backlinks(vault_dir, entity)
        if backlinks:
            context_parts.append(f"### {entity} 历史Bug\n{backlinks[:500]}")
    
    # 检索通用 bug 模式
    patterns = search_tagged_notes(vault_dir, "bug-pattern")
    if patterns:
        context_parts.append(f"## 已知Bug模式\n{patterns[:500]}")
    
    vault_context = "\n\n".join(context_parts)
    return f"{vault_context}\n\n---\n\n{prompt}"
```

预期效果：
- Prompt 大小从 ~8000 字符降至 ~3000 字符
- DeepSeek API 耗时从 60 秒降至 15-20 秒
- 11 个引擎总耗时从 ~11 分钟降至 ~3 分钟（配合 4 并行 workers）

### Level 3：Obsidian 社区插件（1-2 月，远景）

开发 Obsidian 插件，在 Obsidian 内一键触发 QualiBug discovery，结果实时回写。需要 TypeScript + Obsidian Plugin API。

---

## 5. License / 商业模式评估

### 关键事实

| 场景 | 是否侵权 | 说明 |
|------|---------|------|
| QualiBug **生成** `.md` 文件 | ❌ 不侵权 | 纯文本格式，无专利限制 |
| QualiBug **读取** `.md` 做 RAG | ❌ 不侵权 | 文件 I/O，不调用 Obsidian |
| 用户自己装 Obsidian 打开 vault | ❌ 不侵权 | 用户自己的软件使用行为 |
| QualiBug **分发** Obsidian 安装包 | ✅ 侵权 | 绝对不做 |
| QualiBug **读取** `.obsidian/` 配置 | ❌ 不侵权 | JSON 配置文件，公开格式 |
| 内置 D3.js 图谱可视化 | ❌ 不侵权 | D3.js 是 BSD 协议，商用自由 |

### 双轨制方案

- **有 Obsidian 的用户**：直接打开 vault，享受完整 Obsidian 体验
- **没有 Obsidian 的用户**：用 QualiBug 内置的 D3.js Web 图谱（自研，零依赖）

Obsidian 的 `[[双向链接]]` 语法和 `.md` 格式是**完全开放的标准**。QualiBug 只需要做文件读写，不捆绑、不分发 Obsidian 软件，License 零风险。

---

## 6. 实施路线图

```
Phase 1（本周）: Level 1 单向导出
  ├── 实现 ObsidianVaultExporter 模块
  ├── 每个 round 结束后自动生成 vault
  └── 用户打开 Obsidian 即可看到 Graph View

Phase 2（2 周内）: Level 2 RAG 增强
  ├── Reasoner prompt 从 vault 检索历史模式
  ├── Reader 使用分层知识压缩 prompt
  └── 预期：API 耗时降低 70%，bug 命中率提升 2x

Phase 3（1-2 月，可选）: Level 3 插件 + 内置图谱
  ├── 内置 D3.js Web 图谱（替代 Obsidian Graph 对于无 Obsidian 用户）
  └── Obsidian 社区插件（可选，增强体验）
```

---

## 7. 风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Obsidian License | 低 | 只读写 .md 文件，不捆绑软件 |
| Vault 规模过大导致 RAG 检索慢 | 中 | 用现有 SQLite 做索引层，.md 仅做展示 |
| Reasoner prompt 注入质量不稳定 | 中 | A/B 测试：有 RAG vs 无 RAG 的 bug 命中率对比 |
| 团队协作 .md 冲突 | 低 | Git 管理 vault，标准 merge 流程 |

---

## 8. 结论

**推荐执行。** Obsidian 集成在 License 上零风险（纯文件读写），技术上低门槛（Markdown I/O），收益上高杠杆（直接解决 Reader 耗时长、Reasoner 命中率低两个核心瓶颈）。

建议从 Phase 1 开始，2 天出 MVP，验证用户对 Graph View 的接受度后，再投入 Phase 2 的 RAG 增强。
