"""按传感器名称推断物理含义。

主板 VRM 温度没有标准接口，各主板厂在 Super I/O / EC 里的命名不同，HWiNFO 会
翻译成 "VRM"、"VRM MOS"、"MOS" 等；中文版 HWiNFO 则写"供电"、"MOS"。规则必须
同时覆盖中英文。

另一个要点：名称里带"温度"不等于就是个绝对温度。"与 TjMAX 的差值"是相对量，
"过热限制"是布尔标记，都不能当温度画进曲线。

规则集中在此处，便于按实测结果调整；判定理由会显示在界面上供人工复核。
"""

from __future__ import annotations

import re

from .sensors import Kind, Role

# --- 供电(VRM) 关键词 ---
_VRM_EN_STRONG = (
    "vrm",
    "mosfet",
    "power stage",
    "vcore power",
    "vr vddc",
    "vr mvd",
    "dr.mos",
    "drmos",
)
_VRM_ZH_STRONG = ("供电", "供電")
#: "MOS"/"SPS" 这类短词必须整体匹配，否则会误伤无关名称
_VRM_SHORT = re.compile(r"(^|[\s_\-\(/])(mos|vrm|sps)([\s_\-:/\)]|$)", re.IGNORECASE)

#: 供电过热报警标志（布尔量）。读不到 VRM 温度时，这是最直接的替代信号。
_VRM_ALARM = re.compile(r"VR\s*过热|VR\s*过温|供电过热|供电过温|VRM\s*过热|VRM\s*过温",
                        re.IGNORECASE)

# --- 归属提示 ---
#: 注意：不要把"核显"放进 GPU 提示里 —— 那是 CPU 的集成显卡，会让
#: "CPU GT 核心 (核显)" 被误判成独显核心，并污染显卡侧的替代观测项。
_GPU_HINT = re.compile(r"gpu|nvidia|geforce|radeon|显卡|独显", re.IGNORECASE)
_IGPU_HINT = re.compile(r"核显|igpu|integrated", re.IGNORECASE)
_CPU_HINT = re.compile(r"cpu|processor|\bpackage\b|ccd|\bcore\b|soc|封装|核心|处理器",
                       re.IGNORECASE)

# --- 名似温度但并非绝对温度 ---
_NOT_ABSOLUTE = re.compile(r"差值|差异|distance to|delta|offset to", re.IGNORECASE)
#: 阈值/限值：这些是设定的门限，不是当前温度
_LIMIT_FLAG = re.compile(
    r"过热限制|温度限制|thermal\s*limit|throttl|降频"
    r"|warning temperature|critical temperature|max(imum)? temperature"
    r"|high limit|low limit", re.IGNORECASE)
#: 传感器元数据：带 Temperature 字样但描述的是传感器自身参数
_METADATA = re.compile(r"resolution|accuracy|precision", re.IGNORECASE)

_MEMORY = re.compile(r"memor|vram|hbm|gddr|显存", re.IGNORECASE)
_HOTSPOT = re.compile(r"hot\s*spot|热点|结温|結溫", re.IGNORECASE)
_BOARD = re.compile(r"system|chipset|motherboard|mainboard|\bpch\b|主板|芯片组|系统|南桥",
                    re.IGNORECASE)
_PACKAGE = re.compile(r"package|tdie|tctl|封装", re.IGNORECASE)
_SOC = re.compile(r"\bsoc\b|核显|\bgt\b|超核显", re.IGNORECASE)
_CORE_AGG = re.compile(r"核心最大值|核心平均|core max|core average|core temperature",
                       re.IGNORECASE)
_CORE = re.compile(r"core|核心", re.IGNORECASE)


def _has_vrm_word(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for word in _VRM_EN_STRONG:
        if word in lower:
            return True, word
    for word in _VRM_ZH_STRONG:
        if word in text:
            return True, word
    match = _VRM_SHORT.search(text)
    if match:
        return True, match.group(2).lower()
    return False, ""


def classify(group: str, name: str, kind: Kind) -> tuple[Role, str]:
    """返回 (角色, 判定理由)。"""
    # 报警标志不是温度，但无论 HWiNFO 报成哪种类型都要认出来
    if _VRM_ALARM.search(name):
        return Role.VRM_ALARM, "供电过热报警标志"

    if kind != Kind.TEMPERATURE:
        return Role.OTHER, "非温度量"

    text = f"{group} {name}"

    if _NOT_ABSOLUTE.search(name):
        return Role.META, "相对差值，不是绝对温度"
    if _LIMIT_FLAG.search(name):
        return Role.META, "阈值/限值，不是当前温度"
    if _METADATA.search(name):
        return Role.META, "传感器元数据，不是温度读数"

    gpu = bool(_GPU_HINT.search(text))
    cpu = bool(_CPU_HINT.search(text))
    has_vrm, word = _has_vrm_word(text)

    if has_vrm:
        if _GPU_HINT.search(group):
            return Role.GPU_VRM, f"含供电词 '{word}'，位于显卡分组"
        if gpu and not cpu:
            return Role.GPU_VRM, f"含供电词 '{word}' 且属于显卡"
        return Role.CPU_VRM, f"含供电词 '{word}'"

    if gpu:
        # 显存必须优先于热点判断："显存结温"是显存结温，不是 GPU 热点
        if _MEMORY.search(text):
            return Role.GPU_MEMORY, "显存温度"
        if _HOTSPOT.search(text):
            return Role.GPU_HOTSPOT, "显卡热点/结温"
        return Role.GPU_CORE, "显卡核心"

    if _BOARD.search(name) and not cpu:
        return Role.BOARD, "主板/芯片组"
    if _PACKAGE.search(name):
        return Role.CPU_PACKAGE, "CPU 封装"
    if _SOC.search(name):
        return Role.CPU_SOC, "SoC/核显"
    if _CORE_AGG.search(name) or _CORE.search(name):
        return Role.CPU_CORE, "CPU 核心"

    return Role.OTHER, ""


ROLE_LABELS: dict[Role, str] = {
    Role.CPU_VRM: "CPU 供电 (VRM)",
    Role.GPU_VRM: "GPU 供电 (VRM)",
    Role.VRM_UNKNOWN: "供电 (归属不明)",
    Role.VRM_ALARM: "供电过热报警",
    Role.CPU_PACKAGE: "CPU 封装",
    Role.CPU_CORE: "CPU 核心",
    Role.CPU_SOC: "CPU SoC",
    Role.GPU_CORE: "GPU 核心",
    Role.GPU_HOTSPOT: "GPU 热点",
    Role.GPU_MEMORY: "GPU 显存",
    Role.BOARD: "主板",
    Role.OTHER: "其他温度",
    Role.META: "非温度读数",
}
