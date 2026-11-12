# Estante

Estante puts Mathias's Audiobookshelf library on a screen on the office bookshelf. One composition stays up all day, 1920x1080:

- **Now listening**, on the left: the cover of the audiobook in progress, its title, author and narrator, a progress bar with chapter ticks, the current chapter, the time left and how many books were finished this year. With nothing in progress it shows the last finished book under LAST LISTENED, and with no history at all, NOTHING PLAYING. The footer carries the message mark from Ouvido ("Message from Jasmine waiting", or QUIET).
- **The shelf**, on the right: one library as books on four wooden boards. Unread books stand as spines in their cover's dominant colour with the title reading bottom to top (and the author's surname at the top when the spine is wide enough); finished books face out with their covers; the books being listened to face out with a red ribbon and a red progress line along the base. Books come in runs: every volume of a series in order, or the rest of one author's books by title, each run with a label on the board under it (`THE WHEEL OF TIME · 14 of 15`, or the author). Runs with a book in progress come first, then runs with finished books (latest first), then the rest by author surname. The library fills several sections (about five for the audiobooks); a section turns every two minutes, on "next shelf" (`POST /swap`), and jumps once to the section of a book that has just started in Now listening. A line on top counts read, listening and to go, with `shelf k of n` under it; the footer says how many books this section holds, whose names it runs between and when the next one comes.

A small conductor service, estante-ctl, polls Audiobookshelf every minute, redraws when something on screen changed (and the library every 15 minutes), turns the shelf, and writes finished frames straight to the Linux framebuffer `/dev/fb0`: no X, no browser. Ouvido (the voice daemon on the same Pi) and Home Assistant talk to it over HTTP on port 8360. Estante only ever reads Audiobookshelf with GET requests; it never writes progress, marks anything finished or starts a scan.

## Hardware

- **Screen:** KYY K3 portable monitor (Amazon B088TLQR3K), 15.6 inch IPS, 1920x1080 at 60 Hz. It takes the Pi's HDMI through its mini HDMI port and is powered by its own 5 V USB-C adapter, never from the Pi's USB ports (the 3B+ cannot feed it). It stands on its smart cover rotated 180 degrees, so the installer sets `rotate: 180` and every frame is turned before it is written.
- **Host:** Raspberry Pi 3B+, Raspberry Pi OS Lite (Trixie, aarch64, Python 3.13), shared with Ouvido's Docker container. HDMI and `/dev/fb0` belong to Estante; Estante uses no GPIO and never touches Ouvido's container or ports.

## The Pi

- Address `192.168.68.76` (hostname `mathias`), user `admin` with passwordless sudo; the ryzen's key is authorized (`ssh admin@192.168.68.76`).
- The address is a DHCP lease. **TODO (Mathias):** reserve `192.168.68.76` for the Pi on the router, since Home Assistant calls that address.
- Ports: 8360 Estante, 8600 Ouvido.

## Install on the Pi

In this order:

1. Ouvido's container is already installed and running; nothing here touches it.
2. Refresh the package lists:

   ```bash
   sudo apt-get update
   ```

3. Put the checkout at `/opt/estante`, either with git (the repository is private, so the Pi needs a deploy key or a token) or with rsync from the ryzen:

   ```bash
   # on the Pi
   sudo mkdir -p /opt/estante && sudo chown admin:admin /opt/estante
   # on the ryzen
   rsync -a --delete --exclude .venv --exclude out --exclude .claude --exclude .superpowers \
       ~/estante/ admin@192.168.68.76:/opt/estante/
   ```

4. Run the installer and type the Audiobookshelf API token at its prompt (nothing is echoed):

   ```bash
   cd /opt/estante
   sudo scripts/install.sh [--abs-url http://192.168.68.86:13378] [--rotate 180]
   ```

   Without a terminal, give the token through the environment or a file instead:

   ```bash
   read -rs ESTANTE_ABS_TOKEN; export ESTANTE_ABS_TOKEN
   sudo --preserve-env=ESTANTE_ABS_TOKEN scripts/install.sh
   unset ESTANTE_ABS_TOKEN
   # or: a file holding only the token, mode 600
   sudo scripts/install.sh --abs-token-file /root/abs-token
   ```

   Never put the token on the command line: sudo logs the whole command to the journal (the `COMMAND=` line), `ps` shows it for the whole run and the shell keeps it in its history. That is why `--abs-token TOKEN` is refused on a real run and only accepted with `--dry-run`.

