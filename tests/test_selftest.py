"""scripts/selftest.py renders the fixture screens and checks the driver pass is a no-op."""
import subprocess
import sys

from conftest import REPO

SELFTEST = REPO / "scripts" / "selftest.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SELFTEST), *args], capture_output=True, text=True, timeout=60
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


def test_check_rejects_stray_indexes_and_wrong_size():
    import importlib.util

    import pytest
    from PIL import Image

    spec = importlib.util.spec_from_file_location("selftest", SELFTEST)
    selftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selftest)

    stray = Image.new("P", (800, 480), 1)
    stray.putpixel((5, 5), 7)
    with pytest.raises(AssertionError, match="outside 0..5"):
        selftest.check("stray", stray)

    with pytest.raises(AssertionError, match="size"):
        selftest.check("small", Image.new("P", (400, 240), 1))
