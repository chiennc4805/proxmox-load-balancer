from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True)
class PlacementContext:
    nodes: Sequence[str]
    last_node: str | None = None
    excluded_nodes: Sequence[str] = ()


@dataclass(frozen=True)
class PlacementDecision:
    node: str
    algorithm: str
    score: float | None = None
