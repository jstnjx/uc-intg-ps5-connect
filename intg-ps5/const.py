"""Configuration model for the unified PS5/PSN integration (MPL-2.0)."""
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PSNConfig:
    identifier: str
    name: str
    npsso: str = ""
    ps_device: dict[str, Any] = field(default_factory=dict)
    launch_device_id: str = ""
