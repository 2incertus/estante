#!/bin/bash
# Install Estante into an existing InkyPi on the Raspberry Pi. Run as root from the
# Estante checkout (normally /opt/estante), after InkyPi's own installer and reboot:
#
#   sudo scripts/install.sh --abs-url http://192.168.68.86:13378 --abs-token TOKEN
#
# Every step is idempotent and says what it did, so running it again is safe.
#
# --dev-http-only runs only the HTTP steps (InkyPi settings and the three playlist
# instances) against a dev InkyPi (scripts/dev-inkypi.sh run), with INKYPI_URL set,
# the dev checkout as --inkypi and no root, symlinks or systemd.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INKYPI_URL="${INKYPI_URL:-http://127.0.0.1}"
WAIT_SECONDS="${ESTANTE_WAIT_SECONDS:-60}"
PLAYLIST="Default"
AUDIOBOOKS_NAME="Audiobooks"
NOVELS_NAME="Light Novels"
PLUGINS="estante_lib now_listening the_shelf"
CTL_URL="http://127.0.0.1:8360"

ABS_URL=""
ABS_TOKEN=""
INKYPI=""
DEV_HTTP_ONLY=""
AUDIOBOOKS_ID=""
NOVELS_ID=""

usage() {
    cat <<EOF
usage: sudo scripts/install.sh --abs-url URL --abs-token TOKEN [--inkypi DIR]
       scripts/install.sh --dev-http-only [--inkypi DIR] [--abs-url URL --abs-token TOKEN]
                          [--audiobooks-id ID] [--novels-id ID]

  --abs-url URL        Audiobookshelf base URL, e.g. http://192.168.68.86:13378
  --abs-token TOKEN    Audiobookshelf API token (read-only use)
  --inkypi DIR         InkyPi install dir (default /usr/local/inkypi; .inkypi with --dev-http-only)
  --dev-http-only      only save InkyPi settings and add the playlist instances, over HTTP
  --audiobooks-id ID   skip the Audiobookshelf lookup of the "$AUDIOBOOKS_NAME" library
  --novels-id ID       skip the Audiobookshelf lookup of the "$NOVELS_NAME" library

Environment: INKYPI_URL (default http://127.0.0.1), ESTANTE_WAIT_SECONDS (default 60).
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

while [ "$#" -gt 0 ]; do
    case "$1" in
        --abs-url|--abs-token|--inkypi|--audiobooks-id|--novels-id)
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
        --inkypi) INKYPI="${2%/}"; shift 2 ;;
        --dev-http-only) DEV_HTTP_ONLY="yes"; shift ;;
        --audiobooks-id) AUDIOBOOKS_ID="$2"; shift 2 ;;
        --novels-id) NOVELS_ID="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

need_abs=""
if [ -z "$DEV_HTTP_ONLY" ] || [ -z "$AUDIOBOOKS_ID" ] || [ -z "$NOVELS_ID" ]; then
    need_abs="yes"
fi
if [ -n "$need_abs" ] && { [ -z "$ABS_URL" ] || [ -z "$ABS_TOKEN" ]; }; then
    echo "install: --abs-url and --abs-token are both required" >&2
    usage >&2
    exit 2
fi

if [ -n "$DEV_HTTP_ONLY" ]; then
    INKYPI="${INKYPI:-$REPO/.inkypi}"
    DEVICE_JSON="$INKYPI/src/config/device_dev.json"
else
    INKYPI="${INKYPI:-/usr/local/inkypi}"
    DEVICE_JSON="$INKYPI/src/config/device.json"
    [ "$(id -u)" -eq 0 ] || die "run as root: sudo scripts/install.sh ..."
fi

for tool in curl jq; do
    command -v "$tool" > /dev/null || die "$tool is missing: sudo apt install $tool"
done

wait_for() {
    local url="$1"
    local deadline=$((SECONDS + WAIT_SECONDS))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)" = "200" ]; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# ---- 1. InkyPi must be installed first
if [ -z "$DEV_HTTP_ONLY" ]; then
    step "1. InkyPi at $INKYPI"
    if [ ! -d "$INKYPI/src/plugins" ]; then
        cat >&2 <<EOF
