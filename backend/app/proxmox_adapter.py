"""Import bridge for the existing `promox-adapter` directory name."""

from importlib import import_module

_module = import_module("backend.app.promox-adapter.proxmox_adapter")
AdapterError = _module.AdapterError
ProxmoxAdapter = _module.ProxmoxAdapter
