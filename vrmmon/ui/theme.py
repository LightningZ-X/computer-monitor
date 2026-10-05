"""设计令牌与 ttk 主题。

对齐 D:\\deepseekharness\\psu-calculator 的设计系统（ROG / Armoury Crate 风格），
那份 `docs/DESIGN.md` 里的硬规则在这里逐条落地：

  · 背景**三级亮度**分区（#0a0a0a 顶栏 / #121212 主区 / #181818 凹陷），
    靠亮度差与细分隔线分组，而不是"每块都套同一个深色卡片"、也不靠描边。
  · **全局只有一个强调色**（ROG 红 #ff0033），只出现在 1px 描边、指示条、
    小控件底色上；不给大容器铺红底。
  · 圆角 2px（tkinter 控件是矩形，天然符合"面板保持干净矩形"）。
  · 字号收敛成少数几档，字重只用 400 / 600 / 700。
  · 禁止渐变、外发光、emoji；等宽字体承担所有数字对齐。

名字与旧版保持一致（BG/BG2/BG3/FG/MUTED/ACCENT/OK/WARN/CRIT），
这样界面代码只需换数据源，不必改引用点。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------- 背景：三级亮度 ----------
NAV = "#0a0a0a"        # 顶栏 / 工具栏，最暗
BG = "#121212"         # 主内容区
BG2 = "#181818"        # 次级凹陷区（输入框、表格、画布）
SURFACE = "#1a1a1a"    # 浮起表面（下拉、托盘气泡）
SURFACE2 = "#202020"   # 表面上的悬停态 / 按钮
BG3 = SURFACE2         # 兼容旧引用

# ---------- 描边：极低对比，只在需要时出现 ----------
LINE = "#2a2a2a"
LINE_SOFT = "#202020"

# ---------- 文本：四级 ----------
FG = "#ffffff"
TEXT2 = "#c8c8c8"
MUTED = "#8a8a8a"
TEXT4 = "#7d7d7d"

# ---------- ROG 红：全局唯一强调色 ----------
ACCENT = "#ff0033"
ACCENT_DIM = "#c40028"

# ---------- 状态色：只用于有语义的地方 ----------
OK = "#16a34a"
WARN = "#e08600"
CRIT = "#ff0033"

#: 曲线配色。设计系统只有一个强调色，图表用「语义色 + 中性灰」保持同一调性；
#: 红色在温度语境里天然表示"热"，所以放在首位。
PALETTE = ("#ff0033", "#e08600", "#16a34a", "#c8c8c8", "#7d7d7d", "#ffffff")

# ---------- 字体：全站收敛成几档 ----------
SANS = "Segoe UI"
MONO = "Consolas"
FONT_XS = (SANS, 8)
FONT_SM = (SANS, 9)
FONT_BASE = (SANS, 10)
FONT_H = (SANS, 11, "bold")
FONT_SEC = (SANS, 15, "bold")
FONT_MONO = (MONO, 10)
FONT_MONO_SM = (MONO, 9)


def rule(parent: tk.Misc, colour: str = LINE) -> tk.Frame:
    """1px 细分隔线——分组靠它，而不是靠给每块套卡片。"""
    line = tk.Frame(parent, bg=colour, height=1)
    line.pack_propagate(False)
    return line


def accent_bar(parent: tk.Misc, width: int = 0, height: int = 4) -> tk.Frame:
    """4px 红条：设计系统里"选中 / 关键值"的指示条。

    入场动效需要逐帧改宽度，所以调用方通常用 place(relwidth=...) 摆放它，
    而不是 pack。
    """
    bar = tk.Frame(parent, bg=ACCENT, height=height)
    if width:
        bar.configure(width=width)
    bar.pack_propagate(False)
    return bar


def section_header(parent: tk.Misc, text: str, background: str = NAV,
                   padx: int = 0) -> tk.Frame:
    """分区标记：`///` 红斜杠 + 标题（对应 .card > h2::before）。

    两个 Label 通过 `marker` / `title` 属性暴露出去，方便入场动效直接操作
    （滑入 + 上色），不必再去遍历子控件。
    """
    header = tk.Frame(parent, bg=background)
    marker = tk.Label(header, text="///", bg=background, fg=ACCENT,
                      font=(MONO, 11, "bold"))
    marker.pack(side="left")
    title = tk.Label(header, text=text, bg=background, fg=FG, font=FONT_SEC)
    title.pack(side="left", padx=(8, 0))
    header.marker = marker
    header.title = title
    if padx:
        header.pack(fill="x", padx=padx)
    return header


def apply(root: tk.Tk) -> ttk.Style:
    """把设计令牌落到 ttk 上。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)

    style.configure(".", background=BG, foreground=FG, fieldbackground=BG2,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    troughcolor=BG2, focuscolor=ACCENT)

    # 面板
    style.configure("TFrame", background=BG)
    style.configure("Nav.TFrame", background=NAV)
    style.configure("Soft.TFrame", background=BG2)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT_BASE)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SM)
    style.configure("NavMuted.TLabel", background=NAV, foreground=MUTED, font=FONT_SM)
    style.configure("Nav.TLabel", background=NAV, foreground=FG, font=FONT_BASE)
    style.configure("Mono.TLabel", background=BG, foreground=TEXT2, font=FONT_MONO)

    # 按钮：平面、方角，靠亮度差而不是描边
    style.configure("TButton", background=SURFACE2, foreground=FG,
                    borderwidth=0, relief="flat", padding=(10, 5), font=FONT_SM)
    style.map("TButton",
              background=[("pressed", ACCENT_DIM), ("active", LINE), ("disabled", BG2)],
              foreground=[("pressed", FG), ("active", FG), ("disabled", TEXT4)])
    style.configure("Accent.TButton", background=ACCENT, foreground=FG,
                    borderwidth=0, relief="flat", padding=(10, 5), font=FONT_SM)
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_DIM), ("active", ACCENT_DIM)],
              foreground=[("disabled", TEXT4)])

    # 勾选 / 输入
    style.configure("TCheckbutton", background=BG, foreground=TEXT2,
                    font=FONT_SM, focuscolor=ACCENT)
    style.map("TCheckbutton", background=[("active", BG)],
              foreground=[("active", FG)])
    style.configure("Nav.TCheckbutton", background=NAV, foreground=TEXT2, font=FONT_SM)
    style.map("Nav.TCheckbutton", background=[("active", NAV)],
              foreground=[("active", FG)])
    style.configure("TCombobox", fieldbackground=SURFACE2, background=SURFACE2,
                    foreground=FG, arrowcolor=TEXT2, bordercolor=LINE,
                    selectbackground=SURFACE2, selectforeground=FG)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE2)])
    style.configure("TSpinbox", fieldbackground=SURFACE2, background=SURFACE2,
                    foreground=FG, arrowcolor=TEXT2, bordercolor=LINE,
                    insertcolor=FG)
    style.configure("TEntry", fieldbackground=SURFACE2, foreground=FG,
                    bordercolor=LINE, insertcolor=FG)

    # 表格：凹陷背景，选中态靠亮度抬升 + 红字，不铺红底
    style.configure("Treeview", background=BG2, fieldbackground=BG2,
                    foreground=TEXT2, font=FONT_SM, rowheight=22,
                    borderwidth=0, relief="flat")
    style.configure("Treeview.Heading", background=NAV, foreground=MUTED,
                    font=FONT_XS, relief="flat", padding=(6, 4))
    style.map("Treeview.Heading", background=[("active", SURFACE2)])
    style.map("Treeview",
              background=[("selected", SURFACE2)],
              foreground=[("selected", FG)])

    # 滚动条：极简，无描边
    style.configure("TScrollbar", background=SURFACE2, troughcolor=BG2,
                    bordercolor=BG2, arrowcolor=MUTED, relief="flat")
    style.map("TScrollbar", background=[("active", LINE)])

    style.configure("TSeparator", background=LINE)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", background=LINE, gripcount=0)

    return style
