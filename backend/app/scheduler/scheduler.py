"""Choose online nodes and retry safe clone failures in round-robin order."""

from backend.app.proxmox_adapter import AdapterError
from backend.app.scheduler.store import SchedulerError, SchedulerStore


class Scheduler:
    def __init__(self, store: SchedulerStore):
        self.store = store

    def select_node(self, adapter) -> dict[str, str]:
        return self.store.reserve_node(adapter.list_nodes())

    def clone_vm(
        self, adapter, template_vmid: int, *, new_vmid: int | None = None,
        name: str | None = None, full_clone: bool = False,
    ) -> dict:
        online_nodes = adapter.list_nodes()
        attempted: list[str] = []
        last_error: AdapterError | None = None
        for _ in range(len(online_nodes)):
            choice = self.store.reserve_node(online_nodes, attempted.copy())
            node = choice["node"]
            attempted.append(node)
            try:
                result = adapter.clone_and_get_ip(
                    template_vmid, new_vmid=new_vmid, name=name,
                    full_clone=full_clone, target_node=node,
                )
                return {
                    **result, "selected_node": node, "algorithm": choice["algorithm"],
                    "attempted_nodes": attempted,
                }
            except AdapterError as exc:
                exc.attempted_nodes = attempted.copy()
                if not exc.retryable or exc.vmid is None or adapter.vm_exists(exc.vmid):
                    raise
                last_error = exc
        if last_error is not None:
            error = AdapterError(
                f"Clone thất bại trên tất cả node đã thử; lỗi cuối: {last_error}",
                last_error.status_code, last_error.vmid,
            )
            error.attempted_nodes = attempted
            raise error
        raise SchedulerError("Không có node Proxmox nào đang online")
