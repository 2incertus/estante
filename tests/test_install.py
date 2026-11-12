"""scripts/install.sh: syntax, flags, the dry run, the config it writes and the cmdline edit.

The real run happens on the Pi as root; here only --dry-run and the sourced helpers run.
"""
import json
import os
import subprocess

import pytest

from conftest import REPO

INSTALL = REPO / "scripts" / "install.sh"
AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
PI_CMDLINE = (
    "console=serial0,115200 console=tty1 root=PARTUUID=1234abcd-02 rootfstype=ext4 "
    "fsck.repair=yes rootwait cfg80211.ieee80211_regdom=US"
)
WANTED = "video=HDMI-A-1:1920x1080@60D vt.global_cursor_default=0 consoleblank=0"


def _run(*args, env=None):
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **(env or {})},
    )


def _source(snippet, *args, env=None):
    """Run `snippet` with install.sh's helper functions loaded and nothing else."""
    return subprocess.run(
        ["bash", "-c", f'ESTANTE_INSTALL_FUNCTIONS_ONLY=1; source "{INSTALL}"; {snippet}', "_", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **(env or {})},
    )


def _config_block(stdout):
    lines = stdout.splitlines()
    start = lines.index("BEGIN estante.json")
    end = lines.index("END estante.json")
    return json.loads("\n".join(lines[start + 1:end]))


def _load_config(path):
    import estante_ctl

    return estante_ctl.load_config(str(path))


@pytest.fixture(autouse=True)
def _ctl_on_path():
    import sys

    sys.path.insert(0, str(REPO / "ctl"))
    yield
    sys.path.remove(str(REPO / "ctl"))


def test_install_script_parses():
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_usage_on_help_and_bad_args():
    result = _run("--help")
    assert result.returncode == 0
    for flag in ("--abs-token", "--abs-url", "--rotate", "--dry-run", "--library-id", "--cmdline-file"):
        assert flag in result.stdout
    result = _run("--bogus")
    assert result.returncode == 2 and "usage" in result.stderr.lower()


def test_requires_token():
    result = _run("--dry-run")
    assert result.returncode == 2 and "--abs-token" in result.stderr


@pytest.mark.parametrize("value", ["90", "1", "upside", ""])
def test_rotate_only_180_or_0(value):
    result = _run("--dry-run", "--abs-token", "x", "--library-id", "L", "--rotate", value)
    assert result.returncode == 2 and "--rotate" in result.stderr


@pytest.mark.parametrize("flag", ["--abs-url", "--abs-token", "--rotate", "--library-id", "--cmdline-file"])
def test_flag_without_value_names_the_flag(flag):
    result = _run("--abs-token", "x", flag)
    assert result.returncode == 2 and f"{flag} needs a value" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the root guard only shows for normal users")
def test_real_install_refuses_without_root():
    result = _run("--abs-token", "x", "--library-id", "L")
    assert result.returncode == 1 and "root" in result.stderr


