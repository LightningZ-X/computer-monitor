"""PATH 启动器的位置约定。

优先装到**本来就在 PATH 里**的目录（Python 的 Scripts，pip 装命令行工具就用它）。
原因：新追加进 PATH 的目录对**已经在运行的 Explorer 不可见**，而用户新开的终端
是从 Explorer 继承环境的——注册表改对了，终端里照样找不到命令。实测过这个坑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SHIM_NAME = "vrmmon.cmd"


def python_scripts_dir() -> Path | None:
    """当前 Python 的 Scripts 目录（pip 控制台脚本的落点）。"""
    scripts = Path(sys.executable).parent / "Scripts"
    return scripts if scripts.is_dir() else None


def dedicated_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "vrmmon"


def candidates() -> list[Path]:
    """按优先级列出可能的启动器路径。"""
    paths: list[Path] = []
    scripts = python_scripts_dir()
    if scripts is not None:
        paths.append(scripts / SHIM_NAME)
    paths.append(dedicated_dir() / SHIM_NAME)
    return paths


def active_shim() -> Path | None:
    """当前实际生效的启动器（存在即返回）。"""
    for path in candidates():
        if path.exists():
            return path
    return None
