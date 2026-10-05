"""采集线程：轮询数据源、维护历史曲线、写库、评估告警。

线程模型：采集线程独占 Registry / Store / 告警引擎，UI 线程只读 Snapshot。
两者之间用一把锁交换不可变快照，UI 绝不直接碰采集线程的状态。
"""

from __future__ import annotations

import ctypes
import gc
import sys
import threading
import time
from dataclasses import dataclass, field

from .alerts import AlertEngine, AlertEvent
from .config import Config, clamp_interval
from .providers import build_providers
from .providers.base import Provider, ProviderStatus
from .sensors import Registry, Sensor, SensorId
from .store import Store

#: 重新探测数据源可用性的间隔（秒）
PROBE_INTERVAL_S = 10.0
#: 单轮耗时超过「间隔 × 该倍数」才算"采样落后"——避免正常抖动刷屏
OVERRUN_TOLERANCE = 1.5
#: 多久清一次过期记录（秒）
PURGE_INTERVAL_S = 300.0
#: 低于这个间隔就需要提高系统计时器精度（秒）
TIMER_RESOLUTION_THRESHOLD_S = 0.05


def _timer_resolution(raise_it: bool) -> None:
    """把系统计时器精度提到 1ms / 还原。

    Windows 默认的计时器粒度约 **15.6ms**：`Event.wait(0.005)` 实际会睡满 15.6ms。
    实测——不睡能跑 211 轮/秒，但设 10ms 只跑出 55 轮/秒，瓶颈根本不在采集而在睡眠。
    timeBeginPeriod(1) 之后 wait 才能精确到 1ms 量级。

    Win10 2004+ 的计时器精度**按进程**生效，不会全系统拖累功耗；尽管如此，
    只在间隔确实很小时才开，并且与 timeEndPeriod 严格一一对应。
    """
    if sys.platform != "win32":
        return
    try:
        if raise_it:
            ctypes.windll.winmm.timeBeginPeriod(1)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


@dataclass
class Snapshot:
    timestamp: float = 0.0
    sequence: int = 0
    paused: bool = False
    sensors: list[Sensor] = field(default_factory=list)
    statuses: list[tuple[str, ProviderStatus]] = field(default_factory=list)
    #: 自上次 snapshot() 以来新产生的告警
    alerts: list[AlertEvent] = field(default_factory=list)
    written: int = 0
    rows: int = 0
    db_bytes: int = 0
    started_at: float = 0.0
    #: 单轮耗时明显超过间隔的次数——说明这个间隔跑不动
    overruns: int = 0


