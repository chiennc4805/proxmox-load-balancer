from proxmoxer import ProxmoxAPI      
from config import config

class ProxmoxAdapter():
    
    def __init__(self, *, pve_api_host: str, pve_api_user: str, pve_token_name: str, pve_token_value: str, pve_verify_ssl: str, **kwargs):
        self.proxmox = ProxmoxAPI(
            pve_api_host,
            user=pve_api_user,
            token_name=pve_token_name,
            token_value=pve_token_value,
            verify_ssl=pve_verify_ssl,
            **kwargs
        )

    def clone():
        self.proxmox...
