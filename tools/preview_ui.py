"""Offline UI preview with labelled synthetic readings; no hardware or DB writes."""
from __future__ import annotations
import sys
import tkinter as tk
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_lightweight_ui import FakeCollector, sensor
from vrmmon.config import Config
from vrmmon.sensors import Role
from vrmmon.ui.main_window import MainWindow

collector = FakeCollector([
    sensor("CPU Package", Role.CPU_PACKAGE, 58.4),
    sensor("Core Max", Role.CPU_CORE, 61.0),
    sensor("CPU VRM", Role.CPU_VRM, 54.2, group="主板"),
    sensor("GPU Core", Role.GPU_CORE, 48.0, group="GPU"),
    sensor("GPU Hot Spot", Role.GPU_HOTSPOT, 61.5, group="GPU"),
    sensor("GPU Memory", Role.GPU_MEMORY, 56.0, group="GPU"),
])
root = tk.Tk()
with mock.patch.object(MainWindow, "_start_tray"):
    window = MainWindow(root, Config(sound=False, minimize_to_tray=False), collector)
root.title("VELTRIX Monitor · 离线界面预览（模拟数据）")
root.update_idletasks()
window._refresh_once()
root.after(180000, window.quit_app)
root.mainloop()
