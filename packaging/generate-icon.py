"""Generate the editable NEJE Bridge mark and native macOS icon.

Run with a Python that has Pillow installed. The icon is drawn from primitives:
the cyan Bluetooth rune connects to an amber laser focus ring. No vendor logo
or third-party artwork is embedded.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
SIZE = 1024


def draw_icon():
    base = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    shadow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((34, 43, 990, 999), radius=218,
                                             fill=(4, 13, 29, 135))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))

    tile = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    tile_mask = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle((28, 28, 996, 996), radius=220,
                                                 fill=255)
    pixels = tile.load()
    for y in range(SIZE):
        for x in range(SIZE):
            depth = y / SIZE
            flare = max(0, 1 - ((x - 290) ** 2 + (y - 245) ** 2) ** 0.5 / 900)
            pixels[x, y] = (int(14 + 8 * flare), int(35 + 19 * flare - 7 * depth),
                            int(54 + 16 * flare - 8 * depth), 255)
    tile.putalpha(tile_mask)
    base.alpha_composite(tile)
    art = ImageDraw.Draw(base)
    art.rounded_rectangle((49, 49, 975, 975), radius=201,
                          outline=(126, 207, 216, 42), width=4)

    # Cyan Bluetooth rune: large enough to remain legible in a Dock icon.
    cyan = (89, 232, 225, 255)
    art.line([(390, 258), (390, 766)], fill=cyan, width=42, joint='curve')
    art.line([(390, 258), (543, 388), (285, 638)], fill=cyan, width=42,
             joint='curve')
    art.line([(285, 389), (543, 638), (390, 766)], fill=cyan, width=42,
             joint='curve')
    for x, y in [(390, 258), (543, 388), (285, 638), (285, 389),
                 (543, 638), (390, 766)]:
        art.ellipse((x - 21, y - 21, x + 21, y + 21), fill=cyan)

    # A short optical link joins the radio glyph to the amber focus target.
    art.line([(543, 513), (661, 513)], fill=(102, 225, 219, 255), width=23)
    art.line([(664, 513), (700, 513)], fill=(249, 183, 90, 255), width=23)
    amber = (255, 184, 91, 255)
    art.ellipse((659, 407, 871, 619), outline=amber, width=36)
    art.ellipse((736, 484, 794, 542), fill=(255, 227, 166, 255))
    art.line([(765, 365), (765, 394)], fill=amber, width=22)
    art.line([(765, 632), (765, 661)], fill=amber, width=22)
    return base


def main():
    ASSETS.mkdir(exist_ok=True)
    icon = draw_icon()
    png = ASSETS / 'NEJE Bridge.png'
    icns = ASSETS / 'NEJE Bridge.icns'
    icon.save(png, optimize=True)
    icon.save(icns, sizes=[(16, 16), (32, 32), (64, 64), (128, 128),
                           (256, 256), (512, 512), (1024, 1024)])
    print(png)
    print(icns)


if __name__ == '__main__':
    main()
