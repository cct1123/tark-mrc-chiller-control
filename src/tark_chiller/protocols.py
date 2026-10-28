"""CAL and T255P wire formats, command validation and explicit workflows.

Protocols receive an I/O callable; they never open ports or start workers.
Structured device and read-attempt dictionaries are defined alongside them.
"""

import math
from collections.abc import Callable
from functools import cache
from numbers import Real
from typing import Literal, NotRequired, TypedDict

_Mode = Literal["AUTO START", "STANDBY", "RUN", "SAFETY DEFAULT"]
_SensorMode = Literal["INTERNAL", "EXTERNAL"]
_MODES: tuple[_Mode, ...] = ("AUTO START", "STANDBY", "RUN", "SAFETY DEFAULT")


class _Alarms(TypedDict):
    float_switch: bool
    high_temp: bool
    low_temp: bool
    sensor: bool
    eeprom: bool
    watchdog: bool


class _Memory(TypedDict):
    setpoint_c: float
    max_power: int


class _DeviceStatus(TypedDict):
    mode: _Mode
    mode_code: int
    summary_alarm: bool
    chiller_on: bool
    dryer_on: bool
    detail: str
    alarms: NotRequired[_Alarms]
    mode_command_sent: NotRequired[bool]


class _StatusFields(TypedDict, total=False):
    mode: _Mode
    mode_code: int
    summary_alarm: bool
    chiller_on: bool
    dryer_on: bool
    alarms: _Alarms
    mode_command_sent: bool


class _Status(_StatusFields):
    """Connection diagnostics as an ordinary dict; no physical safety guarantee."""

    connected: bool
    backend: str
    detail: str


class _AttemptReadings(TypedDict, total=False):
    """Validated fields from the current read attempt, including a failed attempt.

    The controller writes status before polling. T255P.sample() adds detail,
    temperature and target only after validating each field. Recovery replaces
    the entire dict before each attempt, including a reconnect handshake.
    _poll() may record these fields on failure, always with disconnected status
    and an error. They must never be reused as fresh readings or across retries.
    """

    status: _Status
    detail: str | _DeviceStatus
    temperature_c: float
    setpoint_c: float


class ProtocolError(RuntimeError):
    """Invalid protocol or device response; never accept its payload."""


class _T255PReadError(ProtocolError):
    """Discarded link response; a read may restart with a fresh handshake."""


class _T255PCommandMismatch(_T255PReadError):
    """Checksum-valid reply to another command; never use its payload."""


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _reading(value: object) -> float:
    try:
        return _finite(value, "Celsius reading")
    except ValueError as exc:
        raise ProtocolError(f"Invalid device reading: {value!r}") from exc


