import asyncio
from urllib.parse import quote

import requests
from urllib3.exceptions import HTTPError as UpstreamReadError
from urllib3.exceptions import TimeoutError as UpstreamTimeout

from app.framework.contracts.proxmox import (
    ProxmoxTimeoutError,
    ProxmoxTransportError,
    UpstreamResponse,
)
from app.settings import Settings


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

_REQUEST_HEADERS_OWNED_BY_CLIENT = _HOP_BY_HOP_HEADERS | {
    b'authorization',
    b'cookie',
    b'csrfpreventiontoken',
    b'content-length',
    b'host',
}


class ProxmoxClient:
    """Generic Proxmox adapter shared by workflows, actions and trackers."""

    def __init__(self, settings: Settings):
        self._settings = settings

    async def request(
        self,
        method: str,
        path: str | bytes,
        *,
        query_string: bytes = b'',
        headers: tuple[tuple[bytes, bytes], ...] = (),
        body: bytes = b'',
    ) -> UpstreamResponse:
        # requests is synchronous; run its network I/O outside the event loop.
        try:
            return await asyncio.to_thread(
                self._request,
                method,
                path,
                query_string,
                headers,
                body,
            )
        except (requests.Timeout, UpstreamTimeout) as error:
            raise ProxmoxTimeoutError('The upstream response timed out') from error
        except (requests.RequestException, UpstreamReadError) as error:
            raise ProxmoxTransportError('The upstream request failed') from error

    def _request(
        self,
        method: str,
        path: str | bytes,
        query_string: bytes,
        headers: tuple[tuple[bytes, bytes], ...],
        body: bytes,
    ) -> UpstreamResponse:
        url = self._build_url(path, query_string)
        upstream_headers = self._build_headers(headers)

        # Keep sessions local to a call so one shared client can be used
        # concurrently without sharing mutable cookies/session state.
        with requests.Session() as session:
            session.trust_env = False

            with session.request(
                method=method,
                url=url,
                headers=upstream_headers,
                data=body,
                allow_redirects=False,
                timeout=self._settings.pve_request_timeout_seconds,
                verify=self._settings.pve_verify_ssl,
                stream=True,
            ) as response:
                # Preserve compressed bytes and their Content-Encoding.
                response.raw.decode_content = False
                response_body = response.raw.read()
                response_headers = tuple(
                    (name.lower().encode('latin-1'), value.encode('latin-1'))
                    for name in response.raw.headers
                    for value in response.raw.headers.getlist(name)
                )

                # HTTP error statuses are valid upstream responses, not
                # transport failures. Leave interpretation to the caller.
                return UpstreamResponse(
                    status_code=response.status_code,
                    headers=response_headers,
                    body=response_body,
                )

    def _build_url(self, path: str | bytes, query_string: bytes) -> str:
        raw_path = (
            path.decode('ascii')
            if isinstance(path, bytes)
            else quote(path, safe="/%:@!$&'()*+,;=-._~")
        )
        if raw_path and not raw_path.startswith('/'):
            raise ValueError('Proxmox API path must start with /')

        url = f'{self._settings.pve_api_base_url}{raw_path}'
        if query_string:
            url = f'{url}?{query_string.decode("ascii")}'
        return url

    def _build_headers(
        self,
        headers: tuple[tuple[bytes, bytes], ...],
    ) -> dict[str, str]:
        connection_headers = {
            name.strip().lower()
            for raw_name, raw_value in headers
            if raw_name.lower() == b'connection'
            for name in raw_value.split(b',')
            if name.strip()
        }
        excluded_headers = _REQUEST_HEADERS_OWNED_BY_CLIENT | connection_headers
        upstream_headers = {
            raw_name.decode('latin-1'): raw_value.decode('latin-1')
            for raw_name, raw_value in headers
            if raw_name.lower() not in excluded_headers
        }

        if not any(name.lower() == 'accept-encoding' for name in upstream_headers):
            upstream_headers['Accept-Encoding'] = 'identity'
        upstream_headers['Authorization'] = self._settings.pve_authorization
        return upstream_headers
