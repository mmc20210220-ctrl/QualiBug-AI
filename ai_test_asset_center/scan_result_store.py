"""Sharded scan_result store — 分片先行、逐片脱敏 + 对象树引用化（P0-3）。

Problem
-------
一个完整扫描的 ``scan_result.json`` 会把 v12 企业级 campaign 结果（experiments /
experiment_execution / experiment_compile / 大证据）、obligation_attempt_ledger、
delivery_occurrences 等全部塞进单文件。实测 run16 端到端产物达 4GB
（4,015,228,861 B；v12 占 90.1%，其中 experiments 48.6%、experiment_execution
19.4%、experiment_compile 18.4%）。两个独立瓶颈：

  1. 4GB 根因：同一对象树被嵌套复制多处（Experiment 同时出现在
     ``v12.experiments.by_obligation`` / ``experiments`` / ``all_experiments`` /
     ``experiment_compile.*``；Execution Result 内嵌完整 Experiment/Evidence/
     Finding；Attempt Ledger 内嵌完整 Execution Receipt/Gate/Finding；
     Delivery Occurrence 内嵌完整 Finding/Gate/Evidence）。
  2. redact 性能墙：旧写入流程先对整体 4GB 树做 ``redact_and_validate``
     （deepcopy + 逐字段正则 + reseal + authority 重建 + 全量 scan）再分片，
     实测卡 1 小时+。

修复（都是存储形态变化，执行/交付语义与消费者可见内容不变）：

A. redact 分片化：先分片、逐片脱敏。redact 逐字段，分片后逐片 == 整体；
   peak 内存从"整树 deepcopy + 整树脱敏副本"降为"单片"。完整性校验不变：
   每个分片与骨架都经过 redact_artifact + _reseal_attempt_ledgers +
   scan_for_secrets，authority 指纹链在重建步骤中保持自洽（fail-closed）。

B. 对象树引用化：写入时对树做 in-place 规范化（在 deepcopy 副本上），
   每个 >= dedup floor 的子树只保留一份在 ``_artifact_registry``
   （operations_by_id / experiments_by_id / executions_by_id /
   evidence_by_id / findings_by_id / gate_receipts_by_id / …），树中全部变为
   轻量引用标记；大响应体/证据字符串进 sha256-addressed blob。
   密封 ledger（obligation-attempt-ledger）内部排除去重：其指纹契约要求
   持久化内容完整。加载时 ``hydrate_refs`` 还原完整对象树，消费者看到的内容
   与旧格式逐字节等价；canonical_defect_id / finding_id 等身份不变。

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
  * 大标量子键（如 >= 4MiB 的 blob 字符串）也整体分片；
  * 密封 ledger（schema_version == qualibug.obligation-attempt-ledger.v1）作为
    原子单元整体分片（attempt 指纹封印契约要求内容完整、一次脱敏/重新封印）；
  * list 值过大 → 整体分片；
  * 子键数量超过 ``_CHILD_MEASURE_CAP`` 的 dict 不做逐子测量（避免数万临时文件），
    直接按整体分片决策。
  * 分片文件、索引都经过统一递归脱敏（redact_and_validate），与旧
    ``write_json_redacted`` 契约一致；大分片逐个脱敏，不再整树脱敏。

Compatibility
-------------
  * 旧单文件格式（无分片标记）自动识别，行为等同 ``json.loads``；
  * 旧分片 store（无 ``_artifact_registry``）自动识别，行为与 task-20 一致；
  * 规范化 store 全量加载（``keys=None``）时自动 hydrate 引用并移除 registry
    段 —— 返回树与旧格式等价；``keys=[...]`` 只组装+hydrate 请求的路径；
  * ``update_scan_result_index`` 支持只重写索引（新增小键）而保留分片文件，
    供读-改-写场景（如 customer_ready_snapshot 挂载）以 O(索引) 成本完成。
"""
from __future__ import annotations

