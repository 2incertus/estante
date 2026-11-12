"""Write finished frames to the Linux framebuffer (/dev/fb0). Stdlib only.

`show` takes bytes already in the framebuffer's pixel format (RGB565 little-endian at
16 bpp, BGRX at 32 bpp; `render_screen.to_fb_bytes` makes them with Pillow raw modes)
and only lays the rows out at the device stride. `rgb_to_bgrx` and `rgb_to_rgb565` are
pure-Python references for the same formats.
"""
import os
from dataclasses import dataclass

DEFAULT_DEVICE = "/dev/fb0"
DEFAULT_SYSFS = "/sys/class/graphics/fb0"
BLANK_POWERDOWN = "4"
BLANK_UNBLANK = "0"


@dataclass
class FbInfo:
    width: int
    height: int
    bpp: int
    stride: int

    @property
    def bytes_per_pixel(self):
        return self.bpp // 8

    @property
    def size(self):
        return (self.width, self.height)


def _read(sysfs_dir, name):
    with open(os.path.join(sysfs_dir, name), encoding="ascii") as f:
        return f.read().strip()


def read_info(sysfs_dir=DEFAULT_SYSFS):
    """Geometry from sysfs: virtual_size "W,H", bits_per_pixel, stride (computed when absent)."""
    if not os.path.isdir(sysfs_dir):
        raise FileNotFoundError(f"no framebuffer at {sysfs_dir}: is HDMI connected or the mode forced?")
    width, height = (int(v) for v in _read(sysfs_dir, "virtual_size").split(","))
    bpp = int(_read(sysfs_dir, "bits_per_pixel"))
    try:
        stride = int(_read(sysfs_dir, "stride"))
    except (OSError, ValueError):
        stride = width * bpp // 8
    return FbInfo(width, height, bpp, stride)


def rgb_to_bgrx(rgb):
    """Packed RGB to BGRX (XRGB8888 little-endian): a zero byte after every pixel, R and B swapped."""
    out = bytearray(len(rgb) // 3 * 4)
    out[2::4] = rgb[0::3]
    out[1::4] = rgb[1::3]
    out[0::4] = rgb[2::3]
    return bytes(out)


_HI_R = bytes(v & 0xF8 for v in range(256))
_HI_G = bytes(v >> 5 for v in range(256))
_LO_G = bytes((v << 3) & 0xE0 for v in range(256))
_LO_B = bytes(v >> 3 for v in range(256))


def rgb_to_rgb565(rgb):
    """Packed RGB to RGB565 little-endian (red in the high five bits)."""
    n = len(rgb) // 3
    if n == 0:
        return b""
    r, g, b = bytes(rgb[0::3]), bytes(rgb[1::3]), bytes(rgb[2::3])
    hi = int.from_bytes(r.translate(_HI_R), "big") | int.from_bytes(g.translate(_HI_G), "big")
    lo = int.from_bytes(g.translate(_LO_G), "big") | int.from_bytes(b.translate(_LO_B), "big")
    out = bytearray(2 * n)
    out[0::2] = lo.to_bytes(n, "big")
    out[1::2] = hi.to_bytes(n, "big")
    return bytes(out)


class _Writer:
    """Row layout shared by the device and the file framebuffer; `self._fd` is an open fd."""

    info: FbInfo
    _fd = None

    def show(self, pixels, size):
        """Write a frame of `size` already in the framebuffer format, top left, clipped to the screen."""
        w, h = size
        bpp = self.info.bytes_per_pixel
        src_stride = w * bpp
        if len(pixels) != src_stride * h:
            raise ValueError(f"{len(pixels)} bytes for a {w}x{h} frame at {self.info.bpp} bpp")
        rows = min(h, self.info.height)
        row_bytes = min(w, self.info.width) * bpp
        view = memoryview(pixels)
        if src_stride == self.info.stride and row_bytes == src_stride:
            os.pwrite(self._fd, view[: rows * src_stride], 0)
            return
        for y in range(rows):
            start = y * src_stride
            os.pwrite(self._fd, view[start:start + row_bytes], y * self.info.stride)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Framebuffer(_Writer):
    """The real device: geometry from sysfs, the fd kept open, DPMS through sysfs `blank`."""

    def __init__(self, device=DEFAULT_DEVICE, sysfs_dir=DEFAULT_SYSFS):
        self.device = device
        self.sysfs_dir = sysfs_dir
        self.info = read_info(sysfs_dir)
        self._fd = os.open(device, os.O_RDWR)

    def blank(self, on):
        """Power the screen down (True) or up (False); False when the driver refuses."""
        try:
            with open(os.path.join(self.sysfs_dir, "blank"), "w", encoding="ascii") as f:
                f.write(BLANK_POWERDOWN if on else BLANK_UNBLANK)
            return True
        except OSError:
            return False


class FileFramebuffer(_Writer):
    """A regular file laid out like the device (tests and previews); `blank` is only recorded."""

    def __init__(self, path, info):
        self.path = path
        self.info = info
        self.blank_calls = []
        self._fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        os.ftruncate(self._fd, info.stride * info.height)

    def blank(self, on):
        self.blank_calls.append(bool(on))
        return True
