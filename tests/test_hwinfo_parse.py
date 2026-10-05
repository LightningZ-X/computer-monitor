"""离线验证 HWiNFO 共享内存解析与传感器分类。

用合成缓冲区检查偏移量实现，不需要 HWiNFO 在运行。
运行：python tests/test_hwinfo_parse.py
"""

from __future__ import annotations

import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vrmmon.providers.hwinfo import (  # noqa: E402
    ENTRY_ID, ENTRY_NAME_ORIG, ENTRY_NAME_USER, ENTRY_SENSOR_INDEX, ENTRY_TYPE,
    ENTRY_UNIT, ENTRY_VALUE, ENTRY_VALUE_AVG, ENTRY_VALUE_MAX, ENTRY_VALUE_MIN,
    HEADER_MAGIC, HEADER_SIZE, MIN_ENTRY_SIZE, MIN_SENSOR_SIZE,
    OFF_ENTRY_COUNT, OFF_ENTRY_SECTION, OFF_ENTRY_SIZE, OFF_MAGIC,
    OFF_POLL_TIME, OFF_SENSOR_COUNT, OFF_SENSOR_SECTION, OFF_SENSOR_SIZE,
    SENSOR_ID, SENSOR_INSTANCE, SENSOR_NAME_ORIG, SENSOR_NAME_USER,
    ShmError, _ANSI_CODEC, parse_shared_memory,
)

#: 用真实结构体尺寸，确保测试覆盖与 HWiNFO v8.x 相同的对齐
SENSOR_SIZE = MIN_SENSOR_SIZE
ENTRY_SIZE = MIN_ENTRY_SIZE

#: 中文 Windows 上 HWiNFO 按 GBK 写字符串
CODEC = "cp936" if _ANSI_CODEC == "cp936" else _ANSI_CODEC


def _write_str(buf: bytearray, offset: int, length: int, text: str) -> None:
    raw = text.encode(CODEC) + b"\x00"
    assert len(raw) <= length, f"字符串过长: {text!r}"
    buf[offset:offset + len(raw)] = raw


def build_buffer(sensors: list[str], entries: list[tuple[int, int, str, str, float]],
                 poll_ms: int = 100) -> bytes:
    """entries 每项为 (type, sensor_index, name, unit, value)。"""
    sensor_off = HEADER_SIZE
    entry_off = sensor_off + SENSOR_SIZE * len(sensors)
    total = entry_off + ENTRY_SIZE * len(entries)
    buf = bytearray(total)

    struct.pack_into("<I", buf, OFF_MAGIC, HEADER_MAGIC)
    struct.pack_into("<I", buf, 0x04, 1)          # version
    struct.pack_into("<I", buf, 0x08, 2)          # version2
    struct.pack_into("<q", buf, 0x0C, 0)          # last_update
    struct.pack_into("<I", buf, OFF_SENSOR_SECTION, sensor_off)
    struct.pack_into("<I", buf, OFF_SENSOR_SIZE, SENSOR_SIZE)
    struct.pack_into("<I", buf, OFF_SENSOR_COUNT, len(sensors))
    struct.pack_into("<I", buf, OFF_ENTRY_SECTION, entry_off)
    struct.pack_into("<I", buf, OFF_ENTRY_SIZE, ENTRY_SIZE)
    struct.pack_into("<I", buf, OFF_ENTRY_COUNT, len(entries))
    struct.pack_into("<I", buf, OFF_POLL_TIME, poll_ms)

    for i, sensor_name in enumerate(sensors):
        base = sensor_off + i * SENSOR_SIZE
        struct.pack_into("<I", buf, base + SENSOR_ID, i)
        struct.pack_into("<I", buf, base + SENSOR_INSTANCE, 0)
        _write_str(buf, base + SENSOR_NAME_ORIG, 128, sensor_name)
        # 故意留空 name_user，验证回退到 name_original 的逻辑

    for i, (kind, idx, name, unit, value) in enumerate(entries):
        base = entry_off + i * ENTRY_SIZE
        struct.pack_into("<I", buf, base + ENTRY_TYPE, kind)
        struct.pack_into("<I", buf, base + ENTRY_SENSOR_INDEX, idx)
        struct.pack_into("<I", buf, base + ENTRY_ID, i)
        _write_str(buf, base + ENTRY_NAME_ORIG, 128, name)
        _write_str(buf, base + ENTRY_UNIT, 16, unit)
        struct.pack_into("<d", buf, base + ENTRY_VALUE, value)
        struct.pack_into("<d", buf, base + ENTRY_VALUE_MIN, value - 5.0)
        struct.pack_into("<d", buf, base + ENTRY_VALUE_MAX, value + 5.0)
        struct.pack_into("<d", buf, base + ENTRY_VALUE_AVG, value)

    return bytes(buf)


