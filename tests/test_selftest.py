"""scripts/selftest.py renders the fixture compositions and checks their size and mode."""
import json
import os
import subprocess
import sys

from conftest import REPO

SELFTEST = REPO / "scripts" / "selftest.py"


def _run(*args, inkypi=None):
    env = dict(os.environ)
    env["INKYPI"] = str(inkypi) if inkypi else "/nonexistent-inkypi"
    return subprocess.run(
        [sys.executable, str(SELFTEST), *args], capture_output=True, text=True, timeout=60, env=env
    )


def test_selftest_ok():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "selftest: ok"


def test_selftest_reports_first_failure(tmp_path):
    result = _run("--fixtures", str(tmp_path / "missing"))
    assert result.returncode == 1
    last = result.stdout.strip().splitlines()[-1]
    assert last.startswith("selftest: FAILED") and "missing" in last


def test_check_rejects_wrong_mode_and_size():
    import importlib.util

    import pytest
    from PIL import Image

    spec = importlib.util.spec_from_file_location("selftest", SELFTEST)
    selftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selftest)

    with pytest.raises(AssertionError, match="mode"):
        selftest.check("palette", Image.new("P", (1920, 1080), 1))

    with pytest.raises(AssertionError, match="size"):
        selftest.check("small", Image.new("RGB", (800, 480)))
    selftest.check("ok", Image.new("RGB", (1920, 1080)))


def test_selftest_warns_on_non_neutral_image_settings(tmp_path):
    config = tmp_path / "src" / "config"
    config.mkdir(parents=True)
    (config / "device.json").write_text(json.dumps({"image_settings": {
        "saturation": 1.4, "contrast": 1.0, "sharpness": 1.0, "brightness": 1, "inky_saturation": 0.5,
    }}))
    result = _run(inkypi=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "selftest: ok"
    warnings = [line for line in lines if line.startswith("selftest: warning")]
    assert len(warnings) == 1 and "saturation 1.4" in warnings[0]
    assert "contrast" not in warnings[0] and "brightness" not in warnings[0]


def test_selftest_quiet_without_device_json(tmp_path):
    result = _run(inkypi=tmp_path)
    assert result.returncode == 0
    assert "warning" not in result.stdout