def test_dry_run_against_fake_abs(fake_abs, tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(PI_CMDLINE + "\n")
    result = _run(
        "--dry-run", "--abs-url", fake_abs.base_url, "--abs-token", "TESTTOKEN",
        "--cmdline-file", str(cmdline),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    # every step, in order
    steps = [line for line in out.splitlines() if line.startswith("== ")]
    assert [s.split(".")[0] for s in steps] == [f"== {n}" for n in range(1, 10)]
    assert "dry run" in out.lower()
    # the token is never echoed, anywhere
    assert "TESTTOKEN" not in out and "TESTTOKEN" not in result.stderr
    # the library was resolved through the (fake) Audiobookshelf, by name
    assert AUDIOBOOKS_ID in out
    assert "/api/libraries" in fake_abs.calls
    # nothing changed
    assert cmdline.read_text() == PI_CMDLINE + "\n"
    assert not (tmp_path / "cmdline.txt.estante-bak").exists()
    assert "reboot needed" in out.lower()
    assert "rotate 180" in out
    for command in ("systemctl disable --now getty@tty1.service", "systemctl enable estante-ctl.service"):
        assert command in out
    cfg = _config_block(out)
    assert cfg["walls"] == [{"name": "audiobooks", "library_id": AUDIOBOOKS_ID, "title": "The shelf"}]
    assert cfg["rotate"] == 180 and cfg["abs_url"] == fake_abs.base_url
    assert cfg["abs_token"] != "TESTTOKEN"


def test_dry_run_config_loads_in_the_conductor(fake_abs, tmp_path):
    result = _run(
        "--dry-run", "--abs-url", fake_abs.base_url + "/", "--abs-token", "TESTTOKEN", "--rotate", "0",
        "--cmdline-file", str(tmp_path / "missing.txt"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    path = tmp_path / "estante.json"
    path.write_text(json.dumps(_config_block(result.stdout)))
    cfg = _load_config(path)
    assert cfg["rotate"] == 0 and cfg["abs_url"] == fake_abs.base_url
    assert cfg["walls"][0]["library_id"] == AUDIOBOOKS_ID
    assert cfg["port"] == 8360 and cfg["listen"] == "0.0.0.0" and cfg["state_dir"] == "/var/lib/estante"


def test_dry_run_fails_clearly_when_no_audiobooks_library(fake_abs, tmp_path):
    fake_abs.override("/api/libraries", {"libraries": [{"id": "p", "name": "Podcasts"}]})
    result = _run("--dry-run", "--abs-url", fake_abs.base_url, "--abs-token", "TESTTOKEN")
    assert result.returncode == 1
    assert 'no library named "Audiobooks"' in result.stderr and "Podcasts" in result.stderr


def test_dry_run_fails_clearly_on_a_bad_token(fake_abs):
    result = _run("--dry-run", "--abs-url", fake_abs.base_url, "--abs-token", "WRONG")
    assert result.returncode == 1 and fake_abs.base_url in result.stderr
    assert "WRONG" not in result.stderr + result.stdout


def test_library_id_flag_skips_the_lookup(tmp_path):
    result = _run(
        "--dry-run", "--abs-url", "http://127.0.0.1:9", "--abs-token", "x", "--library-id", "LIB",
        "--cmdline-file", str(tmp_path / "none"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _config_block(result.stdout)["walls"][0]["library_id"] == "LIB"


def test_render_config_fresh_file_loads(tmp_path):
    out = tmp_path / "estante.json"
    result = _source(
        'render_config "$1" "$2" "$3" "$4" "$5" > "$6"',
        str(REPO / "ctl" / "estante.example.json"), str(tmp_path / "absent.json"),
        "http://192.168.68.86:13378", "LIB", "180", str(out),
        env={"ESTANTE_ABS_TOKEN": "sekrit"},
    )
    assert result.returncode == 0, result.stderr
    cfg = _load_config(out)
    assert cfg["abs_token"] == "sekrit" and cfg["abs_url"] == "http://192.168.68.86:13378"
    assert cfg["rotate"] == 180 and cfg["theme"] == "dark"
    assert cfg["walls"] == [{"name": "audiobooks", "library_id": "LIB", "title": "The shelf"}]
    assert "sekrit" not in result.stdout + result.stderr


def test_render_config_keeps_an_existing_files_other_keys(tmp_path):
    existing = tmp_path / "estante.json"
    walls = [
        {"name": "audiobooks", "library_id": "A", "title": "The shelf"},
        {"name": "light_novels", "library_id": "N", "title": "Light novels"},
    ]
    existing.write_text(json.dumps({"theme": "paper", "walls": walls, "abs_token": "old", "poll_seconds": 30}))
    out = tmp_path / "new.json"
    result = _source(
        'render_config "$1" "$2" "$3" "$4" "$5" > "$6"',
        str(REPO / "ctl" / "estante.example.json"), str(existing), "http://abs:13378", "OTHER", "0", str(out),
        env={"ESTANTE_ABS_TOKEN": "new"},
    )
    assert result.returncode == 0, result.stderr
    cfg = _load_config(out)
    assert cfg["theme"] == "paper" and cfg["poll_seconds"] == 30 and cfg["walls"] == walls
    assert cfg["abs_token"] == "new" and cfg["abs_url"] == "http://abs:13378" and cfg["rotate"] == 0


def test_render_config_fills_an_empty_audiobooks_id(tmp_path):
    existing = tmp_path / "estante.json"
    existing.write_text(json.dumps({"walls": [{"name": "audiobooks", "library_id": "", "title": "The shelf"}]}))
    result = _source(
        'render_config "$1" "$2" "$3" "$4" "$5"',
        str(REPO / "ctl" / "estante.example.json"), str(existing), "http://abs:13378", "LIB", "180",
        env={"ESTANTE_ABS_TOKEN": "t"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["walls"][0]["library_id"] == "LIB"


def test_ensure_cmdline_appends_once_and_backs_up(tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(PI_CMDLINE + "\n")
    first = _source('ensure_cmdline "$1" && echo "changed=$CMDLINE_CHANGED"', str(cmdline))
    assert first.returncode == 0, first.stderr
    assert "changed=yes" in first.stdout
    text = cmdline.read_text()
    assert text == PI_CMDLINE + " " + WANTED + "\n"
    assert text.count("\n") == 1
    assert (tmp_path / "cmdline.txt.estante-bak").read_text() == PI_CMDLINE + "\n"

    second = _source('ensure_cmdline "$1" && echo "changed=$CMDLINE_CHANGED"', str(cmdline))
    assert second.returncode == 0, second.stderr
    assert "changed=no" in second.stdout
    assert cmdline.read_text() == text
    # the backup keeps the original, not the edited line
    assert (tmp_path / "cmdline.txt.estante-bak").read_text() == PI_CMDLINE + "\n"


def test_ensure_cmdline_replaces_a_conflicting_value(tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text("root=x consoleblank=600 video=HDMI-A-1:1024x768@60 rootwait\n")
    result = _source('ensure_cmdline "$1" && echo "changed=$CMDLINE_CHANGED"', str(cmdline))
    assert result.returncode == 0, result.stderr
    words = cmdline.read_text().split()
    assert words == [
        "root=x", "consoleblank=0", "video=HDMI-A-1:1920x1080@60D", "rootwait", "vt.global_cursor_default=0",
    ]


def test_ensure_cmdline_dry_run_changes_nothing(tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(PI_CMDLINE + "\n")
    result = _source('DRY_RUN=yes; ensure_cmdline "$1" && echo "changed=$CMDLINE_CHANGED"', str(cmdline))
    assert result.returncode == 0, result.stderr
    assert "changed=yes" in result.stdout and "video=HDMI-A-1:1920x1080@60D" in result.stdout
    assert cmdline.read_text() == PI_CMDLINE + "\n"
    assert not (tmp_path / "cmdline.txt.estante-bak").exists()


def test_ensure_cmdline_keeps_a_file_without_final_newline_on_one_line(tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(PI_CMDLINE)
    result = _source('ensure_cmdline "$1"', str(cmdline))
    assert result.returncode == 0, result.stderr
    assert cmdline.read_text() == PI_CMDLINE + " " + WANTED + "\n"


def test_unit_rewritten_to_the_checkout(tmp_path):
    result = _source('unit_for "$1"', "/home/admin/estante")
    assert result.returncode == 0, result.stderr
    assert "ExecStart=/home/admin/estante/.venv/bin/python /home/admin/estante/ctl/estante_ctl.py" in result.stdout
    assert "/opt/estante" not in result.stdout
    assert "Environment=ESTANTE_CONFIG=/etc/estante/estante.json" in result.stdout
