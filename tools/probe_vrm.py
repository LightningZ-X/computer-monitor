"""判断某个温度通道"是不是供电温度"。

思路：VRM / 供电区探头贴着 CPU 供电模块，其温度必然跟着 CPU 功耗走；
而 PCH、SSD、内存这些通道跟着自己的负载走，与 CPU 功耗弱相关。

所以用已录制的历史数据，算每个温度通道与 CPU 负载参考信号（CPU 封装温度）
的皮尔逊相关系数。相关系数高且波动明显的通道，就是供电区探头的嫌疑对象。

用法：
    python tools/probe_vrm.py                 # 用全部历史数据
    python tools/probe_vrm.py --minutes 30    # 只用最近 30 分钟
    python tools/probe_vrm.py --top 25        # 只看相关性最高的 25 条
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / "data" / "vrmmon.sqlite3"

#: CPU 负载参考信号的候选（按优先级，同时覆盖 HWiNFO 与 LHM 的命名）
REFERENCE_CANDIDATES = (
    "核心最大值",      # HWiNFO
    "Core Max",       # LHM
    "CPU 封装",        # HWiNFO
    "Core Average",   # LHM
    "最高核心",        # HWiNFO
)
#: 功耗参考信号（用于交叉验证）
POWER_HINTS = ("VR VCC 电流", "CPU Package", "CPU 封装功耗", "CPU 功耗")


def open_readonly(path: Path) -> sqlite3.Connection:
    posix = str(path).replace("\\", "/")
    return sqlite3.connect(f"file:{quote(posix, safe='/:')}?mode=ro", uri=True)


def pearson(pairs: list[tuple[float, float]]) -> float | None:
    n = len(pairs)
    if n < 8:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="probe_vrm", description="找出供电温度嫌疑通道")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--minutes", type=float, default=0,
                        help="只用最近 N 分钟的数据（0 表示全部）")
    parser.add_argument("--top", type=int, default=0, help="只显示相关性最高的 N 条")
    parser.add_argument("--all", action="store_true",
                        help="连同'与 TjMAX 的差值'这类伪温度一起显示")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"找不到数据库: {db_path}")
        return 2

    conn = open_readonly(db_path)
    try:
        since = 0.0
        if args.minutes > 0:
            row = conn.execute("SELECT MAX(ts) FROM samples").fetchone()
            since = (row[0] or 0) - args.minutes * 60

        series: dict[str, dict[float, float]] = defaultdict(dict)
        kinds: dict[str, str] = {}
        roles: dict[str, str] = {}
        for ts, grp, name, kind, role, value in conn.execute(
                "SELECT ts, provider || '/' || grp, name, kind, role, value"
                " FROM samples WHERE ts >= ? ORDER BY ts", (since,)):
            key = f"{grp} / {name}"
            series[key][ts] = value
            kinds[key] = kind
            roles[key] = role
    finally:
        conn.close()

    if not series:
        print("数据库里没有数据。")
        return 1

    timestamps = sorted({ts for values in series.values() for ts in values})
    span_min = (timestamps[-1] - timestamps[0]) / 60 if len(timestamps) > 1 else 0
    print(f"数据: {db_path}")
    print(f"时间跨度: {span_min:.1f} 分钟    采样轮次: {len(timestamps)}    "
          f"序列数: {len(series)}")

    # 选参考信号
    reference_key = None
    for candidate in REFERENCE_CANDIDATES:
        for key in series:
            if key.endswith(f"/ {candidate}"):
                reference_key = key
                break
        if reference_key:
            break
    if reference_key is None:
        print("找不到 CPU 负载参考信号（需要 CPU 封装/最高核心温度），无法做相关性分析。")
        return 1
    print(f"参考信号（CPU 负载代理）: {reference_key}")

    power_key = None
    for hint in POWER_HINTS:
        for key in series:
            if hint in key:
                power_key = key
                break
        if power_key:
            break
    if power_key:
        print(f"功耗交叉验证信号: {power_key}")
    print()

    ref = series[reference_key]
    ref_values = list(ref.values())
    ref_mean = sum(ref_values) / len(ref_values)
    ref_stdev = math.sqrt(sum((v - ref_mean) ** 2 for v in ref_values) / len(ref_values))
    print(f"参考信号波动范围: {min(ref_values):.1f} ~ {max(ref_values):.1f} °C   "
          f"标准差 {ref_stdev:.2f}")
    print()

    results = []
    for key, values in series.items():
        if kinds[key] != "Temperature":
            continue
        # 默认排除"与 TjMax 的差值"这类伪温度（Role.META）；
        # Role.OTHER 是 SSD / 内存 SPD 这类真实温度，必须保留
        if not args.all and roles.get(key, "other") == "meta":
            continue
        ordered = sorted(values.values())
        mean = sum(ordered) / len(ordered)
        stdev = math.sqrt(sum((v - mean) ** 2 for v in ordered) / len(ordered))
        pairs = [(ref[ts], values[ts]) for ts in values if ts in ref]
        r = pearson(pairs)
        results.append((key, min(ordered), max(ordered), mean, stdev, r, len(pairs)))

    def split_key(key: str) -> tuple[str, str]:
        group, _, name = key.rpartition(" / ")
        group = group.replace("hwinfo/", "").replace("nvml/", "")
        if ":" in group:
            group = group.rsplit(":", 1)[1].strip()
        return group, name

    print("=" * 100)
    print("温度通道（按与 CPU 负载的相关性降序）")
    print("=" * 100)
    print(f"{'传感器':<30}{'来源硬件':<24}{'最小':>7}{'最大':>7}{'均值':>7}{'波动':>7}{'相关':>7}")
    print("-" * 100)
    ranked = sorted(results, key=lambda item: -(abs(item[5]) if item[5] is not None else -1))
    shown = ranked[:args.top] if args.top else ranked
    for key, lo, hi, mean, stdev, r, n in shown:
        group, name = split_key(key)
        r_text = "n/a" if r is None else f"{r:+.2f}"
        if stdev < 0.5:
            flag = "  ← 无波动，无信息"
        elif r is not None and r >= 0.8:
            flag = "  ← 强相关，供电区嫌疑"
        else:
            flag = ""
        print(f"{name[:28]:<30}{group[:22]:<24}{lo:>7.1f}{hi:>7.1f}"
              f"{mean:>7.1f}{stdev:>7.2f}{r_text:>7}{flag}")

    if power_key:
        print()
        print(f"功耗信号 {power_key}: "
              f"{min(series[power_key].values()):.2f} ~ "
              f"{max(series[power_key].values()):.2f}")
    print()
    print("说明：相关系数是相对 CPU 负载代理信号算的。若参考信号本身波动很小"
          "（标准差 < 1 °C），说明这段时间机器基本空闲，结论不可靠——"
          "需要在负载下重新录制。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
