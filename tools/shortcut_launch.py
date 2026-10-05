"""启动器入口：唤出已有窗口，或启动 vrmmon。

供桌面/开始菜单快捷方式与两个 .bat 共用，保证只有一条启动代码路径。

用法：
    pythonw tools\\shortcut_launch.py            提权启动（完整传感器）
    pythonw tools\\shortcut_launch.py --noadmin  普通权限启动（仅显卡/硬盘）
    pythonw tools\\shortcut_launch.py --minimized 提权并直接最小化到托盘

用 pythonw 作为宿主，不会闪控制台窗口。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vrmmon import launcher  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return launcher.launch(
        elevated="--noadmin" not in args,
        minimized="--minimized" in args,
        source="快捷方式/批处理",
    )


if __name__ == "__main__":
    raise SystemExit(main())
