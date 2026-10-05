"""开机自启：写 HKCU\\...\\Run，不需要管理员权限，也不改系统服务。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "vrmmon"

ROOT = Path(__file__).resolve().parent.parent


def _pythonw() -> str:
    """优先用 pythonw.exe，避免开机弹出控制台黑窗。"""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def command(minimized: bool = True) -> str:
    """开机自启走提权启动器。

    CPU 温度走 MSR、主板温度走端口 I/O，都需要管理员权限；而 Windows 不允许
    自启项静默提权，所以登录时会弹一次 UAC。想免掉这次弹窗需要计划任务或
    常驻服务（见 README）。
    """
    entry = ROOT / "tools" / "autostart_launch.py"
    return f'"{_pythonw()}" "{entry}"'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_REG_NAME)
            return True
    except OSError:
        return False


def enable(minimized: bool = True) -> None:
    if sys.platform != "win32":
        raise RuntimeError("开机自启目前只支持 Windows")
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, command(minimized))


def disable() -> None:
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_REG_NAME)
    except OSError:
        pass


def set_enabled(enabled: bool, minimized: bool = True) -> None:
    enable(minimized) if enabled else disable()
