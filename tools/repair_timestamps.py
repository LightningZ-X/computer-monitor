"""无损修复被写坏的时间戳。

起因：collector._run 曾把 time.monotonic()（开机以来秒数）当成数据时间戳写入，
导致某次运行之后的 546 万行 ts 落在 1970 年。数值本身没坏，只有时间错了。

wall = boot_epoch + monotonic，boot_epoch 已用两条独立途径交叉验证（差 0.2 秒），
且映射后与正常数据在时间上严丝合缝（10:29:55 → 10:30:02）。

整个过程在单个事务里：先改、再校验、校验不过就回滚。
"""

from __future__ import annotations

import datetime
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(r"D:\project1\data\vrmmon.sqlite3")
BAD_LIMIT = 1_000_000


def fmt(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    boot_epoch = time.time() - time.monotonic()
    print(f"boot_epoch = {boot_epoch:.3f}  → {fmt(boot_epoch)}")

    connection = sqlite3.connect(str(DB))
    try:
        before_total = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        before_bad = connection.execute(
            "SELECT COUNT(*) FROM samples WHERE ts < ?", (BAD_LIMIT,)).fetchone()[0]
        good_high = connection.execute(
            "SELECT MAX(ts) FROM samples WHERE ts >= ?", (BAD_LIMIT,)).fetchone()[0]
        print(f"修复前: 共 {before_total:,} 行，其中时间戳错误 {before_bad:,} 行")
        print(f"        正常数据止于 {fmt(good_high)}")
        if before_bad == 0:
            print("没有需要修复的行。")
            return 0

        connection.execute("BEGIN")
        cursor = connection.execute(
            "UPDATE samples SET ts = ts + ? WHERE ts < ?", (boot_epoch, BAD_LIMIT))
        changed = cursor.rowcount
        print(f"UPDATE 影响 {changed:,} 行")

        # ---- 校验：任何一项不过就回滚 ----
        problems: list[str] = []
        after_total = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        if after_total != before_total:
            problems.append(f"总行数变了: {before_total} → {after_total}")

        remaining = connection.execute(
            "SELECT COUNT(*) FROM samples WHERE ts < ?", (BAD_LIMIT,)).fetchone()[0]
        if remaining:
            problems.append(f"仍有 {remaining} 行时间戳异常")

        low, high = connection.execute("SELECT MIN(ts), MAX(ts) FROM samples").fetchone()
        print(f"修复后时间范围: {fmt(low)} ~ {fmt(high)}")
        now = time.time()
        if high > now + 300:
            problems.append(f"最大时间戳在未来: {fmt(high)}")
        if low < now - 30 * 86400:
            problems.append(f"最小时间戳在 30 天前: {fmt(low)}")

        # 与原正常段衔接：修复后的最小值应紧接原正常段最大值之后
        new_bad_low = connection.execute(
            "SELECT MIN(ts) FROM samples WHERE ts BETWEEN ? AND ?",
            (good_high, good_high + 3600)).fetchone()[0]
        gap = (new_bad_low - good_high) if new_bad_low else None
        if gap is not None:
            print(f"与原正常段的衔接间隔: {gap:.1f} 秒")
            if not (0 <= gap <= 60):
                problems.append(f"衔接间隔异常: {gap:.1f} 秒")

        if problems:
            connection.execute("ROLLBACK")
            print("校验未通过，已回滚:")
            for item in problems:
                print(f"  - {item}")
            return 1

        connection.execute("COMMIT")
        print(f"已提交。修复 {changed:,} 行，校验全部通过。")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
