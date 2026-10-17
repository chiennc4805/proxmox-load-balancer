"""Concurrency checks with real PostgreSQL and a configurable fake Proxmox."""

import asyncio
from collections import Counter
import json
import os
import sys
import unittest

import test_clone_postgres as fixtures
from app.framework.contracts.action import WorkflowError
from app.framework.contracts.proxmox import UpstreamResponse
from app.tracker import TaskTracker


class ConfigurableCluster(fixtures.FakeCluster):
    def __init__(self):
        super().__init__()
        self.maxmem = 8 * 1024**3
        self.offline = set()
        self.unavailable_storage = set()
        self.clone_delay = 0
        self.active_posts = 0
        self.peak_posts = 0

    async def request(self, method, path, **kwargs):
        if method == 'POST' and path.endswith('/clone'):
            self.active_posts += 1
            self.peak_posts = max(self.peak_posts, self.active_posts)
            try:
                await asyncio.sleep(self.clone_delay)
                return await super().request(method, path, **kwargs)
            finally:
                self.active_posts -= 1
        response = await super().request(method, path, **kwargs)
        if path == '/nodes':
            data = response.json()['data']
            for node in data:
                node['maxmem'] = self.maxmem
                if node['node'] in self.offline:
                    node['status'] = 'offline'
            return UpstreamResponse(200, response.headers, json.dumps({'data': data}).encode())
        if path.endswith('/storage') and path.split('/')[2] in self.unavailable_storage:
            return UpstreamResponse(200, response.headers, b'{"data":[]}')
        return response


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL')
class CloneConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ClonePostgresTests.asyncSetUp(self)
        self.proxmox = ConfigurableCluster()

    asyncTearDown = fixtures.ClonePostgresTests.asyncTearDown
    execute = fixtures.ClonePostgresTests.execute
    states = fixtures.ClonePostgresTests.states

    async def batch(self, count, target='auto'):
        return await asyncio.wait_for(asyncio.gather(
            *(self.execute(400 + index, target) for index in range(count)),
            return_exceptions=True,
        ), timeout=30)

    def assert_admitted(self, results, expected):
        failures = [result for result in results if not isinstance(result, UpstreamResponse)]
        self.assertEqual(len(results) - len(failures), expected, repr(failures))
        self.assertTrue(all(isinstance(error, WorkflowError) and error.status_code == 409 for error in failures))

    async def test_round_robin_three_complete_rounds(self):
        self.proxmox.cpu = 4
        self.proxmox.available = 100 * 1024**3
        self.assert_admitted(await self.batch(9), 9)
        async with self.operations.transaction() as tx:
            rows = await tx.query(
                'SELECT selected_target FROM operations WHERE cluster_key=%s ORDER BY created_at,id',
                (self.key,),
            )
            self.assertEqual([row['selected_target'] for row in rows], ['pve1', 'pve2', 'pve3'] * 3)
            self.assertEqual(await self.operations.cursor(tx), 'pve3')

    async def test_parallel_requests_compete_for_ram_not_cpu(self):
        self.proxmox.cpu = 8
        self.proxmox.maxmem = 1024**3
        self.assert_admitted(await self.batch(12), 3)
        self.assertEqual(Counter(self.proxmox.created.values()), {'pve1': 1, 'pve2': 1, 'pve3': 1})

    async def test_parallel_requests_compete_for_shared_storage(self):
        self.proxmox.cpu = 8
        self.proxmox.available = 7 * 1024**3
        self.assert_admitted(await self.batch(12), 2)
        async with self.operations.transaction() as tx:
            holds = await self.operations.holds(tx)
        self.assertEqual(sum(hold['storage_bytes'] for hold in holds), 6 * 1024**3)

    async def test_fixed_target_cannot_steal_other_nodes_capacity(self):
        self.assert_admitted(await self.batch(12, target='pve2'), 1)
        self.assertEqual(list(self.proxmox.created.values()), ['pve2'])
        async with self.operations.transaction() as tx:
            self.assertIsNone(await self.operations.cursor(tx))

    async def test_auto_skips_offline_and_storage_unavailable_nodes(self):
        self.proxmox.offline = {'pve2'}
        self.proxmox.unavailable_storage = {'pve3'}
        self.assert_admitted(await self.batch(6), 1)
        self.assertEqual(list(self.proxmox.created.values()), ['pve1'])

    async def test_holds_protect_capacity_before_vm_is_visible(self):
        self.proxmox.inventory_lag = True
        self.assert_admitted(await self.batch(12), 3)
        self.assertEqual(len(await self.states()), 3)

    async def test_twenty_requests_with_same_vmid_submit_once(self):
        results = await asyncio.gather(*(self.execute(450) for _ in range(20)), return_exceptions=True)
        self.assert_admitted(results, 1)
        self.assertEqual(len(self.proxmox.posts), 1)
        self.assertEqual(len(await self.states()), 1)

    async def test_native_submissions_overlap_after_admission(self):
        self.proxmox.clone_delay = 0.5
        self.assert_admitted(await self.batch(3), 3)
        self.assertGreaterEqual(self.proxmox.peak_posts, 2)

    async def test_rejected_admission_does_not_advance_cursor(self):
        self.assert_admitted(await self.batch(3), 3)
        async with self.operations.transaction() as tx:
            before = await self.operations.cursor(tx)
        with self.assertRaises(WorkflowError):
            await self.execute(499)
        async with self.operations.transaction() as tx:
            self.assertEqual(await self.operations.cursor(tx), before)
        self.assertEqual(len(self.proxmox.posts), 3)

    async def test_failed_task_capacity_is_reusable(self):
        self.assert_admitted(await self.batch(3), 3)
        self.proxmox.task_status = 'stopped'
        self.proxmox.task_exitstatus = 'ERROR: simulated failure before VM creation'
        self.proxmox.created.clear()
        await TaskTracker(self.proxmox, self.operations).run_once()
        self.assertTrue(all(row['reservation'] == 'RELEASED' for row in await self.states()))
        results = await asyncio.gather(*(self.execute(500 + index) for index in range(3)))
        self.assert_admitted(results, 3)

    async def test_database_lock_coordinates_separate_processes(self):
        # Every child has its own event loop, asyncio.Lock and fake inventory.
        # Only PostgreSQL reservations/advisory locks are shared.
        code = '''
import asyncio, json, os, sys
from app.framework.contracts.action import WorkflowError
from app.framework.workflows.loader import load_workflow
from app.infrastructure.database.operations import OperationStore
from app.settings import Settings
from test_clone_postgres import FakeCluster
from test_workflow import clone_request
async def run():
    settings = Settings('fake', 'fake', 'fake', 'fake', True, 2,
        database_dsn=os.environ['TEST_DATABASE_DSN'], cluster_key=os.environ['TEST_CLUSTER_KEY'])
    operations = OperationStore(settings.database_dsn, settings.cluster_key)
    try:
        response = await load_workflow('vm.clone', FakeCluster(), settings, operations).execute(clone_request(int(sys.argv[1]), 'auto'))
        print(json.dumps({'status': response.status_code}))
    except WorkflowError as error:
        print(json.dumps({'status': error.status_code}))
asyncio.run(run())
'''
        environment = dict(os.environ, TEST_DATABASE_DSN=self.settings.database_dsn, TEST_CLUSTER_KEY=self.key)
        async def child(vmid):
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-c', code, str(vmid), env=environment,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            self.assertEqual(process.returncode, 0, stderr.decode())
            return json.loads(stdout)['status']
        statuses = await asyncio.gather(*(child(600 + index) for index in range(8)))
        self.assertEqual(Counter(statuses), {200: 3, 409: 5})
        async with self.operations.transaction() as tx:
            holds = await self.operations.holds(tx)
        self.assertEqual(Counter(hold['target_node'] for hold in holds), {'pve1': 1, 'pve2': 1, 'pve3': 1})
