# Estante v2 (HDMI screen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the e-ink target with Mathias's KYY K3 travel monitor (15.6", 1920x1080 IPS, mini HDMI) driven by the Raspberry Pi 3B+ on the office shelf: one always-on composition, "Now listening" on the left and the whole cover wall of one library on the right, with a swap between the audiobook and light-novel walls, Recado's mark, and a night/presence sleep rule driven by Home Assistant.

**Architecture:** `estante_lib` keeps its stdlib core (`abs`, `model`, `state`) and text helpers. A new Pillow layer draws the 1920x1080 composition in full colour (`theme`, `render_now_column`, `render_wall`, `render_screen`) and a small `display_fb` module writes it to the Pi's `/dev/fb0` (no X, no browser, DPMS blank through sysfs). The conductor `estante-ctl` (port 8360, LAN) owns the loop: poll Audiobookshelf, rerender on change, swap walls, hold the badge and the screen power/dim state; HA calls `/screen` for the night rule; Ouvido calls `/swap`, `/show`, `/badge` by voice. InkyPi and everything Spectra 6 are retired (git tag `inkypi-v1`).

**Tech Stack:** Python 3.13 on the Pi (Raspberry Pi OS Lite Trixie, aarch64) in `/opt/estante/.venv` with Pillow 11 (PyPI aarch64 wheel); Python 3.12/3.13 on the ryzen for dev; stdlib `urllib`, `http.server`, `threading`, `fcntl`; Linux framebuffer via sysfs + `/dev/fb0`; systemd; Home Assistant `rest_command` + automations.

**Spec:** the decisions below (Mathias, 2026-11-12, via AskUserQuestion) plus section 8 of `/srv/appdata/html-gallery/ouvido.html` for the parts that carry over (what Now listening shows, read/listening/unread meaning, sorting, badge). The previous plan `docs/superpowers/plans/2026-10-27-estante.md` documents the reusable core.

## Decisions taken 2026-11-12 (Mathias)

- Screen: KYY K3, Amazon B088TLQR3K, 15.6" 1920x1080 60 Hz IPS, mini HDMI + USB-C (DP alt) + USB-C power, no battery, smart cover as stand, speakers. No e-ink panel will be bought.
- Host: the Pi 3B+ (192.168.68.76, hostname `mathias`, user `admin`, ryzen key authorized) over HDMI; the screen is powered by its own 5 V adapter, never the Pi's USB ports.
- Layout: Now listening always on the left; the whole cover wall of one library on the right; a swap toggles the wall between Audiobooks and Light Novels. Nothing rotates away.
- Night: screen off at night and on when the office is in use, through Home Assistant; dimmed in the evening.

## Calls made by the planner (not agreed by Mathias; stated in the handoff)

- Framebuffer, not a browser: the Pi draws PNG-like frames straight to `/dev/fb0`; Chromium on a 1 GB Pi next to Ouvido's wake-word daemon is too heavy. HDMI mode is forced on the kernel command line (`video=HDMI-A-1:1920x1080@60D`) so the framebuffer exists even when the screen is off or unplugged at boot.
- Dark theme by default (near-black background, warm off-white type); a `paper` theme (the e-ink look) is available via config and both are previewed for him.
- Wall tiles: whole library on screen, 21 x 15 cells for 311 audiobooks (56 px covers) and 29 x 21 for 597 light novels (39 px); read in full colour, listening full colour with a 3 px red frame, unread greyscale at low brightness (a ghost on the dark theme).
- Polling: Audiobookshelf every 60 s; the composition rerenders when the now-listening record changed (`lastUpdate`), when the wall library changed, when the badge changed, or every 15 min for the wall. No redraw floor (an LCD does not flash).
- Screen power: `/screen {"on": bool, "dim": 0..1}`; off = DPMS via `/sys/class/graphics/fb0/blank` (fallback: paint black); dim = software brightness multiply before the blit. Policy (hours, presence, evening) lives in HA; the Pi only obeys.
- Walls are configured in `/etc/estante/estante.json` (`walls`: ordered list of `{name, library_id}`); `/show` accepts a wall name (`audiobooks`, `light_novels`) or its library id. Ouvido's phrases send exactly those two names.
- The conductor listens on `0.0.0.0:8360` (HA on the N100 must reach `/screen`); no auth, LAN only, the homelab norm.
- Retire InkyPi files from the tree (plugins `now_listening`, `the_shelf`, `inkypi_glue`, `palette` quantize paths stay only where the wall ghost needs them, `dev-inkypi.sh`, `shoot-inkypi.js`, `test_inkypi_dev.py`, `test_plugins.py`); `git tag inkypi-v1` keeps them.
- The Pi's IP is a DHCP lease; HA uses the IP until Mathias reserves it on the router (his action, flagged in the README and the handoff).

