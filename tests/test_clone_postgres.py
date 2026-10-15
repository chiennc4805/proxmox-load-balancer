import asyncio
import json
import os
import unittest
from urllib.parse import parse_qs, unquote
from uuid import uuid4

from app.framework.contracts.action import WorkflowError
from app.framework.contracts.proxmox import ProxmoxTimeoutError, UpstreamResponse
from app.framework.workflows.loader import load_workflow
from app.infrastructure.database.operations import OperationStore
from app.settings import Settings, get_settings
from app.tracker import TaskTracker
from test_workflow import clone_request


class FakeCluster:
    def __init__(self):
        self.created = {}
        self.posts = []
        self.cpu = 1
        self.available = 20 * 1024**3
        self.task_status = 'running'
        self.reject = False
        self.timeout = False
        self.inventory_lag = False
        self.task_exitstatus = 'OK'
        self.poll_error = False

    async def request(self, method, path, **kwargs):
        await asyncio.sleep(0.002)
        path = unquote(path)
        data = None
        status = 200
        if path == '/nodes':
            data = [{'node': node, 'status': 'online', 'maxcpu': self.cpu, 'maxmem': 8 * 1024**3} for node in ('pve1', 'pve2', 'pve3')]
        elif path == '/cluster/resources':
            data = [{'vmid': 9001, 'type': 'qemu', 'template': 1, 'node': 'pve1', 'maxcpu': 1, 'maxmem': 1024**3}]
            if not self.inventory_lag:
                data += [{'vmid': vmid, 'node': node, 'type': 'qemu', 'maxcpu': 1, 'maxmem': 1024**3} for vmid, node in self.created.items()]
        elif path.endswith('/storage/nas-nfs/content'):
            data = [{'volid': 'nas-nfs:9001/base.raw', 'size': 3 * 1024**3}]
        elif path.endswith('/storage'):
            data = [{'storage': 'nas-nfs', 'active': 1, 'enabled': 1, 'shared': 1, 'content': 'images', 'avail': self.available}]
        elif '/tasks/' in path:
            if self.poll_error:
                raise ProxmoxTimeoutError('Simulated polling timeout')
            data = {'status': self.task_status, 'exitstatus': self.task_exitstatus if self.task_status == 'stopped' else None}
        elif path.endswith('/clone') and method == 'POST':
            params = parse_qs(kwargs['body'].decode())
            self.posts.append(params)
            if self.timeout:
                raise ProxmoxTimeoutError('Simulated unknown outcome')
            if self.reject:
                return UpstreamResponse(400, ((b'content-type', b'application/json'),), b'{"data":null,"errors":{"newid":"rejected"}}')
            vmid = int(params['newid'][0])
            self.created[vmid] = params.get('target', [path.split('/')[2]])[0]
            data = f'UPID:pve1:00000001:00000001:00000001:qmclone:{vmid}:root@pam:'
        elif path.endswith('/config'):
            vmid = int(path.split('/')[-2])
            if vmid != 9001 and self.created.get(vmid) != path.split('/')[2]:
                status = 404
            data = {'cores': 1, 'memory': 1024, 'scsi0': 'nas-nfs:9001/base.raw,size=3G'}
        else:
            raise AssertionError(f'Unexpected fake request: {method} {path}')
        return UpstreamResponse(status, ((b'content-type', b'application/json'),), json.dumps({'data': data}).encode())


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Set RUN_POSTGRES_TESTS=1 with local PostgreSQL')
class ClonePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key = 'tests:' + str(uuid4())
        self.settings = Settings('fake', 'fake', 'fake', 'fake', True, 2,
            database_dsn=get_settings().database_dsn, cluster_key=self.key)
        self.operations = OperationStore(self.settings.database_dsn, self.key)
        await self.operations.migrate()
        self.proxmox = FakeCluster()

    async def asyncTearDown(self):
        assert self.key.startswith('tests:')
        async with self.operations.transaction() as tx:
            await tx.query('DELETE FROM resource_reservations WHERE cluster_key=%s', (self.key,))
            await tx.query('DELETE FROM operations WHERE cluster_key=%s', (self.key,))
            await tx.query('DELETE FROM round_robin_cursors WHERE cluster_key=%s', (self.key,))

    async def execute(self, vmid, target='auto'):
        return await load_workflow('vm.clone', self.proxmox, self.settings, self.operations).execute(clone_request(vmid, target))

    async def states(self):
        async with self.operations.transaction() as tx:
            return await tx.query(
                "SELECT o.newid,o.status,r.status AS reservation FROM operations o JOIN resource_reservations r ON r.operation_id=o.id WHERE o.cluster_key=%s ORDER BY o.newid", (self.key,),
            )

    async def test_concurrent_clones_do_not_overallocate_cpu(self):
        results = await asyncio.gather(*(self.execute(200 + index) for index in range(4)), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, UpstreamResponse) for result in results), 3)
        self.assertEqual(sum(isinstance(result, WorkflowError) for result in results), 1)
        self.assertEqual({node for node in self.proxmox.created.values()}, {'pve1', 'pve2', 'pve3'})
        states = await self.states()
        self.assertEqual(len(states), 3)
        self.assertTrue(all(row['status'] == 'SUBMITTED' and row['reservation'] == 'HELD' for row in states))

    async def test_fixed_and_omitted_target_do_not_update_cursor(self):
        await self.execute(200, None)
        await self.execute(201, 'pve2')
        self.assertNotIn('target', self.proxmox.posts[0])
        self.assertEqual(self.proxmox.posts[1]['target'], ['pve2'])
        async with self.operations.transaction() as tx:
            self.assertIsNone(await self.operations.cursor(tx))

    async def test_duplicate_vmid_is_submitted_only_once(self):
        results = await asyncio.gather(self.execute(200), self.execute(200), return_exceptions=True)
        self.assertEqual(len(self.proxmox.posts), 1)
        self.assertEqual(sum(isinstance(result, WorkflowError) for result in results), 1)

    async def test_storage_is_shared_not_multiplied_by_node_count(self):
        self.proxmox.cpu = 4
        self.proxmox.available = 7 * 1024**3
        await self.execute(200)
        await self.execute(201)
        with self.assertRaises(WorkflowError):
            await self.execute(202)
        self.assertEqual(len(self.proxmox.posts), 2)

    async def test_known_rejection_releases_and_unknown_outcome_keeps_hold(self):
        self.proxmox.reject = True
        response = await self.execute(200)
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await self.states())[0]['reservation'], 'RELEASED')
        self.proxmox.reject = False
        self.proxmox.timeout = True
        with self.assertRaises(ProxmoxTimeoutError):
            await self.execute(201)
        state = (await self.states())[1]
        self.assertEqual((state['status'], state['reservation']), ('UNKNOWN', 'HELD'))
        self.assertEqual(len(self.proxmox.posts), 2)

    async def test_tracker_waits_for_terminal_success_before_consuming(self):
        await self.execute(200)
        tracker = TaskTracker(self.proxmox, self.operations)
        await tracker.run_once()
        self.assertEqual((await self.states())[0]['reservation'], 'HELD')
        self.proxmox.task_status = 'stopped'
        await tracker.run_once()
        self.assertEqual((await self.states())[0]['status'], 'SUCCEEDED')
        self.assertEqual((await self.states())[0]['reservation'], 'CONSUMED')
        await tracker.run_once()
        self.assertEqual((await self.states())[0]['reservation'], 'CONSUMED')

    async def test_consumed_allocation_remains_counted_during_inventory_lag(self):
        await self.execute(200, 'pve1')
        self.proxmox.inventory_lag = True
        self.proxmox.task_status = 'stopped'
        await TaskTracker(self.proxmox, self.operations).run_once()
        with self.assertRaises(WorkflowError):
            await self.execute(201, 'pve1')

    async def test_tracker_failure_releases_but_poll_timeout_does_not(self):
        await self.execute(200)
        tracker = TaskTracker(self.proxmox, self.operations)
        self.proxmox.poll_error = True
        await tracker.run_once()
        self.assertEqual((await self.states())[0]['reservation'], 'HELD')
        self.proxmox.poll_error = False
        self.proxmox.task_status = 'stopped'
        self.proxmox.task_exitstatus = 'ERROR: native clone failed'
        await tracker.run_once()
        state = (await self.states())[0]
        self.assertEqual((state['status'], state['reservation']), ('FAILED', 'RELEASED'))

    async def test_many_waiting_requests_do_not_exhaust_threadpool(self):
        results = await asyncio.wait_for(asyncio.gather(
            *(self.execute(300 + index) for index in range(40)),
            return_exceptions=True,
        ), timeout=20)
        self.assertEqual(sum(isinstance(result, UpstreamResponse) for result in results), 3)
        self.assertEqual(len(self.proxmox.posts), 3)


if __name__ == '__main__':
    unittest.main()
