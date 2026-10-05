"""主窗口：供电健康面板 + 传感器树 + 实时曲线 + 托盘。

线程约定：控件只在主线程操作。托盘菜单回调通过 post() 投递到命令队列，
由 _drain_commands() 在主线程执行。
"""

from __future__ import annotations

import contextlib
import io
import queue
import re
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..alerts import AlertEvent
from ..classify import ROLE_LABELS
from ..config import Config, MIN_INTERVAL_S
from ..sensors import Kind, Role, Sensor, format_value
from . import motion, theme
from . import assets as ui_assets
from .theme import (ACCENT, BG, BG2, CRIT, FG, FONT_MONO_SM, LINE,
                    MUTED, NAV, SURFACE, TEXT2, TEXT4, WARN)

REFRESH_MS = 800
#: 项目根目录（标志等资源从这里找）
ROOT = Path(__file__).resolve().parent.parent.parent




class MainWindow:
    def __init__(self, root: tk.Tk, config: Config, collector,
                 start_minimized: bool = False) -> None:
        self.root = root
        self.config = config
        self.collector = collector
        self.tray = None
        self._commands: queue.Queue = queue.Queue()
        self._tree_keys: frozenset | None = None
        self._show_all = tk.BooleanVar(value=False)
        self._alert_log: list[AlertEvent] = []
        self._last_alert: AlertEvent | None = None
        self._latest_snapshot = None
        self._status_override = ""
        #: 由 shutdown 通道在另一个线程置位，_refresh 看到后优雅退出
        self.shutdown_requested = False
        #: 由唤出通道置位：重复启动时把窗口显示到前台
        self.show_requested = False
        #: 已开始退出：此后不得再排 after、不得再碰控件
        self._closing = False

        self._apply_theme()
        self._build_window()
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()

        #: 动效调度器：目前只用于告警时的状态栏脉冲
        self._motion = motion.Animator(root)

        if start_minimized:
            root.withdraw()
        self._start_tray()
        self.root.after(REFRESH_MS, self._refresh)

    # ---------- 构建 ----------

    def _apply_theme(self) -> None:
        """所有配色/字体都来自 ui/theme.py，此处不再就地写死颜色。"""
        theme.apply(self.root)

    def _build_window(self) -> None:
        self.root.title("vrmmon - 供电温度监控")
        self.root.geometry("1180x760")
        self.root.minsize(900, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_window_icon()

    def _apply_window_icon(self) -> None:
        """设置标题栏 / 任务栏图标。

        之前只给快捷方式设了图标，窗口自己从来没设过——任务栏里显示的是
        Python 默认图标，和托盘、快捷方式对不上。
        """
        icon = ui_assets.find_icon()
        if icon is not None and icon.exists():
            try:
                # default=True 让之后创建的所有 toplevel 都用它
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        mark = ui_assets.HEADER_MARK
        if mark.exists():
            try:
                self._window_icon = tk.PhotoImage(master=self.root, file=str(mark))
                self.root.iconphoto(True, self._window_icon)
            except tk.TclError:
                self._window_icon = None


    def _build_menu(self) -> None:
        # 菜单属于"浮起表面"，用 --surface 而不是主背景
        def make_menu(**kwargs):
            return tk.Menu(self.root, tearoff=0, bg=SURFACE, fg=TEXT2,
                           activebackground=ACCENT, activeforeground=FG, **kwargs)

        menubar = make_menu()
        file_menu = make_menu()
        file_menu.add_command(label="导出 CSV…", command=self.export_csv)
        file_menu.add_command(label="打开数据目录", command=self.open_data_dir)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.quit_app)
        menubar.add_cascade(label="文件", menu=file_menu)

        collect_menu = make_menu()
        collect_menu.add_command(label="暂停 / 继续", command=self.toggle_pause)
        collect_menu.add_command(label="重置极值", command=self.reset_extremes)
        menubar.add_cascade(label="采集", menu=collect_menu)

        tools_menu = make_menu()
        tools_menu.add_command(label="体检报告…", command=self.show_doctor)
        tools_menu.add_command(label="告警日志…", command=self.show_alert_log)
        tools_menu.add_separator()
        tools_menu.add_command(label="设为开机自启",
                               command=lambda: self.set_autostart(True))
        tools_menu.add_command(label="取消开机自启",
                               command=lambda: self.set_autostart(False))
        menubar.add_cascade(label="工具", menu=tools_menu)

        help_menu = make_menu()
        help_menu.add_command(label="为什么读不到供电温度？", command=self.show_explainer)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

    def _build_toolbar(self) -> None:
        # 顶栏用最暗的一级背景，靠亮度差与主内容区分开（设计系统不用描边分区）
        bar = ttk.Frame(self.root, style="Nav.TFrame", padding=(10, 7))
        bar.pack(fill="x")

        # 标志 lockup：与功耗计算器同一枚闪电标记 + LIGHTNING 字标，用强调色上色。
        # 那边 .logo 是 [mark 22px] gap 11px [wordmark 140px]，这里同比例放大到
        # 标志高 34px（字标宽度由 install_menu_shortcut.py 按同一比例预生成）。
        self._logo_images: list[tk.PhotoImage] = []
        for path, gap in ((ui_assets.HEADER_MARK, ui_assets.header_gap()),
                          (ui_assets.HEADER_WORDMARK, 18)):
            if not path.exists():
                continue
            try:
                image = tk.PhotoImage(master=bar, file=str(path))
            except tk.TclError:
                continue
            self._logo_images.append(image)
            tk.Label(bar, image=image, bg=NAV).pack(side="left", padx=(0, gap))

        self.btn_pause = ttk.Button(bar, text="暂停", width=8, command=self.toggle_pause)
        self.btn_pause.pack(side="left")
        ttk.Button(bar, text="导出 CSV", width=10,
                   command=self.export_csv).pack(side="left", padx=4)
        ttk.Button(bar, text="重置极值", width=10,
                   command=self.reset_extremes).pack(side="left")

        ttk.Label(bar, text="采样间隔", style="NavMuted.TLabel").pack(
            side="left", padx=(16, 4))
        self.var_interval = tk.StringVar(value=f"{self.config.interval_s:g}")
        spin = ttk.Spinbox(bar, from_=MIN_INTERVAL_S, to=10.0, increment=0.005, width=6,
                           textvariable=self.var_interval, command=self._on_interval)
        spin.pack(side="left")
        spin.bind("<Return>", lambda _e: self._on_interval())
        ttk.Label(bar, text="秒", style="NavMuted.TLabel").pack(side="left", padx=(4, 0))

        ttk.Checkbutton(bar, text="显示全部读数", variable=self._show_all,
                        style="Nav.TCheckbutton",
                        command=self._on_filter).pack(side="left", padx=(16, 0))
        self.var_autostart = tk.BooleanVar(value=self.is_autostart())
        ttk.Checkbutton(bar, text="开机自启", variable=self.var_autostart,
                        style="Nav.TCheckbutton",
                        command=lambda: self.set_autostart(self.var_autostart.get())
                        ).pack(side="left", padx=(12, 0))
        self.var_sound = tk.BooleanVar(value=self.config.sound)
        ttk.Checkbutton(bar, text="告警声音", variable=self.var_sound,
                        style="Nav.TCheckbutton",
                        command=self._on_sound).pack(side="left", padx=(12, 0))

        self.btn_admin = ttk.Button(bar, text="以管理员重启", width=14,
                                    command=self.restart_as_admin)
        self.btn_admin.pack(side="right")
        if self.is_elevated():
            self.btn_admin.configure(text="已提权", state="disabled")

        theme.rule(self.root, LINE).pack(fill="x")

    def _build_body(self) -> None:
        """只剩传感器表——曲线与健康面板已按要求移除。"""
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True, padx=10, pady=(8, 6))

        header_row = tk.Frame(shell, bg=BG)
        header_row.pack(fill="x")
        header = theme.section_header(header_row, "传感器", background=BG)
        header.pack(side="left")
        self.count_label = tk.Label(header_row, text="", bg=BG, fg=MUTED,
                                    font=FONT_MONO_SM)
        self.count_label.pack(side="right")

        # 4px 红条：设计系统里"关键值"的指示条
        theme.accent_bar(shell, height=4).pack(fill="x", pady=(6, 8))

        tree_shell = tk.Frame(shell, bg=BG2)
        tree_shell.pack(fill="both", expand=True)

        columns = ("cur", "min", "max", "role")
        self.tree = ttk.Treeview(tree_shell, columns=columns, show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="传感器 / 分组")
        self.tree.column("#0", width=380, anchor="w", stretch=True)
        for key, title, width in (("cur", "当前", 96), ("min", "最小", 88),
                                  ("max", "最大", 88), ("role", "角色", 120)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="center", stretch=False)
        self.tree.column("role", anchor="w")
        # 配色只表达语义：红=告警、橙=供电、灰=无信息
        self.tree.tag_configure("vrm", foreground=WARN)
        self.tree.tag_configure("alarm_on", foreground=CRIT)
        self.tree.tag_configure("alarm_off", foreground=TEXT4)
        self.tree.tag_configure("temp", foreground=TEXT2)
        self.tree.tag_configure("other", foreground=TEXT4)
        self.tree.tag_configure("group", foreground=MUTED)

        scroll = ttk.Scrollbar(tree_shell, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
    def _build_status(self) -> None:
        bar = tk.Frame(self.root, bg=NAV)
        bar.pack(fill="x", side="bottom")
        self._status_bar = bar
        # 状态栏全是数字，用等宽字体保证对齐
        self.status_label = tk.Label(bar, text="正在启动…", bg=NAV, fg=MUTED,
                                     anchor="w", font=FONT_MONO_SM)
        self.status_label.pack(fill="x", padx=12, pady=5)
        # 先 pack 状态栏再 pack 分隔线，线才会落在状态栏上方
        theme.rule(self.root, LINE).pack(fill="x", side="bottom")

    def _start_tray(self) -> None:
        from .. import logging_setup
        try:
            from .tray import Tray
            self.tray = Tray(self)
            self.tray.start()
        except Exception as exc:
            self.tray = None
            logging_setup.log_exception(*sys.exc_info(), context="托盘启动")
            self._status_override = f"托盘不可用: {exc}"
            return
        # 后台线程里抛的异常传不回来，只能用"图标是否真的出现"判断
        if not self.tray.wait_visible(3.0):
            logging_setup.log_message(
                "托盘图标 3 秒内未出现，按不可用处理；关闭窗口将直接退出", "托盘")
            self.tray.stop()
            self.tray = None
            self._status_override = "托盘图标未能显示，关闭窗口将直接退出"

    # ---------- 主线程命令队列 ----------

    def post(self, callback) -> None:
        """供托盘等外部线程投递命令到主线程。"""
        self._commands.put(callback)

    #: 状态栏临时消息的存活时间（秒）
    STATUS_OVERRIDE_TTL = 8.0

    @property
    def _status_override(self) -> str:
        return self.__status_override

    @_status_override.setter
    def _status_override(self, text: str) -> None:
        """临时消息带过期时间，过一会儿自动切回实时状态。"""
        self.__status_override = text
        self._status_override_until = time.time() + self.STATUS_OVERRIDE_TTL

    def _drain_commands(self) -> None:
        while True:
            try:
                callback = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                callback()
            except Exception as exc:
                self._status_override = f"命令失败: {exc}"

    # ---------- 刷新 ----------

    def _refresh(self) -> None:
        """刷新入口。异常必须被吞掉并落盘，否则 after 链会断、界面就此冻死。

        注意退出顺序：root.destroy() 之后挂起的 after 回调仍可能被派发一次，
        那时控件已不存在，任何操作都会抛 TclError。所以退出后既不能再碰控件，
        也不能再排下一次 after。
        """
        if self._closing:
            return
        if self.shutdown_requested:
            self.quit_app("收到 vrmmon stop 信号")
            return
        if self.show_requested:
            self.show_requested = False
            self.show_window()
        try:
            self._refresh_once()
        except tk.TclError:
            # 窗口正在销毁，正常收尾，不要再刷
            self._closing = True
            return
        except Exception:
            from .. import logging_setup
            logging_setup.log_exception(*sys.exc_info(), context="界面刷新")
            self._status_override = f"界面刷新异常，详见 {logging_setup.LOG_PATH.name}"
        if self._closing:
            return
        try:
            self.root.after(REFRESH_MS, self._refresh)
        except tk.TclError:
            self._closing = True


    def _refresh_once(self) -> None:
        self._drain_commands()
        snapshot = self.collector.snapshot()

        self._latest_snapshot = snapshot
        sensors = snapshot.sensors
        visible = self._visible_sensors(sensors)
        self._sync_tree(visible)
        self._update_tree(visible)
        self._update_count(visible, sensors)
        self._handle_alerts(snapshot.alerts)
        self._update_tray(snapshot)
        self._update_status(snapshot)
    def _visible_sensors(self, sensors: list[Sensor]) -> list[Sensor]:
        if self._show_all.get():
            return sensors
        # 默认隐藏"名似温度但不是温度"的读数（差值/阈值/分辨率），避免污染视图
        return [s for s in sensors
                if (s.kind is Kind.TEMPERATURE and s.role is not Role.META)
                or s.is_alarm or s.is_vrm]

    def _sync_tree(self, sensors: list[Sensor]) -> None:
        keys = frozenset((s.id.provider, s.id.group, s.id.name, s.id.kind)
                         for s in sensors)
        if keys == self._tree_keys:
            return
        self._tree_keys = keys
        self.tree.delete(*self.tree.get_children())
        groups: dict[str, str] = {}
        for sensor in sorted(sensors, key=lambda s: (s.id.provider, s.id.group, s.id.name)):
            gkey = f"G|{sensor.id.provider}|{sensor.id.group}"
            parent = groups.get(gkey)
            if parent is None:
                parent = self.tree.insert(
                    "", "end", iid=gkey, open=True,
                    text=f"{sensor.id.group}  [{sensor.id.provider}]",
                    values=("", "", "", ""), tags=("group",))
                groups[gkey] = parent
            self.tree.insert(parent, "end", iid=str(sensor.id), text=sensor.id.name,
                             values=("", "", "", ""), tags=self._tags(sensor))

    @staticmethod
    def _tags(sensor: Sensor) -> tuple[str, ...]:
        if sensor.is_alarm:
            return ("alarm_on",) if sensor.triggered else ("alarm_off",)
        if sensor.is_vrm:
            return ("vrm",)
        if sensor.kind is Kind.TEMPERATURE:
            return ("temp",)
        return ("other",)

    def _update_count(self, visible: list[Sensor], all_sensors: list[Sensor]) -> None:
        """右上角计数：当前视图里有几项、其中几个是温度。"""
        temps = sum(1 for s in visible if s.kind is Kind.TEMPERATURE)
        self.count_label.config(
            text=f"{len(visible)} 项 / {temps} 个温度（全部读数 {len(all_sensors)}）")

    def _update_tree(self, sensors: list[Sensor]) -> None:
        for sensor in sensors:
            iid = str(sensor.id)
            if not self.tree.exists(iid):
                continue
            low, high = sensor.bounds()
            self.tree.set(iid, "cur", format_value(sensor.last_value, sensor.unit))
            self.tree.set(iid, "min", format_value(low, sensor.unit))
            self.tree.set(iid, "max", format_value(high, sensor.unit))
            self.tree.set(iid, "role", ROLE_LABELS.get(sensor.role, sensor.role.value))
            self.tree.item(iid, tags=self._tags(sensor))

    # ---------- 告警 ----------

    def _handle_alerts(self, events: list[AlertEvent]) -> None:
        for event in events:
            self._alert_log.append(event)
            self._last_alert = event
            self._pulse_status(event.is_severe)
            if event.is_severe:
                self._beep()
                if self.tray is not None:
                    self.tray.notify(event.message, f"vrmmon · {event.title}")

    def _pulse_status(self, severe: bool) -> None:
        """告警时状态栏脉冲几下——对应设计里的 psu-ui-feedback（控件改动反馈）。

        用背景色往返插值，不动文字，结束后精确回到常态色。
        """
        bar = getattr(self, "_status_bar", None)
        if bar is None or not motion.animations_enabled(self.config):
            return
        peak = CRIT if severe else WARN
        cycles, duration = 3, 900

        def frame(progress: float) -> None:
            # 三角波：0→1→0 走 cycles 次，整体随时间衰减，收得干净
            phase = progress * cycles
            triangle = 1.0 - abs((phase % 2.0) - 1.0)
            strength = triangle * (1.0 - progress * 0.4) * 0.45
            colour = motion.lerp_color(NAV, peak, strength)
            bar.configure(bg=colour)
            self.status_label.configure(bg=colour)

        def done() -> None:
            bar.configure(bg=NAV)
            self.status_label.configure(bg=NAV)

        self._motion.tween(duration, frame, on_done=done, ease=motion.LINEAR)

    def _beep(self) -> None:
        if not self.var_sound.get():
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass

    # ---------- 托盘 ----------

    def _update_tray(self, snapshot) -> None:
        if self.tray is None:
            return
        cpu = self._best(snapshot.sensors, (Role.CPU_VRM, Role.CPU_PACKAGE,
                                            Role.CPU_CORE, Role.CPU_SOC))
        gpu = self._best(snapshot.sensors, (Role.GPU_VRM, Role.GPU_HOTSPOT,
                                            Role.GPU_MEMORY, Role.GPU_CORE))
        overall = max([v for v in (cpu, gpu) if v is not None], default=None)

        warn = self.config.thresholds.warn
        critical = self.config.thresholds.critical
        if overall is None:
            level = "stale"
        elif overall >= critical:
            level = "critical"
        elif overall >= warn:
            level = "warn"
        else:
            level = "ok"

        vrm = [s for s in snapshot.sensors if s.is_vrm]
        if vrm:
            vrm_text = "供电温度 " + ", ".join(
                f"{s.id.name} {format_value(s.last_value, s.unit)}" for s in vrm[:3])
        else:
            vrm_text = "供电温度：本机未暴露"
        paused = "（已暂停）" if snapshot.paused else ""
        # 图标现在只表达状态色，"是几度"放在悬停提示里
        tooltip = (f"vrmmon{paused}\n"
                   f"最高 {format_value(overall, '°C')}\n"
                   f"CPU {format_value(cpu, '°C')}   GPU {format_value(gpu, '°C')}\n"
                   f"{vrm_text}")
        self.tray.update(tooltip, level)

    @staticmethod
    def _best(sensors: list[Sensor], roles: tuple[Role, ...]) -> float | None:
        values = [s.last_value for s in sensors
                  if s.role in roles and s.last_value is not None]
        return max(values) if values else None

    # ---------- 状态栏 ----------


    def _update_status(self, snapshot) -> None:
        if self._status_override and time.time() < self._status_override_until:
            self.status_label.config(text=self._status_override, fg=WARN)
            return
        self._status_override = ""

        statuses = " | ".join(f"{label}: {status.state}"
                              for label, status in snapshot.statuses)
        uptime = max(1.0, time.time() - snapshot.started_at)
        hours, remainder = divmod(int(uptime), 3600)
        size_mb = snapshot.db_bytes / (1024 * 1024)

        # 把"这个采样频率一天要吃掉多少磁盘"直接摆出来，别让它默默增长
        rate = snapshot.written / uptime
        row_bytes = snapshot.db_bytes / snapshot.rows if snapshot.rows else 64.0
        gb_per_day = rate * 86400 * row_bytes / (1024 ** 3)

        # 实际采样速率 vs 设定值。间隔调得很小时，一眼能看出有没有跑得动。
        # 用累计平均而不是瞬时值：单轮偶尔被系统调度拖慢是正常的，
        # 累计平均才能反映"到底跟不跟得上"。
        actual_rate = snapshot.sequence / uptime
        target = self.config.interval_s
        behind = target > 0 and actual_rate < 0.9 / target
        sampling = (f"采样 {actual_rate:.0f}/秒"
                    + (f"（设定 {1 / target:.0f}）" if target > 0 else "（不间歇）"))

        text = (f"{statuses}    |    已采集 {snapshot.written} 点 / 入库 {snapshot.rows} 行"
                f" ({size_mb:.1f} MB)    |    {sampling}"
                f"    |    {rate:.0f} 行/秒 ≈ {gb_per_day:.2f} GB/天"
                f"    |    运行 {hours:d}:{remainder // 60:02d}"
                + ("    |    已暂停" if snapshot.paused else ""))
        self.status_label.config(
            text=text,
            fg=WARN if (gb_per_day >= 1.0 or behind) else MUTED)
    def _on_interval(self) -> None:
        try:
            seconds = float(self.var_interval.get())
        except ValueError:
            self.var_interval.set(f"{self.collector.interval_s:g}")
            return
        self.collector.set_interval(seconds)
        self.config.interval_s = self.collector.interval_s
        self.var_interval.set(f"{self.collector.interval_s:g}")

    def _on_filter(self) -> None:
        self._tree_keys = None  # 强制重建

    def _on_sound(self) -> None:
        self.config.sound = self.var_sound.get()

    def toggle_pause(self) -> None:
        paused = not self.collector.paused
        self.collector.set_paused(paused)
        self.btn_pause.config(text="继续" if paused else "暂停")

    def is_paused(self) -> bool:
        return self.collector.paused

    def reset_extremes(self) -> None:
        self.collector.reset_extremes()
        self._status_override = "极值已重置"

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def is_autostart(self) -> bool:
        from .. import autostart
        try:
            return autostart.is_enabled()
        except Exception:
            return False

    def set_autostart(self, enabled: bool) -> None:
        from .. import autostart
        try:
            autostart.set_enabled(enabled)
            self.var_autostart.set(enabled)
            self._status_override = f"开机自启已{'启用' if enabled else '取消'}"
        except Exception as exc:
            self.var_autostart.set(not enabled)
            self._status_override = f"设置开机自启失败: {exc}"

    def toggle_autostart(self) -> None:
        self.set_autostart(not self.is_autostart())

    def is_elevated(self) -> bool:
        from .. import elevation
        return elevation.is_elevated()

    def restart_as_admin(self) -> None:
        from .. import elevation
        started, message = elevation.relaunch_elevated(minimized=False)
        if not started:
            self._status_override = message
            return
        self._status_override = "已发起提权重启，本窗口即将关闭"
        # 先让旧实例退出，避免两个实例同时写库
        self.quit_app("发起提权重启")

    # ---------- 文件操作 ----------

    def export_csv(self) -> None:
        export_dir = self.config.resolve(self.config.export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        default = export_dir / time.strftime("vrmmon_%Y%m%d_%H%M%S.csv")
        path = filedialog.asksaveasfilename(
            title="导出 CSV", initialdir=str(export_dir),
            initialfile=default.name, defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            count = self.collector.export_csv(Path(path))
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self._status_override = f"已导出 {count} 行到 {path}"
        messagebox.showinfo("导出完成", f"已导出 {count} 行到\n{path}", parent=self.root)

    def open_data_dir(self) -> None:
        import os
        directory = self.config.resolve(self.config.db_path).parent
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(directory)  # noqa: S606 - 打开资源管理器
        except Exception as exc:
            self._status_override = f"打开目录失败: {exc}"

    # ---------- 报告 ----------

    def _text_window(self, title: str, content: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("900x600")
        window.configure(bg=BG)
        text = tk.Text(window, bg=BG2, fg=FG, insertbackground=FG, wrap="none",
                       font=("Consolas", 9), borderwidth=0)
        scroll = ttk.Scrollbar(window, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", content)
        text.config(state="disabled")

    def show_doctor(self) -> None:
        from .. import doctor
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor.main(["--all", "--names-only"])
        self._text_window("体检报告", buffer.getvalue())

    def show_alert_log(self) -> None:
        if not self._alert_log:
            content = "暂无告警。"
        else:
            content = "\n".join(
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e.timestamp))}"
                f"  [{e.level.upper():8}] {e.title}: {e.message}"
                for e in self._alert_log)
        self._text_window("告警日志", content)

    def show_explainer(self) -> None:
        messagebox.showinfo(
            "为什么需要管理员权限？",
            "CPU 温度来自 CPU 内部的 DTS，要读 MSR 寄存器；主板温度来自 Super I/O\n"
            "或 EC 芯片，要读端口 I/O。这两类操作都只能在 ring0 完成，Windows 不允许\n"
            "普通进程做——所有同类框架（PawnIO、WinRing0、HWiNFO 自带驱动）都把设备\n"
            "访问限定给管理员。实测 \\\\.\\PawnIO 对非提权进程返回 ACCESS_DENIED，\n"
            "所以这是 Windows 的硬约束，不是本程序的缺陷。\n\n"
            "不提权时，本程序仍然能独立读到（不需要任何第三方软件）：\n"
            "  显卡核心温度、显存结温、硬盘温度 —— 这些走 NVAPI / SMART，是用户态接口。\n\n"
            "以管理员身份运行后，LibreHardwareMonitor 的内核驱动（走已安装的 PawnIO\n"
            "框架）会生效，即可读到 CPU 每核温度、封装温度、主板 Super I/O 温度、\n"
            "内存 SPD 温度。\n\n"
            "关于「供电温度」：主板 VRM 温度没有标准接口，要靠主板厂的私有映射。\n"
            "本机（ASUS G835LW 笔记本）实测 HWiNFO 与 LHM 都没有 VRM 通道；\n"
            "提权后如果 LHM 的 Super I/O 映射表里有 VRM 通道，会自动显示出来。\n\n"
            "读不到时，本程序提供两种替代观测：\n"
            "  1. 供电过热报警：CPU 自己上报的 VR/过热事件标记，直接反映供电过热。\n"
            "  2. 热负荷代理：VRM 输出电流 + CPU 封装/核心温度，可判断供电散热是否吃紧。",
            parent=self.root)

    # ---------- 退出 ----------

    def _on_close(self) -> None:
        if self.config.minimize_to_tray and self.tray is not None:
            self.root.withdraw()
            self.tray.notify("已最小化到托盘，仍在后台记录", "vrmmon")
        elif self.config.minimize_to_tray:
            self.quit_app("关闭窗口（托盘不可用，无法最小化）")
        else:
            self.quit_app("关闭窗口")

    def quit_app(self, reason: str = "用户操作") -> None:
        if self._closing:
            return
        self._closing = True

        from .. import logging_setup
        logging_setup.log_message(f"正常退出（原因：{reason}）", "退出")

        try:
            self.config.interval_s = self.collector.interval_s
            from .. import config as config_module
            config_module.save(self.config)
        except Exception:
            pass
        if self.tray is not None:
            self.tray.stop()
        try:
            self.collector.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
