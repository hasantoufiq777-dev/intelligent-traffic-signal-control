"""Signal controller factory (Factory pattern) over interchangeable controllers (Strategy pattern).

Every junction controller has the same interface (BaseController: ``step(t)``, phase, SPaT, queues),
so the runner never branches on the control method. This module maps a controller name, as given on
the command line or by the dashboard, to the class that implements it. The ``*_vanet`` names use the
same class as their exact-data twin: what differs is the StateProvider injected (exact SUMO data or
VANET estimates, an Adapter), not the decision logic.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from sim.controllers.adaptive import AdaptiveController
from sim.controllers.base import BaseController
from sim.controllers.dynamic import DynamicController
from sim.controllers.fixed import FixedTimeController
from sim.controllers.leveling import LevelingController
from sim.controllers.preemption import PreemptionController
from sim.sensing import StateProvider

# name -> controller class (the provider decides whether it sees exact data or VANET estimates)
CONTROLLERS: Dict[str, Type[BaseController]] = {
    "level": LevelingController,  # default mode: longest queue down to the next one, then repeat
    "level_vanet": LevelingController,
    "fixed": FixedTimeController,
    "dynamic": DynamicController,  # congestion-aware phase choice and green length
    "dynamic_vanet": DynamicController,
    "adaptive": AdaptiveController,
    "adaptive_vanet": AdaptiveController,
    "adaptive_ev": PreemptionController,
    "adaptive_ev_tuned": PreemptionController,
    "adaptive_ev_vanet": PreemptionController,
    "adaptive_ev_vanet_tuned": PreemptionController,
}


def create_controller(controller_name: str, tls_id: str, params: Dict[str, Any], provider: StateProvider,
                      sumo_api: Any = None) -> BaseController:
    """The controller for one junction, with its parameters and state provider injected."""
    cls = CONTROLLERS.get(controller_name.lower())
    if cls is None:
        raise ValueError(f"Controller '{controller_name}' is not supported or not implemented yet.")
    return cls(tls_id, params=params, provider=provider, sumo_api=sumo_api)
