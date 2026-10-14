from app.framework.contracts.proxmox import ProxmoxAPI, UpstreamResponse
from app.framework.contracts.request import EndpointRequest
from app.framework.workflows.base import BaseWorkflow


class DefaultProxyWorkflow(BaseWorkflow):
    def __init__(self, proxmox: ProxmoxAPI):
        self._proxmox = proxmox

    async def execute(self, request: EndpointRequest) -> UpstreamResponse:
        return await self._proxmox.request(
            method=request.method,
            path=request.raw_api_path,
            query_string=request.raw_query_string,
            headers=request.headers,
            body=request.body,
        )
