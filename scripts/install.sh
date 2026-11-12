#!/bin/bash
# Install Estante on the Raspberry Pi that drives the HDMI screen. Run as root from the
# Estante checkout (normally /opt/estante):
#
#   sudo scripts/install.sh --abs-token TOKEN [--abs-url URL] [--rotate 180|0]
#
# Every step is idempotent and says what it did, so running it again is safe.
# --dry-run prints what it would do and changes nothing (no root needed); it still
# looks the library up in Audiobookshelf, which is a GET.
#
# It never touches Docker or Ouvido's container, ports or GPIO.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/.venv"
CONFIG_DIR="/etc/estante"
CONFIG_FILE="$CONFIG_DIR/estante.json"
STATE_DIR="/var/lib/estante"
UNIT_FILE="/etc/systemd/system/estante-ctl.service"
CTL_URL="http://127.0.0.1:8360"
WAIT_SECONDS="${ESTANTE_WAIT_SECONDS:-60}"
LIBRARY_NAME="Audiobooks"
PACKAGES=(python3-venv libopenjp2-7 libjpeg62-turbo curl)
CMDLINE_PARAMS=("video=HDMI-A-1:1920x1080@60D" "vt.global_cursor_default=0" "consoleblank=0")

ABS_URL="http://192.168.68.86:13378"
ABS_TOKEN=""
ROTATE="180"
ROTATE_GIVEN=""
LIBRARY_ID=""
CMDLINE_FILE="/boot/firmware/cmdline.txt"
DRY_RUN=""
CMDLINE_CHANGED="no"

usage() {
    cat <<EOF
usage: sudo scripts/install.sh --abs-token TOKEN [--abs-url URL] [--rotate 180|0]
                               [--library-id ID] [--cmdline-file PATH] [--dry-run]

  --abs-token TOKEN    Audiobookshelf API token (written to $CONFIG_FILE, mode 600, never echoed)
  --abs-url URL        Audiobookshelf base URL (default $ABS_URL)
  --rotate 180|0       180 when the monitor stands upside down (default 180)
  --library-id ID      skip the Audiobookshelf lookup of the "$LIBRARY_NAME" library
  --cmdline-file PATH  kernel command line to edit (default $CMDLINE_FILE)
  --dry-run            print what would be done, change nothing

Environment: ESTANTE_WAIT_SECONDS (default 60), how long to wait for estante-ctl.
EOF
}

die() {
    echo "install: $1" >&2
    exit 1
}

step() {
    echo
    echo "== $1"
}

