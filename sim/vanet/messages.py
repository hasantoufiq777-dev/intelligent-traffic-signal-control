"""Message dataclasses for the VANET communication layer.

Defines protocol data units for V2I, V2V, and infrastructure signaling:
- BEACON: Periodic vehicle telemetry (position, speed, edge/lane).
- EV_ALERT: Emergency vehicle warning broadcasts (ETA, route, priority).
- INCIDENT_FLAG: Incident and lane blockage notices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MessageType(str, Enum):
    BEACON = "BEACON"
    EV_ALERT = "EV_ALERT"
    INCIDENT_FLAG = "INCIDENT_FLAG"
    SPAT = "SPAT"


@dataclass
class BaseMessage:
    """Base VANET message header."""

    msg_id: str = ""
    msg_type: MessageType = MessageType.BEACON
    timestamp: float = 0.0
    ttl: int = 1
    hop_count: int = 0


@dataclass
class BeaconMessage(BaseMessage):
    """Periodic telemetry beacon broadcasted by equipped vehicles."""

    sender_id: str = ""
    pos: Tuple[float, float] = (0.0, 0.0)
    speed: float = 0.0
    edge_id: str = ""
    lane_id: str = ""
    vtype: str = "car"

    def __post_init__(self) -> None:
        self.msg_type = MessageType.BEACON


@dataclass
class EvAlertMessage(BaseMessage):
    """Emergency vehicle alert broadcasted via V2I / V2V."""

    ev_id: str = ""
    pos: Tuple[float, float] = (0.0, 0.0)
    speed: float = 0.0
    route_edges: List[str] = field(default_factory=list)
    current_edge: str = ""
    # Progress along the route, known to the EV's OBU: index of the current route edge and
    # position on the current lane (m). route_index -1 = unknown (older/hand-made alerts).
    route_index: int = -1
    lane_pos: float = 0.0
    eta: float = 0.0
    sender_id: str = ""
    via_backbone: bool = False

    def __post_init__(self) -> None:
        self.msg_type = MessageType.EV_ALERT
        if self.ttl == 1:
            self.ttl = 3


@dataclass
class IncidentFlagMessage(BaseMessage):
    """Incident notice generated upon detecting stopped traffic or lane blockage."""

    edge_id: str = ""
    reason: str = "stopped_vehicle"
    # "lane_blocked" (some lanes blocked: very high cost), "edge_blocked" (all lanes
    # blocked: infinite cost) or "cleared" (the RSU no longer sees the incident).
    kind: str = "lane_blocked"
    rsu_id: str = ""
    lanes_blocked: int = 0
    n_lanes: int = 1
    n_vehicles: int = 0

    def __post_init__(self) -> None:
        self.msg_type = MessageType.INCIDENT_FLAG
        self.ttl = 5


@dataclass
class SpatMessage(BaseMessage):
    """Signal Phase and Timing broadcast by an RSU (I2V) for eco-driving (GLOSA).

    approaches: {"N": {"signal", "min_s", "max_s", "next_green_min_s", "next_green_max_s"}, ...}
    with times in seconds counted from `timestamp`.
    """

    tls_id: str = ""
    approaches: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.msg_type = MessageType.SPAT
