from pathlib import Path
from typing import Any

import yaml

from app.framework.contracts.route import Route, RoutesConfig


DEFAULT_ROUTES_FILE = Path(__file__).resolve().parents[2] / 'config' / 'routes.yaml'


def read_routes(file_path: str | Path = DEFAULT_ROUTES_FILE) -> RoutesConfig:
    path = Path(file_path)

    with path.open(mode='r', encoding='utf-8') as routes_file:
        raw_config = yaml.safe_load(routes_file)

    if not isinstance(raw_config, dict):
        raise ValueError('Routes configuration must be a mapping')

    version = raw_config.get('version')

    raw_routes = raw_config.get('routes')
    if not isinstance(raw_routes, list):
        raise ValueError('Routes configuration must contain a routes list')

    routes = [_parse_route(raw_route) for raw_route in raw_routes]
    _validate_unique_routes(routes)

    return RoutesConfig(version=version, routes=routes)


def _parse_route(raw_route: Any) -> Route:
    if not isinstance(raw_route, dict):
        raise ValueError('Each route must be a mapping')

    required_fields = ('id', 'endpoint', 'method', 'workflow')
    missing_fields = [
        field_name
        for field_name in required_fields
        if not isinstance(raw_route.get(field_name), str)
        or not raw_route[field_name].strip()
    ]

    if missing_fields:
        missing = ', '.join(missing_fields)
        raise ValueError(f'Route is missing required string fields: {missing}')

    endpoint = raw_route['endpoint'].strip()
    if not endpoint.startswith('/'):
        raise ValueError(f'Route endpoint must start with /: {endpoint}')

    return Route(
        id=raw_route['id'].strip(),
        endpoint=endpoint,
        method=raw_route['method'].strip().upper(),
        workflow=raw_route['workflow'].strip(),
    )


def _validate_unique_routes(routes: list[Route]) -> None:
    seen_route_ids: set[str] = set()
    seen_endpoints: set[tuple[str, str]] = set()

    for route in routes:
        if route.id in seen_route_ids:
            raise ValueError(f'Duplicate route id: {route.id}')
        seen_route_ids.add(route.id)

        endpoint_key = (route.method, route.endpoint)
        if endpoint_key in seen_endpoints:
            raise ValueError(
                f'Duplicate intercepted route: {route.method} {route.endpoint}'
            )
        seen_endpoints.add(endpoint_key)
