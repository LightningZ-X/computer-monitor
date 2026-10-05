"""开机自启入口：静默发起提权启动主程序。

注册表 Run 项本身无法提权，所以这里用 pythonw 起一个无控制台窗口的进程，
再由它调用 ShellExecuteW("runas") 弹出 UAC 并以管理员身份启动 vrmmon。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vrmmon import elevation  # noqa: E402


def main() -> int:
    started, message = elevation.relaunch_elevated(minimized=True)
    if not started:
        # 提权被拒时退回普通权限启动，至少还能看显卡温度
        import subprocess
        exe = Path(sys.executable).with_name("pythonw.exe")
        if not exe.exists():
            exe = Path(sys.executable)
        subprocess.Popen([str(exe), str(ROOT / "main.py"), "--minimized"],
                         cwd=str(ROOT), close_fds=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
