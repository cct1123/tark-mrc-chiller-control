"""Synthetic test bytes and memory endpoints. Nothing here describes Tark hardware."""

from collections import deque
from threading import Event
from time import sleep

from tark_chiller.controller import ProtocolError
from tark_chiller.serial import RS485Mode, SerialDevice, SerialSettings


class FakeCodec:
    def __init__(self):
        self.request_id = 0

    def encode(self, operation, value=None):
        self.request_id += 1
        return f"TEST|{self.request_id}|{operation}|{'' if value is None else value}\n".encode()

    def is_complete(self, response):
        return response.endswith(b"\n")

    def decode(self, operation, response):
        fields = response.decode().strip().split("|")
        if len(fields) != 4 or fields[:3] != ["TEST", str(self.request_id), operation]:
            raise ProtocolError("Unexpected synthetic response identity or operation")
        if operation == "get_status":
            if fields[3] != "connected":
                raise ProtocolError("Malformed synthetic status")
            return "Synthetic test only"
        if operation == "set_setpoint":
            if fields[3] != "ok":
                raise ProtocolError("Malformed synthetic acknowledgement")
            return None
        return float(fields[3])


class FakeSerial:
    def __init__(self, response=None):
        self.is_open = False
        self.timeout = self.write_timeout = 1.0
        self.rs485_mode = None
        self.open_count = self.close_count = self.write_count = self.read_count = 0
        self.applied_writes = 0
        self.setpoint = self.temperature = 20.0
        self.response = response
        self.delay_s = 0.0
        self.open_error = self.read_error = self.write_error = self.close_error = None
        self.write_result = None
        self.buffer = b""
        self.writes = deque(maxlen=128)
        self.factory_arguments = []
        self.read_pending = Event()

    def factory(self, **settings):
        assert settings["port"] is None
        if self.is_open:
            raise OSError("Synthetic endpoint already in use")
        self.factory_arguments.append(settings)
        self.timeout = settings["timeout"]
        self.write_timeout = settings["write_timeout"]
        return self

    def open(self):
        self.open_count += 1
        if self.open_error:
            raise self.open_error
        self.is_open = True

    def close(self):
        self.close_count += 1
        if self.close_error:
            raise self.close_error
        self.is_open = False
        self.buffer = b""

    def write(self, request):
        if not self.is_open:
            raise OSError("Synthetic endpoint closed")
        self.write_count += 1
        self.writes.append(request)
        if self.write_error:
            raise self.write_error
        tag, request_id, operation, value = request.decode().strip().split("|")
        assert tag == "TEST"
        if operation == "set_setpoint":
            self.setpoint = float(value)
            self.applied_writes += 1
            result = "ok"
        elif operation == "get_status":
            result = "connected"
        else:
            result = self.temperature if operation == "get_temperature" else self.setpoint
        self.buffer = (
            f"TEST|{request_id}|{operation}|{result}\n".encode()
            if self.response is None
            else self.response
        )
        return len(request) if self.write_result is None else self.write_result

    def read(self, size=1):
        self.read_count += 1
        self.read_pending.set()
        if self.read_error:
            raise self.read_error
        if self.delay_s:
            delay, self.delay_s = self.delay_s, 0
            sleep(delay)
        if not self.buffer:
            sleep(self.timeout)
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result


def fake_settings():
    return SerialSettings("FAKE-ONLY", 19200, 7, "E", 2, False, False, False, False, False)


def make_serial(*, endpoint=None, rs485=False, codec=None, **kwargs):
    endpoint = endpoint if endpoint is not None else FakeSerial()
    device = SerialDevice(
        fake_settings(),
        rs485=RS485Mode(True, False, False, None, None) if rs485 else None,
        codec=codec if codec is not None else FakeCodec(),
        serial_factory=endpoint.factory,
        **kwargs,
    )
    return device, endpoint
