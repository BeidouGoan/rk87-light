"""Generate RK87 Light visual assets: app icon (multi-size ICO) and frosted UI bg."""
import os
from PIL import Image, ImageDraw, ImageFilter

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(ASSETS, exist_ok=True)

BG = (15, 19, 25)
CARD = (27, 34, 44)
ACCENT = (110, 231, 200)
ACCENT_DEEP = (30, 122, 99)


def make_icon_base(size: int) -> Image.Image:
    """A mint rounded square with a simplified keyboard glyph."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(s * 0.06)
    d.rounded_rectangle((pad, pad, s - pad, s - pad), radius=int(s * 0.22),
                        fill=ACCENT + (255,))
    # keyboard body
    kb_x0, kb_y0 = int(s * 0.22), int(s * 0.34)
    kb_x1, kb_y1 = int(s * 0.78), int(s * 0.68)
    d.rounded_rectangle((kb_x0, kb_y0, kb_x1, kb_y1), radius=int(s * 0.05),
                        fill=(20, 40, 36, 255))
    # key dots: 3 rows
    rows = 3
    cols = 7
    for r in range(rows):
        for c in range(cols):
            kx = kb_x0 + int((kb_x1 - kb_x0) * (c + 0.5) / cols)
            ky = kb_y0 + int((kb_y1 - kb_y0) * (r + 0.5) / rows)
            rad = max(1, int(s * 0.018))
            colour = ACCENT + (255,) if (r + c) % 3 == 0 else (90, 200, 175, 255)
            d.ellipse((kx - rad, ky - rad, kx + rad, ky + rad), fill=colour)
    # spacebar highlight
    sx0 = kb_x0 + int((kb_x1 - kb_x0) * 0.3)
    sx1 = kb_x0 + int((kb_x1 - kb_x0) * 0.7)
    sy = kb_y1 - int(s * 0.035)
    d.rounded_rectangle((sx0, sy, sx1, sy + int(s * 0.022)), radius=int(s * 0.01),
                        fill=ACCENT + (255,))
    return img.resize((size, size), Image.LANCZOS)


def build_ico():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [make_icon_base(s) for s in sizes]
    imgs[0].save(
        os.path.join(ASSETS, "rk87-light.ico"),
        format="ICO", sizes=[(s, s) for s in sizes],
    )
    make_icon_base(64).save(os.path.join(ASSETS, "rk87-light.png"))
    make_icon_base(24).save(os.path.join(ASSETS, "rk87-light-24.png"))
    print("icon assets written")


def build_bg(w=940, h=620):
    """Dark frosted background with faint mint radial glows, corners filled KEY."""
    img = Image.new("RGB", (w, h), BG)
    glow = Image.new("RGB", (w, h), BG)
    gd = ImageDraw.Draw(glow)
    # two soft glows
    gd.ellipse((-120, -160, 420, 340), fill=(24, 58, 52))
    gd.ellipse((w - 360, h - 320, w + 120, h + 160), fill=(20, 48, 58))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.blend(img, glow, 0.55)
    # subtle card band behind panels
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((20, 16, w - 20, h - 16), radius=22, outline=(80, 100, 110, 40), width=1)
    # transparency key corners (matching rk87light.KEY = #000001)
    mask = Image.new("L", (w, h), 255)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w - 1, h - 1), radius=22, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1))
    key_layer = Image.new("RGB", (w, h), (0, 0, 1))
    img = Image.composite(img, key_layer, mask)
    img.save(os.path.join(ASSETS, "ui-bg.png"))
    print("ui-bg.png written")


if __name__ == "__main__":
    build_ico()
    build_bg()
