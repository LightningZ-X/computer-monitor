"""端到端冒烟测试：真实读取 HWiNFO / NVML，跑一遍采集 → 入库 → 导出。

需要 HWiNFO64 正在运行（共享内存已开启）。
运行：python tests/smoke_collector.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from itertools import islice
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vrmmon.classify import ROLE_LABELS  # noqa: E402
from vrmmon.collector import Collector  # noqa: E402
from vrmmon.config import Config  # noqa: E402
from vrmmon.sensors import Kind, Role, format_value  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    with tempfile.TemporaryDirectory() as tmp:
        config = Config()
        config.db_path = str(Path(tmp) / "smoke.sqlite3")
        config.interval_s = 0.5

        collector = Collector(config)
        collector.start()

        # LHM 冷启动要枚举全部硬件（数秒），不能用固定等待——否则会偶发地
        # 在还没有任何采样时就去看结果。
        deadline = time.time() + 40
        snapshot = collector.snapshot()
        while time.time() < deadline and snapshot.written == 0:
            time.sleep(1.0)
            snapshot = collector.snapshot()
        # 再采一小段，保证历史曲线有点可画
        time.sleep(3.0)
        # 导出前必须先暂停并等在途的一轮写完：否则采集线程会在
        # export_csv() 与 stop() 之间再落一轮，导出行数必然比最终库行数少一轮。
        collector.set_paused(True)
        time.sleep(config.interval_s * 2 + 0.5)
        snapshot = collector.snapshot()

        print("=" * 70)
        print("数据源状态")
        for label, status in snapshot.statuses:
            print(f"  [{'OK ' if status.ok else 'FAIL'}] {label}: {status.state}"
                  f"{' - ' + status.detail if status.detail else ''}")

        print("=" * 70)
        print(f"传感器总数: {len(snapshot.sensors)}")
        temps = [s for s in snapshot.sensors if s.kind is Kind.TEMPERATURE]
        print(f"温度传感器: {len(temps)}")
        print(f"采样轮次: {snapshot.sequence}    已采集点数: {snapshot.written}")
        print(f"入库行数: {snapshot.rows}    库大小: {snapshot.db_bytes} 字节")

        print("=" * 70)
        print("供电相关传感器")
        vrm = [s for s in snapshot.sensors if s.is_vrm]
        if vrm:
            for sensor in vrm:
                print(f"  {ROLE_LABELS[sensor.role]:<16} {sensor.id.name} = "
                      f"{format_value(sensor.last_value, sensor.unit)}")
        else:
            print("  （无）")
        for sensor in snapshot.sensors:
            if sensor.is_alarm:
                state = "已置位" if sensor.triggered else "正常"
                print(f"  报警标记            {sensor.id.name} = {state}")

        print("=" * 70)
        print("告警事件")
        if snapshot.alerts:
            for event in snapshot.alerts:
                print(f"  [{event.level}] {event.title}: {event.message}")
        else:
            print("  （无）")

        csv_path = Path(tmp) / "export.csv"
        rows = collector.export_csv(csv_path)
        print("=" * 70)
        print(f"CSV 导出: {rows} 行, 文件 {csv_path.stat().st_size} 字节")
        with csv_path.open(encoding="utf-8-sig") as handle:
            for line in islice(handle, 3):
                print("  " + line.rstrip())

        # 退出后数据库应已落盘
        expected = snapshot.written
        collector.stop()
        reopened = sqlite3.connect(str(config.db_path))
        final_rows = reopened.execute("SELECT COUNT(*) FROM samples").fetchone()[0]

        # 时间戳必须是墙钟。
        # 回归保护：曾经把 time.monotonic()（开机以来秒数）当成数据时间戳写库，
        # 5,469,624 行的 ts 落在 1970 年——值没坏、时间全错，肉眼完全看不出来，
        # 直到导出和保留策略都失效才暴露。这里断言 ts 与当前墙钟在合理范围内。
        lowest, highest = reopened.execute(
            "SELECT MIN(ts), MAX(ts) FROM samples").fetchone()
        reopened.close()
        wall = time.time()
        drift = max(abs(wall - lowest), abs(wall - highest))
        print("=" * 70)
        print(f"时间戳: {datetime.fromtimestamp(lowest):%Y-%m-%d %H:%M:%S}"
              f" ~ {datetime.fromtimestamp(highest):%Y-%m-%d %H:%M:%S}")
        print(f"与墙钟最大偏差: {drift:.1f} 秒（必须远小于一天，否则是把 monotonic 写进去了）")

        print("=" * 70)
        print(f"重开数据库读到 {final_rows} 行（采集期间报告 {expected} 行）")
        ok = final_rows > 0 and rows == final_rows and drift < 3600
        if not ok:
            if not final_rows:
                print("失败 - 没有任何数据入库")
            elif rows != final_rows:
                print(f"失败 - 库里有 {final_rows} 行，导出了 {rows} 行（CSV 与数据库不一致）")
            else:
                print(f"失败 - 时间戳偏差 {drift:.1f} 秒，像是把 monotonic 写进了数据库")
        else:
            print("OK - 冒烟测试通过")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
