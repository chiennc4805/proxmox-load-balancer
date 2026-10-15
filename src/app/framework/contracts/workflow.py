from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    uses: str
    args: Any


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    steps: tuple[WorkflowStep, ...]
    response: Any
