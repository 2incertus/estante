"""scripts/preview.py: the RGB565 decode, the round trip and a whole run against the fake ABS."""
import importlib.util

import pytest
from PIL import Image

from conftest import REPO

PREVIEW = REPO / "scripts" / "preview.py"


@pytest.fixture(scope="module")
def preview():
    spec = importlib.util.spec_from_file_location("preview", PREVIEW)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gradient(size=(64, 16)):
    pixels = [
        (x * 4 % 256, (x * 7 + y * 13) % 256, (255 - y * 9) % 256)
        for y in range(size[1])
        for x in range(size[0])
    ]
    return Image.frombytes("RGB", size, bytes(v for px in pixels for v in px))


def test_decode_rgb565_inverts_to_fb_bytes(preview):
    from estante_lib.display_fb import FbInfo
    from estante_lib.render_screen import to_fb_bytes

    img = _gradient()
    info = FbInfo(64, 16, 16, 128)
    decoded = preview.decode_rgb565(to_fb_bytes(img, info), img.size)
    assert decoded.mode == "RGB" and decoded.size == img.size
    raw, back = img.tobytes(), decoded.tobytes()
    masks = (0xF8, 0xFC, 0xF8)
    assert back == bytes(v & masks[i % 3] for i, v in enumerate(raw))


def test_round_trip_ok_and_catches_a_wrong_frame(preview, tmp_path):
    img = _gradient((48, 27))
    ok, err = preview.fb_round_trip(img, str(tmp_path / "fb"), img.size)
    assert ok and err <= 7
    assert (tmp_path / "fb").stat().st_size == 48 * 27 * 2

    class Broken:
        @staticmethod
        def decode(data, size):
            return Image.new("RGB", size, (255, 0, 255))

    ok, err = preview.fb_round_trip(img, str(tmp_path / "fb2"), img.size, decode=Broken.decode)
    assert not ok and err > 7


def test_output_names(preview):
    args = preview.parse_args(["--theme", "paper"])
    assert preview.output_name(args) == "screen-paper.png"
    args = preview.parse_args(["--badge", "--dim", "0.4"])
    assert preview.output_name(args) == "screen-dark-badge-dim.png"
    args = preview.parse_args(["--dnd"])
    assert preview.output_name(args) == "screen-dark-dnd.png"


@pytest.mark.parametrize("dim", ["1.5", "-0.1", "x"])
def test_dim_must_be_0_to_1(preview, dim):
    with pytest.raises(SystemExit):
        preview.parse_args(["--dim", dim])


def test_preview_against_fake_abs(preview, fake_abs, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ESTANTE_STATE_DIR", str(tmp_path / "state"))
    env = tmp_path / "abs.env"
    env.write_text(f"ABS_URL={fake_abs.base_url}\nexport ABS_TOKEN='TESTTOKEN'\n")
    out = tmp_path / "out"
    code = preview.main([
        "--env", str(env), "--theme", "paper", "--badge", "--dim", "0.5", "--out", str(out),
        "--fb-file", str(tmp_path / "fb"),
    ])
    printed = capsys.readouterr()
    assert code == 0, printed.out + printed.err
    with Image.open(out / "screen-paper-badge-dim.png") as img:
        assert img.size == (1920, 1080) and img.mode == "RGB"
    assert "fb round trip: ok" in printed.out
    assert (tmp_path / "fb").stat().st_size == 1920 * 1080 * 2
    assert "TESTTOKEN" not in printed.out + printed.err
    # GET only, and the covers landed in the state dir's cache
    assert any(p.endswith("/cover") for p in fake_abs.calls)
    assert list((tmp_path / "state" / "covers").glob("*-w120.jpg"))


def test_preview_fails_cleanly_without_credentials(preview, tmp_path, capsys):
    env = tmp_path / "abs.env"
    env.write_text("OTHER=1\n")
    assert preview.main(["--env", str(env), "--out", str(tmp_path / "out")]) == 2
    assert "ABS_URL" in capsys.readouterr().err


def test_preview_fails_cleanly_without_an_audiobooks_library(preview, fake_abs, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ESTANTE_STATE_DIR", str(tmp_path / "state"))
    fake_abs.override("/api/libraries", {"libraries": [{"id": "p", "name": "Podcasts"}]})
    env = tmp_path / "abs.env"
    env.write_text(f"ABS_URL={fake_abs.base_url}\nABS_TOKEN=TESTTOKEN\n")
    assert preview.main(["--env", str(env), "--out", str(tmp_path / "out")]) == 2
    assert "audiobooks" in capsys.readouterr().err
