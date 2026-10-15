import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

import psycopg
from psycopg.rows import dict_row

from app.framework.contracts.action import WorkflowError


_local_locks = WeakKeyDictionary()


def _admission_lock(cluster_key):
    loop = asyncio.get_running_loop()
    locks = _local_locks.setdefault(loop, {})
    return locks.setdefault(cluster_key, asyncio.Lock())


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        def run():
            cursor = self.connection.execute(sql, params)
            return cursor.fetchall() if cursor.description else []
        return await asyncio.to_thread(run)


class OperationStore:
    def __init__(self, dsn: str | None, cluster_key: str):
        self.dsn = dsn
        self.cluster_key = cluster_key
        self.lock_key = int.from_bytes(sha256(cluster_key.encode()).digest()[:8], 'big', signed=True)

    @asynccontextmanager
    async def transaction(self, *, lock: bool = True):
        # Wait asynchronously within a process instead of occupying all
        # executor threads with PostgreSQL lock waiters. PostgreSQL remains
        # the authoritative cross-process lock.
        if lock:
            async with _admission_lock(self.cluster_key):
                async with self._transaction(lock=True) as tx:
                    yield tx
        else:
            async with self._transaction(lock=False) as tx:
                yield tx

    @asynccontextmanager
    async def _transaction(self, *, lock: bool):
        if not self.dsn:
            raise WorkflowError('DATABASE_DSN is required for clone workflows', 503)
        connection = None
        try:
            # Synchronous psycopg in threads also works on Windows Proactor
            # loops. Each transaction owns a connection; no shared cursor.
            connection = await asyncio.to_thread(
                psycopg.connect, self.dsn, row_factory=dict_row, connect_timeout=5,
            )
            tx = Transaction(connection)
            if lock:
                await tx.query('SELECT pg_advisory_xact_lock(%s)', (self.lock_key,))
            yield tx
            await asyncio.to_thread(connection.commit)
        except psycopg.Error as error:
            if connection:
                await asyncio.to_thread(connection.rollback)
            raise WorkflowError('Database operation could not be completed', 503) from error
        except BaseException:
            if connection:
                await asyncio.to_thread(connection.rollback)
            raise
        finally:
            if connection:
                await asyncio.to_thread(connection.close)

    async def migrate(self):
        schema = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
        async with self.transaction() as tx:
            for statement in schema.split(';'):
                if statement.strip():
                    await tx.query(statement)

    async def holds(self, tx: Transaction):
        return await tx.query(
            "SELECT * FROM resource_reservations WHERE cluster_key=%s AND status='HELD'",
            (self.cluster_key,),
        )

    async def allocations(self, tx: Transaction):
        return await tx.query(
            "SELECT * FROM resource_reservations WHERE cluster_key=%s AND status='CONSUMED'",
            (self.cluster_key,),
        )

    async def reserve(self, tx, clone, node, requirements, correlation_id):
        operation_id = str(uuid4())
        await tx.query(
            """INSERT INTO operations
               (id,cluster_key,correlation_id,source_node,source_vmid,target_mode,
                requested_target,selected_target,newid,status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'RESERVED')""",
            (operation_id, self.cluster_key, correlation_id, clone.source_node,
             clone.source_vmid, clone.mode, clone.requested_target, node, clone.newid),
        )
        await tx.query(
            """INSERT INTO resource_reservations
               (operation_id,cluster_key,newid,target_node,storage_id,vcpu,memory_bytes,storage_bytes,status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'HELD')""",
            (operation_id,self.cluster_key,clone.newid,node,requirements.storage_id,
             requirements.vcpu,requirements.memory_bytes,requirements.storage_bytes),
        )
        if clone.mode == 'AUTO':
            await tx.query(
                """INSERT INTO round_robin_cursors VALUES (%s,%s)
                   ON CONFLICT (cluster_key) DO UPDATE SET last_selected_node=EXCLUDED.last_selected_node""",
                (self.cluster_key, node),
            )
        return operation_id

    async def cursor(self, tx):
        rows = await tx.query('SELECT last_selected_node FROM round_robin_cursors WHERE cluster_key=%s', (self.cluster_key,))
        return rows[0]['last_selected_node'] if rows else None

    async def mark_submitting(self, operation_id):
        async with self.transaction() as tx:
            rows = await tx.query(
                """UPDATE operations SET status='SUBMITTING',updated_at=now()
                   WHERE id=%s AND status='RESERVED' RETURNING id""", (operation_id,),
            )
            if not rows:
                raise WorkflowError('Operation is not ready for submission', 409)

    async def submitted(self, operation_id, upid, task_node, status):
        async with self.transaction() as tx:
            rows = await tx.query(
                """UPDATE operations SET status='SUBMITTED',upid=%s,task_node=%s,
                   upstream_status=%s,updated_at=now() WHERE id=%s AND status='SUBMITTING' RETURNING id""",
                (upid,task_node,status,operation_id),
            )
            if not rows:
                raise WorkflowError('Operation submission could not be persisted', 503)

    async def fail(self, operation_id, *, unknown: bool, upstream_status: int | None = None):
        async with self.transaction() as tx:
            rows = await tx.query(
                """UPDATE operations SET status=%s,upstream_status=COALESCE(%s,upstream_status),
                   failure_origin=%s,updated_at=now()
                   WHERE id=%s AND status IN ('RESERVED','SUBMITTING') RETURNING id""",
                ('UNKNOWN' if unknown else 'FAILED', upstream_status,
                 'submission' if unknown else 'admission_or_upstream', operation_id),
            )
            if rows and not unknown:
                await tx.query("UPDATE resource_reservations SET status='RELEASED',updated_at=now() WHERE operation_id=%s", (operation_id,))

    async def pending(self):
        async with self.transaction(lock=False) as tx:
            return await tx.query(
                "SELECT * FROM operations WHERE cluster_key=%s AND status='SUBMITTED' ORDER BY created_at",
                (self.cluster_key,),
            )

    async def finish(self, operation_id, success: bool):
        async with self.transaction() as tx:
            rows = await tx.query(
                """UPDATE operations SET status=%s,updated_at=now(),failure_origin=%s
                   WHERE id=%s AND status='SUBMITTED' RETURNING id""",
                ('SUCCEEDED' if success else 'FAILED', None if success else 'proxmox_task', operation_id),
            )
            if rows:
                await tx.query(
                    "UPDATE resource_reservations SET status=%s,updated_at=now() WHERE operation_id=%s",
                    ('CONSUMED' if success else 'RELEASED', operation_id),
                )