class TestParse(unittest.TestCase):
    def test_parses_sensors_and_entries(self) -> None:
        sensor_names = ["Nuvoton NCT6687D", "GPU0 RTX 5080"]
        entries = [
            (1, 0, "VRM MOS", "°C", 61.5),
            (1, 0, "System", "°C", 41.0),
            (1, 1, "GPU Hot Spot", "°C", 78.25),
            (2, 0, "Vcore", "V", 1.32),
        ]
        parsed, poll_ms = parse_shared_memory(build_buffer(sensor_names, entries))

        self.assertEqual(poll_ms, 100)
        self.assertEqual(len(parsed), 4)
        self.assertEqual(parsed[0].group, "Nuvoton NCT6687D")
        self.assertEqual(parsed[0].name, "VRM MOS")
        self.assertAlmostEqual(parsed[0].value, 61.5)
        self.assertAlmostEqual(parsed[0].vmin, 56.5)
        self.assertAlmostEqual(parsed[0].vmax, 66.5)
        self.assertEqual(parsed[0].unit, "°C")
        self.assertEqual(parsed[2].group, "GPU0 RTX 5080")
        self.assertAlmostEqual(parsed[2].value, 78.25)

    @unittest.skipUnless(CODEC == "cp936", "仅在中文 Windows 上验证 GBK 解码")
    def test_decodes_chinese_names_from_gbk(self) -> None:
        """真实环境：HWiNFO 中文版把"封装"按 GBK 写入共享内存。"""
        buf = build_buffer(
            ["CPU [#0]: Intel Core Ultra 9 275HX: Enhanced"],
            [(1, 0, "CPU 封装", "°C", 72.0),
             (1, 0, "GPU 热点温度", "°C", 65.0),
             (1, 0, "显存结温", "°C", 68.0)],
        )
        # 确认字节层面确实是 GBK 而不是 cp1252
        self.assertIn("封装".encode("cp936"), buf)

        parsed, _ = parse_shared_memory(buf)
        self.assertEqual([e.name for e in parsed], ["CPU 封装", "GPU 热点温度", "显存结温"])
        self.assertEqual(parsed[0].unit, "°C")

    def test_rejects_bad_magic(self) -> None:
        buf = bytearray(build_buffer(["A"], [(1, 0, "X", "°C", 1.0)]))
        struct.pack_into("<I", buf, OFF_MAGIC, 0xDEADBEEF)
        with self.assertRaises(ShmError):
            parse_shared_memory(bytes(buf))

    def test_rejects_out_of_range_offsets(self) -> None:
        buf = bytearray(build_buffer(["A"], [(1, 0, "X", "°C", 1.0)]))
        struct.pack_into("<I", buf, OFF_ENTRY_COUNT, 999999)  # 超过 MAX_ENTRY_COUNT
        with self.assertRaises(ShmError):
            parse_shared_memory(bytes(buf))

    def test_rejects_shrunk_struct_sizes(self) -> None:
        buf = bytearray(build_buffer(["A"], [(1, 0, "X", "°C", 1.0)]))
        struct.pack_into("<I", buf, OFF_SENSOR_SIZE, 16)
        with self.assertRaises(ShmError):
            parse_shared_memory(bytes(buf))

    def test_rejects_short_buffer(self) -> None:
        with self.assertRaises(ShmError):
            parse_shared_memory(b"\x00" * 8)


