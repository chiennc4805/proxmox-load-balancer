import asyncio
from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock, Mock

from app.framework.actions.loader import load_action
from app.framework.contracts.action import ActionContext, WorkflowError
from app.framework.contracts.request import EndpointRequest
from app.framework.workflows.expressions import resolve
from app.framework.workflows.loader import load_plan
from app.framework.workflows.validator import validate_workflow
from app.infrastructure.proxmox.native import ProxmoxNativeAction, parse_native_call
from app.services.clone import build_clone_arguments, normalize_clone
from app.services.resources import Requirements, choose_target, eligible_nodes


def clone_request(newid=200, target=None, node='pve1'):
    body = f'newid={newid}&full=1'.encode()
    if target is not None:
        body += b'&target=' + target.encode()
    return EndpointRequest(
        'POST', f'/nodes/{node}/qemu/9001/clone',
        f'/nodes/{node}/qemu/9001/clone'.encode(), b'', (),
        ((b'content-type', b'application/x-www-form-urlencoded'),), body,
        {'node': node, 'vmid': '9001'},
    )


class WorkflowTests(unittest.TestCase):
    def test_plan_loads_native_and_adhoc_into_one_action_contract(self):
        from app.framework.contracts.action import Action
        plan = load_plan('vm.clone')
        self.assertEqual([step.id for step in plan.steps], ['prepare', 'clone', 'record'])
        self.assertTrue(all(isinstance(step.action, Action) for step in plan.steps))
        self.assertIsInstance(plan.steps[1].action, ProxmoxNativeAction)

    def test_native_parser_keeps_references_symbolic_until_execution(self):
        call = parse_native_call('proxmox.native.nodes(${request.path_params.node}).qemu(9001).clone.post')
        self.assertEqual(call.method, 'POST')
        self.assertEqual(call.segments, ('nodes', '${request.path_params.node}', 'qemu', '9001', 'clone'))

    def test_native_value_cannot_inject_a_resource_or_method(self):
        request = clone_request(node='a.b).qemu(42).delete')
        proxmox = Mock()
        from app.framework.contracts.proxmox import UpstreamResponse
        proxmox.request = AsyncMock(return_value=UpstreamResponse(200, (), b'{"data":[]}'))
        context = ActionContext(request, proxmox, None, None, 'test')
        action = load_action('proxmox.native.nodes(${request.path_params.node}).qemu.get')
        asyncio.run(action.execute({}, context))
        path = proxmox.request.call_args.args[1]
        self.assertEqual(path, '/nodes/a.b%29.qemu%2842%29.delete/qemu')

    def test_loader_rejects_untrusted_imports_and_non_action_classes(self):
        for uses in ('os.system', 'adhoc.clone.os', 'proxmox.native.nodes(__import__("os")).get'):
            with self.subTest(uses=uses), self.assertRaises(WorkflowError):
                load_action(uses)

    def test_validator_rejects_future_steps_and_duplicate_ids(self):
        raw = {'name': 'test', 'mode': 'sync', 'steps': [
            {'id': 'one', 'uses': 'proxmox.native.nodes.get', 'args': '${steps.two.output}'},
        ], 'response': {'from': '${steps.one.output}'}}
        with self.assertRaises(WorkflowError):
            validate_workflow(raw)
        raw['steps'][0]['args'] = {}
        raw['steps'].append(dict(raw['steps'][0]))
        with self.assertRaises(WorkflowError):
            validate_workflow(raw)

    def test_resolver_preserves_bytes_objects_and_rejects_private_access(self):
        request = clone_request()
        context = ActionContext(request, None, None, None, 'test')
        self.assertIs(resolve('${request}', context), request)
        self.assertEqual(resolve('${request.body}', context), request.body)
        with self.assertRaises(WorkflowError):
            resolve('${request.__class__}', context)

    def test_fixed_and_omitted_target_keep_original_wire_body(self):
        for target in (None, 'pve2'):
            request = clone_request(target=target)
            intent = normalize_clone(request)
            args = build_clone_arguments(request, intent, target or 'pve1')
            self.assertEqual(intent.mode, 'FIXED')
            self.assertEqual(args.body, request.body)
            self.assertEqual('target' in args.params, target is not None)

    def test_auto_changes_only_the_form_target_bytes(self):
        request = replace(clone_request(target='auto'), body=b'name=a%2bb+z&newid=200&target=auto&full=1&custom=x%20y')
        args = build_clone_arguments(request, normalize_clone(request), 'pve2')
        self.assertEqual(args.body, b'name=a%2bb+z&newid=200&target=pve2&full=1&custom=x%20y')

    def test_json_auto_preserves_unrelated_values(self):
        request = replace(clone_request(), headers=((b'content-type', b'application/json'),), body=b'{"newid":200,"target":"auto","description":"test"}')
        args = build_clone_arguments(request, normalize_clone(request), 'pve2')
        self.assertEqual(json.loads(args.body), {'newid': 200, 'target': 'pve2', 'description': 'test'})

    def test_duplicates_and_missing_newid_are_rejected(self):
        for body in (b'target=auto', b'newid=200&target=auto&target=pve1', b'newid=200&newid=201'):
            with self.subTest(body=body), self.assertRaises(WorkflowError):
                normalize_clone(replace(clone_request(), body=body))

    def test_fixed_does_not_fallback_and_round_robin_handles_changed_ring(self):
        fixed = normalize_clone(clone_request(target='pve2'))
        with self.assertRaises(WorkflowError):
            choose_target(fixed, ['pve1', 'pve3'])
        auto = normalize_clone(clone_request(target='auto'))
        self.assertEqual(choose_target(auto, ['pve1', 'pve3'], 'pve2'), 'pve3')
        self.assertEqual(choose_target(auto, ['pve1', 'pve3'], 'pve3'), 'pve1')

    def test_hold_is_not_counted_twice_when_pending_vm_appears(self):
        nodes = [{'node': 'pve1', 'status': 'online', 'maxcpu': 2, 'maxmem': 8 * 1024**3}]
        storage = {'pve1': {'active': 1, 'enabled': 1, 'shared': 1, 'content': 'images', 'avail': 20 * 1024**3}}
        requirement = Requirements(1, 1024**3, 'nas-nfs', 3 * 1024**3)
        holds = [{'newid': 200, 'target_node': 'pve1', 'vcpu': 1, 'memory_bytes': 1024**3, 'storage_bytes': 3 * 1024**3, 'storage_id': 'nas-nfs'}]
        vms = [{'vmid': 200, 'node': 'pve1', 'maxcpu': 1, 'maxmem': 1024**3}]
        self.assertEqual(eligible_nodes(nodes, vms, storage, requirement, holds), ['pve1'])
        self.assertEqual(eligible_nodes(nodes, vms, storage, requirement, holds * 2), [])


if __name__ == '__main__':
    unittest.main()
