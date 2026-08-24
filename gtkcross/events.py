"""构建日志与事件日志设施（纯 Python 层，无 fd 操作/无线程）。

OutputTee —— 构建期间把本进程 stdout/stderr 的输出复制一份到日志文件；
             子进程输出由 toolchain.run() 以管道逐行中继，同样落入日志
             （见 sink_write，capture 模式下也落盘但不刷控制台）。
EventLog  —— 追加式事件日志：按时间戳记录构建过程中的异常事件
             （known failure 是否如期出现/缺席、unexpected 失败、
             退出码异常、命令/recipe 失败、run 起止），跨 run 持久。

此前版本的 fd 级 tee（dup/pipe + 后台泵线程）在 MinGW Python 下泵线程
死锁、管道写满导致全部子进程阻塞，已废弃。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# -- build log sink -------------------------------------------------------
# 模块级 sink：OutputTee 激活期间非 None；toolchain.run() 与 _TeeStream 共用。

_sink_lock = threading.Lock()
_sink_file = None


def sink_write(data: "str | bytes") -> None:
    """把一行/一段输出写入构建日志（未激活时为 no-op，绝不抛出）。"""
    f = _sink_file
    if f is None:
        return
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    with _sink_lock:
        try:
            f.write(data)
            f.flush()
        except (OSError, ValueError):
            pass


class _TeeStream:
    """包装文本流：write 透传原流并复制到 sink（仅 stdout/stderr 替身）。"""

    def __init__(self, original):
        self._original = original

    def write(self, s: str) -> int:
        sink_write(s)
        return self._original.write(s)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name):
        # isatty 等其余属性委托原流
        return getattr(self._original, name)


class OutputTee:
    """context manager：构建期间把本进程输出复制到日志文件。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._saved = None
        self._file = None

    def __enter__(self) -> "OutputTee":
        global _sink_file
        self._file = open(
            self.path, "a", encoding="utf-8", errors="replace"
        )
        _sink_file = self._file
        self._file.write(f"\n===== gtkcross build log {_ts()} =====\n")
        self._saved = (
            __import__("sys").stdout,
            __import__("sys").stderr,
        )
        s = __import__("sys")
        s.stdout = _TeeStream(s.stdout)  # type: ignore[assignment]
        s.stderr = _TeeStream(s.stderr)  # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        global _sink_file
        import sys

        sys.stdout, sys.stderr = self._saved  # type: ignore[misc]
        try:
            self._file.flush()
            self._file.close()
        except (OSError, ValueError):
            pass
        finally:
            _sink_file = None


# -- event log --------------------------------------------------------------

class EventLog:
    """追加式事件日志（跨 run 持久）。

    每行一条：{timestamp} [{target}] {kind}: {message}
    kind 词汇：run-start / run-ok / run-failed / recipe-failed /
    tests-pass / tests-known-observed / tests-known-absent /
    tests-unexpected / tests-exit-anomaly
    """

    def __init__(self, path: Path, target: str):
        self.path = Path(path)
        self.target = target

    def log(self, kind: str, msg: str) -> None:
        line = f"{_ts()} [{self.target}] {kind}: {msg}\n"
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            # 事件日志写不进去不应打断构建
            print(f"  [event-log] 写入失败: {e}")
