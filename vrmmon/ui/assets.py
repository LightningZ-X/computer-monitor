"""界面用到的图片素材路径。

全都由 `tools/install_menu_shortcut.py` 生成/搬运，这里只集中声明位置——
之前 tray.py、main_window.py、boot.py 各自拼一遍路径，容易漏改。
"""

from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"

#: 与功耗计算器同一套 logo：闪电标记 + LIGHTNING 字标（白色遮罩原图）
MARK_SOURCE = ASSETS / "lightning-mark-source.png"
WORDMARK_SOURCE = ASSETS / "lightning-wordmark-source.png"

#: 预先按强调色上好色的版本（tkinter 不能给图片上色）
HEADER_MARK = ASSETS / "vrmmon-mark.png"
HEADER_WORDMARK = ASSETS / "vrmmon-wordmark.png"

#: 应用图标（多尺寸 ICO）。文件名带内容哈希，所以这里按最新一个来找——
#: 哈希是为了绕开 Explorer 的图标缓存（它以路径为键），见 tools/install_menu_shortcut.py。
def find_icon() -> Path | None:
    candidates = list(ASSETS.glob("vrmmon*.ico"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


#: 顶栏 lockup 里标志的高度（px）。字标宽度由同一比例推出。
HEADER_MARK_HEIGHT = 34


def header_gap(mark_height: int = HEADER_MARK_HEIGHT) -> int:
    """标志与字标之间的间距。取那边 .logo 的 gap 11px 同比放大。"""
    return round(11 * mark_height / (22 * 166 / 88))
