"""Generate PWA icons (amber block-M on umber black) with no image libraries."""
import os
import struct
import zlib

BG = (19, 16, 9)
AMBER = (255, 176, 32)

# 12x12 block-M glyph
GLYPH = [
    "............",
    "............",
    ".XX......XX.",
    ".XXX....XXX.",
    ".XXXX..XXXX.",
    ".XX.XXXX.XX.",
    ".XX..XX..XX.",
    ".XX......XX.",
    ".XX......XX.",
    ".XX......XX.",
    "............",
    "............",
]


def make_png(size, path):
    cell = size // 12
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter byte
        gy = min(y // cell, 11)
        for x in range(size):
            gx = min(x // cell, 11)
            r, g, b = AMBER if GLYPH[gy][gx] == "X" else BG
            row += bytes((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 9)) +
           chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    print("wrote", path)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "icons")
    os.makedirs(out, exist_ok=True)
    make_png(192, os.path.join(out, "icon-192.png"))
    make_png(512, os.path.join(out, "icon-512.png"))
