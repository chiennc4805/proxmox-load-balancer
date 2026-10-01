from abc import ABC, abstractmethod


class Algorithm(ABC):
    @staticmethod
    @abstractmethod
    def select(nodes: list[str], last_node: str | None, excluded_nodes: list[str] = ()) -> str:
        """Choose one node from an already filtered candidate list."""