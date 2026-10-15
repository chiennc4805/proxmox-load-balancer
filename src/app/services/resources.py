import asyncio
from dataclasses import dataclass
import re
from urllib.parse import urlencode

from app.framework.contracts.action import WorkflowError


@dataclass(frozen=True)
class Requirements:
    vcpu: int
    memory_bytes: int
    storage_id: str
    storage_bytes: int


async def read_data(proxmox, path: str, query: dict | None = None):
    response = await proxmox.request('GET', path, query_string=urlencode(query or {}).encode())
    if response.status_code != 200:
        raise WorkflowError('Required Proxmox resource data is unavailable', 502)
    try:
        return response.json()['data']
    except (ValueError, KeyError, TypeError) as error:
        raise WorkflowError('Invalid Proxmox resource response', 502) from error


class ResourceCollector:
    def __init__(self, proxmox, storage_id: str):
        self.proxmox = proxmox
        self.storage_id = storage_id

    async def collect(self, clone):
        nodes, vms = await asyncio.gather(
            read_data(self.proxmox, '/nodes'),
            read_data(self.proxmox, '/cluster/resources', {'type': 'vm'}),
        )
        source = next((vm for vm in vms if vm.get('vmid') == clone.source_vmid and vm.get('type') == 'qemu'), None)
        if not source or source.get('node') != clone.source_node:
            raise WorkflowError('Source VM is not present on the requested node', 404)
        if any(vm.get('vmid') == clone.newid for vm in vms):
            raise WorkflowError('Requested newid is already in use', 409)
        config = await read_data(self.proxmox, f'/nodes/{clone.source_node}/qemu/{clone.source_vmid}/config')
        destination_storage = clone.params.get('storage', self.storage_id)
        if destination_storage != self.storage_id:
            raise WorkflowError('Slice 0 resource accounting supports only the configured shared storage')
        volumes = await read_data(
            self.proxmox, f'/nodes/{clone.source_node}/storage/{self.storage_id}/content',
            {'vmid': clone.source_vmid},
        )
        volume_sizes = {volume['volid']: int(volume['size']) for volume in volumes if 'size' in volume}
        storage_bytes = 0
        for name, value in config.items():
            if not re.fullmatch(r'(?:scsi|virtio|ide|sata|efidisk|tpmstate)[0-9]+', name):
                continue
            volume = value.split(',')[0]
            if 'media=cdrom' in value and 'cloudinit' not in volume:
                continue
            if not volume.startswith(self.storage_id + ':'):
                raise WorkflowError('Slice 0 requires source disks on the configured shared storage')
            size_match = re.search(r'(?:^|,)size=([0-9.]+)([KMGT]?)', value)
            if size_match:
                size = int(float(size_match.group(1)) * 1024 ** ' KMGT'.index(size_match.group(2) or ' '))
            elif volume in volume_sizes:
                size = volume_sizes[volume]
            else:
                raise WorkflowError('Source disk size could not be determined')
            storage_bytes += size
        requirements = Requirements(
            int(config.get('cores', 1)) * int(config.get('sockets', 1)),
            int(config.get('memory', 512)) * 1024 ** 2,
            self.storage_id,
            storage_bytes,
        )
        if requirements.vcpu < 1 or requirements.memory_bytes < 1:
            raise WorkflowError('Source resource configuration is invalid')
        storages = {}
        async def storage_for(node):
            if node.get('status') != 'online':
                return
            try:
                entries = await read_data(self.proxmox, f'/nodes/{node["node"]}/storage')
                storages[node['node']] = next((entry for entry in entries if entry.get('storage') == self.storage_id), None)
            except WorkflowError:
                storages[node['node']] = None
        await asyncio.gather(*(storage_for(node) for node in nodes))
        return nodes, vms, storages, requirements


def eligible_nodes(nodes, vms, storages, requirements, holds, cpu_overcommit=1.0):
    held_vmids = {hold['newid'] for hold in holds}
    committed_cpu, committed_memory = {}, {}
    for vm in vms:
        if vm.get('template') or vm.get('vmid') in held_vmids:
            continue
        node = vm['node']
        committed_cpu[node] = committed_cpu.get(node, 0) + int(vm.get('maxcpu', 0))
        committed_memory[node] = committed_memory.get(node, 0) + int(vm.get('maxmem', 0))
    held_storage = sum(hold['storage_bytes'] for hold in holds if hold['storage_id'] == requirements.storage_id)
    eligible = []
    for node in nodes:
        name = node['node']
        storage = storages.get(name)
        if node.get('status') != 'online' or not storage:
            continue
        if not all(storage.get(key) for key in ('active', 'enabled', 'shared')) or 'images' not in storage.get('content', '').split(','):
            continue
        node_holds = [hold for hold in holds if hold['target_node'] == name]
        cpu_available = int(node.get('maxcpu', 0)) * cpu_overcommit - committed_cpu.get(name, 0) - sum(hold['vcpu'] for hold in node_holds)
        memory_available = int(node.get('maxmem', 0)) - committed_memory.get(name, 0) - sum(hold['memory_bytes'] for hold in node_holds)
        if requirements.vcpu <= cpu_available and requirements.memory_bytes <= memory_available and requirements.storage_bytes <= int(storage.get('avail', 0)) - held_storage:
            eligible.append(name)
    return sorted(eligible)


def choose_target(clone, eligible, last_node=None):
    if clone.mode == 'FIXED':
        target = clone.requested_target or clone.source_node
        if target not in eligible:
            raise WorkflowError('Requested target does not pass resource admission', 409)
        return target
    if not eligible:
        raise WorkflowError('No node passes resource admission', 409)
    return next((node for node in eligible if last_node is None or node > last_node), eligible[0])
