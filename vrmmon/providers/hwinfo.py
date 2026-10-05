"""HWiNFO64 共享内存数据源。

读取命名共享内存 ``Global\\HWiNFO_SENS_SM2``。需要 HWiNFO64 正在运行，
且已在「设置」中勾选 *Shared Memory Support*。

二进制布局依据（HWiNFO v8.x 实测）：
  - https://gist.github.com/namazso/0c37be5a53863954c8c8279f66cfb1cc
  - https://www.hwinfo.com/forum/threads/shared-memory-support.18/

映射区被视为不可信输入：先按 VirtualQuery 得到的区域大小整块复制进 bytes，
再用 struct.unpack_from 解析，头部字段全部做边界检查。任何越界都会抛
struct.error 并被捕获，而不是触发访问违例。
"""

from __future__ import annotations

import ctypes
import logging
import re
import struct
import sys

from ..classify import classify
from ..sensors import Kind, Registry, Sensor, SensorId
from .base import Provider, ProviderStatus

log = logging.getLogger(__name__)

SHM_NAME = "Global\\HWiNFO_SENS_SM2"
FILE_MAP_READ = 0x0004

HEADER_SIZE = 48
HEADER_MAGIC = 0x53695748  # 'HWiS'

# 头部字段偏移
OFF_MAGIC = 0x00
OFF_SENSOR_SECTION = 0x14
OFF_SENSOR_SIZE = 0x18
OFF_SENSOR_COUNT = 0x1C
OFF_ENTRY_SECTION = 0x20
OFF_ENTRY_SIZE = 0x24
OFF_ENTRY_COUNT = 0x28
OFF_POLL_TIME = 0x2C

# 传感器元素内部偏移
SENSOR_ID = 0x00
SENSOR_INSTANCE = 0x04
SENSOR_NAME_ORIG = 0x08
SENSOR_NAME_USER = 0x88

# 读数元素内部偏移
ENTRY_TYPE = 0x00
ENTRY_SENSOR_INDEX = 0x04
ENTRY_ID = 0x08
ENTRY_NAME_ORIG = 0x0C
ENTRY_NAME_USER = 0x8C
ENTRY_UNIT = 0x10C
ENTRY_VALUE = 0x11C
ENTRY_VALUE_MIN = 0x124
ENTRY_VALUE_MAX = 0x12C
ENTRY_VALUE_AVG = 0x134

# 我们实际读取字段所需的最小结构体尺寸
MIN_SENSOR_SIZE = 264
MIN_ENTRY_SIZE = 316

# 头部字段是外部输入，加硬上限避免损坏头部导致极端循环
MAX_TOTAL_SIZE = 64 * 1024 * 1024
MAX_SENSOR_COUNT = 4096
MAX_ENTRY_COUNT = 65536

