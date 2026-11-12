# Estante v3: the shelf of spines (right side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cover-wall on the right of the screen with a literal bookshelf: four wooden boards per screen, unread books standing as coloured spines with their titles, finished and in-progress books turned face-out with their covers, the whole library spread over several shelf sections that turn every two minutes (or on "next shelf" by voice), and the shelf jumping to the section that holds the book Mathias just started.

**Architecture:** A new `render_spines` module replaces `render_wall` in the composition: it lays the ordered shelf (`model.build_shelf`) into sections of four boards by greedy packing, draws spines (dominant cover colour, rotated title) and face-outs (cover on a plinth, ribbon + progress line when in progress), and returns the section image plus section metadata. The conductor keeps one wall (audiobooks) but gains a section cursor persisted in state, a `shelf_minutes` timer, `/swap` = next section, and a jump to the current book's section when Now listening changes item. Previews, self-test, README and the design doc follow.

**Tech Stack:** as v2 (Python 3.12/3.13, Pillow, stdlib conductor), repo `~/estante` branch `v3` from `main` (cb7a765).

**Spec:** Mathias chose mock A on 2026-11-12 ("I like 1 the best"). Reference mock renderer (ideas and measurements, not production code): `/tmp/estante/mock2/mock2-reference.py` (function `mock_spines`, `spine_image`, `dominant`), its output `/srv/appdata/html-gallery/ouvido/estante-mock-a-spines.png`. Everything else from the v2 plan (`docs/superpowers/plans/2026-11-12-estante-screen.md`) stands: Now listening column, badge footer, `/screen`, HA, install.

## Calls made by the planner (stated in the handoff)

- Wooden boards stay (two-tone brown, 14 px) on both themes; the paper theme keeps paper background behind them.
- Spine width 22..52 px from duration (`18 + hours * 0.9`, clamped), spine height 178..206 px deterministic per book (hash of item id), so a book keeps its shape across renders. Face-out slot 150 px wide: cover fitted 138x138 on a dark plinth, bottom aligned; in-progress adds a red ribbon (12 px wide, 60 px long, notched) at the top right of the cover and a progress line along the base.
- Spine colour = dominant cover colour (saturation-weighted, skipping near-white and near-black), text colour black or off-white by luminance; a missing cover gets a muted colour from the title hash. Title in Garamond 22 (18 when the spine is under 30 px) reading bottom to top, ellipsized to the spine height minus 60 px; the author's last name in Inter 11 at the top when the spine is 34 px or wider.
- Organised by series (Mathias, 2026-11-12: "ideally organized by series or something smart"): books are grouped into runs. A series run holds every volume of one series in sequence order; the remaining books of one author form an author run (sorted by title). Runs are ordered by activity: runs with a book in progress first (most recent `lastUpdate` first), then runs with finished books (most recent `finishedAt` first), then the rest by author surname. A run is packed onto one board whenever it fits the board's width; a run wider than a board wraps and continues on the next board. Each run gets a shelf label on the board under it: Inter 11, letter-spaced caps, muted, `{SERIES NAME} · {read} of {n}` for a series run and `{AUTHOR}` for an author run; labels never overlap (a run narrower than its label shows a truncated label).
- Sections: boards 230 px pitch (y 110, 340, 570, 800), usable height 206, board 14 px; pieces packed left to right with a 2 px gap, wrapping to the next board, four boards per section; the number of sections falls out of the packing (about 4 for 311 books). Header subtitle `Audiobooks, by author  ·  shelf {k} of {n}`; footer left `{count} of {total} on this shelf  ·  {first author surname} to {last author surname}  ·  next shelf in {m} min`.
- Rotation: `shelf_minutes` (default 2) in config; `/swap` advances one section immediately and resets the timer; `/show {"screen": "shelf 3"}` or `{"section": 3}` selects a section (1-based); when Now listening changes to a different item that is on the shelf, the cursor jumps to its section once. The section cursor is persisted in state (`pages[library_id]` reused).
- Ouvido's phrase "next shelf" calls `POST /swap` (to be agreed with that session).
- The rows and series mocks are kept as future options in the gallery only; no code for them now.

## Global Constraints

As the v2 plan: no em dashes or emojis; 4-space Python; stdlib only in ctl/ and in abs/model/state; GET-only Audiobookshelf; copy strings exact; fonts EB Garamond and Inter; nothing heavy on the ryzen; commits `feat(estante): ...` with no trailer; zero warnings; TDD.

## Review Focus

