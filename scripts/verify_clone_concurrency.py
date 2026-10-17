"""Read-only by default; --submit creates up to three stopped dev VMs, without cleanup/retry."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import threading
import time
from urllib.parse import quote, urlencode

import psycopg
from psycopg.rows import dict_row
import requests

from app.framework.contracts.request import EndpointRequest
from app.infrastructure.database.operations import OperationStore
from app.infrastructure.proxmox.client import ProxmoxClient
from app.services.clone import normalize_clone
from app.services.resources import ResourceCollector, choose_target, eligible_nodes
from app.settings import get_settings


def emit(event, **values):
    print(json.dumps({'event': event, **values}, default=str), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', default='http://127.0.0.1:8000')
    parser.add_argument('--template', type=int, default=9001)
    parser.add_argument('--count', type=int, default=3, choices=(1, 2, 3))
    parser.add_argument('--submit', action='store_true')
    options = parser.parse_args()
    settings = get_settings()

    def call(method, path, **kwargs):
        with requests.Session() as client:
            client.trust_env = False
            return client.request(method, options.gateway + '/api2/json' + path, timeout=120, **kwargs)

    def get(path, **kwargs):
        response = call('GET', path, **kwargs)
        response.raise_for_status()
        return response.json()['data']

    inventory = get('/cluster/resources', params={'type': 'vm'})
    source = next((vm for vm in inventory if vm.get('vmid') == options.template and vm.get('template')), None)
    if source is None:
        raise RuntimeError('Selected fixture must be an existing template')
    path = f'/nodes/{source["node"]}/qemu/{options.template}/clone'

    # These are client-supplied IDs, not a new gateway auto-VMID feature.
    with psycopg.connect(settings.database_dsn) as connection:
        historical = connection.execute('SELECT newid FROM operations WHERE cluster_key=%s', (settings.cluster_key,)).fetchall()
    used = {vm['vmid'] for vm in inventory} | {row[0] for row in historical}
    candidate = int(get('/cluster/nextid'))
    vmids = []
    while len(vmids) < options.count + 1:
        if candidate not in used:
            vmids.append(candidate)
        candidate += 1

    def parameters(vmid):
        return {'newid': vmid, 'full': 1, 'target': 'auto', 'name': f'gateway-concurrent-{vmid}'}

    def intent(vmid):
        request = EndpointRequest(
            'POST', path, path.encode(), b'', (),
            ((b'content-type', b'application/x-www-form-urlencoded'),),
            urlencode(parameters(vmid)).encode(),
            {'node': source['node'], 'vmid': str(options.template)},
        )
        return normalize_clone(request)

    async def preflight():
        operations = OperationStore(settings.database_dsn, settings.cluster_key)
        async with operations.transaction() as tx:
            holds = await operations.holds(tx)
            allocations = await operations.allocations(tx)
            cursor = await operations.cursor(tx)
            nodes, vms, storages, requirements = await ResourceCollector(
                ProxmoxClient(settings), settings.shared_storage,
            ).collect(intent(vmids[0]))
            visible = {vm['vmid'] for vm in vms}
            for allocation in allocations:
                if allocation['newid'] not in visible:
                    vms.append({'vmid': allocation['newid'], 'node': allocation['target_node'],
                                'maxcpu': allocation['vcpu'], 'maxmem': allocation['memory_bytes']})
            initial_eligible = eligible_nodes(nodes, vms, storages, requirements, holds)
            planned, previous = [], cursor
            for vmid in vmids[:-1]:
                eligible = eligible_nodes(nodes, vms, storages, requirements, holds)
                selected = choose_target(intent(vmid), eligible, previous)
                planned.append(selected)
                holds.append({'newid': vmid, 'target_node': selected, **asdict(requirements)})
                previous = selected
            return cursor, initial_eligible, planned, requirements

    cursor, eligible, expected, requirements = asyncio.run(preflight())
    emit('preflight', template=options.template, source=source['node'], cursor=cursor,
         eligible=eligible, proposed_vmids=vmids[:-1], expected_placement_order=expected,
         requirements=asdict(requirements), submit=options.submit)
    if not options.submit:
        return

    # One extra request duplicates the first VMID, deliberately racing its owner.
    requested = vmids[:-1] + [vmids[0]]
    barrier = threading.Barrier(len(requested))
    def submit(vmid):
        barrier.wait(timeout=10)
        started = time.monotonic()
        response = call('POST', path, data=parameters(vmid))
        result = {'vmid': vmid, 'http_status': response.status_code,
                  'elapsed_seconds': round(time.monotonic() - started, 3), 'body': response.json()}
        emit('submitted', **result)
        return result

    with ThreadPoolExecutor(max_workers=len(requested)) as executor:
        results = list(executor.map(submit, requested))

    def rows():
        with psycopg.connect(settings.database_dsn, row_factory=dict_row) as connection:
            return connection.execute(
                '''SELECT o.newid,o.selected_target,o.status,o.upid,o.task_node,r.status AS reservation
                   FROM operations o JOIN resource_reservations r ON r.operation_id=o.id
                   WHERE o.cluster_key=%s AND o.newid=ANY(%s) ORDER BY o.created_at,o.id''',
                (settings.cluster_key, vmids[:-1]),
            ).fetchall()

    checks = {}
    checks['one_success_per_vmid'] = all(
        sum(result['http_status'] == 200 and result['vmid'] == vmid for result in results) == 1
        for vmid in vmids[:-1]
    )
    checks['duplicate_rejected_once'] = sum(result['http_status'] == 409 for result in results) == 1
    accepted = [result for result in results if result['http_status'] == 200]
    deadline, last, peak_running = time.monotonic() + 300, None, 0
    while time.monotonic() < deadline:
        operations = rows()
        tasks = {}
        for operation in operations:
            if operation['upid']:
                task = get(f'/nodes/{quote(operation["task_node"], safe="")}/tasks/{quote(operation["upid"], safe="")}/status')
                tasks[str(operation['newid'])] = task
        peak_running = max(peak_running, sum(task.get('status') == 'running' for task in tasks.values()))
        progress = [(row['newid'], row['status'], row['reservation']) for row in operations]
        if progress != last:
            emit('progress', operations=operations, tasks=tasks)
            last = progress
        if all(row['status'] in {'SUCCEEDED', 'FAILED', 'UNKNOWN'} for row in operations):
            break
        time.sleep(2)
    else:
        emit('tracking_timeout', seconds=300)

    operations = rows()
    checks['one_operation_per_vmid'] = len(operations) == options.count
    checks['round_robin_order'] = [row['selected_target'] for row in operations] == expected
    checks['all_tasks_succeeded'] = len(operations) == options.count and all(row['status'] == 'SUCCEEDED' for row in operations)
    checks['no_held_reservations'] = all(row['reservation'] == 'CONSUMED' for row in operations)
    checks['raw_upid_matches_database'] = all(
        any(row['newid'] == result['vmid'] and row['upid'] == result['body'].get('data') for row in operations)
        for result in accepted
    )
    vm_states = {}
    for operation in operations:
        if operation['status'] == 'SUCCEEDED':
            vm = get(f'/nodes/{operation["selected_target"]}/qemu/{operation["newid"]}/status/current')
            vm_states[str(operation['newid'])] = vm['status']
    checks['successful_vms_stopped'] = all(state == 'stopped' for state in vm_states.values())

    before = len(operations)
    invalid = parameters(vmids[-1])
    invalid['target'] = 'gateway-test-node-does-not-exist'
    response = call('POST', path, data=invalid)
    with psycopg.connect(settings.database_dsn) as connection:
        rejected_operations = connection.execute(
            'SELECT count(*) FROM operations WHERE cluster_key=%s AND newid=%s',
            (settings.cluster_key, vmids[-1]),
        ).fetchone()[0]
    checks['invalid_fixed_target_rejected_without_reservation'] = response.status_code == 409 and rejected_operations == 0 and len(rows()) == before
    emit('summary', checks=checks, peak_observed_running_tasks=peak_running,
         operations=operations, vm_states=vm_states, kept_vmids=list(vm_states),
         invalid_target_http_status=response.status_code)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
