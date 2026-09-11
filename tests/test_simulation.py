"""TEST-013: repeatable simulator and synthetic full serial stack, no physical I/O."""

import json
import math
from concurrent.futures import ThreadPoolExecutor

import pytest

from development.testing import FakeProtocol, FakeSerialEndpoint, make_fake_device
from tark_chiller import Chiller, CoolantProfile, ProtocolError, SetpointValidationError
from tark_chiller.errors import ChillerTimeoutError, TransportError
from tark_chiller.simulator import SimulatedDevice


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_measurements_hold_until_interval_while_thermal_state_advances():
    clock = Clock()
    simulator = SimulatedDevice(clock=clock, time_constant_s=10, measurement_interval_s=2)
    simulator.connect()
    simulator.set_setpoint(10)
    assert simulator.get_temperature() == 20
    clock.now = 1
    assert simulator.get_temperature() == 20
    clock.now = 2
    measurement = 10 + 10 * math.exp(-0.2)
    assert simulator.get_temperature() == pytest.approx(measurement)
    clock.now = 3
    simulator.set_setpoint(30)
    assert simulator.get_temperature() == pytest.approx(measurement)
    clock.now = 4
    at_change = 10 + 10 * math.exp(-0.3)
    assert simulator.get_temperature() == pytest.approx(30 + (at_change - 30) * math.exp(-0.1))


def test_seeded_noise_repeats_without_feeding_back_into_thermal_state():
    clock = Clock()
    first = SimulatedDevice(clock=clock, noise_std_c=0.02, seed=43, measurement_interval_s=1)
    second = SimulatedDevice(clock=clock, noise_std_c=0.02, seed=43, measurement_interval_s=1)
    third = SimulatedDevice(clock=clock, noise_std_c=0.02, seed=44, measurement_interval_s=1)
    devices = (first, second, third)
    for device in devices:
        device.connect()
        device.set_setpoint(10)
    sequences = [[], [], []]
    for index in range(20):
        clock.now = index
        for sequence, device in zip(sequences, devices, strict=True):
            sequence.append(device.get_temperature())
            assert device.get_temperature() == sequence[-1]
    assert sequences[0] == sequences[1]
    assert sequences[0] != sequences[2]
    residuals = [value - (10 + 10 * math.exp(-i / 30)) for i, value in enumerate(sequences[0])]
    assert all(abs(residual) < 0.1 for residual in residuals)


@pytest.mark.parametrize("field", ["measurement_interval_s", "noise_std_c"])
@pytest.mark.parametrize("value", [-1, math.nan, math.inf, True, "1", 10**400])
def test_invalid_simulation_options(field, value):
    with pytest.raises(SetpointValidationError):
        SimulatedDevice(**{field: value})


@pytest.mark.parametrize("seed", [True, 1.2, "seed"])
def test_invalid_seed(seed):
    with pytest.raises(SetpointValidationError):
        SimulatedDevice(seed=seed)


@pytest.mark.parametrize(
    "operation", ["connect", "get_temperature", "get_setpoint", "get_status", "set_setpoint"]
)
@pytest.mark.parametrize("fault", ["timeout", "disconnect"])
def test_scripted_simulator_faults_exhaust_and_reconnect_preserves_target(operation, fault):
    simulator = SimulatedDevice(clock=Clock())
    simulator.connect()
    simulator.set_setpoint(15)
    if operation == "connect":
        simulator.disconnect()
    simulator.inject_fault(operation, fault, count=2)
    expected = ChillerTimeoutError if fault == "timeout" else TransportError
    for _ in range(2):
        if operation != "connect":
            simulator.connect()
        with pytest.raises(expected):
            getattr(simulator, operation)(25) if operation == "set_setpoint" else getattr(
                simulator, operation
            )()
        assert not simulator.is_connected
    simulator.connect()
    assert simulator.get_setpoint() == 15
    assert simulator.get_temperature() == 20


@pytest.mark.parametrize("invalid", [True, "20", math.nan, math.inf, -1, 40.1])
def test_invalid_write_does_not_consume_simulated_fault_or_operation(invalid):
    simulator = SimulatedDevice()
    simulator.connect()
    simulator.inject_fault("set_setpoint", "timeout")
    before = simulator.operation_counts
    with pytest.raises(SetpointValidationError):
        simulator.set_setpoint(invalid)
    assert simulator.operation_counts == before
    with pytest.raises(ChillerTimeoutError):
        simulator.set_setpoint(20)


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
def test_fake_stack_common_api_never_constructs_os_serial(monkeypatch, interface):
    def forbidden(**settings):
        pytest.fail("A hardware-free test attempted the OS serial factory")

    monkeypatch.setattr("tark_chiller.transport._serial_factory", forbidden)
    monkeypatch.setattr("tark_chiller.transport._rs485_mode_factory", forbidden)
    clock = Clock()
    device, endpoint = make_fake_device(simulator=SimulatedDevice(clock=clock), interface=interface)
    chiller = Chiller(device)
    chiller.connect()
    chiller.connect()
    assert endpoint.open_count == 1
    assert chiller.get_temperature() == 20
    chiller.set_setpoint(10)
    clock.now = 30
    assert chiller.get_temperature() == pytest.approx(10 + 10 * math.exp(-1))
    assert chiller.get_setpoint() == 10
    assert "SYNTHETIC TEST ONLY" in chiller.get_status().detail
    assert endpoint.applied_setpoints == 1
    assert endpoint.read_count > endpoint.write_count
    assert (endpoint.rs485_mode is not None) == (interface == "rs485")
    chiller.disconnect()
    chiller.disconnect()
    assert not endpoint.is_open
    assert not endpoint.simulator.is_connected


