r"""管理员权限相关：检测与提权重启。

为什么需要它：CPU 温度走 MSR、主板温度走 Super I/O 端口 I/O，两者都是
ring0 操作。所有同类框架（PawnIO / WinRing0 / HWiNFO 自带驱动）都把设备
访问权限限定给管理员——实测 `\\.\PawnIO` 对非提权进程返回 ACCESS_DENIED。
所以这不是本程序的选择，而是 Windows 的硬约束。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW_SHOWNORMAL = 1
#: ShellExecuteW 返回值 > 32 表示成功
SHELL_SUCCESS = 32


def is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _launcher() -> str:
    """优先 pythonw.exe，避免提权后多弹一个控制台窗口。"""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def relaunch_elevated(minimized: bool = False) -> tuple[bool, str]:
    """用 UAC 提权重启本程序。返回 (是否已发起, 说明)。"""
    if sys.platform != "win32":
        return False, "仅支持 Windows"
    if is_elevated():
        return False, "当前已经是管理员权限"

    entry = ROOT / "main.py"
    if not entry.exists():
        return False, f"找不到入口文件 {entry}"

    parameters = f'"{entry}"'
    if minimized:
        parameters += " --minimized"
    return run_elevated(_launcher(), parameters)


def run_elevated(executable: str, parameters: str) -> tuple[bool, str]:
    """用 UAC 提权运行任意命令。返回 (是否已发起, 说明)。"""
    if sys.platform != "win32":
        return False, "仅支持 Windows"
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, parameters, str(ROOT), SW_SHOWNORMAL)
    except Exception as exc:
        return False, f"调用 UAC 失败: {exc}"

    if result <= SHELL_SUCCESS:
        # 常见：5=用户拒绝了 UAC，1223=用户在 UAC 对话框点了取消
        return False, f"提权被拒绝或失败（ShellExecuteW 返回 {result}）"
    return True, "已发起提权运行"
