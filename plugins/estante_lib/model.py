"""Shelf statuses and ordering, now-listening selection, visible key. Stdlib only."""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

STATUS_READ = "read"
STATUS_LISTENING = "listening"
STATUS_UNREAD = "unread"
LISTENING_MIN_PROGRESS = 0.01

_SERIES_RE = re.compile(r"^(.*?)\s*#\s*([0-9]+(?:\.[0-9]+)?)")
_NUMBERED_CHAPTER_RE = re.compile(r"^chapter\s+0*([0-9]+)$", re.IGNORECASE)
_LAST = "\uffff"


def progress_index(me):
    return {p["libraryItemId"]: p for p in me.get("mediaProgress", []) if p.get("libraryItemId")}


def status_of(item_id, progress):
    p = progress.get(item_id)
    if not p:
        return STATUS_UNREAD
    if p.get("isFinished"):
        return STATUS_READ
    if (p.get("progress") or 0) > LISTENING_MIN_PROGRESS:
        return STATUS_LISTENING
    return STATUS_UNREAD


def series_key(series_name):
    m = _SERIES_RE.match(series_name or "")
    if m:
        return (m.group(1).strip().lower(), float(m.group(2)))
    return ((series_name or "").lower(), 0.0)


def _book_number(raw):
    whole, _, frac = raw.partition(".")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{frac}" if frac else whole


def series_line(series_name):
    m = _SERIES_RE.match(series_name or "")
    if not m:
        return (series_name or "").strip()
    name, number = m.group(1).strip(), _book_number(m.group(2))
    return f"{name}, book {number}" if name else f"book {number}"


def _metadata(item):
    return (item.get("media") or {}).get("metadata") or {}


def shelf_sort_key(item):
    md = _metadata(item)
    author = (md.get("authorNameLF") or "").lower() or _LAST
    title = (md.get("titleIgnorePrefix") or md.get("title") or "").lower()
    return (author, series_key(md.get("seriesName") or ""), title)


@dataclass(frozen=True)
class ShelfBook:
    item_id: str
    title: str
    author: str
    series: str
    updated_at: int
    status: str


def _names(md):
    """(author, narrator, series) from minified or full item metadata."""
    author = md.get("authorName")
    if author is None:
        author = ", ".join(a.get("name", "") for a in md.get("authors") or [])
    narrator = md.get("narratorName")
    if narrator is None:
        narrator = ", ".join(md.get("narrators") or [])
    series = md.get("seriesName")
    if series is None:
        parts = []
        for s in md.get("series") or []:
            seq = s.get("sequence")
            parts.append(f"{s.get('name', '')} #{seq}" if seq else s.get("name", ""))
        series = ", ".join(parts)
    return author or "", narrator or "", series or ""


def build_shelf(items, progress):
    books = []
    for item in sorted(items, key=shelf_sort_key):
        md = _metadata(item)
        author, _, series = _names(md)
        books.append(
            ShelfBook(
                item_id=item["id"],
                title=md.get("title") or "",
                author=author,
                series=series,
                updated_at=int(item.get("updatedAt") or 0),
                status=status_of(item["id"], progress),
            )
        )
    return books


def shelf_counts(books):
    counts = {STATUS_READ: 0, STATUS_LISTENING: 0, STATUS_UNREAD: 0}
    for b in books:
        counts[b.status] += 1
    counts["total"] = len(books)
    return counts


def page_count(total, per_page):
    return max(1, math.ceil(total / per_page)) if per_page > 0 else 1


def finished_in_year(me, year, tz):
    n = 0
    for p in me.get("mediaProgress", []):
        at = p.get("finishedAt")
        if p.get("isFinished") and at and datetime.fromtimestamp(at / 1000, tz).year == year:
            n += 1
    return n


def chapter_index(chapters, current_time):
    if not chapters:
        return -1
    if current_time < chapters[0].get("start", 0):
        return 0
    found = 0
    for i, ch in enumerate(chapters):
        if ch.get("start", 0) <= current_time:
            found = i
            if current_time < ch.get("end", 0):
                return i
    return found


