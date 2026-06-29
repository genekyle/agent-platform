from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


StageHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class StageDefinition:
    name: str
    adapter_id: str
    handler: StageHandler
