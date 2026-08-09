"""Sharded scan_result store — 通用分片持久化与兼容加载（scan_result 产物膨胀治理）。

Problem
-------
一个完整扫描的 ``scan_result.json`` 会把 v12 企业级 campaign 结果（experiments /
experiment_execution / experiment_compile / 大证据）、obligation_attempt_ledger、
delivery_occurrences 等全部塞进单文件。实测 run10 端到端产物达 4GB，全量
``json.loads`` 直接 MemoryError，读 / 写 / 评分全部被阻塞。

Storage layout（分片是存储形态变化，内容与旧单文件完全一致）
-----------------------------------------------------------
::

    <output>/scan_result.json                —— 索引：小键 inline + 大键 null 占位 + 分片清单
    <output>/scan_result.parts/<path>.json   —— 每个大键一个分片文件（path 为点分键路径，
                                                如 ``v12.experiments.by_obligation``）

递归分片规则（通用，不针对任何具体键）：
  * 顶层键序列化后 >= threshold 且为 dict/list → 分片；
  * dict 值过大时先看其直接子键：任一子键 >= threshold → 递归下钻（父键保留为骨架，
    大子键各自分片）；没有任何大子键 → 该 dict 整体作为一个分片；
  * list 值过大 → 整体分片；
  * 子键数量超过 ``_CHILD_MEASURE_CAP`` 的 dict 不做逐子测量（避免数万临时文件），
    直接按整体分片决策。
  * 分片文件、索引都经过统一递归脱敏（redact_and_validate），与旧
    ``write_json_redacted`` 契约一致。

Compatibility
-------------
  * 旧单文件格式（无分片标记）自动识别，行为等同 ``json.loads``；
  * ``load_scan_result(path, keys=None)`` 全量组装；``keys=[...]`` 只组装请求的
    点分路径（流式 API），大产物消费方按需加载，不再全量入内存；
  * ``update_scan_result_index`` 支持只重写索引（新增小键）而保留分片文件，
    供读-改-写场景（如 customer_ready_snapshot 挂载）以 O(索引) 成本完成。
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .artifact_redactor import (
    ArtifactSecretLeakError,
    _find_cycle,
    redact_and_validate,
)

# 分片清单在索引文件中的标记键（顶层键，天然不会与产品键冲突）。
SHARD_MARKER = "_scan_result_shards"
SCAN_RESULT_SHARD_SCHEMA = "qualibug.scan-result-shard.v1"
SHARD_DIR_NAME = "scan_result.parts"

# 默认分片阈值：单键序列化字节数 >= 该值即分片（4 MiB）。
DEFAULT_SHARD_THRESHOLD_BYTES = 4 * 1024 * 1024

# 超过该字节数的文件先做轻量探测（直接 json.loads 会先于分片检测失败）。
_DIRECT_LOAD_LIMIT_BYTES = 256 * 1024 * 1024

# dict 直接子键数量超过该值时不做逐子测量，整体分片决策。
_CHILD_MEASURE_CAP = 1500

_ARTIFACT_REPLACE_ATTEMPTS = 4
_ARTIFACT_REPLACE_RETRY_SECONDS = 0.25

# 结构字符快速扫描（C 速度），用于大文件顶层键探测与离线转换。
_STRUCT_RE = re.compile(rb'["{}\[\],:]')
_WS = frozenset((0x20, 0x09, 0x0A, 0x0D))


# ═════════════════════════════════════════════════════════════════════════════
# 底层工具
# ═════════════════════════════════════════════════════════════════════════════

def _atomic_write_json(path: Path, payload: dict[str, Any], *, indent: int = 2) -> None:
    """原子写 JSON（临时文件 + os.replace），带重试，与现有产物写路径一致。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".q-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=indent, default=str)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        raise
    for attempt in range(_ARTIFACT_REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except PermissionError as exc:
            if attempt + 1 >= _ARTIFACT_REPLACE_ATTEMPTS:
                raise PermissionError(
                    exc.errno or 5,
                    f"{exc}; recoverable artifact retained at {temporary}",
                ) from exc
            time.sleep(_ARTIFACT_REPLACE_RETRY_SECONDS)


def _stream_dump(value: Any, target: Path, *, indent: int) -> int:
    """流式序列化 value 到文件，返回字节数（峰值内存与 json.dump 一致，不产生大字符串）。"""
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=indent, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    return target.stat().st_size


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _join_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else str(key)


def _shard_file_name(dotted_path: str) -> str:
    """点分路径 → 分片文件名（点替换为下划线；分片路径约定不含含点键名）。"""
    return dotted_path.replace(".", "_") + ".json"


def _shard_spec(dotted: str, file_path: Path, size: int) -> dict[str, Any]:
    return {
        "file": file_path.name,
        "bytes": size,
        "sha256": _sha256_file(file_path),
    }


def _temp_path(parts_dir: Path) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=parts_dir,
        prefix=".q-", suffix=".tmp", delete=False,
    ) as handle:
        handle.write("")
    return Path(handle.name)


