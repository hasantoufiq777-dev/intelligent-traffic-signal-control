"""Unit tests for the VANET emulation layer, channel physics, and VANET state provider."""

from __future__ import annotations

import math
from pathlib import Path
import pytest

from sim.runner import run_simulation
from sim.vanet.channel import WirelessChannel
from sim.vanet.messages import BeaconMessage
from sim.vanet.nodes import RSUNode, is_vehicle_equipped


def test_channel_delivery_probability_non_increasing_with_distance() -> None:
    """Verify that packet delivery probability monotonically decreases with Euclidean distance."""
    channel = WirelessChannel(range_m=300.0, base_loss=0.05, max_loss=0.4)

    distances = [i * 10.0 for i in range(40)]  # 0 to 390m
    probabilities = [1.0 - channel.calculate_loss_probability(d) for d in distances]

    for i in range(len(probabilities) - 1):
        assert probabilities[i] >= probabilities[i + 1] - 1e-9, (
            f"Delivery probability increased from d={distances[i]} ({probabilities[i]}) "
            f"to d={distances[i+1]} ({probabilities[i+1]})"
        )

    # Beyond range (300m), delivery probability must be exactly 0
    assert channel.calculate_loss_probability(301.0) == 1.0
    assert channel.calculate_loss_probability(500.0) == 1.0


def test_channel_no_message_delivered_before_delay() -> None:
    """Verify that scheduled messages are NEVER popped before their assigned delay interval."""
    channel = WirelessChannel(
        range_m=300.0,
        base_loss=0.0,
        max_loss=0.0,
        base_delay_ms=20.0,
        jitter_ms=10.0,  # Delay between 20ms and 30ms (0.020s to 0.030s)
        seed=42,
    )

    beacon = BeaconMessage(sender_id="veh1", pos=(10.0, 10.0), speed=10.0)
    tx_time = 1.0
    success = channel.transmit(
        message=beacon,
        sender_pos=(10.0, 10.0),
        receiver_pos=(20.0, 20.0),
        receiver_id="J0",
        current_time=tx_time,
    )
    assert success is True

    # Check that at tx_time, nothing is due
    due_0 = channel.get_due_messages(tx_time)
    assert len(due_0) == 0

    # Check at 10ms after transmit (still < 20ms min delay)
    due_10ms = channel.get_due_messages(tx_time + 0.010)
    assert len(due_10ms) == 0

    # Check at 35ms after transmit (guaranteed > 30ms max delay)
    due_35ms = channel.get_due_messages(tx_time + 0.035)
    assert len(due_35ms) == 1
    msg, r_id, lat = due_35ms[0]
    assert msg.sender_id == "veh1"
    assert r_id == "J0"
    assert 20.0 <= lat <= 30.0


def test_vanet_queue_estimate_near_oracle_under_ideal_conditions() -> None:
    """Verify that with 100% penetration, 0 loss, and ideal conditions, MAE <= 0.5 vehicles."""
    res = run_simulation(
        scenario="normal",
        controller="adaptive_vanet",
        seed=1,
        penetration=1.0,
        loss=0.0,
        range_m=500.0,
        params={"vanet": {"max_loss": 0.0, "base_delay_ms": 0.0, "jitter_ms": 0.0}},
        max_steps=100,
        gui=False,
        live=False,
    )

    assert res["controller"] == "adaptive_vanet"
    assert res["queue_estimate_mae"] is not None
    # Estimation error between RSU and Oracle should be negligible (< 0.5 vehicles)
    assert res["queue_estimate_mae"] <= 0.5
    assert res["pdr"] == pytest.approx(1.0, 0.01)


def test_reproducibility_same_seed() -> None:
    """Verify that simulations with identical seeds produce bitwise-identical results."""
    res1 = run_simulation(
        scenario="navigation",
        controller="adaptive_vanet",
        seed=123,
        penetration=0.6,
        loss=0.1,
        max_steps=60,
        gui=False,
        live=False,
    )

    res2 = run_simulation(
        scenario="navigation",
        controller="adaptive_vanet",
        seed=123,
        penetration=0.6,
        loss=0.1,
        max_steps=60,
        gui=False,
        live=False,
    )

    for k in (
        "avg_waiting_time",
        "avg_time_loss",
        "avg_travel_time",
        "max_queue",
        "throughput_veh_per_hr",
        "pdr",
        "avg_latency_ms",
        "queue_estimate_mae",
    ):
        assert res1[k] == res2[k], f"Metric {k} differs between runs with same seed: {res1[k]} vs {res2[k]}"


def test_obu_equipped_vehicle_selection() -> None:
    """Verify OBU equipment hashing logic and emergency vehicle priority."""
    # Emergency vehicles are always equipped regardless of penetration
    assert is_vehicle_equipped("amb1", vtype="emergency", penetration=0.0) is True
    assert is_vehicle_equipped("police_car", vtype="emergency", penetration=0.0) is True
    assert is_vehicle_equipped("ev_unit_1", vtype="car", penetration=0.0) is True

    # At penetration 1.0, all normal cars are equipped
    assert is_vehicle_equipped("car1", vtype="car", penetration=1.0) is True
    assert is_vehicle_equipped("car2", vtype="car", penetration=1.0) is True

    # At penetration 0.0, no normal cars are equipped
    assert is_vehicle_equipped("car1", vtype="car", penetration=0.0) is False
    assert is_vehicle_equipped("car2", vtype="car", penetration=0.0) is False

    # At penetration 0.5, empirical fraction across population should be close to 0.5
    equipped_count = sum(
        1 for i in range(1000) if is_vehicle_equipped(f"car_{i}", vtype="car", penetration=0.5, seed=42)
    )
    empirical_fraction = equipped_count / 1000.0
    assert 0.45 <= empirical_fraction <= 0.55


def test_rsu_stale_beacon_expiry() -> None:
    """Verify that RSUNode discards beacons after stale_timeout."""
    rsu = RSUNode(
        tls_id="J0",
        pos=(0.0, 0.0),
        range_m=300.0,
        stale_timeout=3.0,
        queue_speed_threshold=1.0,
        queue_zone_length=100.0,
        penetration=1.0,
        controlled_lanes=["N2TL_0", "N2TL_1"],
        lane_directions={"N2TL_0": "N", "N2TL_1": "N"},
        edge_directions={"N2TL": "N"},
    )

    beacon = BeaconMessage(
        sender_id="veh1",
        pos=(0.0, 20.0),
        speed=0.0,
        lane_id="N2TL_0",
        edge_id="N2TL",
    )

    # Receive beacon at t=10.0
    rsu.receive_beacon(beacon, current_time=10.0)
    assert "veh1" in rsu.received_beacons
    assert rsu.estimate_queues(current_time=11.0)["N"] == 1

    # At t=12.5 (age = 2.5s <= 3.0s), still active
    assert rsu.estimate_queues(current_time=12.5)["N"] == 1

    # At t=13.5 (age = 3.5s > 3.0s), expired and pruned
    assert rsu.estimate_queues(current_time=13.5)["N"] == 0
    assert "veh1" not in rsu.received_beacons
