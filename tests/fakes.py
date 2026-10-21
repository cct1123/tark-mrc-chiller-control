"""Fault injection around the actual memory-only CAL protocol simulator."""

from collections import deque
from threading import Event
from time import sleep

from tark_chiller.controller import Cal33xx, TarkChiller
from tark_chiller.simulator import CalSimulator, _ThermalModel


class FakeSerial(CalSimulator):
    def __init__(self):
        super().__init__()
        self.registers[0x007F] = 200
        self._model._setpoint = 20
        self.rs485_mode = None
        self.open_count = self.close_count = self.write_count = self.read_count = 0
        self.response = None
        self.delay_s = 0.0
        self.open_error = self.read_error = self.write_error = self.close_error = None
        self.write_result = None
        self.writes = deque(maxlen=128)
        self.factory_arguments = []
        self.read_pending = Event()

    def factory(self, **settings):
        assert settings["port"] is None
        self.factory_arguments.append(settings)
        return super().factory(**settings)

    def open(self):
        self.open_count += 1
        if self.open_error:
            raise self.open_error
        super().open()

    def close(self):
        self.close_count += 1
        if self.close_error:
            raise self.close_error
        super().close()

    def write(self, request):
        self.write_count += 1
        self.writes.append(request)
        if self.write_error:
            raise self.write_error
        written = super().write(request)
        if self.response is not None:
            self._buffer = self.response
        return written if self.write_result is None else self.write_result

    def read(self, size=1):
        self.read_count += 1
        self.read_pending.set()
        if self.read_error:
            raise self.read_error
        if self.delay_s:
            delay, self.delay_s = self.delay_s, 0
            sleep(delay)
        return super().read(size)


def fake_settings():
    return {
        "port": "FAKE-ONLY",
        "baudrate": 19200,
        "bytesize": 8,
        "parity": "E",
        "stopbits": 1,
        "xonxoff": False,
        "rtscts": False,
        "dsrdtr": False,
        "rts": False,
        "dtr": False,
    }


def make_serial(*, endpoint=None, rs485=False, **kwargs):
    endpoint = endpoint if endpoint is not None else FakeSerial()
    device = TarkChiller(
        fake_settings(),
        rs485={
            "rts_level_for_tx": True,
            "rts_level_for_rx": False,
            "loopback": False,
            "delay_before_tx": None,
            "delay_before_rx": None,
        }
        if rs485
        else None,
        protocol=Cal33xx(1, True, 1, 2),
        serial_factory=endpoint.factory,
        **kwargs,
    )
    return device, endpoint


class FakeChiller(TarkChiller):
    """Unit-test I/O hooks; protocol integration uses the memory serial endpoints."""

    def __init__(self, *, clock=None, time_constant_s=30.0, **options):
        super().__init__(fake_settings(), protocol=Cal33xx(1), **options)
        self._model = _ThermalModel(
            time_constant_s=time_constant_s, **({"clock": clock} if clock else {})
        )

    @property
    def _port_connected(self):
        return self._model.is_connected

    def _open_port(self):
        self._model.connect()

    def _close_port(self):
        self._model.disconnect()

    def _read_temperature(self):
        return self._model._read_temperature()

    def _read_setpoint(self):
        return self._model._read_setpoint()

    def _write_setpoint(self, value):
        self._model._write_setpoint(value)

    def _read_status(self):
        return self._model._read_status()

    def _read_sample(self):
        return self._read_status(), self._read_temperature(), self._read_setpoint()
