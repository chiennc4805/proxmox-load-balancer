import unittest
from unittest.mock import MagicMock, patch

from backend.app.controllers.vm_controller import VmController
from backend.app.models.vm import CloneRequest
from backend.app.proxmox_adapter import AdapterError
from backend.app.scheduler.algorithms import RoundRobin
from backend.app.scheduler.scheduler import Scheduler, SchedulerError


class FakeJobStore:
    def __init__(self, algorithm="round_robin"):
        self.algorithm = algorithm
        self.created = []
        self.succeeded = []
        self.failed = []
        self.reservations = []
        self.last_node = None

    def create_job(self, job_type, request_payload):
        self.created.append((job_type, request_payload))
        return f"job-{len(self.created)}"

    def get_config(self):
        return {"algorithm": self.algorithm, "last_node": self.last_node}

    def reserve_placement(self, job_id, job_type, algorithm, choose_node):
        choice = choose_node(self.last_node)
        self.last_node = choice["node"]
        self.reservations.append((job_id, job_type, algorithm, choice))
        return choice

    def mark_succeeded(self, job_id, result_payload, **kwargs):
        self.succeeded.append((job_id, result_payload, kwargs))

    def mark_failed(self, job_id, error_payload):
        self.failed.append((job_id, error_payload))


class RoundRobinTests(unittest.TestCase):
    def test_rotates_in_stable_order_and_survives_missing_last_node(self):
        nodes = ["pve3", "pve1", "pve2"]
        self.assertEqual(RoundRobin.select(nodes, None), "pve1")
        self.assertEqual(RoundRobin.select(nodes, "pve1"), "pve2")
        self.assertEqual(RoundRobin.select(nodes, "pve2"), "pve3")
        self.assertEqual(RoundRobin.select(nodes, "pve3"), "pve1")
        self.assertEqual(RoundRobin.select(["pve1", "pve3"], "pve2"), "pve1")
        self.assertEqual(RoundRobin.select(nodes, "pve2", ["pve1", "pve2"]), "pve3")

    def test_invalid_admin_config_is_rejected(self):
        with self.assertRaises(SchedulerError):
            Scheduler.validate("unknown")

    def test_scheduler_filters_attempted_nodes_and_uses_last_node(self):
        decision = Scheduler("round_robin").select_node(
            ["pve1", "pve2", "pve3"], last_node="pve1", excluded_nodes=["pve2"]
        )
        self.assertEqual(decision.node, "pve3")
        self.assertEqual(decision.algorithm, "round_robin")


class VmControllerTests(unittest.TestCase):
    def test_retries_next_node_after_clone_failure_without_vm(self):
        adapter = MagicMock()
        adapter.list_nodes.return_value = ["pve1", "pve2", "pve3"]
        adapter.vm_exists.return_value = False
        adapter.clone_and_get_ip.side_effect = [
            AdapterError("disk full", vmid=101, retryable=True),
            {"vmid": 101, "node": "pve2", "ip_address": "192.168.1.10"},
        ]
        store = FakeJobStore()
        controller = VmController(store, adapter_factory=lambda: adapter)

        result = controller.clone_vm(CloneRequest(template_vmid=9000))

        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["attempted_nodes"], ["pve1", "pve2"])
        self.assertEqual(result["selected_node"], "pve2")
        self.assertEqual(store.succeeded[0][1]["status"], "succeeded")
        self.assertEqual(len(store.reservations), 2)

    def test_does_not_retry_when_vm_was_created(self):
        adapter = MagicMock()
        adapter.list_nodes.return_value = ["pve1", "pve2"]
        adapter.vm_exists.return_value = True
        adapter.clone_and_get_ip.side_effect = AdapterError("clone task failed", vmid=101, retryable=True)
        store = FakeJobStore()
        controller = VmController(store, adapter_factory=lambda: adapter)

        with self.assertRaises(AdapterError):
            controller.clone_vm(CloneRequest(template_vmid=9000))

        adapter.clone_and_get_ip.assert_called_once()
        self.assertEqual(store.failed[0][0], "job-1")

    def test_clone_to_node_records_job_state(self):
        from backend.app.models.vm import CloneToNodeRequest

        adapter = MagicMock()
        adapter.clone_and_get_ip.return_value = {
            "vmid": 102, "node": "pve2", "ip_address": "192.168.1.11"
        }
        store = FakeJobStore()
        controller = VmController(store, adapter_factory=lambda: adapter)

        result = controller.clone_vm_to_node(CloneToNodeRequest(template_vmid=9000, target_node="pve2"))

        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["selected_node"], "pve2")
        self.assertEqual(store.created[0][0], "vm.clone_to_node")
        self.assertEqual(store.succeeded[0][2]["resource_id"], "102")


class ApiWiringTests(unittest.TestCase):
    def test_admin_config_requires_key(self):
        from fastapi import HTTPException
        from backend.app.main import _require_admin_key

        with patch.dict("os.environ", {"ADMIN_API_KEY": "secret"}):
            with self.assertRaises(HTTPException) as raised:
                _require_admin_key("wrong")
            self.assertEqual(raised.exception.status_code, 401)
            _require_admin_key("secret")

    def test_clone_route_calls_controller(self):
        from backend.app.main import clone_vm

        with patch("backend.app.main.vm_controller") as controller:
            controller.clone_vm.return_value = {
                "job_id": "job-1",
                "status": "succeeded",
                "vmid": 101,
                "node": "pve2",
                "ip_address": "192.168.1.10",
                "selected_node": "pve2",
                "algorithm": "round_robin",
                "attempted_nodes": ["pve2"],
            }
            result = clone_vm(CloneRequest(template_vmid=9000))

        self.assertEqual(result["job_id"], "job-1")
        controller.clone_vm.assert_called_once()


if __name__ == "__main__":
    unittest.main()