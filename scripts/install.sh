#!/bin/bash
# Install Estante on the Raspberry Pi that drives the HDMI screen. Run as root from the
# Estante checkout (normally /opt/estante):
#
#   sudo scripts/install.sh [--abs-url URL] [--rotate 180|0]
#
# and type the Audiobookshelf token at the prompt (or give it as ESTANTE_ABS_TOKEN through
# sudo --preserve-env=ESTANTE_ABS_TOKEN, or as --abs-token-file PATH). The token is never
# taken from the command line on a real run: sudo logs the command line to the journal,
# ps shows it for the whole run and the shell keeps it in its history.
#
# Every step is idempotent and says what it did, so running it again is safe.
# --dry-run prints what it would do and changes nothing (no root needed); it still
# looks the library up in Audiobookshelf, which is a GET.
#
# It never touches Docker or Ouvido's container, ports or GPIO.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/.venv"
# ESTANTE_CONFIG_DIR is for tests only: the service unit reads /etc/estante/estante.json
CONFIG_DIR="${ESTANTE_CONFIG_DIR:-/etc/estante}"
CONFIG_FILE="$CONFIG_DIR/estante.json"
STATE_DIR="/var/lib/estante"
UNIT_FILE="/etc/systemd/system/estante-ctl.service"
SERVICE="estante-ctl.service"
CTL_URL="http://127.0.0.1:8360"
WAIT_SECONDS="${ESTANTE_WAIT_SECONDS:-60}"
PROC_CMDLINE="${ESTANTE_PROC_CMDLINE:-/proc/cmdline}"
DEFAULT_ABS_URL="http://192.168.68.86:13378"
LIBRARY_NAME="Audiobooks"
PACKAGES=(python3-venv libopenjp2-7 libjpeg62-turbo curl)
CMDLINE_PARAMS=("video=HDMI-A-1:1920x1080@60D" "vt.global_cursor_default=0" "consoleblank=0")

ABS_URL=""
ABS_TOKEN=""
ABS_TOKEN_FLAG=""
ABS_TOKEN_FILE=""
ROTATE=""
LIBRARY_ID=""
CMDLINE_FILE="/boot/firmware/cmdline.txt"
DRY_RUN=""
CMDLINE_CHANGED="no"
CONFIG_CHANGED="no"

