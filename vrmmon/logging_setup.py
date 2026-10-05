"""把未捕获异常与生命周期事件落盘。

以 pythonw 启动时没有控制台，异常会静默消失、进程无声退出——对后台常驻的
监控程序来说这是最糟糕的失败模式。所以：

  data/vrmmon-error.log       只放异常，空 == 没有出错
  data/vrmmon-lifecycle.log   启动 / 退出原因，用来回答"它怎么没了"
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_PATH = DATA_DIR / "vrmmon-error.log"
LIFECYCLE_PATH = DATA_DIR / "vrmmon-lifecycle.log"


def _append(path: Path, head: str, body: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {head} =====\n"
                         f"{body}\n")
            handle.flush()
    except Exception:
        # 记日志本身失败不应再抛异常
        pass


def log_exception(exc_type, exc, tb, context: str = "") -> None:
    try:
        body = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        body = repr(exc)
    _append(LOG_PATH, context or "未捕获异常", body)


def log_message(text: str, context: str = "信息") -> None:
    _append(LIFECYCLE_PATH, context, text)


def install(context: str = "未捕获异常") -> None:
    """安装全局 excepthook。"""
    def hook(exc_type, exc, tb):
        log_exception(exc_type, exc, tb, context)
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = hook
