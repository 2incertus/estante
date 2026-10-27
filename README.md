# Estante

Estante puts an Audiobookshelf library on a 7.3 inch Spectra 6 e-ink panel on the office bookshelf. Three screens take turns:

- **Now listening**: the cover of the audiobook in progress, its title, author, narrator and series, a progress bar with chapter ticks, the current chapter and the time left. With nothing in progress it shows the last finished book under LAST LISTENED, and with no history at all, NOTHING PLAYING.
- **The shelf**: every book in the Audiobooks library as a tile in shelf order (author, then series, then title), 50 covers to a page (or 32 larger covers, or the whole library on one screen, set per instance). Read books are in colour, the book being listened to has a red frame, unread books are pale grey ghosts.
- **Light novels**: the same shelf for the Light Novels library.

A small conductor service, estante-ctl, rotates the screens (every 30 minutes by default), turns the shelf pages, redraws Now listening when the chapter or progress visibly changes, and takes button presses and the message-waiting mark from the Ouvido daemon on the same Pi. Estante only ever reads Audiobookshelf with GET requests; it never writes progress, marks anything finished or starts a scan.

## Hardware

- Pimoroni Inky Impression 7.3" (2025 edition): 800x480, Spectra 6 inks (black, white, yellow, red, blue, green), driven by the `inky` library's `InkyE673` class. A full refresh takes up to about 32 seconds.
- Raspberry Pi 3B+ running Raspberry Pi OS Bookworm, shared with Ouvido (microphone, blink(1), buttons).
- Pins: Estante uses SPI0 (BCM 8, 10, 11), DC 22, RESET 27, BUSY 17 and I2C 2/3. Ouvido owns the buttons on BCM 5, 6, 16 and 24.

## How it fits InkyPi

Estante runs inside [InkyPi](https://github.com/fatihak/InkyPi) (GPL-3.0, tested at commit `df5fac4`) rather than beside it:

- Two InkyPi plugins, `plugins/now_listening` and `plugins/the_shelf`, subclass InkyPi's `BasePlugin`. The shelf plugin is used twice, once per library, so the playlist holds three instances: "Now listening", "The shelf" and "Light novels". Their settings pages are InkyPi's own, at `/plugin/the_shelf` and `/plugin/now_listening`.
- `plugins/estante_lib` holds everything else: the Audiobookshelf client and cover cache, the shelf model, the shared state file (`/var/lib/estante/state.json`), the Spectra 6 palette and the two renderers. Every image comes out as 800x480 in exactly the six driver colours, so the inky driver's own quantize pass changes nothing and the panel shows what was rendered.
- `ctl/estante_ctl.py` (the estante-ctl service, port 8360, stdlib only) owns rotation. It asks InkyPi to draw an instance through InkyPi's `POST /display_plugin_instance`, writes the shelf page into the state file before each shelf turn, and polls Audiobookshelf every 5 minutes while Now listening is up.
- InkyPi's own playlist cycle is parked at its maximum, 24 hours. Every display call from estante-ctl resets InkyPi's timer, so InkyPi's cycle never fires while estante-ctl is running.

## The Ouvido contract

Ouvido calls estante-ctl on the Pi. All calls are POST with a 2 second timeout and no auth, and all three answer 202 at once; the redraw runs in the background because a Spectra 6 refresh takes up to about 32 seconds.

- `http://127.0.0.1:8360/swap`, no body: button C pressed, show the next screen.
- `http://127.0.0.1:8360/show` with `{"screen":"the_shelf"}`: button C held, show that screen. The screen is an instance name or its slug (`now_listening`, `the_shelf`, `light_novels`).
- `http://127.0.0.1:8360/badge` with `{"message_waiting":bool,"dnd":bool,"from":"Jasmine"}` and an optional `"count":int`, sent whenever a flag changes. The footer then reads "Message from Jasmine waiting" or shows QUIET.
- `GET http://127.0.0.1:8360/state` returns `{"screen","screens","badge","busy","last_display","next_rotation","rotate_minutes"}`.

Recado never calls Estante; its waiting message reaches the panel through Ouvido's badge call. Ports on the Pi: InkyPi 80, Estante 8360, Ouvido 8600.

estante-ctl listens on 127.0.0.1 by default. Ouvido's container uses host networking, so loopback reaches it, and the routes have no auth, so nothing else on the network should. Widen `listen` (to `0.0.0.0` or the Pi's LAN address) only if a caller on another machine needs it and the network is trusted.

## Install on the Pi

In this order:

1. Install Ouvido's Docker container first, following Ouvido's own instructions.
2. Install InkyPi with its own installer, which enables SPI and I2C, then reboot:

   ```bash
   git clone https://github.com/fatihak/InkyPi.git ~/InkyPi
   cd ~/InkyPi && sudo bash install/install.sh
   sudo reboot
   ```

3. Clone Estante to `/opt/estante` (the repository is private, so the Pi needs a deploy key or a token for this):

   ```bash
   sudo git clone https://github.com/2incertus/estante.git /opt/estante
   ```

