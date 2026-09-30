import ipaddress
import unittest
from unittest.mock import MagicMock

from backend.app.proxmox_adapter import AdapterError, ProxmoxAdapter


class ProxmoxAdapterTests(unittest.TestCase):
    def setUp(self):
        settings = MagicMock()
        settings.network = ipaddress.ip_network("172.20.11.0/24")
        settings.clone_timeout = 5
        settings.agent_timeout = 5
        self.client = MagicMock()
        self.adapter = ProxmoxAdapter(settings, self.client)
        self.client.cluster.resources.get.return_value = [
            {"type": "lxc", "vmid": 7000},
            {"type": "qemu", "vmid": 9000, "node": "pve1", "template": 1, "name": "win-test"},
        ]

    def test_list_only_qemu_vms(self):
        self.assertEqual([vm["vmid"] for vm in self.adapter.list_vms()], [9000])
        self.client.cluster.resources.get.assert_called_once_with(type="vm")

    def test_clone_waits_for_tasks_and_selects_lab_ip(self):
        self.client.cluster.nextid.get.return_value = "10001"
        qemu = self.client.nodes.return_value.qemu
        qemu.return_value.clone.post.return_value = "UPID:clone"
        qemu.return_value.status.start.post.return_value = "UPID:start"
        self.client.nodes.return_value.tasks.return_value.status.get.return_value = {
            "status": "stopped", "exitstatus": "OK"
        }
        qemu.return_value.agent.return_value.get.return_value = {
            "result": [
                {"ip-addresses": [
                    {"ip-address": "127.0.0.1"},
                    {"ip-address": "169.254.2.5"},
                    {"ip-address": "172.20.11.101"},
                ]}
            ]
        }

        result = self.adapter.clone_and_get_ip(9000, name="lab-test")

        self.assertEqual(result, {"vmid": 10001, "node": "pve1", "ip_address": "172.20.11.101"})
        qemu.return_value.clone.post.assert_called_once_with(newid=10001, full=0, name="lab-test")
        qemu.return_value.status.start.post.assert_called_once_with()
        self.assertEqual(self.client.nodes.return_value.tasks.call_count, 2)

    def test_clone_task_failure_does_not_start_vm(self):
        self.client.cluster.nextid.get.return_value = "10001"
        self.client.nodes.return_value.qemu.return_value.clone.post.return_value = "UPID:clone"
        self.client.nodes.return_value.tasks.return_value.status.get.return_value = {
            "status": "stopped", "exitstatus": "ERROR"
        }

        with self.assertRaises(AdapterError) as raised:
            self.adapter.clone_and_get_ip(9000)

        self.assertEqual(raised.exception.vmid, 10001)
        self.client.nodes.return_value.qemu.return_value.status.start.post.assert_not_called()

    def test_non_template_is_rejected(self):
        self.client.cluster.resources.get.return_value[1]["template"] = 0
        with self.assertRaises(AdapterError) as raised:
            self.adapter.clone_and_get_ip(9000)
        self.assertEqual(raised.exception.status_code, 400)
        self.client.nodes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
