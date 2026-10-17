from dataclasses import dataclass
from typing import Any

from app.framework.contracts.action import BaseAction


@dataclass(frozen=True)
class PlannedStep:
    id: str
    action: BaseAction
    args: Any


@dataclass(frozen=True)
class ExecutionPlan:
    name: str
    steps: tuple[PlannedStep, ...]
    response: Any
