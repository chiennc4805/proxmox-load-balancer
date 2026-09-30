import ipaddress
import os
from typing import Dict, List

def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()

def _required_int(name: str, *, minimum: int | None = None) -> int:
    raw_value = _required_env(name)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise RuntimeError(
            f"Environment variable {name} must be greater than or equal to {minimum}"
        )
    return value

def _required_bool(name: str) -> bool:
    raw_value = _required_env(name).lower()
    if raw_value not in {"true", "false"}:
        raise RuntimeError(f"Environment variable {name} must be true or false")
    return raw_value == "true"

def _protocol_ports() -> Dict[str, int]:
    result: Dict[str, int] = {}
    for item in _required_csv("VM_PROTOCOL_PORTS"):
        try:
            protocol, raw_port = item.split(":", 1)
            port = int(raw_port)
        except ValueError as exc:
            raise RuntimeError(
                "VM_PROTOCOL_PORTS must use protocol:port pairs separated by commas"
            ) from exc
        protocol = protocol.strip().lower()
        if protocol not in {"rdp", "vnc", "ssh"}:
            raise RuntimeError(f"Unsupported protocol in VM_PROTOCOL_PORTS: {protocol}")
        if not 1 <= port <= 65535:
            raise RuntimeError(f"Invalid port for {protocol}: {port}")
        result[protocol] = port
    return result

def _required_csv(name: str) -> List[str]:
    values = [item.strip() for item in _required_env(name).split(",") if item.strip()]
    if not values:
        raise RuntimeError(f"Environment variable {name} must contain at least one value")
    return values

class Config():

    def __init__(self):
        self.PVE_API_HOST = _required_env("PVE_API_HOST")
        self.PVE_API_USER = _required_env("PVE_API_USER")
        self.PVE_TOKEN_NAME = _required_env("PVE_TOKEN_NAME")
        self.PVE_TOKEN_VALUE = _required_env("PVE_TOKEN_VALUE")
        self.PVE_VERIFY_SSL = _required_bool("PVE_VERIFY_SSL")

        self.TEMPLATE_VMID_MIN = _required_int("TEMPLATE_VMID_MIN", minimum=1)
        self.TEMPLATE_VMID_MAX = _required_int("TEMPLATE_VMID_MAX", minimum=1)
        self.STUDENT_VMID_MIN = _required_int("STUDENT_VMID_MIN", minimum=1)
        self.STUDENT_VMID_MAX = _required_int("STUDENT_VMID_MAX", minimum=1)
        self.DEFAULT_TEMPLATE_VMID = _required_int("DEFAULT_TEMPLATE_VMID", minimum=1)
        if self.TEMPLATE_VMID_MIN > self.TEMPLATE_VMID_MAX:
            raise RuntimeError("TEMPLATE_VMID_MIN cannot be greater than TEMPLATE_VMID_MAX")
        if self.STUDENT_VMID_MIN > self.STUDENT_VMID_MAX:
            raise RuntimeError("STUDENT_VMID_MIN cannot be greater than STUDENT_VMID_MAX")
        if not self.TEMPLATE_VMID_MIN <= self.DEFAULT_TEMPLATE_VMID <= self.TEMPLATE_VMID_MAX:
            raise RuntimeError("DEFAULT_TEMPLATE_VMID must be inside the template VMID range")

        self.LAB_NETWORK_CIDR = _required_env("LAB_NETWORK_CIDR")
        try:
            self.LAB_NETWORK = ipaddress.ip_network(
                self.LAB_NETWORK_CIDR, strict=False
            )
        except ValueError as exc:
            raise RuntimeError("LAB_NETWORK_CIDR must be a valid IP network") from exc
        if self.LAB_NETWORK.version != 4:
            raise RuntimeError("LAB_NETWORK_CIDR must be an IPv4 network")

        self.VM_PROTOCOL_PORTS = _protocol_ports()
        self.DEFAULT_VM_PROTOCOL = _required_env("DEFAULT_VM_PROTOCOL").lower()
        if self.DEFAULT_VM_PROTOCOL not in self.VM_PROTOCOL_PORTS:
            raise RuntimeError(
                "DEFAULT_VM_PROTOCOL must be present in VM_PROTOCOL_PORTS"
            )
        self.DEFAULT_VM_PORT = self.VM_PROTOCOL_PORTS[self.DEFAULT_VM_PROTOCOL]
        self.VM_CLONE_TIMEOUT_SECONDS = _required_int(
            "VM_CLONE_TIMEOUT_SECONDS", minimum=1
        )
        self.VM_CONNECTION_TIMEOUT_SECONDS = _required_int(
            "VM_CONNECTION_TIMEOUT_SECONDS", minimum=1
        )
        self.VM_AGENT_TIMEOUT_SECONDS = _required_int(
            "VM_AGENT_TIMEOUT_SECONDS", minimum=1
        )
        self.VM_BOOT_WAIT_SECONDS = _required_int("VM_BOOT_WAIT_SECONDS", minimum=0)
        self.VM_VERIFY_CONNECTION = _required_bool("VM_VERIFY_CONNECTION")

config = Config()
