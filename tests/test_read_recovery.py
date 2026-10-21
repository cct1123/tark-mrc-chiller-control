"""Bounded T255P read recovery using memory endpoints, never bench hardware."""

import csv
from threading import Event
from time import monotonic, sleep

import pytest

import tark_chiller.controller as controller
from tark_chiller.controller import ProtocolError
from tark_chiller.simulator import T255PSimulator


def wrong_echo():
    body = b"#Z02010"
    return body + f"{sum(body) & 255:02X}\r".encode("ascii")


@pytest.fixture
def link(monkeypatch, request):
    now = [100.0]
    monkeypatch.setattr(controller, "perf_counter", lambda: now[0])
    endpoint = T255PSimulator()
    chiller = endpoint.chiller(reconnect_attempts=getattr(request, "param", 2), reconnect_delay_s=0.5)
    requests = []
    original = endpoint._respond

    def respond(request):
        requests.append((request[1:2], now[0]))
        return original(request)

    monkeypatch.setattr(endpoint, "_respond", respond)
    with chiller:
        monkeypatch.setattr(
            chiller._cancel, "wait", lambda seconds: now.__setitem__(0, now[0] + seconds)
        )
        requests.clear()
        yield chiller, endpoint, requests


@pytest.mark.parametrize("command", [b"U", b"J", b"I", b"H"])
@pytest.mark.parametrize("rejection", [wrong_echo(), b"#i2BE\r", b"#U1A9\r"])
def test_one_link_error_reopens_and_repeats_whole_poll(
    link, monkeypatch, command, rejection, caplog
):
    chiller, endpoint, requests = link
    original = endpoint._respond
    injected = False

    def respond(request):
        nonlocal injected
        result = original(request)
        if request[1:2] == command and not injected:
            injected = True
            return rejection
        return result

    monkeypatch.setattr(endpoint, "_respond", respond)
    sample = chiller._poll(monotonic())
    assert injected and not sample["error"]
    assert sample["temperature_c"] == 20 and sample["setpoint_c"] == 20
    assert sample["status"]["connected"]
    failed = next(i for i, (cmd, _) in enumerate(requests) if cmd == command)
    assert requests[failed + 1][0] == b"U"
    assert requests[failed + 1][1] - requests[failed][1] >= 3.0
    assert [cmd for cmd, _ in requests[-4:]] == [b"U", b"J", b"I", b"H"]
    assert "Read recovery 1/2" in caplog.text and "frame=" in caplog.text
    assert not endpoint.applied_writes


@pytest.mark.parametrize("link", [0], indirect=True)
def test_read_recovery_remains_opt_in(link, monkeypatch):
    chiller, endpoint, requests = link
    original = endpoint._respond

    def respond(request):
        original(request)
        return wrong_echo()

    monkeypatch.setattr(endpoint, "_respond", respond)
    with pytest.raises(ProtocolError, match="echo mismatch"):
        chiller.read_temperature()
    assert len(requests) == 1 and not endpoint.is_open
    assert "recovery suspended" in chiller._fault


def test_actual_bad_command_retries_then_reports_exact_device_error(link, monkeypatch):
    chiller, endpoint, requests = link
    original = endpoint._respond

    def respond(request):
        assert request == b".U83\r"  # The driver sends uppercase U, not lowercase i.
        original(request)
        return b"#i2BE\r"

    monkeypatch.setattr(endpoint, "_respond", respond)
    with pytest.raises(ProtocolError, match="communication error: bad command") as caught:
        chiller.read_temperature()
    assert "expected=b'U'" in str(caught.value) and "echo=b'i'" in str(caught.value)
    assert len(requests) == 3
    assert "Recovery exhausted" in chiller._fault and not endpoint.is_open


def test_successful_slow_poll_records_completion_and_clears_recovery_state(link, monkeypatch):
    chiller, endpoint, requests = link
    original = endpoint._respond
    ticks = iter([100.0, 104.0])
    monkeypatch.setattr(controller.time, "monotonic", lambda: next(ticks))

    def respond(request):
        assert chiller.snapshot()["poll_in_progress"] is True
        return original(request)

    monkeypatch.setattr(endpoint, "_respond", respond)
    sample = chiller._poll(99.0)
    assert not sample["error"]
    assert sample["sampled_monotonic"] == 100 and sample["completed_monotonic"] == 104
    assert not chiller.snapshot()["poll_in_progress"]
    assert chiller.snapshot()["recovery_error"] == ""


def test_failed_poll_keeps_known_backend_label(link, monkeypatch):
    chiller, endpoint, _ = link
    chiller._record_and_publish_sample(chiller._poll(monotonic()))
    monkeypatch.setattr(endpoint, "_respond", lambda request: b"#i2BE\r")
    sample = chiller._poll(monotonic())
    assert sample["error"] and sample["status"]["backend"] == "simulator"


