from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import yaml

from app.framework.actions.loader import load_action
from app.framework.contracts.action import ActionContext, WorkflowError
from app.framework.contracts.execution_plan import ExecutionPlan, PlannedStep
from app.framework.workflows.base import BaseWorkflow
from app.framework.workflows.default_proxy import DefaultProxyWorkflow
from app.framework.workflows.engine import WorkflowEngine
from app.framework.workflows.validator import validate_workflow


WORKFLOWS_DIRECTORY = Path(__file__).resolve().parents[2] / 'config' / 'workflows'


@lru_cache(maxsize=16)
def load_plan(name: str) -> ExecutionPlan:
    matches = []
    for path in sorted(WORKFLOWS_DIRECTORY.glob('*.yaml')):
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
        if isinstance(raw, dict) and raw.get('name') == name:
            matches.append(validate_workflow(raw))
    if len(matches) != 1:
        raise WorkflowError('Workflow must resolve to exactly one definition', 500)
    definition = matches[0]
    return ExecutionPlan(
        definition.name,
        tuple(PlannedStep(step.id, load_action(step.uses), step.args) for step in definition.steps),
        definition.response,
    )


class ConfiguredWorkflow(BaseWorkflow):
    def __init__(self, plan, proxmox, settings, operations):
        self.plan, self.proxmox, self.settings = plan, proxmox, settings
        self.operations = operations

    async def execute(self, request):
        context = ActionContext(
            request=request, proxmox=self.proxmox,
            operations=self.operations,
            settings=self.settings, correlation_id=str(uuid4()),
        )
        return await WorkflowEngine().execute(self.plan, context)


def load_workflow(name, proxmox, settings, operations) -> BaseWorkflow:
    if name == 'default_proxy':
        return DefaultProxyWorkflow(proxmox)
    return ConfiguredWorkflow(load_plan(name), proxmox, settings, operations)
