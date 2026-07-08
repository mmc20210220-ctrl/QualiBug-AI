"""主链 7 证据服务：/api/evidence/artifact 端点安全与正确性回归。

覆盖：正常返回图片字节 + 正确 MIME、路径穿越拦截、子树越界拦截、
类型白名单拦截、缺参与不存在文件的错误码。
"""
import os
import sys
from pathlib import Path

# private_pilot_service 在导入期校验 JWT 密钥，单测需预置（仅开发占位值）
os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.private_pilot_service import PrivatePilotHandler  # noqa: E402


class _FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)


class _StubHandler:
    """最小 handler 桩：只实现 _handle_evidence_artifact 依赖的响应原语。"""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.wfile = _FakeWriter()
        self.json_payloads: list[tuple[object, int]] = []

    # 复用真实实现
    _handle_evidence_artifact = PrivatePilotHandler._handle_evidence_artifact

    def _json(self, body, status: int = 200, extra_headers=None) -> None:
        self.json_payloads.append((body, status))

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def end_headers(self) -> None:
        pass


def _make_artifact(tmp_path: Path, project: str, name: str, data: bytes) -> str:
    art_dir = tmp_path / "platform_workspace" / project / "browser_runs" / "run1"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / name).write_bytes(data)
    return f"platform_workspace/{project}/browser_runs/run1/{name}"


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_serves_png_artifact_with_correct_mime(tmp_path):
    ref = _make_artifact(tmp_path, "demo", "final.png", PNG_BYTES)
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", ref, tmp_path)
    assert handler.status == 200
    assert handler.headers["Content-Type"] == "image/png"
    assert handler.headers["X-Content-Type-Options"] == "nosniff"
    assert b"".join(handler.wfile.chunks) == PNG_BYTES
    assert not handler.json_payloads  # 成功路径不走 json 错误


def test_serves_har_as_json(tmp_path):
    ref = _make_artifact(tmp_path, "demo", "session.har", b'{"log": {}}')
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", ref, tmp_path)
    assert handler.status == 200
    assert handler.headers["Content-Type"] == "application/json"


def test_missing_ref_returns_400(tmp_path):
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", "", tmp_path)
    assert handler.json_payloads[-1][1] == 400
    assert handler.json_payloads[-1][0]["error"] == "MISSING_ARTIFACT_REF"


def test_path_traversal_is_blocked(tmp_path):
    # 在 root 外放一个敏感文件，尝试用 ../ 穿越读取
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(b"TOPSECRET")
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", "../secret.png", tmp_path)
    assert handler.json_payloads, "穿越请求必须被拦截为 json 错误"
    assert handler.json_payloads[-1][1] in (400, 403)
    assert handler.status != 200


def test_outside_browser_runs_subtree_is_blocked(tmp_path):
    # 合法存在但不在 browser_runs 子树内的文件必须被拒
    other = tmp_path / "platform_workspace" / "demo" / "input" / "leak.png"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_bytes(PNG_BYTES)
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", "platform_workspace/demo/input/leak.png", tmp_path)
    assert handler.json_payloads[-1][1] == 403
    assert handler.json_payloads[-1][0]["error"] == "ARTIFACT_OUTSIDE_ALLOWED_SUBTREE"


def test_blocked_extension_is_rejected(tmp_path):
    ref = _make_artifact(tmp_path, "demo", "evil.exe", b"MZ")
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", ref, tmp_path)
    assert handler.json_payloads[-1][1] == 415
    assert handler.json_payloads[-1][0]["error"] == "ARTIFACT_TYPE_BLOCKED"


def test_missing_file_returns_404(tmp_path):
    (tmp_path / "platform_workspace" / "demo" / "browser_runs").mkdir(parents=True, exist_ok=True)
    handler = _StubHandler()
    handler._handle_evidence_artifact("demo", "platform_workspace/demo/browser_runs/nope.png", tmp_path)
    assert handler.json_payloads[-1][1] == 404