def _promote_temp(tmp: Path, final_path: Path, temps: list[Path]) -> None:
    """临时测量文件提升为正式分片（同目录 rename，失败即爆出——绝不静默丢数据）。"""
    tmp.replace(final_path)
    if tmp in temps:
        temps.remove(tmp)


def _discard_temp(tmp: Path, temps: list[Path]) -> None:
    try:
        tmp.unlink()
    except OSError:
        pass
    finally:
        if tmp in temps:
            temps.remove(tmp)


# ═════════════════════════════════════════════════════════════════════════════
# 写入（分片持久化，保持 redaction 契约）
# ═════════════════════════════════════════════════════════════════════════════

def _whole_or_inline(
    value: Any,
    *,
    threshold_bytes: int,
    indent: int,
    parts_dir: Path,
    path: str,
    temps: list[Path],
    shards: list[tuple[str, dict[str, Any]]],
    known_size: int | None = None,
    known_tmp: Path | None = None,
) -> Any:
    """整体分片 or 内联：优先复用父级已测量的临时文件；否则流式测量。

    返回值 None 表示已整体分片（调用方以 null 占位）。
    """
    tmp = known_tmp
    size = known_size
    if tmp is None or size is None:
        tmp = _temp_path(parts_dir)
        temps.append(tmp)
        size = _stream_dump(value, tmp, indent=indent)
    if size >= threshold_bytes:
        final_path = parts_dir / _shard_file_name(path)
        _promote_temp(tmp, final_path, temps)
        shards.append((path, _shard_spec(path, final_path, size)))
        return None
    _discard_temp(tmp, temps)
    return value


def _partition(
    value: Any,
    *,
    threshold_bytes: int,
    indent: int,
    parts_dir: Path,
    path: str,
    temps: list[Path],
    shards: list[tuple[str, dict[str, Any]]],
    known_size: int | None = None,
    known_tmp: Path | None = None,
) -> Any:
    """递归分片。返回骨架值（None 表示该值已整体分片为叶分片）。

    每个候选值先流式写临时文件精确测量；>= 阈值的 dict/list 要么递归下钻
    （父键变骨架），要么整体提升为分片文件（临时文件 rename 即分片，零重复写）。
    """
    if isinstance(value, dict):
        if len(value) > _CHILD_MEASURE_CAP:
            return _whole_or_inline(
                value, threshold_bytes=threshold_bytes, indent=indent,
                parts_dir=parts_dir, path=path, temps=temps, shards=shards,
                known_size=known_size, known_tmp=known_tmp,
            )
        measured: list[tuple[Any, Any, Path, int]] = []
        any_big = False
        try:
            for key, child in value.items():
                tmp = _temp_path(parts_dir)
                temps.append(tmp)
                size = _stream_dump(child, tmp, indent=indent)
                measured.append((key, child, tmp, size))
                if size >= threshold_bytes and isinstance(child, (dict, list)):
                    any_big = True
            if any_big:
                inline: dict[str, Any] = {}
                for key, child, tmp, size in measured:
                    if size >= threshold_bytes and isinstance(child, (dict, list)):
                        sub_inline = _partition(
                            child, threshold_bytes=threshold_bytes, indent=indent,
                            parts_dir=parts_dir, path=_join_path(path, key),
                            temps=temps, shards=shards,
                            known_size=size, known_tmp=tmp,
                        )
                        # sub_inline is None ⇔ 该子键整体分片 → null 占位；
                        # 否则是骨架 dict（其大子键已分片）→ 保留骨架。
                        inline[key] = sub_inline if sub_inline is not None else None
                        if tmp.exists():
                            _discard_temp(tmp, temps)
                    else:
                        inline[key] = child
                        _discard_temp(tmp, temps)
                return inline
            # 无大子键
            if known_size is not None and known_size >= threshold_bytes:
                # 父级已精确测量且足够大 → 复用父级临时文件整体分片
                final_path = parts_dir / _shard_file_name(path)
                _promote_temp(known_tmp, final_path, temps)
                shards.append((path, _shard_spec(path, final_path, known_size)))
                return None
            total = sum(size for _, _, _, size in measured)
            for _, _, tmp, _ in measured:
                _discard_temp(tmp, temps)
            if total >= threshold_bytes:
                return _whole_or_inline(
                    value, threshold_bytes=threshold_bytes, indent=indent,
                    parts_dir=parts_dir, path=path, temps=temps, shards=shards,
                )
            return value
        finally:
            for _, _, tmp, _ in measured:
                if tmp.exists():
                    _discard_temp(tmp, temps)
    if isinstance(value, list):
        return _whole_or_inline(
            value, threshold_bytes=threshold_bytes, indent=indent,
            parts_dir=parts_dir, path=path, temps=temps, shards=shards,
            known_size=known_size, known_tmp=known_tmp,
        )
    # 标量永不分片
    if known_tmp is not None:
        _discard_temp(known_tmp, temps)
    return value