4. Run the installer with the Audiobookshelf address and an API token:

   ```bash
   cd /opt/estante
   sudo scripts/install.sh --abs-url http://192.168.68.86:13378 --abs-token ...
   ```

`scripts/install.sh` is safe to run again; each step says what it did or that it found the work already done. It links the three plugin folders into `/usr/local/inkypi/src/plugins/`, adds `ABS_URL` and `ABS_TOKEN` to InkyPi's `.env` when they are missing (the file behind InkyPi's API Keys page, which sits next to the real InkyPi `src` folder, so `~/InkyPi/.env` after the steps above), creates `/var/lib/estante` and `/etc/estante/estante.json`, restarts InkyPi, sets InkyPi's plugin cycle to 24 hours through its settings form, adds the three playlist instances (looking up the Audiobooks and Light Novels library ids from Audiobookshelf), installs and starts `estante-ctl.service`, and finally runs `scripts/selftest.py` with InkyPi's Python and Pillow. The self-test renders the three screens from the recorded fixtures, never touches the panel, and prints `selftest: ok`.

## /etc/estante/estante.json

estante-ctl reads this file (path from `ESTANTE_CONFIG`, set in the service unit). Missing keys take the defaults shown.

| Key | Default | Meaning |
|-----|---------|---------|
| `listen` | `127.0.0.1` | address estante-ctl listens on; loopback is enough for Ouvido (host networking), widen it only for a trusted caller on another machine, as the routes have no auth |
| `port` | `8360` | its port; Ouvido expects 8360 |
| `inkypi_url` | `http://127.0.0.1:80` | where InkyPi answers |
| `inkypi_device_json` | `/usr/local/inkypi/src/config/device.json` | InkyPi's config, read for the playlist's instances |
| `inkypi_env` | empty | InkyPi's `.env`, read for `ABS_URL` and `ABS_TOKEN`; empty = derived from the real location of InkyPi's src; the installer writes the explicit path |
| `playlist` | `Default` | the InkyPi playlist whose instances rotate |
| `rotate_minutes` | `30` | time each screen stays up |
| `poll_minutes` | `5` | how often Now listening checks Audiobookshelf |
| `min_redraw_minutes` | `10` | least time between two Now listening redraws |
| `state_dir` | `/var/lib/estante` | state file and cover cache; the plugins read it here too, so leave it |

After editing, run `sudo systemctl restart estante-ctl`.

## Development on the ryzen

The panel is not needed for development. The loop:

```bash
scripts/dev-inkypi.sh                 # one time: clone InkyPi into .inkypi/, build its venv, link the plugins
.venv/bin/pytest tests                # the whole suite, including InkyPi in dev mode with a mock display
.venv/bin/python scripts/preview.py --screen shelf   # render from the live Audiobookshelf into out/
scripts/dev-inkypi.sh run             # InkyPi dev mode on http://127.0.0.1:18080/ against the fixtures
NODE_PATH=/home/ubuntu/node_modules node scripts/shoot-inkypi.js   # screenshots into out/shots/
```

`scripts/preview.py` reads the token from `~/config/planetario.env` and writes two PNGs per screen: the exact driver colours and an approximation of how the panel looks. `scripts/dev-inkypi.sh run --live` serves InkyPi against the `ABS_URL` and `ABS_TOKEN` in `.inkypi/.env` instead of the fixtures. InkyPi's own `inkypi.py --dev` insists on port 8080, which is taken on the ryzen, so `run` serves the same app on port 18080 and puts the dev checkout's config back when it stops. `scripts/install.sh --dev-http-only` runs just the settings and playlist steps of the installer against that dev server (`INKYPI_URL=http://127.0.0.1:18080`, with `--audiobooks-id` and `--novels-id` when no Audiobookshelf is reachable). `scripts/make_icons.py` redraws the two plugin icons.

## Known limits

- The look on the real panel is not verified yet. Every pixel is a driver colour and the driver pass is a no-op in tests, but how Spectra 6 inks read on the shelf has to be checked once the panel arrives.
- Leave InkyPi's image settings (saturation, contrast, sharpness, brightness) at 1.0. Any other value makes InkyPi blend the image before the driver sees it, and flat areas will dither. The installer sets all four to 1.0 when it saves InkyPi's settings, and the self-test prints a warning if any has drifted since.
- InkyPi's own daily playlist tick only matters if estante-ctl is dead; then the screens would rotate once a day.
- Covers come from Audiobookshelf the first time each shelf page is drawn, and a mosaic pulls the whole library at once. The first pass over both libraries fetches about 900 covers (311 audiobooks and 597 light novels), several minutes over WiFi; after that they come from the cache in `/var/lib/estante/covers`. estante-ctl waits up to 10 minutes for InkyPi to finish one draw; a first mosaic slower than that counts as a failed draw, and the screen is tried again on its next turn with the covers fetched so far already cached.
- Estante screens are landscape only; InkyPi's orientation must stay horizontal.

## License

GPL-3.0, as the plugins subclass InkyPi's GPL-3.0 code. EB Garamond and Inter are under the SIL Open Font License 1.1; the text is in `plugins/estante_lib/fonts/OFL.txt`.
