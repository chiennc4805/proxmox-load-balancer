"""Durable scheduler config and atomic round-robin cursor in PostgreSQL."""

import os
import re
import threading
from functools import lru_cache

import psycopg

from backend.app.scheduler.algorithms import ALGORITHMS


NODE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")


class SchedulerError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class SchedulerStore:
    def __init__(self):
        self._initialized = False
        self._init_lock = threading.Lock()

    @staticmethod
    def _connect():
        password = os.getenv("DB_PASSWORD", "")
        if not password:
            raise SchedulerError("Thiếu DB_PASSWORD cho scheduler")
        return psycopg.connect(
            host=os.getenv("DB_HOST", "db"),
            dbname=os.getenv("DB_NAME", "proxmox_lb"),
            user=os.getenv("DB_USER", "proxmox_lb"),
            password=password,
            connect_timeout=5,
        )

    def _initialize(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            default_nodes = [node.strip() for node in os.getenv("SCHEDULER_NODES", "pve1").split(",")]
            self.validate("round_robin", default_nodes)
            try:
                with self._connect() as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS scheduler_state (
                            id SMALLINT PRIMARY KEY CHECK (id = 1),
                            algorithm TEXT NOT NULL,
                            eligible_nodes TEXT[] NOT NULL,
                            last_node TEXT
                        )
                    """)
                    conn.execute(
                        "INSERT INTO scheduler_state (id, algorithm, eligible_nodes) "
                        "VALUES (1, %s, %s) ON CONFLICT (id) DO NOTHING",
                        ("round_robin", default_nodes),
                    )
            except psycopg.Error as exc:
                raise SchedulerError("Không kết nối được PostgreSQL của scheduler") from exc
            self._initialized = True

    @staticmethod
    def validate(algorithm: str, eligible_nodes: list[str]):
        if algorithm not in ALGORITHMS:
            raise SchedulerError("Thuật toán chưa được hỗ trợ", 400)
        if not eligible_nodes or len(set(eligible_nodes)) != len(eligible_nodes):
            raise SchedulerError("eligible_nodes phải có ít nhất một node và không trùng lặp", 400)
        if any(not NODE_NAME.fullmatch(node) for node in eligible_nodes):
            raise SchedulerError("Tên node không hợp lệ", 400)

    @staticmethod
    def _as_config(row):
        return {"algorithm": row[0], "eligible_nodes": row[1], "last_node": row[2]}

    def get_config(self) -> dict:
        self._initialize()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT algorithm, eligible_nodes, last_node FROM scheduler_state WHERE id = 1"
                ).fetchone()
                return self._as_config(row)
        except psycopg.Error as exc:
            raise SchedulerError("Không đọc được cấu hình scheduler") from exc

    def set_config(self, algorithm: str, eligible_nodes: list[str]) -> dict:
        self.validate(algorithm, eligible_nodes)
        self._initialize()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "UPDATE scheduler_state SET algorithm = %s, eligible_nodes = %s, "
                    "last_node = NULL WHERE id = 1 RETURNING algorithm, eligible_nodes, last_node",
                    (algorithm, eligible_nodes),
                ).fetchone()
                return self._as_config(row)
        except psycopg.Error as exc:
            raise SchedulerError("Không lưu được cấu hình scheduler") from exc

    def reserve_node(self, online_nodes: list[str]) -> dict[str, str]:
        self._initialize()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT algorithm, eligible_nodes, last_node FROM scheduler_state "
                    "WHERE id = 1 FOR UPDATE"
                ).fetchone()
                algorithm, eligible_nodes, last_node = row
                candidates = sorted(set(online_nodes) & set(eligible_nodes))
                if not candidates:
                    raise SchedulerError("Không có node được phép nào đang online")
                if algorithm not in ALGORITHMS:
                    raise SchedulerError("Thuật toán trong database chưa được hỗ trợ")
                selected = ALGORITHMS[algorithm].select(candidates, last_node)
                conn.execute("UPDATE scheduler_state SET last_node = %s WHERE id = 1", (selected,))
                return {"node": selected, "algorithm": algorithm}
        except psycopg.Error as exc:
            raise SchedulerError("Không lưu được lượt chọn node của scheduler") from exc


@lru_cache(maxsize=1)
def get_store() -> SchedulerStore:
    return SchedulerStore()