class Collector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.providers: list[Provider] = build_providers()
        self.registry = Registry()
        self.engine = AlertEngine(config.thresholds, config.alert_on_vrm_flag)
        self.store = Store(config.resolve(config.db_path))

        self._lock = threading.Lock()
        self._statuses: list[tuple[str, ProviderStatus]] = []
        self._alerts: list[AlertEvent] = []
        self._sequence = 0
        self._written = 0
        self._overruns = 0
        #: 当前是否已提高系统计时器精度（必须与 timeEndPeriod 配对）
        self._timer_raised = False
        #: 上次落盘时刻（采样与入库解耦，见 log_interval_s）
        self._last_log = 0.0
        #: 上次清理过期记录的时刻
        self._last_purge = time.monotonic()
        # 已有历史数据时从现有行数起算，之后按写入量自增，避免每轮 COUNT(*)
        self._rows = self.store.rows()
        self._db_bytes = self.store.size_bytes()
        self._last_probe = 0.0

        self.started_at = time.time()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vrmmon-collector",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        # 数据源可能持有硬件句柄（LibreHardwareMonitor 的 Computer 必须关闭）
        for provider in self.providers:
            closer = getattr(provider, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        self.store.close()

    def set_paused(self, paused: bool) -> None:
        self._paused.set() if paused else self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    # ---------- 采集 ----------

    def _run(self) -> None:
        self._probe(force=True)
        # 采集循环不再新建配置/注册表类对象，把已存在的对象移出 GC 的扫描范围，
        # 减少高频采样下的 GC 停顿（实测能占掉 17% 的墙钟时间）。
        try:
            gc.freeze()
        except Exception:
            pass
        try:
            while not self._stop.is_set():
                interval = self.config.interval_s
                self._sync_timer_resolution(interval)
                started = time.monotonic()
                if not self._paused.is_set():
                    try:
                        self._tick(started)
                    except Exception as exc:  # 采集线程绝不能因单次异常退出
                        self._push_alert(AlertEvent(started, "warn", "采集异常", repr(exc)))

                # 关键：间隔应当是**周期**，不是"跑完再睡多久"。
                # 若写成 wait(interval)，实际周期 = 单轮耗时 + interval，
                # 单轮 100ms 时 50ms 间隔只会跑出约 6.7 轮/秒。
                elapsed = time.monotonic() - started
                if elapsed > interval * OVERRUN_TOLERANCE:
                    with self._lock:
                        self._overruns += 1
                self._stop.wait(max(0.0, interval - elapsed))
        finally:
            self._sync_timer_resolution(1.0)  # 无论如何都要还原

    def _sync_timer_resolution(self, interval: float) -> None:
        """间隔小就提高计时器精度、变大就还原。与 begin/end 严格配对。"""
        want = interval < TIMER_RESOLUTION_THRESHOLD_S
        if want == self._timer_raised:
            return
        _timer_resolution(want)
        self._timer_raised = want

    def _probe(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_probe < PROBE_INTERVAL_S:
            return
        self._last_probe = now
        statuses: list[tuple[str, ProviderStatus]] = []
        for provider in self.providers:
            try:
                statuses.append((provider.label, provider.probe()))
            except Exception as exc:
                statuses.append((provider.label, ProviderStatus(False, "探测异常", repr(exc))))
        with self._lock:
            self._statuses = statuses

    def _tick(self, now: float) -> None:
        seen: list[Sensor] = []
        values: dict[SensorId, float] = {}

        for provider in self.providers:
            try:
                got = provider.read(self.registry)
            except Exception as exc:
                provider.last_error = repr(exc)
                continue
            for sensor in got:
                value = sensor.last_value
                if value is None or value != value:  # None 或 NaN
                    continue
                seen.append(sensor)
                values[sensor.id] = value

        # 告警每轮都判（要抓瞬时尖峰），但落盘按 log_interval_s 节流：
        # 50ms 采样 × 768 个读数 ≈ 14,750 行/秒 ≈ 145 GB/天，磁盘扛不住。
        added = 0
        log_interval = self.config.log_interval_s
        if log_interval <= 0 or now - self._last_log >= log_interval:
            added = self.store.add(now, seen, values)
            self._last_log = now
        events = self.engine.evaluate(seen, now)

        # 保留策略：定期删掉过期记录。没有它，1 秒采样也有 3~10 GB/天，
        # 50ms 采样更会几天撑满磁盘。
        if self.config.retention_days > 0 and now - self._last_purge >= PURGE_INTERVAL_S:
            self._last_purge = now
            cutoff = now - self.config.retention_days * 86400
            removed = self.store.purge(cutoff)
            if removed:
                with self._lock:
                    self._rows = max(0, self._rows - removed)

        with self._lock:
            self._sequence += 1
            self._written += added
            self._rows += added
            self._db_bytes = self.store.size_bytes()
            self._alerts.extend(events)

        if events:
            self._probe(force=True)

    def _push_alert(self, event: AlertEvent) -> None:
        with self._lock:
            self._alerts.append(event)

    # ---------- UI 读取 ----------

    def snapshot(self) -> Snapshot:
        """返回一份不可变快照，并清空待处理的告警。"""
        with self._lock:
            alerts, self._alerts = self._alerts, []
            return Snapshot(
                timestamp=time.time(),
                sequence=self._sequence,
                paused=self._paused.is_set(),
                sensors=self.registry.all(),
                statuses=list(self._statuses),
                alerts=alerts,
                written=self._written,
                rows=self._rows,
                db_bytes=self._db_bytes,
                started_at=self.started_at,
                overruns=self._overruns,
            )

    def reset_extremes(self) -> None:
        for sensor in self.registry.all():
            sensor.clear_extremes()

    def export_csv(self, dest, since: float | None = None) -> int:
        with self._lock:
            self.store.flush()
        return self.store.export_csv(dest, since=since)

    @property
    def interval_s(self) -> float:
        return self.config.interval_s

    def set_interval(self, seconds: float) -> None:
        self.config.interval_s = clamp_interval(seconds)