5. Reboot when it says `REBOOT NEEDED`: `sudo reboot`. The installer decides this from the running kernel (`/proc/cmdline`), not from its own edit, so it keeps saying it at the end of every run until the Pi has rebooted with the three parameters.

`scripts/install.sh` is safe to run again; every step prints what it did or that the work was already done, and `--dry-run` prints the whole plan without changing anything (it still looks the library up in Audiobookshelf, which is a GET). The steps:

1. `apt-get install -y` whichever of `python3-venv`, `libopenjp2-7`, `libjpeg62-turbo` and `curl` is missing.
2. `python3 -m venv /opt/estante/.venv` when absent, then `pip install "pillow>=11"` with `PIP_ONLY_BINARY=:all:`, so Pillow always comes as the aarch64 wheel and is never compiled on the Pi.
3. `/etc/estante/estante.json`, mode 600, written to a temp file and renamed into place: from `ctl/estante.example.json` the first time. On later runs the existing file is kept: `abs_token` stays as it is unless this run is given a token (`--abs-token-file` or `ESTANTE_ABS_TOKEN`; a rerun does not prompt when the config already holds one), while `abs_url`, `rotate` and the audiobooks wall's `library_id` keep their existing values unless `--abs-url`, `--rotate` or `--library-id` is given (a flag always wins). When the audiobooks wall has no id yet, the id of the library named "Audiobooks" is looked up through `GET /api/libraries`; the installer stops with the list of libraries when there is none. A token this run was given is checked first with one `GET /api/me`: a 401 or 403 stops the installer with that status before the config is touched, so a typo never reaches the service. The token goes to curl on stdin and is never echoed.
4. `/var/lib/estante/covers`, the state file's directory and the cover cache.
5. `/boot/firmware/cmdline.txt` gains `video=HDMI-A-1:1920x1080@60D vt.global_cursor_default=0 consoleblank=0` (a parameter already there with another value is replaced in place; the line stays one line). The first edit keeps the original as `cmdline.txt.estante-bak`. The forced mode matters: the K3 sends no EDID, so without it the framebuffer comes up at 1024x768 and only exists while a screen answers.
6. `systemctl disable --now getty@tty1.service`, so the login console does not draw over the framebuffer.
7. `ctl/estante-ctl.service` goes to `/etc/systemd/system/` with `/opt/estante` replaced by the real checkout path (its `ExecStartPre` unbinds the kernel console from the framebuffer, `echo 0 > /sys/class/vtconsole/vtcon1/bind`, so kernel messages never paint over the frame; a missing `vtcon1` is ignored), then `daemon-reload` (when the unit changed) and `enable`. The service is restarted only when the unit or the config changed, started when it is not running, and otherwise left alone ("unchanged, not restarted"; after a code update, `sudo systemctl restart estante-ctl`). Then a wait of up to 60 seconds for `GET http://127.0.0.1:8360/state` to answer 200; when it never does, the error carries the `REBOOT NEEDED` line too while the running kernel lacks the forced mode (without it there may be no `/dev/fb0` yet).
8. Whether a reboot is needed: yes while the running kernel's `/proc/cmdline` lacks any of the three parameters. The same `REBOOT NEEDED` line closes the run.
9. `scripts/selftest.py` with the venv's Python, then `/state` printed.

Flags: `--abs-token-file PATH` (else `ESTANTE_ABS_TOKEN`, else the existing config's token, else the prompt), `--abs-url` (default: the existing config's, else `http://192.168.68.86:13378`, the N100 on the LAN), `--rotate 180|0` (default: the existing config's, else 180 with a reminder printed), `--library-id` (default: the existing config's, else looked up), `--cmdline-file` (another kernel command line, for tests), `--dry-run`, and `--abs-token TOKEN` for dry runs only. A flag value starting with `--` counts as missing, so `--abs-token --dry-run` stops instead of starting a real run.

The self-test renders the composition from the recorded fixtures through a local fake Audiobookshelf, times each frame through the same path as the service, the 180 degree turn and the framebuffer conversion included (the budget on the 3B+ is 3 seconds; it warns above), and reads the framebuffer's geometry from sysfs: a depth other than 16 or 32 bpp fails, a size other than 1920x1080 warns that the forced mode only applies after the reboot. It never writes to the screen and ends with `selftest: ok`.