def test_fake_stack_uses_explicit_coolant_on_every_boundary():
    profile = CoolantProfile("test-only", -5, 30, "Synthetic fixture only")
    device, endpoint = make_fake_device(simulator=SimulatedDevice(coolant=profile))
    chiller = Chiller(device, coolant=profile)
    chiller.connect()
    chiller.set_setpoint(-2)
    assert chiller.get_setpoint() == -2
    before = endpoint.write_count
    with pytest.raises(SetpointValidationError):
        chiller.set_setpoint(-6)
    with pytest.raises(SetpointValidationError):
        device.set_setpoint(-6)
    assert endpoint.write_count == before
    chiller.disconnect()


@pytest.mark.parametrize(
    "fault", ["malformed", "wrong_id", "wrong_operation", "invalid_number", "oversized"]
)
def test_fake_responses_fail_closed_and_can_reconnect(fault):
    device, endpoint = make_fake_device(transaction_timeout_s=1)
    device.connect()
    endpoint.inject_fault("get_temperature", fault)
    with pytest.raises(ProtocolError):
        device.get_temperature()
    assert not device.is_connected
    assert endpoint.operation_counts["get_temperature"] == 1
    device.connect()
    assert device.get_temperature() == 20
    device.disconnect()


@pytest.mark.parametrize("fault", ["timeout", "truncated", "disconnect"])
def test_fake_communication_failure_is_typed_and_target_preserved(fault):
    device, endpoint = make_fake_device(transaction_timeout_s=0.02)
    device.connect()
    device.set_setpoint(15)
    endpoint.inject_fault("get_temperature", fault)
    expected = TransportError if fault == "disconnect" else ChillerTimeoutError
    with pytest.raises(expected):
        device.get_temperature()
    assert not device.is_connected
    device.connect()
    assert device.get_setpoint() == 15
    assert endpoint.applied_setpoints == 1
    device.disconnect()


def test_lost_write_acknowledgement_is_not_replayed_on_reconnect():
    device, endpoint = make_fake_device(transaction_timeout_s=0.02)
    chiller = Chiller(device)
    chiller.connect()
    endpoint.inject_fault("set_setpoint", "ack_lost")
    with pytest.raises(ChillerTimeoutError):
        chiller.set_setpoint(12)
    assert endpoint.applied_setpoints == 1
    chiller.connect()
    assert chiller.get_setpoint() == 12
    assert endpoint.operation_counts["set_setpoint"] == 1
    assert endpoint.applied_setpoints == 1
    chiller.disconnect()


def test_finite_connect_fault_script_and_bounded_trace():
    device, endpoint = make_fake_device()
    endpoint.inject_fault("connect", "disconnect", count=2)
    for _ in range(2):
        with pytest.raises(TransportError):
            device.connect()
    device.connect()
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert list(workers.map(lambda _: device.get_temperature(), range(180))) == [20] * 180
    assert len(endpoint.trace) == 128
    assert endpoint.operation_counts["get_temperature"] == 180
    assert endpoint.open_count == 3
    device.disconnect()


@pytest.mark.parametrize("result", [True, None, "20", math.nan, math.inf, [], {}])
def test_fake_codec_rejects_nonfinite_or_wrong_type_numeric_response(result):
    codec = FakeProtocol()
    request = json.loads(codec.encode("get_temperature"))
    request.pop("value")
    request["result"] = result
    with pytest.raises(ProtocolError):
        codec.decode("get_temperature", json.dumps(request).encode())


def test_fake_codec_requires_outstanding_request_and_valid_status():
    codec = FakeProtocol()
    request = json.loads(codec.encode("get_status"))
    request.pop("value")
    request["result"] = {"connected": "true", "detail": "test only"}
    with pytest.raises(ProtocolError, match="status"):
        codec.decode("get_status", json.dumps(request).encode())
    request["result"]["connected"] = True
    with pytest.raises(ProtocolError, match="outstanding"):
        codec.decode("get_status", json.dumps(request).encode())


@pytest.mark.parametrize("fixture", [SimulatedDevice, FakeSerialEndpoint])
@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_fault_script_requires_positive_integer_count(fixture, count):
    with pytest.raises(ValueError):
        fixture().inject_fault("get_temperature", "timeout", count=count)
