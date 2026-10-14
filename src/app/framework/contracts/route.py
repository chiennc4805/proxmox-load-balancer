from dataclasses import dataclass

@dataclass(frozen=True)
class Route():
    id: str
    endpoint: str
    method: str
    workflow: str

@dataclass(frozen=True)
class RoutesConfig:
    version: int
    routes: list[Route]

@dataclass(frozen=True)
class RouteMatch:
    route_id: str
    workflow: str
    path_params: dict[str, str]