def test_setpoint_rejection_keeps_validated_temperature_in_failed_csv_row(
    link, monkeypatch, tmp_path
):
    chiller, endpoint, _ = link
    original = endpoint._respond

    def respond(request):
        result = original(request)
        return b"#i2BE\r" if request[1:2] == b"H" else result

    monkeypatch.setattr(endpoint, "_respond", respond)
    path = tmp_path / "partial.csv"
    chiller.start_monitoring(interval_s=0.01, csv_path=path)
    deadline = monotonic() + 2
    while chiller.snapshot()["failed_samples"] < 1:
        assert monotonic() < deadline
        sleep(0.005)
    chiller.stop_monitoring()
    snapshot = chiller.snapshot()
    sample = snapshot["history"][-1]
    assert sample["temperature_c"] == 20 and sample["setpoint_c"] is None
    assert sample["status"]["backend"] == "simulator"
    assert not sample["status"]["connected"] and not endpoint.is_open
    # The worker may publish another failed poll before the caller stops it.
    assert snapshot["failed_samples"] >= 1
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == snapshot["failed_samples"]
    for row in rows:
        assert row["temperature_c"] == "20.0" and row["setpoint_c"] == ""
        assert "bad command" in row["error"] and row["connected"] == "False"


def test_failed_recovery_handshake_does_not_reuse_previous_partial_poll(link, monkeypatch):
    chiller, endpoint, _ = link
    original = endpoint._respond
    retrying = False

    def respond(request):
        nonlocal retrying
        result = original(request)
        if request[1:2] == b"H":
            retrying = True
            return wrong_echo()
        if retrying and request[1:2] == b"U":
            return b"#i2BE\r"
        return result

    monkeypatch.setattr(endpoint, "_respond", respond)
    sample = chiller._poll(monotonic())
    assert sample["temperature_c"] is sample["setpoint_c"] is None
    assert "bad command" in sample["error"]


def test_monitor_survives_exhausted_read_recovery_then_resumes(link, monkeypatch, tmp_path):
    chiller, endpoint, requests = link
    original = endpoint._respond

    def respond(request):
        result = original(request)
        return b"#i2BE\r" if len(requests) <= 3 else result

    monkeypatch.setattr(endpoint, "_respond", respond)
    path = tmp_path / "failed.csv"
    chiller.start_monitoring(interval_s=0.01, csv_path=path)
    deadline = monotonic() + 2
    while chiller.snapshot()["sample_count"] < 2:
        assert monotonic() < deadline
        sleep(0.005)
    snapshot = chiller.snapshot()
    assert chiller.running and not chiller._fault
    assert snapshot["failed_samples"] == 1 and not snapshot["service_error"]
    assert snapshot["logging_enabled"] and endpoint.is_open
    assert snapshot["history"][0]["temperature_c"] is None
    assert snapshot["history"][-1]["temperature_c"] == 20
    assert snapshot["history"][-1]["status"]["connected"]
    assert not endpoint.applied_writes
    chiller.stop_monitoring()
    path.unlink()  # CSV handle was released, including on Windows.


@pytest.mark.parametrize("fault", ["checksum", "sensor", "device_error"])
def test_other_protocol_faults_still_suspend_without_retry(link, monkeypatch, fault):
    chiller, endpoint, requests = link
    original = endpoint._respond

    def respond(request):
        result = original(request)
        if fault == "checksum":
            return result[:-3] + b"00\r"
        if fault == "device_error":
            body = b"#U3"
            return body + f"{sum(body) & 255:02X}\r".encode("ascii")
        return result

    if fault == "sensor":
        endpoint.alarms = "000100"
    monkeypatch.setattr(endpoint, "_respond", respond)
    sample = chiller._poll(monotonic())
    assert sample["error"] and not endpoint.is_open
    assert len(requests) == (2 if fault == "sensor" else 1)
    assert chiller._fault and not endpoint.applied_writes


@pytest.mark.parametrize("rejection", [wrong_echo(), b"#i2BE\r"])
def test_rejected_write_ack_never_retries_or_recovers(link, monkeypatch, rejection):
    chiller, endpoint, requests = link
    # The simulator fixture opts into writes; only this test requests a target.
    original = endpoint._respond

    def respond(request):
        result = original(request)
        return rejection if request[1:2] == b"M" else result

    monkeypatch.setattr(endpoint, "_respond", respond)
    with pytest.raises(ProtocolError, match="write outcome uncertain"):
        chiller.set_setpoint(19)
    assert endpoint.applied_writes == 1
    assert [cmd for cmd, _ in requests].count(b"M") == 1
    before = len(requests)
    with pytest.raises(ProtocolError, match="uncertain"):
        chiller.connect()
    assert len(requests) == before


def test_disconnect_cancels_wrong_echo_recovery_wait(monkeypatch):
    endpoint = T255PSimulator()
    chiller = endpoint.chiller(reconnect_attempts=2, reconnect_delay_s=10)
    waiting = Event()
    chiller.connect()
    original_wait = chiller._cancel.wait

    def wait(seconds):
        waiting.set()
        return original_wait(seconds)

    monkeypatch.setattr(chiller._cancel, "wait", wait)
    monkeypatch.setattr(endpoint, "_respond", lambda request: wrong_echo())
    try:
        chiller.start_monitoring(interval_s=0.01)
        assert waiting.wait(1)
        started = monotonic()
        chiller.disconnect(timeout_s=1)
        assert monotonic() - started < 0.5
        assert not chiller.running and not endpoint.is_open
    finally:
        chiller.disconnect()
