from dataclasses import dataclass
import gzip
import json
import zlib
from typing import Any
from typing import Protocol


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes

    def json(self) -> Any:
        body = self.body
        encoding = dict(self.headers).get(b'content-encoding', b'identity').lower()
        if encoding == b'gzip':
            body = gzip.decompress(body)
        elif encoding == b'deflate':
            body = zlib.decompress(body)
        elif encoding != b'identity':
            raise ValueError('Unsupported upstream content encoding')
        return json.loads(body)


@dataclass(frozen=True)
class NativeArguments:
    """API parameters plus the original wire payload for intercepted requests."""

    params: dict[str, Any]
    body: bytes
    headers: tuple[tuple[bytes, bytes], ...]
    query_string: bytes = b''


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
