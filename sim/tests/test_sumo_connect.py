"""Unit tests for sim/sumo_connect.py: how SUMO is started and TraCI connects (the 'map not loading' fix)."""

from __future__ import annotations

import socket

import pytest

import sim.sumo_connect as sc


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.killed = False

    def kill(self):
        self.killed = True


class FakeSocket:
    def __init__(self):
        self.timeout = "unset"

    def settimeout(self, value):
        self.timeout = value


class FakeTraci:
    """Records how init() was called; can fail like a SUMO that never opens its port."""

    def __init__(self, fail=False):
        self.fail = fail
        self.init_args = None
        self.timeout_during_init = None
        self.sock = FakeSocket()
        self.started = None

    def init(self, port, numRetries, host, proc):
        self.init_args = {"port": port, "numRetries": numRetries, "host": host, "proc": proc}
        self.timeout_during_init = socket.getdefaulttimeout()
        print("Could not connect to TraCI server, retrying")  # what TraCI prints while it retries
        if self.fail:
            raise ConnectionRefusedError("nothing listens")

    def getConnection(self):
        return type("Conn", (), {"_socket": self.sock})()

    def start(self, cmd):
        self.started = cmd


@pytest.fixture
def popen(monkeypatch):
    made = []

    def fake_popen(cmd):
        made.append(FakeProc(cmd))
        return made[-1]

    monkeypatch.setattr(sc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("sumolib.miscutils.getFreeSocketPort", lambda: 45678)
    return made


def test_libsumo_runs_in_process_without_a_port(popen):
    api = FakeTraci()
    sc.start_sumo(api, ["sumo", "-c", "x.sumocfg"], is_libsumo=True)
    assert api.started == ["sumo", "-c", "x.sumocfg"] and popen == [] and api.init_args is None


def test_sumo_gets_a_free_port_and_traci_connects_to_ipv4(popen):
    api = FakeTraci()
    sc.start_sumo(api, ["sumo", "-n", "net.xml"])
    assert popen[0].cmd == ["sumo", "-n", "net.xml", "--remote-port", "45678"]
    assert api.init_args["host"] == "127.0.0.1"  # never "localhost", which may resolve to ::1 first
    assert api.init_args["port"] == 45678 and api.init_args["proc"] is popen[0]
    assert api.init_args["numRetries"] == 60


def test_each_attempt_has_a_time_limit_and_the_default_is_restored(popen):
    before = socket.getdefaulttimeout()
    api = FakeTraci()
    sc.start_sumo(api, ["sumo"])
    assert api.timeout_during_init == sc.ATTEMPT_TIMEOUT_S == 3.0
    assert socket.getdefaulttimeout() == before


def test_the_open_connection_has_no_time_limit(popen):
    api = FakeTraci()
    sc.start_sumo(api, ["sumo"])
    assert api.sock.timeout is None  # a long simulation step must not time out


def test_retry_messages_go_to_stderr_not_the_json_stream(popen, capsys):
    sc.start_sumo(FakeTraci(), ["sumo"])
    out, err = capsys.readouterr()
    assert out == "" and "retrying" in err


def test_failed_connection_kills_sumo_and_reports_the_error(popen, capsys):
    before = socket.getdefaulttimeout()
    with pytest.raises(ConnectionRefusedError):
        sc.start_sumo(FakeTraci(fail=True), ["sumo"])
    assert popen[0].killed
    assert socket.getdefaulttimeout() == before
    capsys.readouterr()
    print('{"type": "status"}')  # after the failure, stdout is the JSON stream again
    assert capsys.readouterr().out.strip() == '{"type": "status"}'
