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

    def test_list_only_online_nodes(self):
        self.client.cluster.resources.get.return_value = [
            {"type": "node", "node": "pve2", "status": "offline"},
            {"type": "node", "node": "pve1", "status": "online"},
            {"type": "qemu", "node": "pve3", "status": "running"},
        ]
        self.assertEqual(self.adapter.list_nodes(), ["pve1"])
        self.client.cluster.resources.get.assert_called_once_with(type="node")

    def test_clone_waits_for_tasks_and_selects_lab_ip(self):
        self.client.cluster.nextid.get.return_value = "10001"
        qemu = self.client.nodes.return_value.qemu
        qemu.return_value.clone.post.return_value = "UPID:clone"
        qemu.return_value.status.start.post.return_value = "UPID:start"
        qemu.return_value.config.get.side_effect = [
            {"net0": "virtio=52:54:00:00:00:01,bridge=vmbr1", "agent": "enabled=1"},
            {"net0": "virtio=52:54:00:00:00:02,bridge=vmbr1", "agent": "enabled=1"},
        ]
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
        qemu.return_value.config.post.assert_not_called()

    def test_dhcp_ip_outside_configured_subnet_uses_cloned_nic(self):
        self.client.cluster.nextid.get.return_value = "10002"
        qemu = self.client.nodes.return_value.qemu
        qemu.return_value.clone.post.return_value = "UPID:clone"
        qemu.return_value.status.start.post.return_value = "UPID:start"
        self.client.nodes.return_value.tasks.return_value.status.get.return_value = {
            "status": "stopped", "exitstatus": "OK"
        }
        qemu.return_value.config.get.side_effect = [
            {"net0": "virtio=52:54:00:00:00:01,bridge=vmbr0", "agent": "enabled=1"},
            {"net0": "virtio=52:54:00:00:00:02,bridge=vmbr0", "agent": "enabled=1"},
        ]
        qemu.return_value.agent.return_value.get.return_value = {
            "result": [
                {"name": "docker0", "hardware-address": "02:42:ac:14:0b:01",
                 "ip-addresses": [{"ip-address": "172.20.11.1"}]},
                {"name": "ens18", "hardware-address": "52:54:00:00:00:02",
                 "ip-addresses": [{"ip-address": "192.168.1.56"}]},
            ]
        }

        result = self.adapter.clone_and_get_ip(9000)

        self.assertEqual(result["ip_address"], "192.168.1.56")

    def test_explicit_target_uses_source_for_clone_and_target_for_vm_operations(self):
        self.client.cluster.nextid.get.return_value = "10004"
        nodes = {"pve1": MagicMock(), "pve2": MagicMock()}
        self.client.nodes.side_effect = nodes.__getitem__
        source_qemu = nodes["pve1"].qemu.return_value
        target_qemu = nodes["pve2"].qemu.return_value
        source_qemu.clone.post.return_value = "UPID:clone"
        target_qemu.status.start.post.return_value = "UPID:start"
        for node in nodes.values():
            node.tasks.return_value.status.get.return_value = {
                "status": "stopped", "exitstatus": "OK"
            }
        source_qemu.config.get.return_value = {
            "net0": "virtio=52:54:00:00:00:01,bridge=vmbr0", "agent": "enabled=1"
        }
        target_qemu.config.get.return_value = {
            "net0": "virtio=52:54:00:00:00:02,bridge=vmbr0", "agent": "enabled=1"
        }
        target_qemu.agent.return_value.get.return_value = {
            "result": [{"name": "ens18", "hardware-address": "52:54:00:00:00:02",
                        "ip-addresses": [{"ip-address": "192.168.1.58"}]}]
        }

        result = self.adapter.clone_and_get_ip(9000, target_node="pve2")

        self.assertEqual(result, {"vmid": 10004, "node": "pve2", "ip_address": "192.168.1.58"})
        source_qemu.clone.post.assert_called_once_with(newid=10004, full=0, target="pve2")
        nodes["pve1"].tasks.assert_called_once_with("UPID:clone")
        target_qemu.status.start.post.assert_called_once_with()
        nodes["pve2"].tasks.assert_called_once_with("UPID:start")
        source_qemu.status.start.post.assert_not_called()
        target_qemu.agent.assert_called_once_with("network-get-interfaces")

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
