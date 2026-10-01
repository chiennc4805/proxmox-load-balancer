"""Placement algorithms. Candidates are already filtered by the scheduler."""

from collections.abc import Sequence

from backend.app.interfaces import Algorithm
from backend.app.scheduler.rust_topsis import rank_nodes


class RoundRobin(Algorithm):
    name = "round_robin"

    @staticmethod
    def select(
        nodes: Sequence[str], 
        last_node: str | None, 
        excluded_nodes: Sequence[str] = ()
    ) -> str:
        if not nodes:
            raise ValueError("Round robin cần ít nhất một node")
        ordered = sorted(set(nodes))
        excluded = set(excluded_nodes)
        start = (ordered.index(last_node) + 1) % len(ordered) if last_node in ordered else 0
        for offset in range(len(ordered)):
            node = ordered[(start + offset) % len(ordered)]
            if node not in excluded:
                return node
        raise ValueError("Không còn node nào để thử")


class Topsis:
    name = "topsis"

    @staticmethod
    def rank(nodes: Sequence[dict], resource: dict) -> list[dict]:
        return rank_nodes(nodes, resource)


ALGORITHMS = {RoundRobin.name: RoundRobin, Topsis.name: Topsis}
