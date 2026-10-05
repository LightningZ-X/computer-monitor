"""配置文件读写（config.json，放在项目根目录）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

#: 采样间隔的取值边界。只在这里定义，别处一律调用 clamp_interval()，避免各改各的。
#:
#: 下限 5ms：实测单轮成本约 4.74ms（93 个传感器、含落盘），也就是天花板约 211 轮/秒，
#: 所以 5ms（200 轮/秒）已经贴着能力上限了，再小只会变成忙等。
#: 注意跑在这个量级会吃掉一整个 CPU 核心。
#:
#: 还有个**数据新鲜度**的上限不属于本程序：HWiNFO 的共享内存按它自己的
#: SensorInterval 刷新（本机 100ms），所以间隔低于 100ms 时，HWiNFO 那一路
#: 会读到重复快照——只有 LHM 那一路是真正的高频。
MIN_INTERVAL_S = 0.005
MAX_INTERVAL_S = 60.0


def clamp_interval(seconds: float) -> float:
    """把采样间隔钳到合法范围。"""
    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, float(seconds)))


@dataclass
class Thresholds:
    warn: float = 85.0
    critical: float = 95.0


@dataclass
class Config:
    #: 采样间隔（秒）。下限见 MIN_INTERVAL_S。
    interval_s: float = 1.0
    #: 入库间隔（秒）。0 表示每轮都写。
    #:
    #: 采样与入库必须解耦：50ms 采样 × 768 个读数 ≈ 14,750 行/秒 ≈ 145 GB/天，
    #: 磁盘扛不住。所以界面/告警按 interval_s 走，落盘按这里节流。
    log_interval_s: float = 1.0
    #: 记录保留天数。0 表示不清理。超出部分会被定期删除，避免无限增长。
    retention_days: float = 2.0
    #: 保存目标（由 load() 填入）。空表示**不保存**。
    #:
    #: 用来避免不该落盘的情况覆盖用户配置：命令行 --interval 只是本次生效，
    #: 测试用的临时 Config 也不该写真实文件。不参与序列化。
    save_path: str = ""
    #: 相对 ROOT 的路径
    db_path: str = "data/vrmmon.sqlite3"
    export_dir: str = "exports"
    thresholds: Thresholds = field(default_factory=Thresholds)
    #: 供电过热报警位置位时立刻告警
    alert_on_vrm_flag: bool = True
    sound: bool = True
    minimize_to_tray: bool = True
    #: 界面动效总开关。系统关闭「显示动画」时，即使这里是 True 也会整段归零
    #: （不是加速播放）——见 ui/motion.py 的 reduced-motion 处理。
    animations: bool = True

    def resolve(self, rel: str) -> Path:
        path = Path(rel)
        return path if path.is_absolute() else ROOT / path


def load(path: Path | None = None) -> Config:
    """读取配置；缺字段用默认值补齐，多余字段忽略，损坏时回退默认值。"""
    path = path or CONFIG_PATH
    config = Config()
    config.save_path = str(path)
    if not path.exists():
        save(config, path)
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(raw, dict):
        return config

    valid = {f.name for f in fields(Config)}
    for key, value in raw.items():
        if key not in valid:
            continue
        if key == "thresholds" and isinstance(value, dict):
            th = config.thresholds
            for tkey in ("warn", "critical"):
                if isinstance(value.get(tkey), (int, float)):
                    setattr(th, tkey, float(value[tkey]))
        elif isinstance(getattr(config, key), list):
            if isinstance(value, list):
                setattr(config, key, [str(v) for v in value])
        elif isinstance(getattr(config, key), bool):
            if isinstance(value, bool):
                setattr(config, key, value)
        elif isinstance(getattr(config, key), (int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                # 按字段声明的类型决定 float / int，别写死某个键名——
                # 否则新增的浮点配置会被 int() 截断（0.5 变 0）。
                current = getattr(config, key)
                setattr(config, key,
                        float(value) if isinstance(current, float) else int(value))
        elif isinstance(value, str):
            setattr(config, key, value)
    config.interval_s = clamp_interval(config.interval_s)
    config.log_interval_s = max(0.0, min(MAX_INTERVAL_S, config.log_interval_s))
    config.retention_days = max(0.0, min(365.0, config.retention_days))
    return config


def save(config: Config, path: Path | None = None) -> None:
    """写回配置。save_path 为空且未显式给 path 时**不写**（见 Config.save_path）。"""
    target = path or config.save_path
    if not target:
        return
    path = Path(target)
    data = asdict(config)
    data.pop("save_path", None)  # 运行时状态，不进配置文件
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
