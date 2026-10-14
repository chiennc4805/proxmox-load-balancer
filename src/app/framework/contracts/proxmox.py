from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes


class ProxmoxTransportError(Exception):
    """The upstream request failed without a complete HTTP response."""


class ProxmoxTimeoutError(ProxmoxTransportError):
    """The upstream connection or response timed out."""


class ProxmoxAPI(Protocol):
    async def request(
        self,
        method: str,
        path: str | bytes,
        *,
        query_string: bytes = b'',
        headers: tuple[tuple[bytes, bytes], ...] = (),
        body: bytes = b'',
    ) -> UpstreamResponse:
        ...
