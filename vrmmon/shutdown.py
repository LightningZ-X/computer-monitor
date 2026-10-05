"""跨进程事件通道：优雅关闭 + 唤出窗口。

为什么不用 taskkill 枚举进程：
  1. 提权进程无法被普通权限的进程终止；
  2. `Get-CimInstance Win32_Process` 对非提权调用者不返回提权进程的 CommandLine，
     所以按命令行匹配根本找不到实例（实测会误报"没有正在运行的实例"）；
  3. 强杀会丢掉未落盘的数据，也不释放 LibreHardwareMonitor 的内核驱动句柄。

用命名事件做协作式通信：程序持有事件对象并等待，任何同用户进程（不论是否提权）
都能发信号。事件对象的默认 DACL 含当前用户 SID，所以普通权限也能通知提权实例。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

#: 程序存活标志：该事件存在 == 有实例在运行
EVENT_NAME = "Local\\vrmmon-shutdown"
#: 请求把已有窗口唤到前台（双击快捷方式时用）
SHOW_EVENT_NAME = "Local\\vrmmon-show"

EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


class EventChannel:
    """主程序侧：持有事件对象，每次收到信号回调一次。

    name 可注入是为了测试——否则单元测试会误伤正在运行的真实实例。
    """

    def __init__(self, name: str = EVENT_NAME) -> None:
        self.name = name
        self._handle = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def open(self) -> bool:
        if sys.platform != "win32":
            return False
        handle = _kernel32().CreateEventW(None, True, False, self.name)
        if not handle:
            return False
        self._handle = handle
        return True

    def wait(self, on_signal) -> None:
        """后台线程循环等待。事件是手动重置的，收到后要 Reset 才能再次触发。"""
        if not self._handle:
            return

        def run() -> None:
            kernel32 = _kernel32()
            while not self._stopped.is_set():
                try:
                    kernel32.WaitForSingleObject(self._handle, INFINITE)
                    kernel32.ResetEvent(self._handle)
                except Exception:
                    return
                if self._stopped.is_set():
                    return
                try:
                    on_signal()
                except Exception:
                    pass

        self._thread = threading.Thread(target=run, name=f"vrmmon-event-{self.name}",
                                        daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stopped.set()
        if self._handle:
            try:
                # 唤醒等待线程，让它可以退出
                _kernel32().SetEvent(self._handle)
            except Exception:
                pass
            try:
                _kernel32().CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


def is_app_running(name: str = EVENT_NAME) -> bool:
    """事件对象存在即说明有实例持有它。不需要任何权限。

    name 可注入是为了测试：单元测试用独立事件名，避免误伤真实实例。
    """
    if sys.platform != "win32":
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenEventW(SYNCHRONIZE, False, name)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def signal(name: str) -> bool:
    """给指定事件发信号。返回是否发出。"""
    if sys.platform != "win32":
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, name)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def request_shutdown(name: str = EVENT_NAME) -> bool:
    return signal(name)


def request_show(name: str = SHOW_EVENT_NAME) -> bool:
    """请已有实例把窗口唤到前台。"""
    return signal(name)


#: 兼容旧名字
ShutdownChannel = EventChannel
