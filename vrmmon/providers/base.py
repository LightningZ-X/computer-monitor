"""数据源适配器接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..sensors import Registry, Sensor


@dataclass
class ProviderStatus:
    #: 能否产出数据（不是"是否完整"——部分可用也算 ok，细节看 state/detail）
    ok: bool
    state: str            # 简短状态，用于状态栏
    detail: str = ""      # 排查建议
    sensors: int = 0

    def __str__(self) -> str:
        return f"{self.state}{' - ' + self.detail if self.detail else ''}"


class Provider(ABC):
    """一个数据源。read() 必须永不抛异常，失败时返回空列表并更新 status()。"""

    key: str = ""
    label: str = ""

    #: 最近一次 read() 的结果，供 status() 汇报
    last_error: str = ""

    @abstractmethod
    def probe(self) -> ProviderStatus:
        """在不读取数据的前提下检查数据源是否可用。"""

    @abstractmethod
    def read(self, registry: Registry) -> list[Sensor]:
        """读取一轮数据，把传感器注册进 registry 并返回本轮出现的传感器。"""
