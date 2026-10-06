"""Offline UI/performance regressions. No hardware polling, user DB or tray.

Run: python tests/test_lightweight_ui.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vrmmon.alerts import AlertEvent
from vrmmon.collector import Collector, Snapshot
from vrmmon.config import Config, clamp_interval, load
from vrmmon.providers.base import ProviderStatus
from vrmmon.sensors import Kind, Role, Sensor, SensorId
from vrmmon.ui.main_window import MainWindow


def sensor(name, role, value, kind=Kind.TEMPERATURE, group="CPU"):
    result = Sensor(SensorId("test", group, name, kind.value), kind,
                    "°C" if kind is Kind.TEMPERATURE else "V", role)
    result.observe(value, None, None)
    return result


class FakeCollector:
    def __init__(self, sensors=None):
        self.sensors = sensors if sensors is not None else [
            sensor("CPU Package", Role.CPU_PACKAGE, 55),
            sensor("GPU Core", Role.GPU_CORE, 46, group="GPU"),
        ]
        self.sequence = 1
        self.paused = False
        self.interval_s = 2.0
        self.events = []
        self.started_at = time.time() - 30

    def snapshot(self):
        events, self.events = self.events, []
        return Snapshot(sequence=self.sequence, paused=self.paused,
                        sensors=self.sensors,
                        statuses=[("Offline test", ProviderStatus(True, "可用", ""))],
                        alerts=events, started_at=self.started_at)

    def set_interval(self, seconds):
        self.interval_s = clamp_interval(seconds)

    def set_paused(self, paused):
        self.paused = paused

    def reset_extremes(self):
        for item in self.sensors:
            item.clear_extremes()

    def stop(self):
        pass


class ConfigTests(unittest.TestCase):
    def test_old_busy_polling_config_is_clamped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"interval_s": .005}), encoding="utf-8")
            self.assertEqual(load(path).interval_s, .5)

    def test_safe_defaults_keep_logging_less_frequent_than_sampling(self):
        config = Config()
        self.assertEqual(config.interval_s, 2.0)
        self.assertEqual(config.log_interval_s, 5.0)
        self.assertFalse(config.animations)

    def test_collector_clamps_programmatic_intervals_and_throttles_disk_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(interval_s=.005, db_path=str(Path(directory) / "test.db"))
            with mock.patch("vrmmon.collector.build_providers", return_value=[]):
                collector = Collector(config)
            try:
                self.assertEqual(collector.interval_s, .5)
                with mock.patch.object(collector.store, "size_bytes", return_value=123) as stat:
                    for timestamp in range(100, 110):
                        collector._tick(timestamp)
                    stat.assert_called_once()
                    collector._tick(110)
                    self.assertEqual(stat.call_count, 2)
            finally:
                collector.stop()


class LightweightUI(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.collector = FakeCollector()
        self.config = Config(sound=False, minimize_to_tray=False)
        with mock.patch.object(MainWindow, "_start_tray"):
            self.window = MainWindow(self.root, self.config, self.collector)
        self.root.update()
        self.window._refresh_once()

    def tearDown(self):
        self.window._closing = True
        self.root.destroy()

    def test_brand_assets_and_no_canvas_or_animation(self):
        self.assertIn("VELTRIX", self.root.title())
        self.assertEqual(len(self.window._logo_images), 2)
        self.assertFalse(self.window._motion.busy)
        self.assertFalse(any(isinstance(w, tk.Canvas) for w in self.root.winfo_children()))

    def test_unchanged_sequence_skips_table(self):
        with mock.patch.object(self.window, "_update_tree") as update:
            self.window._refresh_once()
        update.assert_not_called()

    def test_new_sequence_with_same_values_does_not_write_cells(self):
        self.collector.sequence += 1
        with mock.patch.object(self.window.tree, "item", wraps=self.window.tree.item) as item:
            self.window._refresh_once()
        self.assertFalse(any("values" in call.kwargs or "tags" in call.kwargs
                             for call in item.call_args_list))

    def test_changed_values_reach_table(self):
        self.collector.sensors[0].observe(60, None, None)
        self.collector.sequence += 1
        self.window._refresh_once()
        self.assertEqual(self.window.tree.set(str(self.collector.sensors[0].id), "cur"), "60.0 °C")

    def test_hidden_window_skips_rendering_but_handles_alerts(self):
        self.root.withdraw()
        self.collector.sequence += 1
        self.collector.events = [AlertEvent(time.time(), "critical", "test", "hot")]
        self.window.tray = mock.Mock()
        with mock.patch.object(self.window, "_update_tree") as update:
            self.window._refresh_once()
        update.assert_not_called()
        self.window.tray.notify.assert_called_once()
        self.assertEqual(len(self.window._alert_log), 1)
        self.assertFalse(self.window._motion.busy)
        self.window.tray = None

    def test_restore_shows_latest_sample(self):
        self.root.withdraw()
        self.window._refresh_once()
        self.collector.sensors[0].observe(62, None, None)
        self.collector.sequence += 1
        self.root.deiconify()
        self.root.update()
        self.window._refresh_once()
        self.assertEqual(self.window._summary_labels["cpu"].cget("text"), "62.0 °C")

    def test_search_and_clear_preserve_readings(self):
        self.window._search.set("gpu")
        self.assertEqual(len(self.window._tree_values), 1)
        self.window._search.set("")
        self.assertEqual(len(self.window._tree_values), 2)

    def test_rebuild_preserves_collapsed_groups_and_selection(self):
        group = self.window.tree.get_children()[0]
        selected = self.window.tree.get_children(group)[0]
        self.window.tree.item(group, open=False)
        self.window.tree.selection_set(selected)
        self.collector.sensors.append(sensor("Core 2", Role.CPU_CORE, 52))
        self.collector.sequence += 1
        self.window._refresh_once()
        self.assertFalse(self.window.tree.item(group, "open"))
        self.assertIn(selected, self.window.tree.selection())

    def test_summary_never_substitutes_voltage_or_cpu_temp_for_vrm(self):
        self.collector.sensors.append(sensor("VRM voltage", Role.CPU_VRM, 1.2, Kind.VOLTAGE))
        self.collector.sequence += 1
        self.window._refresh_once()
        self.assertEqual(self.window._summary_labels["vrm"].cget("text"), "未提供")

    def test_refresh_has_exactly_one_scheduled_callback(self):
        self.window._refresh()
        old = self.window._refresh_after
        self.window._refresh()
        scheduled = self.root.tk.call("after", "info")
        self.assertNotIn(old, scheduled)
        self.assertIn(self.window._refresh_after, scheduled)

    def test_alert_history_stays_bounded(self):
        self.window._handle_alerts([AlertEvent(time.time(), "warn", "test", str(i))
                                    for i in range(600)])
        self.assertEqual(len(self.window._alert_log), 500)

    def test_sub_hertz_sampling_is_not_displayed_as_zero(self):
        self.window._update_status(self.collector.snapshot())
        self.assertIn("设定 0.5", self.window.status_label.cget("text"))


class TrayUpdates(unittest.TestCase):
    def test_tooltip_changes_do_not_regenerate_icon(self):
        try:
            from vrmmon.ui import tray
        except ImportError:
            self.skipTest("optional tray dependencies are not installed")
        instance = tray.Tray(mock.Mock())
        instance._icon = mock.Mock()
        with mock.patch.object(tray, "make_icon") as make:
            instance.update("CPU 50", "ok")
            instance.update("CPU 51", "ok")
            make.assert_called_once_with("ok")
            instance.update("CPU 90", "warn")
            self.assertEqual(make.call_count, 2)

    def test_tray_icon_is_cached_and_fits_square(self):
        try:
            from vrmmon.ui import tray
        except ImportError:
            self.skipTest("optional tray dependencies are not installed")
        image = tray.make_icon("ok")
        self.assertIs(image, tray.make_icon("ok"))
        left, top, right, bottom = image.getbbox()
        self.assertGreater(left, 0)
        self.assertGreater(top, 0)
        self.assertLess(right, image.width)
        self.assertLess(bottom, image.height)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    unittest.main(verbosity=2)
