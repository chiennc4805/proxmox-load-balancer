from abc import ABC, abstractmethod

from app.framework.contracts.proxmox import UpstreamResponse
from app.framework.contracts.request import EndpointRequest


class BaseWorkflow(ABC):
    @abstractmethod
    async def execute(self, request: EndpointRequest) -> UpstreamResponse:
        pass
