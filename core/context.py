from dataclasses import dataclass, field
from core.proxmox_adapter import ProxmoxAdapter

@dataclass
class RuntimeContext():
    request: dict
    proxmox: ProxmoxAdapter