## Contracts (frozen with Ouvido 2026-11-12)

- Ouvido (voice, on the Pi, host networking) calls: `POST http://127.0.0.1:8360/swap` ("swap the shelf/wall"), `POST /show {"screen":"light_novels"}` or `{"screen":"audiobooks"}`, `POST /badge {"message_waiting":bool,"dnd":bool,"from":"Jasmine"}`. All 202.
- HA calls `POST http://192.168.68.76:8360/screen {"on":bool,"dim":0..1}` (202) through a `rest_command`.
- `GET /state` returns `{"wall","walls","badge","screen":{"on","dim"},"busy","last_render","now_listening":{"item_id","title","pct"}}`.
- Ports on the Pi: 8360 Estante, 8600 Ouvido. No GPIO used by Estante. HDMI and `/dev/fb0` belong to Estante.

## Global Constraints

- No em dashes and no emojis anywhere: code, comments, copy, docs, commits, test names.
- Python: 4-space indent, LF, compatible with 3.12 and 3.13. `estante_lib.abs`, `.model`, `.state`, `.display_fb` and `ctl/estante_ctl.py` import only the stdlib; Pillow only in `.text`, `.theme`, `.render_*`, `.palette`.
- Shell: `#!/bin/bash`, `set -e`, every variable quoted.
- Canvas: 1920x1080 RGB. Left column x 0..620 (Now listening), divider at x 630, wall x 650..1880, y 110..1030 for tiles. Every coordinate scales by `s = min(W/1920, H/1080)` so another 16:9 size does not crash.
- Audiobookshelf: GET only, never a route that writes progress. Credentials live in `/etc/estante/estante.json` (`abs_url`, `abs_token`, file mode 600) on the Pi and come from `~/config/planetario.env` on the ryzen for previews and fixtures. Tokens never land in git.
- Copy strings, exact: `NOW LISTENING`, `LAST LISTENED`, `NOTHING PLAYING`, `ESTANTE`, `read by {narrator}`, `{n} finished in {year}`, `{read} read  ·  {listening} listening  ·  {unread} to go`, `{library}, by author`, `{pct}% of the shelf read`, `Message from {from} waiting`, `{n} messages from {from} waiting`, `Message waiting`, `{n} messages waiting`, `QUIET`, `{h}h {mm}m left`.
- Fonts: the vendored EB Garamond and Inter (`plugins/estante_lib/fonts/`, OFL).
- Pi: nothing heavier than one render per minute; the renderer must finish a full frame in under 3 s on the 3B+ (measure in Task 4). Never touch Ouvido's container, its ports or GPIO.
- Ryzen CPU: tests and previews only (seconds); no long jobs.
- Repo `~/estante`, branch `v2` from `main`, commits `feat(estante): ...` with no Co-Authored-By trailer. Fixtures are the real sanitized data already in `tests/fixtures/`.

## Review Focus

