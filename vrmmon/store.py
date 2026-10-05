"""SQLite 长时间记录 + CSV 导出。

Collector 在主线程里构造、在采集线程里写库，所以连接必须开
check_same_thread=False 并用锁串行化访问。导出用独立的只读连接，
这样长时间导出不会阻塞采集（WAL 模式允许读写并发）。
"""

from __future__ import annotations

import csv
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .sensors import Kind, Role, Sensor

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts       REAL    NOT NULL,
    provider TEXT    NOT NULL,
    grp      TEXT    NOT NULL,
    name     TEXT    NOT NULL,
    kind     TEXT    NOT NULL,
    role     TEXT    NOT NULL,
    value    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples (ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    """按轮次批量写入，避免每条一次事务。

    提交时机取"行数"与"时间"先到者：纯按行数提交时，50ms 采样下
    每 ~2 轮就 commit 一次（每秒 8 次），事务开销会明显拖慢采集循环；
    纯按时间又会在低频时攒太多。两者结合后，最坏情况也只丢约 1 秒数据。
    """

    def __init__(self, path: Path, flush_rows: int = 2000,
                 flush_interval_s: float = 1.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 跨线程使用：连接在 UI 线程创建，写入发生在采集线程
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.RLock()  # add() 内部会调 flush()，必须可重入
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', '1')")
        self._conn.commit()
        self._pending: list[tuple] = []
        self._flush_rows = flush_rows
        self._flush_interval_s = flush_interval_s
        self._last_flush = time.monotonic()

    def add(self, timestamp: float, sensors: list[Sensor], values: dict) -> int:
        """追加一批采样，返回实际入队的行数。"""
        added = 0
        with self._lock:
            for sensor in sensors:
                value = values.get(sensor.id)
                if value is None or value != value:  # None 或 NaN
                    continue
                self._pending.append((
                    timestamp, sensor.id.provider, sensor.id.group, sensor.id.name,
                    sensor.kind.value, sensor.role.value, float(value),
                ))
                added += 1
            if (len(self._pending) >= self._flush_rows
                    or time.monotonic() - self._last_flush >= self._flush_interval_s):
                self._flush_locked()
        return added

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        try:
            self._conn.executemany(
                "INSERT INTO samples (ts, provider, grp, name, kind, role, value)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", self._pending)
            self._conn.commit()
            self._last_flush = time.monotonic()
        except sqlite3.Error:
            self._conn.rollback()
        finally:
            self._pending.clear()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def purge(self, cutoff: float) -> int:
        """删除 cutoff 之前的记录，返回删除行数。

        这是"长时间记录"能成立的前提：1 秒采样约 3~10 GB/天，
        50ms 采样更会几天撑满磁盘。删完顺手 truncate 一次 WAL，
        否则释放的空间会一直挂在 -wal 文件里。
        """
        with self._lock:
            self._flush_locked()
            try:
                cursor = self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
                removed = cursor.rowcount or 0
                self._conn.commit()
                if removed:
                    # 让 WAL 不要无限增长；TRUNCATE 会把已提交内容并回主库
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                return removed
            except sqlite3.Error:
                return 0

    def rows(self) -> int:
        with self._lock:
            try:
                return int(self._conn.execute(
                    "SELECT COUNT(*) FROM samples").fetchone()[0]) + len(self._pending)
            except sqlite3.Error:
                return 0

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                try:
                    total += candidate.stat().st_size
                except OSError:
                    pass
        return total

    def span(self) -> tuple[float | None, float | None]:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT MIN(ts), MAX(ts) FROM samples").fetchone()
            except sqlite3.Error:
                return None, None
        return (row[0], row[1]) if row else (None, None)

    def export_csv(self, dest: Path, since: float | None = None,
                   until: float | None = None) -> int:
        """导出为 CSV（UTF-8 BOM，方便 Excel 直接打开中文）。返回行数。"""
        self.flush()  # 先把内存里未落盘的行写进去
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        query = "SELECT ts, provider, grp, name, kind, role, value FROM samples"
        clauses: list[str] = []
        params: list[float] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts, provider, grp, name"

        # 独立只读连接，避免长时间导出阻塞采集线程
        posix = str(self.path).replace("\\", "/")
        uri = f"file:{quote(posix, safe='/:')}?mode=ro"
        try:
            reader = sqlite3.connect(uri, uri=True)
        except sqlite3.Error:
            reader = self._conn  # 退化：退回主连接
        owns_reader = reader is not self._conn

        count = 0
        try:
            with dest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["时间", "时间戳", "数据源", "分组", "传感器",
                                 "类型", "角色", "数值"])
                for row in reader.execute(query, params):
                    ts, provider, grp, name, kind, role, value = row
                    writer.writerow([
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                        f"{ts:.3f}", provider, grp, name, kind, role, value,
                    ])
                    count += 1
        finally:
            if owns_reader:
                reader.close()
        return count

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def role_of(value: str) -> Role:
    try:
        return Role(value)
    except ValueError:
        return Role.OTHER


def kind_of(value: str) -> Kind:
    try:
        return Kind(value)
    except ValueError:
        return Kind.OTHER
