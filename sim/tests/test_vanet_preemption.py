"""Unit and integration tests for Module 8: VANET-based emergency preemption (adaptive_ev_vanet).

Tests:
1. Under ideal conditions (loss 0, delay 0), adaptive_ev_vanet matches adaptive_ev within small tolerance (<= 3s).
2. Causality invariant: alerts are never delivered or accessible before their propagation delay elapses.
3. Alert timeout safety: stale alerts older than alert_timeout (2.0s) expire and disable preemption.
4. V2V multi-hop relaying: equipped civilian vehicles relay alerts to reach RSUs beyond single-hop radio range.
5. Channel loss degradation: higher wireless channel loss degrades EV travel time (monotonically non-decreasing).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict
import pytest

from sim.runner import run_simulation
from sim.vanet.alerts import AlertManager
from sim.vanet.channel import WirelessChannel
from sim.vanet.messages import EvAlertMessage
from sim.vanet.nodes import RSUNode

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_vanet_preemption_matches_oracle_under_ideal_conditions() -> None:
    """Verify that with loss=0 and delay=0, adaptive_ev_vanet matches adaptive_ev within <= 3s."""
    # 1. Oracle baseline
    res_oracle = run_simulation(
        scenario="emergency",
        controller="adaptive_ev",
        seed=1,
        max_steps=180,
        gui=False,
        live=False,
    )
    oracle_ev_tt = res_oracle["ev_travel_time"]

    # 2. VANET preemption with ideal channel
    res_vanet = run_simulation(
        scenario="emergency",
        controller="adaptive_ev_vanet",
        seed=1,
        loss=0.0,
        range_m=500.0,
        params={"vanet": {"base_delay_ms": 0.0, "jitter_ms": 0.0}},
        max_steps=180,
        gui=False,
        live=False,
    )
    vanet_ev_tt = res_vanet["ev_travel_time"]

    print(f"\nOracle EV Travel Time: {oracle_ev_tt:.1f}s | VANET EV Travel Time: {vanet_ev_tt:.1f}s")
    assert oracle_ev_tt > 0.0
    assert vanet_ev_tt > 0.0
    assert abs(vanet_ev_tt - oracle_ev_tt) <= 3.0, (
        f"VANET EV travel time ({vanet_ev_tt}s) diverged from Oracle ({oracle_ev_tt}s) by > 3s"
    )


def test_alerts_never_used_before_delivery() -> None:
    """Verify that alerts are never ingested or returned before scheduled delivery time."""
    channel = WirelessChannel(
        range_m=300.0,
        base_loss=0.0,
        max_loss=0.0,
        base_delay_ms=500.0,  # 500ms delay = 0.5s
        jitter_ms=0.0,
        seed=42,
    )
    rsu = RSUNode(
        tls_id="J0",
        pos=(300.0, 300.0),
        range_m=300.0,
        controlled_lanes=["S2J0_0"],
        lane_directions={"S2J0_0": "S"},
        edge_directions={"S2J0": "S"},
    )
    alerts = AlertManager(
        channel=channel,
        rsus={"J0": rsu},
        tls_ids=["J0"],
        alert_interval=0.5,
        alert_timeout=2.0,
    )

    alert_msg = EvAlertMessage(
        msg_id="alert_test_1",
        ev_id="amb1",
        pos=(300.0, 150.0),
        speed=15.0,
        route_edges=["S2J0", "J02N"],
        current_edge="S2J0",
        timestamp=10.0,
    )

    # Transmit at t = 10.0s (scheduled delivery at t = 10.5s)
    channel.transmit(
        message=alert_msg,
        sender_pos=(300.0, 150.0),
        receiver_pos=(300.0, 300.0),
        receiver_id="J0",
        current_time=10.0,
    )

    # At t = 10.2s, message should not be delivered yet
    due_early = channel.get_due_messages(10.2)
    assert len(due_early) == 0, "Message was delivered prematurely before delay elapsed"
    assert len(alerts.get_rsu_ev_info("J0", 10.2)) == 0, "Alert info was accessible before delivery"

    # At t = 10.5s, message is delivered
    due_on_time = channel.get_due_messages(10.5)
    assert len(due_on_time) == 1
    msg, rsu_id, lat_ms = due_on_time[0]
    alerts.handle_delivered_alert(rsu_id, msg, lat_ms, 10.5)

    ev_info = alerts.get_rsu_ev_info("J0", 10.5)
    assert len(ev_info) == 1
    assert ev_info[0]["id"] == "amb1"
    assert ev_info[0]["dist"] <= 150.0


def test_alert_timeout_disables_preemption() -> None:
    """Verify that an alert ceases to trigger preemption once alert_timeout (2.0s) expires."""
    channel = WirelessChannel(range_m=300.0, base_loss=0.0, max_loss=0.0, seed=42)
    rsu = RSUNode(
        tls_id="J0",
        pos=(300.0, 300.0),
        range_m=300.0,
        controlled_lanes=["S2J0_0"],
        edge_directions={"S2J0": "S"},
    )
    alerts = AlertManager(
        channel=channel,
        rsus={"J0": rsu},
        tls_ids=["J0"],
        alert_timeout=2.0,
    )

    alert_msg = EvAlertMessage(
        msg_id="alert_test_timeout",
        ev_id="amb1",
        pos=(300.0, 200.0),
        speed=15.0,
        route_edges=["S2J0", "J02N"],
        current_edge="S2J0",
        timestamp=50.0,
    )

    # Ingest at t = 50.0s
    alerts._ingest_rsu_alert("J0", alert_msg, sim_time=50.0)

    # Within timeout (t = 51.5s, age = 1.5s <= 2.0s): EV info is active
    info_fresh = alerts.get_rsu_ev_info("J0", sim_time=51.5)
    assert len(info_fresh) == 1
    assert info_fresh[0]["alert_age"] == 1.5

    # After timeout (t = 52.5s, age = 2.5s > 2.0s): alert has expired
    info_expired = alerts.get_rsu_ev_info("J0", sim_time=52.5)
    assert len(info_expired) == 0, "Expired alert (> 2.0s) was not evicted"


def test_v2v_relay_extends_reach_beyond_single_hop_range() -> None:
    """Verify that equipped civilian vehicles relay alerts to RSUs beyond direct radio range."""
    # Direct range = 100m. EV at distance = 160m (beyond single-hop).
    # Civilian vehicle at distance = 80m from EV and 80m from RSU.
    channel = WirelessChannel(range_m=100.0, base_loss=0.0, max_loss=0.0, seed=1)
    rsu = RSUNode(
        tls_id="J0",
        pos=(300.0, 300.0),
        range_m=100.0,
        edge_directions={"S2J0": "S"},
    )
    alerts = AlertManager(
        channel=channel,
        rsus={"J0": rsu},
        tls_ids=["J0"],
        relay_enabled=True,
        max_hops=3,
    )

    ev_pos = (300.0, 140.0)    # Distance to RSU = 160m (> 100m range)
    car_pos = (300.0, 220.0)   # Distance to EV = 80m (<= 100m), distance to RSU = 80m (<= 100m)

    # Mock SUMO API returning the EV and the relay civilian car
    class MockSumo:
        class vehicle:
            @staticmethod
            def getIDList():
                return ["amb1", "civ_car_1"]

            @staticmethod
            def getTypeID(vid: str):
                return "emergency" if vid == "amb1" else "car"

            @staticmethod
            def getPosition(vid: str):
                return ev_pos if vid == "amb1" else car_pos

            @staticmethod
            def getSpeed(vid: str):
                return 15.0

            @staticmethod
            def getRoute(vid: str):
                return ["S2J0", "J02N"]

            @staticmethod
            def getRoadID(vid: str):
                return "S2J0"

    mock_sumo = MockSumo()

    # Step simulation with relay enabled
    alerts.step(sim_time=10.0, sumo_api=mock_sumo)

    # Deliver messages in channel queue
    due = channel.get_due_messages(10.1)
    for msg, rsu_id, lat_ms in due:
        alerts.handle_delivered_alert(rsu_id, msg, lat_ms, 10.1)

    ev_info = alerts.get_rsu_ev_info("J0", sim_time=10.1)
    assert len(ev_info) == 1, "Alert failed to reach RSU via V2V multi-hop relay"
    assert ev_info[0]["hop_count"] == 1, f"Expected 1 relay hop, got {ev_info[0]['hop_count']}"


def test_ev_travel_time_non_decreasing_with_channel_loss() -> None:
    """Verify that higher channel loss results in equal or higher EV travel time (within noise over seeds)."""
    seeds = [1, 2, 3]
    tts_loss_0 = []
    tts_loss_high = []

    for s in seeds:
        res_0 = run_simulation(
            scenario="emergency",
            controller="adaptive_ev_vanet",
            seed=s,
            loss=0.0,
            max_steps=180,
            gui=False,
            live=False,
        )
        tts_loss_0.append(res_0["ev_travel_time"])

        res_high = run_simulation(
            scenario="emergency",
            controller="adaptive_ev_vanet",
            seed=s,
            loss=0.8,
            max_steps=180,
            gui=False,
            live=False,
        )
        tts_loss_high.append(res_high["ev_travel_time"])

    avg_0 = sum(tts_loss_0) / len(tts_loss_0)
    avg_high = sum(tts_loss_high) / len(tts_loss_high)
    print(f"\nMean EV Travel Time (Loss 0.0): {avg_0:.1f}s | Mean EV Travel Time (Loss 0.8): {avg_high:.1f}s")

    assert avg_0 > 0.0
    assert avg_high > 0.0
    assert avg_high >= avg_0 - 1.0, f"Expected avg_high ({avg_high}s) >= avg_0 ({avg_0}s)"