# Echo a command, then run it unless this is a dry run. Never pass the token here.
run() {
    if [ -n "$DRY_RUN" ]; then
        echo "would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

package_installed() {
    dpkg-query -W -f='${Status}' "$1" 2> /dev/null | grep -q "ok installed"
}

pillow_ok() {
    [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c \
        'import sys, PIL; sys.exit(0 if int(PIL.__version__.split(".")[0]) >= 11 else 1)' 2> /dev/null
}

# GET /api/libraries. The token goes to curl on stdin as a config line, so it never
# shows in the process list.
abs_libraries() {
    local token="${ABS_TOKEN//\\/\\\\}"
    token="${token//\"/\\\"}"
    printf 'header = "Authorization: Bearer %s"\n' "$token" \
        | curl -sf --max-time 15 -K - "$ABS_URL/api/libraries"
}

# stdin: the /api/libraries JSON. Prints the id of the library named $1 (case and
# surrounding spaces ignored), or explains on stderr and fails.
find_library_id() {
    local program
    program="$(cat <<'PY'
import json
import sys

name, url = sys.argv[1], sys.argv[2]
libraries = json.load(sys.stdin).get("libraries") or []
ids = [lib.get("id") for lib in libraries if (lib.get("name") or "").strip().lower() == name.lower()]
if not ids or not ids[0]:
    known = ", ".join(f"{lib.get('name')} ({lib.get('id')})" for lib in libraries) or "none"
    sys.exit(f'install: no library named "{name}" at {url}; libraries: {known}')
print(ids[0])
PY
)"
    python3 -c "$program" "$1" "$2"
}

# The config estante-ctl reads, on stdout: the example, then an existing config over it,
# then abs_url, abs_token (from $ESTANTE_ABS_TOKEN) and rotate from the flags. The
# audiobooks wall gets the library id when it has none; other walls are kept.
# Args: example existing abs_url library_id rotate
render_config() {
    python3 - "$@" <<'PY'
import json
import os
import sys

example, existing, url, library_id, rotate = sys.argv[1:6]
with open(example, encoding="utf-8") as f:
    cfg = json.load(f)
try:
    with open(existing, encoding="utf-8") as f:
        old = json.load(f)
except FileNotFoundError:
    old = {}
except (OSError, ValueError) as e:
    sys.exit(f"install: cannot read {existing}: {e}")
if not isinstance(old, dict):
    sys.exit(f"install: {existing} is not a JSON object")
cfg.update(old)
cfg["abs_url"] = url
cfg["abs_token"] = os.environ["ESTANTE_ABS_TOKEN"]
cfg["rotate"] = int(rotate)
walls = cfg.get("walls") or []
if not walls:
    walls = [{"name": "audiobooks", "library_id": "", "title": "The shelf"}]
for wall in walls:
    if wall.get("name") == "audiobooks" and not wall.get("library_id"):
        wall["library_id"] = library_id
cfg["walls"] = walls
json.dump(cfg, sys.stdout, indent=2)
print()
PY
}

# Make the kernel command line in $1 carry CMDLINE_PARAMS: a parameter with the same key
# (video=HDMI-A-1:, consoleblank=, ...) is replaced in place, a missing one appended. The
# first edit keeps the original as $1.estante-bak. Sets CMDLINE_CHANGED to yes or no.
ensure_cmdline() {
    local file="$1"
    local line=""
    local new want key found i
    local -a words
    CMDLINE_CHANGED="no"
    IFS= read -r line < "$file" || true
    read -ra words <<< "$line"
    for want in "${CMDLINE_PARAMS[@]}"; do
        case "$want" in
            video=*) key="${want%%:*}:" ;;
            *) key="${want%%=*}=" ;;
        esac
        found=""
        for i in "${!words[@]}"; do
            case "${words[$i]}" in
                "$key"*)
                    words[i]="$want"
                    found="yes"
                    ;;
            esac
        done
        if [ -z "$found" ]; then
            words+=("$want")
        fi
    done
    new="${words[*]}"
    if [ "$new" = "$line" ]; then
        echo "$file already has: ${CMDLINE_PARAMS[*]}"
        return 0
    fi
    CMDLINE_CHANGED="yes"
    if [ -n "$DRY_RUN" ]; then
        echo "would back up $file to $file.estante-bak and write:"
        echo "$new"
        return 0
    fi
    if [ ! -e "$file.estante-bak" ]; then
        cp -p "$file" "$file.estante-bak"
        echo "backed up $file to $file.estante-bak"
    fi
    printf '%s\n' "$new" > "$file.estante-tmp"
    mv -f "$file.estante-tmp" "$file"
    echo "wrote $file:"
    echo "$new"
}

# The service unit with /opt/estante replaced by the checkout at $1.
unit_for() {
    sed "s#/opt/estante#$1#g" "$REPO/ctl/estante-ctl.service"
}

wait_for_state() {
    local deadline=$((SECONDS + WAIT_SECONDS))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$CTL_URL/state" || true)" = "200" ]; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# tests load the helpers above without running the installer