def chapter_line(chapter_title, idx, count):
    n = idx + 1
    title = (chapter_title or "").strip()
    m = _NUMBERED_CHAPTER_RE.match(title)
    if not title or (m and int(m.group(1)) == n):
        return f"Chapter {n} of {count}"
    return f"{title} ({n} of {count})"


def time_left(duration, current_time):
    minutes = int(max(0.0, (duration or 0) - (current_time or 0)) // 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


@dataclass
class NowListening:
    item_id: str
    title: str
    author: str
    narrator: str
    series: str
    updated_at: int
    duration: float
    current_time: float
    chapters: list = field(default_factory=list)
    finished: bool = False
    finished_at: "int | None" = None
    last_update: int = 0

    @property
    def pct(self):
        if self.finished:
            return 1.0
        if not self.duration:
            return 0.0
        return min(1.0, max(0.0, self.current_time / self.duration))

    @property
    def chapter_idx(self):
        return chapter_index(self.chapters, self.current_time)

    @property
    def chapter_count(self):
        return len(self.chapters)

    @property
    def chapter_title(self):
        idx = self.chapter_idx
        return (self.chapters[idx].get("title") or "") if idx >= 0 else ""


def _duration(*sources):
    for value in sources:
        if value:
            return float(value)
    return 0.0


def _now_listening(meta_item, detail, prog, finished):
    md = _metadata(meta_item)
    author, narrator, series = _names(md)
    chapters = list((detail.get("media") or {}).get("chapters") or [])
    duration = _duration(
        (meta_item.get("media") or {}).get("duration"),
        (detail.get("media") or {}).get("duration"),
        prog.get("duration"),
        # Finished records often store duration 0 and the plain item detail has
        # none, so the end of the last chapter stands in for a finished book.
        chapters[-1].get("end") if finished and chapters else 0,
    )
    return NowListening(
        item_id=meta_item["id"],
        title=md.get("title") or "",
        author=author,
        narrator=narrator,
        series=series,
        updated_at=int(meta_item.get("updatedAt") or 0),
        duration=duration,
        current_time=duration if finished else float(prog.get("currentTime") or 0),
        chapters=chapters,
        finished=finished,
        finished_at=prog.get("finishedAt"),
        last_update=int(prog.get("lastUpdate") or 0),
    )


IN_PROGRESS_LOOKAHEAD = 10


def pick_now_listening(client):
    # podcast episodes share both lists; only books belong on Now listening
    books = [
        e for e in client.items_in_progress(IN_PROGRESS_LOOKAHEAD) if e.get("mediaType") == "book"
    ]
    if books:
        entry = books[0]
        detail = client.item(entry["id"])
        prog = client.progress(entry["id"]) or {}
        return _now_listening(entry, detail, prog, finished=False)
    finished = [
        p
        for p in client.me().get("mediaProgress", [])
        if p.get("isFinished") and not p.get("episodeId") and p.get("libraryItemId")
    ]
    if not finished:
        return None
    last = max(finished, key=lambda p: (p.get("finishedAt") or 0, p.get("lastUpdate") or 0))
    detail = client.item(last["libraryItemId"])
    return _now_listening(detail, detail, last, finished=True)


def visible_key(nl, badge):
    badge = badge or {}
    return (
        nl.item_id if nl else "none",
        nl.chapter_idx if nl else -1,
        int(nl.pct * 100) if nl else 0,
        nl.finished if nl else False,
        bool(badge.get("message_waiting")),
        bool(badge.get("dnd")),
    )


# The shelf of spines: books grouped into runs (one series, or one author's other books)


@dataclass
class Run:
    key: str
    label: str
    kind: str
    books: list


RUN_SERIES = "series"
RUN_AUTHOR = "author"
_SERIES_SPLIT_RE = re.compile(r"(?<=[0-9]),\s+")


def surname(author):
    """The first author's last name: "Amir Levine, Md, Rachel Heller" -> "Levine"."""
    first = (author or "").split(",")[0].split()
    return first[-1] if first else ""


def book_hours(items):
    """Item id -> duration in hours, from the library items."""
    return {it["id"]: float((it.get("media") or {}).get("duration") or 0) / 3600 for it in items}


def _series_entries(series_name):
    """[(name, sequence or None)] for every series named in a seriesName."""
    entries = []
    for token in _SERIES_SPLIT_RE.split(series_name or ""):
        m = _SERIES_RE.match(token)
        if m:
            name, seq = m.group(1).strip(), float(m.group(2))
        else:
            name, seq = token.strip(), None
        if name:
            entries.append((name, seq))
    return entries


def _pick_series(entries, members):
    """The series a book shelves under: numbered first, then the one with most books, then by name."""
    if not entries:
        return None
    numbered = [e for e in entries if e[1] is not None] or entries
    return min(numbered, key=lambda e: (-members[e[0].lower()], e[0].lower()))


def shelf_runs(books, items_by_id, progress):
    """The shelf as runs, ordered by activity.

    A series run holds the volumes of one series in sequence order (a series with a single
    volume on the shelf joins its author instead); an author run holds the rest of one
    author's books by title. Runs with a book in progress come first (latest lastUpdate
    first), then runs with finished books (latest finishedAt first), then the others by
    author surname.
    """
    entries = {b.item_id: _series_entries(b.series) for b in books}
    members = {}
    for found in entries.values():
        for name in {name.lower() for name, _ in found}:
            members[name] = members.get(name, 0) + 1
    picked = {b.item_id: _pick_series(entries[b.item_id], members) for b in books}
    chosen = {}
    for pick in picked.values():
        if pick:
            chosen[pick[0].lower()] = chosen.get(pick[0].lower(), 0) + 1

    def sort_title(book):
        md = _metadata(items_by_id.get(book.item_id) or {})
        return (md.get("titleIgnorePrefix") or book.title or "").lower()

    groups = {}
    for book in books:
        pick = picked[book.item_id]
        if pick and chosen[pick[0].lower()] > 1:
            key, kind, name = f"{RUN_SERIES}:{pick[0].lower()}", RUN_SERIES, pick[0]
        else:
            key, kind, name = f"{RUN_AUTHOR}:{(book.author or '').lower()}", RUN_AUTHOR, book.author
        group = groups.setdefault(key, {"kind": kind, "name": name, "books": []})
        group["books"].append((pick[1] if pick and kind == RUN_SERIES else None, book))

    runs = []
    for key, group in groups.items():
        if group["kind"] == RUN_SERIES:
            ordered = sorted(
                group["books"], key=lambda t: (t[0] is None, t[0] or 0.0, sort_title(t[1]))
            )
            members_of = [b for _, b in ordered]
            read = sum(1 for b in members_of if b.status == STATUS_READ)
            label = f"{group['name'].upper()} · {read} of {len(members_of)}"
        else:
            members_of = sorted((b for _, b in group["books"]), key=sort_title)
            label = (group["name"] or "").upper()
        runs.append(Run(key, label, group["kind"], members_of))

    def activity(run):
        listening = [
            (progress.get(b.item_id) or {}).get("lastUpdate") or 0
            for b in run.books if b.status == STATUS_LISTENING
        ]
        if listening:
            return (0, -max(listening), "", run.key)
        finished = [
            (progress.get(b.item_id) or {}).get("finishedAt") or 0
            for b in run.books if b.status == STATUS_READ
        ]
        if finished:
            return (1, -max(finished), "", run.key)
        author = run.books[0].author if run.books else ""
        return (2, 0, (surname(author).lower() or _LAST), run.key)

    return sorted(runs, key=activity)


def shelf_parts(items, progress):
    """(books, runs, hours, pct) of one library: what the shelf of spines is drawn from."""
    books = build_shelf(items, progress)
    runs = shelf_runs(books, {it["id"]: it for it in items}, progress)
    pct = {
        b.item_id: float((progress.get(b.item_id) or {}).get("progress") or 0.0)
        for b in books if b.status == STATUS_LISTENING
    }
    return books, runs, book_hours(items), pct
