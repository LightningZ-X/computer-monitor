"""数据源适配器。

顺序即界面显示顺序。LibreHardwareMonitor 放在最前，因为它是**独立**数据源
（自带内核驱动，不需要任何第三方软件常驻）；HWiNFO 作为可选补充，
能额外提供 EC 通道、显卡 I2C 等它自己逆向出来的量。
"""

from .base import Provider, ProviderStatus
from .hwinfo import HwinfoProvider
from .lhm import LhmProvider
from .nvml_gpu import NvmlProvider

__all__ = [
    "Provider", "ProviderStatus", "HwinfoProvider", "LhmProvider", "NvmlProvider",
    "build_providers",
]


def build_providers() -> list[Provider]:
    return [LhmProvider(), HwinfoProvider(), NvmlProvider()]
