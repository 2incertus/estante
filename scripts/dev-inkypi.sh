#!/bin/bash
# A hardware-free InkyPi checkout in .inkypi/ with the Estante plugins linked in.
#
#   scripts/dev-inkypi.sh            set up (idempotent): clone, venv, plugin symlinks
#   scripts/dev-inkypi.sh run        serve InkyPi on 127.0.0.1:18080 against the fixture
#                                    Audiobookshelf (tests/fixtures), mock display
#   scripts/dev-inkypi.sh run --live serve against the ABS_URL and ABS_TOKEN in .inkypi/.env
#
# InkyPi's own `inkypi.py --dev` hard-codes port 8080, which is taken on the ryzen, so
# `run` imports the same app in dev mode and serves it with waitress on another port.
# `run` restores .inkypi/src/config/device_dev.json, .inkypi/.env and the current image
# when it stops, so the checkout stays as cloned.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV="$REPO/.inkypi"
INKYPI_REPO="${INKYPI_REPO:-https://github.com/fatihak/InkyPi.git}"
INKYPI_COMMIT="df5fac46274a54b682f557a8ad4cde05dc41679a"
PLUGINS="estante_lib now_listening the_shelf"
PORT="${ESTANTE_INKYPI_PORT:-18080}"

# The venv uses the same interpreter as the repo's .venv so tests/test_inkypi_dev.py can
# import InkyPi's dependencies (flask, waitress, pi-heif, pytz) from it in-process.
if [ -z "$PYTHON" ]; then
    if [ -x "$REPO/.venv/bin/python" ]; then
        PYTHON="$REPO/.venv/bin/python"
    else
        PYTHON="python3"
    fi
fi

setup() {
    if [ ! -d "$DEV/.git" ]; then
        echo "cloning InkyPi into $DEV"
        git clone --quiet "$INKYPI_REPO" "$DEV"
    fi
    if [ "$(git -C "$DEV" rev-parse HEAD)" != "$INKYPI_COMMIT" ]; then
        git -C "$DEV" checkout --quiet "$INKYPI_COMMIT"
    fi
    echo "InkyPi at $(git -C "$DEV" rev-parse --short HEAD)"

    local stamp="$DEV/venv/.estante-requirements"
    if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$INKYPI_COMMIT" ]; then
        if [ ! -x "$DEV/venv/bin/python" ]; then
            echo "creating $DEV/venv with $("$PYTHON" -V)"
            "$PYTHON" -m venv "$DEV/venv"
        fi
        local reqs
        reqs="$(mktemp)"
        # inky drives the real panel and cysystemd reads the journal: neither exists here.
        grep -vE '^(inky|cysystemd)([=<>~! ]|$)' "$DEV/install/requirements.txt" > "$reqs"
        echo "installing InkyPi requirements (without inky and cysystemd)"
        "$DEV/venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$reqs"
        rm -f "$reqs"
        echo "$INKYPI_COMMIT" > "$stamp"
    else
        echo "venv ready: $DEV/venv"
    fi

    for name in $PLUGINS; do
        ln -sfn "$REPO/plugins/$name" "$DEV/src/plugins/$name"
    done
    echo "linked $PLUGINS into $DEV/src/plugins/"

    cat <<EOF

InkyPi dev checkout ready.
  Upstream dev mode (mock display, 800x480, port 8080, PNGs in .inkypi/src/mock_display_output/):
    cd .inkypi/src && ../venv/bin/python inkypi.py --dev
  On the ryzen 8080 is taken, so use instead:
    scripts/dev-inkypi.sh run          # fixture Audiobookshelf, http://127.0.0.1:$PORT/
    scripts/dev-inkypi.sh run --live   # ABS_URL and ABS_TOKEN from .inkypi/.env
  Screenshots of the settings pages while it runs:
    NODE_PATH=/home/ubuntu/node_modules node scripts/shoot-inkypi.js
EOF
}

run() {
    local mode="fake"
    if [ "$1" = "--live" ]; then
        mode="live"
    fi
    if [ ! -x "$DEV/venv/bin/python" ] || [ ! -L "$DEV/src/plugins/the_shelf" ]; then
        echo "run scripts/dev-inkypi.sh first" >&2
        exit 1
    fi
    mkdir -p "$REPO/out/dev-state"
    cd "$DEV/src"
    ESTANTE_REPO="$REPO" ESTANTE_ABS="$mode" ESTANTE_PORT="$PORT" \
        ESTANTE_STATE_DIR="$REPO/out/dev-state" \
        exec "$DEV/venv/bin/python" - <<'PY'
import os
import signal
import sys

repo = os.environ["ESTANTE_REPO"]
port = int(os.environ["ESTANTE_PORT"])
config_path = os.path.abspath("config/device_dev.json")
env_path = os.path.abspath("../.env")
image_path = os.path.abspath("static/images/current_image.png")


def snapshot(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def restore(path, data):
    if data is None:
        if os.path.exists(path):
            os.remove(path)
    else:
        with open(path, "wb") as f:
            f.write(data)


saved = {path: snapshot(path) for path in (config_path, env_path, image_path)}
fake = None
if os.environ["ESTANTE_ABS"] == "fake":
    sys.path.insert(0, os.path.join(repo, "tests"))
    from fake_abs import FakeAbs

    fake = FakeAbs(os.path.join(repo, "tests", "fixtures")).__enter__()
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"ABS_URL={fake.base_url}\nABS_TOKEN=TESTTOKEN\n")
    print(f"fixture Audiobookshelf at {fake.base_url}", flush=True)

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
sys.argv = ["inkypi.py", "--dev"]
sys.path.insert(0, os.getcwd())
inkypi = None
try:
    import waitress

    import inkypi

    inkypi.refresh_task.start()
    print(f"InkyPi (dev mode) on http://127.0.0.1:{port}/", flush=True)
    waitress.serve(inkypi.app, host="127.0.0.1", port=port, threads=4)
except KeyboardInterrupt:
    pass
finally:
    if inkypi is not None:
        inkypi.refresh_task.stop()
    for path, data in saved.items():
        restore(path, data)
    if fake is not None:
        fake.__exit__(None, None, None)
    print("stopped; device_dev.json, .env and current_image.png restored", flush=True)
PY
}

case "$1" in
    "")
        setup
        ;;
    run)
        shift
        run "$@"
        ;;
    *)
        echo "usage: scripts/dev-inkypi.sh [run [--live]]" >&2
        exit 2
        ;;
esac
