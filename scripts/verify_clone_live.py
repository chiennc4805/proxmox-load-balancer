"""Read-only by default. --submit creates three stopped dev VMs and keeps them."""

import argparse
import json
import time
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row
import requests

from app.settings import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', default='http://127.0.0.1:8000')
    parser.add_argument('--template', type=int, default=9001)
    parser.add_argument('--fixed-target', default='pve2')
    parser.add_argument('--submit', action='store_true')
    options = parser.parse_args()
    settings = get_settings()
    with requests.Session() as client:
        client.trust_env = False
        def get(path, **kwargs):
            response = client.get(options.gateway + '/api2/json' + path, timeout=30, **kwargs)
            response.raise_for_status()
            return response.json()['data']
        vms = get('/cluster/resources', params={'type': 'vm'})
        source = next((vm for vm in vms if vm.get('vmid') == options.template), None)
        if not source or not source.get('template'):
            raise RuntimeError('The selected fixture is not an available template')
        source_node = source['node']
        print(json.dumps({'template': options.template, 'source_node': source_node, 'submit': options.submit}), flush=True)
        if not options.submit:
            print(json.dumps({'nextid': get('/cluster/nextid'), 'nodes': [node['node'] for node in get('/nodes')]}))
            return
        for scenario, target in (('omitted', None), ('fixed', options.fixed_target), ('auto', 'auto')):
            newid = int(get('/cluster/nextid'))
            params = {'newid': newid, 'full': 1, 'name': f'gateway-clone-{scenario}-{newid}'}
            if target is not None:
                params['target'] = target
            response = client.post(
                options.gateway + f'/api2/json/nodes/{source_node}/qemu/{options.template}/clone',
                data=params, timeout=120,
            )
            if response.status_code != 200:
                print(json.dumps({'scenario': scenario, 'newid': newid, 'status': response.status_code, 'response': response.json()}), flush=True)
                response.raise_for_status()
            upid = response.json()['data']
            print(json.dumps({'scenario': scenario, 'newid': newid, 'accepted': True, 'upid': upid}), flush=True)
            deadline = time.monotonic() + 300
            last = None
            while time.monotonic() < deadline:
                task_node = upid.split(':')[1]
                task = get(f'/nodes/{task_node}/tasks/{quote(upid, safe="")}/status')
                with psycopg.connect(settings.database_dsn, row_factory=dict_row) as connection:
                    row = connection.execute(
                        """SELECT o.id,o.status,o.selected_target,r.status AS reservation
                           FROM operations o JOIN resource_reservations r ON r.operation_id=o.id
                           WHERE o.cluster_key=%s AND o.newid=%s ORDER BY o.created_at DESC LIMIT 1""",
                        (settings.cluster_key, newid),
                    ).fetchone()
                progress = (task.get('status'), row['status'] if row else None)
                if progress != last:
                    print(json.dumps({'scenario': scenario, 'newid': newid, 'task': progress[0], 'operation': progress[1]}), flush=True)
                    last = progress
                if row and row['status'] == 'SUCCEEDED':
                    vm = get(f'/nodes/{row["selected_target"]}/qemu/{newid}/status/current')
                    assert task.get('exitstatus') == 'OK'
                    assert row['reservation'] == 'CONSUMED'
                    assert vm['status'] == 'stopped'
                    print(json.dumps({'scenario': scenario, 'newid': newid, 'target': row['selected_target'], 'result': 'SUCCEEDED', 'reservation': row['reservation'], 'vm_status': vm['status']}), flush=True)
                    break
                if row and row['status'] in {'FAILED', 'UNKNOWN'}:
                    raise RuntimeError(f'Clone {newid} ended in {row["status"]}; kept for inspection')
                time.sleep(3)
            else:
                raise RuntimeError(f'Clone {newid} did not reach a terminal tracked state in 300 seconds')


if __name__ == '__main__':
    main()
