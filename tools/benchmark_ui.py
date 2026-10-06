"""Measure Tk rendering with 300 fixed synthetic sensors, without hardware polling.

python tools/benchmark_ui.py [--baseline FILE]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
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


def measure(window_class):
    root = tk.Tk()
    collector = FakeCollector([sensor(f"Core {i}", Role.CPU_CORE, 50+i%20)
                               for i in range(300)])
    config = Config(sound=False, animations=False, minimize_to_tray=False)
    with mock.patch.object(window_class, "_start_tray"):
        window = window_class(root, config, collector)
    root.update()
    window._refresh_once()
    original_item, original_set = window.tree.item, window.tree.set
    writes = 0

    def item(*args, **kwargs):
        nonlocal writes
        if "values" in kwargs or "tags" in kwargs:
            writes += 1
        return original_item(*args, **kwargs)

    def tree_set(*args, **kwargs):
        nonlocal writes
        if len(args) >= 3:
            writes += 1
        return original_set(*args, **kwargs)

    window.tree.item = item
    window.tree.set = tree_set
    results = {}
    try:
        for name, change in (("unchanged_sequence", False), ("same_values_new_sequence", True)):
            writes = 0
            started = time.perf_counter()
            for _ in range(150):
                if change:
                    collector.sequence += 1
                window._refresh_once()
            results[name] = {"ms_per_refresh": round((time.perf_counter()-started)*1000/150, 4),
                             "table_writes_per_refresh": writes/150}
        root.withdraw()
        writes = 0
        started = time.perf_counter()
        for _ in range(150):
            collector.sequence += 1
            window._refresh_once()
        results["hidden"] = {"ms_per_refresh": round((time.perf_counter()-started)*1000/150, 4),
                             "table_writes_per_refresh": writes/150}
    finally:
        window._closing = True
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.destroy()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    results = {"workload": "300 synthetic sensors; 150 refresh calls per scenario",
               "current": measure(MainWindow)}
    if args.baseline:
        spec = importlib.util.spec_from_file_location("vrmmon.ui._benchmark_baseline", args.baseline)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        results["baseline"] = measure(module.MainWindow)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
