from __future__ import annotations

import re
from collections.abc import Iterable

from app.framework.contracts.route import Route, RouteMatch


_PATH_PARAMETER_PATTERN = re.compile(r'^\{([A-Za-z_][A-Za-z0-9_]*)\}$')
DEFAULT_PROXY_ROUTE_ID = 'default_proxy'
DEFAULT_PROXY_WORKFLOW = 'default_proxy'


class RouteMatcher:
    def match(
        self,
        routes: Iterable[Route],
        request_method: str,
        api_path: str,
    ) -> RouteMatch:
        for route in routes:
            matched_route = self._match_route(
                route=route,
                request_method=request_method,
                api_path=api_path,
            )

            if matched_route is not None:
                return matched_route

        # Default proxy là fallback của routing, không phải một route cần khai báo
        # trong routes.yaml. Vì vậy mọi request đều có đúng một RouteMatch.
        return RouteMatch(
            route_id=DEFAULT_PROXY_ROUTE_ID,
            workflow=DEFAULT_PROXY_WORKFLOW,
            path_params={},
        )

    def _match_route(
        self,
        route: Route,
        request_method: str,
        api_path: str,
    ) -> RouteMatch | None:
        if route.method.upper() != request_method.upper():
            return None

        route_segments = self._split_path(route.endpoint, field_name='route endpoint')
        request_segments = self._split_path(api_path, field_name='API path')

        if len(route_segments) != len(request_segments):
            return None

        path_params: dict[str, str] = {}

        for route_segment, request_segment in zip(
            route_segments,
            request_segments,
            strict=True,
        ):
            parameter_name = self._get_parameter_name(route_segment)

            if parameter_name is not None:
                if not request_segment:
                    return None

                if parameter_name in path_params:
                    raise ValueError(
                        f'Duplicate path parameter: {parameter_name}'
                    )

                path_params[parameter_name] = request_segment
                continue

            if route_segment != request_segment:
                return None

        return RouteMatch(
            route_id=str(route.id),
            workflow=route.workflow,
            path_params=path_params,
        )

    @staticmethod
    def _split_path(path: str, field_name: str) -> list[str]:
        if not path.startswith('/'):
            raise ValueError(f'{field_name} must start with /')

        # Remove only the leading slash. Keeping the trailing empty segment
        # means `/clone` and `/clone/` remain two different paths.
        return path[1:].split('/')

    @staticmethod
    def _get_parameter_name(segment: str) -> str | None:
        parameter_match = _PATH_PARAMETER_PATTERN.fullmatch(segment)

        if parameter_match is not None:
            return parameter_match.group(1)

        if segment.startswith('{') or segment.endswith('}'):
            raise ValueError(f'Invalid path parameter segment: {segment}')

        return None
