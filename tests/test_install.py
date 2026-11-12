"""scripts/install.sh: syntax, flags, the token sources, the dry run, the config it writes,
the cmdline edit and the reboot decision.

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
DEAD_URL = "http://127.0.0.1:9"


@pytest.fixture
def config_dir(tmp_path):
    """An empty stand-in for /etc/estante, so no test reads the machine's real config."""
    d = tmp_path / "etc-estante"
    d.mkdir()
    return d


def _env(extra=None, config_dir=None):
    env = {k: v for k, v in os.environ.items() if k != "ESTANTE_ABS_TOKEN"}
    if config_dir is not None:
        env["ESTANTE_CONFIG_DIR"] = str(config_dir)
    env.update(extra or {})
    return env


def _run(*args, env=None, config_dir=None):
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env=_env(env, config_dir),
    )


def _source(snippet, *args, env=None):
    """Run `snippet` with install.sh's helper functions loaded and nothing else."""
    return subprocess.run(
        ["bash", "-c", f'ESTANTE_INSTALL_FUNCTIONS_ONLY=1; source "{INSTALL}"; {snippet}', "_", *args],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        env=_env(env),
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
    for flag in (
        "--abs-token-file", "ESTANTE_ABS_TOKEN", "--abs-url", "--rotate", "--dry-run", "--library-id",
        "--cmdline-file",
    ):
        assert flag in result.stdout
    result = _run("--bogus")
    assert result.returncode == 2 and "usage" in result.stderr.lower()


# ---- the token


def test_no_token_and_no_tty_errors_clearly(config_dir):
    result = _run("--dry-run", "--library-id", "L", config_dir=config_dir)
    assert result.returncode == 2
    for hint in ("ESTANTE_ABS_TOKEN", "--abs-token-file", "terminal"):
        assert hint in result.stderr


def test_abs_token_flag_is_refused_for_a_real_run(config_dir):
    result = _run("--abs-token", "SEKRIT", "--library-id", "L", config_dir=config_dir)
    assert result.returncode == 2
    assert "--abs-token" in result.stderr and "--dry-run" in result.stderr
    assert "journal" in result.stderr and "ESTANTE_ABS_TOKEN" in result.stderr
    assert "SEKRIT" not in result.stderr + result.stdout


def test_token_file_works_and_is_never_echoed(fake_abs, config_dir, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("TESTTOKEN\n")
    token_file.chmod(0o600)
    result = _run(
        "--dry-run", "--abs-url", fake_abs.base_url, "--abs-token-file", str(token_file),
        config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert AUDIOBOOKS_ID in result.stdout and "/api/libraries" in fake_abs.calls
    assert "TESTTOKEN" not in result.stdout + result.stderr


def test_token_file_missing_or_empty_fails(config_dir, tmp_path):
    result = _run(
        "--dry-run", "--library-id", "L", "--abs-token-file", str(tmp_path / "none"), config_dir=config_dir,
    )
    assert result.returncode == 2 and "cannot read" in result.stderr
    empty = tmp_path / "empty"
    empty.write_text("\n")
    result = _run("--dry-run", "--library-id", "L", "--abs-token-file", str(empty), config_dir=config_dir)
    assert result.returncode == 2 and "empty" in result.stderr


def test_token_from_the_environment(fake_abs, config_dir):
    result = _run(
        "--dry-run", "--abs-url", fake_abs.base_url,
        env={"ESTANTE_ABS_TOKEN": "TESTTOKEN"}, config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert AUDIOBOOKS_ID in result.stdout
    assert "TESTTOKEN" not in result.stdout + result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the root guard only shows for normal users")
def test_real_install_refuses_without_root(config_dir):
    result = _run("--library-id", "L", env={"ESTANTE_ABS_TOKEN": "x"}, config_dir=config_dir)
    assert result.returncode == 1 and "root" in result.stderr


# ---- flags


@pytest.mark.parametrize("value", ["90", "1", "upside", ""])
def test_rotate_only_180_or_0(value, config_dir):
    result = _run("--dry-run", "--abs-token", "x", "--library-id", "L", "--rotate", value, config_dir=config_dir)
    assert result.returncode == 2 and "--rotate" in result.stderr


@pytest.mark.parametrize(
    "flag", ["--abs-url", "--abs-token", "--abs-token-file", "--rotate", "--library-id", "--cmdline-file"]
)
def test_flag_without_value_names_the_flag(flag):
    result = _run("--dry-run", flag)
    assert result.returncode == 2 and f"{flag} needs a value" in result.stderr


def test_flag_value_starting_with_dashes_is_a_missing_value(config_dir):
    # `--abs-token --dry-run` must not swallow --dry-run and start a real run
    result = _run("--abs-token", "--dry-run", "--library-id", "L", config_dir=config_dir)
    assert result.returncode == 2 and "--abs-token needs a value" in result.stderr
    result = _run("--dry-run", "--abs-token", "x", "--library-id", "--rotate", "0", config_dir=config_dir)
    assert result.returncode == 2 and "--library-id needs a value" in result.stderr


# ---- the dry run


def test_dry_run_against_fake_abs(fake_abs, tmp_path, config_dir):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(PI_CMDLINE + "\n")
    result = _run(
        "--dry-run", "--abs-url", fake_abs.base_url, "--abs-token", "TESTTOKEN",
        "--cmdline-file", str(cmdline), config_dir=config_dir,
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
    assert list(config_dir.iterdir()) == []
    assert "rotate 180" in out
    for command in ("systemctl disable --now getty@tty1.service", "systemctl enable estante-ctl.service"):
        assert command in out
    cfg = _config_block(out)
    assert cfg["walls"] == [{"name": "audiobooks", "library_id": AUDIOBOOKS_ID, "title": "The shelf"}]
    assert cfg["rotate"] == 180 and cfg["abs_url"] == fake_abs.base_url
    assert cfg["abs_token"] != "TESTTOKEN"


def test_dry_run_config_loads_in_the_conductor(fake_abs, tmp_path, config_dir):
    result = _run(
        "--dry-run", "--abs-url", fake_abs.base_url + "/", "--abs-token", "TESTTOKEN", "--rotate", "0",
        "--cmdline-file", str(tmp_path / "missing.txt"), config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    path = tmp_path / "estante.json"
    path.write_text(json.dumps(_config_block(result.stdout)))
    cfg = _load_config(path)
    assert cfg["rotate"] == 0 and cfg["abs_url"] == fake_abs.base_url
    assert cfg["walls"][0]["library_id"] == AUDIOBOOKS_ID
    assert cfg["port"] == 8360 and cfg["listen"] == "0.0.0.0" and cfg["state_dir"] == "/var/lib/estante"


def test_dry_run_fails_clearly_when_no_audiobooks_library(fake_abs, config_dir):
    fake_abs.override("/api/libraries", {"libraries": [{"id": "p", "name": "Podcasts"}]})
    result = _run("--dry-run", "--abs-url", fake_abs.base_url, "--abs-token", "TESTTOKEN", config_dir=config_dir)
    assert result.returncode == 1
    assert 'no library named "Audiobooks"' in result.stderr and "Podcasts" in result.stderr


def test_dry_run_fails_clearly_on_a_bad_token(fake_abs, config_dir):
    result = _run("--dry-run", "--abs-url", fake_abs.base_url, "--abs-token", "WRONG", config_dir=config_dir)
    assert result.returncode == 1 and fake_abs.base_url in result.stderr
    assert "WRONG" not in result.stderr + result.stdout


def test_library_id_flag_skips_the_lookup(tmp_path, config_dir):
    result = _run(
        "--dry-run", "--abs-url", DEAD_URL, "--abs-token", "x", "--library-id", "LIB",
        "--cmdline-file", str(tmp_path / "none"), config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _config_block(result.stdout)["walls"][0]["library_id"] == "LIB"


# ---- reruns over an existing config


def _existing(config_dir, **values):
    cfg = {
        "abs_url": "http://kept:13378",
        "abs_token": "old",
        "rotate": 0,
        "theme": "paper",
        "walls": [
            {"name": "audiobooks", "library_id": "KEPT", "title": "The shelf"},
            {"name": "light_novels", "library_id": "", "title": "Light novels"},
        ],
    }
    cfg.update(values)
    (config_dir / "estante.json").write_text(json.dumps(cfg))
    return cfg


def test_rerun_keeps_url_rotate_and_library_id_without_a_lookup(config_dir, tmp_path):
    existing = _existing(config_dir)
    result = _run(
        "--dry-run", "--abs-token", "x", "--cmdline-file", str(tmp_path / "none"), config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "library id KEPT (from" in result.stdout
    cfg = _config_block(result.stdout)
    assert cfg["abs_url"] == "http://kept:13378" and cfg["rotate"] == 0
    assert cfg["walls"] == existing["walls"] and cfg["theme"] == "paper"
    assert "rotate 0 (from" in result.stdout


def test_rerun_flags_override_the_existing_config(config_dir, tmp_path):
    _existing(config_dir)
    result = _run(
        "--dry-run", "--abs-token", "x", "--abs-url", "http://new:13378", "--rotate", "180",
        "--library-id", "NEW", "--cmdline-file", str(tmp_path / "none"), config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = _config_block(result.stdout)
    assert cfg["abs_url"] == "http://new:13378" and cfg["rotate"] == 180
    assert cfg["walls"][0]["library_id"] == "NEW" and cfg["walls"][1]["name"] == "light_novels"


def test_rerun_without_an_id_looks_it_up(fake_abs, config_dir, tmp_path):
    _existing(config_dir, abs_url=fake_abs.base_url, walls=[{"name": "audiobooks", "library_id": ""}])
    result = _run(
        "--dry-run", "--abs-token", "TESTTOKEN", "--cmdline-file", str(tmp_path / "none"), config_dir=config_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "/api/libraries" in fake_abs.calls
    assert _config_block(result.stdout)["walls"][0]["library_id"] == AUDIOBOOKS_ID


# ---- render_config and the real config write


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
    assert cfg["theme"] == "paper" and cfg["poll_seconds"] == 30
    # the id passed in is the one the installer decided on: it replaces the audiobooks id
    assert cfg["walls"] == [dict(walls[0], library_id="OTHER"), walls[1]]
    assert cfg["abs_token"] == "new" and cfg["abs_url"] == "http://abs:13378" and cfg["rotate"] == 0


def test_config_get_reads_the_existing_values(tmp_path):
    path = tmp_path / "estante.json"
    path.write_text(json.dumps({
        "abs_url": "http://u", "rotate": 0, "walls": [{"name": "audiobooks", "library_id": "X"}],
    }))
    result = _source(
        'config_get "$1" abs_url; config_get "$1" rotate; config_get "$1" audiobooks_id; config_get "$2" rotate',
        str(path), str(tmp_path / "absent.json"),
    )
    assert result.returncode == 0, result.stderr
    # nothing at all for the absent file
    assert result.stdout.splitlines() == ["http://u", "0", "X"]


def test_write_config_mode_600_atomic_and_idempotent(tmp_path):
    etc = tmp_path / "etc"
    snippet = (
        'CONFIG_DIR="$1"; CONFIG_FILE="$1/estante.json"; '
        'write_config "$2" "$3" "$4"; echo "changed=$CONFIG_CHANGED"'
    )
    env = {"ESTANTE_ABS_TOKEN": "sekrit"}
    first = _source(snippet, str(etc), "http://abs:13378", "LIB", "180", env=env)
    assert first.returncode == 0, first.stderr
    assert "changed=yes" in first.stdout and "sekrit" not in first.stdout + first.stderr
    path = etc / "estante.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert _load_config(path)["abs_token"] == "sekrit"
    inode = path.stat().st_ino

    second = _source(snippet, str(etc), "http://abs:13378", "LIB", "180", env=env)
    assert second.returncode == 0, second.stderr
    assert "already up to date" in second.stdout and "changed=no" in second.stdout
    assert path.stat().st_ino == inode

    third = _source(snippet, str(etc), "http://abs:13378", "LIB", "0", env=env)
    assert third.returncode == 0, third.stderr
    assert "changed=yes" in third.stdout
    # replaced by a rename, not rewritten in place, and no temp file left behind
    assert path.stat().st_ino != inode
    assert path.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in etc.iterdir()) == ["estante.json"]
    assert _load_config(path)["rotate"] == 0


def test_write_config_takes_the_token_scoped_to_the_call(tmp_path):
    # the installer runs `ESTANTE_ABS_TOKEN="$ABS_TOKEN" write_config ...`, never exporting it
    etc = tmp_path / "etc"
    result = _source(
        'CONFIG_DIR="$1"; CONFIG_FILE="$1/estante.json"; ESTANTE_ABS_TOKEN=scoped write_config u L 180 > /dev/null; '
        'echo "after=${ESTANTE_ABS_TOKEN:-unset}"',
        str(etc),
    )
    assert result.returncode == 0, result.stderr
    assert "after=unset" in result.stdout
    assert _load_config(etc / "estante.json")["abs_token"] == "scoped"


# ---- the kernel command line


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


# ---- the reboot decision, from the running kernel


def test_reboot_needed_reads_the_running_kernel(tmp_path):
    proc = tmp_path / "cmdline"
    proc.write_text(PI_CMDLINE + " video=HDMI-A-1:1920x1080@60D consoleblank=0\n")
    result = _source('reboot_needed "$1" && echo needed || echo fine', str(proc))
    assert result.stdout.strip() == "needed"
    proc.write_text("coherent_pool=1M " + PI_CMDLINE + " " + WANTED + "\n")
    result = _source('reboot_needed "$1" && echo needed || echo fine', str(proc))
    assert result.stdout.strip() == "fine"
    # a prefix is not the parameter
    proc.write_text(PI_CMDLINE + " video=HDMI-A-1:1920x1080@60Dx vt.global_cursor_default=0 consoleblank=0\n")
    result = _source('reboot_needed "$1" && echo needed || echo fine', str(proc))
    assert result.stdout.strip() == "needed"


def _dry_run_with_proc(tmp_path, config_dir, proc_text, cmdline_text):
    proc = tmp_path / "proc-cmdline"
    proc.write_text(proc_text)
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(cmdline_text)
    return _run(
        "--dry-run", "--abs-token", "x", "--library-id", "L", "--cmdline-file", str(cmdline),
        env={"ESTANTE_PROC_CMDLINE": str(proc)}, config_dir=config_dir,
    )


def test_reboot_still_needed_when_the_file_is_done_but_the_kernel_is_not(tmp_path, config_dir):
    # the file already carries the parameters (an earlier run edited it) but the Pi never rebooted
    result = _dry_run_with_proc(
        tmp_path, config_dir, PI_CMDLINE + " consoleblank=0\n", PI_CMDLINE + " " + WANTED + "\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already has" in result.stdout
    last = result.stdout.strip().splitlines()[-1]
    assert last.startswith("REBOOT NEEDED")
    assert "video=HDMI-A-1:1920x1080@60D" in last and "vt.global_cursor_default=0" in last


def test_no_reboot_once_the_kernel_has_the_parameters(tmp_path, config_dir):
    result = _dry_run_with_proc(
        tmp_path, config_dir, PI_CMDLINE + " " + WANTED + "\n", PI_CMDLINE + " " + WANTED + "\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REBOOT NEEDED" not in result.stdout and "no reboot needed" in result.stdout


# ---- the service unit


def test_unit_rewritten_to_the_checkout():
    result = _source('unit_for "$1"', "/home/admin/estante")
    assert result.returncode == 0, result.stderr
    assert "ExecStart=/home/admin/estante/.venv/bin/python /home/admin/estante/ctl/estante_ctl.py" in result.stdout
    assert "/opt/estante" not in result.stdout
    assert "Environment=ESTANTE_CONFIG=/etc/estante/estante.json" in result.stdout


@pytest.mark.parametrize("path", ["/srv/a&b/estante", "/srv/a#b/estante", "/srv/a\\1/estante"])
def test_unit_for_a_path_with_sed_metacharacters(path):
    result = _source('unit_for "$1"', path)
    assert result.returncode == 0, result.stderr
    assert f"ExecStart={path}/.venv/bin/python {path}/ctl/estante_ctl.py" in result.stdout.splitlines()
    assert f"WorkingDirectory={path}" in result.stdout.splitlines()