install: $INKYPI/src/plugins is missing, so InkyPi is not installed yet.
Install it first, then reboot and run this script again:
    git clone https://github.com/fatihak/InkyPi.git ~/InkyPi
    cd ~/InkyPi && sudo bash install/install.sh
    sudo reboot
EOF
        exit 1
    fi
    echo "found $INKYPI/src/plugins"

    # ---- 2. plugin symlinks
    step "2. link the plugins into InkyPi"
    for name in $PLUGINS; do
        ln -sfn "$REPO/plugins/$name" "$INKYPI/src/plugins/$name"
        echo "$INKYPI/src/plugins/$name -> $REPO/plugins/$name"
    done

    # ---- 3. credentials in InkyPi's .env
    # InkyPi runs src/inkypi.py through its real path, so its .env (the API Keys page)
    # lives next to the real src directory, not under the /usr/local/inkypi symlink.
    ENV_FILE="$(dirname "$(realpath "$INKYPI/src")")/.env"
    step "3. Audiobookshelf credentials in $ENV_FILE"
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    for pair in "ABS_URL=$ABS_URL" "ABS_TOKEN=$ABS_TOKEN"; do
        key="${pair%%=*}"
        if grep -q "^$key=" "$ENV_FILE"; then
            echo "$key already set, left as is"
        else
            # A hand-edited .env may lack its final newline; do not glue onto its last line.
            if [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
                echo >> "$ENV_FILE"
            fi
            echo "$pair" >> "$ENV_FILE"
            echo "$key added"
        fi
    done

    # ---- 4. state dir and estante.json
    step "4. /var/lib/estante and /etc/estante/estante.json"
    mkdir -p /var/lib/estante/covers /etc/estante
    if [ -f /etc/estante/estante.json ]; then
        echo "/etc/estante/estante.json exists, left as is"
    else
        jq --arg env "$ENV_FILE" --arg dev "$DEVICE_JSON" \
            '.inkypi_env = $env | .inkypi_device_json = $dev' \
            "$REPO/ctl/estante.example.json" > /etc/estante/estante.json
        echo "wrote /etc/estante/estante.json (inkypi_env $ENV_FILE)"
    fi

    # ---- 5. restart InkyPi so it loads the plugins
    step "5. restart inkypi.service"
    systemctl restart inkypi.service
    wait_for "$INKYPI_URL/" || die "InkyPi did not answer at $INKYPI_URL/ within ${WAIT_SECONDS}s"
    echo "InkyPi is up at $INKYPI_URL/"
else
    wait_for "$INKYPI_URL/" || die "InkyPi did not answer at $INKYPI_URL/ within ${WAIT_SECONDS}s"
    echo "dev InkyPi at $INKYPI_URL/ (config $DEVICE_JSON)"
fi

[ -f "$DEVICE_JSON" ] || die "$DEVICE_JSON is missing"

# ---- 6. park InkyPi's own playlist cycle at its maximum, 24 hours
step "6. InkyPi plugin cycle interval: 24 hours"
# The settings form posts every field, so send back what is configured now and change
# only the interval. estante-ctl's display calls reset InkyPi's timer, so this cycle
# only fires if estante-ctl is not running.
system_tz="$(timedatectl show -p Timezone --value 2> /dev/null || true)"
settings_args=()
while IFS= read -r field; do
    settings_args+=(--data-urlencode "$field")
done < <(jq -r --arg tz "${system_tz:-UTC}" '
    (.image_settings // {}) as $img
    | ["deviceName=\(.name // "InkyPi")",
       "orientation=\(.orientation // "horizontal")",
       "timezoneName=\(.timezone // $tz)",
       "timeFormat=\(.time_format // "12h")",
       "unit=hour",
       "interval=24",
       "saturation=\($img.saturation // 1.0)",
       "contrast=\($img.contrast // 1.0)",
       "sharpness=\($img.sharpness // 1.0)",
       "brightness=\($img.brightness // 1.0)"]
    + (if .inverted_image then ["invertImage=on"] else [] end)
    + (if .log_system_stats then ["logSystemStats=on"] else [] end)
    + (if (.display_type // "inky") == "inky"
       then ["inky_saturation=\($img.inky_saturation // 0.5)"] else [] end)
    | .[]' "$DEVICE_JSON")
reply="$(curl -s --max-time 30 -X POST "$INKYPI_URL/save_settings" "${settings_args[@]}")"
echo "$reply" | jq -e '.success == true' > /dev/null || die "save_settings failed: $reply"
echo "saved (plugin_cycle_interval_seconds 86400)"

# ---- 7. the three playlist instances
step "7. playlist \"$PLAYLIST\": Now listening, The shelf, Light novels"

library_id() {
    local name="$1"
    local libraries
    local id
    libraries="$(curl -sf --max-time 15 -H "Authorization: Bearer $ABS_TOKEN" "$ABS_URL/api/libraries")" \
        || die "could not list the libraries at $ABS_URL"
    id="$(echo "$libraries" | jq -r --arg name "$name" '.libraries[] | select(.name == $name) | .id' | head -n 1)"
    [ -n "$id" ] || die "no library named \"$name\" at $ABS_URL"
    echo "$id"
}

add_instance() {
    local plugin_id="$1"
    local name="$2"
    shift 2
    local refresh
    refresh="$(jq -cn --arg playlist "$PLAYLIST" --arg name "$name" \
        '{playlist: $playlist, instance_name: $name, refreshType: "interval", unit: "hour", interval: 1}')"
    local reply
    reply="$(curl -s --max-time 120 -X POST "$INKYPI_URL/add_plugin" \
        --data-urlencode "plugin_id=$plugin_id" \
        --data-urlencode "refresh_settings=$refresh" \
        "$@")"
    if echo "$reply" | jq -e '.success == true' > /dev/null 2>&1; then
        echo "added \"$name\""
    elif echo "$reply" | jq -e '(.error // "") | test("already exists")' > /dev/null 2>&1; then
        echo "\"$name\" is already there, left as is"
    else
        die "adding \"$name\" failed: $reply"
    fi
}

instance_exists() {
    jq -e --arg playlist "$PLAYLIST" --arg name "$1" \
        '.playlist_config.playlists[]? | select(.name == $playlist) | .plugins[]? | select(.name == $name)' \
        "$DEVICE_JSON" > /dev/null 2>&1
}

if instance_exists "Now listening"; then
    echo "\"Now listening\" is already there, left as is"
else
    add_instance now_listening "Now listening"
fi
if instance_exists "The shelf"; then
    echo "\"The shelf\" is already there, left as is"
else
    if [ -z "$AUDIOBOOKS_ID" ]; then
        AUDIOBOOKS_ID="$(library_id "$AUDIOBOOKS_NAME")"
    fi
    add_instance the_shelf "The shelf" --data-urlencode "library_id=$AUDIOBOOKS_ID" \
        --data-urlencode "layout=pages" --data-urlencode "title=The shelf"
fi
if instance_exists "Light novels"; then
    echo "\"Light novels\" is already there, left as is"
else
    if [ -z "$NOVELS_ID" ]; then
        NOVELS_ID="$(library_id "$NOVELS_NAME")"
    fi
    add_instance the_shelf "Light novels" --data-urlencode "library_id=$NOVELS_ID" \
        --data-urlencode "layout=pages" --data-urlencode "title=Light novels"
fi

if [ -n "$DEV_HTTP_ONLY" ]; then
    echo
    echo "dev HTTP steps done"
    exit 0
fi

# ---- 8. the conductor service
step "8. estante-ctl.service"
sed "s#/opt/estante#$REPO#g" "$REPO/ctl/estante-ctl.service" > /etc/systemd/system/estante-ctl.service
systemctl daemon-reload
systemctl enable estante-ctl.service > /dev/null
systemctl restart estante-ctl.service
wait_for "$CTL_URL/state" || die "estante-ctl did not answer at $CTL_URL/state within ${WAIT_SECONDS}s"
echo "estante-ctl is up: $(curl -s --max-time 5 "$CTL_URL/state")"

# ---- 9. self-test with InkyPi's Pillow
step "9. self-test"
if "$INKYPI/venv_inkypi/bin/python" "$REPO/scripts/selftest.py"; then
    echo
    echo "Estante is installed. Buttons arrive from Ouvido at $CTL_URL (swap, show, badge)."
else
    die "self-test failed (see above); the panel may show wrong colours"
fi
