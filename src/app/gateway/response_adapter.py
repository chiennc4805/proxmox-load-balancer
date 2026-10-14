from fastapi.responses import Response

from app.framework.contracts.proxmox import UpstreamResponse


_HOP_BY_HOP_HEADERS = {
    b'connection',
    b'keep-alive',
    b'proxy-authenticate',
    b'proxy-authorization',
    b'te',
    b'trailer',
    b'transfer-encoding',
    b'upgrade',
}


def to_http_response(upstream: UpstreamResponse, request_method: str) -> Response:
    connection_headers = {
        name.strip().lower()
        for raw_name, raw_value in upstream.headers
        if raw_name.lower() == b'connection'
        for name in raw_value.split(b',')
        if name.strip()
    }
    excluded_headers = _HOP_BY_HOP_HEADERS | connection_headers
    headers = [
        (name.lower(), value)
        for name, value in upstream.headers
        if name.lower() not in excluded_headers
    ]

    status_allows_body = not (
        upstream.status_code < 200
        or upstream.status_code in {204, 304}
        or request_method.upper() == 'HEAD'
    )
    body = upstream.body if status_allows_body else b''
    if status_allows_body and not any(name == b'content-length' for name, _ in headers):
        headers.append((b'content-length', str(len(body)).encode('ascii')))

    response = Response(content=body, status_code=upstream.status_code)
    response.raw_headers = headers
    return response
