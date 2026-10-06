"""生成图标，并在开始菜单与桌面创建可双击的快捷方式。

双击后会自动请求管理员权限（走 tools/run_elevated.py），这样才有 CPU / 主板
传感器；用 pythonw 作为宿主，所以**不会闪控制台窗口**。

用法：
    python tools\\install_menu_shortcut.py              # 安装（开始菜单 + 桌面）
    python tools\\install_menu_shortcut.py --no-desktop # 只装开始菜单
    python tools\\install_menu_shortcut.py --uninstall  # 卸载
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 图标文件名带内容哈希，见 _icon_path()。
ICON_STEM = "vrmmon"
SHORTCUT_NAME = "vrmmon 供电温度监控.lnk"

#: 快捷方式描述保持 ASCII：PowerShell 5.1 读无 BOM 的 UTF-8 命令会乱码
DESCRIPTION = "VELTRIX Monitor - CPU/GPU temperature monitoring"


#: 图标与界面标志沿用界面设计令牌（见 vrmmon/ui/theme.py），不另起一套配色。
#: 底色取 --bg #121212、满幅不透明、无描边无圆角——与功耗计算器的
#: assets/apple-touch-icon.png 完全一致（那边 180×180 全不透明，底 #121212）。
ICON_BG = (18, 18, 18)          # --bg       #121212
ICON_ACCENT = (255, 0, 51)      # --rog      ROG 红，全局唯一强调色

#: 按标志宽高比等比放入正方形，宽度占 82%，确保 16px 托盘/任务栏不裁切。
ICON_MARK_HEIGHT_RATIO = 0.82 * 278 / 334

#: 与功耗计算器同一套 VELTRIX 标志和字标。原图都是"纯白 + 透明遮罩"，
#: 需要按强调色上色（对应那边 CSS 的 background: var(--rog) + mask-image）。
ASSETS = ROOT / "assets"
MARK_SOURCE = ASSETS / "veltrix-mark-source.png"
WORDMARK_SOURCE = ASSETS / "veltrix-wordmark-source.png"
#: 素材原图的兜底来源：**只有** assets/ 里缺 veltrix-*-source.png 时才会用到。
#: 正常情况下那两个源文件已在仓库里，换机器也能跑；默认指向本机那份功耗计算器
#: 工程，可用环境变量 VRMMON_PSU_ASSETS 覆盖。
PSU_ASSETS = Path(os.environ.get(
    "VRMMON_PSU_ASSETS", r"D:\deepseekharness\psu-calculator\assets"))
PSU_MARK = PSU_ASSETS / "veltrix-mark-source.png"
PSU_WORDMARK = PSU_ASSETS / "veltrix-wordmark-source.png"
HEADER_MARK = ASSETS / "vrmmon-mark.png"
HEADER_WORDMARK = ASSETS / "vrmmon-wordmark.png"

#: 顶栏比例与功耗计算器一致：标志宽 32px，334/278；字标宽 140px。
WORDMARK_WIDTH_PER_MARK_HEIGHT = 140 / (32 * 278 / 334)


def _ensure_source(path: Path, fallback: Path) -> Path:
    """把素材原图收到本项目里，之后不再依赖 deepseekharness 的路径。"""
    ASSETS.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if not fallback.exists():
            raise FileNotFoundError(f"找不到素材原图：{fallback}")
        shutil.copyfile(fallback, path)
    return path


def _tinted(source: Path, height: int | None = None, width: int | None = None):
    """把白色遮罩按强调色上色，并缩放到指定高度或宽度。"""
    from PIL import Image

    src = Image.open(source).convert("RGBA")
    if width is None:
        assert height is not None
        width = max(1, round(src.width * height / src.height))
    elif height is None:
        height = max(1, round(src.height * width / src.width))
    alpha = src.getchannel("A").resize((width, height), Image.LANCZOS)
    tinted = Image.new("RGBA", (width, height), ICON_ACCENT + (0,))
    tinted.putalpha(alpha)
    return tinted


def _tinted_mark(height: int):
    return _tinted(_ensure_source(MARK_SOURCE, PSU_MARK), height=height)


def make_icon() -> Path:
    """应用图标：满幅 --bg 底 + 大红闪电。

    几何与配色对齐功耗计算器的 apple-touch-icon.png：底色 #121212、满幅不透明、
    无描边无圆角，标志等比缩放，宽度占 82%，缩到 16px 仍保持完整轮廓。

    文件名带内容哈希——原因见 _icon_path()。
    """
    import io

    from PIL import Image

    ASSETS.mkdir(parents=True, exist_ok=True)
    size = 256
    icon = Image.new("RGBA", (size, size), ICON_BG + (255,))
    mark = _tinted_mark(round(size * ICON_MARK_HEIGHT_RATIO))
    icon.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))

    buffer = io.BytesIO()
    icon.save(buffer, format="ICO",
              sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    data = buffer.getvalue()

    path = _icon_path(data)
    path.write_bytes(data)
    _drop_stale_icons(keep=path)
    return path


def _icon_path(data: bytes) -> Path:
    """图标文件名带内容哈希。

    Explorer 的图标缓存**以路径为键**：路径不变、内容变了也照样用旧图标。
    实测踩到过——新增图标是 16:25，而 iconcache_*.db 还停在 09:59，
    桌面一直显示旧图标，跑 ie4uinit -show 也不会重写那些缓存。
    把哈希写进文件名，路径一变就不会命中旧缓存，也不用重启 Explorer
    （重启会关掉用户所有资源管理器窗口）。
    """
    digest = hashlib.sha256(data).hexdigest()[:8]
    return ASSETS / f"{ICON_STEM}-{digest}.ico"


def _drop_stale_icons(keep: Path) -> None:
    """删掉旧哈希的图标，别在 assets 里越积越多。"""
    for old in ASSETS.glob(f"{ICON_STEM}*.ico"):
        if old != keep:
            try:
                old.unlink()
            except OSError:
                pass


def prepare_header_mark() -> Path:
    """界面顶栏用的标记（tkinter 不能给图片上色，所以这里预先成一版红色 PNG）。"""
    _tinted_mark(28).save(HEADER_MARK)
    return HEADER_MARK


def prepare_header_wordmark(mark_height: int = 28) -> Path:
    """顶栏字标（VELTRIX）。宽度按那边 .logo 的比例与标志高度联动。"""
    width = round(mark_height * WORDMARK_WIDTH_PER_MARK_HEIGHT)
    _tinted(_ensure_source(WORDMARK_SOURCE, PSU_WORDMARK), width=width).save(
        HEADER_WORDMARK)
    return HEADER_WORDMARK


def _powershell(script: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, result.stdout.strip()


def _create_shortcut(directory: Path, icon: Path) -> tuple[bool, str]:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    # 走与 `vrmmon` 命令相同的启动逻辑：已有实例时唤出窗口，而不是静默退出
    launcher = ROOT / "tools" / "shortcut_launch.py"
    arguments = f'"{launcher}"'

    link = directory / SHORTCUT_NAME
    directory.mkdir(parents=True, exist_ok=True)
    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$lnk = $ws.CreateShortcut('{link}')\n"
        f"$lnk.TargetPath = '{pythonw}'\n"
        f"$lnk.Arguments = '{arguments}'\n"
        f"$lnk.WorkingDirectory = '{ROOT}'\n"
        f"$lnk.Description = '{DESCRIPTION}'\n"
        f"$lnk.IconLocation = '{icon}'\n"
        "$lnk.Save()\n"
        f"if (Test-Path '{link}') {{ 'OK' }} else {{ 'MISSING' }}\n"
    )
    ok, message = _powershell(script)
    if not ok:
        return False, message
    if "OK" not in message:
        return False, "PowerShell 没有报告创建成功"
    return True, str(link)


def _folders() -> dict[str, Path]:
    ok, output = _powershell(
        "[Environment]::GetFolderPath('Programs')\n"
        "[Environment]::GetFolderPath('Desktop')\n")
    if not ok:
        return {}
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    return {"startmenu": Path(lines[0]), "desktop": Path(lines[1])}


def install(with_desktop: bool = True) -> int:
    icon = make_icon()
    print(f"图标已生成   {icon}")
    mark = prepare_header_mark()
    print(f"顶栏标志     {mark}")
    wordmark = prepare_header_wordmark()
    print(f"顶栏字标     {wordmark}")

    folders = _folders()
    if "startmenu" not in folders:
        print("拿不到开始菜单/桌面路径", file=sys.stderr)
        return 1

    targets = [("开始菜单", folders["startmenu"])]
    if with_desktop:
        targets.append(("桌面", folders["desktop"]))

    failures = 0
    for label, directory in targets:
        ok, detail = _create_shortcut(directory, icon)
        print(f"{label:<8} {'已创建  ' + detail if ok else '失败: ' + detail}")
        failures += 0 if ok else 1

    print()
    if failures:
        print(f"有 {failures} 个快捷方式创建失败。", file=sys.stderr)
        return 1
    print("完成。双击图标即可启动（会弹一次 UAC 以获取管理员权限）。")
    return 0


def uninstall() -> int:
    folders = _folders()
    removed = 0
    for directory in folders.values():
        link = directory / SHORTCUT_NAME
        if link.exists():
            link.unlink()
            print(f"已删除 {link}")
            removed += 1
    if not removed:
        print("没有找到已安装的快捷方式")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="install_menu_shortcut",
                                     description="创建可双击的快捷方式")
    parser.add_argument("--uninstall", action="store_true", help="删除快捷方式")
    parser.add_argument("--no-desktop", action="store_true", help="只装开始菜单")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("仅支持 Windows", file=sys.stderr)
        return 2
    return uninstall() if args.uninstall else install(not args.no_desktop)


if __name__ == "__main__":
    raise SystemExit(main())
