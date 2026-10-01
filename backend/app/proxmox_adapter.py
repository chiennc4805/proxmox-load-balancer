"""Compatibility bridge for the old flat adapter import path."""

from backend.app.proxmox.adapter import AdapterError, AdapterSettings, ProxmoxAdapter

__all__ = ["AdapterError", "AdapterSettings", "ProxmoxAdapter"]
