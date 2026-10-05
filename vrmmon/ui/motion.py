"""动效基础设施：缓动曲线、颜色插值、reduced-motion 检测、补间调度。

对齐功耗计算器的动效契约（`docs/DESIGN.md` 第 2 节）：

  · 曲线就用那边 CSS 定义的同名三条：`--ease` / `--motion-enter` / `--motion-fast`，
    这里把 cubic-bezier 按同样四个分量实现，不另调。
  · **必须保留 reduced-motion 归零**——这不是可选项。本程序额外认
    Windows 的「在 Windows 中显示动画」开关，以及 config.animations。
  · tkinter 没有内建动画，只能靠 after() 自绘。所有补间都按**真实流逝时间**
    算进度，掉帧只会跳帧，不会让动画变慢（与网页用 RAF 同理）。
"""

from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from typing import Callable

# ---------------------------------------------------------------- 缓动曲线


def _bezier_axis(t: float, p1: float, p2: float) -> float:
    """三次贝塞尔在某一维上的取值（端点固定为 0 与 1）。"""
    inv = 1.0 - t
    return 3.0 * inv * inv * t * p1 + 3.0 * inv * t * t * p2 + t ** 3


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """把 CSS 的 cubic-bezier(x1,y1,x2,y2) 变成 x→y 的缓动函数。

    给定进度 x 求对应 y，需要先反解 t。二分 24 次足够（误差 < 1e-7）。
    """
    def ease(x: float) -> float:
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        low, high = 0.0, 1.0
        for _ in range(24):
            mid = (low + high) / 2.0
            if _bezier_axis(mid, x1, x2) < x:
                low = mid
            else:
                high = mid
        return _bezier_axis((low + high) / 2.0, y1, y2)
    return ease


#: 与 style.css 的变量一一对应
EASE = cubic_bezier(0.40, 0.00, 0.20, 1.00)     # --ease
ENTER = cubic_bezier(0.16, 1.00, 0.30, 1.00)    # --motion-enter
FAST = cubic_bezier(0.20, 0.00, 0.20, 1.00)     # --motion-fast
EXIT = cubic_bezier(0.40, 0.00, 1.00, 1.00)     # --motion-exit
LINEAR: Callable[[float], float] = lambda x: x


# ---------------------------------------------------------------- 插值


def lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16)


def lerp_color(start: str, end: str, t: float) -> str:
    """在两个 #rrggbb 之间线性插值。tkinter 的 Canvas/控件颜色都是字符串，
    所以"淡入淡出"只能靠朝背景色插值来模拟（Canvas 不支持逐项 alpha）。"""
    if t <= 0.0:
        return start
    if t >= 1.0:
        return end
    a, b = rgb(start), rgb(end)
    return "#%02x%02x%02x" % (
        round(lerp(a[0], b[0], t)),
        round(lerp(a[1], b[1], t)),
        round(lerp(a[2], b[2], t)),
    )


# ---------------------------------------------------------------- reduced motion

#: SPI_GETCLIENTAREAANIMATION——Windows「在 Windows 中显示动画」开关
_SPI_GETCLIENTAREAANIMATION = 0x0042


def system_prefers_reduced_motion() -> bool:
    """Windows 关闭「显示动画」时返回 True。取不到就当作不要求归零。"""
    if sys.platform != "win32":
        return False
    try:
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        return bool(ok) and not enabled.value
    except Exception:
        return False


def animations_enabled(config=None) -> bool:
    """动效总开关：配置关掉、或系统要求 reduce，就整段归零（不是"变快"）。"""
    if config is not None and not getattr(config, "animations", True):
        return False
    return not system_prefers_reduced_motion()


# ---------------------------------------------------------------- 补间调度


class Animator:
    """在某个 tkinter 控件上调度补间。

    用真实流逝时间计算进度；可整体取消，窗口销毁后不再回调。
    """

    #: 目标帧率上限。再高对 tkinter 没意义，只是白烧 CPU。
    FPS = 60

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self._tokens: set[object] = set()
        #: 补间回调抛 TclError 时只记一次日志：不能让它静默消失，
        #: 否则动画"停在半路"会变成一个查不出原因的现象。
        self._logged_error = False

    @property
    def busy(self) -> bool:
        return bool(self._tokens)

    def _note_tcl_error(self, where: str) -> None:
        if self._logged_error:
            return
        self._logged_error = True
        try:
            import sys

            from .. import logging_setup
            logging_setup.log_exception(*sys.exc_info(), context=f"动效回调（{where}）")
        except Exception:
            pass

    def tween(self, duration_ms: float, on_frame: Callable[[float], None],
              on_done: Callable[[], None] | None = None,
              ease: Callable[[float], float] = EASE) -> object | None:
        """在 duration_ms 内把 0→1 的缓动进度喂给 on_frame。"""
        if duration_ms <= 0:
            on_frame(1.0)
            if on_done is not None:
                on_done()
            return None

        token = object()
        started = time.monotonic()
        interval = max(8, int(1000 / self.FPS))

        def step() -> None:
            if token not in self._tokens:
                return
            raw = min(1.0, (time.monotonic() - started) * 1000.0 / duration_ms)
            try:
                on_frame(ease(raw))
            except tk.TclError:      # 控件已销毁
                self._tokens.discard(token)
                self._note_tcl_error("补间")
                return
            if raw >= 1.0:
                self._tokens.discard(token)
                if on_done is not None:
                    on_done()
                return
            self.widget.after(interval, step)

        self._tokens.add(token)
        self.widget.after(interval, step)
        return token

    def after(self, delay_ms: float, callback: Callable[[], None]) -> object:
        """延时执行，同样纳入统一取消。"""
        token = object()

        def run() -> None:
            if token not in self._tokens:
                return
            self._tokens.discard(token)
            try:
                callback()
            except tk.TclError:
                pass

        self._tokens.add(token)
        self.widget.after(max(0, int(delay_ms)), run)
        return token

    def cancel_all(self) -> None:
        self._tokens.clear()
