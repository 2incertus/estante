"""scripts/install.sh: syntax, argument handling and the .env and settings helpers; the real run happens on the Pi."""
import json
import os
import subprocess

import pytest

from conftest import REPO

INSTALL = REPO / "scripts" / "install.sh"


def _run(*args, env=None):
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **(env or {})},
    )


def test_install_script_parses():
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_usage_on_help_and_bad_args():
    result = _run("--help")
    assert result.returncode == 0 and "--abs-url" in result.stdout and "--dev-http-only" in result.stdout
    result = _run("--bogus")
    assert result.returncode == 2 and "usage" in result.stderr.lower()


def test_requires_abs_credentials():
    result = _run("--abs-url", "http://abs.local:13378")
    assert result.returncode == 2 and "--abs-token" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the root guard only shows for normal users")
def test_full_install_refuses_without_root():
    result = _run("--abs-url", "http://abs.local:13378", "--abs-token", "x", "--inkypi", "/nonexistent")
    assert result.returncode == 1 and "root" in result.stderr


def test_dev_http_only_fails_cleanly_when_inkypi_is_down():
    result = _run(
        "--dev-http-only",
        "--audiobooks-id", "a",
        "--novels-id", "b",
        env={"INKYPI_URL": "http://127.0.0.1:9", "ESTANTE_WAIT_SECONDS": "1"},
    )
    assert result.returncode == 1 and "127.0.0.1:9" in result.stderr


@pytest.mark.parametrize("flag", ["--abs-url", "--abs-token", "--inkypi", "--audiobooks-id", "--novels-id"])
def test_flag_without_value_names_the_flag(flag):
    result = _run("--abs-url", "http://abs.local:13378", flag)
    assert result.returncode == 2 and f"{flag} needs a value" in result.stderr


def _source(snippet, *args):
    """Run `snippet` with install.sh's helper functions loaded and nothing else."""
    return subprocess.run(
        ["bash", "-c", f'ESTANTE_INSTALL_FUNCTIONS_ONLY=1; source "{INSTALL}"; {snippet}', "_", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_env_key_empty_value_is_rewritten(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nABS_URL=http://kept:13378\nABS_TOKEN=\nLAST=x")
    for key, value in (("ABS_URL", "http://new:13378"), ("ABS_TOKEN", "tok")):
        result = _source('ensure_env_key "$1" "$2" "$3"', str(env), key, value)
        assert result.returncode == 0, result.stderr
    assert env.read_text().splitlines() == ["OTHER=1", "ABS_URL=http://kept:13378", "LAST=x", "ABS_TOKEN=tok"]
    result = _source('ensure_env_key "$1" ABS_TOKEN other', str(env))
    assert "ABS_TOKEN already set" in result.stdout
    assert env.read_text().count("ABS_TOKEN=") == 1


def test_settings_fields_force_neutral_image_settings(tmp_path):
    device = tmp_path / "device.json"
    device.write_text(json.dumps({
        "name": "Shelf",
        "display_type": "inky",
        "image_settings": {
            "saturation": 1.4, "contrast": 1.2, "sharpness": 0.8, "brightness": 1.1, "inky_saturation": 0.6,
        },
    }))
    result = _source('settings_fields "$1" UTC', str(device))
    assert result.returncode == 0, result.stderr
    fields = result.stdout.splitlines()
    for name in ("saturation", "contrast", "sharpness", "brightness"):
        assert f"{name}=1.0" in fields
    assert "inky_saturation=0.6" in fields and "deviceName=Shelf" in fields and "interval=24" in fields
