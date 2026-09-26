"""Generate the original Light Masks icon using only the Python standard library."""

import struct
import zlib
from pathlib import Path

SIZE = 256
DESTINATION = Path(__file__).resolve().parents[1] / "custom_components" / "light_masks" / "brand"


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def pixel(x: int, y: int) -> tuple[int, int, int, int]:
    result = (0, 0, 0, 0)
    for center_y, color in (
        (167, (139, 92, 246, 255)),
        (133, (6, 182, 212, 255)),
        (99, (245, 158, 11, 255)),
    ):
        if abs(x - 128) / 104 + abs(y - center_y) / 55 <= 1:
            result = color
    if (
        (x - 128) ** 2 + (y - 86) ** 2 <= 20**2
        or (117 <= x <= 139 and 96 <= y <= 114)
        or (119 <= x <= 137 and 120 <= y <= 125)
    ):
        result = (255, 255, 255, 255)
    return result


def main() -> None:
    scanlines = b"".join(
        b"\x00" + bytes(channel for x in range(SIZE) for channel in pixel(x, y))
        for y in range(SIZE)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines, 9))
        + chunk(b"IEND", b"")
    )
    DESTINATION.mkdir(exist_ok=True)
    (DESTINATION / "icon.png").write_bytes(png)


if __name__ == "__main__":
    main()
