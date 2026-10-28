"""Structured equipment display with fresh, missing and historical telemetry."""

from copy import deepcopy
from datetime import UTC, datetime
from json import dumps
from time import monotonic, sleep

import pytest
from plotly.utils import PlotlyJSONEncoder

from tark_chiller._views import render_status
from tark_chiller.simulator import CalSimulator, T255PSimulator


def find(component, identifier):
    if getattr(component, "id", None) == identifier:
        return component
    children = (
        component if isinstance(component, (list, tuple)) else getattr(component, "children", [])
    )
    for child in children if isinstance(children, (list, tuple)) else [children]:
        result = find(child, identifier)
        if result is not None:
            return result
    return None


def text(component):
    return dumps(component, cls=PlotlyJSONEncoder)


@pytest.fixture(params=["t255p", "cal"])
def sample(request):
    endpoint = T255PSimulator() if request.param == "t255p" else CalSimulator()
    if request.param == "t255p":
        endpoint.alarms = "010001"
    with endpoint.chiller() as c:
        c.start_monitoring(interval_s=0.01)
        deadline = monotonic() + 2
        while not c.snapshot()["sample_count"]:
            assert monotonic() < deadline
            sleep(0.005)
        c.stop_monitoring()
        snapshot = c.snapshot()
    # Inspect a just-completed poll without leaving a worker active during assertions.
    snapshot["running"] = True
    return request.param, snapshot


def test_equipment_fields_use_structured_data_and_never_parse_detail(sample):
    protocol, snapshot = sample
    snapshot["history"][-1]["status"]["detail"] = "mode=STANDBY; high_temp=0; watchdog=0"
    equipment = find(render_status(snapshot), "equipment-status")
    content = text(equipment)
    assert "Equipment status" in content and "Chiller" in content and "Dryer" in content
    if protocol == "t255p":
        assert find(equipment, "equipment-alarm-count").children == "2 active alarms"
        assert find(equipment, "equipment-alarm-count").color == "danger"
        assert "RUN" in content and "STANDBY" not in content
        assert "On" in content and "Off" in content
        assert '"Active"' in text(find(equipment, "alarm-high_temp"))
        assert '"Active"' in text(find(equipment, "alarm-watchdog"))
        assert '"Clear"' in text(find(equipment, "alarm-float_switch"))
        assert "Temperature sensor alarm" in content and "Reported alarm states" in content
        assert "Inactive" not in content
        state = snapshot["history"][-1]["status"]
        state["summary_alarm"] = False
        state["alarms"] = {key: False for key in state["alarms"]}
        equipment = find(render_status(snapshot), "equipment-status")
        content = text(equipment)
        assert find(equipment, "equipment-alarm-count").children == "0 active alarms"
        assert '"Active"' not in content and '"Clear"' in content
        assert '"RUN"' in content and '"On"' in content
        assert render_status(snapshot)[2] == "STANDBY"
    else:
        assert find(equipment, "equipment-alarm-count").children == "Alarm states unavailable"
        assert "Unavailable" in content and '"Clear"' not in content
        assert '"RUN"' not in content
        assert render_status(snapshot)[2] is None


@pytest.mark.parametrize(
    "state", ["stopped", "stale", "recovering", "fault", "disconnected", "missing"]
)
def test_equipment_fields_do_not_treat_old_or_missing_telemetry_as_live(sample, state):
    _, original = sample
    snapshot = deepcopy(original)
    if state == "stopped":
        snapshot["running"] = False
    elif state == "stale":
        snapshot["history"][-1]["completed_monotonic"] -= 10
    elif state == "recovering":
        snapshot["recovery_error"] = "Retrying read"
    elif state == "fault":
        snapshot["service_error"] = "Read failed"
    elif state == "disconnected":
        snapshot["history"][-1]["status"]["connected"] = False
    else:
        snapshot["history"] = []
    content = text(find(render_status(snapshot), "equipment-status"))
    assert "Unavailable" in content
    assert '"RUN"' not in content and '"Active"' not in content and '"Clear"' not in content
    assert "Alarm states unavailable" in content
    assert "0 active" not in content
    assert render_status(snapshot)[2] is None


def test_missing_alarm_fields_are_unavailable_even_when_mode_is_fresh(sample):
    _, snapshot = sample
    snapshot["history"][-1]["status"].pop("alarms", None)
    equipment = find(render_status(snapshot), "equipment-status")
    assert find(equipment, "equipment-alarm-count").children == "Alarm states unavailable"
    for key in ("float_switch", "high_temp", "low_temp", "sensor", "eeprom", "watchdog"):
        assert '"Unavailable"' in text(find(equipment, f"alarm-{key}"))


def test_poll_timestamp_elapsed_time_and_diagnostics_are_last_poll_data(sample):
    _, snapshot = sample
    latest = snapshot["history"][-1]
    latest["timestamp_utc"] = datetime(2026, 10, 28, 18, 30, tzinfo=UTC)
    latest["elapsed_s"] = 12.5
    snapshot["running"] = False
    cards = render_status(snapshot)
    assert find(cards, "poll-started-utc").children == "Poll started: 2026-10-28 18:30:00 UTC"
    assert find(cards, "monitor-elapsed").children == "Monitoring elapsed 12.5 s (last poll)"
    diagnostics = find(cards, "controller-diagnostics")
    assert diagnostics.start_collapsed is True
    assert diagnostics.persistence is True
    assert diagnostics.persistence_type == "memory"
    assert latest["status"]["detail"] in text(diagnostics)


def test_initial_session_does_not_show_a_zero_alarm_count_or_invent_timing():
    snapshot = T255PSimulator().chiller().snapshot()
    cards = render_status(snapshot)
    assert find(cards, "poll-started-utc").children == "Poll started: Unavailable"
    assert find(cards, "monitor-elapsed").children == "Monitoring elapsed: Unavailable"
    assert find(cards, "equipment-alarm-count").children == "Alarm states unavailable"
    assert find(cards, "controller-diagnostics") is None
