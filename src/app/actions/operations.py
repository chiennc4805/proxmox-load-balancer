import logging
import re

from app.framework.contracts.action import BaseAction, ActionResult, WorkflowError


logger = logging.getLogger(__name__)


class RecordSubmission(BaseAction):
    async def execute(self, args, context) -> ActionResult:
        response = args['upstream_response']
        operation_id = args['operation_id']
        if not 200 <= response.status_code < 300:
            await context.operations.fail(operation_id, unknown=response.status_code >= 500, upstream_status=response.status_code)
            return ActionResult(response)
        try:
            upid = response.json()['data']
            if not isinstance(upid, str) or not re.fullmatch(r'UPID:[^:]+:.+', upid):
                raise ValueError()
            task_node = upid.split(':')[1]
        except (ValueError, KeyError, TypeError) as error:
            raise WorkflowError('Proxmox submission did not return a valid task identifier', 502) from error
        logger.info('clone_accepted operation=%s upid=%s', operation_id, upid)
        await context.operations.submitted(operation_id, upid, task_node, response.status_code)
        return ActionResult(response)
