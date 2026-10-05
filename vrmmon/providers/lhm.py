"""LibreHardwareMonitor 直读数据源（进程内，经 pythonnet 加载 .NET 程序集）。

这是不依赖 HWiNFO 的主路径。LibreHardwareMonitor 自带一个内核驱动，能读：
  - CPU MSR → Intel/AMD 的 DTS 每核温度、封装温度
  - 主板 Super I/O / EC 端口 I/O → 主板温度、风扇、电压（部分主板映射表含 VRM 通道）
  - SMBus → 内存 SPD 温度
  - NVAPI/NVML → 显卡核心、显存结温

关键约束：以上大部分需要**内核驱动**，而加载驱动需要管理员权限。
未提权时只能拿到显卡与硬盘温度——此时 probe() 会如实报告缺什么。
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..classify import classify
from ..sensors import Kind, Registry, Sensor, SensorId
from .base import Provider, ProviderStatus

ROOT = Path(__file__).resolve().parent.parent.parent

#: 各类硬件的刷新节奏（秒）。0 表示每轮都刷新。
#:
#: 实测（本机，104 个读数）：一轮 LHM 读取 92.6ms，其中
#:   GpuNvidia.Update()  46.4ms   ← NVAPI 调用，占一半
#:   Cpu.Update()         2.6ms
#:   Storage ×2           2.2ms
#: 如果全部每轮刷新，单轮就锁死在 90ms 以上，采样间隔无法低于 100ms。
#: 而 GPU / 存储 / 内存的温度变化远慢于 CPU，低频刷新完全够用——
#: CPU 才是需要 50ms 高频的那一路。
HARDWARE_PERIOD_S: dict[str, float] = {
    "Cpu": 0.0,
    "GpuNvidia": 1.0,
    "GpuAmd": 1.0,
    "GpuIntel": 0.5,
    "Motherboard": 1.0,
    "Memory": 2.0,
    "Storage": 5.0,
    "Psu": 2.0,
    "Cooler": 2.0,
    "Battery": 10.0,
    "Network": 10.0,
}
DEFAULT_PERIOD_S = 2.0


@dataclass
class _Slot:
    """一个传感器的静态信息 + 它的 .NET 对象。

    名字/类型/角色/ID 这些每轮都不变，读一次就够；每轮只读 Value/Min/Max。
    这样把每个传感器的跨边界属性访问从 6 次降到 3 次。

    `sensor_id` 也缓存：原先每轮都给每个传感器 new 一个 SensorId，
    高频采样下每秒产生上万个短命对象，把 GC 逼出 30~50ms 的停顿
    （实测 5ms 间隔下 2.7% 的慢轮吃掉了 17% 的墙钟时间）。
    """

    sensor: object
    sensor_id: SensorId
    name: str
    kind: Kind
    unit: str
    role: Role
    reason: str

#: LHM 的 SensorType → 我们的 Kind
SENSOR_TYPES: dict[str, Kind] = {
    "Temperature": Kind.TEMPERATURE,
    "Voltage": Kind.VOLTAGE,
    "Current": Kind.CURRENT,
    "Power": Kind.POWER,
    "Energy": Kind.POWER,
    "Fan": Kind.FAN,
    "Control": Kind.USAGE,
    "Clock": Kind.CLOCK,
    "Frequency": Kind.CLOCK,
    "Load": Kind.USAGE,
    "Level": Kind.USAGE,
    "Factor": Kind.OTHER,
    "Data": Kind.OTHER,
    "SmallData": Kind.OTHER,
    "Throughput": Kind.OTHER,
    "TimeSpan": Kind.OTHER,
    "Flow": Kind.OTHER,
    "Humidity": Kind.OTHER,
}


def runtime_dir() -> Path:
    """DLL 目录。默认用 .NET Framework 版本（机器上必然存在）。"""
    override = os.environ.get("VRMMON_LHM_DIR")
    if override:
        return Path(override)
    return ROOT / "packages" / "flat472"


class LhmProvider(Provider):
    key = "lhm"
    label = "LibreHardwareMonitor 直读"

    def __init__(self, dll_dir: Path | None = None) -> None:
        self.dll_dir = Path(dll_dir) if dll_dir else runtime_dir()
        self._ready = False
        self._init_error = ""
        self._computer = None
        self._computer_type = None
        self._elevated = False
        self._cpu_temperatures = 0
        #: 每个硬件的传感器静态信息缓存（按硬件路径区分）
        self._slots: dict[str, list[_Slot]] = {}
        #: 每个硬件上次 Update() 的时刻，用于按类型控制刷新节奏
        self._last_update: dict[str, float] = {}

    # ---------- 初始化 ----------

    def _ensure(self) -> str:
        """返回空串表示可用，否则返回原因。"""
        if self._ready:
            return self._init_error
        if not (self.dll_dir / "LibreHardwareMonitorLib.dll").exists():
            self._init_error = (f"缺少 {self.dll_dir}（先运行 tools\\setup_lhm.ps1 部署依赖）")
            self._ready = True
            return self._init_error

        # pythonnet 的运行时只能在 import clr 之前设定，且一个进程只能初始化一次
        os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
        if str(self.dll_dir) not in sys.path:
            sys.path.insert(0, str(self.dll_dir))

        try:
            import clr
        except ImportError:
            self._init_error = "未安装 pythonnet（pip install pythonnet）"
            self._ready = True
            return self._init_error
        except Exception as exc:
            self._init_error = f"pythonnet 初始化失败: {exc}"
            self._ready = True
            return self._init_error

        try:
            clr.AddReference(str(self.dll_dir / "LibreHardwareMonitorLib.dll"))
            from LibreHardwareMonitor.Hardware import Computer
        except Exception as exc:
            self._init_error = f"加载 LibreHardwareMonitorLib 失败: {exc}"
            self._ready = True
            return self._init_error

        self._computer_type = Computer
        computer = Computer()
        computer.IsCpuEnabled = True
        computer.IsMotherboardEnabled = True
        computer.IsControllerEnabled = True
        computer.IsMemoryEnabled = True
        computer.IsGpuEnabled = True
        computer.IsStorageEnabled = True
        computer.IsNetworkEnabled = False
        computer.IsPsuEnabled = True

        try:
            computer.Open()
        except Exception as exc:
            self._init_error = f"打开硬件失败（多半是没有管理员权限加载驱动）: {exc}"
            self._ready = True
            return self._init_error

        self._computer = computer
        self._elevated = self._detect_elevated()

        # 驱动没加载时，CPU 节点在但没有任何温度传感器——这是最可靠的判断依据
        self._cpu_temperatures = self._count_cpu_temperatures()

        self._ready = True
        self._init_error = ""
        return ""

    @staticmethod
    def _detect_elevated() -> bool:
        try:
            from System.Security.Principal import (  # noqa: N812
                WindowsBuiltInRole, WindowsIdentity, WindowsPrincipal,
            )
            return bool(WindowsPrincipal(WindowsIdentity.GetCurrent())
                        .IsInRole(WindowsBuiltInRole.Administrator))
        except Exception:
            return False

    def _count_cpu_temperatures(self) -> int:
        """只数**有值**的 CPU 温度通道。

        驱动没生效时 LHM 仍会创建这些传感器对象，但 Value 全是 None；
        按对象计数会谎报"读到了温度"。
        """
        count = 0
        for hardware in self._computer.Hardware:
            if str(hardware.HardwareType) != "Cpu":
                continue
            try:
                hardware.Update()
            except Exception:
                pass
            for sensor in hardware.Sensors:
                if str(sensor.SensorType) == "Temperature" and sensor.Value is not None:
                    count += 1
        return count

    def _count_cpu_channels(self) -> int:
        """CPU 温度通道总数（含无值的），用于说明"通道存在但读不到值"。"""
        total = 0
        for hardware in self._computer.Hardware:
            if str(hardware.HardwareType) != "Cpu":
                continue
            for sensor in hardware.Sensors:
                if str(sensor.SensorType) == "Temperature":
                    total += 1
        return total

    def close(self) -> None:
        if self._computer is not None:
            try:
                self._computer.Close()
            except Exception:
                pass
            self._computer = None

    # ---------- 采集 ----------

    def probe(self) -> ProviderStatus:
        error = self._ensure()
        if error:
            return ProviderStatus(False, "不可用", error)

        counts: dict[str, int] = {}
        for hardware in self._computer.Hardware:
            key = str(hardware.HardwareType)
            counts[key] = counts.get(key, 0) + 1
        summary = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))

        if self._cpu_temperatures == 0:
            channels = self._count_cpu_channels()
            if channels:
                reason = (f"CPU 温度传感器存在 {channels} 个但全部无值："
                          f"内核驱动未能读取 MSR。以管理员身份运行可解决")
            elif self._elevated:
                reason = "已提权，但 LHM 仍未给出 CPU 温度通道"
            else:
                reason = "内核驱动未加载，读不到 CPU 与主板传感器；以管理员身份运行可解决"
            return ProviderStatus(
                True, "仅显卡/硬盘可用",
                f"识别到 {summary}；{reason}",
            )

        return ProviderStatus(
            True, "完整",
            f"识别到 {summary}；CPU 温度通道 {self._cpu_temperatures} 个有值"
            + ("（已提权）" if self._elevated else ""),
        )

    def read(self, registry: Registry) -> list[Sensor]:
        if self._ensure():
            return []
        seen: list[Sensor] = []
        now = time.monotonic()
        for hardware in self._computer.Hardware:
            self._walk(hardware, "", registry, seen, now)
        return seen

    def _walk(self, hardware, path: str, registry: Registry,
              seen: list[Sensor], now: float) -> None:
        here = f"{hardware.HardwareType}: {hardware.Name}" if not path \
            else f"{path} / {hardware.Name}"

        period = HARDWARE_PERIOD_S.get(str(hardware.HardwareType), DEFAULT_PERIOD_S)
        if period <= 0 or now - self._last_update.get(here, 0.0) >= period:
            try:
                hardware.Update()
            except Exception:
                pass
            self._last_update[here] = now

        slots = self._slots.get(here)
        if slots is None or len(slots) != len(hardware.Sensors):
            slots = self._build_slots(hardware, here)
            self._slots[here] = slots

        for slot in slots:
            value = slot.sensor.Value
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            item = registry.get(slot.sensor_id, slot.kind, slot.unit,
                                slot.role, slot.reason)
            item.observe(number, _optional(slot.sensor.Min), _optional(slot.sensor.Max))
            seen.append(item)

        for sub in hardware.SubHardware:
            self._walk(sub, here, registry, seen, now)

    def _build_slots(self, hardware, here: str) -> list[_Slot]:
        """只为这个硬件构造一次传感器静态信息。"""
        slots: list[_Slot] = []
        try:
            sensors = hardware.Sensors
        except Exception:
            return slots
        for sensor in sensors:
            try:
                name = str(sensor.Name)
                kind = SENSOR_TYPES.get(str(sensor.SensorType), Kind.OTHER)
            except Exception:
                continue
            if not name:
                continue
            role, reason = classify(here, name, kind)
            slots.append(_Slot(sensor, SensorId(self.key, here, name, kind.value),
                               name, kind, _unit(kind), role, reason))
        return slots

def _optional(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _unit(kind: Kind) -> str:
    return {
        Kind.TEMPERATURE: "°C",
        Kind.VOLTAGE: "V",
        Kind.CURRENT: "A",
        Kind.POWER: "W",
        Kind.FAN: "RPM",
        Kind.CLOCK: "MHz",
        Kind.USAGE: "%",
    }.get(kind, "")
