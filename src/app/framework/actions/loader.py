from importlib import import_module
import re

from app.framework.contracts.action import BaseAction, WorkflowError
from app.infrastructure.proxmox.native import PREFIX as NATIVE_PREFIX, ProxmoxNativeAction, parse_native_call


def load_action(uses: str) -> BaseAction:
    if uses.startswith(NATIVE_PREFIX):
        return ProxmoxNativeAction(parse_native_call(uses))
    if not re.fullmatch(r'adhoc(?:\.[A-Za-z][A-Za-z0-9_]*){2,}', uses):
        raise WorkflowError(f'Unsupported action: {uses}', 500)
    parts = uses.split('.')[1:]
    try:
        module = import_module('app.actions.' + '.'.join(parts[:-1]))
        implementation = getattr(module, parts[-1])
    except (ImportError, AttributeError) as error:
        raise WorkflowError(f'Action implementation is unavailable: {uses}', 500) from error
    if not isinstance(implementation, type) or not issubclass(implementation, BaseAction):
        raise WorkflowError(f'Action must inherit BaseAction: {uses}', 500)
    return implementation()
