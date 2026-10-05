"""vrmmon 命令行入口——供 PATH 中的 vrmmon.cmd 调用。

用法：
  vrmmon                启动界面（默认提权，否则读不到 CPU / 主板传感器）
  vrmmon noadmin        以普通权限启动（只有显卡/硬盘温度）
  vrmmon minimized      提权并直接最小化到托盘
  vrmmon doctor [...]   在终端里输出体检报告（参数透传给 doctor）
  vrmmon stop           让正在运行的实例优雅退出（不需要管理员权限）
  vrmmon where          显示安装路径、数据库与权限状态
  vrmmon help           显示本帮助
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 兜底强杀用。只在优雅关闭超时后使用，且对提权实例需要提权才能生效。
LIST_PIDS = (
    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -match 'main\\.py' } | "
    "ForEach-Object { $_.ProcessId }"
)


def _start_gui(elevated: bool = True, minimized: bool = False) -> int:
    """与快捷方式共用同一套启动逻辑（已有实例则唤出窗口）。"""
    from vrmmon import launcher
    return launcher.launch(elevated=elevated, minimized=minimized,
                           source="命令 vrmmon")


def _doctor(extra: list[str]) -> int:
    from vrmmon import doctor
    return doctor.main(extra)


def _instance_pids() -> list[int]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", LIST_PIDS],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(token) for token in result.stdout.split() if token.strip().isdigit()]


def _stop(timeout_s: float = 20.0) -> int:
    from vrmmon import shutdown

    if not shutdown.is_app_running():
        print("没有正在运行的 vrmmon 实例。")
        return 0

    if not shutdown.request_shutdown():
        print("无法通知正在运行的实例。", file=sys.stderr)
        return 1

    print("已请求退出，等待数据落盘 ...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.5)
        if not shutdown.is_app_running():
            print("已优雅退出。")
            return 0

    print(f"等待 {timeout_s:.0f} 秒仍未退出，尝试强制结束 ...", file=sys.stderr)
    pids = _instance_pids()
    if not pids:
        print("枚举不到进程（提权实例对普通权限进程不可见）。"
              "请用任务管理器结束 pythonw.exe。", file=sys.stderr)
        return 1
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, timeout=15, check=False)
    time.sleep(1.0)
    return 0 if not shutdown.is_app_running() else 1


def _where() -> int:
    from vrmmon import config as config_module
    from vrmmon import elevation, shortcut, shutdown

    config = config_module.load()
    database = config.resolve(config.db_path)
    shim = shortcut.active_shim()

    print(f"项目根目录   {ROOT}")
    print(f"Python       {sys.executable}")
    print(f"启动器       {shim if shim else '未安装（运行 tools\\install_shortcut.py）'}")
    if shim is not None:
        on_path = any(Path(part).resolve() == shim.parent.resolve()
                      for part in os.environ.get("PATH", "").split(os.pathsep)
                      if part.strip() and Path(part).exists())
        print(f"             在 PATH 中: {'是' if on_path else '否'}")
    print(f"运行状态     {'正在运行' if shutdown.is_app_running() else '未运行'}")
    print(f"数据库       {database}")
    if database.exists():
        size = sum(p.stat().st_size for p in
                   (database, Path(str(database) + '-wal'), Path(str(database) + '-shm'))
                   if p.exists())
        print(f"              {size / 1024 / 1024:.1f} MB")
    else:
        print("              （尚未创建）")
    from vrmmon import logging_setup
    errors = logging_setup.LOG_PATH
    lifecycle = logging_setup.LIFECYCLE_PATH
    print(f"异常日志     {errors}  "
          f"[{'有 ' + str(errors.stat().st_size) + ' 字节' if errors.exists() else '空'}]")
    if lifecycle.exists():
        tail = lifecycle.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        print(f"退出记录     {tail[-1] if tail else '（空）'}")
    print(f"当前权限     {'管理员' if elevation.is_elevated() else '普通用户'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _start_gui(elevated=True)

    head, rest = args[0].lower(), args[1:]
    if head in ("help", "-h", "--help", "/?"):
        print(__doc__)
        return 0
    if head in ("noadmin", "plain", "--no-admin"):
        return _start_gui(elevated=False)
    if head == "minimized":
        return _start_gui(elevated=True, minimized=True)
    if head in ("doctor", "check"):
        return _doctor(rest)
    if head in ("stop", "kill", "quit"):
        return _stop()
    if head in ("where", "status", "info"):
        return _where()
    if head in ("gui", "start"):
        return _start_gui(elevated="--noadmin" not in rest)

    print(f"未知子命令: {head}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
