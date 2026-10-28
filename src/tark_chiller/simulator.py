"""Memory-only thermal and byte-protocol simulators, with an optional dashboard.

Run with python -m tark_chiller.simulator. No simulator opens a hardware port.
"""

import argparse
import math
import signal
import time
from collections.abc import Callable
from threading import current_thread, main_thread
from time import monotonic, sleep
from types import FrameType
from typing import Any

from .controller import TarkChiller, _seconds
from .protocols import T255P, Cal33xx, ProtocolError, _finite, _rtu, _t255p_frame, _t255p_payload


class _ThermalModel:
    """Internal thermal dynamics for the memory-only serial simulators.

    This uncalibrated model starts at 20 Celsius. It has no worker, GUI dependency
    or physical coolant/flow model. An injected clock makes tests repeatable.
    """

    def __init__(
        self,
        *,
        time_constant_s: float = 30.0,
        clock: Callable[[], float] = monotonic,
        initial_setpoint_c: float = 20.0,
    ) -> None:
        self._tau = _finite(time_constant_s, "time_constant_s")
        if self._tau <= 0:
            raise ValueError("time_constant_s must be positive")
        self._clock = clock
        self._connected = False
        self._updated = 0.0
        self._temperature = 20.0
        self._setpoint = _finite(initial_setpoint_c, "initial_setpoint_c")

    def connect(self) -> None:
        if not self._connected:
            self._updated = self._clock()
            self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self._advance()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _advance(self) -> None:
        if not self._connected:
            raise ConnectionError("Simulator is disconnected")
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._temperature = self._setpoint + (self._temperature - self._setpoint) * math.exp(
            -elapsed / self._tau
        )
        self._updated = now

    def _read_temperature(self) -> float:
        self._advance()
        return self._temperature

    def _read_setpoint(self) -> float:
        if not self._connected:
            raise ConnectionError("Simulator is disconnected")
        return self._setpoint

    def _write_setpoint(self, value: float) -> None:
        self._advance()
        self._setpoint = value


