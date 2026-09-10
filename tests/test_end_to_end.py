"""TEST-016: simulator through serial transport, monitoring, CSV and Dash HTTP."""

import json
import subprocess
import sys
from pathlib import Path


def test_hardware_free_demo_with_concurrent_dash_faults_and_shutdown(tmp_path):
    script = Path(__file__).resolve().parents[1] / "examples" / "hardware_free_demo.py"
    output = tmp_path / "integration"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--duration",
            "5",
            "--interval",
            "0.1",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert summary["result"] == "PASS"
    assert summary["samples"] == summary["csv_rows"] >= 40
    assert summary["failed_samples"] > 0
    assert summary["applied_setpoints"] == 3
    assert summary["invalid_writes_rejected"] > 0
    assert summary["page_reloads"] > 0
    assert summary["samples_after_browser_closed"] > 0
    assert summary["clean_shutdown"]
    assert output.with_name("integration-trajectory.html").is_file()