import copy
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
    _rederive_redaction_sensitive_authority,
    _reseal_attempt_ledgers,
    redact_and_validate,
    redact_artifact,
    scan_for_secrets,
)
from .scan_result_normalizer import (
    BLOB_KEY,
    DEFAULT_BLOB_THRESHOLD_BYTES,
    DEFAULT_DEDUP_THRESHOLD_BYTES,
    NORMALIZED_SCHEMA,
    REF_KEY,
    REGISTRY_KEY,
    blob_to_dotted,
    hydrate_refs,
    is_blob_marker,
    is_ref_marker,
    normalize_scan_result,
    ref_to_dotted,
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

# 标量子键独立分片下限：字符串（如 blob）>= 该值才整体分片；小于该值内联进骨架。
# 与容器分片阈值（threshold_bytes）解耦——小阈值测试下字符串保持内联，
# 生产环境大响应体仍能分片出索引。
_SCALAR_PIECE_MIN_BYTES = 1024 * 1024

_ARTIFACT_REPLACE_ATTEMPTS = 4
_ARTIFACT_REPLACE_RETRY_SECONDS = 0.25

# 结构字符快速扫描（C 速度），用于大文件顶层键探测与离线转换。
_STRUCT_RE = re.compile(rb'["{}\[\],:]')
_WS = frozenset((0x20, 0x09, 0x0A, 0x0D))

# 密封 ledger：整体原子分片（指纹封印契约；attempt 内容必须完整脱敏/重新封印）。
_LEDGER_SCHEMA = "qualibug.obligation-attempt-ledger.v1"

# authority 重建所需的 envelope 键（root 与 v12 两个 scope）。
_AUTHORITY_SCOPE_KEYS = (
    "obligation_attempt_ledger",
    "delivery_occurrences",
    "canonical_defect_registry",
    "mainline_run",
    "formal_delivery_authority",
)


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
    pieces: list[tuple[str, Path, int]],
    known_size: int | None = None,
    known_tmp: Path | None = None,
) -> Any:
    """整体分片 or 内联：优先复用父级已测量的临时文件；否则流式测量。

    返回值 None 表示已整体分片（调用方以 null 占位；临时文件移交调用方，
    脱敏后写为最终分片文件）。标量（含大字符串）同样适用。
    """
    tmp = known_tmp
    size = known_size
    if tmp is None or size is None:
        tmp = _temp_path(parts_dir)
        temps.append(tmp)
        size = _stream_dump(value, tmp, indent=indent)
    if size >= threshold_bytes:
        if tmp in temps:
            temps.remove(tmp)
        pieces.append((path, tmp, size))
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
    pieces: list[tuple[str, Path, int]],
    known_size: int | None = None,
    known_tmp: Path | None = None,
) -> Any:
    """递归分片。返回骨架值（None 表示该值已整体分片为叶分片）。

    每个候选值先流式写临时文件精确测量；>= 阈值的 dict/list/大标量要么递归
    下钻（父键变骨架），要么整体记入 ``pieces``（临时文件移交调用方逐片脱敏，
    零重复写）。密封 ledger 作为原子单元整体分片。
    """
    if isinstance(value, dict):
        if value.get("schema_version") == _LEDGER_SCHEMA:
            # 密封 ledger：attempt 指纹封印契约要求内容完整。整体作为单片，
            # 逐片脱敏时一次完成 redact + reseal + scan，指纹链保持自洽。
            return _whole_or_inline(
                value, threshold_bytes=threshold_bytes, indent=indent,
                parts_dir=parts_dir, path=path, temps=temps, pieces=pieces,
                known_size=known_size, known_tmp=known_tmp,
            )
        if len(value) > _CHILD_MEASURE_CAP:
            return _whole_or_inline(
                value, threshold_bytes=threshold_bytes, indent=indent,
                parts_dir=parts_dir, path=path, temps=temps, pieces=pieces,
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
                            temps=temps, pieces=pieces,
                            known_size=size, known_tmp=tmp,
                        )
                        # sub_inline is None ⇔ 该子键整体分片 → null 占位；
                        # 否则是骨架 dict（其大子键已分片）→ 保留骨架。
                        inline[key] = sub_inline if sub_inline is not None else None
                        if tmp in temps and tmp.exists():
                            _discard_temp(tmp, temps)
                    elif size >= max(threshold_bytes, _SCALAR_PIECE_MIN_BYTES):
                        # 大标量子键（如 >= 4MiB 的 blob 字符串）整体分片。
                        if tmp in temps:
                            temps.remove(tmp)
                        pieces.append((_join_path(path, key), tmp, size))
                        inline[key] = None
                    else:
                        inline[key] = child
                        _discard_temp(tmp, temps)
                return inline
            # 无大子键
            if known_size is not None and known_size >= threshold_bytes:
                # 父级已精确测量且足够大 → 复用父级临时文件整体分片
                if known_tmp in temps:
                    temps.remove(known_tmp)
                pieces.append((path, known_tmp, known_size))
                return None
            total = sum(size for _, _, _, size in measured)
            for _, _, tmp, _ in measured:
                _discard_temp(tmp, temps)
            if total >= threshold_bytes:
                return _whole_or_inline(
                    value, threshold_bytes=threshold_bytes, indent=indent,
                    parts_dir=parts_dir, path=path, temps=temps, pieces=pieces,
                )
            return value
        finally:
            for _, _, tmp, _ in measured:
                if tmp in temps and tmp.exists():
                    _discard_temp(tmp, temps)
    if isinstance(value, list):
        return _whole_or_inline(
            value, threshold_bytes=threshold_bytes, indent=indent,
            parts_dir=parts_dir, path=path, temps=temps, pieces=pieces,
            known_size=known_size, known_tmp=known_tmp,
        )
    # 标量（含大字符串）：>= 阈值整体分片，否则内联
    return _whole_or_inline(
        value, threshold_bytes=threshold_bytes, indent=indent,
        parts_dir=parts_dir, path=path, temps=temps, pieces=pieces,
        known_size=known_size, known_tmp=known_tmp,
    )