class CalSimulator:
    """Memory-only CAL serial endpoint, using the same frames as the driver.

    Factory MRC target is 10 C; initial liquid is a synthetic 20 C. Staging and
    keypad locking follow CAL section 2.3. Temperature dynamics are uncalibrated.
    Closing the serial connection does not stop the simulated controller.
    registers/coils expose selected device state for deliberate fault tests.
    """

    _simulated = True

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._model = _ThermalModel(clock=clock, initial_setpoint_c=10.0)
        self.registers = {
            0x04FC: 1,
            0x04FD: 2,
            0x0198: 10,
            0x0199: 1,
            0x0096: 0,
            0x0094: 400,
            0x0125: 2,
            0x0306: 0,
            0x0305: 0,
            0x007F: 100,
        }
        self.coils = {0x0028: 0, 0x002A: 1}
        self.is_open = False
        self.timeout = self.write_timeout = 1.0
        self.port: str | None = None
        self.rts = self.dtr = False
        self.remote_program_mode = False
        self.front_panel_busy = False
        self.applied_writes = 0
        self._security = 0
        self._buffer = b""

    def chiller(self, *, allow_writes: bool = True, **options: Any) -> TarkChiller:
        """A disconnected controller using this memory-only serial endpoint."""
        settings = {
            "port": "MEMORY-ONLY",
            "baudrate": 9600,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "xonxoff": False,
            "rtscts": False,
            "dsrdtr": False,
            "rts": False,
            "dtr": False,
        }
        return TarkChiller(
            settings,
            protocol=Cal33xx(1, allow_writes, 1, 2),
            serial_factory=self.factory,
            **options,
        )

    def factory(self, **settings: Any) -> "CalSimulator":
        if self.is_open:
            raise OSError("Simulated endpoint already open")
        self.timeout, self.write_timeout = settings["timeout"], settings["write_timeout"]
        return self

    def open(self) -> None:
        self.is_open = True
        self._model.connect()

    def close(self) -> None:
        self.is_open = False
        self._buffer = b""

    def reset_input_buffer(self) -> None:
        self._buffer = b""

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def write(self, request: bytes) -> int:
        if not self.is_open:
            raise OSError("Simulated endpoint closed")
        self._buffer = self._respond(request)
        return len(request)

    def read(self, size: int = 1) -> bytes:
        if not self._buffer:
            sleep(self.timeout)
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result

    def _respond(self, request: bytes) -> bytes:
        if len(request) != 8 or _rtu(request[:-2]) != request or request[0] != 1:
            return b""
        function = request[1]
        address, value = int.from_bytes(request[2:4], "big"), int.from_bytes(request[4:6], "big")
        security, self._security = self._security, 0  # Consumed by the very next message.

        def error(code: int) -> bytes:
            return _rtu(bytes((1, function | 0x80, code)))

        if function in (1, 3):
            if value != 1:
                return error(1)
            if function == 1:
                if address not in self.coils:
                    return error(2)
                return _rtu(bytes((1, 1, 1, self.coils[address])))
            if address == 0x001C:
                raw = round(self._model._read_temperature() * 10)
            elif address in self.registers:
                raw = self.registers[address]
            else:
                return error(2)
            return _rtu(b"\x01\x03\x02" + (raw & 0xFFFF).to_bytes(2, "big"))
        if function != 6:
            return b""  # Older CAL firmware does not reliably reject unknown functions.
        if address == 0x0300:
            self._security = value
        elif address == 0x1500:
            if security != 5:
                return b""
            if self.front_panel_busy:
                return error(6)
            self.remote_program_mode = True
        elif address == 0x007F:
            self.registers[address] = value  # Staged only; firmware does not validate limits.
        elif address == 0x1600:
            if security != 6:
                return b""
            if not self.remote_program_mode:
                return error(1)
            self._model._write_setpoint(self.registers[0x007F] / 10)
            self.remote_program_mode = False
            self.applied_writes += 1
        else:
            return error(2)  # Deliberately implement only the supported subset.
        return request


