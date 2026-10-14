from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request

from app.framework.contracts.proxmox import ProxmoxTimeoutError, ProxmoxTransportError
from app.framework.contracts.request import EndpointRequest
from app.framework.contracts.route import RouteMatch
from app.framework.workflows.default_proxy import DefaultProxyWorkflow
from app.gateway.response_adapter import to_http_response
from app.gateway.route_matcher import DEFAULT_PROXY_WORKFLOW, RouteMatcher
from app.infrastructure.config.route_loader import read_routes
from app.infrastructure.proxmox.client import ProxmoxClient
from app.settings import get_settings


router = APIRouter(tags=['proxmox'])

HTTP_METHODS = [
    'GET',
    'HEAD',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
    'OPTIONS',
    'TRACE',
]

def get_proxmox_client() -> ProxmoxClient:
    return ProxmoxClient(settings=get_settings())


@router.api_route('', methods=HTTP_METHODS)
@router.api_route('/{path:path}', methods=HTTP_METHODS)
async def handle_proxmox_request(
    request: Request,
    path: str = '',
    proxmox: ProxmoxClient = Depends(get_proxmox_client),
) -> Any:
    # Authentication sẽ được thêm ở API boundary trước khi request được xử lý.

    # FastAPI trả path không có dấu slash đầu tiên. Route configuration dùng
    # path tương đối so với /api2/json và luôn bắt đầu bằng slash.
    api_path = f'/{path}' if path else '/'

    # Slice hiện tại đọc file cấu hình tại request time. Khi flow đã ổn định,
    # RoutesConfig và RouteMatcher sẽ được khởi tạo một lần lúc application
    # startup mà không làm thay đổi contract của matcher.
    routes_config = read_routes()
    route_matcher = RouteMatcher()

    matched_route: RouteMatch = route_matcher.match(
        routes=routes_config.routes,
        request_method=request.method,
        api_path=api_path,
    )

    raw_path = request.scope.get('raw_path', b'')
    api_prefix = b'/api2/json'

    if raw_path.startswith(api_prefix):
        raw_api_path = raw_path[len(api_prefix):]
    else:
        # Fallback for non-ASGI test doubles that do not provide raw_path.
        raw_api_path = quote(api_path, safe='/').encode('ascii')

    # Body không thuộc trách nhiệm của RouteMatcher. Sau khi route đã match,
    # Controller đọc raw body và ghép nó với HTTP envelope cùng path params để
    # tạo input đầy đủ cho endpoint workflow. Không parse JSON/form tại đây.
    endpoint_request = EndpointRequest(
        method=request.method,
        api_path=api_path,
        raw_api_path=raw_api_path,
        raw_query_string=request.scope.get('query_string', b''),
        query_params=tuple(request.query_params.multi_items()),
        headers=tuple(request.headers.raw),
        body=await request.body(),
        path_params=dict(matched_route.path_params),
    )

    # Tại đây đã có đủ dữ liệu cho bước tiếp theo:
    #   matched_route.workflow  -> workflow cần thực thi
    #   endpoint_request        -> method/path/query/headers/body/path params
    #
    if matched_route.workflow == DEFAULT_PROXY_WORKFLOW:
        workflow = DefaultProxyWorkflow(proxmox=proxmox)
        try:
            upstream_response = await workflow.execute(endpoint_request)
        except ProxmoxTimeoutError as error:
            raise HTTPException(
                status_code=504,
                detail='The upstream response could not be completed',
            ) from error
        except ProxmoxTransportError as error:
            raise HTTPException(
                status_code=502,
                detail='The request could not be completed',
            ) from error

        return to_http_response(upstream_response, request.method)

    # TODO: Giao custom matched_route.workflow và endpoint_request cho
    # OperationService. Phần thực thi custom workflow chưa thuộc phạm vi hiện tại.
    return None
