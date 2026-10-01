from pydantic import BaseModel, Field


class CloneRequest(BaseModel):
    template_vmid: int = Field(ge=100)
    new_vmid: int | None = Field(default=None, ge=100)
    name: str | None = Field(default=None, max_length=100)
    full_clone: bool = False


class CloneToNodeRequest(CloneRequest):
    target_node: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")


class SchedulerConfigRequest(BaseModel):
    algorithm: str = Field(min_length=1)