class T255PSimulator:
    """Memory-only Rev C endpoint, not a calibrated thermal/electrical model.

    State is evaluated lazily against an injected clock. Target retention, serial
    watchdog and control continue after port close. Exposed mode/alarms/flow pause
    are deliberate test controls; they never contact hardware. G models explicit
    RUN/STANDBY requests from the production driver and standalone example.
    """

    _simulated = True

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._updated = clock()
        self._control_s = 0.0
        self._model = _ThermalModel(clock=lambda: self._control_s)
        self._model.connect()
        self.mode = "0"  # AUTO START; the manual's default retained target is 20 C.
        self.alarms = "000000"  # float, high, low, sensor, EEPROM, watchdog
        self.dryer_on = False  # Synthetic choice, not an installed-unit assertion.
        self.max_power = 100  # Synthetic read-only field, not a power calibration.
        self.sensor_mode = "INTERNAL"  # Selection only; no external thermal model.
        self.applied_writes = 0
        self._remote = False
        self._last_valid = self._updated
        self._next_xon = self._updated
        self.is_open = False
        self.timeout = self.write_timeout = 3.0
        self.port: str | None = None
        self.rts = self.dtr = False
        self.xonxoff = True
        self.flow_paused = False
        self._buffer = b""

    def chiller(self, *, allow_writes: bool = True, **options: Any) -> TarkChiller:
        """Build a disconnected controller for this memory endpoint; no hardware I/O.

        Writes default to enabled for simulation exercises. Pass controller options
        such as timeout_s directly; the injected clock affects modeled device state,
        while serial timeout waits still use real time.
        """
        settings = {
            "port": "MEMORY-ONLY",
            "baudrate": 9600,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "xonxoff": True,
            "rtscts": False,
            "dsrdtr": False,
            "rts": False,
            "dtr": False,
        }
        profile: dict[str, Any] = {
            "setpoint_range": (5.0, 45.0),
            "coolant_source": "ThermoTek T255P Rev C pp9/20 (simulation)",
        }
        profile.update(options)
        return TarkChiller(
            settings, protocol=T255P(allow_writes), serial_factory=self.factory, **profile
        )

    def factory(self, **settings: Any) -> "T255PSimulator":
        if self.is_open:
            raise OSError("Simulated endpoint already open")
        self.timeout, self.write_timeout = settings["timeout"], settings["write_timeout"]
        self.xonxoff = settings["xonxoff"]
        return self

    def _sync(self) -> None:
        now = self._clock()
        if self.mode in ("0", "2"):
            self._control_s += max(0.0, now - self._updated)
        self._updated = now
        if self.alarms[3] == "1":
            self.mode = "1"  # Sensor failure enters standby (printed p8).
        if self._remote and now - self._last_valid > 10:
            self._remote = False
            self._next_xon = now
        if not self._remote and self.is_open and now >= self._next_xon:
            self._buffer += b"\x11"
            self._next_xon = now + 0.5

    @property
    def remote_active(self) -> bool:
        self._sync()
        return self._remote

    def power_cycle(self) -> None:
        """Synthetic reset retains target; there is no corresponding driver API."""
        self._sync()
        self.mode, self._remote = "0", False
        self._buffer = b""
        self._next_xon = self._clock()

    def open(self) -> None:
        self.is_open = True
        self._sync()

    def close(self) -> None:
        self._sync()
        self.is_open = False
        self._buffer = b""

    def reset_input_buffer(self) -> None:
        self._sync()
        self._buffer = b""

    def set_flow_paused(self, paused: bool) -> None:
        self.flow_paused = paused
        self._buffer += b"\x13" if paused else b"\x11"

    @property
    def in_waiting(self) -> int:
        self._sync()
        data = self._buffer.translate(None, b"\x11\x13") if self.xonxoff else self._buffer
        return len(data)

    def write(self, request: bytes) -> int:
        if not self.is_open:
            raise OSError("Simulated endpoint closed")
        self._sync()
        if self.xonxoff and self.flow_paused:
            sleep(self.write_timeout)
            raise TimeoutError("Simulated T255P XOFF write timeout")
        self._buffer += self._respond(request)
        return len(request)

    def read(self, size: int = 1) -> bytes:
        if not self.is_open:
            raise OSError("Simulated endpoint closed")
        self._sync()
        if self.xonxoff:
            self._buffer = self._buffer.translate(None, b"\x11\x13")
        if not self._buffer:
            sleep(self.timeout)
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result

    def _respond(self, request: bytes) -> bytes:
        command = request[1:2]

        def reply(data: bytes = b"", error: bytes = b"0") -> bytes:
            return _t255p_frame(b"#" + command + error + data)

        try:
            payload = _t255p_payload(request, b".")
        except ProtocolError:
            return reply(error=b"1")
        command, data = payload[:1], payload[1:]
        if command not in (b"U", b"G", b"H", b"I", b"J", b"M", b"O"):
            return reply(error=b"2")
        if not self._remote and command != b"U":
            return b""  # U is required to establish/re-establish a serial session.
        if command in (b"U", b"I", b"J") and data:
            return reply(error=b"3")
        if command == b"U":
            if self.mode == "0":
                self.mode = "2"
            self._remote = True
            response = (
                self.mode
                + str(int("1" in self.alarms))
                + str(int(self.mode in ("0", "2")))
                + str(int(self.dryer_on))
            ).encode("ascii")
        elif command == b"G":
            if data not in (b"0", b"1"):
                return reply(error=b"3")
            self.mode = "1" if data == b"0" else "2"
            response = data
        elif command == b"H":
            if data != b"0":
                return reply(error=b"3")
            response = (
                f"0{round(self._model._read_setpoint() * 10):+04d},{self.max_power:03d}"
            ).encode("ascii")
        elif command == b"I":
            response = f"{round(self._model._read_temperature() * 100):+05d}".encode("ascii")
        elif command == b"J":
            response = self.alarms.encode("ascii")
        elif command == b"O":
            if data not in (b"0", b"1"):
                return reply(error=b"3")
            self.sensor_mode = "INTERNAL" if data == b"0" else "EXTERNAL"
            response = data
        else:
            try:
                target = T255P._signed(data, 3)
            except ProtocolError:
                return reply(error=b"3")
            if not -5 <= target <= 45:
                return reply(error=b"3")
            self._model._write_setpoint(target)
            self.applied_writes += 1
            response = data
        self._last_valid = self._clock()
        return reply(response)


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Monitor without a browser")
    models = parser.add_mutually_exclusive_group()
    models.add_argument(
        "--cal", action="store_true", help="Use the memory-only CAL byte-protocol simulator"
    )
    models.add_argument(
        "--t255p", action="store_true", help="Use the memory-only T255P byte-protocol simulator"
    )
    parser.add_argument("--duration", type=float, help="Headless seconds; omit to run until Ctrl+C")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--csv", help="New output CSV path; existing files are never overwritten")
    parser.add_argument("--history", type=int, default=3600, help="Maximum retained samples")
    args = parser.parse_args(argv)
    try:
        _seconds(args.interval, "interval")
        if args.duration is not None:
            _seconds(args.duration, "duration")
            if not args.headless:
                raise ValueError("--duration requires --headless")
        chiller = CalSimulator().chiller() if args.cal else T255PSimulator().chiller()
    except ValueError as exc:
        parser.error(str(exc))
    if not args.headless:
        try:
            from .gui import run_dashboard
        except ImportError as exc:
            parser.error(
                f"Dashboard unavailable: {exc}. Install the gui extra from the same source "
                "or wheel used to install tark-mrc-chiller."
            )
    previous_handlers = {}
    try:
        if current_thread() is main_thread():
            for name in ("SIGTERM", "SIGBREAK"):
                if (sig := getattr(signal, name, None)) is not None:
                    previous_handlers[sig] = signal.signal(sig, _interrupt)
        try:
            if args.headless:
                with chiller:
                    try:
                        chiller.start_monitoring(
                            interval_s=args.interval,
                            csv_path=args.csv,
                            history_size=args.history,
                        )
                        print("Simulator monitoring started. Press Ctrl+C to stop.", flush=True)
                        deadline = (
                            time.monotonic() + args.duration if args.duration is not None else None
                        )
                        while True:
                            snapshot = chiller.snapshot()
                            if (
                                not snapshot["running"]
                                or snapshot["logging_error"]
                                or snapshot["failed_samples"]
                            ):
                                break
                            if deadline is not None and time.monotonic() >= deadline:
                                break
                            time.sleep(0.05)
                    except KeyboardInterrupt:
                        pass
                    chiller.stop_monitoring()
            else:
                print(
                    "Open http://127.0.0.1:8056. Press Connect, then Start monitoring.", flush=True
                )
                run_dashboard(
                    chiller,
                    stale_after_s=max(3.0, args.interval * 3),
                    interval_s=args.interval,
                    csv_path=args.csv,
                    history_size=args.history,
                )
        except KeyboardInterrupt:
            pass
    except (OSError, ValueError) as exc:
        parser.error("\n".join([str(exc), *getattr(exc, "__notes__", [])]))
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    snapshot = chiller.snapshot()
    print(f"Stopped simulator; {len(snapshot['history'])} samples retained.")
    if snapshot["service_error"] or snapshot["logging_error"] or snapshot["failed_samples"]:
        raise SystemExit(
            snapshot["service_error"] or snapshot["logging_error"] or "A simulator poll failed"
        )


if __name__ == "__main__":
    main()
