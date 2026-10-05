"""以管理员身份启动一条命令（会弹一次 UAC）。

用法：
    python tools/run_elevated.py python tools/elevated_report.py
    python tools/run_elevated.py python main.py
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW_SHOWNORMAL = 1
SHELL_SUCCESS = 32


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提权运行命令")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="要运行的命令，例如：python tools/elevated_report.py")
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("需要给出要运行的程序")

    exe = command[0]
    lower = Path(exe).name.lower()
    if lower in ("python", "python.exe", "pythonw", "pythonw.exe"):
        candidate = Path(sys.executable)
        if lower.startswith("pythonw"):
            gui = candidate.with_name("pythonw.exe")
            candidate = gui if gui.exists() else candidate
        exe = str(candidate)
    params = subprocess.list2cmdline(command[1:])

    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, params, str(ROOT), SW_SHOWNORMAL)
    if result <= SHELL_SUCCESS:
        # 5 = 用户拒绝 UAC，1223 = 在 UAC 对话框点了取消
        print(f"提权失败或被拒绝（ShellExecuteW 返回 {result}）")
        return 1
    print(f"已发起提权运行：{exe} {params}")
    print("（该进程独立运行，请等待它写出报告文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