class TestClassify(unittest.TestCase):
    def test_english_vrm_and_gpu(self) -> None:
        from vrmmon.classify import classify
        from vrmmon.sensors import Kind, Role

        self.assertEqual(classify("Nuvoton NCT6687D", "VRM MOS", Kind.TEMPERATURE)[0], Role.CPU_VRM)
        self.assertEqual(classify("Nuvoton NCT6687D", "VRM", Kind.TEMPERATURE)[0], Role.CPU_VRM)
        self.assertEqual(classify("ASUS ROG", "CPU VRM Temperature", Kind.TEMPERATURE)[0], Role.CPU_VRM)
        self.assertEqual(classify("GPU0 RTX 5080", "GPU VRM", Kind.TEMPERATURE)[0], Role.GPU_VRM)
        self.assertEqual(classify("GPU0 RTX 5080", "GPU Hot Spot", Kind.TEMPERATURE)[0], Role.GPU_HOTSPOT)
        self.assertEqual(classify("Intel CPU", "CPU Package", Kind.TEMPERATURE)[0], Role.CPU_PACKAGE)
        self.assertEqual(classify("Nuvoton", "VRM", Kind.VOLTAGE)[0], Role.OTHER)

    def test_chinese_names_observed_on_this_machine(self) -> None:
        """这些名称取自本机 HWiNFO 8.54 的真实输出。"""
        from vrmmon.classify import classify
        from vrmmon.sensors import Kind, Role

        cases = [
            ("CPU [#0]: Intel Core Ultra 9 275HX: DTS", "CPU 封装", Role.CPU_PACKAGE),
            ("CPU [#0]: Intel Core Ultra 9 275HX: DTS", "P-core 0", Role.CPU_CORE),
            ("CPU [#0]: Intel Core Ultra 9 275HX: Enhanced", "CPU IA 核心", Role.CPU_CORE),
            ("CPU [#0]: Intel Core Ultra 9 275HX: Enhanced", "CPU GT 核心 (核显)", Role.CPU_SOC),
            ("dGPU [#2]: NVIDIA GeForce RTX 5080 Laptop", "GPU 温度", Role.GPU_CORE),
            ("dGPU [#2]: NVIDIA GeForce RTX 5080 Laptop", "GPU 热点温度", Role.GPU_HOTSPOT),
            ("dGPU [#2]: NVIDIA GeForce RTX 5080 Laptop", "显存 A0 温度", Role.GPU_MEMORY),
            ("dGPU [#2]: NVIDIA GeForce RTX 5080 Laptop", "显存结温", Role.GPU_MEMORY),
            ("ASUS G835LW (Intel PCH)", "PCH 温度", Role.BOARD),
            ("ASUS G835LW (Intel PCH)", "VRM 供电温度", Role.CPU_VRM),
        ]
        for group, name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(classify(group, name, Kind.TEMPERATURE)[0], expected)

    def test_relative_and_limit_fields_are_not_temperatures(self) -> None:
        """「与 TjMAX 的差值」是相对量，「过热限制」是标记，都不能当温度画曲线。"""
        from vrmmon.classify import classify
        from vrmmon.sensors import Kind, Role

        self.assertEqual(
            classify("DTS", "E-core 14 与 TjMAX 的差值", Kind.TEMPERATURE)[0], Role.META)
        self.assertEqual(
            classify("dGPU [#2]: RTX 5080", "GPU 过热限制", Kind.TEMPERATURE)[0], Role.META)

    def test_vrm_over_temperature_alarm_is_recognised(self) -> None:
        """读不到 VRM 温度时的替代信号：CPU 上报的供电过热报警标志。"""
        from vrmmon.classify import classify
        from vrmmon.sensors import Kind, Role

        group = "CPU [#0]: Intel Core Ultra 9 275HX: 性能受限原因"
        self.assertEqual(classify(group, "IA: VR 过热警报", Kind.OTHER)[0], Role.VRM_ALARM)
        self.assertEqual(classify(group, "GT: VR 过热警报", Kind.OTHER)[0], Role.VRM_ALARM)
        # 同一分组里的其他限制原因不能被误判成报警
        self.assertEqual(classify(group, "IA: VR TDC", Kind.OTHER)[0], Role.OTHER)
        self.assertEqual(
            classify(group, "RING: Max VR Voltage, ICCmax, PL4", Kind.OTHER)[0], Role.OTHER)


