"""NVIDIA GPU 直读数据源（NVML）。

不依赖 HWiNFO：直接通过 NVML 读显卡温度与功耗。
注意 NVML 只暴露驱动愿意公开的量。消费级显卡的供电(VRM)温度通常不在其中，
此时本数据源不会有 GPU 供电项，由 HWiNFO 侧补足或如实显示"未暴露"。
"""

from __future__ import annotations

from ..classify import classify
from ..sensors import Kind, Registry, Sensor, SensorId
from .base import Provider, ProviderStatus

try:  # pragma: no cover - 取决于是否安装
    import pynvml  # type: ignore
except ImportError:  # pragma: no cover
    pynvml = None


# NVML 温度传感器类型
TEMP_GPU = 0
TEMP_MEMORY = 1

#: NVML 里可能存在的额外温度传感器名（不同驱动/卡型支持不同）
_EXTRA_TEMPS = (
    ("GPU 热点温度", 2),   # NVML_TEMPERATURE_COUNT_THRESHOLD 附近，部分卡可用
)


class NvmlProvider(Provider):
    key = "nvml"
    label = "NVIDIA NVML"

    def __init__(self) -> None:
        self._inited = False

    def _ensure(self) -> str:
        if pynvml is None:
            return "未安装 pynvml（pip install pynvml）"
        if not self._inited:
            try:
                pynvml.nvmlInit()
                self._inited = True
            except Exception as exc:
                return f"NVML 初始化失败: {exc}"
        return ""

    def probe(self) -> ProviderStatus:
        err = self._ensure()
        if err:
            return ProviderStatus(False, "不可用", err)
        try:
            count = pynvml.nvmlDeviceGetCount()
            names = []
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                names.append(pynvml.nvmlDeviceGetName(handle))
                if isinstance(names[-1], bytes):
                    names[-1] = names[-1].decode("utf-8", "replace")
        except Exception as exc:
            return ProviderStatus(False, "枚举失败", str(exc))
        return ProviderStatus(True, "正常", f"{count} 块显卡: {', '.join(names)}", sensors=count)

    def read(self, registry: Registry) -> list[Sensor]:
        if self._ensure():
            return []
        seen: list[Sensor] = []
        try:
            count = pynvml.nvmlDeviceGetCount()
        except Exception as exc:
            self.last_error = str(exc)
            return []

        for index in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
            except Exception:
                continue
            group = f"GPU{index} {name}"

            for reading_name, temp_id in (("GPU 核心温度", TEMP_GPU),) + _EXTRA_TEMPS:
                value = self._temperature(handle, temp_id)
                if value is None:
                    continue
                self._add(registry, seen, group, reading_name, Kind.TEMPERATURE, value, "°C")

            power = self._power(handle)
            if power is not None:
                self._add(registry, seen, group, "GPU 功耗", Kind.POWER, power, "W")

            fan = self._fan(handle)
            if fan is not None:
                self._add(registry, seen, group, "GPU 风扇", Kind.FAN, fan, "%")

        return seen

    @staticmethod
    def _temperature(handle, temp_id: int) -> float | None:
        try:
            return float(pynvml.nvmlDeviceGetTemperature(handle, temp_id))
        except Exception:
            return None

    @staticmethod
    def _power(handle) -> float | None:
        try:
            return pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
        except Exception:
            return None

    @staticmethod
    def _fan(handle) -> float | None:
        try:
            return float(pynvml.nvmlDeviceGetFanSpeed(handle))
        except Exception:
            return None

    def _add(self, registry: Registry, seen: list[Sensor], group: str, name: str,
             kind: Kind, value: float, unit: str) -> None:
        role, reason = classify(group, name, kind)
        sensor = registry.get(SensorId(self.key, group, name, kind.value), kind, unit,
                              role, reason)
        sensor.observe(value, None, None)
        seen.append(sensor)
