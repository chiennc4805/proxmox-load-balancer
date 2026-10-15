import re
from typing import Any

from app.framework.contracts.action import WorkflowError
from app.framework.contracts.workflow import WorkflowDefinition, WorkflowStep
from app.framework.workflows.expressions import EXPRESSION, REFERENCE


def references(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)
    elif isinstance(value, str):
        yield from (match.group(1) for match in EXPRESSION.finditer(value))


def validate_workflow(raw: Any) -> WorkflowDefinition:
    if not isinstance(raw, dict) or not isinstance(raw.get('name'), str):
        raise WorkflowError('Workflow must have a name', 500)
    if raw.get('mode') != 'sync' or not isinstance(raw.get('steps'), list) or not raw['steps']:
        raise WorkflowError('Workflow must define non-empty sequential sync steps', 500)
    seen: set[str] = set()
    steps = []
    for step in raw['steps']:
        if not isinstance(step, dict) or set(step) - {'id', 'uses', 'args'}:
            raise WorkflowError('Step supports only id, uses and args', 500)
        step_id, uses = step.get('id'), step.get('uses')
        if not isinstance(step_id, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', step_id) or step_id in seen:
            raise WorkflowError('Step IDs must be valid and unique', 500)
        if not isinstance(uses, str):
            raise WorkflowError('Step uses must be a string', 500)
        _check_references([uses, step.get('args', {})], seen)
        steps.append(WorkflowStep(step_id, uses, step.get('args', {})))
        seen.add(step_id)
    response = raw.get('response')
    if not isinstance(response, dict) or set(response) != {'from'}:
        raise WorkflowError('Workflow must define response.from', 500)
    _check_references(response['from'], seen)
    return WorkflowDefinition(raw['name'], tuple(steps), response['from'])


def _check_references(value: Any, available_steps: set[str]):
    for reference in references(value):
        if not REFERENCE.fullmatch(reference):
            raise WorkflowError(f'Invalid expression: {reference}', 500)
        parts = reference.split('.')
        if parts[0] == 'steps' and (len(parts) < 3 or parts[1] not in available_steps or parts[2] != 'output'):
            raise WorkflowError(f'Reference must point to an earlier step: {reference}', 500)
