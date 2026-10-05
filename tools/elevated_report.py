"""在管理员权限下生成体检报告，用于验证 CPU / 主板传感器是否可读。

自己以普通权限运行没有意义（照样读不到），必须配合 tools/run_elevated.py：
    python tools/run_elevated.py python tools/elevated_report.py

产出两个文件（UTF-8）：
    elevated-groups.txt   按硬件分组的读数/温度汇总
    elevated-names.txt    全部传感器名称与角色
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vrmmon import doctor  # noqa: E402
from vrmmon.providers.lhm import LhmProvider  # noqa: E402
from vrmmon.sensors import Registry  # noqa: E402


def capture(arguments: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        doctor.main(arguments)
    return buffer.getvalue()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    (ROOT / "elevated-groups.txt").write_text(
        capture(["--groups"]), encoding="utf-8")
    (ROOT / "elevated-names.txt").write_text(
        capture(["--all", "--names-only"]), encoding="utf-8")

    # 单独统计 LHM：提权后 CPU 温度通道是否真的有值
    provider = LhmProvider()
    status = provider.probe()
    registry = Registry()
    seen = provider.read(registry)
    temperatures = [s for s in seen if s.kind.value == "Temperature"]
    lines = [
        f"提权状态: {provider._elevated}",
        f"数据源状态: {status.state}",
        f"说明: {status.detail}",
        f"读数总数: {len(seen)}    温度数: {len(temperatures)}",
        "",
        "LHM 读到的全部温度:",
    ]
    for sensor in sorted(temperatures, key=lambda s: (s.id.group, s.id.name)):
        lines.append(f"  {sensor.id.group:<50} {sensor.id.name:<30} "
                     f"{sensor.last_value:8.1f} {sensor.unit}")
    provider.close()
    (ROOT / "elevated-lhm.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
