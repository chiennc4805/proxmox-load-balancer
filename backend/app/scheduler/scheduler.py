"""Choose an online, administratively enabled node for each clone."""

from backend.app.scheduler.store import SchedulerStore


class Scheduler:
    def __init__(self, store: SchedulerStore):
        self.store = store

    def select_node(self, adapter) -> dict[str, str]:
        return self.store.reserve_node(adapter.list_nodes())
