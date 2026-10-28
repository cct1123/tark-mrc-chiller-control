"""Polling traffic and cancellation checks against memory-only serial endpoints."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest

from tark_chiller.simulator import CalSimulator, T255PSimulator


@pytest.mark.parametrize("factory", [CalSimulator, T255PSimulator])
def test_serial_settings_are_never_changed_while_open(factory):
    class GuardedEndpoint(factory):
        def __setattr__(self, name, value):
            if name in ("timeout", "write_timeout") and getattr(self, "is_open", False):
                raise AssertionError("Changing live timeouts reconfigures the Windows UART")
            super().__setattr__(name, value)

    endpoint = GuardedEndpoint()
    with endpoint.chiller() as chiller:
        for _ in range(3):
            assert not chiller._poll(monotonic())["error"]
        chiller.apply_setpoint(18)
        assert chiller.read_setpoint() == 18


@pytest.mark.parametrize("factory,count", [(CalSimulator, 13), (T255PSimulator, 4)])
def test_poll_shares_checks_only_within_one_sample(factory, count, monkeypatch):
    endpoint = factory()
    requests = []
    original = endpoint.write

    def write(request):
        requests.append(request)
        return original(request)

    monkeypatch.setattr(endpoint, "write", write)
    with endpoint.chiller() as chiller:
        expected_setpoint = chiller.read_setpoint()
        for _ in range(2):
            requests.clear()
            sample = chiller._poll(monotonic())
            assert not sample["error"] and sample["status"]["connected"]
            assert sample["temperature_c"] is not None
            assert sample["setpoint_c"] == expected_setpoint
            assert len(requests) == count
        assert not endpoint.applied_writes


@pytest.mark.parametrize("factory", [CalSimulator, T255PSimulator])
def test_poll_still_rejects_sensor_fault(factory):
    endpoint = factory()
    with endpoint.chiller() as chiller:
        if isinstance(endpoint, CalSimulator):
            endpoint.registers[0x0306] |= 0x20
        else:
            endpoint.alarms = "000100"
        sample = chiller._poll(monotonic())
        assert sample["error"]
        assert sample["temperature_c"] is sample["setpoint_c"] is None
        assert not sample["status"]["connected"]
        assert not endpoint.applied_writes


@pytest.mark.parametrize("factory", [CalSimulator, T255PSimulator])
def test_disconnect_interrupts_long_read_timeout(factory, monkeypatch):
    endpoint = factory()
    reading = Event()
    original = endpoint.read
    with endpoint.chiller(timeout_s=3) as chiller:

        def read(size):
            reading.set()
            return original(size)

        monkeypatch.setattr(endpoint, "read", read)
        monkeypatch.setattr(endpoint, "_respond", lambda request: b"")
        with ThreadPoolExecutor(1) as workers:
            pending = workers.submit(chiller.read_temperature)
            assert reading.wait(1)
            started = monotonic()
            chiller.disconnect(timeout_s=1)
            assert monotonic() - started < 0.5
            with pytest.raises(ConnectionError):
                pending.result(timeout=1)
        assert not endpoint.is_open and not endpoint.applied_writes


def test_buffered_reply_uses_one_read_instead_of_one_per_byte(monkeypatch):
    endpoint = T255PSimulator()
    sizes = []
    original = endpoint.read

    def read(size):
        sizes.append(size)
        return original(size)

    monkeypatch.setattr(endpoint, "read", read)
    with endpoint.chiller() as chiller:
        sizes.clear()
        assert chiller.read_temperature() == 20
        assert len(sizes) == 3
        assert all(size > 1 for size in sizes)


@pytest.mark.parametrize("factory,count", [(CalSimulator, 30), (T255PSimulator, 3)])
def test_write_after_poll_runs_fresh_preflight_and_readback(factory, count, monkeypatch):
    endpoint = factory()
    requests = []
    original = endpoint.write

    def write(request):
        requests.append(request)
        return original(request)

    monkeypatch.setattr(endpoint, "write", write)
    with endpoint.chiller() as chiller:
        assert not chiller._poll(monotonic())["error"]
        requests.clear()
        chiller.apply_setpoint(18)
        assert len(requests) == count
        assert endpoint.applied_writes == 1
        assert chiller.read_setpoint() == 18
