"""Placement scheduler: filter candidates, then delegate selection to an algorithm."""

from collections.abc import Sequence

from backend.app.models.placement import PlacementDecision
from backend.app.scheduler.algorithms import ALGORITHMS, Topsis
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
        node_stats: Sequence[dict] = (),
        resource_stats: dict | None = None,
    ) -> PlacementDecision:
        candidates = online_node_names(nodes, excluded_nodes)
        if not candidates:
            if nodes:
                raise SchedulerError("All online Proxmox nodes have already been attempted", 502)
            raise SchedulerError("No online Proxmox node is available")
        if self.algorithm == Topsis.name:
            ranked = self.score_nodes(
                candidates, node_stats, resource_stats or {}, excluded_nodes=()
            )
            return PlacementDecision(
                node=ranked[0]["node"], algorithm=self.algorithm, score=ranked[0]["score"]
            )

        selected = ALGORITHMS[self.algorithm].select(candidates, last_node, excluded_nodes)
        return PlacementDecision(node=selected, algorithm=self.algorithm)

    def score_nodes(
        self,
        nodes: Sequence[str],
        node_stats: Sequence[dict],
        resource_stats: dict,
        excluded_nodes: Sequence[str] = (),
    ) -> list[dict]:
        if self.algorithm != Topsis.name:
            raise SchedulerError("Scoring preview is only available for TOPSIS", 400)
        candidates = online_node_names(nodes, excluded_nodes)
        candidate_set = set(candidates)
        candidate_stats = [node for node in node_stats if node.get("name") in candidate_set]
        if not candidate_stats:
            raise SchedulerError("No online Proxmox node is available")
        if len(candidate_stats) != len(candidates):
            raise SchedulerError("Missing resource metrics for a TOPSIS candidate", 502)
        try:
            ranked = Topsis.rank(candidate_stats, resource_stats)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise SchedulerError(f"TOPSIS scoring failed: {exc}", 502) from exc
        if not ranked:
            raise SchedulerError("TOPSIS returned no scored nodes", 502)
        return ranked
