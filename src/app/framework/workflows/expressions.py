from dataclasses import fields, is_dataclass
import re
from typing import Any

from app.framework.contracts.action import ActionContext, WorkflowError


EXPRESSION = re.compile(r'\$\{([^{}]+)\}')
REFERENCE = re.compile(r'(?:request|steps)(?:\.[A-Za-z][A-Za-z0-9_]*)*')


def resolve_reference(reference: str, context: ActionContext) -> Any:
    if not REFERENCE.fullmatch(reference):
        raise WorkflowError(f'Invalid workflow reference: {reference}', 500)
    parts = reference.split('.')
    value: Any = {'request': context.request, 'steps': context.steps}[parts[0]]
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif is_dataclass(value) and part in {field.name for field in fields(value)}:
            value = getattr(value, part)
        else:
            raise WorkflowError(f'Unresolved workflow reference: {reference}', 500)
    return value


def resolve(value: Any, context: ActionContext) -> Any:
    if isinstance(value, dict):
        return {key: resolve(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    if not isinstance(value, str):
        return value
    match = EXPRESSION.fullmatch(value)
    if match:
        return resolve_reference(match.group(1), context)
    return EXPRESSION.sub(
        lambda match: str(resolve_reference(match.group(1), context)), value,
    )
