"""Public synchronous driver behavior and safety, independent of optional packages."""

import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import sleep

import pytest

from tark_chiller import Chiller, ProtocolError, Simulator


def test_core_import_and_construction_have_no_optional_imports_or_worker():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys, threading
from tark_chiller import Chiller, Simulator
before = threading.active_count()
chiller = Chiller(Simulator())
assert not chiller.is_connected
assert not chiller.read_status().connected
assert threading.active_count() == before
assert not {'dash', 'plotly', 'serial', 'tark_chiller.monitor'} & sys.modules.keys()
""",
        ],
        check=True,
    )


def test_explicit_connection_and_context_cleanup():
    chiller = Chiller(Simulator())
    for call in (
        chiller.read_temperature,
        chiller.read_setpoint,
        lambda: chiller.set_setpoint(18),
        chiller.start_monitoring,
    ):
        with pytest.raises(ConnectionError):
            call()
    with pytest.raises(RuntimeError, match="experiment failed"):
        with chiller:
            assert chiller.read_status().connected
            assert chiller.read_temperature() == pytest.approx(20)
            chiller.set_setpoint(18)
            assert chiller.read_setpoint() == 18
            raise RuntimeError("experiment failed")
    assert not chiller.is_connected
    chiller.disconnect()


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "20",
        "20 C",
        "68 F",
        None,
        math.nan,
        math.inf,
        -math.inf,
        1.99,
        40.01,
        -12,
        68,
        293.15,
        10**400,
    ],
)
def test_invalid_setpoints_never_touch_backend(value):
    class Untouchable:
        def __getattribute__(self, name):
            pytest.fail(f"Invalid setpoint accessed backend {name}")

    chiller = Chiller(Untouchable())
    with pytest.raises(ValueError):
        chiller.set_setpoint(value)


@pytest.mark.parametrize("value", [2, 40])
def test_default_bounds_inclusive(value):
    with Chiller(Simulator()) as chiller:
        chiller.set_setpoint(value)
        assert chiller.read_setpoint() == value


@pytest.mark.parametrize(
    "config",
    [
        {"setpoint_range": (-5, 30)},
        {"coolant": "unverified"},
        {"coolant": ""},
        {"coolant_source": ""},
        {"setpoint_range": [2, 40]},
        {"setpoint_range": (40, 2)},
        {"setpoint_range": (2, 2)},
        {"setpoint_range": (math.nan, 40)},
        {"setpoint_range": (2, math.inf)},
        {"setpoint_range": (True, 40)},
        {"setpoint_range": (2, "40")},
    ],
)
def test_custom_bounds_need_valid_limits_and_provenance(config):
    with pytest.raises(ValueError):
        Chiller(Simulator(), **config)


def test_custom_coolant_explicit_immutable_configuration():
    with Chiller(
        Simulator(),
        setpoint_range=(-5, 30),
        coolant="test mixture",
        coolant_source="test only; no physical suitability claim",
    ) as chiller:
        chiller.set_setpoint(-2)
        assert chiller.read_setpoint() == -2
        with pytest.raises(ValueError):
            chiller.set_setpoint(-6)
        for name in ("setpoint_range", "coolant", "coolant_source"):
            with pytest.raises(AttributeError):
                setattr(chiller, name, "bypass")


@pytest.mark.parametrize("value", [True, "20", None, math.nan, math.inf])
@pytest.mark.parametrize("operation", ["temperature", "setpoint"])
def test_bad_backend_reading_suspends_without_retry(value, operation, monkeypatch):
    backend = Simulator()
    monkeypatch.setattr(backend, f"_read_{operation}", lambda: value)
    with Chiller(backend, reconnect_attempts=2) as chiller:
        with pytest.raises(ProtocolError):
            getattr(chiller, f"read_{operation}")()
        assert not chiller.is_connected
        assert "suspended" in chiller.read_status().detail
        with pytest.raises(ConnectionError):
            chiller.read_temperature()


def test_outside_write_limits_reading_is_visible(monkeypatch):
    backend = Simulator()
    monkeypatch.setattr(backend, "_read_temperature", lambda: -3)
    monkeypatch.setattr(backend, "_read_setpoint", lambda: 45)
    with Chiller(backend) as chiller:
        assert chiller.read_temperature() == -3
        assert chiller.read_setpoint() == 45


def test_multiple_instruments_and_serialized_reads_writes():
    class SlowSimulator(Simulator):
        active = maximum = 0
        guard = Lock()

        def _read_temperature(self):
            with self.guard:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            try:
                sleep(0.002)
                return super()._read_temperature()
            finally:
                with self.guard:
                    self.active -= 1

        def _write_setpoint(self, value):
            self._read_temperature()
            super()._write_setpoint(value)

    backend = SlowSimulator()
    with Chiller(backend) as chiller, Chiller(Simulator()) as other:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda n: chiller.set_setpoint(18) if n % 2 else chiller.read_temperature(),
                    range(50),
                )
            )
        assert backend.maximum == 1
        assert chiller.read_setpoint() == 18
        assert other.read_setpoint() == 20


def test_simulator_thermal_response_and_disconnected_pause():
    now = [0.0]
    with Chiller(Simulator(clock=lambda: now[0], time_constant_s=10)) as chiller:
        chiller.set_setpoint(10)
        now[0] = 10
        expected = 10 + 10 * math.exp(-1)
        assert chiller.read_temperature() == pytest.approx(expected)
        chiller.disconnect()
        now[0] = 100
        chiller.connect()
        assert chiller.read_temperature() == pytest.approx(expected)
        assert chiller.read_setpoint() == 10


@pytest.mark.parametrize(
    "config",
    [{"reconnect_attempts": x} for x in (-1, 11, True, 1.5)]
    + [{"reconnect_delay_s": x} for x in (0, -1, math.inf, math.nan, 61)],
)
def test_recovery_configuration_is_bounded(config):
    with pytest.raises(ValueError):
        Chiller(Simulator(), **config)


def test_cancelled_queued_setpoint_does_not_cross_reconnect(monkeypatch):
    from threading import current_thread

    entered, release, queued, cancelled = Event(), Event(), Event(), Event()
    backend = Simulator()
    original_read = backend._read_temperature

    def blocking_read():
        entered.set()
        assert release.wait(3)
        return original_read()

    monkeypatch.setattr(backend, "_read_temperature", blocking_read)
    chiller = Chiller(backend)
    chiller.connect()
    lock, intent = chiller._lock, chiller._intent

    class ObservedLock:
        def __enter__(self):
            if current_thread().name.startswith("writer"):
                queued.set()
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

    def observe_intent(connected):
        token = intent(connected)
        if not connected:
            cancelled.set()
        return token

    monkeypatch.setattr(chiller, "_lock", ObservedLock())
    monkeypatch.setattr(chiller, "_intent", observe_intent)
    with (
        ThreadPoolExecutor(2) as pool,
        ThreadPoolExecutor(1, thread_name_prefix="writer") as writer,
    ):
        reading = pool.submit(chiller.read_temperature)
        assert entered.wait(1)
        writing = writer.submit(chiller.set_setpoint, 15)
        assert queued.wait(1)
        closing = pool.submit(chiller.disconnect)
        try:
            assert cancelled.wait(1)
        finally:
            release.set()
        with pytest.raises(ConnectionError):
            reading.result(2)
        with pytest.raises(ConnectionError):
            writing.result(2)
        closing.result(2)
    chiller.connect()
    assert chiller.read_setpoint() == 20
    chiller.disconnect()
