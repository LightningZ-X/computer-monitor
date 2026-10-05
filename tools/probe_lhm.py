"""用 pythonnet 把 LibreHardwareMonitorLib 直接加载进 Python 进程读硬件传感器。

这是 vrmmon 摆脱 HWiNFO 依赖的关键验证：LibreHardwareMonitor 自带内核驱动，
能读 CPU MSR(DTS)、主板 Super I/O、SMBus(内存 SPD) 这些用户态拿不到的量。

用法：
    python tools/probe_lhm.py            # 默认 netfx + net472
    python tools/probe_lhm.py --clr coreclr
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="probe_lhm")
    parser.add_argument("--clr", default="netfx", choices=["netfx", "coreclr"])
    args = parser.parse_args(argv)

    # 必须在 import clr 之前设置，运行时只能初始化一次
    os.environ["PYTHONNET_RUNTIME"] = args.clr
    flat = PACKAGES / ("flat472" if args.clr == "netfx" else "flat8")
    if not flat.exists():
        print(f"缺少目录 {flat}")
        return 2

    sys.path.insert(0, str(flat))
    print(f"运行时: {args.clr}    程序集目录: {flat}")

    try:
        import clr  # noqa: F401
    except Exception as exc:
        print(f"pythonnet 初始化失败: {exc}")
        return 2

    try:
        clr.AddReference(str(flat / "LibreHardwareMonitorLib.dll"))
    except Exception as exc:
        print(f"加载 LibreHardwareMonitorLib 失败: {exc}")
        return 2

    from System import Environment  # noqa: E402
    from System.Security.Principal import WindowsIdentity, WindowsPrincipal, WindowsBuiltInRole  # noqa: E402

    identity = WindowsIdentity.GetCurrent()
    elevated = WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator)
    print(f"CLR 版本: {Environment.Version}")
    print(f"进程已提权: {elevated}")

    try:
        from LibreHardwareMonitor.Hardware import Computer  # noqa: E402
    except Exception as exc:
        print(f"导入 Computer 类型失败: {exc}")
        return 2

    computer = Computer()
    computer.IsCpuEnabled = True
    computer.IsMotherboardEnabled = True
    computer.IsControllerEnabled = True
    computer.IsMemoryEnabled = True
    computer.IsGpuEnabled = True
    computer.IsStorageEnabled = True
    computer.IsNetworkEnabled = False
    computer.IsPsuEnabled = True

    open_error = None
    try:
        computer.Open()
    except Exception as exc:
        open_error = f"{type(exc).__name__}: {exc}"
        print(f"Computer.Open() 抛异常: {open_error}")

    try:
        print(f"\n识别到的硬件顶层节点: {len(computer.Hardware)}")
        for hardware in computer.Hardware:
            print(f"  {hardware.HardwareType}: {hardware.Name}"
                  f"  (子硬件 {len(hardware.SubHardware)})")

        total = 0
        temps = 0
        print("\n" + "=" * 96)
        print("全部传感器")
        print("=" * 96)

        def walk(hardware, path: str) -> None:
            nonlocal total, temps
            try:
                hardware.Update()
            except Exception as exc:
                print(f"  !! {hardware.Name} Update 失败: {exc}")
            here = f"{path} / {hardware.Name}" if path else str(hardware.Name)
            for sensor in hardware.Sensors:
                # Value 是 Nullable<double>，pythonnet 下可能是 None
                if sensor.Value is None:
                    continue
                total += 1
                kind = str(sensor.SensorType)
                value = float(sensor.Value)
                if kind == "Temperature":
                    temps += 1
                    print(f"  [温度] {here[:44]:<46} {sensor.Name:<28} {value:7.1f} °C")
            for sub in hardware.SubHardware:
                walk(sub, here)

        for hardware in computer.Hardware:
            walk(hardware, "")

        print(f"\n合计 {total} 个读数，其中 {temps} 个温度")
    finally:
        try:
            computer.Close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