class TestExtremes(unittest.TestCase):
    def test_reset_stops_using_source_extremes(self) -> None:
        """点过「重置极值」后，不该再显示 HWiNFO 自带的（更早的）极值。"""
        from vrmmon.sensors import Kind, Registry, Role, SensorId

        registry = Registry()
        sensor = registry.get(SensorId("t", "g", "n"), Kind.TEMPERATURE, "°C",
                              Role.CPU_CORE, "")
        sensor.observe(70.0, 40.0, 95.0)
        self.assertEqual(sensor.bounds(), (40.0, 95.0))

        sensor.clear_extremes()
        self.assertEqual(sensor.bounds(), (None, None))

        sensor.observe(72.0, 40.0, 95.0)
        self.assertEqual(sensor.bounds(), (72.0, 72.0))


    def test_lhm_thresholds_and_metadata_are_not_temperatures(self) -> None:
        """LHM 的温度列表里混着差值、阈值和传感器元数据，都不是当前温度。"""
        from vrmmon.classify import classify
        from vrmmon.sensors import Kind, Role

        cases = [
            ("Cpu: Intel Core Ultra 9 275HX", "P-Core #1 Distance to TjMax"),
            ("Memory: SK Hynix - HMCG78AGBSA095N (#1)", "Temperature Sensor Resolution"),
            ("Memory: SK Hynix - HMCG78AGBSA095N (#1)", "Thermal Sensor High Limit"),
            ("Memory: SK Hynix - HMCG78AGBSA095N (#1)", "Thermal Sensor Critical High Limit"),
            ("Storage: HFS001TEJ9X125N", "Critical Temperature"),
            ("Storage: HFS001TEJ9X125N", "Warning Temperature"),
        ]
        for group, name in cases:
            with self.subTest(name=name):
                role, reason = classify(group, name, Kind.TEMPERATURE)
                self.assertEqual(role, Role.META, f"{name} -> {role}（{reason}）")

    def test_lhm_real_temperature_names(self) -> None:
        """这些名称取自本机 LHM 提权后的真实输出。"""
        from vrmmon.classify import classify
        from vrmmon.sensors import Kind, Role

        cpu = "Cpu: Intel Core Ultra 9 275HX"
        gpu = "GpuNvidia: NVIDIA GeForce RTX 5080 Laptop GPU"
        memory = "Memory: SK Hynix - HMCG78AGBSA095N (#1)"
        cases = [
            (cpu, "Core Max", Role.CPU_CORE),
            (cpu, "Core Average", Role.CPU_CORE),
            (gpu, "GPU Hot Spot", Role.GPU_HOTSPOT),
            (gpu, "GPU Memory Junction", Role.GPU_MEMORY),
            (gpu, "GPU Memory #3", Role.GPU_MEMORY),
            (memory, "DIMM #1", Role.OTHER),
        ]
        for group, name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(classify(group, name, Kind.TEMPERATURE)[0], expected)


class TestAlerts(unittest.TestCase):
    def test_real_temperatures_with_other_role_still_alert(self) -> None:
        """SSD/内存温度的角色是 OTHER，但它们是真实温度，必须能触发告警。

        （只有 META——差值/阈值/分辨率——才应该被排除。）
        """
        from vrmmon.alerts import AlertEngine
        from vrmmon.config import Thresholds
        from vrmmon.sensors import Kind, Registry, Role, SensorId

        registry = Registry()
        engine = AlertEngine(Thresholds(warn=85.0, critical=95.0))

        ssd = registry.get(SensorId("t", "Storage: X", "Temperature"),
                           Kind.TEMPERATURE, "°C", Role.OTHER, "")
        ssd.observe(90.0, None, None)

        meta = registry.get(SensorId("t", "Cpu: Y", "Core #1 Distance to TjMax"),
                            Kind.TEMPERATURE, "°C", Role.META, "")
        meta.observe(90.0, None, None)

        events = engine.evaluate([ssd, meta], 1000.0)
        self.assertEqual([event.level for event in events], ["warn"])
        self.assertIn("Temperature", events[0].message)

    def test_vrm_alarm_flag_fires(self) -> None:
        from vrmmon.alerts import AlertEngine
        from vrmmon.config import Thresholds
        from vrmmon.sensors import Kind, Registry, Role, SensorId

        registry = Registry()
        engine = AlertEngine(Thresholds(), alert_on_vrm_flag=True)
        flag = registry.get(SensorId("t", "Cpu: Y", "IA: VR 过热警报"),
                            Kind.OTHER, "Yes/No", Role.VRM_ALARM, "")
        flag.observe(1.0, None, None)
        events = engine.evaluate([flag], 1000.0)
        self.assertEqual([event.level for event in events], ["alarm"])

        # 同一个报警位在冷却时间内不应重复触发
        self.assertEqual(engine.evaluate([flag], 1001.0), [])


class TestRegistry(unittest.TestCase):
    def test_same_name_different_kind_are_distinct(self) -> None:
        """同名同组但不同量必须分开。

        真实案例：LibreHardwareMonitor 的 "CPU Package" 同时是温度和功耗，
        "P-Core #1" 同时是电压和频率。SensorId 不含 kind 时它们会被合并，
        功耗会把温度覆盖掉，温度那一路数据被静默丢弃。
        """
        from vrmmon.sensors import Kind, Registry, Role, SensorId

        registry = Registry()
        temperature = registry.get(
            SensorId("lhm", "Cpu: X", "CPU Package", Kind.TEMPERATURE.value),
            Kind.TEMPERATURE, "°C", Role.CPU_PACKAGE, "")
        temperature.observe(61.0, None, None)

        power = registry.get(
            SensorId("lhm", "Cpu: X", "CPU Package", Kind.POWER.value),
            Kind.POWER, "W", Role.OTHER, "")
        power.observe(45.0, None, None)

        self.assertIsNot(temperature, power)
        self.assertEqual(temperature.last_value, 61.0)
        self.assertEqual(power.last_value, 45.0)
        self.assertEqual(len(registry), 2)
        self.assertEqual(temperature.kind, Kind.TEMPERATURE)
        self.assertEqual(power.kind, Kind.POWER)

    def test_role_is_refreshed_on_each_read(self) -> None:
        from vrmmon.sensors import Kind, Registry, Role, SensorId

        registry = Registry()
        sensor_id = SensorId("lhm", "Cpu: X", "Core Max", Kind.TEMPERATURE.value)
        registry.get(sensor_id, Kind.TEMPERATURE, "°C", Role.OTHER, "")
        refreshed = registry.get(sensor_id, Kind.TEMPERATURE, "°C", Role.CPU_CORE, "核心")
        self.assertEqual(refreshed.role, Role.CPU_CORE)


