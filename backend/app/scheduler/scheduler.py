"""Placement scheduler: filter candidates, then delegate selection to an algorithm."""

from collections.abc import Sequence

from backend.app.models.placement import PlacementDecision
from backend.app.scheduler.algorithms import ALGORITHMS
from backend.app.scheduler.filters import online_node_names


class SchedulerError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class Scheduler:
    def __init__(self, algorithm: str):
        self.algorithm = algorithm
        if algorithm not in ALGORITHMS:
            raise SchedulerError("Unsupported scheduler algorithm", 400)

    @staticmethod
    def validate(algorithm: str):
        if algorithm not in ALGORITHMS:
            raise SchedulerError("Unsupported scheduler algorithm", 400)

    def select_node(
        self,
        nodes: Sequence[str],
        last_node: str | None = None,
        excluded_nodes: Sequence[str] = (),
    ) -> PlacementDecision:
        candidates = online_node_names(nodes, excluded_nodes)
        if not candidates:
            if nodes:
                raise SchedulerError("All online Proxmox nodes have already been attempted", 502)
            raise SchedulerError("No online Proxmox node is available")
        selected = ALGORITHMS[self.algorithm].select(candidates, last_node, excluded_nodes)
        return PlacementDecision(node=selected, algorithm=self.algorithm)