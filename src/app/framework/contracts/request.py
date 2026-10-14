from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointRequest:
    method: str
    api_path: str
    raw_api_path: bytes
    raw_query_string: bytes
    query_params: tuple[tuple[str, str], ...]
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes
    path_params: dict[str, str]