1. Screen unplugged or powered off at boot or during the day: the framebuffer must still exist (forced mode) and writes must not fail; when HA turns the screen back on, the frame is redrawn immediately (Task 2 `test_unblank_triggers_redraw`, Task 0 hardware check).
2. Audiobookshelf down for hours: the last frame stays on screen, the conductor keeps polling, the badge and `/screen` still work (Task 2 `test_poll_failure_keeps_last_frame`).
3. Podcasts, finished-only libraries, books with 820 chapters, blank authors, long titles, pale covers: handled by the carried-over model and text helpers; the wall must not crash on a library with zero or thousands of items (Task 1 `test_wall_grid_for_zero_and_many`).
4. Two callers at once (Ouvido swap during a render, HA screen-off during a swap): one worker thread, a pending-action slot that keeps user actions, and screen state applied after the render in flight (Task 2 `test_screen_off_during_render_wins`).
5. Pi boot order: the service starts before Docker or HA are reachable and before the network is up; it must retry, not crash (Task 3 unit `Restart=always`, `test_startup_without_network_retries`).

---

## File map

```
plugins/estante_lib/
  abs.py, model.py, state.py, text.py, fonts/            (kept)
  palette.py                                              (kept; quantize/fade still used by the paper theme preview only)
  theme.py                                                NEW: dark and paper colour sets
  render_common.py                                        MODIFIED: footer in theme colours, no quantize step
  render_now.py                                           MODIFIED: becomes the left column renderer at 620x1080
  render_wall.py                                          NEW: the cover wall (replaces render_shelf.py)
  render_screen.py                                        NEW: composes column + wall, applies dim
  display_fb.py                                           NEW: sysfs geometry, blit, blank/unblank, fake backend
ctl/estante_ctl.py                                        REWRITTEN: walls, render loop, /screen; no InkyPi
ctl/estante.example.json, ctl/estante-ctl.service         MODIFIED
scripts/preview.py                                        MODIFIED: 1920x1080, --theme, --wall, --dim, --badge
scripts/install.sh                                        REWRITTEN for the Pi (venv, cmdline.txt, getty, service)
scripts/selftest.py                                       MODIFIED: renders the composition, checks timing
scripts/record_fixtures.py                                kept
ha/estante_screen.yaml                                    NEW: rest_command + automations (reference copy of what goes into HA)
README.md                                                 REWRITTEN
tests/ test_theme.py, test_render_wall.py, test_render_screen.py, test_display_fb.py, test_ctl.py (rewritten), test_install.py (updated)
Removed: plugins/now_listening/, plugins/the_shelf/, plugins/estante_lib/inkypi_glue.py, plugins/estante_lib/render_shelf.py, scripts/dev-inkypi.sh, scripts/shoot-inkypi.js, tests/test_inkypi_dev.py, tests/test_plugins.py, tests/test_render_shelf.py, tests/fake_inkypi.py
```

## Task batches

- Task 0 (owner, on the Pi, before dispatch): force the HDMI mode, free tty1, create the venv, verify `/dev/fb0` geometry and `blank`.
- Batch A: Task 1 (theme, wall, column, composition, display_fb). One opus implementer.
- Batch B: Task 2 (conductor v2) in a worktree, in parallel with A (consumes only the signatures below).
- Batch C: Task 3 (install, service, preview, selftest, README) after A and B.
- Task 4 (owner): install on the Pi, measure render time, look at the screen when it is plugged in.
- Task 5: HA rest_command + automations (ha-dev rules), after Task 4 proves `/screen`.

---

### Task 0: Pi preparation (owner)

- [ ] Back up `/boot/firmware/cmdline.txt` to `cmdline.txt.estante-bak`; append ` video=HDMI-A-1:1920x1080@60D vt.global_cursor_default=0 consoleblank=0` to the single line (idempotent check first).
- [ ] `sudo systemctl disable --now getty@tty1.service` (login stays on SSH and serial).
- [ ] `sudo apt-get install -y python3-venv` is already satisfied; create `/opt/estante` owned by `admin` later in Task 3 (no code yet).
- [ ] Reboot (Ouvido said a reboot is fine); verify: `/dev/fb0` exists, `/sys/class/graphics/fb0/virtual_size` is `1920,1080`, `bits_per_pixel` 32, `stride` 7680, `card0-HDMI-A-1/enabled` is `enabled`; `echo 4 | sudo tee /sys/class/graphics/fb0/blank` then `echo 0` succeed and flip `card0-HDMI-A-1/dpms`.
- [ ] Record the real values in the ledger; they become the defaults in `display_fb.py` tests.

