from typing import Any

from backend.app.jobs.store import JobStore, JobStoreError, get_job_store
from backend.app.models.job import JobStatus
from backend.app.models.vm import CloneRequest, CloneToNodeRequest
from backend.app.proxmox_adapter import AdapterError, ProxmoxAdapter
from backend.app.scheduler.scheduler import Scheduler, SchedulerError


class VmController:
    def __init__(self, job_store: JobStore | None = None, adapter_factory=None):
        self.job_store = job_store or get_job_store()
        self.adapter_factory = adapter_factory or ProxmoxAdapter.from_env

    @staticmethod
    def _payload(request) -> dict[str, Any]:
        if hasattr(request, "model_dump"):
            return request.model_dump()
        return request.dict()

    @staticmethod
    def _adapter_error_payload(exc: AdapterError) -> dict[str, Any]:
        return {
            "message": str(exc),
            "status_code": exc.status_code,
            "vmid": exc.vmid,
            "retryable": exc.retryable,
            "attempted_nodes": exc.attempted_nodes,
        }

    @staticmethod
    def _scheduler_error_payload(exc: SchedulerError) -> dict[str, Any]:
        return {"message": str(exc), "status_code": exc.status_code}

    def clone_vm(self, request: CloneRequest) -> dict:
        job_id = self.job_store.create_job("vm.clone", self._payload(request))
        attempted: list[str] = []
        try:
            adapter = self.adapter_factory()
            algorithm = self.job_store.get_config()["algorithm"]
            placement_data = (
                adapter.get_placement_data(request.template_vmid)
                if algorithm == "topsis"
                else None
            )
            online_nodes = (
                [node["name"] for node in placement_data["nodes"]]
                if placement_data is not None
                else adapter.list_nodes()
            )
            last_error: AdapterError | None = None

            for _ in range(len(online_nodes)):
                def choose_node(last_node: str | None) -> dict[str, Any]:
                    decision = Scheduler(algorithm).select_node(
                        online_nodes,
                        last_node,
                        attempted.copy(),
                        node_stats=placement_data["nodes"] if placement_data else (),
                        resource_stats=placement_data["resource"] if placement_data else None,
                    )
                    attempted.append(decision.node)
                    return {
                        "node": decision.node,
                        "algorithm": decision.algorithm,
                        "attempted_nodes": attempted.copy(),
                        "score": decision.score,
                    }

                choice = self.job_store.reserve_placement(
                    job_id, "vm.clone", algorithm, choose_node
                )
                node = choice["node"]
                try:
                    result = adapter.clone_and_get_ip(
                        request.template_vmid,
                        new_vmid=request.new_vmid,
                        name=request.name,
                        full_clone=request.full_clone,
                        target_node=node,
                    )
                    response = {
                        **result,
                        "job_id": job_id,
                        "status": JobStatus.SUCCEEDED.value,
                        "selected_node": node,
                        "algorithm": algorithm,
                        "attempted_nodes": attempted.copy(),
                        "scheduler_score": choice.get("score"),
                    }
                    self.job_store.mark_succeeded(
                        job_id,
                        response,
                        resource_type="vm",
                        resource_id=str(result.get("vmid")),
                    )
                    return response
                except AdapterError as exc:
                    exc.attempted_nodes = attempted.copy()
                    if not exc.retryable or exc.vmid is None or adapter.vm_exists(exc.vmid):
                        self.job_store.mark_failed(job_id, self._adapter_error_payload(exc))
                        raise
                    last_error = exc

            if last_error is not None:
                error = AdapterError(
                    f"Clone failed on all attempted nodes; last error: {last_error}",
                    last_error.status_code,
                    last_error.vmid,
                )
                error.attempted_nodes = attempted.copy()
                self.job_store.mark_failed(job_id, self._adapter_error_payload(error))
                raise error

            raise SchedulerError("No online Proxmox node is available")
        except SchedulerError as exc:
            self.job_store.mark_failed(job_id, self._scheduler_error_payload(exc))
            raise
        except JobStoreError:
            raise
        except AdapterError:
            raise

    def preview_topsis(self, template_vmid: int) -> dict:
        """Score current online nodes without creating a job or cloning a VM."""
        adapter = self.adapter_factory()
        placement = adapter.get_placement_data(template_vmid)
        nodes = placement["nodes"]
        ranked = Scheduler("topsis").score_nodes(
            [node["name"] for node in nodes], nodes, placement["resource"]
        )
        metrics = {node["name"]: node for node in nodes}
        scored_nodes = [
            {"rank": rank, **entry, **metrics[entry["node"]]}
            for rank, entry in enumerate(ranked, start=1)
        ]
        return {
            "algorithm": "topsis",
            "template_vmid": template_vmid,
            "resource": placement["resource"],
            "selected_node": ranked[0]["node"],
            "nodes": scored_nodes,
        }

    def clone_vm_to_node(self, request: CloneToNodeRequest) -> dict:
        job_id = self.job_store.create_job("vm.clone_to_node", self._payload(request))
        try:
            result = self.adapter_factory().clone_and_get_ip(
                request.template_vmid,
                new_vmid=request.new_vmid,
                name=request.name,
                full_clone=request.full_clone,
                target_node=request.target_node,
            )
            response = {
                **result,
                "job_id": job_id,
                "status": JobStatus.SUCCEEDED.value,
                "selected_node": request.target_node,
                "algorithm": None,
                "attempted_nodes": [request.target_node],
            }
            self.job_store.mark_succeeded(
                job_id,
                response,
                resource_type="vm",
                resource_id=str(result.get("vmid")),
            )
            return response
        except AdapterError as exc:
            exc.attempted_nodes = [request.target_node]
            self.job_store.mark_failed(job_id, self._adapter_error_payload(exc))
            raise
