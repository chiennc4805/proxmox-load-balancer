from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.app.proxmox_adapter import AdapterError, ProxmoxAdapter


app = FastAPI(title="Proxmox Load Balancer")


class CloneRequest(BaseModel):
    template_vmid: int = Field(ge=100)
    new_vmid: int | None = Field(default=None, ge=100)
    name: str | None = Field(default=None, max_length=100)
    full_clone: bool = False


def _api_error(exc: AdapterError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"message": str(exc), "vmid": exc.vmid},
    )


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
        return ProxmoxAdapter.from_env().clone_and_get_ip(
            request.template_vmid,
            new_vmid=request.new_vmid,
            name=request.name,
            full_clone=request.full_clone,
        )
    except AdapterError as exc:
        raise _api_error(exc) from exc