### Task 1: Theme, wall, column, composition, framebuffer writer

**Files:**
- Create: `plugins/estante_lib/theme.py`, `plugins/estante_lib/render_wall.py`, `plugins/estante_lib/render_screen.py`, `plugins/estante_lib/display_fb.py`
- Modify: `plugins/estante_lib/render_common.py`, `plugins/estante_lib/render_now.py`
- Delete: `plugins/estante_lib/render_shelf.py`, `plugins/estante_lib/inkypi_glue.py`, `plugins/now_listening/`, `plugins/the_shelf/`, `tests/test_render_shelf.py`, `tests/test_plugins.py`, `tests/test_inkypi_dev.py`, `tests/fake_inkypi.py` (fake_inkypi only after Task 2 lands; see Interfaces)
- Test: `tests/test_theme.py`, `tests/test_render_wall.py`, `tests/test_render_screen.py`, `tests/test_display_fb.py`, update `tests/test_render_now.py`

**Interfaces:**
- Produces `estante_lib.theme`:
  - `@dataclass(frozen=True) Theme(name, bg, ink, muted, accent, series, mark, frame, paper_tile)` with RGB tuples
  - `DARK = Theme("dark", bg=(18,18,20), ink=(236,233,224), muted=(150,150,144), accent=(214,69,58), series=(120,160,230), mark=(90,170,110), frame=(214,69,58), paper_tile=(40,40,44))`
  - `PAPER = Theme("paper", bg=(236,233,224), ink=(26,28,31), muted=(110,110,104), accent=(168,34,30), series=(38,78,160), mark=(40,96,52), frame=(168,34,30), paper_tile=(222,219,210))`
  - `THEMES = {"dark": DARK, "paper": PAPER}`, `get_theme(name) -> Theme` (KeyError lists names)
- Produces `estante_lib.render_wall`:
  - `@dataclass WallView(title: str, subtitle: str, books: list[ShelfBook], covers: dict[str, Image], counts: dict, badge_free: bool = True)`
  - `grid_for(n: int, area: tuple[int,int], gap: int = 3) -> tuple[int,int,int]` (cols, rows, cell) as before: `cols = ceil(sqrt(n * w / h))`, `rows = ceil(n / cols)`, `cell = min(w // cols, h // rows)`; `n == 0` -> `(1, 1, min(w, h))`
  - `render_wall(view: WallView, theme: Theme, size: tuple[int,int] = (1230, 1080), scale: float = 1.0) -> Image` (RGB). Header at the top of the column: title Garamond 44 weight 500 at (0, 48) anchor `lm`; counts Inter 20 weight 500 at (W, 36) anchor `rm`; subtitle Inter 16 in `theme.series` at (W, 66) anchor `rm`; rule at y 86. Tiles in x 0..W, y 110..1030: `grid_for(len(books), (W, 920))`, tile = cell - 3, grid centred horizontally and top-aligned; cover `ImageOps.fit` square; read: full colour; listening: full colour + 3 px `theme.frame` outside the tile (drawn after all tiles); unread: greyscale, brightness x 0.35 on dark (x 0.55 on paper) blended 50% toward `theme.paper_tile`; missing cover: solid `theme.paper_tile` with the same read/unread treatment. Footer: rule at y 1044, `{pct}% of the shelf read` Inter 18 at (0, 1064) anchor `lm`, `ESTANTE` Inter 16 weight 700 in `theme.mark` at (W, 1064) anchor `rm`.