def write_scan_result(
    path: Path | str,
    result: dict[str, Any],
    *,
    threshold_bytes: int = DEFAULT_SHARD_THRESHOLD_BYTES,
    indent: int = 2,
    post_redaction_validator: Callable[[Any], None] | None = None,
    normalize: bool = True,
    dedup_threshold_bytes: int = DEFAULT_DEDUP_THRESHOLD_BYTES,
    blob_threshold_bytes: int = DEFAULT_BLOB_THRESHOLD_BYTES,
) -> dict[str, Any]:
    """分片持久化 scan_result（内容 == 旧单文件，redaction 契约不变）。

    P0-3 写入流程（从根因修复 4GB + redact 性能墙）：
      1. ``_find_cycle`` 预检；
      2. 规范化（可选，默认开）：在 deepcopy 副本上把 >= dedup floor 的子树
         收进 ``_artifact_registry``（by-id maps / content hash / blob），
         树中只留引用标记 —— 调用方树不被改动，同一子树只持久化一次；
      3. 分片先行：``_partition`` 只测量+切分（临时文件），不做整体脱敏；
      4. 逐片脱敏：每个分片独立 ``redact_artifact`` + ``_reseal_attempt_ledgers``
         + ``scan_for_secrets``（fail-closed），peak 内存 = 单片；
      5. 骨架整体脱敏（``redact_and_validate``，内联场景的 reseal/re-derive
         在这里完成）；
      6. authority 重建：被分片切开的 envelope 键（ledger/occurrences）从
         已脱敏分片加载并 hydrate 引用后执行 ``_rederive_redaction_sensitive_authority``，
         重建产物就地写回骨架并单独 redact+scan，然后所有分片占位还原；
      7. 原子写索引（含分片清单）。

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
    registry_stats: dict[str, int] | None = None
    if normalize:
        # 规范化在 deepcopy 副本上进行：调用方在 write 之后仍会继续读写 result
        # （__main__ / scan_execution_outcome 的 customer-ready 静态产物、
        #  job_formal_planning_proof 的返回值），副本保证其看到的树不被改动。
        work = copy.deepcopy(result)
        registry = normalize_scan_result(
            work,
            dedup_threshold_bytes=dedup_threshold_bytes,
            blob_threshold_bytes=blob_threshold_bytes,
        )
        if not registry.is_empty():
            work[REGISTRY_KEY] = registry.as_payload()
        registry_stats = dict(registry.stats)
    else:
        work = result
    parts_dir = target.parent / SHARD_DIR_NAME
    parts_dir.mkdir(parents=True, exist_ok=True)
    temps: list[Path] = []
    pieces: list[tuple[str, Path, int]] = []
    try:
        skeleton = _partition(
            work,
            threshold_bytes=threshold_bytes,
            indent=indent,
            parts_dir=parts_dir,
            path="",
            temps=temps,
            pieces=pieces,
        )
        if skeleton is None:
            raise ArtifactSecretLeakError(
                "scan_result root must not be sharded as a whole value",
                scan_result={"code": "scan_result_root_sharded"},
            )
    finally:
        for tmp in list(temps):
            _discard_temp(tmp, temps)
    # ── 逐片脱敏（A）：分片先行、逐片 redact，完整性校验逐片 fail-closed ──
    manifest_shards: dict[str, dict[str, Any]] = {}
    piece_events: list[dict[str, Any]] = []
    piece_scans: list[dict[str, Any]] = []
    for dotted, tmp, raw_size in pieces:
        try:
            with open(tmp, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        redacted, piece_receipt = redact_artifact(value)
        # 密封 ledger 分片在这里完成 reseal（attempt/ledger 指纹链自洽）。
        redacted = _reseal_attempt_ledgers(redacted)
        piece_scan = scan_for_secrets(redacted)
        if not piece_scan.get("safe"):
            raise ArtifactSecretLeakError(
                f"artifact secret scan failed with {piece_scan.get('issue_count')} issue(s) "
                f"in shard {dotted}",
                scan_result={
                    "shard": dotted,
                    "secret_scan": piece_scan,
                    "redaction": piece_receipt,
                },
            )
        if post_redaction_validator is not None:
            post_redaction_validator(redacted)
        final_path = parts_dir / _shard_file_name(dotted)
        _stream_dump(redacted, final_path, indent=indent)
        manifest_shards[dotted] = _shard_spec(dotted, final_path, final_path.stat().st_size)
        piece_events.extend(list(piece_receipt.get("events") or []))
        piece_scans.append(piece_scan)
    # ── 骨架脱敏（小；内联 ledger 的 reseal 与内联 authority 重建在此完成）──
    redacted_skeleton, skeleton_receipt = redact_and_validate(skeleton)
    if post_redaction_validator is not None:
        post_redaction_validator(redacted_skeleton)
    # ── authority 重建（B 兼容）：被分片切开的 envelope 键加载后重建 ──
    _rederive_authority_artifacts(
        redacted_skeleton,
        manifest_shards,
        parts_dir,
        indent=indent,
    )
    manifest: dict[str, Any] = {
        "schema_version": SCAN_RESULT_SHARD_SCHEMA,
        "threshold_bytes": threshold_bytes,
        "indent": indent,
        "shards": manifest_shards,
    }
    redacted_skeleton[SHARD_MARKER] = manifest
    _atomic_write_json(target, redacted_skeleton, indent=indent)
    return _combine_redaction_receipt(
        skeleton_receipt,
        piece_events,
        piece_scans,
        normalization=registry_stats,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 写入辅助：引用解析 / authority 重建 / receipt 组合
# ═════════════════════════════════════════════════════════════════════════════

class _RegistryResolver:
    """按 ref key 加载 registry entry（惰性、带缓存）。

    ref key（``<map>:<id>`` / ``sha256:<hex>``）→ 索引点分路径 → 骨架下钻；
    遇 null 占位自动加载分片。同一 entry 被多处引用时共享同一对象。
    """

    def __init__(
        self,
        skeleton: dict[str, Any],
        shards: dict[str, dict[str, Any]],
        parts_dir: Path,
    ) -> None:
        self._skeleton = skeleton
        self._shards = shards
        self._parts_dir = parts_dir
        self._cache: dict[str, Any] = {}

    def resolve(self, key: str) -> Any:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        dotted = _resolve_ref_dotted(key)
        node = _descend_and_fill(self._skeleton, self._shards, self._parts_dir, dotted)
        if node is None:
            raise ValueError(f"scan_result artifact registry entry missing: {key}")
        self._cache[key] = node
        return node


def _resolve_ref_dotted(key: str) -> str:
    if key.startswith("sha256:"):
        return blob_to_dotted(key)
    return ref_to_dotted(key)


def _path_node(
    skeleton: dict[str, Any],
    dotted: str,
    shards: dict[str, dict[str, Any]],
    parts_dir: Path,
) -> Any:
    """返回点分路径上的节点（沿途自动加载分片）；缺失返回 None。"""
    parts = dotted.split(".")
    node: Any = skeleton
    for i, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            return None
        value = node[part]
        if value is None:
            prefix = ".".join(parts[: i + 1])
            spec = shards.get(prefix)
            if spec is None:
                return None
            value = _load_shard_value(parts_dir, spec, prefix)
            node[part] = value
        node = value
    return node


def _set_path_none(skeleton: dict[str, Any], dotted: str) -> None:
    """把点分路径的叶子键还原为 null（写索引前还原分片占位）。"""
    parts = dotted.split(".")
    node: Any = skeleton
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict) and parts[-1] in node:
        node[parts[-1]] = None


def _set_path_value(skeleton: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node: Any = skeleton
    for part in parts[:-1]:
        if not isinstance(node, dict):
            return
        child = node.get(part)
        if not isinstance(child, dict):
            node[part] = {}
        node = node[part]
    if isinstance(node, dict):
        node[parts[-1]] = value


def _rederive_authority_artifacts(
    redacted_skeleton: dict[str, Any],
    manifest_shards: dict[str, dict[str, Any]],
    parts_dir: Path,
    *,
    indent: int = 2,
) -> None:
    """分片 store 上的 authority 指纹链重建（与整树 redaction 语义等价）。

    骨架 ``redact_and_validate`` 内部的重建只覆盖全部内联的 envelope；这里补齐
    被分片切开的 envelope（root / v12 的 ledger、occurrences）：从已脱敏分片
    加载 + hydrate 引用 → ``_rederive_redaction_sensitive_authority`` →
    重建的 formal_delivery_authority / canonical_defect_registry 单独
    redact+scan 后写回骨架 → 所有分片占位与整体注册的引用标记还原
    （索引保持骨架形态，不内联大内容）。
    """
    scope_dots = list(_AUTHORITY_SCOPE_KEYS) + [
        f"v12.{key}" for key in _AUTHORITY_SCOPE_KEYS
    ]
    if not any(dotted in manifest_shards for dotted in scope_dots):
        # 全部内联：骨架 redact_and_validate 已重建过，无需重复。
        return
    resolver = _RegistryResolver(redacted_skeleton, manifest_shards, parts_dir)
    restored_refs: dict[str, dict[str, Any]] = {}
    for dotted in scope_dots:
        node = _path_node(redacted_skeleton, dotted, manifest_shards, parts_dir)
        if node is None:
            continue
        if is_ref_marker(node) or is_blob_marker(node):
            # 整体注册的输入：快照引用标记，重建期间用解析内容，之后还原。
            restored_refs[dotted] = node
            marker_key = node.get(REF_KEY) or node.get(BLOB_KEY)
            resolved = resolver.resolve(marker_key)
            _set_path_value(redacted_skeleton, dotted, resolved)
            node = resolved
        if isinstance(node, (dict, list)):
            hydrate_refs(node, resolver.resolve)
    _rederive_redaction_sensitive_authority(redacted_skeleton)
    # 重建产物是新内容：单独 redact + scan（小对象；fail-closed）。
    for dotted in (
        "formal_delivery_authority",
        "canonical_defect_registry",
        "v12.formal_delivery_authority",
        "v12.canonical_defect_registry",
    ):
        node = _path_node(redacted_skeleton, dotted, manifest_shards, parts_dir)
        if not isinstance(node, dict):
            continue
        redacted, _receipt = redact_and_validate(node)
        _set_path_value(redacted_skeleton, dotted, redacted)
    # 还原整体注册输入的引用标记；再还原所有分片占位（含 re-derivation 期间
    # hydrate 的 ledger/occurrences/registry entries），索引保持骨架形态。
    for dotted, marker in restored_refs.items():
        _set_path_value(redacted_skeleton, dotted, marker)
    for dotted in sorted(manifest_shards):
        _set_path_none(redacted_skeleton, dotted)


def _combine_redaction_receipt(
    skeleton_receipt: dict[str, Any],
    piece_events: list[dict[str, Any]],
    piece_scans: list[dict[str, Any]],
    *,
    normalization: dict[str, int] | None = None,
) -> dict[str, Any]:
    """聚合骨架与各分片的脱敏 receipt（与整树 receipt 契约一致）。"""
    events = list(skeleton_receipt.get("redaction", {}).get("events") or [])
    events.extend(piece_events)
    scans = [skeleton_receipt.get("secret_scan")] + list(piece_scans)
    issues = [
        issue
        for scan in scans
        if isinstance(scan, dict)
        for issue in list(scan.get("issues") or [])
    ]
    safe = all(
        isinstance(scan, dict) and scan.get("safe") is True for scan in scans
    )
    return {
        "schema_version": skeleton_receipt.get("schema_version"),
        "redaction": {
            "schema_version": skeleton_receipt.get("redaction", {}).get(
                "schema_version"
            ),
            "redaction_applied": bool(events),
            "event_count": len(events),
            "events": events[:200],
            "secret_types": sorted({
                hit
                for event in events
                for hit in (event.get("hits") or [])
            }),
        },
        "secret_scan": {
            "schema_version": skeleton_receipt.get("secret_scan", {}).get(
                "schema_version"
            ),
            "safe": safe,
            "issue_count": len(issues),
            "issues": issues[:100],
        },
        "safe_to_persist": safe,
        "normalization": normalization,
    }


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
    resolve: Callable[[str], Any] | None = None,
) -> Any:
    """按点分路径下钻骨架；遇到 null 占位时加载对应分片并继续下钻。返回目标节点。

    ``strict=False``（默认）：请求路径在 store 中不存在时返回 None（消费方按
    ``.get()`` 语义容错）；``strict=True``：缺失即爆出（完整性断言用）。
    分片清单存在但分片文件缺失时无论何种模式都 fail-loud。
    ``resolve`` 提供时，下钻途中穿过引用标记（整体注册的子树 / blob）会先解析
    引用再继续（部分加载路径穿过 ref 也能得到完整内容）。
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
        if resolve is not None and i < len(parts) - 1 and (
            is_ref_marker(value) or is_blob_marker(value)
        ):
            marker_key = value.get(REF_KEY) or value.get(BLOB_KEY)
            resolved = resolve(marker_key)
            if resolved is None:
                raise FileNotFoundError(
                    f"scan_result artifact ref unresolvable: {marker_key}"
                )
            node[part] = resolved
            value = resolved
        node = value
    # 最终节点是引用标记（整体注册的子树 / blob）且提供了 resolve 时同样解析，
    # 并把解析结果写回骨架（部分加载请求整体注册键时返回完整内容）。
    if (
        resolve is not None
        and (is_ref_marker(node) or is_blob_marker(node))
        and parts
    ):
        marker_key = node.get(REF_KEY) or node.get(BLOB_KEY)
        resolved = resolve(marker_key)
        if resolved is None:
            raise FileNotFoundError(
                f"scan_result artifact ref unresolvable: {marker_key}"
            )
        _set_path_value(skeleton, dotted, resolved)
        node = resolved
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
    规范化 store（带 ``_artifact_registry``）自动 hydrate 引用：
    keys=None 全量 hydrate 后移除 registry 段（返回树与旧格式等价）；
    keys=[...] 只 hydrate 请求的子树（registry 段保留在树中）；
    keys=[] 不加载、不 hydrate（纯索引检查）。
    """
    manifest = payload.get(SHARD_MARKER)
    if not isinstance(manifest, dict):
        return payload  # 旧单文件（或任意普通 JSON 对象）
    skeleton = {key: value for key, value in payload.items() if key != SHARD_MARKER}
    shards = manifest.get("shards") if isinstance(manifest.get("shards"), dict) else {}
    resolver: _RegistryResolver | None = None
    registry_section = skeleton.get(REGISTRY_KEY)
    normalized = isinstance(registry_section, dict) and (
        registry_section.get("schema_version") == NORMALIZED_SCHEMA
    )
    if normalized:
        resolver = _RegistryResolver(skeleton, shards, parts_dir)
    if keys is None:
        for dotted in shards:
            _descend_and_fill(skeleton, shards, parts_dir, dotted)
        if normalized:
            hydrate_refs(skeleton, resolver.resolve)
            skeleton.pop(REGISTRY_KEY, None)
    else:
        for dotted in keys:
            if isinstance(dotted, str) and dotted:
                _descend_and_fill(
                    skeleton, shards, parts_dir, dotted, resolve=resolver.resolve if normalized else None,
                )
        if normalized and keys:
            for dotted in keys:
                if not isinstance(dotted, str) or not dotted:
                    continue
                node = _path_node(skeleton, dotted, shards, parts_dir)
                if isinstance(node, (dict, list)):
                    hydrate_refs(node, resolver.resolve)
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
