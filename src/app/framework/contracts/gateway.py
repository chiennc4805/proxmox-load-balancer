from fastapi import Request
from dataclasses import dataclass

@dataclass(frozen=True)
class GatewayContext:
    request: Request
    api_path: str
    path_params: dict[str, str]