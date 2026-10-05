"""单实例保护。

没有它会出现两个进程同时写 SQLite、同时往托盘注册图标；而且提权实例无法被
普通权限的进程杀掉，一旦重复启动就很难收拾。

用会话级命名互斥体（Local\\）而不是 Global\\：Global 需要管理员权限才能创建，
而这里恰恰要允许"提权实例"和"非提权实例"互相看见。
"""

from __future__ import annotations

import ctypes
import sys

MUTEX_NAME = "Local\\vrmmon-single-instance"
ERROR_ALREADY_EXISTS = 183

_handle: int | None = None


def acquire() -> bool:
    """拿到单实例锁返回 True；已有实例在运行返回 False。

    非 Windows 或创建失败时一律返回 True —— 单实例保护失败不应该阻止程序启动。
    """
    global _handle
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]

    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _handle = handle
    return True
