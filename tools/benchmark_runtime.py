"""Measure real collection with default polling and an isolated temporary DB.

python tools/benchmark_runtime.py --seconds 30
No elevation, driver installs, user DB changes or network calls are performed.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vrmmon.collector import Collector
from vrmmon.config import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 120:
        parser.error("--seconds must be between 5 and 120")
    with tempfile.TemporaryDirectory() as directory:
        config = Config(db_path=str(Path(directory) / "benchmark.db"), sound=False)
        collector = Collector(config)
        collector.start()
        try:
            deadline = time.monotonic() + 20
            while collector.snapshot().sequence == 0 and time.monotonic() < deadline:
                time.sleep(.1)
            first = collector.snapshot()
            wall, cpu = time.monotonic(), time.process_time()
            time.sleep(args.seconds)
            elapsed, used = time.monotonic()-wall, time.process_time()-cpu
            latest = collector.snapshot()
            report = {"workload": "real hardware collection, no UI, isolated temporary DB",
                      "interval_s": config.interval_s, "log_interval_s": config.log_interval_s,
                      "seconds": round(elapsed, 2), "samples": latest.sequence-first.sequence,
                      "sensors": len(latest.sensors), "cpu_seconds": round(used, 4),
                      "cpu_percent_one_core": round(100*used/elapsed, 3),
                      "cpu_percent_machine": round(100*used/elapsed/(os.cpu_count() or 1), 4)}
            try:
                import psutil
                report["rss_mb"] = round(psutil.Process().memory_info().rss/(1024**2), 1)
            except ImportError:
                pass
            print(json.dumps(report, indent=2))
        finally:
            collector.stop()


if __name__ == "__main__":
    main()
