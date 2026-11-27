"""Unit tests for sim/vanet/sensing_vanet.py: controllers see traffic only through the VANET layer."""

from __future__ import annotations

from sim.vanet.sensing_vanet import VanetStateProvider


class FakeTmc:
    def get_edge_speeds(self):
        return {"e1": 12.5}


class FakeRsu:
    edge_directions = {"in_n": "N", "in_e": "E"}


class FakeVanet:
    def __init__(self, incidents=None):
        self.tmc = FakeTmc()
        self.rsus = {"J0": FakeRsu()}
        self.incidents = incidents or {}
        self.stepped = []

    def get_rsu_queue_estimate(self, tls):
        return {"N": 4, "E": 1} if tls == "J0" else {}

    def get_rsu_waiting_times(self, tls):
        return {"N": 12.0}

    def get_rsu_ev_info(self, tls):
        return [{"id": "amb1", "dir": "N"}]

    def get_incidents(self):
        return dict(self.incidents)

    def step(self, t):
        self.stepped.append(t)


def test_name_is_vanet():
    assert VanetStateProvider(FakeVanet()).name == "vanet"


def test_readings_come_from_the_roadside_units():
    p = VanetStateProvider(FakeVanet())
    assert p.get_queues("J0") == {"N": 4, "E": 1}
    assert p.get_waiting_times("J0") == {"N": 12.0}
    assert p.get_ev_info("J0")[0]["id"] == "amb1"
    assert p.get_edge_speeds() == {"e1": 12.5}


def test_only_fully_blocked_roads_into_this_junction_count():
    net = FakeVanet({"in_n": "edge_blocked", "in_e": "lane_blocked", "elsewhere": "edge_blocked"})
    assert VanetStateProvider(net).get_blocked_approaches("J0") == {"N"}


def test_no_incidents_or_unknown_junction_means_nothing_blocked():
    assert VanetStateProvider(FakeVanet()).get_blocked_approaches("J0") == set()
    net = FakeVanet({"in_n": "edge_blocked"})
    assert VanetStateProvider(net).get_blocked_approaches("J9") == set()


def test_update_steps_the_vanet_emulation():
    net = FakeVanet()
    VanetStateProvider(net).update(42.0)
    assert net.stepped == [42.0]
