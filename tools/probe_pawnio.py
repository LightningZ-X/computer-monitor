"""查清 PawnIO 这条路能不能用：设备是否可开、LHM 暴露了什么 PawnIO API。

PawnIO 是当前唯一"已正确安装到 DriverStore、且不在微软易受攻击驱动黑名单"
的 ring0 框架，LHM 0.9.7 支持它。如果能用，就不必再装第二个驱动。
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLAT = ROOT / "packages" / "flat472"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def try_open_device(path: str) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None,
                                 OPEN_EXISTING, 0, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        error = ctypes.get_last_error()
        return f"打不开 (Win32 错误 {error})"
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return "可以打开！"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print("=== PawnIO 设备可访问性（非提权）===")
    for name in (r"\\.\PawnIO", r"\\.\PawnIO0", r"\\.\WinRing0_1_2_0"):
        print(f"  {name:<22} {try_open_device(name)}")

    os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
    sys.path.insert(0, str(FLAT))
    import clr
    clr.AddReference(str(FLAT / "LibreHardwareMonitorLib.dll"))

    import System

    assembly = System.Reflection.Assembly.LoadFrom(
        str(FLAT / "LibreHardwareMonitorLib.dll"))

    print("\n=== 程序集里与 Pawn / Ring0 相关的类型 ===")
    for type_ in assembly.GetTypes():
        name = type_.FullName or ""
        if "pawn" in name.lower() or "ring0" in name.lower():
            methods = []
            try:
                methods = [m.Name for m in type_.GetMethods(
                    System.Reflection.BindingFlags.Public
                    | System.Reflection.BindingFlags.Static
                    | System.Reflection.BindingFlags.DeclaredOnly)]
            except Exception:
                pass
            print(f"  {name}")
            if methods:
                print(f"      公开静态方法: {', '.join(sorted(set(methods))[:14])}")

    print("\n=== 驱动可用性判定的公开 API ===")
    for type_ in assembly.GetTypes():
        name = type_.FullName or ""
        if not name.endswith("Ring0") and "Ring0" not in name:
            continue
        try:
            properties = [p.Name for p in type_.GetProperties(
                System.Reflection.BindingFlags.Public
                | System.Reflection.BindingFlags.Static
                | System.Reflection.BindingFlags.DeclaredOnly)]
        except Exception:
            properties = []
        if properties:
            print(f"  {name}: 属性 {', '.join(sorted(set(properties)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
