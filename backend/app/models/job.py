from dataclasses import dataclass
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class JobRecord:
    id: str
    job_type: str
    status: str
    request_payload: dict[str, Any]
    result_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    algorithm: str | None = None
    selected_node: str | None = None
    attempted_nodes: list[str] | None = None