usage() {
    cat <<EOF
usage: sudo scripts/install.sh [--abs-url URL] [--rotate 180|0] [--library-id ID]
                               [--abs-token-file PATH] [--cmdline-file PATH] [--dry-run]

The Audiobookshelf API token, written to $CONFIG_FILE (mode 600) and never echoed,
comes from the first of:
  --abs-token-file PATH  a file holding the token (keep it mode 600)
  ESTANTE_ABS_TOKEN      the environment; through sudo: sudo --preserve-env=ESTANTE_ABS_TOKEN ...
  a prompt               when stdin is a terminal
  --abs-token TOKEN      only with --dry-run (sudo would log it, ps would show it)

  --abs-url URL        Audiobookshelf base URL (default: the existing config's, else $DEFAULT_ABS_URL)
  --rotate 180|0       180 when the monitor stands upside down (default: the existing config's, else 180)
  --library-id ID      id of the "$LIBRARY_NAME" library (default: the existing config's, else looked up)
  --cmdline-file PATH  kernel command line to edit (default $CMDLINE_FILE)
  --dry-run            print what would be done, change nothing

Environment: ESTANTE_WAIT_SECONDS (default 60), how long to wait for estante-ctl.
EOF
}

die() {
    echo "install: $1" >&2
    exit 1
}

usage_error() {
    echo "install: $1" >&2
    exit 2
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

# One value of an existing config: abs_url, rotate or audiobooks_id (the library id of
# the wall named audiobooks). Prints nothing when the file, the key or the value is missing
# or unreadable.
config_get() {
    local program
    program="$(cat <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
except (OSError, ValueError):
    sys.exit(0)
if not isinstance(cfg, dict):
    sys.exit(0)
if key == "audiobooks_id":
    walls = [w for w in cfg.get("walls") or [] if isinstance(w, dict) and w.get("name") == "audiobooks"]
    value = walls[0].get("library_id") if walls else ""
else:
    value = cfg.get(key)
if value is not None and value != "":
    print(value)
PY
)"
    python3 -c "$program" "$1" "$2"
}

# The config estante-ctl reads, on stdout: the example, then an existing config over it,
# then abs_url, abs_token (from $ESTANTE_ABS_TOKEN), rotate and the audiobooks wall's
# library id as the installer decided them. Other keys and walls are kept.
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
    if wall.get("name") == "audiobooks":
        wall["library_id"] = library_id
cfg["walls"] = walls
json.dump(cfg, sys.stdout, indent=2)
print()
PY
}

# Write $CONFIG_FILE (mode 600) from render_config, through a temp file in the same
# directory and a rename, so a reader never sees half a file. The token comes from
# $ESTANTE_ABS_TOKEN. Sets CONFIG_CHANGED to yes or no. Args: abs_url library_id rotate
write_config() {
    local tmp
    mkdir -p "$CONFIG_DIR"
    tmp="$(mktemp "$CONFIG_DIR/.estante.json.XXXXXX")"
    chmod 600 "$tmp"
    if ! render_config "$REPO/ctl/estante.example.json" "$CONFIG_FILE" "$1" "$2" "$3" > "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if [ -f "$CONFIG_FILE" ] && cmp -s "$tmp" "$CONFIG_FILE"; then
        rm -f "$tmp"
        CONFIG_CHANGED="no"
        echo "$CONFIG_FILE already up to date"
    else
        CONFIG_CHANGED="yes"
        mv -f "$tmp" "$CONFIG_FILE"
        echo "wrote $CONFIG_FILE (abs_url, abs_token, rotate, audiobooks wall)"
    fi
    chmod 600 "$CONFIG_FILE"
    echo "mode 600"
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

# The CMDLINE_PARAMS the running kernel (the command line in $1, /proc/cmdline) lacks,
# space separated; all of them when it cannot be read.
missing_params() {
    local line=""
    local want word found
    local -a words
    local -a missing=()
    if [ -r "$1" ]; then
        IFS= read -r line < "$1" || true
    fi
    read -ra words <<< "$line"
    for want in "${CMDLINE_PARAMS[@]}"; do
        found=""
        for word in "${words[@]}"; do
            if [ "$word" = "$want" ]; then
                found="yes"
            fi
        done
        if [ -z "$found" ]; then
            missing+=("$want")
        fi
    done
    echo "${missing[*]}"
}

# True when the running kernel (command line in $1) lacks any of CMDLINE_PARAMS.
reboot_needed() {
    [ -n "$(missing_params "$1")" ]
}

reboot_line() {
    echo "REBOOT NEEDED: the running kernel lacks $(missing_params "$PROC_CMDLINE"); run: sudo reboot"
}

# The service unit with /opt/estante replaced by the checkout at $1 (any characters).
unit_for() {
    python3 -c '
import sys
with open(sys.argv[1], encoding="utf-8") as f:
    sys.stdout.write(f.read().replace("/opt/estante", sys.argv[2]))
' "$REPO/ctl/estante-ctl.service" "$1"
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
        --abs-url|--abs-token|--abs-token-file|--rotate|--library-id|--cmdline-file)
            # a value that looks like a flag is a missing value: `--abs-token --dry-run`
            # must not swallow --dry-run and start a real run
            if [ "$#" -lt 2 ] || [ "${2#--}" != "$2" ]; then
                echo "install: $1 needs a value" >&2
                usage >&2
                exit 2
            fi
            ;;
    esac
    case "$1" in
        --abs-url) ABS_URL="${2%/}"; shift 2 ;;
        --abs-token) ABS_TOKEN_FLAG="$2"; shift 2 ;;
        --abs-token-file) ABS_TOKEN_FILE="$2"; shift 2 ;;
        --rotate)
            [ "$2" = "180" ] || [ "$2" = "0" ] || usage_error "--rotate must be 180 or 0, not \"$2\""
            ROTATE="$2"
            shift 2
            ;;
        --library-id) LIBRARY_ID="$2"; shift 2 ;;
        --cmdline-file) CMDLINE_FILE="$2"; shift 2 ;;
        --dry-run) DRY_RUN="yes"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [ -n "$ABS_TOKEN_FLAG" ] && [ -z "$DRY_RUN" ]; then
    usage_error "--abs-token is only accepted with --dry-run: on a real run sudo logs it to the \
journal (the COMMAND= line), ps shows it for the whole run and the shell keeps it in its \
history. Type it at the prompt (just run sudo scripts/install.sh), or export ESTANTE_ABS_TOKEN \
and run sudo --preserve-env=ESTANTE_ABS_TOKEN scripts/install.sh, or use --abs-token-file PATH."
fi
if [ -z "$DRY_RUN" ] && [ "$(id -u)" -ne 0 ]; then
    die "run as root: sudo scripts/install.sh ..."
