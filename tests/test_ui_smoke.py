"""界面冒烟测试：构建真实窗口、喂入真实采集快照，核对控件内容。

比截图更可靠——直接断言控件里的文字、表格行与状态栏。
运行：python tests/test_ui_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vrmmon.collector import Collector  # noqa: E402
from vrmmon.config import Config  # noqa: E402
from vrmmon.ui import main_window as mw  # noqa: E402
from vrmmon.ui.theme import ACCENT, NAV  # noqa: E402


def dump(label: str, value) -> None:
    print(f"  {label}: {value}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    failures: list[str] = []

    def check(condition: bool, description: str) -> None:
        print(f"  [{'PASS' if condition else 'FAIL'}] {description}")
        if not condition:
            failures.append(description)

    # ignore_cleanup_errors：断言中途失败时 collector 还没停，SQLite 文件仍被占用，
    # 清理会再抛一个 PermissionError 把真正的异常盖住（踩过）。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        config = Config()
        config.db_path = str(Path(tmp) / "ui.sqlite3")
        config.interval_s = 0.5
        config.sound = False
        config.minimize_to_tray = False
        # 兜底：万一走到退出路径，也绝不写真实的 config.json
        config.save_path = str(Path(tmp) / "config.json")

        collector = Collector(config)
        collector.start()

        # LHM 冷启动要枚举全部硬件（数秒），不能用固定等待
        deadline = time.time() + 40
        snapshot = collector.snapshot()
        while time.time() < deadline and snapshot.written == 0:
            time.sleep(1.0)
            snapshot = collector.snapshot()
        time.sleep(2.5)
        snapshot = collector.snapshot()

        root = tk.Tk()
        root.geometry("1100x720")
        with mock.patch.object(mw.MainWindow, "_start_tray", lambda self: None):
            window = mw.MainWindow(root, config, collector)
        window.tray = None
        root.update_idletasks()
        root.update()
        window._refresh()
        root.update_idletasks()
        root.update()

        print("=" * 72)
        print("窗口")
        print("=" * 72)
        dump("tk 版本", root.tk.call("info", "patchlevel"))
        dump("窗口尺寸", f"{root.winfo_width()}x{root.winfo_height()}")
        logos = getattr(window, "_logo_images", [])
        dump("顶栏 lockup", " + ".join(f"{i.width()}x{i.height()}" for i in logos)
             or "未加载")
        check(len(logos) == 2, "顶栏是完整 lockup（闪电标记 + LIGHTNING 字标）")

        print("=" * 72)
        print("已移除的部分（回归保护）")
        print("=" * 72)
        check(not hasattr(window, "plot"), "温度曲线已移除")
        check(not hasattr(window, "health_labels"), "健康面板已移除")
        check(not hasattr(window, "health_badge"), "健康徽标已移除")
        canvases = sum(1 for child in root.winfo_children()
                       if isinstance(child, tk.Canvas))
        dump("窗口内的画布数量", canvases)
        check(canvases == 0, "窗口里没有任何画布控件")
        check(not hasattr(window, "plot"), "曲线用的画布类已不存在")
        check(not hasattr(window, "_boot"), "启动动画已删除")

        print("=" * 72)
        print("传感器表")
        print("=" * 72)
        children = window.tree.get_children()
        dump("分组数", len(children))
        row_count = sum(len(window.tree.get_children(group)) for group in children)
        dump("传感器行数", row_count)
        dump("右上角计数", window.count_label.cget("text"))
        for group in children[:3]:
            print(f"  ├─ {window.tree.item(group, 'text')}"
                  f"  ({len(window.tree.get_children(group))} 行)")
        shown = 0
        for group in children:
            for iid in window.tree.get_children(group):
                role = window.tree.set(iid, "role")
                if role in ("CPU 封装", "GPU 热点", "GPU 显存", "供电过热报警"):
                    print(f"  │   {window.tree.item(iid, 'text'):<24}"
                          f" 当前={window.tree.set(iid, 'cur'):>8}"
                          f" 最大={window.tree.set(iid, 'max'):>8} 角色={role}")
                    shown += 1
        has_hwinfo = any(s.id.provider == "hwinfo"
                         for s in window._latest_snapshot.sensors)
        source_note = "HWiNFO 在运行" if has_hwinfo else "仅独立数据源"
        check(row_count > 0, f"温度视图有行 ({row_count} 行, {source_note})")
        check(shown > 0, f"出现供电/热状况相关行 ({shown} 条)")
        check("项 /" in window.count_label.cget("text"), "右上角计数已填充")

        meta_rows = [iid for group in children
                     for iid in window.tree.get_children(group)
                     if window.tree.set(iid, "role") == "非温度读数"]
        check(not meta_rows, f"默认视图排除了伪温度（{len(meta_rows)} 行泄漏）")

        print("=" * 72)
        print("状态栏")
        print("=" * 72)
        status = window.status_label.cget("text")
        dump("内容", status)
        check("LibreHardwareMonitor" in status or "HWiNFO" in status,
              "状态栏显示数据源状态")
        check("GB/天" in status, "状态栏显示磁盘增长估算")
        check("曲线" not in status, "状态栏不再提曲线")

        print("=" * 72)
        print("图标一致性（窗口 / 托盘 / 标志必须同一套语言）")
        print("=" * 72)
        from vrmmon.ui import tray as tray_module  # noqa: PLC0415

        check(getattr(window, "_window_icon", None) is not None,
              "窗口/任务栏图标已设置（此前从未设置过）")
        check(tray_module.MARK_SOURCE.exists(), "闪电标记源文件存在")
        icon = tray_module.make_icon("ok")
        dump("托盘图标尺寸", icon.size)
        pixels = icon.load()
        assert pixels is not None
        colours = {(pixels[x, y][0], pixels[x, y][1], pixels[x, y][2])
                   for y in range(icon.height) for x in range(icon.width)
                   if pixels[x, y][3] > 200}
        dump("托盘图标不透明像素的颜色", colours)
        check(colours == {(22, 163, 74)},
              "托盘图标 = 闪电标记 + 状态色（不再是数字方块）")

        print("=" * 72)
        print("动效（含 reduced-motion 归零）")
        print("=" * 72)
        from vrmmon.ui import motion  # noqa: PLC0415

        check(abs(motion.EASE(0.0)) < 1e-9 and abs(motion.EASE(1.0) - 1.0) < 1e-9,
              "缓动曲线端点为 0 / 1")
        check(motion.ENTER(0.25) > 0.7, "ENTER 曲线是快进慢出（与 CSS 一致）")
        check(all(motion.EASE(i / 20) <= motion.EASE((i + 1) / 20) + 1e-9
                  for i in range(20)), "缓动曲线单调不回弹")
        dump("系统要求归零", motion.system_prefers_reduced_motion())

        # 启动动画已按要求删除：不得再有 boot 模块或相关控件
        import importlib.util  # noqa: PLC0415

        check(importlib.util.find_spec("vrmmon.ui.boot") is None,
              "ui/boot.py 已删除")
        check(not hasattr(window, "_boot"), "窗口上没有启动动画对象")
        check(not window._motion.busy, "窗口构建后没有挂着的补间")
        canvases_after = sum(1 for child in root.winfo_children()
                             if isinstance(child, tk.Canvas))
        check(canvases_after == 0, "窗口内画布数始终为 0")

        # 残留的告警脉冲：触发一次，确认会走完并精确回到常态色
        window._pulse_status(False)
        check(window._motion.busy, "告警脉冲已启动")
        deadline = time.time() + 5
        while time.time() < deadline and window._motion.busy:
            root.update()
            time.sleep(0.016)
        check(not window._motion.busy, "告警脉冲已走完")
        dump("脉冲后状态栏底色", window._status_bar.cget("bg"))
        check(window._status_bar.cget("bg") == NAV, "脉冲结束后底色精确回到 NAV")

        # 归零路径：animations=False 时不得有任何补间
        quiet = Config()
        quiet.db_path = str(Path(tmp) / "ui2.sqlite3")
        quiet.save_path = ""
        quiet.animations = False
        quiet.sound = False
        quiet.minimize_to_tray = False
        quiet_root = tk.Tk()
        quiet_root.geometry("900x600")
        with mock.patch.object(mw.MainWindow, "_start_tray", lambda self: None):
            quiet_window = mw.MainWindow(quiet_root, quiet, collector)
        quiet_window.tray = None
        quiet_root.update_idletasks()
        quiet_root.update()
        quiet_window._pulse_status(True)
        dump("归零时的补间状态", quiet_window._motion.busy)
        check(not quiet_window._motion.busy, "animations=False 时不排任何补间")
        quiet_root.destroy()

        print("=" * 72)
        print("数值精度（对齐 HWiNFO 惯例）")
        print("=" * 72)
        from vrmmon.sensors import decimals_for, format_value  # noqa: PLC0415

        cases = (("°C", 61.76, 1), ("W", 41.254, 2), ("V", 0.688, 3),
                 ("A", 12.345, 3), ("RPM", 1234.6, 0), ("MHz", 2285.24, 0),
                 ("%", 37.25, 1), ("", 123.456, 1))
        for unit, raw, want in cases:
            got = format_value(raw, unit)
            dump(f"{unit or '(无单位)'} ← {raw}", got)
            check(decimals_for(unit) == want,
                  f"{unit or '(无单位)'} 精度 = {want} 位")
        check(format_value(61.76, "°C") == "61.8 °C", "温度 0.1")
        check(format_value(65.0, "°C") == "65.0 °C", "整度温度显示为 65.0")
        check(format_value(41.254, "W") == "41.25 W", "功耗 0.01")
        check(format_value(0.688, "V") == "0.688 V", "电压 0.001")
        check(format_value(2285.24, "MHz") == "2285 MHz", "频率取整")
        check(format_value(None, "°C") == "N/A", "缺失值显示 N/A")

        # 表格里实际渲染出来的字符串也要符合精度。
        # 功耗不是温度，默认视图会被过滤掉——先打开"显示全部读数"，
        # 否则这一条会空转（查了 0 个单元格却"通过"）。
        window._show_all.set(True)
        window._on_filter()
        window._refresh()
        root.update_idletasks()
        root.update()

        all_groups = window.tree.get_children()
        cells = [window.tree.set(iid, col)
                 for group in all_groups
                 for iid in window.tree.get_children(group)
                 for col in ("cur", "min", "max")]
        temps = [c for c in cells if c.endswith("°C")]
        powers = [c for c in cells if c.endswith(" W")]
        dump("表格温度单元格示例", temps[:3])
        dump("表格功耗单元格示例", powers[:3] or "（本机当前无功耗读数）")

        def decimals_in(cell: str) -> int:
            head = cell.rsplit(" ", 1)[0]
            return len(head.split(".")[1]) if "." in head else 0

        check(temps and all(decimals_in(c) == 1 for c in temps),
              f"表格里每个温度都是 1 位小数（共 {len(temps)} 个单元格）")
        check(all(decimals_in(c) == 2 for c in powers),
              f"表格里每个功耗都是 2 位小数（共 {len(powers)} 个单元格）")

        root.destroy()
        collector.stop()

    print("=" * 72)
    if failures:
        print(f"失败 {len(failures)} 项:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("OK - 界面冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
