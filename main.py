"""vrmmon 入口：主板 CPU 供电(VRM)与 GPU 供电温度监控。

用法：
    python main.py                # 正常启动
    python main.py --minimized    # 直接最小化到托盘（开机自启用）
    python main.py --no-tray      # 不启用托盘
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vrmmon import config as config_module  # noqa: E402
from vrmmon.collector import Collector  # noqa: E402
from vrmmon.ui import MainWindow  # noqa: E402


def enable_dpi_awareness() -> None:
    """避免高分屏上界面发虚。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_app_user_model_id() -> None:
    """声明 AppUserModelID，让任务栏用本程序自己的图标。

    pythonw 托管的脚本，任务栏默认按 pythonw.exe 分组并显示它的图标——
    `iconbitmap` / `iconphoto` 只改得到标题栏，改不到任务栏按钮。
    显式声明 AppUserModelID 后，Windows 才会用窗口图标（我们的闪电图标）。
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LightningZ.vrmmon.TemperatureMonitor")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vrmmon", description="供电温度监控")
    parser.add_argument("--minimized", action="store_true", help="启动后直接最小化到托盘")
    parser.add_argument("--no-tray", action="store_true", help="不启用托盘图标")
    parser.add_argument("--interval", type=float, help="采样间隔（秒）")
    parser.add_argument("--doctor", action="store_true", help="只跑体检报告后退出")
    args = parser.parse_args(argv)

    enable_dpi_awareness()
    set_app_user_model_id()

    if args.doctor:
        from vrmmon import doctor
        return doctor.main([])

    from vrmmon.single_instance import acquire
    if not acquire():
        # 已有实例：请它把窗口唤到前台，而不是静默退出——否则双击图标
        # 看起来就是"完全没反应"。
        from vrmmon import launcher
        launcher.request_show_from_other_instance()
        print("vrmmon 已在运行，已请它显示窗口。", file=sys.stderr)
        return 0

    config = config_module.load()
    if args.interval:
        config.interval_s = config_module.clamp_interval(args.interval)
        # 命令行覆盖只对本次运行生效，退出时不要把临时值写回配置
        config.save_path = ""

    collector = Collector(config)
    collector.start()

    root = tk.Tk()
    window = MainWindow(root, config, collector, start_minimized=args.minimized)
    if args.no_tray:
        if window.tray is not None:
            window.tray.stop()
        window.tray = None
        root.deiconify()
    elif args.minimized and window.tray is None:
        # 托盘不可用时必须露出窗口，否则程序会"消失"
        root.deiconify()

    from vrmmon import elevation, logging_setup, shutdown
    logging_setup.install("主程序未捕获异常")
    logging_setup.log_message(
        f"启动：{'管理员' if elevation.is_elevated() else '普通用户'}权限，"
        f"采样间隔 {config.interval_s:g}s", "启动")

    # 协作式通道：关闭（vrmmon stop）与唤出窗口（双击快捷方式）
    to_shutdown = shutdown.EventChannel(shutdown.EVENT_NAME)
    if to_shutdown.open():
        to_shutdown.wait(lambda: setattr(window, "shutdown_requested", True))
    else:
        logging_setup.log_message("无法创建关闭事件，vrmmon stop 将不可用", "启动")

    to_show = shutdown.EventChannel(shutdown.SHOW_EVENT_NAME)
    if to_show.open():
        to_show.wait(lambda: setattr(window, "show_requested", True))
    else:
        logging_setup.log_message("无法创建唤出事件，重复启动时无法自动显示窗口", "启动")

    try:
        root.mainloop()
    except Exception:
        logging_setup.log_exception(*sys.exc_info(), context="mainloop")
    finally:
        to_show.close()
        to_shutdown.close()
        try:
            collector.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
