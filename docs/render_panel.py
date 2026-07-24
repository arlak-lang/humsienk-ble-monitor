#!/usr/bin/env python3
"""Render a faithful mock of the CYD panel for the README (docs/panel.png).

Recreates cyd_monitor/src/main.cpp's drawDashboard(): SOC ring + 7-segment SOC
and watts + status + voltage + battery-health line, at 2x the 320x240 display.
Run: python3 docs/render_panel.py
"""
from PIL import Image, ImageDraw, ImageFont

S = 2                       # scale (display is 320x240)
W, H = 320 * S, 240 * S
GREEN = (0, 220, 90)
ORANGE = (255, 150, 0)
WHITE = (240, 240, 240)
GREY = (55, 55, 55)
LGREY = (170, 170, 170)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# 7-segment segment map per digit (segments a b c d e f g)
SEG = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgcde", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}


def draw_digit(d, x, y, w, h, ch, color, t):
    """Draw one 7-segment digit in box (x,y,w,h) with segment thickness t."""
    if ch not in SEG:
        return
    on = SEG[ch]
    m = y + h / 2
    rects = {
        "a": (x + t, y, x + w - t, y + t),
        "g": (x + t, m - t / 2, x + w - t, m + t / 2),
        "d": (x + t, y + h - t, x + w - t, y + h),
        "f": (x, y + t / 2, x + t, m - t / 2),
        "b": (x + w - t, y + t / 2, x + w, m - t / 2),
        "e": (x, m + t / 2, x + t, y + h - t / 2),
        "c": (x + w - t, m + t / 2, x + w, y + h - t / 2),
    }
    for s in on:
        d.rectangle(rects[s], fill=color)


def draw_number(d, s, cx, cy, dh, color):
    """Draw a 7-seg number centred at (cx, cy) with digit height dh. Returns width."""
    dw = int(dh * 0.60)
    t = max(3, int(dh * 0.14))
    gap = int(dh * 0.12)
    total = len(s) * dw + (len(s) - 1) * gap
    x = cx - total / 2
    y = cy - dh / 2
    for ch in s:
        draw_digit(d, x, y, dw, dh, ch, color, t)
        x += dw + gap
    return total


def main():
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    f = lambda px: ImageFont.truetype(FONT, px)

    # ---- example reading (a charging pack) ----
    soc, watts, volts, charging = 100, 512, 53.6, True
    ncol = GREEN if charging else ORANGE

    # SOC ring (grey track + coloured fill from top, clockwise)
    cx, cy = 90 * S, 118 * S
    mr, width = 80 * S, 16 * S
    box = [cx - mr, cy - mr, cx + mr, cy + mr]
    d.arc(box, 0, 360, fill=GREY, width=width)
    d.arc(box, -90, -90 + 3.6 * soc, fill=GREEN, width=width)

    # SOC number + small % superscript, centred in the ring hole
    num = str(soc)
    dh = 46 * S
    nx = cx - 6 * S
    w = draw_number(d, num, nx, cy, dh, WHITE)
    d.text((nx + w / 2 + 8 * S, cy - dh / 2 + 4 * S), "%", font=f(20 * S),
           fill=WHITE, anchor="lm")

    # Right column: status + big watts + label
    rx = 246 * S
    d.text((rx, 34 * S), "CHARGING" if charging else "IDLE", font=f(15 * S),
           fill=ncol, anchor="mm")
    tri = 12 * S
    d.polygon([(rx - tri, 60 * S), (rx + tri, 60 * S), (rx, 46 * S)], fill=ncol)  # up arrow
    draw_number(d, str(watts), rx, 118 * S, 44 * S, WHITE)
    d.text((rx, 162 * S), "WATTS", font=f(16 * S), fill=WHITE, anchor="mm")

    # Bottom strip: voltage + battery health
    d.text((8 * S, H - 12 * S), f"{volts:.1f} V", font=f(13 * S), fill=LGREY, anchor="lm")
    d.text((W - 8 * S, H - 12 * S), "2 batteries OK", font=f(12 * S), fill=GREEN, anchor="rm")

    # subtle screen bezel
    d.rectangle([1, 1, W - 2, H - 2], outline=(45, 45, 45), width=2)

    out = __file__.rsplit("/", 1)[0] + "/panel.png"
    img.save(out)
    print("wrote", out, img.size)


if __name__ == "__main__":
    main()
