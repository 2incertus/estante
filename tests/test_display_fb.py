import os
import subprocess
import sys
import time

import pytest

from conftest import REPO
from estante_lib.display_fb import (
    FbInfo,
    FileFramebuffer,
    Framebuffer,
    read_info,
    rgb_to_bgrx,
    rgb_to_rgb565,
)

# vc4drmfb on the Pi 3B+ with the forced 1080p mode: RGB565, rgba 5/11,6/5,5/0,0/0
DEFAULT = FbInfo(1920, 1080, 16, 3840)


def _sysfs(tmp_path, virtual_size="1920,1080", bpp="16", stride="3840"):
    d = tmp_path / "fb0"
    d.mkdir()
    (d / "virtual_size").write_text(virtual_size + "\n")
    (d / "bits_per_pixel").write_text(bpp + "\n")
    if stride is not None:
        (d / "stride").write_text(stride + "\n")
    (d / "blank").write_text("0\n")
    return d


def _px565(r, g, b):
    return (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).to_bytes(2, "little")


def test_rgb_to_bgrx():
    assert rgb_to_bgrx(b"\x01\x02\x03") == b"\x03\x02\x01\x00"
    assert rgb_to_bgrx(b"\x01\x02\x03\xff\x80\x00") == b"\x03\x02\x01\x00\x00\x80\xff\x00"
    assert rgb_to_bgrx(b"") == b""


def test_rgb_to_rgb565_little_endian():
    assert rgb_to_rgb565(b"\xff\xff\xff") == b"\xff\xff"
    assert rgb_to_rgb565(b"\xff\x00\x00") == b"\x00\xf8"
    assert rgb_to_rgb565(b"\x00\xff\x00") == b"\xe0\x07"
    assert rgb_to_rgb565(b"\x00\x00\xff") == b"\x1f\x00"
    assert rgb_to_rgb565(bytes([0, 0, 0, 0x9C, 0x5A, 0x31])) == b"\x00\x00" + _px565(0x9C, 0x5A, 0x31)


def test_bgrx_reference_is_fast_enough_for_two_megapixels():
    rgb = os.urandom(1920 * 1080 * 3)
    start = time.perf_counter()
    out = rgb_to_bgrx(rgb)
    assert time.perf_counter() - start < 0.5
    assert len(out) == 1920 * 1080 * 4
    assert out[4 * 1000:4 * 1000 + 4] == bytes([rgb[3002], rgb[3001], rgb[3000], 0])


def test_read_info(tmp_path):
    assert read_info(str(_sysfs(tmp_path))) == DEFAULT


def test_read_info_32_bpp(tmp_path):
    d = _sysfs(tmp_path, bpp="32", stride="7680")
    assert read_info(str(d)) == FbInfo(1920, 1080, 32, 7680)


def test_read_info_without_stride(tmp_path):
    d = _sysfs(tmp_path, virtual_size="1024,768", bpp="16", stride=None)
    assert read_info(str(d)) == FbInfo(1024, 768, 16, 2048)


def _frame_565(w, h, colour=(10, 20, 30)):
    return bytearray(_px565(*colour) * (w * h))


def test_file_framebuffer_writes_stride_times_height(tmp_path):
    path = tmp_path / "fb.raw"
    fb = FileFramebuffer(str(path), DEFAULT)
    frame = _frame_565(1920, 1080)
    off = (456 * 1920 + 123) * 2
    frame[off:off + 2] = _px565(200, 100, 50)
    fb.show(bytes(frame), (1920, 1080))
    fb.close()
    data = path.read_bytes()
    assert len(data) == 3840 * 1080
    assert data[456 * 3840 + 123 * 2:456 * 3840 + 123 * 2 + 2] == _px565(200, 100, 50)
    assert data[0:2] == _px565(10, 20, 30)