- Modifies `estante_lib.render_now`: `render_now_column(view: NowListeningView, theme: Theme, size: tuple[int,int] = (620, 1080), scale: float = 1.0) -> Image` (RGB, full colour, no quantize). Label at y 56 (Inter 18 weight 600, `theme.accent`); cover contained in 520x520 at (50, 90); title `fit_text(title, SERIF, 48, 30, 520, 2, 500)` first baseline 650, 1.05 em; author Garamond 28 at +14; `read by` Garamond 22; series Garamond 22 in `theme.series`; progress bar x 50..570 at y 900 height 14 outline `theme.ink` 2 px, fill `theme.accent`, ticks thinned to 96; chapter line Inter 20 at y 948; time line Inter 20 at y 980; footer rule y 1024 and text y 1052 via `draw_footer` (default left `{n} finished in {year}`, badge replaces it; the `ESTANTE` mark is NOT drawn in this column, the wall carries it). `render_now_listening` (the 800x480 e-ink entry point) is deleted along with its P-mode tests; the tests are rewritten for the column.
- Modifies `estante_lib.render_common`: `draw_footer(draw, theme, x0, x1, y_rule, y_text, default_left, badge, scale=1.0, mark=True)`; `finish()` removed.
- Produces `estante_lib.render_screen`:
  - `@dataclass ScreenView(now: NowListeningView, wall: WallView, theme: Theme, dim: float = 1.0)`
  - `render_screen(view: ScreenView, size: tuple[int,int] = (1920, 1080)) -> Image` (RGB): fills `theme.bg`, pastes the column at (0, 0), draws a 1 px divider in `theme.muted` at x 630 from y 40 to 1040, pastes the wall at (650, 0); if `dim < 1.0`, multiplies brightness by `dim` (`ImageEnhance.Brightness`), clamped to 0.1..1.0.
- Produces `estante_lib.display_fb` (stdlib only):
  - `@dataclass FbInfo(width: int, height: int, bpp: int, stride: int)`
  - `read_info(sysfs_dir: str = "/sys/class/graphics/fb0") -> FbInfo` (parses `virtual_size`, `bits_per_pixel`, `stride`; `stride` missing -> `width * bpp // 8`)
  - `class Framebuffer(device: str = "/dev/fb0", sysfs_dir: str = "/sys/class/graphics/fb0")` with `info: FbInfo`, `show(img) -> None` (accepts a PIL image or raw RGB bytes + size: resize with `Image.LANCZOS` when the size differs, convert to the fb format: 32 bpp -> BGRX bytes, 16 bpp -> RGB565 little-endian; write row by row honouring `stride`; `os.pwrite` on an fd kept open), `blank(on: bool) -> bool` (writes `4` or `0` to `sysfs_dir/blank`; False when the write fails; the caller paints black as a fallback), `close()`.
  - `class FileFramebuffer(path: str, info: FbInfo)` with the same interface backed by a regular file (tests and the ryzen preview); `blank` records calls in `.blank_calls`.
  - To keep `display_fb` PIL-free, `show` takes `(rgb_bytes: bytes, size: tuple[int,int])`; the conductor converts with `img.tobytes()` (Task 2). Provide `rgb_to_bgrx(rgb: bytes) -> bytes` and `rgb_to_rgb565(rgb: bytes) -> bytes` as pure functions (bytes/bytearray loops are too slow for 2 MP on a 3B+; use `int.from_bytes` tricks or `memoryview` slicing: 32 bpp = insert a zero byte every 3 and swap R/B, which is a 4-way slice assignment on a bytearray; 16 bpp may be slow and only needs to be correct).