fi

# ---- the token: --abs-token (dry run only), --abs-token-file, ESTANTE_ABS_TOKEN, a prompt
if [ -n "$ABS_TOKEN_FLAG" ]; then
    ABS_TOKEN="$ABS_TOKEN_FLAG"
elif [ -n "$ABS_TOKEN_FILE" ]; then
    [ -f "$ABS_TOKEN_FILE" ] && [ -r "$ABS_TOKEN_FILE" ] \
        || usage_error "cannot read --abs-token-file $ABS_TOKEN_FILE"
    IFS= read -r ABS_TOKEN < "$ABS_TOKEN_FILE" || true
    ABS_TOKEN="${ABS_TOKEN//[[:space:]]/}"
    [ -n "$ABS_TOKEN" ] || usage_error "--abs-token-file $ABS_TOKEN_FILE is empty"
    mode="$(stat -c '%a' "$ABS_TOKEN_FILE" 2> /dev/null || true)"
    if [ -n "$mode" ] && [ "${mode: -2}" != "00" ]; then
        echo "install: warning: $ABS_TOKEN_FILE is mode $mode; chmod 600 it" >&2
    fi
elif [ -n "${ESTANTE_ABS_TOKEN:-}" ]; then
    ABS_TOKEN="$ESTANTE_ABS_TOKEN"
elif [ -t 0 ]; then
    read -rs -p "Audiobookshelf API token: " ABS_TOKEN
    echo >&2
    [ -n "$ABS_TOKEN" ] || usage_error "no token typed"
else
    usage_error "no Audiobookshelf token: set ESTANTE_ABS_TOKEN (through sudo: \
sudo --preserve-env=ESTANTE_ABS_TOKEN), pass --abs-token-file PATH, or run from a terminal \
to be prompted"
fi

# ---- what an earlier install left in the config; flags override it
EXISTING_URL="$(config_get "$CONFIG_FILE" abs_url)"
EXISTING_ROTATE="$(config_get "$CONFIG_FILE" rotate)"
EXISTING_LIBRARY_ID="$(config_get "$CONFIG_FILE" audiobooks_id)"

if [ -n "$DRY_RUN" ]; then
    echo "dry run: nothing will be changed"
fi
echo "checkout: $REPO"
if [ -n "$ABS_URL" ]; then
    echo "Audiobookshelf: $ABS_URL (from --abs-url)"
elif [ -n "$EXISTING_URL" ]; then
    ABS_URL="${EXISTING_URL%/}"
    echo "Audiobookshelf: $ABS_URL (from the existing config)"
else
    ABS_URL="$DEFAULT_ABS_URL"
    echo "Audiobookshelf: $ABS_URL (the default)"