Logs: `journalctl -u estante-ctl -f`.

## /etc/estante/estante.json

estante-ctl reads this file (path from `ESTANTE_CONFIG`, set in the service unit). Missing keys take the defaults shown. After editing, `sudo systemctl restart estante-ctl`.

| Key | Default | Meaning |
|-----|---------|---------|
| `listen` | `0.0.0.0` | address the HTTP API listens on; Home Assistant on another machine must reach `/screen`; no auth, LAN only |
| `port` | `8360` | its port; Ouvido and Home Assistant expect 8360 |
| `abs_url` | empty | Audiobookshelf base URL; the installer writes `http://192.168.68.86:13378` |
| `abs_token` | empty | Audiobookshelf API token (read with GET only); the reason the file is mode 600 |
| `theme` | `dark` | `dark` (near-black, warm off-white type) or `paper` (the e-ink look) |
| `walls` | the audiobooks wall | ordered list of `{"name", "library_id", "title"}`; an empty `library_id` is resolved from the library whose name matches `name` (`light_novels` matches "Light Novels") |
| `poll_seconds` | `60` | how often Audiobookshelf is read |
| `wall_refresh_minutes` | `15` | the wall's library is read again at least this often |
| `state_dir` | `/var/lib/estante` | `state.json` (wall, shelf section per library, last book seen, badge, screen power), `covers/` and `spines.json` (each cover's spine colour, computed once) |
| `canvas` | `[1920, 1080]` | size the composition is drawn at; scaled to the framebuffer when they differ |
| `rotate` | `0` (installer: `180`) | `0` or `180`; 180 for the monitor standing upside down |
| `fb_device` | `/dev/fb0` | the framebuffer device |
| `fb_sysfs` | `/sys/class/graphics/fb0` | its sysfs directory: geometry, and `blank` for screen off |
| `fb_file` | empty | when set, frames go to this regular file instead of the device (development) |
| `fb_size`, `fb_bpp` | `[1920, 1080]`, `16` | the shape of `fb_file` |
| `cover_width` | `160` | width of the shelf's cached covers (face-outs and spine colours); above the 138 px face-out so covers are only ever scaled down |
| `now_cover_width` | `520` | width of the Now listening cover |
| `shelf_minutes` | `2` | the shelf turns to its next section this often (not while the screen is off) |

A second wall, to show by voice with `/show` (one wall is one library; `/swap` turns the shelf of the wall on screen):

```json
"walls": [
  {"name": "audiobooks", "library_id": "b14de8f5-...", "title": "The shelf"},
  {"name": "light_novels", "library_id": "", "title": "Light novels"}
]
```

## The HTTP contract

All on port 8360, JSON in and out, no auth.

From **Ouvido** (on the Pi, host networking, so `127.0.0.1`):

- `POST /badge` with `{"message_waiting": bool, "dnd": bool, "from": "Jasmine"}` and an optional `"count": int`, sent whenever a flag changes. Answers 202 with the stored badge; the screen redraws when it changed.
- `POST /screen` with `{"on": bool}`: Ouvido may turn the screen on or off.
- `POST /swap`, no body: the next shelf section, now, and the two-minute timer starts again; past the last section the shelf starts at the first. 202 with `{"queued": "next_shelf", "section": k}` (k counts from 1). Ouvido's phrase "next shelf" calls it.
- `POST /show` with `{"section": k}` or `{"screen": "shelf k"}`: that section (from 1), timer restarted. 202 with `{"queued": "show", "section": k}`, 404 with `{"sections": n}` when there is no such section, 400 when `section` is not a whole number. A new book in Now listening does not move the shelf within a minute of a `/swap` or a section `/show`.
- `POST /show` with `{"screen": "audiobooks"}` or `{"screen": "light_novels"}` (a wall name or its library id): show that wall, at the section it was last on. 202, or 404 with the list of walls when there is no such wall.

From **Home Assistant** (`http://192.168.68.76:8360`):

- `POST /screen` with `{"on": bool, "dim": 0..1}`. Off powers the panel down through the framebuffer's `blank` (or paints black when the driver refuses); on wakes it and redraws; `dim` multiplies the frame's brightness (floored at 0.1). Either key may be left out. 202 with the resulting screen state, 400 when a value has the wrong type or range. Power and dim survive a restart.

For anyone:

- `GET /state` returns `{"wall", "walls", "shelf": {"section", "sections", "next_turn"}, "badge", "screen": {"on", "dim"}, "busy", "last_render", "now_listening": {"item_id", "title", "pct"}}`; `shelf.section` counts from 1 and `next_turn` is an ISO time in UTC.

## Home Assistant

The policy lives in Home Assistant; the Pi only obeys. A `rest_command.estante_screen` posts `{"on", "dim"}` to `http://192.168.68.76:8360/screen`, and automations on the office presence boolean `input_boolean.office_occupied` drive it: office occupied between 07:00 and 23:30 turns the screen on (dimmed to 0.6 after 19:00), the office empty (the boolean is already debounced by 15 minutes) turns it off, 23:30 turns it off whatever the presence, 07:00 turns it on when the office is already occupied, and 19:00 dims it when occupied. Times are Home Assistant's local time.

Nothing of this is configured in Home Assistant yet. The rest_command and the automations above are the plan; their reference YAML is applied in Home Assistant in its own step, after the Pi answers on 8360. Until then the screen stays on at full brightness.

## Development on the ryzen

No Pi or screen is needed:

```bash
.venv/bin/pytest -q                          # the whole suite, about 60 s
ESTANTE_STATE_DIR=/tmp/estante/state .venv/bin/python scripts/preview.py \
    --env ~/config/planetario.env --theme dark --section 2 --badge --out out --fb-file out/fb.raw
.venv/bin/python scripts/selftest.py         # what the installer runs on the Pi
bash scripts/install.sh --dry-run --library-id ID --abs-token x   # the install plan, nothing changed
```

The dry run accepts `--abs-token` because it writes nothing; drop `--library-id` and give the token as `ESTANTE_ABS_TOKEN` or `--abs-token-file` to see the real library lookup (a GET).

`scripts/preview.py` reads `ABS_URL` and `ABS_TOKEN` from the `--env` file (GET only, the token is never printed) and renders the live composition as estante-ctl would draw it, with the audiobooks shelf: `--section N` (from 1; past the last one it stops with the count), `--theme dark|paper`, `--dim 0..1`, `--badge` (a message from Jasmine waiting), `--dnd` (quiet hours). It writes `out/screen-<theme>[-sN][-badge][-dnd][-dim].png` (`-sN` from section 2 on). With `--fb-file` it also writes the frame as 1920x1080 RGB565 framebuffer bytes, reads them back, decodes them and prints `fb round trip: ok` when every pixel matches the frame within the 5/6/5 bit rounding. Covers are cached under `$ESTANTE_STATE_DIR/covers` (default `out/state`) and their spine colours in `spines.json` beside them, so only the first run fetches and computes them. Gallery copies go to `/srv/appdata/html-gallery/ouvido/` (docs.library.icu).

## Known limits

- The K3 sends no EDID, so the 1920x1080 mode is forced on the kernel command line; until the reboot after the first install the framebuffer is 1024x768 and frames are scaled down to it.
- The first shelf render fetches every cover of the library from Audiobookshelf, about 300 for the audiobooks (about 600 more for a light-novel wall), a few minutes over WiFi, and computes each spine colour once; after that they come from `/var/lib/estante/covers` and `spines.json`. Until Audiobookshelf has answered once the screen shows the ESTANTE mark.
- Whether the K3 wakes by itself when the HDMI signal returns after a screen-off still has to be checked on the real panel, along with how the look (both themes, the dim level) reads on it.
- The Pi's address is a DHCP lease until it is reserved on the router (see above).
- The renderer's 3 second budget per frame, and 1.5 seconds for a shelf section alone, are measured by the self-test on the Pi itself. A section is slowest the first time it is drawn (every spine is new); its spines are then kept in memory.
- Labels of one-book runs are often wider than the book: they are cut short with an ellipsis (`MA…`) rather than overlap the next run.
- `GET /state` reports `now_listening.pct` as a whole percent, rounded down (47.9% reads 47).

## License

GPL-3.0 (see `LICENSE`). The InkyPi version this grew from is kept under the git tag `inkypi-v1`. EB Garamond and Inter are under the SIL Open Font License 1.1; the text is in `plugins/estante_lib/fonts/OFL.txt`.
