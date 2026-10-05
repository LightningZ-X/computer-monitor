"""命令行体检：列出所有数据源，并判断本机能否读到供电(VRM)温度。

用法：
    python -m vrmmon.doctor                      # 只看温度传感器
    python -m vrmmon.doctor --all                # 列出全部读数
    python -m vrmmon.doctor --grep VR            # 只列名称匹配的传感器
    python -m vrmmon.doctor --out report.txt     # 写成 UTF-8 文件，避免控制台 GBK 乱码
"""

from __future__ import annotations

import argparse
import re
import sys

from .classify import ROLE_LABELS
from .providers import build_providers
from .sensors import Kind, Registry, Role, format_value


def _force_utf8() -> None:
    """传感器名含 ° 等字符，Windows 控制台默认 GBK，直接打印会崩。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()

    parser = argparse.ArgumentParser(prog="vrmmon.doctor", description="检查供电温度可读性")
    parser.add_argument("--all", action="store_true", help="列出全部读数，而不只是温度")
    parser.add_argument("--provider", help="只看某个数据源 (hwinfo / nvml)")
    parser.add_argument("--out", help="把报告写成 UTF-8 文件（绕开 PowerShell 重定向的代码页转换）")
    parser.add_argument("--names-only", action="store_true", help="只列传感器名与角色")
    parser.add_argument("--grep", help="只列名称匹配该正则的传感器")
    parser.add_argument("--groups", action="store_true",
                        help="按硬件分组汇总读数个数（判断某类传感器是否存在时最有用）")
    args = parser.parse_args(argv)

    if args.out:
        sys.stdout = open(args.out, "w", encoding="utf-8", newline="\n")

    providers = build_providers()
    if args.provider:
        providers = [p for p in providers if p.key == args.provider]
        if not providers:
            print(f"没有名为 {args.provider} 的数据源", file=sys.stderr)
            return 2

    registry = Registry()

    print("=" * 78)
    print("数据源状态")
    print("=" * 78)
    any_ok = False
    for provider in providers:
        status = provider.probe()
        any_ok = any_ok or status.ok
        print(f"[{'OK  ' if status.ok else 'FAIL'}] {provider.label} ({provider.key})")
        print(f"       状态: {status.state}")
        if status.detail:
            print(f"       说明: {status.detail}")
        if status.ok:
            provider.read(registry)

    if not any_ok:
        print("\n所有数据源都不可用，无法取数。")
        return 1

    if args.groups:
        print()
        print("=" * 78)
        print("按硬件分组汇总")
        print("=" * 78)
        buckets: dict[str, list[int]] = {}
        for sensor in registry.all():
            bucket = buckets.setdefault(sensor.id.group, [0, 0])
            bucket[0] += 1
            if sensor.kind is Kind.TEMPERATURE:
                bucket[1] += 1
        for group, (total, temps) in sorted(buckets.items(), key=lambda kv: -kv[1][0]):
            print(f"  {total:5} 读数 / {temps:3} 温度   {group}")
        print(f"\n  合计 {len(registry.all())} 读数 / "
              f"{sum(1 for s in registry.all() if s.kind is Kind.TEMPERATURE)} 温度")
        return 0

    sensors = [s for s in registry.all()
               if args.all or (s.kind is Kind.TEMPERATURE and s.role is not Role.META)]
    if args.grep:
        pattern = re.compile(args.grep, re.IGNORECASE)
        sensors = [s for s in sensors if pattern.search(f"{s.id.group} {s.id.name}")]
    order = {role.value: i for i, role in enumerate(ROLE_LABELS)}
    sensors.sort(key=lambda s: (order.get(s.role.value, 99), s.id.group, s.id.name))

    print()
    print("=" * 78)
    print("发现的传感器" if args.all else "发现的温度传感器（按角色排序）")
    print("=" * 78)

    if not sensors:
        print("(无)")
    current_group = None
    for sensor in sensors:
        role = ROLE_LABELS.get(sensor.role, sensor.role.value)
        decimals = 1 if sensor.kind == Kind.TEMPERATURE else 2
        shown = format_value(sensor.last_value, sensor.unit, decimals)
        if args.names_only:
            print(f"{sensor.id.group} | {sensor.id.name} | {role}")
            continue
        group = f"{sensor.id.provider}/{sensor.id.group}"
        if group != current_group:
            print(f"\n-- {group}")
            current_group = group
        lo, hi = sensor.bounds()
        rng = f"   范围 {lo:.1f} ~ {hi:.1f}" if lo is not None and hi is not None else ""
        print(f"   {sensor.id.name:<30} {shown:>12}  {role:<14}{rng}")

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)

    vrm = registry.vrm()
    cpu = [s for s in vrm if s.role is Role.CPU_VRM]
    gpu = [s for s in vrm if s.role is Role.GPU_VRM]
    unknown = [s for s in vrm if s.role is Role.VRM_UNKNOWN]

    if cpu:
        print("[可读] CPU 供电温度: " + ", ".join(s.id.name for s in cpu))
    else:
        print("[缺失] 没有识别到 CPU 供电(VRM) 温度")
    if gpu:
        print("[可读] GPU 供电温度: " + ", ".join(s.id.name for s in gpu))
    else:
        print("[缺失] 没有识别到 GPU 供电(VRM) 温度")
    if unknown:
        print("[待确认] 疑似供电但归属不明: " + ", ".join(s.id.name for s in unknown))

    alarms = registry.by_role(Role.VRM_ALARM)
    if alarms:
        for sensor in alarms:
            state = "已触发!" if sensor.triggered else "正常"
            print(f"[报警] {sensor.id.group} / {sensor.id.name} = "
                  f"{format_value(sensor.last_value, sensor.unit, 2)} ({state})")

    # 没有 VRM 温度时，给出最接近的替代观测项，而不是留空
    if not cpu or not gpu:
        print()
        print("替代观测项（VRM 温度不可读时的最接近指标）：")
        if not cpu:
            proxies = registry.by_role(Role.CPU_PACKAGE, Role.CPU_CORE, Role.CPU_SOC)
            top = max((s.last_value for s in proxies if s.last_value is not None), default=None)
            names = ", ".join(sorted({s.id.name for s in proxies}))[:90]
            print(f"  CPU 侧: 最高 {format_value(top, '°C')}  ({names})")
        if not gpu:
            proxies = registry.by_role(Role.GPU_HOTSPOT, Role.GPU_MEMORY, Role.GPU_CORE)
            top = max((s.last_value for s in proxies if s.last_value is not None), default=None)
            names = ", ".join(sorted({s.id.name for s in proxies}))[:90]
            print(f"  GPU 侧: 最高 {format_value(top, '°C')}  ({names})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
