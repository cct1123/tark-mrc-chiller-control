"""TEST-002 / TEST-003: common API, policy guard and deterministic simulator."""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from tark_chiller import (
    DISTILLED_WATER,
    Chiller,
    ChillerConnectionError,
    CoolantProfile,
    DeviceStatus,
    ProtocolError,
    SetpointValidationError,
    SimulatedDevice,
    TransportError,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SpyDevice:
    def __init__(self):
        self.connected = False
        self.operations = []
        self.temperature = 20.0
        self.setpoint = 20.0
        self.write_error = None

    def connect(self):
        self.operations.append("connect")
        self.connected = True

    def disconnect(self):
        self.operations.append("disconnect")
        self.connected = False

    @property
    def is_connected(self):
        self.operations.append("is_connected")
        return self.connected

    def get_temperature(self):
        return self.temperature

    def get_setpoint(self):
        return self.setpoint

    def set_setpoint(self, value_c):
        self.operations.append(("write", value_c))
        if self.write_error:
            raise self.write_error
        self.setpoint = value_c

    def get_status(self):
        return DeviceStatus(self.connected, "spy")


@pytest.mark.parametrize("backend_factory", [SimulatedDevice, SpyDevice])
def test_common_api_and_explicit_connection(backend_factory):
    backend = backend_factory()
    chiller = Chiller(backend)
    assert not chiller.is_connected
    assert not chiller.get_status().connected
    for operation in (
        chiller.get_temperature,
        chiller.get_setpoint,
        lambda: chiller.set_setpoint(25.0),
    ):
        with pytest.raises(ChillerConnectionError):
            operation()
    chiller.connect()
    assert chiller.is_connected
    assert chiller.get_status().connected
    assert chiller.get_temperature() == pytest.approx(20.0)
    assert chiller.get_setpoint() == 20.0
    chiller.set_setpoint(25.0)
    assert chiller.get_setpoint() == 25.0
    chiller.disconnect()
    chiller.disconnect()
    assert not chiller.is_connected


@pytest.mark.parametrize(
    "invalid", [True, False, "20", None, math.nan, math.inf, -math.inf, 1.99, 40.01, -12, 10**400]
)
def test_invalid_setpoint_never_touches_backend(invalid):
    backend = SpyDevice()
    chiller = Chiller(backend)
    with pytest.raises(SetpointValidationError):
        chiller.set_setpoint(invalid)
    assert backend.operations == []


@pytest.mark.parametrize("boundary", [2, 40])
def test_water_bounds_are_inclusive(boundary):
    backend = SpyDevice()
    chiller = Chiller(backend)
    chiller.connect()
    chiller.set_setpoint(boundary)
    assert backend.setpoint == float(boundary)
    assert isinstance(backend.setpoint, float)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": " "},
        {"name": None},
        {"source": ""},
        {"source": None},
        {"minimum_c": math.nan},
        {"maximum_c": math.inf},
        {"minimum_c": True},
        {"maximum_c": "40"},
        {"minimum_c": 41},
        {"minimum_c": 40},
    ],
)
def test_invalid_coolant_profile(changes):
    fields = dict(name="test profile", minimum_c=2, maximum_c=40, source="test-only policy")
    fields.update(changes)
    with pytest.raises(SetpointValidationError):
        CoolantProfile(**fields)


def test_explicit_profile_provenance_and_matching_backend():
    profile = CoolantProfile("test coolant", -5, 30, "test fixture; no physical suitability claim")
    chiller = Chiller(SimulatedDevice(coolant=profile), coolant=profile)
    chiller.connect()
    chiller.set_setpoint(-2)
    assert chiller.get_setpoint() == -2.0
    assert chiller.coolant.source == profile.source
    with pytest.raises(FrozenInstanceError):
        profile.minimum_c = -20
    with pytest.raises(AttributeError):
        chiller.coolant = DISTILLED_WATER


