"""Public synchronous driver behavior and safety, independent of optional packages."""

import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from time import sleep

import pytest
from fakes import FakeChiller, fake_settings

from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import Cal33xx, ProtocolError
from tark_chiller.simulator import CalSimulator, T255PSimulator


def test_public_modules_have_no_legacy_aliases():
    import importlib.util

    import tark_chiller
    from tark_chiller import controller, protocols

    assert TarkChiller.__name__ == "TarkChiller"
    assert not hasattr(tark_chiller, "TarkChiller")
    for name in ("T255P", "Cal33xx", "ProtocolError"):
        assert hasattr(protocols, name)
        assert not hasattr(controller, name)
    for name in (
        "SerialDevice",
        "Chiller",
        "Simulator",
        "Monitor",
        "Codec",
        "Status",
        "Sample",
        "Snapshot",
        "SerialSettings",
        "RS485Mode",
    ):
        assert not hasattr(controller, name)
    for name in ("serial", "monitor", "__main__"):
        assert importlib.util.find_spec(f"tark_chiller.{name}") is None
    package = Path(controller.__file__).parent
    assert {p.name for p in package.glob("*.py")} == {
        "controller.py",
        "protocols.py",
        "gui.py",
        "_views.py",
        "simulator.py",
    }


def test_read_recovery_budget_is_per_operation(monkeypatch):
    backend = FakeChiller(reconnect_attempts=1, reconnect_delay_s=0.001)
    original = backend._read_temperature
    calls = 0

    def intermittent_read():
        nonlocal calls
        calls += 1
        if calls % 2:
            raise OSError("Transient read failure")
        return original()

    monkeypatch.setattr(backend, "_read_temperature", intermittent_read)
    with backend as chiller:
        assert chiller.read_temperature() == 20
        assert chiller.read_temperature() == 20
        assert calls == 4 and chiller.is_connected


def test_core_import_and_construction_have_no_optional_imports_or_worker():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys, threading
from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import T255P
assert "tark_chiller.simulator" not in sys.modules
before = threading.active_count()
settings = dict(port="UNOPENED", baudrate=9600, bytesize=8, parity="N", stopbits=1,
                xonxoff=True, rtscts=False, dsrdtr=False, rts=False, dtr=False)
