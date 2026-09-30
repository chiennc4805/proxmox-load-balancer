"""Placement algorithms. Candidates are already filtered by the scheduler."""

from collections.abc import Sequence

from backend.app.interfaces import Algorithm


class RoundRobin(Algorithm):
    name = "round_robin"

    @staticmethod
    def select(nodes: Sequence[str], last_node: str | None) -> str:
        if not nodes:
            raise ValueError("Round robin cần ít nhất một node")
        ordered = sorted(set(nodes))
        if last_node not in ordered:
            return ordered[0]
        return ordered[(ordered.index(last_node) + 1) % len(ordered)]


ALGORITHMS = {RoundRobin.name: RoundRobin}
