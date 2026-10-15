from dataclasses import dataclass
import logging

from app.framework.contracts.action import Action, ActionResult, WorkflowError
from app.framework.contracts.proxmox import NativeArguments
from app.services.clone import build_clone_arguments, normalize_clone
from app.services.resources import ResourceCollector, choose_target, eligible_nodes


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedClone:
    operation_id: str
    source_node: str
    source_vmid: int
    selected_target: str
    clone_params: NativeArguments


class PrepareClone(Action):
    async def execute(self, args, context) -> ActionResult:
        clone = normalize_clone(args['request'])
        collector = ResourceCollector(context.proxmox, context.settings.shared_storage)
        # Gather inventory under the same cluster admission lock as holds/RR.
        # Admissions are serialized, while submitted Proxmox tasks run concurrently.
        async with context.operations.transaction() as tx:
            holds = await context.operations.holds(tx)
            if any(hold['newid'] == clone.newid for hold in holds):
                raise WorkflowError('Requested newid is already reserved', 409)
            nodes, vms, storages, requirements = await collector.collect(clone)
            # A successful task may become visible in cluster inventory after
            # its reservation is consumed. Keep an allocation fallback until
            # inventory sees that VM; never count both for the same VMID.
            visible_ids = {vm.get('vmid') for vm in vms}
            for allocation in await context.operations.allocations(tx):
                if allocation['newid'] not in visible_ids:
                    vms.append({
                        'vmid': allocation['newid'], 'node': allocation['target_node'],
                        'maxcpu': allocation['vcpu'], 'maxmem': allocation['memory_bytes'],
                    })
            eligible = eligible_nodes(nodes, vms, storages, requirements, holds)
            last = await context.operations.cursor(tx) if clone.mode == 'AUTO' else None
            node = choose_target(clone, eligible, last)
            operation_id = await context.operations.reserve(tx, clone, node, requirements, context.correlation_id)
        context.operation_id = operation_id
        logger.info('clone_reserved operation=%s newid=%s target=%s correlation=%s', operation_id, clone.newid, node, context.correlation_id)
        return ActionResult(PreparedClone(
            operation_id, clone.source_node, clone.source_vmid, node,
            build_clone_arguments(args['request'], clone, node),
        ))
