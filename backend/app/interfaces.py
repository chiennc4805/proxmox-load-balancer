from abc import ABC, abstractmethod

class ProvisioningEngine(ABC):

    def __init__(self):
        ...

    
    @abstractmethod
    def provision(self):
        ...


class Algorithm(ABC):
    
    def do(self):
        ...