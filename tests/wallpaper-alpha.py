"""Reproduce the approved alpha-only repair of the supplied RGB 3.png.

Usage: python3 tests/wallpaper-alpha.py original/3.png config/hyprlax/3.png
Requires Pillow. RGB channels are preserved byte for byte.
"""

import sys
from collections import deque

from PIL import Image


def remove_background(source, destination):
    image = Image.open(source).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    seen = bytearray(width * height)
    queue = deque()

    def visit(x, y):
        index = y * width + x
        if not seen[index] and max(pixels[x, y][:3]) <= 3:
            seen[index] = 1
            queue.append((x, y))

    for x in range(width):
        visit(x, 0)
        visit(x, height - 1)
    for y in range(height):
        visit(0, y)
        visit(width - 1, y)
    while queue:
        x, y = queue.popleft()
        pixels[x, y] = (*pixels[x, y][:3], 0)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                visit(nx, ny)
    image.save(destination)
    assert Image.open(source).convert("RGB").tobytes() == image.convert("RGB").tobytes()
    print(f"Made {sum(seen)} edge-connected black pixels transparent; RGB unchanged")


if __name__ == "__main__":
    remove_background(*sys.argv[1:])