# HWiNFO 的传感器类型枚举
SENSOR_TYPES: dict[int, Kind] = {
    1: Kind.TEMPERATURE,
    2: Kind.VOLTAGE,
    3: Kind.FAN,
    4: Kind.CURRENT,
    5: Kind.POWER,
    6: Kind.CLOCK,
    7: Kind.USAGE,
    8: Kind.OTHER,
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _ansi_codec() -> str:
    """HWiNFO 用系统 ANSI 代码页写字符串，不是 UTF-8 也不是固定的 cp1252。

    中文 Windows 上是 GBK(cp936)，英文 Windows 上才是 cp1252。
    参考实现硬编码 cp1252，在中文系统上会把"封装"读成"·â×°"。
    """
    if sys.platform == "win32":
        try:
            return f"cp{ctypes.windll.kernel32.GetACP()}"
        except Exception:  # pragma: no cover - 非 Windows 或被策略限制
            return "mbcs"
    return "cp1252"


_ANSI_CODEC = _ansi_codec()

if sys.platform == "win32":
    import ctypes.wintypes

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", ctypes.wintypes.DWORD),
            ("__alignment1", ctypes.wintypes.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", ctypes.wintypes.DWORD),
            ("Protect", ctypes.wintypes.DWORD),
            ("Type", ctypes.wintypes.DWORD),
            ("__alignment2", ctypes.wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE
    _kernel32.OpenFileMappingW.argtypes = [
        ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR,
    ]
    _kernel32.MapViewOfFile.restype = ctypes.c_void_p
    _kernel32.MapViewOfFile.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_size_t,
    ]
    _kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    _kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    _kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    _kernel32.VirtualQuery.restype = ctypes.c_size_t
    _kernel32.VirtualQuery.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t,
    ]
else:  # pragma: no cover
    MEMORY_BASIC_INFORMATION = None
    _kernel32 = None


class ShmError(RuntimeError):
    """共享内存存在但内容不可用。"""


def _mapped_region_size(ptr: int) -> int:
    mbi = MEMORY_BASIC_INFORMATION()
    if _kernel32.VirtualQuery(ptr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
        return 0
    return int(mbi.RegionSize)


def _decode(raw: bytes, offset: int, length: int) -> str:
    """解码以 NUL 结尾的字段，按系统 ANSI 代码页。"""
    data = raw[offset:offset + length].split(b"\x00")[0]
    try:
        text = data.decode(_ANSI_CODEC, errors="replace")
    except LookupError:  # pragma: no cover
        text = data.decode("cp1252", errors="replace")
    return _CONTROL_CHARS.sub("", text).strip()


def read_shared_memory() -> bytes:
    """把共享内存整块复制成 bytes。不可用时抛 ShmError。"""
    if _kernel32 is None:
        raise ShmError("HWiNFO 共享内存仅支持 Windows")

    handle = _kernel32.OpenFileMappingW(FILE_MAP_READ, False, SHM_NAME)
    if not handle:
        raise ShmError("无法打开共享内存 Global\\HWiNFO_SENS_SM2（HWiNFO 未运行或未开启共享内存）")

    ptr = _kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
    if not ptr:
        err = ctypes.get_last_error()
        _kernel32.CloseHandle(handle)
        raise ShmError(f"MapViewOfFile 失败 (Win32 错误 {err})")

    try:
        region = _mapped_region_size(ptr)
        if region < HEADER_SIZE:
            raise ShmError(f"映射区过小 ({region} 字节)")
        size = min(region, MAX_TOTAL_SIZE)
        return bytes((ctypes.c_char * size).from_address(ptr))
    finally:
        _kernel32.UnmapViewOfFile(ptr)
        _kernel32.CloseHandle(handle)


class HwinfoEntry:
    __slots__ = ("group", "name", "kind", "unit", "value", "vmin", "vmax", "vavg")

    def __init__(self, group: str, name: str, kind: Kind, unit: str,
                 value: float, vmin: float, vmax: float, vavg: float) -> None:
        self.group = group
        self.name = name
        self.kind = kind
        self.unit = unit
        self.value = value
        self.vmin = vmin
        self.vmax = vmax
        self.vavg = vavg


def parse_shared_memory(buf: bytes) -> tuple[list[HwinfoEntry], int]:
    """解析共享内存快照，返回 (读数列表, 轮询周期毫秒)。纯函数，便于测试。"""
    if len(buf) < HEADER_SIZE:
        raise ShmError(f"缓冲区过小 ({len(buf)} 字节)")

    magic = struct.unpack_from("<I", buf, OFF_MAGIC)[0]
    if magic != HEADER_MAGIC:
        raise ShmError(f"magic 不匹配: 0x{magic:08X}（期望 0x{HEADER_MAGIC:08X}）")

    sensor_off = struct.unpack_from("<I", buf, OFF_SENSOR_SECTION)[0]
    sensor_size = struct.unpack_from("<I", buf, OFF_SENSOR_SIZE)[0]
    sensor_count = struct.unpack_from("<I", buf, OFF_SENSOR_COUNT)[0]
    entry_off = struct.unpack_from("<I", buf, OFF_ENTRY_SECTION)[0]
    entry_size = struct.unpack_from("<I", buf, OFF_ENTRY_SIZE)[0]
    entry_count = struct.unpack_from("<I", buf, OFF_ENTRY_COUNT)[0]
    poll_ms = struct.unpack_from("<I", buf, OFF_POLL_TIME)[0]

    if sensor_size < MIN_SENSOR_SIZE or entry_size < MIN_ENTRY_SIZE:
        raise ShmError(f"结构体尺寸异常 (sensor={sensor_size}, entry={entry_size})")
    if sensor_count > MAX_SENSOR_COUNT or entry_count > MAX_ENTRY_COUNT:
        raise ShmError(f"元素数量异常 (sensors={sensor_count}, entries={entry_count})")
    if sensor_off + sensor_size * sensor_count > len(buf):
        raise ShmError("传感器区越界")
    if entry_off + entry_size * entry_count > len(buf):
        raise ShmError("读数区越界")

    groups: list[str] = []
    for i in range(sensor_count):
        base = sensor_off + i * sensor_size
        name = _decode(buf, base + SENSOR_NAME_USER, 128) or _decode(buf, base + SENSOR_NAME_ORIG, 128)
        groups.append(name or f"Sensor {i}")

    entries: list[HwinfoEntry] = []
    for i in range(entry_count):
        base = entry_off + i * entry_size
        raw_type = struct.unpack_from("<I", buf, base + ENTRY_TYPE)[0]
        kind = SENSOR_TYPES.get(raw_type, Kind.OTHER)
        idx = struct.unpack_from("<I", buf, base + ENTRY_SENSOR_INDEX)[0]
        group = groups[idx] if idx < len(groups) else f"Sensor {idx}"
        name = _decode(buf, base + ENTRY_NAME_USER, 128) or _decode(buf, base + ENTRY_NAME_ORIG, 128)
        unit = _decode(buf, base + ENTRY_UNIT, 16)
        value = struct.unpack_from("<d", buf, base + ENTRY_VALUE)[0]
        vmin = struct.unpack_from("<d", buf, base + ENTRY_VALUE_MIN)[0]
        vmax = struct.unpack_from("<d", buf, base + ENTRY_VALUE_MAX)[0]
        vavg = struct.unpack_from("<d", buf, base + ENTRY_VALUE_AVG)[0]
        if not name:
            continue
        entries.append(HwinfoEntry(group, name, kind, unit, value, vmin, vmax, vavg))

    return entries, poll_ms


def hwinfo_running() -> bool:
    """HWiNFO64.EXE 是否在运行（不需要 psutil）。"""
    try:
        import psutil  # type: ignore
    except ImportError:
        return True  # 无法判断，交给共享内存的打开结果说明问题
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower().startswith("hwinfo"):
                return True
        except Exception:  # pragma: no cover - 进程可能瞬时退出
            continue
    return False


class HwinfoProvider(Provider):
    key = "hwinfo"
    label = "HWiNFO 共享内存"

    def __init__(self) -> None:
        self.poll_ms = 0
        self._running_cache: bool | None = None

    def probe(self) -> ProviderStatus:
        try:
            buf = read_shared_memory()
        except ShmError as exc:
            running = hwinfo_running()
            if running:
                return ProviderStatus(
                    False, "共享内存未开启",
                    "HWiNFO 正在运行，但需要在「设置 → Shared Memory Support」中勾选后重启 HWiNFO",
                )
            return ProviderStatus(False, "HWiNFO 未运行", "请先启动 HWiNFO64 并打开传感器窗口")
        try:
            entries, poll_ms = parse_shared_memory(buf)
        except ShmError as exc:
            return ProviderStatus(False, "共享内存格式异常", str(exc))
        self.poll_ms = poll_ms
        temps = sum(1 for e in entries if e.kind == Kind.TEMPERATURE)
        return ProviderStatus(
            True, "正常", f"{len(entries)} 个读数（其中 {temps} 个温度），HWiNFO 轮询 {poll_ms} ms",
            sensors=len(entries),
        )

    def read(self, registry: Registry) -> list[Sensor]:
        try:
            entries, poll_ms = parse_shared_memory(read_shared_memory())
        except ShmError as exc:
            self.last_error = str(exc)
            return []
        self.last_error = ""
        self.poll_ms = poll_ms

        seen: list[Sensor] = []
        for entry in entries:
            role, reason = classify(entry.group, entry.name, entry.kind)
            sensor_id = SensorId(self.key, entry.group, entry.name, entry.kind.value)
            sensor = registry.get(sensor_id, entry.kind, entry.unit, role, reason)
            sensor.observe(entry.value, entry.vmin, entry.vmax)
            seen.append(sensor)
        return seen