- [ ] **Step 1: Write the failing tests** (pixel assertions on RGB images using theme colours; `grid_for(311,(1230,920)) == (21,15,58)`, `grid_for(597,(1230,920)) == (29,21,42)`, `grid_for(0,(1230,920)) == (1,1,920)`; the listening tile frame pixel; unread tile mean brightness below read tile mean; the composition size 1920x1080 and divider pixel; dim 0.5 halves the mean; `rgb_to_bgrx(b"\x01\x02\x03") == b"\x03\x02\x01\x00"`; `FileFramebuffer.show` writes `stride * height` bytes with the right pixel at (x, y); `read_info` from a temp sysfs dir; `test_wall_grid_for_zero_and_many` with 0 and 5000 books)
- [ ] **Step 2: Run them, see ImportError**
- [ ] **Step 3: Implement; delete the InkyPi files listed above (keep `tests/fake_inkypi.py` until Task 2 merges, then Task 3 deletes it)**
- [ ] **Step 4: `.venv/bin/pytest -q` green, zero warnings** (test_ctl.py will still be the v1 conductor's tests at this point; they must still pass because Task 2 replaces them on its own branch)
- [ ] **Step 5: Commit** `feat(estante): 1080p composition, cover wall, themes, framebuffer writer; retire InkyPi plugins`

### Task 2: Conductor v2

**Files:**
- Rewrite: `ctl/estante_ctl.py`, `ctl/estante.example.json`, `ctl/estante-ctl.service`
- Test: `tests/test_ctl.py` (rewritten), `tests/fake_fb.py` (or reuse `display_fb.FileFramebuffer`)

**Interfaces:**
- Consumes `abs.AbsClient`, `abs.CoverCache`, `model.*`, `state.*`, `theme.get_theme`, `render_now.NowListeningView`, `render_wall.WallView`, `render_screen.ScreenView/render_screen`, `display_fb.Framebuffer/FileFramebuffer/read_info`.
- `DEFAULTS = {"listen": "0.0.0.0", "port": 8360, "abs_url": "", "abs_token": "", "theme": "dark", "walls": [{"name": "audiobooks", "library_id": ""}, {"name": "light_novels", "library_id": ""}], "poll_seconds": 60, "wall_refresh_minutes": 15, "state_dir": "/var/lib/estante", "fb_device": "/dev/fb0", "fb_sysfs": "/sys/class/graphics/fb0", "fb_file": "", "cover_width": 120, "now_cover_width": 520}` (`fb_file` non-empty = FileFramebuffer at that path with `fb_size` `[1920,1080]`, for the ryzen).
- `load_config(path)`; `resolve_walls(cfg, client) -> list[Wall]` fills empty `library_id`s by matching the ABS library name case-insensitively ("audiobooks" -> "Audiobooks", "light_novels" -> "Light Novels"); unknown names raise with the list of libraries.
- `class Conductor(threading.Thread)`: `request(action, screen=None)` for `swap` | `show` | `redraw`; `set_badge(incoming) -> (badge, changed)`; `set_screen(on: bool | None, dim: float | None) -> dict`; `snapshot()`; `run()`; `stop()`. Loop every 0.5 s: pending action first (latest user action wins; `redraw` never replaces swap/show); else poll when due (`poll_seconds`): fetch now-listening; rerender when its `(item_id, last_update, finished)` changed, when the wall changed, when the badge changed, when `wall_refresh_minutes` elapsed, or when the screen was just turned on. A render = `render_screen(ScreenView(...))` on the worker thread, then `fb.show(img.tobytes(), img.size)` unless the screen is off. `set_screen(on=False)` -> `fb.blank(True)` (fallback: show a black frame); `set_screen(on=True)` -> `fb.blank(False)` + immediate redraw; `dim` persisted in state and applied on the next render (which is triggered immediately). Covers through `CoverCache` (wall tiles at `cover_width`, the now cover at `now_cover_width`). ABS failures: log, keep the last frame, retry next poll; the first frame after boot is a plain `theme.bg` with "ESTANTE" until ABS answers.
- Routes: `POST /swap` 202 `{"queued":"swap"}`; `POST /show {"screen": name|library_id}` 202 or 404 with `walls`; `POST /badge` 202 `{"badge", "redraw"}`; `POST /screen {"on"?, "dim"?}` 202 `{"screen": {"on","dim"}}` (400 on bad JSON or dim outside 0..1); `GET /state` 200 `{"wall","walls","badge","screen","busy","last_render","now_listening"}`; 404/405 otherwise; unexpected errors 500 JSON.
- Unit: `User=root`, `After=network-online.target`, `Wants=network-online.target`, `ExecStart=/opt/estante/.venv/bin/python /opt/estante/ctl/estante_ctl.py`, `Environment=ESTANTE_CONFIG=/etc/estante/estante.json`, `Restart=always`, `RestartSec=5`, `WantedBy=multi-user.target`.
- Tests use `FileFramebuffer` and the fake ABS: startup frame written; poll renders after a progress override; swap changes the wall and the rendered wall header; show by name and by id; badge redraw; screen off blanks and stops blits, on unblanks and redraws (`test_unblank_triggers_redraw`); `test_poll_failure_keeps_last_frame` (fake ABS `fail_with=500`); `test_screen_off_during_render_wins`; `test_startup_without_network_retries` (abs url pointing at a closed port, conductor alive, `/state` answers, first ABS success renders); snapshot keys exact; dim validation.

- [ ] Steps: failing tests, implement, green, commit `feat(estante): conductor v2 drives the HDMI framebuffer: walls, poll loop, /screen`.

### Task 3: Install, preview, self-test, README

**Files:** `scripts/install.sh` (rewrite), `scripts/preview.py`, `scripts/selftest.py`, `README.md`, delete `scripts/dev-inkypi.sh`, `scripts/shoot-inkypi.js`, `tests/fake_inkypi.py`, update `tests/test_install.py`, `tests/test_selftest.py`.

- `install.sh` (Pi, root, idempotent, echo each step): (1) `apt-get install -y python3-venv libopenjp2-7` if missing; (2) `python3 -m venv /opt/estante/.venv` and `pip install pillow>=11` (wheel); (3) `/etc/estante/estante.json` from the example when absent, filled with `--abs-url`, `--abs-token` (mode 600), library ids resolved by name through the ABS API (curl + python3 json); (4) `/var/lib/estante/covers`; (5) cmdline.txt: ensure the three parameters from Task 0 (append once; keep a backup); (6) `systemctl disable --now getty@tty1`; (7) install and enable `estante-ctl.service`; (8) print whether a reboot is needed (cmdline changed); (9) run `scripts/selftest.py` in the venv and `curl -s http://127.0.0.1:8360/state`.
- `preview.py`: `--env`, `--theme dark|paper`, `--wall audiobooks|light_novels`, `--dim`, `--badge`, `--dnd`, `--out out/`; writes `screen-<theme>-<wall>[...].png` and, when `--fb-file` is given, also writes the raw framebuffer bytes through `FileFramebuffer` and reads them back to prove the round trip; gallery copies `estante-screen-dark.png`, `estante-screen-paper.png`, `estante-screen-dark-novels.png`, `estante-screen-dark-badge.png`, `estante-screen-dark-dim.png`.
- `selftest.py`: renders the composition from fixtures, times it (prints seconds; warns above 3 s), checks `read_info` on the real sysfs when present, exits 0/1.
- README: what it is now; hardware (KYY K3, cable, 5 V adapter); the Pi (IP, user, reservation TODO); install; config keys; the HTTP contract (Ouvido and HA); the HA automation summary; dev loop on the ryzen; known limits (first render fetches up to 900 covers; the monitor must wake on signal, verify; DHCP lease).

### Task 4: Install on the Pi (owner)

- `git clone` (or rsync) `~/estante` to the Pi at `/opt/estante`, run `install.sh`, confirm `/state`, time a render from the journal, look at the screen once it is plugged in; copy a phone photo or `fb` dump to the gallery.

### Task 5: Home Assistant (ha-dev rules: check existing office automations first, verify triggers)

- `rest_command.estante_screen` (POST `http://192.168.68.76:8360/screen`, JSON payload with `on` and `dim` templates) in `configuration.yaml` (or `rest_command.yaml` include), `rest_command.reload`.
- Automations: `Estante screen: office in use` (trigger `input_boolean.office_occupied` on, condition time 07:00 to 23:30 -> on, dim 1.0 before 19:00 else 0.6); `Estante screen: office empty` (off after 15 min off); `Estante screen: night` (23:30 -> off); `Estante screen: morning` (07:00 and office occupied -> on). Keep the reference YAML in `ha/estante_screen.yaml`. Verify by firing each and reading `/state` on the Pi.