fi
if [ -n "$ROTATE" ]; then
    echo "rotate $ROTATE (from --rotate)"
elif [ "$EXISTING_ROTATE" = "180" ] || [ "$EXISTING_ROTATE" = "0" ]; then
    ROTATE="$EXISTING_ROTATE"
    echo "rotate $ROTATE (from the existing config; --rotate to change it)"
else
    ROTATE="180"
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

# ---- 3. the config, with the Audiobooks library id
step "3. $CONFIG_FILE"
if [ -n "$LIBRARY_ID" ]; then
    echo "library id $LIBRARY_ID (from --library-id)"
elif [ -n "$EXISTING_LIBRARY_ID" ]; then
    LIBRARY_ID="$EXISTING_LIBRARY_ID"
    echo "library id $LIBRARY_ID (from the existing config; --library-id to change it)"
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
    ESTANTE_ABS_TOKEN="$ABS_TOKEN" write_config "$ABS_URL" "$LIBRARY_ID" "$ROTATE"
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

# ---- 7. the conductor service: restarted only when its unit or its config changed
step "7. $SERVICE"
unit="$(unit_for "$REPO")"
UNIT_CHANGED="no"
if [ -f "$UNIT_FILE" ] && [ "$(cat "$UNIT_FILE")" = "$unit" ]; then
    echo "$UNIT_FILE already up to date"
elif [ -n "$DRY_RUN" ]; then
    echo "would write $UNIT_FILE (ExecStart under $REPO)"
else
    printf '%s\n' "$unit" > "$UNIT_FILE"
    UNIT_CHANGED="yes"
    echo "wrote $UNIT_FILE (ExecStart under $REPO)"
fi
if [ -n "$DRY_RUN" ]; then
    run systemctl daemon-reload
    run systemctl enable "$SERVICE"
    echo "would run: systemctl restart $SERVICE (only when the unit or the config changed; start when not running)"
else
    if [ "$UNIT_CHANGED" = "yes" ]; then
        run systemctl daemon-reload
    fi
    if systemctl is-enabled --quiet "$SERVICE" 2> /dev/null; then
        echo "$SERVICE already enabled"
    else
        run systemctl enable "$SERVICE"
    fi
    if [ "$UNIT_CHANGED" = "yes" ] || [ "$CONFIG_CHANGED" = "yes" ]; then
        run systemctl restart "$SERVICE"
    elif ! systemctl is-active --quiet "$SERVICE"; then
        run systemctl start "$SERVICE"
    else
        echo "$SERVICE unchanged, not restarted (after a code update: sudo systemctl restart estante-ctl)"
    fi
    wait_for_state \
        || die "estante-ctl did not answer at $CTL_URL/state within ${WAIT_SECONDS}s (journalctl -u estante-ctl)"
    echo "estante-ctl answers at $CTL_URL/state"
fi

# ---- 8. reboot? Decided by the running kernel, not by this run's edit
step "8. reboot"
if reboot_needed "$PROC_CMDLINE"; then
    echo "reboot needed: $PROC_CMDLINE lacks $(missing_params "$PROC_CMDLINE")"
else
    echo "no reboot needed: the running kernel has ${CMDLINE_PARAMS[*]}"
fi

# ---- 9. self-test and the conductor's state
step "9. self-test and /state"
if [ -n "$DRY_RUN" ]; then
    echo "would run: $VENV/bin/python $REPO/scripts/selftest.py"
    echo "would run: curl -s $CTL_URL/state"
else
    "$VENV/bin/python" "$REPO/scripts/selftest.py" || die "self-test failed (see above)"
    echo "state: $(curl -s --max-time 5 "$CTL_URL/state")"
    echo
    echo "Estante is installed. Ouvido and Home Assistant reach it at port 8360."
fi

# the last word of every run, until the running kernel has the parameters
if reboot_needed "$PROC_CMDLINE"; then
    echo
    reboot_line
fi
