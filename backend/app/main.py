import hmac
import os

from fastapi import FastAPI, Header, HTTPException

from backend.app.controllers.job_controller import JobController
from backend.app.controllers.vm_controller import VmController
from backend.app.jobs.store import JobStoreError, get_job_store
from backend.app.models.vm import CloneRequest, CloneToNodeRequest, SchedulerConfigRequest
from backend.app.proxmox_adapter import AdapterError, ProxmoxAdapter
from backend.app.scheduler.scheduler import SchedulerError


app = FastAPI(title="Proxmox Load Balancer")
vm_controller = VmController()
job_controller = JobController()


def _api_error(exc: AdapterError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"message": str(exc), "vmid": exc.vmid, "attempted_nodes": exc.attempted_nodes},
    )


def _scheduler_error(exc: SchedulerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"message": str(exc)})


def _job_store_error(exc: JobStoreError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"message": str(exc)})


def _require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_API_KEY", "")
    if not expected:
        raise HTTPException(503, detail={"message": "Missing ADMIN_API_KEY"})
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(401, detail={"message": "Invalid admin API key"})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/vms")
def list_vms() -> list[dict]:
    try:
        return ProxmoxAdapter.from_env().list_vms()
    except AdapterError as exc:
        raise _api_error(exc) from exc


@app.get("/api/resources")
def list_resources(type: str | None = None) -> list[dict]:
    try:
        return ProxmoxAdapter.from_env().list_resources(type)
    except AdapterError as exc:
        raise _api_error(exc) from exc


@app.get("/api/nodes")
def list_nodes() -> list[dict]:
    try:
        return ProxmoxAdapter.from_env().list_node_resources()
    except AdapterError as exc:
        raise _api_error(exc) from exc


@app.post("/api/vms/clone")
def clone_vm(request: CloneRequest) -> dict:
    try:
        return vm_controller.clone_vm(request)
    except AdapterError as exc:
        raise _api_error(exc) from exc
    except SchedulerError as exc:
        raise _scheduler_error(exc) from exc
    except JobStoreError as exc:
        raise _job_store_error(exc) from exc


@app.post("/api/vms/clone-to-node")
def clone_vm_to_node(request: CloneToNodeRequest) -> dict:
    """Direct adapter test with an explicit target node."""
    try:
        return vm_controller.clone_vm_to_node(request)
    except AdapterError as exc:
        raise _api_error(exc) from exc
    except JobStoreError as exc:
        raise _job_store_error(exc) from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return job_controller.get_job(job_id)
    except JobStoreError as exc:
        raise _job_store_error(exc) from exc


@app.get("/api/admin/scheduler")
def get_scheduler_config(x_admin_key: str | None = Header(default=None)) -> dict:
    _require_admin_key(x_admin_key)
    try:
        return get_job_store().get_config()
    except JobStoreError as exc:
        raise _job_store_error(exc) from exc


@app.put("/api/admin/scheduler")
def set_scheduler_config(
    request: SchedulerConfigRequest, x_admin_key: str | None = Header(default=None)
) -> dict:
    _require_admin_key(x_admin_key)
    try:
        return get_job_store().set_config(request.algorithm)
    except (JobStoreError, SchedulerError) as exc:
        status_code = getattr(exc, "status_code", 503)
        raise HTTPException(status_code=status_code, detail={"message": str(exc)}) from exc