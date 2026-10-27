"""Wireless communication channel model for VANET emulation.

Implements distance-dependent quadratic path loss, stochastic delay with jitter,
a seeded random number generator for deterministic experiments, and a scheduled
delivery queue releasing messages on the simulation step covering their delay.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PendingDelivery:
    """Scheduled message awaiting delivery."""

    delivery_time: float
    message: Any
    receiver_id: str
    distance: float
    delay_ms: float


class WirelessChannel:
    """Emulated wireless channel with quadratic loss and delay/jitter queue."""

    def __init__(
        self,
        range_m: float = 300.0,
        base_loss: float = 0.0,
        max_loss: float = 0.2,
        base_delay_ms: float = 10.0,
        jitter_ms: float = 5.0,
        seed: int = 1,
    ) -> None:
        """Initialize channel model with RF and delay parameters."""
        self.range_m = float(range_m)
        self.base_loss = max(0.0, min(1.0, float(base_loss)))
        self.max_loss = max(self.base_loss, min(1.0, float(max_loss)))
        self.base_delay_ms = max(0.0, float(base_delay_ms))
        self.jitter_ms = max(0.0, float(jitter_ms))
        self.seed = int(seed)

        self.rng = random.Random(self.seed)
        self.queue: List[PendingDelivery] = []
        self.latencies_ms: List[float] = []

        self.stats: Dict[str, int] = {
            "sent": 0,
            "delivered": 0,
            "lost": 0,
        }

    def calculate_loss_probability(self, distance: float) -> float:
        """Calculate transmission failure probability based on Euclidean distance.

        Loss model:
            P(loss) = base_loss + (max_loss - base_loss) * (d / range)^2 for d <= range
            P(loss) = 1.0 for d > range
        """
        if distance > self.range_m or self.range_m <= 0.0:
            return 1.0
        if distance <= 0.0:
            return self.base_loss

        ratio = distance / self.range_m
        prob = self.base_loss + (self.max_loss - self.base_loss) * (ratio ** 2)
        return max(0.0, min(1.0, prob))

    def calculate_delay_ms(self) -> float:
        """Compute one-way transmission delay in milliseconds."""
        if self.jitter_ms <= 0.0:
            return self.base_delay_ms
        return self.base_delay_ms + self.rng.uniform(0.0, self.jitter_ms)

    def transmit(
        self,
        message: Any,
        sender_pos: Tuple[float, float],
        receiver_pos: Tuple[float, float],
        receiver_id: str,
        current_time: float,
    ) -> bool:
        """Attempt to transmit a message across the wireless medium.

        Returns True if the message was accepted for propagation, False if dropped due to loss or range.
        """
        dist = math.hypot(sender_pos[0] - receiver_pos[0], sender_pos[1] - receiver_pos[1])
        self.stats["sent"] += 1

        loss_prob = self.calculate_loss_probability(dist)
        if loss_prob >= 1.0 or self.rng.random() < loss_prob:
            self.stats["lost"] += 1
            return False

        delay_ms = self.calculate_delay_ms()
        delay_s = max(0.0, delay_ms / 1000.0)
        delivery_time = current_time + delay_s

        self.queue.append(
            PendingDelivery(
                delivery_time=delivery_time,
                message=message,
                receiver_id=receiver_id,
                distance=dist,
                delay_ms=delay_ms,
            )
        )
        return True

    def get_due_messages(self, current_time: float) -> List[Tuple[Any, str, float]]:
        """Pop and return all messages scheduled for delivery on or before current_time.

        Returns a list of tuples: (message, receiver_id, latency_ms).
        """
        due: List[Tuple[Any, str, float]] = []
        remaining: List[PendingDelivery] = []

        for item in self.queue:
            if item.delivery_time <= current_time:
                due.append((item.message, item.receiver_id, item.delay_ms))
                self.stats["delivered"] += 1
                self.latencies_ms.append(item.delay_ms)
            else:
                remaining.append(item)

        self.queue = remaining
        return due

    def get_pdr(self) -> float:
        """Compute packet delivery ratio (PDR)."""
        sent = self.stats["sent"]
        if sent == 0:
            return 1.0
        return float(self.stats["delivered"] / sent)

    def get_avg_latency_ms(self) -> float:
        """Compute average latency of successfully delivered messages."""
        if not self.latencies_ms:
            return self.base_delay_ms
        return float(sum(self.latencies_ms) / len(self.latencies_ms))
