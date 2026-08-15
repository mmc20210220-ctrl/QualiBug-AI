"""Product error code registry.

Every failure path in QualiBug should emit a structured error code from this
registry. Format: ``QB-{MODULE}{SEQ}`` where MODULE is a single uppercase
letter identifying the subsystem and SEQ is a zero-padded sequence number.

Usage::

    from ai_test_asset_center.error_codes import ErrorCode, ERRORS

    # In application code:
    raise ProductError(ErrorCode.LLM_TIMEOUT, detail="DeepSeek did not respond in 300s")

    # In logging:
    logger.error("LLM call failed", extra={"error_code": ErrorCode.LLM_TIMEOUT.code, "context": {...}})
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErrorDef:
    """Immutable error code definition."""

    code: str
    severity: str  # P0 / P1 / P2 / P3
    user_message: str  # 客户能看懂的中文描述
    operator_hint: str  # 运维排查指引
    auto_fix: str = ""  # 可选的自动修复建议

    def __str__(self) -> str:
        return self.code


# ---------------------------------------------------------------------------
# Error Code Registry
# ---------------------------------------------------------------------------

_ERRORS: dict[str, ErrorDef] = {}


def _register(
    code: str,
    severity: str,
    user_message: str,
    operator_hint: str,
    auto_fix: str = "",
) -> ErrorDef:
    err = ErrorDef(
        code=code,
        severity=severity,
        user_message=user_message,
        operator_hint=operator_hint,
        auto_fix=auto_fix,
    )
    _ERRORS[code] = err
    return err


# === QB-S: 服务启动 / HTTP ===

S_PORT_IN_USE = _register(
    "QB-S001", "P0",
    "服务端口被占用，无法启动",
    "检查 QUALIBUG_PORT(默认8088)是否被其他进程占用: netstat -ano | findstr :8088",
    "关闭占用端口的程序，或设置环境变量 QUALIBUG_PORT 为其他端口",
)
S_PYTHON_VERSION = _register(
    "QB-S002", "P0",
    "Python版本过低，需要3.11以上",
    "运行 python --version 确认版本，需 >= 3.11",
    "安装 Python 3.11+ 并重新部署",
)
S_IMPORT_FAILED = _register(
    "QB-S003", "P0",
    "核心模块加载失败",
    "检查依赖是否完整: pip install -r requirements.txt",
)
S_UNHANDLED_EXCEPTION = _register(
    "QB-S999", "P0",
    "发生未预期的程序异常",
    "查看 qualibug_error.log 中的完整堆栈信息",
)
S_THREAD_EXCEPTION = _register(
    "QB-S998", "P1",
    "后台线程发生未预期异常",
    "查看 qualibug_error.log 中 thread 字段的堆栈信息",
)
S_DISK_SPACE_LOW = _register(
    "QB-S004", "P1",
    "磁盘空间不足，可能影响日志和扫描结果保存",
    "检查部署目录所在磁盘剩余空间，建议 > 500MB",
    "清理磁盘或扩容",
)
S_ENV_MISSING = _register(
    "QB-S005", "P2",
    "部分环境变量未配置",
    "对照 .env.local.example 检查必需环境变量",
)
S_STARTUP_CHECK_FAILED = _register(
    "QB-S006", "P1",
    "启动自检发现问题，服务可能无法正常工作",
    "查看启动日志中的具体检查项失败原因",
)

# === QB-C: 凭证 / 认证 ===

C_KEY_MISSING = _register(
    "QB-C001", "P0",
    "凭证加密密钥缺失，无法安全存储凭据",
    "检查 platform_workspace/.secrets/ 目录下是否存在 credential_encryption.key",
    "重启服务会自动生成密钥，但已保存的凭据需要重新配置",
)
C_DECRYPT_FAILED = _register(
    "QB-C002", "P1",
    "凭据解密失败，可能是密钥不匹配",
    "确认 credential_encryption.key 未被替换或损坏",
)
C_TOKEN_EXPIRED = _register(
    "QB-C003", "P1",
    "目标系统认证凭据已过期",
    "在前端「凭据管理」页面重新配置目标系统的账号密码或Token",
)
C_AUTH_REJECTED = _register(
    "QB-C004", "P1",
    "目标系统拒绝了认证请求",
    "确认配置的账号密码/Token正确，且账号未被锁定",
)

# === QB-L: LLM 调用 ===

L_TIMEOUT = _register(
    "QB-L001", "P1",
    "AI模型响应超时",
    "检查模型API端点网络连通性，确认 timeout_seconds >= 300",
    "检查网络连接，或联系模型服务商确认服务状态",
)
L_RATE_LIMIT = _register(
    "QB-L002", "P1",
    "AI模型调用频率超限",
    "降低并发数(max_workers)或增加调用间隔",
)
L_INVALID_RESPONSE = _register(
    "QB-L003", "P2",
    "AI模型返回了无法解析的响应",
    "检查模型版本是否兼容，查看原始响应内容",
)
L_CONNECTION_REFUSED = _register(
    "QB-L004", "P0",
    "无法连接AI模型服务",
    "检查模型API地址和端口是否正确，网络是否可达",
    "确认 API 地址配置正确，检查防火墙规则",
)
L_TOKEN_EXCEEDED = _register(
    "QB-L005", "P2",
    "AI模型输出被截断（超过max_tokens限制）",
    "当前 max_tokens 设置可能不足以容纳完整输出",
)
L_API_KEY_INVALID = _register(
    "QB-L006", "P0",
    "AI模型API密钥无效",
    "在前端「模型配置」页面检查API Key是否正确",
)

# === QB-D: 发现管线 ===

D_BEHAVIOR_IR_FAILED = _register(
    "QB-D001", "P1",
    "行为模型构建失败",
    "检查输入的API文档和PRD是否格式正确、内容完整",
)
D_OBLIGATION_GEN_FAILED = _register(
    "QB-D002", "P2",
    "测试义务生成异常",
    "查看 trace_ledger 中的具体阻塞原因",
)
D_CAMPAIGN_CREATE_FAILED = _register(
    "QB-D003", "P1",
    "扫描任务创建失败",
    "检查项目配置和输入文件是否完整",
)
D_MAINLINE_CONTRACT_ERROR = _register(
    "QB-D004", "P0",
    "发现主线契约校验失败",
    "检查 mainline_authority 配置和 policy_version 是否一致",
)
D_NO_SOURCE_MATERIAL = _register(
    "QB-D005", "P1",
    "缺少必要的输入材料（PRD/API文档/数据库Schema）",
    "在前端「项目管理」页面上传完整的项目资料",
)

# === QB-X: 实验执行 ===

X_CONNECTION_REFUSED = _register(
    "QB-X001", "P1",
    "无法连接目标系统",
    "确认目标系统地址和端口配置正确，且目标系统正在运行",
    "检查目标系统是否启动，网络是否可达",
)
X_TIMEOUT = _register(
    "QB-X002", "P2",
    "目标系统响应超时",
    "目标系统响应过慢，可能是负载过高或网络延迟",
)
X_BLOCKED_MISSING_ACTOR = _register(
    "QB-X003", "P2",
    "缺少执行所需的测试账号",
    "检查项目配置中是否声明了足够的测试角色/账号",
)
X_BLOCKED_MISSING_BINDING = _register(
    "QB-X004", "P2",
    "运行时路径绑定失败",
    "检查API文档中是否包含具体的路径参数示例",
)
X_BLOCKED_MISSING_OBSERVER = _register(
    "QB-X005", "P2",
    "缺少观测器，无法验证执行结果",
    "检查API文档中是否声明了GET查询接口用于验证",
)
X_BLOCKED_CONTROL_ARM_NOT_PROVEN = _register(
    "QB-X008", "P2",
    "对照组请求已发出，但未能证明其执行成功",
    "检查对照组返回的状态码与响应是否被完整采集，确认对照账号权限配置正确",
)
X_DECLARED_INTERFACE_NOT_IMPLEMENTED = _register(
    "QB-X010", "P1",
    "源材料声明的接口在目标运行时返回框架级 404（路由未注册）",
    "接口文档声明了该路由，但部署的目标服务未实现；确认目标版本与文档一致或补充缺失实现",
)
X_BLOCKED_OBSERVER_RECEIPT_INDETERMINATE = _register(
    "QB-X009", "P2",
    "观测器已执行，但回执无法判定结果",
    "检查观测接口返回的字段是否包含可用于判定的状态信息",
)
X_SSL_ERROR = _register(
    "QB-X006", "P1",
    "目标系统SSL证书验证失败",
    "确认目标系统HTTPS证书有效，或配置跳过证书验证",
)
X_DNS_FAILED = _register(
    "QB-X007", "P1",
    "目标系统域名解析失败",
    "检查DNS配置，确认目标系统域名可以正常解析",
)

# === QB-O: Oracle / 判定 ===

O_ACTIVATION_FAILED = _register(
    "QB-O001", "P2",
    "合约Oracle激活条件不满足",
    "检查实验是否包含完整的control/treatment证据",
)
O_ASSERTION_ERROR = _register(
    "QB-O002", "P2",
    "断言执行异常",
    "查看具体断言的输入数据和执行堆栈",
)

# === QB-G: 交付门禁 ===

G_CLEANUP_MISSING = _register(
    "QB-G001", "P2",
    "清理回执缺失，发现无法通过交付门禁",
    "检查目标系统是否提供了DELETE接口用于清理测试数据",
)
G_EVIDENCE_INCOMPLETE = _register(
    "QB-G002", "P2",
    "证据链不完整",
    "检查执行过程中是否有observer或binding失败",
)
G_PRODUCTION_WRITE_BLOCKED = _register(
    "QB-G003", "P0",
    "检测到生产环境写入风险，已自动拦截",
    "确认目标环境类型配置正确（test/staging/production）",
)

# === QB-I: 文档解析 / 输入 ===

I_PARSE_FAILED = _register(
    "QB-I001", "P1",
    "文档解析失败",
    "检查上传的文件格式是否正确（支持.md/.json/.yaml/.docx/.pdf/.har）",
)
I_EMPTY_INPUT = _register(
    "QB-I002", "P1",
    "输入文件为空或无有效内容",
    "确认上传的文件包含有效的API文档或需求描述",
)
I_ENCODING_ERROR = _register(
    "QB-I003", "P2",
    "文件编码无法识别",
    "请将文件转换为UTF-8编码后重新上传",
)

# === QB-N: 网络 / 外部服务 ===

N_DNS_FAILED = _register(
    "QB-N001", "P1",
    "DNS解析失败",
    "检查网络配置和DNS服务器设置",
)
N_PROXY_ERROR = _register(
    "QB-N002", "P1",
    "代理服务器连接失败",
    "如果使用了代理，检查代理地址和端口配置",
)
N_FIREWALL_BLOCKED = _register(
    "QB-N003", "P1",
    "网络连接被防火墙拦截",
    "检查客户环境防火墙规则，确保允许访问模型API和目标系统",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ErrorCode:
    """Namespace for accessing error definitions by attribute."""

    # Service
    PORT_IN_USE = S_PORT_IN_USE
    PYTHON_VERSION = S_PYTHON_VERSION
    IMPORT_FAILED = S_IMPORT_FAILED
    UNHANDLED_EXCEPTION = S_UNHANDLED_EXCEPTION
    THREAD_EXCEPTION = S_THREAD_EXCEPTION
    DISK_SPACE_LOW = S_DISK_SPACE_LOW
    ENV_MISSING = S_ENV_MISSING
    STARTUP_CHECK_FAILED = S_STARTUP_CHECK_FAILED
    # Credentials
    KEY_MISSING = C_KEY_MISSING
    DECRYPT_FAILED = C_DECRYPT_FAILED
    TOKEN_EXPIRED = C_TOKEN_EXPIRED
    AUTH_REJECTED = C_AUTH_REJECTED
    # LLM
    LLM_TIMEOUT = L_TIMEOUT
    LLM_RATE_LIMIT = L_RATE_LIMIT
    LLM_INVALID_RESPONSE = L_INVALID_RESPONSE
    LLM_CONNECTION_REFUSED = L_CONNECTION_REFUSED
    LLM_TOKEN_EXCEEDED = L_TOKEN_EXCEEDED
    LLM_API_KEY_INVALID = L_API_KEY_INVALID
    # Discovery
    BEHAVIOR_IR_FAILED = D_BEHAVIOR_IR_FAILED
    OBLIGATION_GEN_FAILED = D_OBLIGATION_GEN_FAILED
    CAMPAIGN_CREATE_FAILED = D_CAMPAIGN_CREATE_FAILED
    MAINLINE_CONTRACT_ERROR = D_MAINLINE_CONTRACT_ERROR
    NO_SOURCE_MATERIAL = D_NO_SOURCE_MATERIAL
    # Execution
    CONNECTION_REFUSED = X_CONNECTION_REFUSED
    TARGET_TIMEOUT = X_TIMEOUT
    BLOCKED_MISSING_ACTOR = X_BLOCKED_MISSING_ACTOR
    BLOCKED_MISSING_BINDING = X_BLOCKED_MISSING_BINDING
    BLOCKED_MISSING_OBSERVER = X_BLOCKED_MISSING_OBSERVER
    BLOCKED_CONTROL_ARM_NOT_PROVEN = X_BLOCKED_CONTROL_ARM_NOT_PROVEN
    DECLARED_INTERFACE_NOT_IMPLEMENTED = X_DECLARED_INTERFACE_NOT_IMPLEMENTED
    BLOCKED_OBSERVER_RECEIPT_INDETERMINATE = X_BLOCKED_OBSERVER_RECEIPT_INDETERMINATE
    SSL_ERROR = X_SSL_ERROR
    DNS_FAILED_TARGET = X_DNS_FAILED
    # Oracle
    ACTIVATION_FAILED = O_ACTIVATION_FAILED
    ASSERTION_ERROR = O_ASSERTION_ERROR
    # Gate
    CLEANUP_MISSING = G_CLEANUP_MISSING
    EVIDENCE_INCOMPLETE = G_EVIDENCE_INCOMPLETE
    PRODUCTION_WRITE_BLOCKED = G_PRODUCTION_WRITE_BLOCKED
    # Input
    PARSE_FAILED = I_PARSE_FAILED
    EMPTY_INPUT = I_EMPTY_INPUT
    ENCODING_ERROR = I_ENCODING_ERROR
    # Network
    DNS_FAILED = N_DNS_FAILED
    PROXY_ERROR = N_PROXY_ERROR
    FIREWALL_BLOCKED = N_FIREWALL_BLOCKED


ERRORS: dict[str, ErrorDef] = dict(_ERRORS)


def lookup(code: str) -> ErrorDef | None:
    """Look up an error definition by code string."""
    return _ERRORS.get(code)


def all_codes() -> list[ErrorDef]:
    """Return all registered error definitions."""
    return list(_ERRORS.values())


class ProductError(RuntimeError):
    """Application error carrying a structured error code."""

    def __init__(self, error_def: ErrorDef, *, detail: str = "", context: dict[str, Any] | None = None):
        self.error_def = error_def
        self.code = error_def.code
        self.severity = error_def.severity
        self.user_message = error_def.user_message
        self.operator_hint = error_def.operator_hint
        self.detail = detail
        self.context = context or {}
        super().__init__(f"[{error_def.code}] {error_def.user_message}" + (f" | {detail}" if detail else ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "user_message": self.user_message,
            "operator_hint": self.operator_hint,
            "detail": self.detail,
            "context": self.context,
        }
