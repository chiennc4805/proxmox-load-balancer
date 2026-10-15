from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.framework.contracts.proxmox import ProxmoxAPI, UpstreamResponse
from app.framework.contracts.request import EndpointRequest


class WorkflowError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ActionResult:
    output: Any


@dataclass
class ActionContext:
    request: EndpointRequest
    proxmox: ProxmoxAPI
    operations: Any
    settings: Any
    correlation_id: str
    steps: dict[str, ActionResult] = field(default_factory=dict)
    operation_id: str | None = None
    submission_attempted: bool = False
    upstream_response: UpstreamResponse | None = None


class Action(ABC):
    @abstractmethod
    async def execute(self, args: Any, context: ActionContext) -> ActionResult:
        ...
