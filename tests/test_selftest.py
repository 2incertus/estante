"""scripts/selftest.py renders the fixture composition, times it and checks the framebuffer."""
import importlib.util
import subprocess
import sys

import pytest

from conftest import REPO

SELFTEST = REPO / "scripts" / "selftest.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SELFTEST), *args], capture_output=True, text=True, timeout=120
    )


def _module():
    spec = importlib.util.spec_from_file_location("selftest", SELFTEST)
    selftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selftest)
    return selftest


def _sysfs(tmp_path, size="1920,1080", bpp="16", stride="3840"):
    d = tmp_path / "fb0"
    d.mkdir()
    (d / "virtual_size").write_text(size + "\n")
    (d / "bits_per_pixel").write_text(bpp + "\n")
    if stride is not None:
        (d / "stride").write_text(stride + "\n")
    return d


def test_selftest_ok_without_a_framebuffer(tmp_path):
    result = _run("--sysfs", str(tmp_path / "nofb"))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "selftest: ok"
    assert any(line.startswith("render ") and line.rstrip().endswith(" s") for line in lines), lines
    assert any("no framebuffer" in line for line in lines)
    assert not any("warning" in line for line in lines)


def test_selftest_reports_a_forced_1080p_framebuffer(tmp_path):
    result = _run("--sysfs", str(_sysfs(tmp_path)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "framebuffer 1920x1080 at 16 bpp" in result.stdout
    assert "warning" not in result.stdout


def test_selftest_warns_when_the_mode_is_not_forced_yet(tmp_path):
    result = _run("--sysfs", str(_sysfs(tmp_path, size="1024,768", stride="2048")))
    assert result.returncode == 0, result.stdout + result.stderr
    warnings = [line for line in result.stdout.splitlines() if line.startswith("selftest: warning")]
    assert len(warnings) == 1 and "1024x768" in warnings[0] and "reboot" in warnings[0]
    assert result.stdout.strip().splitlines()[-1] == "selftest: ok"


def test_selftest_fails_on_a_depth_it_cannot_write(tmp_path):
    result = _run("--sysfs", str(_sysfs(tmp_path, bpp="24", stride="5760")))
    assert result.returncode == 1
    last = result.stdout.strip().splitlines()[-1]
    assert last.startswith("selftest: FAILED") and "24 bpp" in last


def test_selftest_fails_on_unreadable_sysfs(tmp_path):
    d = tmp_path / "fb0"
    d.mkdir()
    (d / "virtual_size").write_text("garbage\n")
    result = _run("--sysfs", str(d))
    assert result.returncode == 1
    assert result.stdout.strip().splitlines()[-1].startswith("selftest: FAILED")


def test_selftest_warns_over_budget(tmp_path):
    result = _run("--sysfs", str(tmp_path / "nofb"), "--budget", "0")
    assert result.returncode == 0, result.stdout + result.stderr
    warnings = [line for line in result.stdout.splitlines() if line.startswith("selftest: warning")]
    assert len(warnings) == 1 and "over the 0.0 s budget" in warnings[0]


def test_selftest_reports_first_failure(tmp_path):
    result = _run("--fixtures", str(tmp_path / "missing"), "--sysfs", str(tmp_path / "nofb"))
    assert result.returncode == 1
    last = result.stdout.strip().splitlines()[-1]
    assert last.startswith("selftest: FAILED") and "missing" in last


def test_check_rejects_wrong_mode_and_size():
    from PIL import Image

    selftest = _module()
    with pytest.raises(AssertionError, match="mode"):
        selftest.check("palette", Image.new("P", (1920, 1080), 1))
    with pytest.raises(AssertionError, match="size"):
        selftest.check("small", Image.new("RGB", (800, 480)))
    selftest.check("ok", Image.new("RGB", (1920, 1080)))