def test_file_framebuffer_32_bpp(tmp_path):
    info = FbInfo(1920, 1080, 32, 7680)
    path = tmp_path / "fb.raw"
    fb = FileFramebuffer(str(path), info)
    frame = bytearray(b"\x1e\x14\x0a\x00" * (1920 * 1080))
    off = (456 * 1920 + 123) * 4
    frame[off:off + 4] = b"\x32\x64\xc8\x00"
    fb.show(bytes(frame), (1920, 1080))
    data = path.read_bytes()
    assert len(data) == 7680 * 1080
    assert data[456 * 7680 + 123 * 4:456 * 7680 + 124 * 4] == b"\x32\x64\xc8\x00"


def test_padded_stride_rows(tmp_path):
    info = FbInfo(4, 3, 16, 16)  # 8 bytes of pixels, 8 bytes of padding per row
    path = tmp_path / "fb.raw"
    fb = FileFramebuffer(str(path), info)
    frame = bytearray(4 * 3 * 2)
    frame[(2 * 4 + 3) * 2:(2 * 4 + 3) * 2 + 2] = b"\xab\xcd"
    fb.show(bytes(frame), (4, 3))
    data = path.read_bytes()
    assert len(data) == 16 * 3
    assert data[2 * 16 + 6:2 * 16 + 8] == b"\xab\xcd"
    assert data[2 * 16 + 8:2 * 16 + 16] == bytes(8)


def test_smaller_frame_is_written_top_left_and_larger_is_clipped(tmp_path):
    info = FbInfo(4, 2, 16, 8)
    path = tmp_path / "fb.raw"
    fb = FileFramebuffer(str(path), info)
    fb.show(b"\x11\x11" * 2, (2, 1))
    assert path.read_bytes() == b"\x11\x11\x11\x11" + bytes(4) + bytes(8)
    fb.show(b"\x22\x22" * 6 * 3, (6, 3))
    assert path.read_bytes() == b"\x22\x22" * 8


def test_show_rejects_short_bytes(tmp_path):
    fb = FileFramebuffer(str(tmp_path / "fb.raw"), FbInfo(4, 4, 16, 8))
    with pytest.raises(ValueError):
        fb.show(b"\x00" * 10, (4, 4))


def test_file_framebuffer_records_blank_calls(tmp_path):
    fb = FileFramebuffer(str(tmp_path / "fb.raw"), DEFAULT)
    assert fb.blank(True) is True and fb.blank(False) is True
    assert fb.blank_calls == [True, False]
    fb.close()
    fb.close()


def test_framebuffer_on_a_device_file(tmp_path):
    sysfs = _sysfs(tmp_path, virtual_size="16,4", bpp="16", stride="32")
    device = tmp_path / "fb0.dev"
    device.write_bytes(bytes(32 * 4))
    fb = Framebuffer(str(device), str(sysfs))
    assert fb.info == FbInfo(16, 4, 16, 32)
    fb.show(_px565(9, 8, 7) * 64, (16, 4))
    assert device.read_bytes()[-2:] == _px565(9, 8, 7)
    assert fb.blank(True) is True and (sysfs / "blank").read_text().strip() == "4"
    assert fb.blank(False) is True and (sysfs / "blank").read_text().strip() == "0"
    fb.close()


def test_blank_reports_failure(tmp_path):
    sysfs = _sysfs(tmp_path, virtual_size="16,4", bpp="16", stride="32")
    device = tmp_path / "fb0.dev"
    device.write_bytes(bytes(32 * 4))
    fb = Framebuffer(str(device), str(sysfs))
    (sysfs / "blank").unlink()
    (sysfs / "blank").mkdir()  # writing to a directory fails like a driver without DPMS
    assert fb.blank(True) is False
    fb.close()


def test_module_imports_only_the_stdlib():
    code = "import sys; import estante_lib.display_fb; print('PIL' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO / "plugins"),
        check=True,
    )
    assert out.stdout.strip() == "False"
