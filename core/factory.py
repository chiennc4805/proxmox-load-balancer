import importlib
from core.scheduler import Scheduler
from core.interfaces import ProvisioningEngine

class Factory():

    @staticmethod
    def import_class(module_path: str):
        parts = module_path.split('.')
        module_name = '.'.join(parts[:-1])
        class_name = parts[-1]
        module = importlib.import_module(module_name)
        return getattr(module, class_name)

    @staticmethod
    def create_provisioning_engine(mode: str) -> ProvisioningEngine:
        engine_cls = Factory.import_class(f"engines.{mode}")
        engine = engine_cls(...)
        return engine

    @staticmethod
    def create_scheduler(algorithm: str) -> Scheduler:
        algorithm_cls = Factory.import_class(f"algorithms.{algorithm}")
        scheduler = Scheduler(algorithm_cls)
        return scheduler
        

