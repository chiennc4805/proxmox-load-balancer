import hmac
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from backend.app.proxmox_adapter import AdapterError, ProxmoxAdapter
from backend.app.scheduler.scheduler import Scheduler
from backend.app.scheduler.store import SchedulerError, get_store


app = FastAPI(title="Proxmox Load Balancer")


class CloneRequest(BaseModel):
    template_vmid: int = Field(ge=100)
    new_vmid: int | None = Field(default=None, ge=100)
    name: str | None = Field(default=None, max_length=100)
    full_clone: bool = False


class CloneToNodeRequest(CloneRequest):
    target_node: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")


class SchedulerConfigRequest(BaseModel):
    algorithm: str = Field(min_length=1)
    eligible_nodes: list[str] = Field(min_length=1)


def _api_error(exc: AdapterError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"message": str(exc), "vmid": exc.vmid},
    )


def _scheduler_error(exc: SchedulerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"message": str(exc)})


def _require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_API_KEY", "")
    if not expected:
        raise HTTPException(503, detail={"message": "Thiếu ADMIN_API_KEY"})
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(401, detail={"message": "Admin API key không hợp lệ"})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/vms")
def list_vms() -> list[dict]:
    try:
        return ProxmoxAdapter.from_env().list_vms()
    except AdapterError as exc:
        raise _api_error(exc) from exc


@app.post("/api/vms/clone")
def clone_vm(request: CloneRequest) -> dict:
    try:
        adapter = ProxmoxAdapter.from_env()
        choice = Scheduler(get_store()).select_node(adapter)
        result = adapter.clone_and_get_ip(
            request.template_vmid,
            new_vmid=request.new_vmid,
            name=request.name,
            full_clone=request.full_clone,
            target_node=choice["node"],
        )
        return {**result, "selected_node": choice["node"], "algorithm": choice["algorithm"]}
    except AdapterError as exc:
        raise _api_error(exc) from exc
    except SchedulerError as exc:
        raise _scheduler_error(exc) from exc


@app.post("/api/vms/clone-to-node")
def clone_vm_to_node(request: CloneToNodeRequest) -> dict:
    """Direct adapter test with an explicit target node."""
    try:
        return ProxmoxAdapter.from_env().clone_and_get_ip(
            request.template_vmid,
            new_vmid=request.new_vmid,
            name=request.name,
            full_clone=request.full_clone,
            target_node=request.target_node,
        )
    except AdapterError as exc:
        raise _api_error(exc) from exc


@app.get("/api/admin/scheduler")
def get_scheduler_config(x_admin_key: str | None = Header(default=None)) -> dict:
    _require_admin_key(x_admin_key)
    try:
        return get_store().get_config()
    except SchedulerError as exc:
        raise _scheduler_error(exc) from exc


@app.put("/api/admin/scheduler")
def set_scheduler_config(
    request: SchedulerConfigRequest, x_admin_key: str | None = Header(default=None)
) -> dict:
    _require_admin_key(x_admin_key)
    try:
        return get_store().set_config(request.algorithm, request.eligible_nodes)
    except SchedulerError as exc:
        raise _scheduler_error(exc) from exc
