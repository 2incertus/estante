"""scripts/install.sh: syntax and argument handling only; the real run happens on the Pi."""
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