1. A title longer than the spine: ellipsized, never clipped mid-glyph, never drawn outside the spine (Task 1 test).
2. A section with a single face-out book or an empty library: packing never divides by zero, header says shelf 1 of 1 (Task 1 test).
3. Rotation while Audiobookshelf is down: the cursor still turns and the last fetched shelf renders (Task 2 test).
4. The jump to the current book: happens once per item change, not on every poll, and never fights a manual `/swap` within the same minute (Task 2 test).
5. Pi budget: a section render must stay under 1.5 s on the 3B+ (dominant colours cached per cover file in `state_dir/spines.json`) (Task 1 timing test on the ryzen under 0.3 s; Task 3 self-test on the Pi).

---

### Task 1: render_spines

**Files:** Create `plugins/estante_lib/render_spines.py`; modify `plugins/estante_lib/render_screen.py` (compose with the spines renderer; `ScreenView.wall` becomes `ScreenView.shelf: ShelfSectionView`); delete `plugins/estante_lib/render_wall.py` and `tests/test_render_wall.py`; tests `tests/test_render_spines.py`, update `tests/test_render_screen.py`, `tests/conftest.py`, `scripts/preview.py`, `scripts/selftest.py`.

**Interfaces:**
- `@dataclass Piece(book: ShelfBook, kind: str, width: int, height: int)` with `kind` in `spine` | `face`
- `@dataclass Run(key: str, label: str, kind: str, books: list[ShelfBook])` with `kind` in `series` | `author`; `shelf_runs(books: list[ShelfBook], items_by_id: dict, progress: dict) -> list[Run]` in `estante_lib.model` (stdlib only): series runs from `seriesName` ("Name #seq"), author runs for the rest, ordered by activity as in the Calls (progress records give `lastUpdate` and `finishedAt`)
- `plan_sections(runs: list[Run], hours: dict[str, float], width: int = 1230, boards: int = 4, gap: int = 2, run_gap: int = 14) -> list[list[list[tuple[Run, list[Piece]]]]]` (sections -> boards -> placed runs with their pieces), pure and deterministic; a run that fits a board is never split; the label width is accounted for (`max(run width, 40 px)`)
- `spine_colour(cover: Image | None, title: str) -> tuple[int, int, int]` and `ColourCache(path)` with `get(item_id, updated_at, cover_path) -> rgb` persisted as JSON
- `draw_spine(piece, colour, theme) -> Image`, `draw_face(piece, cover: Image | None, pct: float | None, theme) -> Image`
- `@dataclass ShelfSectionView(title: str, subtitle: str, books: list[ShelfBook], hours: dict[str, float], pct: dict[str, float], covers: dict[str, Image], colours: dict[str, tuple], counts: dict, section: int, shelf_minutes: int, badge: dict)`
- `render_section(view: ShelfSectionView, theme: Theme, size=(1230, 1080), scale=1.0) -> tuple[Image, dict]` where the dict carries `sections`, `section`, `first_author`, `last_author`, `count`
- `section_of(books, item_id, hours) -> int | None` (which section holds a book)

Steps: failing tests (runs never split when they fit; ordering by activity with the fixture's in-progress series first; labels drawn under runs; packing never overflows width; deterministic heights; face-out for read and listening; spine text pixels present; ellipsis for a 120-char title; empty library; 311-book fixture gives 3 to 6 sections; timing under 0.3 s for one section with cached colours), implement, previews on both themes from the live library (`scripts/preview.py --section 1..n`) looked at and copied to the gallery as `estante-screen-dark.png` (section 1), `estante-screen-dark-s2.png`, `estante-screen-paper.png`, commit.

### Task 2: conductor rotation, swap, jump

**Files:** modify `ctl/estante_ctl.py`, `ctl/estante.example.json`, `tests/test_ctl.py`, README (config key, routes, voice phrase), `ha/` untouched.

**Interfaces:** `DEFAULTS["shelf_minutes"] = 2`; state `pages[library_id]` = current section (0-based); `/swap` 202 `{"queued": "next_shelf", "section": k}`; `/show` accepts `{"section": k}` (1-based) or `{"screen": "shelf k"}`, 404 when out of range with `sections`; `/state` gains `shelf: {"section": k, "sections": n, "next_turn": iso}`; the render loop turns the section when `shelf_minutes` elapsed; on an item change in Now listening, if `section_of(...)` is not None and differs, set it once and reset the timer; the cover loader fetches covers only for the face-out books of all sections plus the current section's spines colours (spine colours need the 120 px covers anyway, so keep the full cover load but compute colours lazily and cache them).

Steps: failing tests (turn after `shelf_minutes`; `/swap` advances and resets the timer; `/show` by section; jump once on item change; rotation while ABS is down keeps the last books; snapshot keys), implement, full suite, README, commit.

### Task 3 (owner): deploy

rsync to the Pi, restart, self-test timing on the Pi, look at the panel (framebuffer dump), agree "next shelf" with Ouvido, update the design doc section 8, memory, status.
