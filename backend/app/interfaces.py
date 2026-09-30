from abc import ABC, abstractmethod

class ProvisioningEngine(ABC):

    def __init__(self):
        ...

    
    @abstractmethod
    def provision(self):
        ...


class Algorithm(ABC):
    @staticmethod
    @abstractmethod
    def select(nodes: list[str], last_node: str | None) -> str:
        """Choose one node from an already filtered candidate list."""
