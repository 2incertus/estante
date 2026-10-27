"""state.json shared by the plugins and estante-ctl: locked, atomic. Stdlib only."""
import copy
import fcntl
import json
import os
import tempfile

DEFAULT_STATE_DIR = "/var/lib/estante"
DEFAULT_BADGE = {"message_waiting": False, "dnd": False, "count": 0, "from": ""}
_DEFAULTS = {"badge": DEFAULT_BADGE, "pages": {}, "page_counts": {}, "display": {}}
_TRUE_STRINGS = {"1", "true", "yes", "on"}


def state_dir():
    return os.environ.get("ESTANTE_STATE_DIR") or DEFAULT_STATE_DIR


def state_path():
    return os.path.join(state_dir(), "state.json")


def _defaults():
    return copy.deepcopy(_DEFAULTS)


def read_state(path=None):
    path = path or state_path()
    state = _defaults()
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return state
    if not isinstance(loaded, dict):
        return state
    for key, value in loaded.items():
        if key == "badge" and isinstance(value, dict):
            state["badge"].update({k: v for k, v in value.items() if k in DEFAULT_BADGE})
        elif key in _DEFAULTS:
            if isinstance(value, dict):
                state[key] = value
        else:
            state[key] = value
    return state


def write_state(state, path=None):
    path = path or state_path()
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def update_state(mutate, path=None):
    path = path or state_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = read_state(path)
            mutate(state)
            write_state(state, path)
            return state
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return bool(value)


def normalize_badge(current, incoming):
    badge = dict(DEFAULT_BADGE)
    badge.update({k: v for k, v in (current or {}).items() if k in DEFAULT_BADGE})
    incoming = incoming or {}
    for key in ("message_waiting", "dnd"):
        if key in incoming:
            badge[key] = _as_bool(incoming[key])
    if "count" in incoming:
        try:
            badge["count"] = max(0, int(incoming["count"]))
        except (TypeError, ValueError):
            pass
    if "from" in incoming:
        badge["from"] = "" if incoming["from"] is None else str(incoming["from"])
    return badge


def message_text(badge):
    if not badge.get("message_waiting"):
        return ""
    count = badge.get("count") or 0
    sender = badge.get("from") or ""
    if count >= 2:
        return f"{count} messages from {sender} waiting" if sender else f"{count} messages waiting"
    return f"Message from {sender} waiting" if sender else "Message waiting"
