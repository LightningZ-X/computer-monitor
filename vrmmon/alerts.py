"""阈值告警：带滞回与冷却时间，避免在阈值附近反复刷屏。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Thresholds
from .sensors import Kind, Role, Sensor

#: 报警类传感器一旦置位就告警，但同一条目在冷却时间内只报一次
FLAG_COOLDOWN_S = 120.0


@dataclass
class AlertEvent:
    timestamp: float
    level: str      # info / warn / critical / alarm
    title: str
    message: str

    @property
    def is_severe(self) -> bool:
        return self.level in ("critical", "alarm")


class AlertEngine:
    def __init__(self, thresholds: Thresholds, alert_on_vrm_flag: bool = True,
                 cooldown_s: float = 90.0, hysteresis: float = 3.0) -> None:
        self.thresholds = thresholds
        self.alert_on_vrm_flag = alert_on_vrm_flag
        self.cooldown_s = cooldown_s
        self.hysteresis = hysteresis
        self._last_fired: dict[str, float] = {}
        self._armed: dict[str, bool] = {}

    def evaluate(self, sensors: list[Sensor], now: float) -> list[AlertEvent]:
        events: list[AlertEvent] = []

        for sensor in sensors:
            if sensor.last_value is None:
                continue

            # 1) 供电过热报警位：只要置位就报，这是读不到 VRM 温度时最直接的信号
            if sensor.is_alarm:
                if not self.alert_on_vrm_flag:
                    continue
                if not sensor.triggered:
                    continue
                key = f"flag:{sensor.id}"
                if now - self._last_fired.get(key, -1e9) >= FLAG_COOLDOWN_S:
                    self._last_fired[key] = now
                    events.append(AlertEvent(
                        now, "alarm", "供电过热报警",
                        f"{sensor.id.group} 的「{sensor.id.name}」已置位，"
                        f"CPU 供电模块上报过过热事件"))
                continue

            # 2) 温度阈值：只在温度类传感器上判，且带滞回
            #    注意只排除 META（差值/阈值/分辨率）；Role.OTHER 是真实温度
            #    （SSD、内存 SPD 等），它们同样应该能触发告警。
            if sensor.kind is not Kind.TEMPERATURE or sensor.role is Role.META:
                continue
            value = sensor.last_value
            key = str(sensor.id)
            armed = self._armed.get(key, True)

            if value >= self.thresholds.critical:
                if armed:
                    self._armed[key] = False
                    self._last_fired[key] = now
                    events.append(AlertEvent(
                        now, "critical", "温度过高",
                        f"{sensor.id.name} = {value:.1f} °C（危险阈值 "
                        f"{self.thresholds.critical:.0f} °C）"))
            elif value >= self.thresholds.warn:
                if armed and now - self._last_fired.get(key, -1e9) >= self.cooldown_s:
                    self._armed[key] = False
                    self._last_fired[key] = now
                    events.append(AlertEvent(
                        now, "warn", "温度偏高",
                        f"{sensor.id.name} = {value:.1f} °C（告警阈值 "
                        f"{self.thresholds.warn:.0f} °C）"))
            elif value < self.thresholds.warn - self.hysteresis:
                # 降回阈值以下足够多，重新武装
                self._armed[key] = True

        return events
