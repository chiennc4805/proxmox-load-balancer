"""PostgreSQL persistence for synchronous job state and placement cursor history."""

import os
import threading
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from backend.app.models.job import JobStatus
from backend.app.scheduler.scheduler import Scheduler, SchedulerError


class JobStoreError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class JobStore:
    def __init__(self):
        self._initialized = False
        self._init_lock = threading.Lock()

    @staticmethod
    def _connect():
        password = os.getenv("DB_PASSWORD", "")
        if not password:
            raise JobStoreError("Missing DB_PASSWORD for job store")
        return psycopg.connect(
            host=os.getenv("DB_HOST", "db"),
            dbname=os.getenv("DB_NAME", "proxmox_lb"),
            user=os.getenv("DB_USER", "proxmox_lb"),
            password=password,
            connect_timeout=5,
        )

    @staticmethod
    def _create_schema(conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_state (
                id UUID PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                request_payload JSONB NOT NULL,
                result_payload JSONB,
                error_payload JSONB,
                algorithm TEXT,
                selected_node TEXT,
                selected_at TIMESTAMPTZ,
                attempted_nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
                resource_type TEXT,
                resource_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_job_state_type_status_created
            ON job_state (job_type, status, created_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_job_state_placement_cursor
            ON job_state (job_type, algorithm, selected_at DESC)
            WHERE selected_node IS NOT NULL
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_job_state_resource
            ON job_state (resource_type, resource_id)
            WHERE resource_id IS NOT NULL
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduler_config (
                id SMALLINT PRIMARY KEY CHECK (id = 1),
                algorithm TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO scheduler_config (id, algorithm)
            VALUES (1, 'round_robin') ON CONFLICT (id) DO NOTHING
        """)

    def _initialize(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            try:
                with self._connect() as conn:
                    self._create_schema(conn)
                    legacy_table = conn.execute("SELECT to_regclass('scheduler_state')").fetchone()[0]
                    if legacy_table is not None:
                        conn.execute("""
                            UPDATE scheduler_config
                            SET algorithm = scheduler_state.algorithm
                            FROM scheduler_state
                            WHERE scheduler_config.id = 1 AND scheduler_state.id = 1
                        """)
            except psycopg.Error as exc:
                raise JobStoreError("Could not initialize PostgreSQL job store") from exc
            self._initialized = True

    @staticmethod
    def _last_selected_node(conn, job_type: str, algorithm: str) -> str | None:
        row = conn.execute(
            """
            SELECT selected_node FROM job_state
            WHERE job_type = %s
              AND algorithm = %s
              AND selected_node IS NOT NULL
            ORDER BY selected_at DESC
            LIMIT 1
            """,
            (job_type, algorithm),
        ).fetchone()
        return row[0] if row else None

    def create_job(self, job_type: str, request_payload: dict[str, Any]) -> str:
        self._initialize()
        job_id = str(uuid.uuid4())
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO job_state (id, job_type, status, request_payload, started_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (job_id, job_type, JobStatus.RUNNING.value, Jsonb(request_payload)),
                )
        except psycopg.Error as exc:
            raise JobStoreError("Could not create job state") from exc
        return job_id

    def reserve_placement(
        self,
        job_id: str,
        job_type: str,
        algorithm: str,
        choose_node: Callable[[str | None], dict[str, Any]],
    ) -> dict[str, Any]:
        self._initialize()
        try:
            with self._connect() as conn:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"placement:{job_type}:{algorithm}",),
                )
                last_node = self._last_selected_node(conn, job_type, algorithm)
                choice = choose_node(last_node)
                conn.execute(
                    """
                    UPDATE job_state
                    SET algorithm = %s,
                        selected_node = %s,
                        selected_at = NOW(),
                        attempted_nodes = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        algorithm,
                        choice["node"],
                        Jsonb(choice.get("attempted_nodes", [])),
                        job_id,
                    ),
                )
                return choice
        except SchedulerError:
            raise
        except psycopg.Error as exc:
            raise JobStoreError("Could not reserve placement node") from exc

    def mark_succeeded(
        self,
        job_id: str,
        result_payload: dict[str, Any],
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        self._initialize()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE job_state
                    SET status = %s,
                        result_payload = %s,
                        resource_type = %s,
                        resource_id = %s,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        JobStatus.SUCCEEDED.value,
                        Jsonb(result_payload),
                        resource_type,
                        resource_id,
                        job_id,
                    ),
                )
        except psycopg.Error as exc:
            raise JobStoreError("Could not mark job as succeeded") from exc

    def mark_failed(self, job_id: str, error_payload: dict[str, Any]) -> None:
        self._initialize()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE job_state
                    SET status = %s,
                        error_payload = %s,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (JobStatus.FAILED.value, Jsonb(error_payload), job_id),
                )
        except psycopg.Error as exc:
            raise JobStoreError("Could not mark job as failed") from exc

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._initialize()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, job_type, status, request_payload, result_payload, error_payload,
                           algorithm, selected_node, attempted_nodes, resource_type, resource_id,
                           created_at, started_at, finished_at, updated_at
                    FROM job_state WHERE id = %s
                    """,
                    (job_id,),
                ).fetchone()
        except psycopg.Error as exc:
            raise JobStoreError("Could not read job state") from exc
        if row is None:
            raise JobStoreError("Job not found", 404)
        return {
            "id": str(row[0]),
            "job_type": row[1],
            "status": row[2],
            "request_payload": row[3],
            "result_payload": row[4],
            "error_payload": row[5],
            "algorithm": row[6],
            "selected_node": row[7],
            "attempted_nodes": row[8],
            "resource_type": row[9],
            "resource_id": row[10],
            "created_at": row[11].isoformat() if row[11] else None,
            "started_at": row[12].isoformat() if row[12] else None,
            "finished_at": row[13].isoformat() if row[13] else None,
            "updated_at": row[14].isoformat() if row[14] else None,
        }

    def get_config(self) -> dict[str, Any]:
        self._initialize()
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT algorithm FROM scheduler_config WHERE id = 1").fetchone()
                algorithm = row[0]
                last_node = self._last_selected_node(conn, "vm.clone", algorithm)
                return {"algorithm": algorithm, "last_node": last_node}
        except psycopg.Error as exc:
            raise JobStoreError("Could not read scheduler config") from exc

    def set_config(self, algorithm: str) -> dict[str, Any]:
        Scheduler.validate(algorithm)
        self._initialize()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    UPDATE scheduler_config SET algorithm = %s
                    WHERE id = 1 RETURNING algorithm
                    """,
                    (algorithm,),
                ).fetchone()
                last_node = self._last_selected_node(conn, "vm.clone", row[0])
                return {"algorithm": row[0], "last_node": last_node}
        except psycopg.Error as exc:
            raise JobStoreError("Could not save scheduler config") from exc


@lru_cache(maxsize=1)
def get_job_store() -> JobStore:
    return JobStore()