def _rtu(payload: bytes) -> bytes:
    """Append the CAL guide's CRC-16, low byte first (section 2.6)."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


def _rtu_complete(response: bytes) -> bool:
    if len(response) < 2:
        return False
    function = response[1]
    length = {1: 6, 3: 7, 6: 8}.get(function)
    if function & 0x80:
        length = 5
    if length is None:
        raise ProtocolError(f"Unexpected Modbus function 0x{function:02x}")
    return len(response) >= length


RegisterIO = Callable[[int, int, int], int]


AsciiIO = Callable[[bytes, bytes], bytes]


def _t255p_frame(payload: bytes) -> bytes:
    """Rev C section 16: printable payload, additive checksum and CR."""
    return payload + f"{sum(payload) & 0xFF:02X}\r".encode("ascii")


def _t255p_payload(frame: bytes, prefix: bytes) -> bytes:
    if (
        len(frame) < 5
        or not frame.startswith(prefix)
        or not frame.endswith(b"\r")
        or any(not 32 <= byte <= 126 for byte in frame[:-1])
        or any(byte not in b"0123456789abcdefABCDEF" for byte in frame[-3:-1])
    ):
        raise ProtocolError("Malformed T255P ASCII frame")
    if int(frame[-3:-1], 16) != sum(frame[:-3]) & 0xFF:
        raise ProtocolError("T255P checksum mismatch")
    return frame[1:-3]


class T255P:
    """ThermoTek Rev C water protocol; explicitly select for an identified unit.

    U establishes remote communication and can change AUTO START to RUN.
    Getters and the target/sensor setters perform one exchange. set_mode() owns
    a state-aware mode request; read/apply/collect workflows coordinate their
    documented exchanges. No hidden worker runs.
    No identity query exists in the documented subset: selection is human-owned.
    """

    def __init__(self, allow_writes: bool = False) -> None:
        self._allow_writes = allow_writes
        if type(self._allow_writes) is not bool:
            raise ValueError("allow_writes must be boolean")

    @staticmethod
    def is_complete(response: bytes) -> bool:
        """Check partial ASCII framing; reply() validates the completed checksum."""
        if response[:1] != b"#" or any(not 32 <= byte <= 126 for byte in response.rstrip(b"\r")):
            raise ProtocolError("Unexpected T255P response framing")
        return response.endswith(b"\r")

    @staticmethod
    def reply(command: bytes, response: bytes) -> bytes:
        """Validate a complete reply and return data after command/error fields."""
        payload = _t255p_payload(response, b"#")
        if len(payload) < 2:
            raise ProtocolError("Malformed T255P command/error fields")
        error = payload[1:2]
        if error != b"0":
            message = {b"1": "checksum", b"2": "bad command", b"3": "qualifier out of bounds"}
            error_type = _T255PReadError if error in (b"1", b"2") else ProtocolError
            raise error_type(
                f"T255P communication error: {message.get(error, 'unknown code')}; "
                f"expected={command!r}, echo={payload[:1]!r}, frame={response!r}"
            )
        if payload[:1] != command:
            raise _T255PCommandMismatch(
                f"T255P command echo mismatch: expected {command!r}, "
                f"received {payload[:1]!r}; frame={response!r}"
            )
        return payload[2:]

    # Command APIs: each query and target/sensor setter owns one exchange.

    def get_temperature(self, io: AsciiIO) -> float:
        """Send I only and return Celsius; sensor alarms require a separate J."""
        return self._signed(io(b"I", b""), 4)

    def get_memory(self, io: AsciiIO) -> _Memory:
        """Send H0 only; return setpoint_c and raw max_power."""
        data = io(b"H", b"0")
        if len(data) != 9 or data[:1] != b"0" or data[5:6] != b"," or not data[6:].isdigit():
            raise ProtocolError("Malformed T255P memory response")
        target = self._signed(data[1:5], 3)
        if not -5 <= target <= 45:
            raise ProtocolError("T255P reported target outside documented -5..45 C range")
        return {"setpoint_c": target, "max_power": int(data[6:])}

    def get_alarms(self, io: AsciiIO) -> _Alarms:
        """Send J only and return the six named boolean flags."""
        data = io(b"J", b"")
        if len(data) != 6 or any(c not in b"01" for c in data):
            raise ProtocolError("Malformed T255P alarm flags")
        flags = data.decode("ascii")
        return {
            "float_switch": flags[0] == "1",
            "high_temp": flags[1] == "1",
            "low_temp": flags[2] == "1",
            "sensor": flags[3] == "1",
            "eeprom": flags[4] == "1",
            "watchdog": flags[5] == "1",
        }

    def get_status(self, io: AsciiIO) -> _DeviceStatus:
        """Send U only; detailed alarm flags belong to J/get_alarms()."""
        data = io(b"U", b"")
        if len(data) != 4 or data[:1] not in b"0123" or any(c not in b"01" for c in data[1:]):
            raise ProtocolError("Malformed T255P watchdog state")
        mode_code = int(data[:1])
        mode = _MODES[mode_code]
        summary_alarm, chiller_on, dryer_on = (value == ord("1") for value in data[1:])
        return {
            "mode": mode,
            "mode_code": mode_code,
            "summary_alarm": summary_alarm,
            "chiller_on": chiller_on,
            "dryer_on": dryer_on,
            "detail": (
                f"T255P (human-selected; identity not queried); mode={mode}; "
                f"summary_alarm={int(summary_alarm)} chiller_on={int(chiller_on)} "
                f"dryer_on={int(dryer_on)}; flow/leak unknown; last serial reply, not safety proof"
            ),
        }

    def set_setpoint(self, value: float, io: AsciiIO) -> None:
        """Send M only and check its echoed target; no H readback."""
        number = self._validate_target(value)
        target = f"{round(number * 10):+04d}".encode("ascii")
        self._write(b"M", target, io)

    def set_sensor_mode(self, mode: _SensorMode, io: AsciiIO) -> None:
        """Send O once and verify its echo; the manual has no sensor-mode query."""
        if mode not in ("INTERNAL", "EXTERNAL"):
            raise ValueError("Sensor mode must be INTERNAL or EXTERNAL")
        self._write(b"O", b"0" if mode == "INTERNAL" else b"1", io)

    # State-aware mode request: U, optional G, then verified U readback.

    def set_mode(self, mode: Literal["RUN", "STANDBY"], io: AsciiIO) -> _DeviceStatus:
        """Check U; skip redundant G, otherwise send G and verify U readback."""
        if mode not in ("RUN", "STANDBY"):
            raise ValueError("Mode must be RUN or STANDBY")
        self._require_writes()
        state = self.get_status(io)
        running = mode == "RUN"
        if state["chiller_on"] == running and (
            state["mode"] == mode or (running and state["mode"] == "AUTO START")
        ):
            return {**state, "mode_command_sent": False}
        self._write(b"G", b"1" if running else b"0", io)
        state = self.get_status(io)
        if state["mode"] != mode or state["chiller_on"] != (mode == "RUN"):
            raise ProtocolError("T255P mode/chiller-on readback mismatch; outcome uncertain")
        return {**state, "mode_command_sent": True}

    # Checked workflows: explicit coordination and validation of several commands.

    def read_temperature(self, io: AsciiIO) -> float:
        """Checked U/J/I workflow; reject sensor alarms before reading I."""
        self.get_status(io)
        if self.get_alarms(io)["sensor"]:
            raise ProtocolError("T255P sensor alarm; temperature unavailable")
        return self.get_temperature(io)

    def read_setpoint(self, io: AsciiIO) -> float:
        """U/H0 workflow with explicit session renewal."""
        self.get_status(io)
        return self.get_memory(io)["setpoint_c"]

    def collect_status(self, io: AsciiIO) -> _DeviceStatus:
        """Collect U and J in one explicitly named workflow."""
        state = self.get_status(io)
        alarms = self.get_alarms(io)
        state["alarms"] = alarms
        flags = " ".join(f"{name}={int(bool(active))}" for name, active in alarms.items())
        state["detail"] += f"; reported alarms: {flags}"
        return state

    def sample(
        self, io: AsciiIO, partial: _AttemptReadings | None = None
    ) -> tuple[_DeviceStatus, float, float]:
        """Return (detail, temperature_c, setpoint_c) from one U/J/I/H poll.

        If partial is supplied, fill it after validating each field so the caller
        can record historical readings when a later command fails. Its lifetime
        is one attempt; the controller discards it before retrying the whole poll.
        """
        fields: _AttemptReadings = partial if partial is not None else {}
        status = self.collect_status(io)
        fields["detail"] = status
        if status["alarms"]["sensor"]:
            raise ProtocolError("T255P sensor alarm; temperature unavailable")
        fields["temperature_c"] = _reading(self.get_temperature(io))
        fields["setpoint_c"] = _reading(self.get_memory(io)["setpoint_c"])
        return status, fields["temperature_c"], fields["setpoint_c"]

    def apply_setpoint(self, value: float, io: AsciiIO) -> None:
        """U/M/H0 workflow with target readback verification."""
        # Validate before U, so invalid input cannot take serial control.
        self._validate_target(value)
        self._require_writes()
        self.get_status(io)
        self.set_setpoint(value, io)
        if self.get_memory(io)["setpoint_c"] != value:
            raise ProtocolError("T255P target readback mismatch; outcome uncertain")

    # Shared decoding and write validation.

    @staticmethod
    def _signed(data: bytes, digits: int) -> float:
        if len(data) != digits + 1 or data[:1] not in (b"+", b"-") or not data[1:].isdigit():
            raise ProtocolError("Malformed T255P signed temperature")
        return int(data) / (100 if digits == 4 else 10)

    def _require_writes(self) -> None:
        if not self._allow_writes:
            raise ProtocolError(
                "T255P writes disabled; enable with T255P(allow_writes=True) after checking setup"
            )

    def _write(self, command: bytes, data: bytes, io: AsciiIO) -> None:
        """Send one write command and check its acknowledgement echo."""
        self._require_writes()
        if io(command, data) != data:
            raise ProtocolError(f"T255P {command.decode()} echo mismatch; outcome uncertain")

    @staticmethod
    def _validate_target(value: float) -> float:
        number = _finite(value, "Setpoint")
        if not 5 <= number <= 45:
            raise ProtocolError("T255P water protocol supports targets from 5 to 45 C only")
        if abs(number * 10 - round(number * 10)) > 1e-8:
            raise ProtocolError("T255P targets require 0.1 C resolution; no rounding")
        return number


class Cal33xx:
    """Documented CAL 3300/9300 subset; no automatic configuration or reset.

    Reads verify supported family/firmware, RTD input and Celsius units.
    Writes need explicit opt-in and exact expected identity codes from a prior read.
    This driver accepts nonnegative temperatures only; negative wire encoding
    is not specified by the source. Each instance belongs to one TarkChiller.
    """

    def __init__(
        self,
        address: int,
        allow_writes: bool = False,
        expected_model: int | None = None,
        expected_firmware: int | None = None,
    ) -> None:
        self._address = address
        self._allow_writes = allow_writes
        self._expected_model = expected_model
        self._expected_firmware = expected_firmware
        if type(self._address) is not int or not 1 <= self._address <= 247:
            raise ValueError("CAL address must be 1..247; broadcasts are forbidden")
        if type(self._allow_writes) is not bool:
            raise ValueError("allow_writes must be boolean")
        for name, allowed in (("expected_model", (1, 2, 3)), ("expected_firmware", (0xFFFF, 1, 2))):
            value = getattr(self, "_" + name)
            if value is not None and (type(value) is not int or value not in allowed):
                raise ValueError(f"Unsupported CAL {name}")
        if self._allow_writes and (self._expected_model is None or self._expected_firmware is None):
            raise ValueError("Writes require expected_model and expected_firmware from read review")

    def verify(self, io: RegisterIO) -> str:
        """Require supported identity, reviewed codes (if supplied), and RTD/Celsius."""
        model, firmware = io(3, 0x04FC, 1), io(3, 0x04FD, 1)
        if model not in (1, 2, 3) or firmware not in (0xFFFF, 1, 2):
            raise ProtocolError(
                f"Unsupported CAL identity: model=0x{model:04x}, firmware=0x{firmware:04x}"
            )
        if (self._expected_model is not None and model != self._expected_model) or (
            self._expected_firmware is not None and firmware != self._expected_firmware
        ):
            raise ProtocolError("CAL identity differs from the reviewed controller")
        self._units(io)
        return f"CAL 3300/9300 model=0x{model:04x} firmware=0x{firmware:04x}"

    @staticmethod
    def _units(io: RegisterIO) -> None:
        if io(3, 0x0198, 1) != 10 or io(3, 0x0199, 1) != 1:
            raise ProtocolError(
                "This CAL driver requires Inpt=RTD and Unit=C; no settings were changed"
            )

    @staticmethod
    def _temperature(raw: int) -> float:
        if raw > 4000:
            raise ProtocolError(
                "Unsupported temperature encoding/range; negative values are not enabled"
            )
        return raw / 10

    def _write_checks(self, io: RegisterIO, value: float) -> int:
        self._units(io)
        low = self._temperature(io(3, 0x0096, 1))
        high = self._temperature(io(3, 0x0094, 1))
        locked, high_resolution = io(1, 0x0028, 1), io(1, 0x002A, 1)
        initialized = io(3, 0x0125, 1) & 2
        display, ramp = io(3, 0x0306, 1), io(3, 0x0305, 1)
        if locked or not initialized or display & 0x3F or ramp & 0x0E:
            raise ProtocolError(
                "CAL is locked, uninitialized, or displaying a special operating state"
            )
        if not 0 <= low < high or not low <= value <= high:
            raise ProtocolError(f"Target outside controller Lo.SC/Hi.SC [{low:g}, {high:g}] C")
        scaled = value * (10 if high_resolution else 1)
        if abs(scaled - round(scaled)) > 1e-8:
            raise ProtocolError(
                "Target must match DISP resolution (0.1 C high / 1 C low); no rounding"
            )
        return round(value * 10)

    def read_temperature(self, io: RegisterIO) -> float:
        """Require RTD/Celsius and no FAIL display before reading PV."""
        self._units(io)
        display = io(3, 0x0306, 1)
        if display & 0x20:  # CAL display bit 5 means FAIL; PV is unavailable.
            raise ProtocolError(
                f"Controller reports FAIL (display=0x{display:02x}); reading unavailable"
            )
        return self._temperature(io(3, 0x001C, 1))

    def read_setpoint(self, io: RegisterIO) -> float:
        """Require RTD/Celsius before interpreting the SP1 register."""
        self._units(io)
        return self._temperature(io(3, 0x007F, 1))

    def collect_status(self, io: RegisterIO) -> str:
        """Read identity, configuration and display state for diagnostics."""
        identity = self.verify(io)
        display, ramp = io(3, 0x0306, 1), io(3, 0x0305, 1)
        low, high = self._temperature(io(3, 0x0096, 1)), self._temperature(io(3, 0x0094, 1))
        locked, resolution = io(1, 0x0028, 1), io(1, 0x002A, 1)
        initialized = bool(io(3, 0x0125, 1) & 2)
        message = {
            0: "temperature display",
            1: "PARK",
            2: "ALARM",
            3: "TUNE",
            4: "TUNE ATSP",
            5: "HAND",
            0x23: "TUNE FAIL",
            0x25: "HAND FAIL",
            0x26: "INPT FAIL",
            0x27: "DATA FAIL",
        }.get(display & 0x3F, "unknown display state")
        return (
            f"{identity}; RTD/C; Lo.SC={low:g} Hi.SC={high:g}; SP.LK={locked} "
            f"DISP={'high' if resolution else 'low'} initialized={initialized}; "
            f"{message}; display=0x{display:02x} ramp=0x{ramp:02x}; "
            "flow/level/leak unknown"
        )

    def sample(self, io: RegisterIO) -> tuple[str, float, float]:
        """Read status, temperature and target, sharing checks within this poll."""
        read = cache(io)
        return self.collect_status(read), self.read_temperature(read), self.read_setpoint(read)

    def apply_setpoint(self, value: float, io: RegisterIO) -> None:
        """Check prerequisites, stage/save SP1 once and verify its readback."""
        if not self._allow_writes:
            raise ProtocolError(
                "CAL writes disabled; complete read-only review before enabling allow_writes"
            )
        target = _finite(value, "Setpoint")
        if not 0 <= target <= 40:
            raise ProtocolError("This CAL driver supports targets from 0 to 40 C only")
        self.verify(io)
        self._write_checks(io, target)
        # No retries or finally-unlock: exiting can commit an uncertain staged value.
        try:
            io(6, 0x0300, 5)
            io(6, 0x1500, 0)
            raw = self._write_checks(io, target)  # Recheck after front-panel lock.
            io(6, 0x007F, raw)
            io(6, 0x0300, 6)
            io(6, 0x1600, 0)  # Saves settings and initiates controller restart.
            self._units(io)
            if self._temperature(io(3, 0x007F, 1)) != target:
                raise ProtocolError("CAL setpoint readback differs from request")
        except BaseException as exc:
            exc.add_note(
                "CAL write interrupted: keypad/state may be uncertain. No automatic exit or retry."
            )
            raise
