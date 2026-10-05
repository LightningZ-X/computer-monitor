"""启动逻辑：已有实例就唤出窗口，否则提权启动。

这是快捷方式与 `vrmmon` 命令共用的入口。之所以要单独一层：

  之前"程序已在运行"时，第二次启动会被单实例互斥体挡掉并**静默退出**，
  双击图标看起来就是"完全没反应"。而且启动链路本身没有任何日志，
  用户说"启动不了"时无从判断卡在哪一步。

所以这里做两件事：把已有窗口唤到前台；把每一步都写进生命周期日志。
"""

from __future__ import annotations

from . import elevation, logging_setup, shutdown


def _log(text: str) -> None:
    logging_setup.log_message(text, "启动请求")


def launch(elevated: bool = True, minimized: bool = False,
           source: str = "命令行") -> int:
    """启动 vrmmon。返回进程退出码。"""
    from . import single_instance

    if shutdown.is_app_running():
        _log(f"已有实例在运行（来源：{source}），请求唤出窗口")
        if shutdown.request_show():
            print("vrmmon 已经在运行，已请它把窗口显示出来。")
            return 0
        _log("已有实例但无法请求唤出窗口（事件通道不可用）")
        print("vrmmon 已经在运行（见托盘图标）；无法自动唤出窗口。")
        return 0

    if not elevated:
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        python = Path(__import__("sys").executable).with_name("pythonw.exe")
        if not python.exists():
            python = Path(__import__("sys").executable)
        args = [str(python), str(root / "main.py")]
        if minimized:
            args.append("--minimized")
        subprocess.Popen(args, cwd=str(root), close_fds=True)
        _log(f"以普通权限启动（来源：{source}）")
        print("已以普通权限启动（只能读显卡与硬盘温度）。")
        return 0

    started, message = elevation.relaunch_elevated(minimized=minimized)
    _log(f"提权启动（来源：{source}）：{message}")
    print(message)
    if not started:
        print("提权被取消或失败。可改用：启动监控.bat（普通权限，无 CPU 温度）",
              file=__import__("sys").stderr)
    return 0 if started else 1


def request_show_from_other_instance() -> bool:
    """供 main.py 在单实例互斥体冲突时调用。"""
    ok = shutdown.request_show()
    _log("检测到重复启动，已请求唤出已有窗口" if ok else
         "检测到重复启动，但唤出请求失败")
    return ok