def write_scan_result(
    path: Path | str,
    result: dict[str, Any],
    *,
    threshold_bytes: int = DEFAULT_SHARD_THRESHOLD_BYTES,
    indent: int = 2,
    post_redaction_validator: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """分片持久化 scan_result（内容 == 旧单文件，redaction 契约不变）。

    返回 redaction receipt（与 ``write_json_redacted`` 一致）。非 dict 载荷回退为
    旧单文件写路径（scan_result 产品契约恒为 dict）。
    """
    target = Path(path)
    if not isinstance(result, dict):
        from .artifact_redactor import write_json_redacted

        return write_json_redacted(target, result, indent=indent, post_redaction_validator=post_redaction_validator)
    cycle_path = _find_cycle(result)
    if cycle_path:
        raise ArtifactSecretLeakError(
            f"artifact payload is not serializable (cycle at {cycle_path})",
            scan_result={"cycle_path": cycle_path},
        )
    redacted, receipt = redact_and_validate(result)
    if post_redaction_validator is not None:
        post_redaction_validator(redacted)
    parts_dir = target.parent / SHARD_DIR_NAME
    parts_dir.mkdir(parents=True, exist_ok=True)
    temps: list[Path] = []
    shards: list[tuple[str, dict[str, Any]]] = []
    try:
        skeleton = _partition(
            redacted,
            threshold_bytes=threshold_bytes,
            indent=indent,
            parts_dir=parts_dir,
            path="",
            temps=temps,
            shards=shards,
        )
        if skeleton is None:
            raise ArtifactSecretLeakError(
                "scan_result root must not be sharded as a whole value",
                scan_result={"code": "scan_result_root_sharded"},
            )
    finally:
        for tmp in list(temps):
            _discard_temp(tmp, temps)
    manifest: dict[str, Any] = {
        "schema_version": SCAN_RESULT_SHARD_SCHEMA,
        "threshold_bytes": threshold_bytes,
        "indent": indent,
        "shards": {dotted: spec for dotted, spec in shards},
    }
    skeleton[SHARD_MARKER] = manifest
    _atomic_write_json(target, skeleton, indent=indent)
    return receipt


# ═════════════════════════════════════════════════════════════════════════════
# 读取（兼容加载：自动识别分片 / 旧单文件；keys 流式 API）
# ═════════════════════════════════════════════════════════════════════════════

def is_sharded_scan_result(path: Path | str) -> bool:
    """文件是否为分片 store 索引。旧单文件（含 4GB 级）返回 False。

    小文件直接 json.loads 判定；大文件先做 C 速度字面量探测，命中后再做
    顶层键扫描确认（避免把数据字符串里的巧合字面量误判为分片标记）。
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return False
    if size <= _DIRECT_LOAD_LIMIT_BYTES:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and isinstance(payload.get(SHARD_MARKER), dict)
    marker = ('"' + SHARD_MARKER + '"').encode("utf-8")
    with open(target, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        if mm.find(marker) < 0:
            return False
        keys = _fast_top_level_keys(mm)
        return SHARD_MARKER in keys


def _fast_top_level_keys(mm: mmap.mmap) -> set[str]:
    """C 速度扫描顶层键（用于大文件分片标记确认 / 离线转换工具）。"""
    n = len(mm)
    keys: set[str] = set()
    depth = 0
    key: str | None = None
    pos = 0
    while True:
        m = _STRUCT_RE.search(mm, pos)
        if m is None:
            break
        idx = m.start()
        c = mm[idx]
        if c == 0x22:  # quote
            end = _skip_string(mm, idx, n)
            if depth == 1 and key is None:
                j = end
                while j < n and mm[j] in _WS:
                    j += 1
                if j < n and mm[j] == 0x3A:
                    key = mm[idx + 1:end - 1].decode("utf-8", errors="replace")
                    pos = j + 1
                    continue
            pos = end
            continue
        if c in (0x7B, 0x5B):
            depth += 1
        elif c in (0x7D, 0x5D):
            if depth == 1 and key is not None:
                key = None
            depth -= 1
        elif c == 0x2C and depth == 1:
            if key is not None:
                keys.add(key)
                key = None
        pos = idx + 1
    if key is not None:
        keys.add(key)
    return keys


def _skip_string(mm: mmap.mmap, start: int, end: int) -> int:
    j = mm.find(b'"', start + 1)
    while j >= 0 and j < end:
        bs = 0
        k = j - 1
        while k >= start and mm[k] == 0x5C:
            bs += 1
            k -= 1
        if bs % 2 == 0:
            return j + 1
        j = mm.find(b'"', j + 1)
    return end


def _load_shard_value(parts_dir: Path, spec: dict[str, Any], dotted: str) -> Any:
    file_path = parts_dir / str(spec.get("file") or "")
    if not file_path.is_file():
        raise FileNotFoundError(
            f"scan_result shard missing: {dotted} -> {file_path}"
        )
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"scan_result shard unreadable: {dotted} -> {file_path}: {type(exc).__name__}"
        ) from exc


def _descend_and_fill(
    skeleton: dict[str, Any],
    shards: dict[str, dict[str, Any]],
    parts_dir: Path,
    dotted: str,
    *,
    strict: bool = False,
) -> Any:
    """按点分路径下钻骨架；遇到 null 占位时加载对应分片并继续下钻。返回目标节点。

    ``strict=False``（默认）：请求路径在 store 中不存在时返回 None（消费方按
    ``.get()`` 语义容错）；``strict=True``：缺失即爆出（完整性断言用）。
    分片清单存在但分片文件缺失时无论何种模式都 fail-loud。
    """
    parts = dotted.split(".")
    node: Any = skeleton
    for i, part in enumerate(parts):
        if not isinstance(node, dict):
            if strict:
                raise ValueError(f"scan_result shard path invalid: {dotted}")
            return None
        if part not in node:
            if strict:
                raise KeyError(f"scan_result key missing: {dotted}")
            return None
        value = node[part]
        if value is None:
            prefix = ".".join(parts[: i + 1])
            spec = shards.get(prefix)
            if spec is None:
                raise FileNotFoundError(f"scan_result shard spec missing: {prefix}")
            value = _load_shard_value(parts_dir, spec, prefix)
            node[part] = value
        node = value
    if node is None:
        raise FileNotFoundError(f"scan_result shard unresolved: {dotted}")
    return node


def _assemble(
    payload: dict[str, Any],
    target: Path,
    keys: Iterable[str] | None,
    parts_dir: Path,
) -> dict[str, Any]:
    """从索引载荷组装：keys=None 全量；keys 为点分路径列表（精确路径语义）。

    精确路径语义：下钻骨架，遇整体分片占位则加载该分片并继续下钻；请求路径
    指向骨架时返回骨架本身（其子分片不自动加载，避免误载 3.6GB 级子树）。
    """
    manifest = payload.get(SHARD_MARKER)
    if not isinstance(manifest, dict):
        return payload  # 旧单文件（或任意普通 JSON 对象）
    skeleton = {key: value for key, value in payload.items() if key != SHARD_MARKER}
    shards = manifest.get("shards") if isinstance(manifest.get("shards"), dict) else {}
    if keys is None:
        for dotted in shards:
            _descend_and_fill(skeleton, shards, parts_dir, dotted)
    else:
        for dotted in keys:
            if isinstance(dotted, str) and dotted:
                _descend_and_fill(skeleton, shards, parts_dir, dotted)
    return skeleton

def load_scan_result(path: Path | str, *, keys: Iterable[str] | None = None) -> dict[str, Any]:
    """加载 scan_result：自动识别分片 store / 旧单文件。

    ``keys=None`` 组装全量（旧格式等同 json.loads）。``keys=[...]`` 为流式 API：
    只加载请求的点分路径（如 ``["findings", "candidate_findings"]`` 或
    ``["v12.adaptive_planning_history_receipt"]``），其余分片保持 null 占位。
    请求路径命中骨架时，其整个子树（子分片）一并组装。
    旧单文件格式忽略 keys（必须整体解析），小文件行为与 json.loads 一致。
    """
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        size = target.stat().st_size
    except OSError:
        return {}
    if size <= _DIRECT_LOAD_LIMIT_BYTES:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"scan_result must be an object: {target}")
        return _assemble(payload, target, keys, target.parent / SHARD_DIR_NAME)
    # 大文件：先探测是否分片索引
    if is_sharded_scan_result(target):
        payload = json.loads(target.read_text(encoding="utf-8"))
        return _assemble(payload, target, keys, target.parent / SHARD_DIR_NAME)
    # 旧单文件大产物：与历史行为一致（json.loads；过大时 MemoryError 即历史问题）
    return json.loads(target.read_text(encoding="utf-8"))


def shard_specs(path: Path | str) -> dict[str, dict[str, Any]]:
    """读取分片清单（path → spec）。非分片 store 返回空 dict。"""
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    manifest = payload.get(SHARD_MARKER)
    if not isinstance(manifest, dict):
        return {}
    shards = manifest.get("shards")
    return shards if isinstance(shards, dict) else {}


def verify_scan_result_store(path: Path | str, *, check_sha256: bool = False) -> dict[str, Any]:
    """验证分片 store 完整性：清单 vs 实际文件（大小，可选 sha256）。

    用于证据完整性契约的机器检查：每个分片文件存在、字节数与清单一致、
    （可选）sha256 与清单一致。
    """
    target = Path(path)
    if not target.is_file():
        return {"valid": False, "issues": ["index_missing"]}
    if not is_sharded_scan_result(target):
        return {"valid": True, "legacy": True, "issues": []}
    specs = shard_specs(target)
    parts_dir = target.parent / SHARD_DIR_NAME
    issues: list[str] = []
    total_bytes = 0
    for dotted, spec in sorted(specs.items()):
        file_path = parts_dir / str(spec.get("file") or "")
        if not file_path.is_file():
            issues.append(f"shard_missing:{dotted}")
            continue
        size = file_path.stat().st_size
        if int(spec.get("bytes") or -1) != size:
            issues.append(
                f"shard_size_mismatch:{dotted}:manifest={spec.get('bytes')} actual={size}"
            )
        total_bytes += size
        if check_sha256:
            expected = str(spec.get("sha256") or "")
            if expected and _sha256_file(file_path) != expected:
                issues.append(f"shard_sha256_mismatch:{dotted}")
    return {
        "valid": not issues,
        "legacy": False,
        "shard_count": len(specs),
        "shard_total_bytes": total_bytes,
        "issues": issues,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 离线转换（mmap 流式：把旧 4GB 级单文件转成分片 store，不整读入内存）
# ═════════════════════════════════════════════════════════════════════════════

def _scan_region_children(
    mm: mmap.mmap,
    open_idx: int,
    close_idx: int,
) -> list[tuple[str, int, int, str]]:
    """扫描 dict 区域 [open_idx, close_idx] 的直接子键，返回 (key, vstart, vend, kind)。

    vend 为值结束后的第一个字节（容器含闭合括号；标量/字符串为末字符后）。
    """
    n = len(mm)
    entries: list[tuple[str, int, int, str]] = []
    pos = open_idx + 1
    depth = 0
    key: str | None = None
    key_val_start = 0
    while True:
        m = _STRUCT_RE.search(mm, pos, close_idx + 1)
        if m is None:
            break
        idx = m.start()
        c = mm[idx]
        if c == 0x22:  # quote
            s_end = _skip_string(mm, idx, n)
            if depth == 0 and key is None:
                j = s_end
                while j < n and mm[j] in _WS:
                    j += 1
                if j < n and mm[j] == 0x3A:
                    key = mm[idx + 1:s_end - 1].decode("utf-8", errors="replace")
                    key_val_start = j + 1
                    pos = j + 1
                    continue
            if depth == 0 and key is not None:
                entries.append((key, idx, s_end, "str"))
                key = None
                pos = s_end
                continue
            pos = s_end
            continue
        if c in (0x7B, 0x5B):
            if depth == 0 and key is not None:
                entries.append((key, idx, None, "dict" if c == 0x7B else "list"))
                key = None
            depth += 1
            pos = idx + 1
            continue
        if c in (0x7D, 0x5D):
            if depth == 1 and key is None and entries and entries[-1][2] is None:
                kk, vs, _, kt = entries[-1]
                entries[-1] = (kk, vs, idx + 1, kt)
            elif depth == 0 and key is not None:
                entries.append((key, key_val_start, idx, "scalar"))
                key = None
            depth -= 1
            pos = idx + 1
            continue
        if c == 0x2C and depth == 0:
            if key is not None:
                entries.append((key, key_val_start, idx, "scalar"))
                key = None
            pos = idx + 1
            continue
        pos = idx + 1
    return [(k, s, e, t) for (k, s, e, t) in entries if e is not None]


def _partition_region(
    mm: mmap.mmap,
    open_idx: int,
    close_idx: int,
    *,
    threshold_bytes: int,
    path: str,
    shards: list[tuple[str, int, int]],
) -> Any:
    """mmap 区域递归分片（与内存版 ``_partition`` 决策一致）。返回骨架（None=整体分片）。"""
    children = _scan_region_children(mm, open_idx, close_idx)
    big_children = [
        (key, vs, ve, kind)
        for (key, vs, ve, kind) in children
        if (ve - vs) >= threshold_bytes and kind in ("dict", "list")
    ]
    if not big_children:
        region_size = close_idx - open_idx + 1
        if region_size >= threshold_bytes:
            shards.append((path, open_idx, close_idx + 1))
            return None
        inline: dict[str, Any] = {}
        for key, vs, ve, _kind in children:
            inline[key] = json.loads(bytes(mm[vs:ve]).decode("utf-8"))
        return inline
    inline = {}
    for key, vs, ve, kind in children:
        size = ve - vs
        if size >= threshold_bytes and kind in ("dict", "list"):
            if kind == "dict":
                sub_inline = _partition_region(
                    mm, vs, ve - 1,
                    threshold_bytes=threshold_bytes,
                    path=_join_path(path, key),
                    shards=shards,
                )
                inline[key] = sub_inline if sub_inline is not None else None
            else:
                inline[key] = None
                shards.append((_join_path(path, key), vs, ve))
        else:
            inline[key] = json.loads(bytes(mm[vs:ve]).decode("utf-8"))
    return inline


def _copy_span(src: Path, vstart: int, vend: int, dest: Path) -> int:
    """按字节复制源文件值区间到分片文件（内容与旧文件逐字节一致）。"""
    size = vend - vstart
    with open(src, "rb") as handle:
        handle.seek(vstart)
        with open(dest, "wb") as out:
            remaining = size
            while remaining > 0:
                block = handle.read(min(4 * 1024 * 1024, remaining))
                if not block:
                    break
                out.write(block)
                remaining -= len(block)
    return size


def shard_legacy_scan_result(
    path: Path | str,
    *,
    threshold_bytes: int = DEFAULT_SHARD_THRESHOLD_BYTES,
    indent: int = 2,
    keep_legacy: bool = True,
) -> dict[str, Any]:
    """把旧单文件 scan_result 流式转换为分片 store（mmap，不整读入内存）。

    分片文件为原文件值区间的逐字节拷贝；索引中的 inline 小键经解析后重新序列化
    （内容一致，仅空白差异）。转换完成后 scan_result.json 为索引（小文件），
    原文件保留为 ``scan_result.json.legacy``（keep_legacy=True 时）。
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"scan_result missing: {target}")
    if is_sharded_scan_result(target):
        return {"status": "already_sharded", "path": str(target)}
    parts_dir = target.parent / SHARD_DIR_NAME
    parts_dir.mkdir(parents=True, exist_ok=True)
    shards: list[tuple[str, int, int]] = []
    with open(target, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        n = len(mm)
        pos = 0
        while pos < n and (mm[pos] in _WS or mm[pos] in (0xEF, 0xBB, 0xBF)):
            pos += 1
        if pos >= n or mm[pos] != 0x7B:
            raise ValueError(f"legacy scan_result root must be an object: {target}")
        root_open = pos
        # 根闭合括号 = 最后一个非空白字符
        root_close = n - 1
        while root_close > root_open and mm[root_close] in _WS:
            root_close -= 1
        if mm[root_close] != 0x7D:
            raise ValueError(f"legacy scan_result root unclosed: {target}")
        skeleton = _partition_region(
            mm, root_open, root_close,
            threshold_bytes=threshold_bytes, path="", shards=shards,
        )
    if skeleton is None:
        raise ValueError("legacy scan_result root must not be sharded as a whole value")
    manifest_shards: dict[str, dict[str, Any]] = {}
    for dotted, vstart, vend in shards:
        dest = parts_dir / _shard_file_name(dotted)
        size = _copy_span(target, vstart, vend, dest)
        manifest_shards[dotted] = _shard_spec(dotted, dest, size)
    manifest = {
        "schema_version": SCAN_RESULT_SHARD_SCHEMA,
        "threshold_bytes": threshold_bytes,
        "indent": indent,
        "shards": manifest_shards,
    }
    skeleton[SHARD_MARKER] = manifest
    if keep_legacy:
        legacy_path = target.with_name(target.name + ".legacy")
        os.replace(target, legacy_path)
    _atomic_write_json(target, skeleton, indent=indent)
    return {
        "status": "sharded",
        "path": str(target),
        "legacy_backup": str(target.with_name(target.name + ".legacy")) if keep_legacy else "",
        "shard_count": len(manifest_shards),
        "shard_total_bytes": sum(spec["bytes"] for spec in manifest_shards.values()),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 索引更新（读-改-写场景：只重写索引，分片文件不动）
# ═════════════════════════════════════════════════════════════════════════════

def update_scan_result_index(
    path: Path | str,
    updates: dict[str, Any],
    *,
    indent: int = 2,
) -> dict[str, Any]:
    """向分片 store 索引新增/覆盖小键（如 customer_ready_snapshot），分片不变。

    新增值单独走统一脱敏（redact_and_validate）后写入索引，原子替换索引文件。
    对旧单文件（未分片）回退为整体 ``write_scan_result``（内容不变，只改变存储形态）。
    返回 redaction receipt。
    """
    target = Path(path)
    if not updates:
        return {}
    if not target.is_file():
        raise FileNotFoundError(f"scan_result missing: {target}")
    if is_sharded_scan_result(target):
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"scan_result index must be an object: {target}")
        redacted, receipt = redact_and_validate(dict(updates))
        for key, value in redacted.items():
            payload[key] = value
        _atomic_write_json(target, payload, indent=indent)
        return receipt
    # 旧单文件：整体加载后走统一写路径（旧文件通常小；大文件与历史行为一致受限）
    payload = load_scan_result(target)
    payload.update(dict(updates))
    return write_scan_result(target, payload, indent=indent)


__all__ = [
    "SHARD_MARKER",
    "SCAN_RESULT_SHARD_SCHEMA",
    "SHARD_DIR_NAME",
    "DEFAULT_SHARD_THRESHOLD_BYTES",
    "is_sharded_scan_result",
    "load_scan_result",
    "shard_legacy_scan_result",
    "shard_specs",
    "update_scan_result_index",
    "verify_scan_result_store",
    "write_scan_result",
]
