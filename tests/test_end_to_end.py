"""Production paths with optional sustained concurrent Dash sampling."""

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

import pytest
from fakes import make_serial

from tark_chiller import Chiller, Simulator
from tark_chiller.gui import create_app
from tark_chiller.serial import CalSimulator


@pytest.mark.parametrize("backend", ["thermal", "cal"])
def test_simulator_monitor_csv_dash(tmp_path, backend):
    duration = float(os.environ.get("TARK_SOAK_SECONDS", "5" if backend == "cal" else "2"))
    path = tmp_path / "integration.csv"
    # Accelerated synthetic clock gives both models a short test trajectory.
    device = (
        CalSimulator(clock=lambda: monotonic() * 60).device()
        if backend == "cal"
        else Simulator(time_constant_s=0.5)
    )
    with Chiller(device) as chiller:
        monitor = chiller.start_monitoring(interval_s=0.02, csv_path=path, history_size=25)
        chiller.set_setpoint(18)
        app = create_app(chiller, monitor)
        key = next(key for key in app.callback_map if "live-status" in key)

        def browser():
            with app.server.test_client() as client:
                assert client.get("/_dash-layout").status_code == 200
                response = client.post(
                    "/_dash-update-component",
                    json={
                        "output": key,
                        "outputs": [
                            {"id": "live-status", "property": "children"},
                            {"id": "temperature-history", "property": "figure"},
                        ],
                        "inputs": [{"id": "refresh", "property": "n_intervals", "value": 1}],
                        "state": [],
                        "changedPropIds": ["refresh.n_intervals"],
                    },
                )
                assert response.status_code == 200
                assert len(response.json["response"]["temperature-history"]["figure"]["data"]) == 2

        deadline = monotonic() + duration
        callbacks = 0
        with ThreadPoolExecutor(3) as pool:
            while monotonic() < deadline:
                list(pool.map(lambda _: browser(), range(3)))
                callbacks += 3
                assert monitor.snapshot().running
                sleep(0.05)
        before = monitor.snapshot().sample_count
        sleep(0.15)  # Acquisition continues without a browser.
        assert monitor.snapshot().sample_count > before
        chiller.stop_monitoring()
        snapshot = monitor.snapshot()
        assert snapshot.latest.temperature_c < 19
        assert snapshot.latest.setpoint_c == 18
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    # CAL performs multiple RTU frames with mandatory gaps for each poll.
    assert (
        len(rows)
        == snapshot.logged_samples
        == snapshot.sample_count
        >= (3 if backend == "cal" else 25)
    )
    assert len(snapshot.history) == min(25, snapshot.sample_count)
    assert not snapshot.service_error and not snapshot.logging_error
    assert not snapshot.failed_samples and not monitor.running and not chiller.is_connected
    print(
        json.dumps(
            {
                "duration_s": duration,
                "backend": backend,
                "samples": snapshot.sample_count,
                "csv_rows": len(rows),
                "concurrent_callbacks": callbacks,
                "final_temperature_c": snapshot.latest.temperature_c,
                "history_size": len(snapshot.history),
                "clean_shutdown": True,
            }
        )
    )


def test_fake_serial_monitor_reports_outage_and_recovers(tmp_path):
    device, endpoint = make_serial()
    path = tmp_path / "serial.csv"
    with Chiller(device, reconnect_attempts=1, reconnect_delay_s=0.001) as chiller:
        monitor = chiller.start_monitoring(interval_s=0.01, csv_path=path)
        # Inject a cable fault while polling, then leave it until the budget is exhausted.
        endpoint.read_error = OSError("synthetic cable unplugged")
        deadline = monotonic() + 2
        while not monitor.snapshot().failed_samples:
            assert monotonic() < deadline
            sleep(0.005)
        assert not monitor.snapshot().latest.status.connected
        opens = endpoint.open_count
        sleep(0.05)
        assert endpoint.open_count == opens
        endpoint.read_error = None
        chiller.connect()
        while monitor.snapshot().latest.error:
            assert monotonic() < deadline
            sleep(0.005)
        assert monitor.snapshot().latest.temperature_c == 20
        assert endpoint.applied_writes == 0
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert any(row["error"] and row["temperature_c"] == "" for row in rows)
    assert not endpoint.is_open