def test_profile_mismatch_cannot_bypass_backend_guard():
    profile = CoolantProfile("test coolant", -5, 40, "test fixture only")
    chiller = Chiller(SimulatedDevice(), coolant=profile)
    chiller.connect()
    with pytest.raises(SetpointValidationError):
        chiller.set_setpoint(-2)
    assert chiller.get_setpoint() == 20.0


@pytest.mark.parametrize("invalid", [True, "20", None, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("reading", ["temperature", "setpoint"])
def test_invalid_numeric_backend_readings_are_errors(invalid, reading):
    backend = SpyDevice()
    chiller = Chiller(backend)
    chiller.connect()
    setattr(backend, reading, invalid)
    operation = chiller.get_temperature if reading == "temperature" else chiller.get_setpoint
    with pytest.raises(ProtocolError):
        operation()


def test_outside_policy_reading_remains_visible():
    backend = SpyDevice()
    backend.temperature = -3.0
    backend.setpoint = 45.0
    chiller = Chiller(backend)
    chiller.connect()
    assert chiller.get_temperature() == -3.0
    assert chiller.get_setpoint() == 45.0


def test_failed_write_is_not_retried_or_replayed_on_reconnect():
    backend = SpyDevice()
    backend.write_error = TransportError("uncertain write")
    chiller = Chiller(backend)
    chiller.connect()
    with pytest.raises(TransportError, match="uncertain write"):
        chiller.set_setpoint(25)
    chiller.disconnect()
    chiller.connect()
    assert [item for item in backend.operations if isinstance(item, tuple)] == [("write", 25.0)]


def test_concurrent_application_reads_and_writes_are_serialized():
    class ConcurrentDevice(SpyDevice):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.maximum_active = 0
            self.activity_lock = threading.Lock()

        def exchange(self):
            with self.activity_lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            # Represents a slow backend operation which releases the GIL.
            try:
                time.sleep(0.002)
            finally:
                with self.activity_lock:
                    self.active -= 1

        def get_temperature(self):
            self.exchange()
            return self.temperature

        def set_setpoint(self, value_c):
            self.exchange()
            super().set_setpoint(value_c)

    backend = ConcurrentDevice()
    chiller = Chiller(backend)
    chiller.connect()

    def operation(index):
        if index % 2:
            chiller.set_setpoint(25)
        else:
            assert chiller.get_temperature() == 20

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(operation, range(40)))
    assert backend.maximum_active == 1
    assert backend.get_setpoint() == 25


def test_simulator_deterministic_thermal_progression_and_pause():
    clock = Clock()
    simulator = SimulatedDevice(clock=clock, time_constant_s=10)
    chiller = Chiller(simulator)
    chiller.connect()
    chiller.set_setpoint(30)
    clock.now = 10
    expected = 30 - 10 * math.exp(-1)
    assert chiller.get_temperature() == pytest.approx(expected)
    # An idempotent connect must not discard elapsed thermal time.
    clock.now = 20
    chiller.connect()
    expected = 30 - 10 * math.exp(-2)
    assert chiller.get_temperature() == pytest.approx(expected)
    chiller.disconnect()
    clock.now = 100
    chiller.connect()
    assert chiller.get_temperature() == pytest.approx(expected)
    assert chiller.get_setpoint() == 30
    chiller.set_setpoint(10)
    clock.now = 110
    assert chiller.get_temperature() == pytest.approx(10 + (expected - 10) * math.exp(-1))


@pytest.mark.parametrize("invalid", [0, -1, math.nan, math.inf, True, "30"])
def test_simulator_rejects_invalid_time_constant(invalid):
    with pytest.raises(SetpointValidationError):
        SimulatedDevice(time_constant_s=invalid)


def test_backend_itself_guards_setpoints():
    simulator = SimulatedDevice()
    simulator.connect()
    with pytest.raises(SetpointValidationError):
        simulator.set_setpoint(-1)
    assert simulator.get_setpoint() == 20
