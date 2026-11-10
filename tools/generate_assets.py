#!/usr/bin/env python3
"""Generates every visual Android resource for NDX with Pillow and registers the
Android res files in buildozer.spec (android.add_resources).

Each density is RENDERED at its own pixel size (supersampled 4x then downscaled),
never copied from one bitmap."""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
RES = ASSETS / "android" / "res"
FONT_BOLD = ASSETS / "fonts" / "DejaVuSans-Bold.ttf"
FONT = ASSETS / "fonts" / "DejaVuSans.ttf"

TEAL, TEAL_D, INK, SURF, WHITE = (45, 212, 191), (20, 150, 140), (13, 17, 23), (22, 27, 34), (230, 237, 243)
DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
SS = 4  # supersampling factor


def px(dp, scale):
    return int(round(dp * scale))


def gradient(size, c1, c2):
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for i in range(size * 2):
        t = i / (size * 2 - 1)
        col = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)) + (255,)
        d.line([(i, 0), (0, i)], fill=col, width=2)
    return img.crop((0, 0, size, size))


def thick_line(d, pts, w, fill):
    d.line(pts, fill=fill, width=w, joint="curve")
    r = w / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def glyph(size, color_chevron, color_bar, extent=1.0):
    """The NDX mark (a prompt chevron + cursor bar) on a transparent square.
    `extent` scales it around the center (adaptive icons keep it inside the 66/108 safe zone)."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = big * extent
    o = (big - s) / 2
    P = lambda x, y: (o + x * s, o + y * s)
    w = int(0.10 * s)
    thick_line(d, [P(0.27, 0.30), P(0.50, 0.50), P(0.27, 0.70)], w, color_chevron)
    thick_line(d, [P(0.56, 0.71), P(0.76, 0.71)], w, color_bar)
    return img


def compose_legacy(size, shape):
    big = size * SS
    base = gradient(big, (13, 17, 23), (18, 56, 54))
    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    if shape == "round":
        md.ellipse([0, 0, big - 1, big - 1], fill=255)
    else:
        md.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=255)
    g = glyph(size, TEAL + (255,), WHITE + (255,), 0.74).resize((big, big), Image.LANCZOS)
    base.alpha_composite(g)
    base.putalpha(mask)
    return base.resize((size, size), Image.LANCZOS)


def adaptive_layer(size, kind):
    """108dp canvas. Visible area is a mask of ~72dp; keep content inside 66dp (61%)."""
    if kind == "background":
        return gradient(size, (13, 17, 23), (18, 56, 54))
    g = glyph(size, TEAL + (255,), WHITE + (255,), 0.61 * 1.18)
    return g.resize((size, size), Image.LANCZOS)


def monochrome_layer(size):
    g = glyph(size, (0, 0, 0, 255), (0, 0, 0, 255), 0.61 * 1.18)
    return g.resize((size, size), Image.LANCZOS)


def notification_icon(size):
    g = glyph(size, (255, 255, 255, 255), (255, 255, 255, 255), 0.92)
    return g.resize((size, size), Image.LANCZOS)


def save(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return path


def splash(size=1024):
    img = Image.new("RGBA", (size, size), INK + (255,))
    # soft vignette panel
    panel = gradient(size, (13, 17, 23), (16, 40, 40))
    img.alpha_composite(panel)
    icon = compose_legacy(int(size * 0.34), "square")
    img.alpha_composite(icon, ((size - icon.width) // 2, int(size * 0.26)))
    d = ImageDraw.Draw(img)
    f1, f2 = ImageFont.truetype(str(FONT_BOLD), int(size * 0.095)), ImageFont.truetype(str(FONT), int(size * 0.032))
    def center(text, font, y, fill):
        w = d.textlength(text, font=font)
        d.text(((size - w) / 2, y), text, font=font, fill=fill)
    center("NDX", f1, int(size * 0.64), WHITE + (255,))
    center("Universal Developer Environment", f2, int(size * 0.76), (139, 148, 158, 255))
    return img


def illustration(kind, w=640, h=480):
    img = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    S = 2
    def rr(box, r, fill, outline=None, width=0):
        d.rounded_rectangle([v * S for v in box], radius=r * S, fill=fill, outline=outline, width=width * S)
    rr((80, 70, 560, 410), 28, SURF + (255,), (48, 54, 61, 255), 3)
    rr((80, 70, 560, 120), 28, (28, 35, 48, 255))
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse([(112 + i * 32) * S - 9 * S, 95 * S - 9 * S, (112 + i * 32) * S + 9 * S, 95 * S + 9 * S], fill=c + (255,))
    if kind == "projects":
        for i, y in enumerate((160, 220, 280, 340)):
            rr((112, y, 112 + 150 + (i % 2) * 120, y + 30), 10, (48, 54, 61, 255))
            rr((120, y + 8, 134, y + 22), 4, TEAL + (255,))
    else:
        thick_line(d, [(140 * S, 190 * S), (200 * S, 240 * S), (140 * S, 290 * S)], 18 * S, TEAL + (255,))
        thick_line(d, [(230 * S, 300 * S), (330 * S, 300 * S)], 18 * S, WHITE + (255,))
    return img.resize((w, h), Image.LANCZOS)


XML_HEAD = '<?xml version="1.0" encoding="utf-8"?>\n'


def write_values():
    (RES / "values").mkdir(parents=True, exist_ok=True)
    (RES / "values-night").mkdir(parents=True, exist_ok=True)
    (RES / "values" / "ndx_colors.xml").write_text(XML_HEAD + """<resources>
    <color name="ndx_background">#F6F8FA</color>
    <color name="ndx_surface">#FFFFFF</color>
    <color name="ndx_accent">#14968C</color>
    <color name="ndx_on_surface">#1F2328</color>
    <color name="ndx_notification_accent">#2DD4BF</color>
