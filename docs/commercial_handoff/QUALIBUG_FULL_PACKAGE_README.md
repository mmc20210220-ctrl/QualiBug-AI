# QualiBug AI 完整源码包说明

本包是基于用户上传的完整本地仓库代码整理出的完整源码包，并合入了证据链正确性与前后端数据统一收尾修改。

## 本次主链路确认

当前商业化主链路以以下路径为准：

1. 后端入口：`ai_test_asset_center/private_pilot_service.py`
2. Command Center 汇聚：`private_pilot_service.py::_build_command_center()`
3. Display-ready 统一格式化：`ai_test_asset_center/display_ready_formatter.py::format_findings_display_ready()`
4. 最终响应兜底清洗：`display_ready_formatter.py::sanitize_customer_evidence_payload()`
5. 前端数据入口：`frontend/src/api/client.ts::getFindings()` / `frontend/src/api/data.ts`
6. 前端只渲染：`data.risks`

## 关于 phase104

`phase104_*` 文件是历史阶段遗留文件。本次不再把 phase104 当作当前主链路依据，也不再让新增证据正确性测试依赖 phase104。

为避免破坏你仓库中已有历史脚本、旧测试和 phase105 中的历史文档字段，本完整包没有强行删除这些文件；但是当前证据链商业化收尾已经放到主链路 `private_pilot_service.py + display_ready_formatter.py` 中。

## 本次合入的核心修复

1. 已复现 Bug、待验证线索、未复现结果分离统计。
2. 前端统计不再用 `||` 把 0 错误回退成原始候选数。
3. HAR 与 `evidence.calls` 统一成 canonical runtime observation。
4. 接口证据必须 method/path 与当前缺陷一致，否则降级为 `not_reproduced`。
5. 响应体与缺陷描述不匹配时，清空错误响应证据、复现率归零、删除误导性断言。
6. 当前主 HTTP 响应层新增最终兜底：`sanitize_customer_evidence_payload()`。

## 已验证

```bash
python -m pytest -q tests/test_evidence_audit_consistency.py
# 49 passed

cd frontend
npm run typecheck
# passed
```

## 本地启动建议

后端：

```bash
python -m ai_test_asset_center.private_pilot_service
```

前端：

```bash
cd frontend
npm install
npm run dev
```

如果需要打正式前端包：

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

## 打包说明

本完整包是源码包，已排除无效 `.git` worktree 指针、`node_modules`、缓存、构建产物和临时目录，避免 Windows/容器平台依赖导致解压后构建异常。`frontend/package.json` 和 `frontend/package-lock.json` 已保留，可在本地重新安装依赖。