class TestLogging(unittest.TestCase):
    def test_exception_is_written_with_context(self) -> None:
        """pythonw 启动时没有控制台，异常必须落盘，否则进程无声死掉无从排查。"""
        from vrmmon import logging_setup

        with tempfile.TemporaryDirectory() as tmp:
            original_error = logging_setup.LOG_PATH
            original_life = logging_setup.LIFECYCLE_PATH
            logging_setup.LOG_PATH = Path(tmp) / "err.log"
            logging_setup.LIFECYCLE_PATH = Path(tmp) / "life.log"
            try:
                try:
                    raise ValueError("故意抛出的测试异常")
                except ValueError:
                    logging_setup.log_exception(*sys.exc_info(), context="单元测试")
                text = logging_setup.LOG_PATH.read_text(encoding="utf-8")
            finally:
                logging_setup.LOG_PATH = original_error
                logging_setup.LIFECYCLE_PATH = original_life

        self.assertIn("单元测试", text)
        self.assertIn("故意抛出的测试异常", text)
        self.assertIn("ValueError", text)

    def test_logging_never_raises(self) -> None:
        """记日志本身失败时不允许再抛异常。"""
        from vrmmon import logging_setup

        original_error = logging_setup.LOG_PATH
        original_life = logging_setup.LIFECYCLE_PATH
        # 指向一个不可能创建的路径
        logging_setup.LOG_PATH = Path("Z:\\不存在的盘\\x.log")
        logging_setup.LIFECYCLE_PATH = Path("Z:\\不存在的盘\\y.log")
        try:
            logging_setup.log_message("测试")
            logging_setup.log_exception(ValueError, ValueError("x"), None)
        finally:
            logging_setup.LOG_PATH = original_error
            logging_setup.LIFECYCLE_PATH = original_life


class TestShutdownChannel(unittest.TestCase):
    """关闭通道。用独立事件名，避免误伤正在运行的真实实例。"""

    NAME = "Local\\vrmmon-test-shutdown"

    def setUp(self) -> None:
        if sys.platform != "win32":
            self.skipTest("仅 Windows")

    def test_detect_and_signal(self) -> None:
        from vrmmon import shutdown

        self.assertFalse(shutdown.is_app_running(self.NAME))
        self.assertFalse(shutdown.request_shutdown(self.NAME))

        channel = shutdown.EventChannel(self.NAME)
        self.assertTrue(channel.open())
        try:
            self.assertTrue(shutdown.is_app_running(self.NAME))
            fired: list[bool] = []
            channel.wait(lambda: fired.append(True))
            self.assertTrue(shutdown.request_shutdown(self.NAME))
            for _ in range(50):
                if fired:
                    break
                time.sleep(0.05)
            self.assertEqual(fired, [True])
        finally:
            channel.close()

    def test_event_disappears_after_close(self) -> None:
        from vrmmon import shutdown

        channel = shutdown.EventChannel(self.NAME)
        self.assertTrue(channel.open())
        self.assertTrue(shutdown.is_app_running(self.NAME))
        channel.close()
        self.assertFalse(shutdown.is_app_running(self.NAME))

    def test_show_event_can_fire_repeatedly(self) -> None:
        """唤出窗口用的事件必须能反复触发（收到后要手动重置）。"""
        from vrmmon import shutdown

        name = "Local\\vrmmon-test-show"
        channel = shutdown.EventChannel(name)
        self.assertTrue(channel.open())
        try:
            fired: list[int] = []
            channel.wait(lambda: fired.append(len(fired)))
            for expected in (1, 2):
                self.assertTrue(shutdown.signal(name))
                for _ in range(60):
                    if len(fired) >= expected:
                        break
                    time.sleep(0.05)
                self.assertEqual(len(fired), expected, f"第 {expected} 次未被触发")
        finally:
            channel.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
