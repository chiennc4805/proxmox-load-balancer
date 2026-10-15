from importlib import import_module
import re

from app.framework.contracts.action import Action, WorkflowError
from app.infrastructure.proxmox.native import PREFIX, ProxmoxNativeAction, parse_native_call


def load_action(uses: str) -> Action:
    if uses.startswith(PREFIX):
        return ProxmoxNativeAction(parse_native_call(uses))
    if not re.fullmatch(r'adhoc(?:\.[A-Za-z][A-Za-z0-9_]*){2,}', uses):
        raise WorkflowError(f'Unsupported action: {uses}', 500)
    parts = uses.split('.')[1:]
    try:
        module = import_module('app.actions.' + '.'.join(parts[:-1]))
        implementation = getattr(module, parts[-1])
    except (ImportError, AttributeError) as error:
        raise WorkflowError(f'Action implementation is unavailable: {uses}', 500) from error
    if not isinstance(implementation, type) or not issubclass(implementation, Action):
        raise WorkflowError(f'Action must implement the Action contract: {uses}', 500)
    return implementation()