</resources>
""")
    (RES / "values-night" / "ndx_colors.xml").write_text(XML_HEAD + """<resources>
    <color name="ndx_background">#0D1117</color>
    <color name="ndx_surface">#161B22</color>
    <color name="ndx_accent">#2DD4BF</color>
    <color name="ndx_on_surface">#E6EDF3</color>
    <color name="ndx_notification_accent">#2DD4BF</color>
</resources>
""")
    (RES / "values" / "ndx_dimens.xml").write_text(XML_HEAD + """<resources>
    <dimen name="ndx_spacing_s">8dp</dimen>
    <dimen name="ndx_spacing_m">16dp</dimen>
    <dimen name="ndx_spacing_l">24dp</dimen>
    <dimen name="ndx_corner_radius">16dp</dimen>
    <dimen name="ndx_icon_size">24dp</dimen>
</resources>
""")


def main():
    for d, sc in DENSITIES.items():
        save(compose_legacy(px(48, sc), "square"), RES / ("mipmap-" + d) / "icon.png")
        save(compose_legacy(px(48, sc), "round"), RES / ("mipmap-" + d) / "icon_round.png")
        save(adaptive_layer(px(108, sc), "foreground"), RES / ("mipmap-" + d) / "ndx_ic_foreground.png")
        save(monochrome_layer(px(108, sc)), RES / ("mipmap-" + d) / "ndx_ic_monochrome.png")
        save(notification_icon(px(24, sc)), RES / ("drawable-" + d) / "ic_stat_ndx.png")
    save(adaptive_layer(432, "background"), ASSETS / "icon" / "icon_background.png")
    save(adaptive_layer(432, "foreground"), ASSETS / "icon" / "icon_foreground.png")
    save(monochrome_layer(432), ASSETS / "icon" / "icon_monochrome.png")
    save(compose_legacy(192, "square"), ASSETS / "icon" / "icon_legacy.png")
    save(compose_legacy(512, "square"), ASSETS / "icon" / "icon_store_512.png")
    (RES / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
    (RES / "mipmap-anydpi-v26" / "ndx_launcher.xml").write_text(XML_HEAD + """<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ndx_background"/>
    <foreground android:drawable="@mipmap/ndx_ic_foreground"/>
    <monochrome android:drawable="@mipmap/ndx_ic_monochrome"/>
</adaptive-icon>
""")
    s = splash()
    save(s.convert("RGB"), ASSETS / "splash" / "presplash.png")
    save(s.resize((512, 512), Image.LANCZOS), RES / "drawable-nodpi" / "ndx_splash.png")
    save(illustration("projects"), RES / "drawable-nodpi" / "ndx_empty_projects.png")
    save(illustration("terminal"), RES / "drawable-nodpi" / "ndx_empty_terminal.png")
    save(illustration("projects"), ASSETS / "images" / "empty_projects.png")
    save(illustration("terminal"), ASSETS / "images" / "empty_terminal.png")
    save(compose_legacy(128, "square"), ASSETS / "images" / "logo.png")
    # drawable/ : vector logo (resolution independent)
    (RES / "drawable").mkdir(parents=True, exist_ok=True)
    (RES / "drawable" / "ndx_logo_vector.xml").write_text(XML_HEAD + """<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
    <path android:strokeColor="#2DD4BF" android:strokeWidth="11" android:strokeLineCap="round"
        android:strokeLineJoin="round" android:pathData="M37,38 L54,54 L37,70"/>
    <path android:strokeColor="#E6EDF3" android:strokeWidth="11" android:strokeLineCap="round"
        android:pathData="M60,72 L78,72"/>
</vector>
""")
    write_values()
    # ---- register resources in buildozer.spec
    files = sorted(p for p in RES.rglob("*") if p.is_file())
    entries = ["%%(source.dir)s/assets/android/res/%s:%s" % (p.relative_to(RES), p.relative_to(RES)) for p in files]
    spec = ROOT / "buildozer.spec"
    text = spec.read_text()
    text = re.sub(r"^android\.add_resources = .*$", "android.add_resources = " + ",".join(entries), text, flags=re.M)
    spec.write_text(text)
    print("generated %d android resource files; spec updated" % len(files))


if __name__ == "__main__":
    sys.exit(main())