if [ "${ESTANTE_INSTALL_FUNCTIONS_ONLY:-}" = "1" ]; then
    return 0 2> /dev/null || exit 0
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        --abs-url|--abs-token|--rotate|--library-id|--cmdline-file)
            if [ "$#" -lt 2 ]; then
                echo "install: $1 needs a value" >&2
                usage >&2
                exit 2
            fi
            ;;
    esac
    case "$1" in
        --abs-url) ABS_URL="${2%/}"; shift 2 ;;
        --abs-token) ABS_TOKEN="$2"; shift 2 ;;
        --rotate) ROTATE="$2"; ROTATE_GIVEN="yes"; shift 2 ;;
        --library-id) LIBRARY_ID="$2"; shift 2 ;;
        --cmdline-file) CMDLINE_FILE="$2"; shift 2 ;;
        --dry-run) DRY_RUN="yes"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [ -z "$ABS_TOKEN" ]; then
    echo "install: --abs-token is required" >&2
    usage >&2
    exit 2
fi
if [ "$ROTATE" != "180" ] && [ "$ROTATE" != "0" ]; then
    echo "install: --rotate must be 180 or 0, not \"$ROTATE\"" >&2
    exit 2
fi
if [ -z "$DRY_RUN" ] && [ "$(id -u)" -ne 0 ]; then
    die "run as root: sudo scripts/install.sh ..."
fi

if [ -n "$DRY_RUN" ]; then
    echo "dry run: nothing will be changed"
fi
echo "checkout: $REPO"
echo "Audiobookshelf: $ABS_URL"
if [ -n "$ROTATE_GIVEN" ]; then
    echo "rotate $ROTATE"
else
    echo "rotate 180 (the default: the monitor stands upside down; pass --rotate 0 if it stands upright)"
fi

# ---- 1. system packages
step "1. system packages: ${PACKAGES[*]}"
missing=()
for pkg in "${PACKAGES[@]}"; do
    if package_installed "$pkg"; then
        echo "$pkg already installed"
    else
        missing+=("$pkg")
    fi
done
if [ "${#missing[@]}" -gt 0 ]; then
    run apt-get install -y "${missing[@]}"
fi

# ---- 2. the venv and Pillow (a binary wheel, never compiled on the Pi)
step "2. Python venv at $VENV with pillow>=11"
if [ -x "$VENV/bin/python" ]; then
    echo "$VENV exists"
else
    run python3 -m venv "$VENV"
fi
if pillow_ok; then
    echo "Pillow $("$VENV/bin/python" -c 'import PIL; print(PIL.__version__)') already in the venv"
else
    run env PIP_ONLY_BINARY=:all: "$VENV/bin/pip" install --disable-pip-version-check "pillow>=11"
fi

# ---- 3. the config, with the Audiobooks library id from Audiobookshelf
step "3. $CONFIG_FILE"
if [ -n "$LIBRARY_ID" ]; then
    echo "library id $LIBRARY_ID (from --library-id)"
else
    libraries="$(abs_libraries)" \
        || die "could not list the libraries at $ABS_URL/api/libraries (wrong address or token?)"
    LIBRARY_ID="$(printf '%s' "$libraries" | find_library_id "$LIBRARY_NAME" "$ABS_URL")" || exit 1
    echo "library \"$LIBRARY_NAME\" is $LIBRARY_ID"
fi
if [ -n "$DRY_RUN" ]; then
    echo "would write $CONFIG_FILE (mode 600, token hidden here):"
    echo "BEGIN estante.json"
    ESTANTE_ABS_TOKEN="(hidden)" render_config \
        "$REPO/ctl/estante.example.json" "$CONFIG_FILE" "$ABS_URL" "$LIBRARY_ID" "$ROTATE"
    echo "END estante.json"
else
    mkdir -p "$CONFIG_DIR"
    tmp="$(mktemp "$CONFIG_DIR/.estante.json.XXXXXX")"
    chmod 600 "$tmp"
    if ! ESTANTE_ABS_TOKEN="$ABS_TOKEN" render_config \
        "$REPO/ctl/estante.example.json" "$CONFIG_FILE" "$ABS_URL" "$LIBRARY_ID" "$ROTATE" > "$tmp"; then
        rm -f "$tmp"
        exit 1
    fi
    if [ -f "$CONFIG_FILE" ] && cmp -s "$tmp" "$CONFIG_FILE"; then
        rm -f "$tmp"
        echo "$CONFIG_FILE already up to date"
    else
        existed="$([ -f "$CONFIG_FILE" ] && echo "updated" || echo "wrote")"
        mv -f "$tmp" "$CONFIG_FILE"
        echo "$existed $CONFIG_FILE (abs_url, abs_token, rotate, audiobooks wall)"
    fi
    chmod 600 "$CONFIG_FILE"
    echo "mode 600"
