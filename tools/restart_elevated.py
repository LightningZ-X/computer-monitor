"""以管理员身份重启 vrmmon：结束所有已有实例，再启动一个新的。

为什么需要单独一个脚本：提权进程无法被普通权限的进程终止。如果因为误操作
留下多个实例（它们会同时写库、同时注册托盘图标），只能从一个提权进程里清理。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 只杀命令行里带 main.py 的 pythonw，避免误伤其它 Python 程序
FIND_AND_KILL = (
    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -match 'main\\.py' } | "
    "ForEach-Object { Write-Host ('结束 PID ' + $_.ProcessId); "
    "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print("结束已有实例 ...")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", FIND_AND_KILL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    time.sleep(2.5)

    exe = Path(sys.executable).with_name("pythonw.exe")
    if not exe.exists():
        exe = Path(sys.executable)
    print(f"启动新实例: {exe} main.py")
    subprocess.Popen([str(exe), str(ROOT / "main.py")], cwd=str(ROOT),
                     close_fds=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
