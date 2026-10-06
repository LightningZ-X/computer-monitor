"""托盘图标：与顶栏标志、应用图标同一枚闪电标记，用状态色区分告警级别。

温度数值放在悬停提示里（图标本身保持标记识别度，不再把数字画在图标上）。
菜单回调运行在 pystray 自己的线程里，所以一律通过 window.post() 转交主线程，
不直接碰 tkinter 控件。
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache

import pystray
from PIL import Image

from .assets import MARK_SOURCE

#: 标记底色。与 ui/theme.py 的状态色保持同一套 token（ROG 设计系统）
LEVEL_COLORS = {
    "ok": (22, 163, 74),        # --ok        #16a34a
    "warn": (224, 134, 0),      # --warn      #e08600
    "critical": (255, 0, 51),   # --rog       #ff0033
    # 无数据：用 --text-4 而不是 --surface-2。深色任务栏上 #202020 几乎看不见
    "stale": (125, 125, 125),   # --text-4    #7d7d7d
}

ICON_SIZE = 64


@lru_cache(maxsize=4)
def _mark_mask(height: int) -> Image.Image:
    """取标记的 alpha 通道并缩放到指定高度。结果缓存，避免每次换色都读盘。"""
    source = Image.open(MARK_SOURCE).convert("RGBA")
    scale = min(height / source.height, height / source.width)
    width = max(1, round(source.width * scale))
    return source.getchannel("A").resize((width, height), Image.LANCZOS)


@lru_cache(maxsize=4)
def make_icon(level: str) -> Image.Image:
    """托盘图标 = 同一枚闪电标记 + 状态色。与顶栏标志、应用图标视觉一致。"""
    colour = LEVEL_COLORS.get(level, LEVEL_COLORS["stale"])
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    if not MARK_SOURCE.exists():
        # 标记缺失时退化成一块状态色，至少还能看出告警级别
        image.paste(colour + (255,), (8, 8, ICON_SIZE - 8, ICON_SIZE - 8))
        return image

    mask = _mark_mask(int(ICON_SIZE * 0.84))
    tinted = Image.new("RGBA", mask.size, colour + (0,))
    tinted.putalpha(mask)
    image.alpha_composite(tinted, ((ICON_SIZE - mask.width) // 2,
                                   (ICON_SIZE - mask.height) // 2))
    return image


class Tray:
    def __init__(self, window) -> None:
        self.window = window
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None
        self._last_key: tuple | None = None

    def _menu(self) -> pystray.Menu:
        window = self.window
        return pystray.Menu(
            pystray.MenuItem("显示主窗口", lambda: window.post(window.show_window),
                             default=True),
            pystray.MenuItem(
                lambda item: "继续采集" if window.is_paused() else "暂停采集",
                lambda: window.post(window.toggle_pause),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("导出 CSV", lambda: window.post(window.export_csv)),
            pystray.MenuItem(
                "开机自启",
                lambda: window.post(window.toggle_autostart),
                checked=lambda item: window.is_autostart(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: window.post(
                lambda: window.quit_app("托盘菜单退出"))),
        )

    def start(self) -> None:
        self._icon = pystray.Icon("vrmmon", make_icon("stale"),
                                  "VELTRIX Monitor · 温度监控", self._menu())
        self._thread = threading.Thread(target=self._icon.run, name="vrmmon-tray",
                                        daemon=True)
        self._thread.start()

    def wait_visible(self, timeout: float = 3.0) -> bool:
        """等图标真正出现。

        pystray 在后台线程里跑，线程内抛的异常不会传回主线程。只能靠
        Icon.visible 判断它到底起没起来——否则窗口"最小化到托盘"却没有图标，
        用户会以为程序死了（实际还在后台记录，且再也找不回窗口）。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._icon is not None and getattr(self._icon, "visible", False):
                return True
            time.sleep(0.1)
        return False

    def update(self, tooltip: str, level: str) -> None:
        if self._icon is None:
            return
        key = (tooltip, level)
        if key == self._last_key:
            return
        try:
            if self._last_key is None or level != self._last_key[1]:
                self._icon.icon = make_icon(level)
            if self._last_key is None or tooltip != self._last_key[0]:
                self._icon.title = tooltip
            self._last_key = key
        except Exception:  # 托盘不可用时不应影响采集
            pass

    def notify(self, message: str, title: str = "vrmmon") -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