fi

# ---- 4. state dir and cover cache
step "4. $STATE_DIR/covers"
if [ -d "$STATE_DIR/covers" ]; then
    echo "$STATE_DIR/covers exists"
else
    run mkdir -p "$STATE_DIR/covers"
fi

# ---- 5. the kernel command line: forced HDMI mode, no cursor, no console blanking
step "5. $CMDLINE_FILE: ${CMDLINE_PARAMS[*]}"
if [ -f "$CMDLINE_FILE" ]; then
    ensure_cmdline "$CMDLINE_FILE"
elif [ -n "$DRY_RUN" ]; then
    echo "$CMDLINE_FILE not found here; on the Pi it would gain: ${CMDLINE_PARAMS[*]}"
    CMDLINE_CHANGED="yes"
else
    die "$CMDLINE_FILE not found: is this the Pi? (--cmdline-file PATH to point elsewhere)"
fi

# ---- 6. no login console drawing over the framebuffer
step "6. getty on tty1 off"
getty_enabled="$(systemctl is-enabled getty@tty1.service 2> /dev/null || true)"
getty_active="$(systemctl is-active getty@tty1.service 2> /dev/null || true)"
echo "getty@tty1: ${getty_enabled:-unknown}, ${getty_active:-unknown}"
if [ -n "$DRY_RUN" ] || [ "$getty_enabled" = "enabled" ] || [ "$getty_active" = "active" ]; then
    run systemctl disable --now getty@tty1.service
else
    echo "already disabled and stopped"
fi

# ---- 7. the conductor service
step "7. estante-ctl.service"
unit="$(unit_for "$REPO")"
if [ -f "$UNIT_FILE" ] && [ "$(cat "$UNIT_FILE")" = "$unit" ]; then
    echo "$UNIT_FILE already up to date"
elif [ -n "$DRY_RUN" ]; then
    echo "would write $UNIT_FILE (ExecStart under $REPO)"
else
    printf '%s\n' "$unit" > "$UNIT_FILE"
    echo "wrote $UNIT_FILE (ExecStart under $REPO)"
fi
run systemctl daemon-reload
run systemctl enable estante-ctl.service
run systemctl restart estante-ctl.service
if [ -z "$DRY_RUN" ]; then
    wait_for_state || die "estante-ctl did not answer at $CTL_URL/state within ${WAIT_SECONDS}s (journalctl -u estante-ctl)"
    echo "estante-ctl answers at $CTL_URL/state"
fi

# ---- 8. reboot?
step "8. reboot"
if [ "$CMDLINE_CHANGED" = "yes" ]; then
    if [ -n "$DRY_RUN" ]; then
        echo "reboot needed after a real run: the kernel command line would change"
    else
        echo "REBOOT NEEDED: the kernel command line changed; run: sudo reboot"
        echo "(reboot needed for the 1920x1080 mode, the hidden cursor and no console blanking)"
    fi
else
    echo "no reboot needed"
fi

# ---- 9. self-test and the conductor's state
step "9. self-test and /state"
if [ -n "$DRY_RUN" ]; then
    echo "would run: $VENV/bin/python $REPO/scripts/selftest.py"
    echo "would run: curl -s $CTL_URL/state"
    exit 0
fi
"$VENV/bin/python" "$REPO/scripts/selftest.py" || die "self-test failed (see above)"
echo "state: $(curl -s --max-time 5 "$CTL_URL/state")"
echo
echo "Estante is installed. Ouvido and Home Assistant reach it at port 8360."