chiller = TarkChiller(settings, protocol=T255P())
assert not chiller.is_connected
assert not chiller.collect_status()["connected"]
assert threading.active_count() == before
assert 'serial' in sys.modules
assert not {'dash', 'plotly'} & sys.modules.keys()
""",
        ],
        check=True,
    )


def test_explicit_connection_and_context_cleanup():
    endpoint = T255PSimulator()
    chiller = endpoint.chiller()
    for call in (
        chiller.read_temperature,
        chiller.read_setpoint,
        lambda: chiller.apply_setpoint(18),
        chiller.start_monitoring,
    ):
        with pytest.raises(ConnectionError):
            call()
    with pytest.raises(RuntimeError, match="experiment failed"):
        with chiller:
            assert chiller.collect_status()["connected"]
            assert chiller.read_temperature() == pytest.approx(20)
            chiller.apply_setpoint(18)
            assert chiller.read_setpoint() == 18
            raise RuntimeError("experiment failed")
    assert not chiller.is_connected and not endpoint.is_open
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
    def forbidden(**settings):
        pytest.fail("Invalid setpoint attempted serial I/O")

    chiller = TarkChiller(fake_settings(), protocol=Cal33xx(1), serial_factory=forbidden)
    with pytest.raises(ValueError):
        chiller.apply_setpoint(value)


@pytest.mark.parametrize("value", [2, 40])
def test_default_bounds_inclusive(value):
    with CalSimulator().chiller() as chiller:
        chiller.apply_setpoint(value)
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
        TarkChiller(fake_settings(), protocol=Cal33xx(1), **config)


def test_custom_coolant_explicit_immutable_configuration():
    with FakeChiller(
        setpoint_range=(-5, 30),
        coolant="test mixture",
        coolant_source="test only; no physical suitability claim",
    ) as chiller:
        chiller.apply_setpoint(-2)
        assert chiller.read_setpoint() == -2
        with pytest.raises(ValueError):
            chiller.apply_setpoint(-6)
        for name in ("setpoint_range", "coolant", "coolant_source"):
            with pytest.raises(AttributeError):
                setattr(chiller, name, "bypass")


@pytest.mark.parametrize("value", [True, "20", None, math.nan, math.inf])
@pytest.mark.parametrize("operation", ["temperature", "setpoint"])
def test_bad_backend_reading_suspends_without_retry(value, operation, monkeypatch):
    backend = FakeChiller(reconnect_attempts=2)
    monkeypatch.setattr(backend, f"_read_{operation}", lambda: value)
    with backend as chiller:
        with pytest.raises(ProtocolError):
            getattr(chiller, f"read_{operation}")()
        assert not chiller.is_connected
        assert "suspended" in chiller.collect_status()["detail"]
        with pytest.raises(ConnectionError):
            chiller.read_temperature()


def test_outside_write_limits_reading_is_visible(monkeypatch):
    backend = FakeChiller()
    monkeypatch.setattr(backend, "_read_temperature", lambda: -3)
    monkeypatch.setattr(backend, "_read_setpoint", lambda: 45)
    with backend as chiller:
        assert chiller.read_temperature() == -3
        assert chiller.read_setpoint() == 45


def test_multiple_instruments_and_serialized_reads_writes():
    class SlowSimulator(FakeChiller):
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
    with backend as chiller, FakeChiller() as other:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda n: chiller.apply_setpoint(18) if n % 2 else chiller.read_temperature(),
                    range(50),
                )
            )
        assert backend.maximum == 1
        assert chiller.read_setpoint() == 18
        assert other.read_setpoint() == 20


def test_simulator_thermal_response_and_disconnected_pause():
    now = [0.0]
    with FakeChiller(clock=lambda: now[0], time_constant_s=10) as chiller:
        chiller.apply_setpoint(10)
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
        FakeChiller(**config)


def test_cancelled_queued_setpoint_does_not_cross_reconnect(monkeypatch):
    from threading import current_thread

    entered, release, queued, cancelled = Event(), Event(), Event(), Event()
    backend = FakeChiller()
    original_read = backend._read_temperature

    def blocking_read():
        entered.set()
        assert release.wait(3)
        return original_read()

    monkeypatch.setattr(backend, "_read_temperature", blocking_read)
    chiller = backend
    chiller.connect()
    lock, intent = chiller._io_lock, chiller._replace_connection_request

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            return lock.acquire(*args, **kwargs)

        def release(self):
            lock.release()

        def __enter__(self):
            if current_thread().name.startswith("writer"):
                queued.set()
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

    def observe_intent(connected, **kwargs):
        token = intent(connected, **kwargs)
        if not connected:
            cancelled.set()
        return token

    monkeypatch.setattr(chiller, "_io_lock", ObservedLock())
    monkeypatch.setattr(chiller, "_replace_connection_request", observe_intent)
    with (
        ThreadPoolExecutor(2) as pool,
        ThreadPoolExecutor(1, thread_name_prefix="writer") as writer,
    ):
        reading = pool.submit(chiller.read_temperature)
        assert entered.wait(1)
        writing = writer.submit(chiller.apply_setpoint, 15)
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


@pytest.mark.parametrize("method", ["disconnect", "stop_monitoring"])
@pytest.mark.parametrize("timeout", [0, -1, True, math.nan, math.inf])
def test_invalid_shutdown_timeout_preserves_connection(method, timeout):
    with FakeChiller() as chiller:
        with pytest.raises(ValueError):
            getattr(chiller, method)(timeout)
        assert chiller.read_temperature() == 20


@pytest.mark.parametrize("lock_name", ["_intent_lock", "_monitor_lock", "_io_lock"])
def test_disconnect_timeout_includes_each_lock_and_allows_retry(lock_name):
    chiller = FakeChiller()
    chiller.connect()
    lock = getattr(chiller, lock_name)
    with ThreadPoolExecutor(1) as pool:
        lock.acquire()
        try:
            pending = pool.submit(chiller.disconnect, 0.02)
            with pytest.raises(TimeoutError, match="resources remain owned"):
                pending.result(timeout=0.5)
        finally:
            lock.release()
            chiller.disconnect()
    assert not chiller.is_connected


@pytest.mark.parametrize(
    "status",
    [
        None,
        [],
        {},
        {"connected": 1, "backend": "simulator", "detail": "test"},
        {"connected": True, "backend": "", "detail": "test"},
        {"connected": True, "backend": "simulator", "detail": None},
    ],
)
def test_invalid_status_dictionary_is_a_protocol_error(status, monkeypatch):
    backend = FakeChiller()
    monkeypatch.setattr(backend, "_collect_status", lambda: status)
    with backend as chiller:
        with pytest.raises(ProtocolError, match="invalid status"):
            chiller.collect_status()
        assert not chiller.is_connected


def test_status_dictionary_is_detached_from_backend(monkeypatch):
    backend = FakeChiller()
    status = {"connected": True, "backend": "simulator", "detail": "test"}
    monkeypatch.setattr(backend, "_collect_status", lambda: status)
    with backend as chiller:
        returned = chiller.collect_status()
        assert type(returned) is dict
        returned["detail"] = "caller edit"
        assert chiller.collect_status() == status
        assert status["detail"] == "test"
