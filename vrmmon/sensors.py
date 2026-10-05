"""统一传感器模型：所有数据源都归一化成 Sensor / Reading。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    TEMPERATURE = "Temperature"
    VOLTAGE = "Voltage"
    CURRENT = "Current"
    POWER = "Power"
    FAN = "Fan"
    CLOCK = "Clock"
    USAGE = "Usage"
    OTHER = "Other"


class Role(str, Enum):
    """传感器物理含义的最佳猜测（依据名称推断，不保证 100% 正确）。"""

    CPU_VRM = "cpu_vrm"          # 主板 CPU 供电模块 / MOSFET 温度
    GPU_VRM = "gpu_vrm"          # 显卡供电模块温度
    VRM_UNKNOWN = "vrm_unknown"  # 名字表明是供电，但无法判断归属
    VRM_ALARM = "vrm_alarm"      # 供电过热报警标志（不是温度，是布尔标记）
    META = "meta"                # 名字像温度但不是温度读数（差值 / 阈值 / 分辨率）
    CPU_PACKAGE = "cpu_package"
    CPU_CORE = "cpu_core"
    CPU_SOC = "cpu_soc"
    GPU_CORE = "gpu_core"
    GPU_HOTSPOT = "gpu_hotspot"
    GPU_MEMORY = "gpu_memory"
    BOARD = "board"
    OTHER = "other"


#: 真正的"供电温度"角色
VRM_ROLES = (Role.CPU_VRM, Role.GPU_VRM, Role.VRM_UNKNOWN)


@dataclass(frozen=True, order=True)
class SensorId:
    """传感器唯一标识。

    **必须包含 kind**：同一个分组下会出现同名但不同量的传感器，
    例如 LibreHardwareMonitor 的 "CPU Package" 同时是温度和功耗，
    "P-Core #1" 同时是电压和频率。不含 kind 会把它们合并成一个对象，
    后写入的值会覆盖先写入的，导致其中一路数据被静默丢弃。
    """

    provider: str
    group: str
    name: str
    kind: str = ""

    def __str__(self) -> str:
        return f"{self.provider}::{self.group}::{self.name}::{self.kind}"


@dataclass
class Sensor:
    id: SensorId
    kind: Kind
    unit: str = ""
    role: Role = Role.OTHER
    role_reason: str = ""
    #: 最近一次读数
    last_value: float | None = None
    #: 是否采用数据源自带的极值（HWiNFO 的 min/max 是它自己启动以来的，
    #: 可能早于本程序；用户点过"重置极值"后就只用本程序累计的值）
    use_source_extremes: bool = True
    #: 由本程序累积的极值
    run_min: float | None = None
    run_max: float | None = None
    live_min: float | None = None
    live_max: float | None = None

    @property
    def label(self) -> str:
        return f"{self.id.group} / {self.id.name}"

    @property
    def is_vrm(self) -> bool:
        return self.role in VRM_ROLES

    @property
    def is_alarm(self) -> bool:
        return self.role is Role.VRM_ALARM

    def observe(self, value: float, src_min: float | None, src_max: float | None) -> None:
        """记录一次采样，维护运行期极值。"""
        if value != value:  # NaN
            return
        self.last_value = value
        self.run_min = value if self.run_min is None else min(self.run_min, value)
        self.run_max = value if self.run_max is None else max(self.run_max, value)
        if self.use_source_extremes:
            if src_min is not None and src_min == src_min:
                self.live_min = src_min
            if src_max is not None and src_max == src_max:
                self.live_max = src_max

    def clear_extremes(self) -> None:
        """清空极值，并从此只统计本程序自己看到的数据。"""
        self.use_source_extremes = False
        self.run_min = self.run_max = None
        self.live_min = self.live_max = None

    def bounds(self) -> tuple[float | None, float | None]:
        lo = self.live_min if self.live_min is not None else self.run_min
        hi = self.live_max if self.live_max is not None else self.run_max
        if lo is not None and self.run_min is not None:
            lo = min(lo, self.run_min)
        if hi is not None and self.run_max is not None:
            hi = max(hi, self.run_max)
        return lo, hi

    @property
    def triggered(self) -> bool:
        """报警类传感器是否处于触发状态。"""
        return self.is_alarm and bool(self.last_value)


@dataclass
class Reading:
    sensor: Sensor
    value: float
    timestamp: float = field(default_factory=time.time)


class Registry:
    """按 SensorId 合并不同轮次、不同数据源上报的传感器。"""

    def __init__(self) -> None:
        self._sensors: dict[SensorId, Sensor] = {}

    def get(self, sensor_id: SensorId, kind: Kind, unit: str, role: Role, reason: str) -> Sensor:
        existing = self._sensors.get(sensor_id)
        if existing is None:
            existing = Sensor(id=sensor_id, kind=kind, unit=unit, role=role, role_reason=reason)
            self._sensors[sensor_id] = existing
            return existing
        # 数据源重命名/单位变化时保持对象稳定，只更新元数据。
        # role 直接赋值：SensorId 已含 kind，分类结果对同一 id 是稳定的。
        existing.kind = kind
        existing.unit = unit or existing.unit
        existing.role = role
        existing.role_reason = reason
        return existing

    def all(self) -> list[Sensor]:
        return list(self._sensors.values())

    def by_role(self, *roles: Role) -> list[Sensor]:
        wanted = set(roles)
        out = [s for s in self._sensors.values() if s.role in wanted]
        return sorted(out, key=lambda s: (s.role.value, s.id.group, s.id.name))

    def vrm(self, kind: Kind | None = Kind.TEMPERATURE) -> list[Sensor]:
        out = [s for s in self._sensors.values() if s.is_vrm]
        if kind is not None:
            out = [s for s in out if s.kind == kind]
        return sorted(out, key=lambda s: (s.role.value, s.id.group, s.id.name))

    def __len__(self) -> int:
        return len(self._sensors)


#: 各单位显示几位小数。**规则只在这里定义**，所有显示位置自动一致
#: （传感器表三列、托盘提示、体检报告都走 format_value）。
#:
#: 取值对齐 HWiNFO 的惯例——它按传感器类型给固定位数，而不是按实际分辨率：
#: 温度 1 位、电压 3 位、电流 3 位、功耗 2 位、风扇与频率取整。
#: 注意这与"传感器真实分辨率"是两件事：CPU DTS 与 NVML 报的本来就是整数
#: （Intel MSR 里存的是"距 TjMax 多少度"），所以 CPU 核心温度显示 65.0 °C
#: 里的 0 是真的，不是被抹平。要改任何一档，改这张表即可。
DECIMALS_BY_UNIT: dict[str, int] = {
    "°C": 1,     # 温度   0.1
    "V": 3,      # 电压   0.001
    "A": 3,      # 电流   0.001
    "W": 2,      # 功耗   0.01
    "RPM": 0,    # 风扇   整数
    "MHz": 0,    # 频率   整数
    "%": 1,      # 占用率 0.1
}
DEFAULT_DECIMALS = 1


def decimals_for(unit: str) -> int:
    return DECIMALS_BY_UNIT.get(unit, DEFAULT_DECIMALS)


def format_value(value: float | None, unit: str = "",
                 decimals: int | None = None) -> str:
    """格式化读数。

    decimals 为 None 时按单位查 DECIMALS_BY_UNIT——调用方只传 unit 即可，
    不必各自记精度。需要特例（例如体检报告统一两位）才显式传 decimals。
    """
    if value is None or value != value:  # None 或 NaN
        return "N/A"
    if decimals is None:
        decimals = decimals_for(unit)
    text = f"{value:.{decimals}f}"
    return f"{text} {unit}".strip() if unit else text
