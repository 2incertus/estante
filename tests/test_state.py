import json
import os
import threading

from estante_lib.state import (
    DEFAULT_BADGE,
    DEFAULT_STATE_DIR,
    message_text,
    normalize_badge,
    read_state,
    state_dir,
    state_path,
    update_state,
    write_state,
)

DEFAULTS = {"badge": DEFAULT_BADGE, "pages": {}, "page_counts": {}, "display": {}}


def test_state_dir_default(monkeypatch):
    monkeypatch.delenv("ESTANTE_STATE_DIR", raising=False)
    assert state_dir() == DEFAULT_STATE_DIR == "/var/lib/estante"
    assert state_path() == "/var/lib/estante/state.json"


def test_state_path_follows_env(tmp_state_dir):
    assert state_path() == os.path.join(str(tmp_state_dir), "state.json")


def test_read_state_defaults_when_missing(tmp_state_dir):
    assert read_state() == DEFAULTS


def test_read_state_defaults_when_corrupt(tmp_state_dir):
    with open(state_path(), "w") as f:
        f.write("{not json")
    assert read_state() == DEFAULTS


def test_read_state_merges_partial_badge(tmp_state_dir):
    with open(state_path(), "w") as f:
        json.dump({"badge": {"dnd": True}, "pages": {"the_shelf": 2}}, f)
    st = read_state()
    assert st["badge"] == {"message_waiting": False, "dnd": True, "count": 0, "from": ""}
    assert st["pages"] == {"the_shelf": 2} and st["display"] == {}


def test_read_state_does_not_alias_defaults(tmp_state_dir):
    st = read_state()
    st["badge"]["dnd"] = True
    st["pages"]["x"] = 1
    assert DEFAULT_BADGE["dnd"] is False
    assert read_state() == DEFAULTS


def test_write_then_read_roundtrip(tmp_state_dir):
    st = read_state()
    st["display"]["last"] = "now_listening"
    write_state(st)
    assert read_state()["display"] == {"last": "now_listening"}
    assert [n for n in os.listdir(tmp_state_dir) if n != "state.json"] == []


def test_update_state_is_locked_and_atomic(tmp_state_dir):
    def bump(state):
        state["pages"]["x"] = state["pages"].get("x", 0) + 1

    def worker():
        for _ in range(50):
            update_state(bump)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with open(state_path()) as f:
        assert json.load(f)["pages"]["x"] == 400


def test_update_state_returns_state(tmp_state_dir):
    st = update_state(lambda s: s["page_counts"].update({"the_shelf": 7}))
    assert st["page_counts"] == {"the_shelf": 7}


def test_normalize_badge_coerces_and_ignores_unknown():
    b = normalize_badge(DEFAULT_BADGE, {"message_waiting": "yes", "count": "2", "from": "Jasmine", "zzz": 1})
    assert b == {"message_waiting": True, "dnd": False, "count": 2, "from": "Jasmine"}


def test_normalize_badge_keeps_missing_and_clamps():
    cur = {"message_waiting": True, "dnd": True, "count": 3, "from": "Jasmine"}
    assert normalize_badge(cur, {"count": -4}) == dict(cur, count=0)
    assert normalize_badge(cur, {"dnd": False}) == dict(cur, dnd=False)
    assert normalize_badge(cur, {"message_waiting": "false"})["message_waiting"] is False
    assert normalize_badge(cur, {"count": "lots"})["count"] == 3


def test_message_text_variants():
    assert message_text({"message_waiting": False, "count": 3, "from": "Jasmine"}) == ""
    assert message_text({"message_waiting": True, "count": 0, "from": ""}) == "Message waiting"
    assert message_text({"message_waiting": True, "count": 1, "from": "Jasmine"}) == "Message from Jasmine waiting"
    assert message_text({"message_waiting": True, "count": 2, "from": ""}) == "2 messages waiting"
    assert message_text({"message_waiting": True, "count": 2, "from": "Jasmine"}) == "2 messages from Jasmine waiting"
