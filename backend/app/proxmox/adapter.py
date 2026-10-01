"""Small Proxmox VE client for VM listing and clone/start/IP discovery."""

import ipaddress
import os
import re
import secrets
import time
from dataclasses import dataclass

from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException


class AdapterError(Exception):
    def __init__(
        self, message: str, status_code: int = 502, vmid: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.vmid = vmid
        self.retryable = retryable
        self.attempted_nodes: list[str] = []


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise AdapterError(f"{name} phải là số nguyên dương", 500) from exc
    if value < 1:
        raise AdapterError(f"{name} phải là số nguyên dương", 500)
    return value


@dataclass(frozen=True)
class AdapterSettings:
    host: str
    user: str
    token_name: str
    token_value: str
    verify_ssl: bool
    network: ipaddress.IPv4Network
    clone_timeout: int
    agent_timeout: int

    @classmethod
    def from_env(cls) -> "AdapterSettings":
        required = ("PVE_API_HOST", "PVE_API_USER", "PVE_TOKEN_NAME", "PVE_TOKEN_VALUE")
        missing = [key for key in required if not os.getenv(key, "").strip()]
        if missing:
            raise AdapterError("Thiếu cấu hình: " + ", ".join(missing), 503)
        host = os.environ["PVE_API_HOST"].strip()
        if "://" in host or "/" in host:
            raise AdapterError("PVE_API_HOST chỉ nhận IP/hostname, có thể kèm :8006", 500)
        verify = os.getenv("PVE_VERIFY_SSL", "false").strip().lower()
        if verify not in ("true", "false"):
            raise AdapterError("PVE_VERIFY_SSL phải là true hoặc false", 500)
        try:
            network = ipaddress.ip_network(
                os.getenv("LAB_NETWORK_CIDR", "172.20.11.0/24"), strict=False
            )
        except ValueError as exc:
            raise AdapterError("LAB_NETWORK_CIDR không hợp lệ", 500) from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise AdapterError("LAB_NETWORK_CIDR phải là mạng IPv4", 500)
        return cls(
            host=host,
            user=os.environ["PVE_API_USER"].strip(),
            token_name=os.environ["PVE_TOKEN_NAME"].strip(),
            token_value=os.environ["PVE_TOKEN_VALUE"].strip(),
            verify_ssl=verify == "true",
            network=network,
            clone_timeout=_positive_int_env("VM_CLONE_TIMEOUT_SECONDS", 300),
            agent_timeout=_positive_int_env("VM_AGENT_TIMEOUT_SECONDS", 180),
        )


class ProxmoxAdapter:
    def __init__(self, settings: AdapterSettings, client=None):
        self.settings = settings
        self.proxmox = client or ProxmoxAPI(
            settings.host,
            user=settings.user,
            token_name=settings.token_name,
            token_value=settings.token_value,
            verify_ssl=settings.verify_ssl,
            timeout=15,
        )

    @classmethod
    def from_env(cls) -> "ProxmoxAdapter":
        return cls(AdapterSettings.from_env())

    def list_resources(self, resource_type: str | None = None) -> list[dict]:
        """Read cluster resources directly from Proxmox VE."""
        if resource_type not in (None, "vm", "node", "storage"):
            raise AdapterError("Loại resource không hợp lệ", 400)
        try:
            params = {"type": resource_type} if resource_type else {}
            return self.proxmox.cluster.resources.get(**params)
        except Exception as exc:
            raise AdapterError("Không lấy được cluster resources từ Proxmox") from exc

    def list_vms(self) -> list[dict]:
        resources = self.list_resources("vm")
        return sorted(
            (
                {
                    "vmid": int(vm["vmid"]),
                    "name": vm.get("name", ""),
                    "node": vm.get("node", ""),
                    "status": vm.get("status", "unknown"),
                    "template": bool(int(vm.get("template", 0))),
                    "maxmem": vm.get("maxmem", 0),
                    "mem": vm.get("mem", 0),
                    "maxcpu": vm.get("maxcpu", 0),
                }
                for vm in resources
                if vm.get("type") == "qemu"
            ),
            key=lambda vm: vm["vmid"],
        )

    def list_node_resources(self) -> list[dict]:
        return sorted(
            (resource for resource in self.list_resources("node") if resource.get("type") == "node"),
            key=lambda resource: resource.get("node", ""),
        )

    def list_nodes(self) -> list[str]:
        """Return online Proxmox nodes available to the scheduler."""
        resources = self.list_node_resources()
        return sorted({
            resource["node"] for resource in resources
            if resource.get("type") == "node"
            and resource.get("status") == "online"
            and resource.get("node")
        })

    def _wait_task(
        self, node: str, upid: str, timeout: int, label: str, vmid: int,
        retryable_on_failure: bool = False,
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = self.proxmox.nodes(node).tasks(upid).status.get()
            except Exception as exc:
                raise AdapterError(f"Không đọc được trạng thái {label} trên Proxmox", vmid=vmid) from exc
            if state.get("status") == "stopped":
                if state.get("exitstatus") != "OK":
                    raise AdapterError(
                        f"{label} thất bại: {state.get('exitstatus', 'không rõ lý do')}",
                        vmid=vmid, retryable=retryable_on_failure,
                    )
                return
            time.sleep(2)
        raise AdapterError(f"Quá thời gian chờ {label}; kiểm tra VM {vmid} trên Proxmox", 504, vmid)

    @staticmethod
    def _nic_mac(net_config: str) -> str | None:
        first = net_config.split(",", 1)[0]
        if "=" not in first:
            return None
        mac = first.split("=", 1)[1]
        return mac.lower() if re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", mac) else None

    @staticmethod
    def _new_mac() -> str:
        return ":".join(f"{byte:02X}" for byte in bytes([0x02]) + secrets.token_bytes(5))

    def _prepare_clone_network(self, source_node: str, target_node: str, template_vmid: int, vmid: int) -> str | None:
        """Keep the clone's DHCP identity distinct and enable its guest agent."""
        try:
            source = self.proxmox.nodes(source_node).qemu(template_vmid).config.get()
            clone = self.proxmox.nodes(target_node).qemu(vmid).config.get()
            source_mac = self._nic_mac(source.get("net0", ""))
            clone_net0 = clone.get("net0", "")
            clone_mac = self._nic_mac(clone_net0)
            changes = {}

            # Proxmox normally generates a new MAC. Repair only if the clone kept it.
            if source_mac and clone_mac == source_mac:
                model_and_mac, *options = clone_net0.split(",", 1)
                model = model_and_mac.split("=", 1)[0]
                changes["net0"] = f"{model}={self._new_mac()}"
                if options:
                    changes["net0"] += f",{options[0]}"
                clone_mac = self._nic_mac(changes["net0"])

            agent_option = str(clone.get("agent", ""))
            if agent_option != "1" and not re.search(r"(?:^|,)enabled=1(?:,|$)", agent_option):
                changes["agent"] = "enabled=1"
            if changes:
                self.proxmox.nodes(target_node).qemu(vmid).config.post(**changes)
            return clone_mac
        except Exception as exc:
            raise AdapterError(f"VM {vmid} đã clone nhưng không cấu hình được NIC/Guest Agent", vmid=vmid) from exc

    def _find_ip(self, node: str, vmid: int, preferred_mac: str | None = None) -> str:
        deadline = time.monotonic() + self.settings.agent_timeout
        while time.monotonic() < deadline:
            try:
                data = self.proxmox.nodes(node).qemu(vmid).agent("network-get-interfaces").get()
                interfaces = data.get("result", data) if isinstance(data, dict) else data
                candidates = []
                for interface in interfaces:
                    if interface.get("name") == "lo":
                        continue
                    mac_matches = (
                        preferred_mac is not None
                        and str(interface.get("hardware-address", "")).lower() == preferred_mac
                    )
                    for address in interface.get("ip-addresses", []):
                        try:
                            ip = ipaddress.ip_address(address.get("ip-address", ""))
                        except ValueError:
                            continue
                        if not isinstance(ip, ipaddress.IPv4Address):
                            continue
                        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                            continue
                        candidates.append((ip, mac_matches))
                if candidates:
                    # Prefer the VM's primary NIC; DHCP may use a subnet other than LAB_NETWORK_CIDR.
                    best = min(candidates, key=lambda item: (not item[1], item[0] not in self.settings.network))
                    return str(best[0])
            except ResourceException as exc:
                if getattr(exc, "status_code", None) not in (500, 503, 595):
                    raise AdapterError("Không truy vấn được QEMU Guest Agent", vmid=vmid) from exc
            except (AttributeError, TypeError) as exc:
                raise AdapterError("Dữ liệu IP từ QEMU Guest Agent không hợp lệ", vmid=vmid) from exc
            time.sleep(3)
        raise AdapterError(
            f"VM {vmid} đã chạy nhưng Guest Agent chưa báo IPv4 hợp lệ; kiểm tra QEMU Guest Agent và DHCP trong VM",
            504,
            vmid,
        )

    def vm_exists(self, vmid: int) -> bool:
        """Check cluster state before retrying a failed clone on another node."""
        return any(vm["vmid"] == vmid for vm in self.list_vms())

    def clone_and_get_ip(
        self, template_vmid: int, *, new_vmid: int | None = None,
        name: str | None = None, full_clone: bool = False,
        target_node: str | None = None,
    ) -> dict:
        vms = self.list_vms()
        source = next((vm for vm in vms if vm["vmid"] == template_vmid), None)
        if source is None:
            raise AdapterError(f"Không tìm thấy VM {template_vmid}", 404)
        if not source["template"]:
            raise AdapterError(f"VM {template_vmid} chưa được chuyển thành template", 400)
        source_node = source["node"]
        if not source_node:
            raise AdapterError("Không xác định được node của template")
        target_node = target_node.strip() if target_node else source_node
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", target_node):
            raise AdapterError("Tên node đích không hợp lệ", 400)
        try:
            vmid = new_vmid or int(self.proxmox.cluster.nextid.get())
        except Exception as exc:
            raise AdapterError("Không cấp được VMID mới từ Proxmox") from exc
        if vmid == template_vmid or any(vm["vmid"] == vmid for vm in vms):
            raise AdapterError(f"VMID {vmid} đã tồn tại", 409)

        clone_args = {"newid": vmid, "full": int(full_clone)}
        if name:
            clone_args["name"] = name
        if target_node != source_node:
            clone_args["target"] = target_node
        try:
            upid = self.proxmox.nodes(source_node).qemu(template_vmid).clone.post(**clone_args)
        except ResourceException as exc:
            reason = str(exc.content or exc.status_message)[:300]
            raise AdapterError(
                f"Proxmox từ chối clone template {template_vmid} sang {target_node}: {reason}",
                vmid=vmid,
                retryable=getattr(exc, "status_code", None) not in (401, 403, 409),
            ) from exc
        except Exception as exc:
            raise AdapterError(
                f"Không xác định được kết quả clone template {template_vmid} sang {target_node}",
                vmid=vmid,
            ) from exc
        self._wait_task(
            source_node, upid, self.settings.clone_timeout, "clone VM", vmid,
            retryable_on_failure=True,
        )

        clone_mac = self._prepare_clone_network(source_node, target_node, template_vmid, vmid)

        try:
            start_upid = self.proxmox.nodes(target_node).qemu(vmid).status.start.post()
        except Exception as exc:
            raise AdapterError(f"VM {vmid} đã clone nhưng không khởi động được", vmid=vmid) from exc
        self._wait_task(target_node, start_upid, self.settings.clone_timeout, "khởi động VM", vmid)
        ip = self._find_ip(target_node, vmid, clone_mac)
        return {"vmid": vmid, "node": target_node, "ip_address": ip}
