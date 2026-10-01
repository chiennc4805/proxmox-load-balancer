import unittest
from unittest.mock import MagicMock, patch

from backend.app.proxmox_adapter import AdapterError
from backend.app.scheduler.algorithms import RoundRobin
from backend.app.scheduler.scheduler import Scheduler
from backend.app.scheduler.store import SchedulerError, SchedulerStore


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
            SchedulerStore.validate("unknown")

    def test_scheduler_uses_adapter_online_nodes(self):
        adapter = MagicMock()
        adapter.list_nodes.return_value = ["pve1", "pve3"]
        store = MagicMock()
        store.reserve_node.return_value = {"node": "pve3", "algorithm": "round_robin"}
        self.assertEqual(Scheduler(store).select_node(adapter)["node"], "pve3")
        store.reserve_node.assert_called_once_with(["pve1", "pve3"])

    def test_retries_next_node_after_clone_failure_without_vm(self):
        adapter = MagicMock()
        adapter.list_nodes.return_value = ["pve1", "pve2", "pve3"]
        adapter.vm_exists.return_value = False
        adapter.clone_and_get_ip.side_effect = [
            AdapterError("disk full", vmid=101, retryable=True),
            {"vmid": 101, "node": "pve3", "ip_address": "192.168.1.10"},
        ]
        store = MagicMock()
        store.reserve_node.side_effect = [
            {"node": "pve2", "algorithm": "round_robin"},
            {"node": "pve3", "algorithm": "round_robin"},
        ]
        result = Scheduler(store).clone_vm(adapter, 9000)
        self.assertEqual(result["attempted_nodes"], ["pve2", "pve3"])
        self.assertEqual(result["selected_node"], "pve3")
        store.reserve_node.assert_any_call(["pve1", "pve2", "pve3"], ["pve2"])

    def test_does_not_retry_when_vm_was_created(self):
        adapter = MagicMock()
        adapter.list_nodes.return_value = ["pve1", "pve2"]
        adapter.vm_exists.return_value = True
        adapter.clone_and_get_ip.side_effect = AdapterError("clone task failed", vmid=101, retryable=True)
        store = MagicMock()
        store.reserve_node.return_value = {"node": "pve1", "algorithm": "round_robin"}
        with self.assertRaises(AdapterError):
            Scheduler(store).clone_vm(adapter, 9000)
        adapter.clone_and_get_ip.assert_called_once()


class ApiWiringTests(unittest.TestCase):
    def test_admin_config_requires_key(self):
        from fastapi import HTTPException
        from backend.app.main import _require_admin_key

        with patch.dict("os.environ", {"ADMIN_API_KEY": "secret"}):
            with self.assertRaises(HTTPException) as raised:
                _require_admin_key("wrong")
            self.assertEqual(raised.exception.status_code, 401)
            _require_admin_key("secret")

    def test_clone_passes_scheduler_choice_to_adapter(self):
        from backend.app.main import CloneRequest, clone_vm

        adapter = MagicMock()
        adapter.clone_and_get_ip.return_value = {
            "vmid": 101, "node": "pve2", "ip_address": "192.168.1.10"
        }
        with patch("backend.app.main.ProxmoxAdapter.from_env", return_value=adapter), \
             patch("backend.app.main.get_store"), \
             patch("backend.app.main.Scheduler") as scheduler_class:
            scheduler_class.return_value.clone_vm.return_value = {
                "vmid": 101, "node": "pve2", "ip_address": "192.168.1.10",
                "selected_node": "pve2", "algorithm": "round_robin", "attempted_nodes": ["pve2"]
            }
            result = clone_vm(CloneRequest(template_vmid=9000))
        self.assertEqual(result["selected_node"], "pve2")
        self.assertEqual(result["algorithm"], "round_robin")
        scheduler_class.return_value.clone_vm.assert_called_once_with(
            adapter, 9000, new_vmid=None, name=None, full_clone=False
        )


if __name__ == "__main__":
    unittest.main()
