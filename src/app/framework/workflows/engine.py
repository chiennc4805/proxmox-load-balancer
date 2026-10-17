import logging

from app.framework.contracts.action import ActionContext, ActionResult, WorkflowError
from app.framework.contracts.execution_plan import ExecutionPlan
from app.framework.contracts.proxmox import UpstreamResponse
from app.framework.workflows.expressions import resolve


logger = logging.getLogger(__name__)


class WorkflowEngine:
    async def execute(self, plan: ExecutionPlan, context: ActionContext) -> UpstreamResponse:
        try:
            for step in plan.steps:
                result = await step.action.execute(resolve(step.args, context), context)
                if not isinstance(result, ActionResult):
                    raise WorkflowError('BaseAction.execute() must return ActionResult', 500)
                context.steps[step.id] = result
            response = resolve(plan.response, context)
            if not isinstance(response, UpstreamResponse):
                raise WorkflowError('Workflow response must be an UpstreamResponse', 500)
            return response
        except Exception:
            if context.operation_id:
                try:
                    await context.operations.fail(
                        context.operation_id, unknown=context.submission_attempted,
                    )
                except Exception:
                    logger.error('Could not persist workflow failure operation=%s', context.operation_id)
            raise
