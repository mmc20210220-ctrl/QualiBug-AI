# Debug Session: command-center-502
- **Status**: [OPEN]
- **Issue**: `/api/v1/projects/{project}/command-center` 间歇性 502（前端显示“后端连接失败 API 502”），客户端偶发 `RemoteDisconnected`（服务端关闭连接无响应）。
- **Debug Server**: http://127.0.0.1:7777/event
- **Log File**: .dbg/trae-debug-log-command-center-502.ndjson

## Reproduction Steps
1. 启动后端：`python -m ai_test_asset_center.private_pilot_service`
2. 请求：`GET /api/v1/projects/<project>/command-center?project=<project>`
3. 观察：是否返回 200/500 JSON；或出现连接被关闭（前端 502 / RemoteDisconnected）

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | Handler 在写 JSON 响应前发生未捕获异常（例如 `json.dumps()` 因不可序列化对象抛错），导致连接直接断开 | High | Low | Pending |
| B | `_build_command_center()` 内部某分支抛异常未被当前 try/except 覆盖（或异常发生在 try/except 之外）导致请求线程崩溃 | Med | Med | Pending |
| C | command-center 生成逻辑耗时过长或卡死（I/O/锁/大文件），触发上游代理/浏览器超时表现为 502 | Med | Med | Pending |
| D | 进程级异常/线程异常导致服务短暂不可用（端口仍在但连接被复位），与特定项目数据文件结构有关 | Low | High | Pending |

## Log Evidence
- Pending (will collect via Debug Server)

## Verification Conclusion
- Pending (need pre-fix vs post-fix logs)
