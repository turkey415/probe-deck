#!/usr/bin/env python3
"""Build the hardware photos in docs/hardware/ from the original phone photos.

  python3 tools/annotate_photos.py ~/Dropbox/Claude

Each photo is rebuilt from its pixels only (no EXIF/GPS, XMP, MakerNote or hidden depth/HDR images),
converted to sRGB, given HUD-style callouts in Probe Deck's amber, and saved as a small JPEG.
QR serial stickers are blurred. Coordinates below are for a 1050 x 1400 (portrait) frame.
"""
import glob
import io
import os
import sys

from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageFont, ImageOps

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "hardware")
FONT_DIR = "/usr/share/fonts/nerd-fonts/JetBrainsMono/"
K = 2                                   # draw at 2x, then scale down: smooth lines and text
AMBER, RED, GREEN = (255, 176, 0), (255, 90, 77), (61, 255, 122)
INK = (240, 239, 236)


def font(weight, size):
    return ImageFont.truetype(FONT_DIR + f"JetBrainsMonoNerdFont-{weight}.ttf", size * K)


# Each shot: source photo number, file name, step title, QR boxes to blur, callouts.
# A callout: (label, sub text, label x, label y, [target points], colour, style)
#   style "dot" marks points; "box" marks the rectangle (x1, y1, x2, y2) given as the one target.
SHOTS = [
    (13, "00-probe", "PROBE-01 // POCKET GIGABIT PROBE", [], [
        ("RADXA ZERO 3E + POE HAT", "in a 3D-printed case", 40, 120, [(560, 560)], AMBER, "dot"),
        ("SIDE OPENINGS", "for the USB-C ports", 40, 1150, [(405, 860), (690, 830)], AMBER, "dot"),
    ]),
    (1, "01-parts", "01 // THE PARTS", [(180, 862, 252, 928)], [
        ("4 STANDOFFS + 8 SCREWS", "they carry the PoE power", 380, 26, [(220, 75), (115, 110), (550, 160)], RED, "dot"),
        ("POE HAT", "802.3af, powers the board", 610, 190, [(226, 390)], AMBER, "dot"),
        ("MICROSD", "the boot drive", 14, 186, [(70, 438)], AMBER, "dot"),
        ("RADXA ZERO 3E", "RK3566, gigabit Ethernet", 18, 950, [(224, 775)], AMBER, "dot"),
        ("3D-PRINTED CASE", "bottom + taller lid", 380, 985, [(460, 600), (430, 880)], AMBER, "dot"),
        ("PATCH CABLE", "to a PoE switch port", 18, 1170, [(320, 1110)], AMBER, "dot"),
        ("USB CONSOLE CABLE", "USB-C to the switch's RJ45 console", 420, 1180, [(865, 1125)], GREEN, "dot"),
    ]),
    (3, "02-board", "02 // RADXA ZERO 3E", [(470, 900, 602, 1028)], [
        ("RK3566", "4 x Cortex-A55", 760, 560, [(420, 515, 650, 725)], AMBER, "box"),
        ("LPDDR4 RAM", "", 30, 380, [(365, 345, 595, 495)], AMBER, "box"),
        ("USB-C OTG", "5 V in", 760, 205, [(660, 250)], AMBER, "dot"),
        ("USB-C 3.0", "host", 760, 380, [(675, 415)], AMBER, "dot"),
        ("MICRO HDMI", "", 790, 760, [(710, 835)], AMBER, "dot"),
        ("40-PIN HEADER", "the HAT plugs on here", 30, 620, [(280, 185, 395, 925)], AMBER, "box"),
        ("GIGABIT ETHERNET", "PoE power comes in here", 640, 1180, [(530, 1085)], GREEN, "dot"),
        ("MOUNTING HOLES", "for the standoffs", 30, 1180, [(352, 982)], RED, "dot"),
    ]),
    (2, "03-hat-top", "03 // POE HAT, TOP", [], [
        ("POE CONVERTER", "802.3af in, 5 V out", 600, 1170, [(430, 610, 710, 890)], AMBER, "box"),
        ("40-PIN PASS-THROUGH", "every pin comes through", 60, 230, [(430, 560)], AMBER, "dot"),
        ("POE12 / POE36 HOLES", "PoE power flows through", 30, 470, [(103, 835), (300, 1108)], RED, "dot"),
        ("", "the standoffs here", 30, 0, [], RED, "none"),
    ]),
    (5, "04-hat-under", "04 // STANDOFFS ON THE HAT", [(618, 972, 788, 1094)], [
        ("4 STANDOFFS", "screw them tight:", 600, 40, [(455, 205), (140, 290)], RED, "dot"),
        ("", "they carry the PoE power", 600, 0, [], RED, "none"),
        ("40-PIN SOCKET", "onto the board", 760, 420, [(700, 560)], AMBER, "dot"),
        ("20 MM FAN", "optional, on the", 20, 880, [(440, 690), (190, 530)], AMBER, "dot"),
        ("", "HAT's fan plug", 20, 0, [], AMBER, "none"),
    ]),
    (10, "06-sd-screws", "06 // MICROSD + SCREWS", [], [
        ("MICROSD", "slides in under the board", 40, 1040, [(245, 750)], GREEN, "dot"),
        ("SCREWS INTO THE STANDOFFS", "tighten all four", 40, 300, [(232, 615), (300, 885), (868, 432), (985, 660)], RED, "dot"),
        ("USB-C OTG / 5 V IN", "", 330, 1180, [(420, 915)], AMBER, "dot"),
        ("USB-C 3.0", "", 640, 1040, [(575, 862)], AMBER, "dot"),
    ]),
    (9, "05-stack", "05 // THE STACK", [(540, 712, 668, 798)], [
        ("POE HAT", "on top", 30, 250, [(470, 600)], AMBER, "dot"),
        ("STANDOFFS", "between the boards", 30, 430, [(440, 790), (690, 650)], RED, "dot"),
        ("RADXA ZERO 3E", "underneath", 30, 610, [(470, 905)], AMBER, "dot"),
        ("GIGABIT + POE IN", "", 380, 1150, [(680, 895)], GREEN, "dot"),
    ]),
    (11, "07-case", "07 // INTO THE CASE", [(478, 782, 572, 848)], [
        ("TOP SCREWS", "into the standoffs", 790, 560, [(660, 745), (465, 318)], RED, "dot"),
        ("CASE BOTTOM", "3D printed", 40, 1100, [(300, 960)], AMBER, "dot"),
        ("ETHERNET OPENING", "", 600, 1100, [(515, 925)], GREEN, "dot"),
    ]),
    (14, "08-in-use", "08 // ON THE JOB", [], [
        ("PROBE-01", "PoE powered", 640, 210, [(330, 400)], AMBER, "dot"),
        ("POE + DATA", "from any switch port", 20, 880, [(50, 530)], GREEN, "dot"),
        ("CONSOLE CABLE", "probe USB-C to the", 20, 1040, [(440, 520), (232, 722)], AMBER, "dot"),
        ("", "switch's RJ45 console", 20, 0, [], AMBER, "none"),
    ]),
]


def load(path):
    im = Image.open(path)
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im).convert("RGB")
    if icc:
        im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB")
    im.thumbnail((1400, 1400), Image.LANCZOS)
    return Image.frombytes("RGB", im.size, im.tobytes())  # pixels only, no metadata of any kind


def blur(im, boxes):
    for b in boxes:
        region = im.crop(b).filter(ImageFilter.GaussianBlur(9))
        im.paste(region, b[:2])


def hud(base, title, callouts):
    W, H = base.size
    big = base.resize((W * K, H * K), Image.LANCZOS).convert("RGBA")
    # a little vignette and faint scanlines
    shade = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shade).ellipse((-W * .25, -H * .2, W * 1.25, H * 1.2), fill=255)
    shade = shade.filter(ImageFilter.GaussianBlur(120)).resize(big.size)
    dark = Image.new("RGBA", big.size, (0, 0, 0, 110))
    big = Image.composite(big, Image.alpha_composite(big, dark), shade)
    lines = Image.new("RGBA", big.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for y in range(0, big.size[1], 3 * K):
        ld.line([(0, y), (big.size[0], y)], fill=(0, 0, 0, 22), width=K)
    big = Image.alpha_composite(big, lines)

    glow = Image.new("RGBA", big.size, (0, 0, 0, 0))   # lines and marks, blurred underneath for a soft glow
    marks = Image.new("RGBA", big.size, (0, 0, 0, 0))
    gd, md = ImageDraw.Draw(glow), ImageDraw.Draw(marks)
    boxes = Image.new("RGBA", big.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(boxes)
    f1, f2 = font("Bold", 21), font("Medium", 16)
    s = lambda v: int(v * K)

    merged = []                       # a "none" entry is one more line of text for the callout above it
    for label, sub, lx, ly, targets, col, style in callouts:
        if style == "none":
            merged[-1][1].append(sub)
        else:
            merged.append([label, [sub] if sub else [], lx, ly, targets, col, style])
    for label, subs, lx, ly, targets, col, style in merged:
        tw = max([f1.getlength(label)] + [f2.getlength(x) for x in subs])
        bx1, by1 = s(lx), s(ly)
        bx2, by2 = bx1 + tw + s(30), by1 + s(36 + 22 * len(subs))
        for t in targets:
            if style == "box" and len(t) == 4:
                x1, y1, x2, y2 = (s(v) for v in t)
                c = s(18)
                for (px, py, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
                    for d in (gd, md):
                        d.line([(px, py + dy * c), (px, py), (px + dx * c, py)], fill=col + (255,), width=s(2.5))
                tx, ty = (x1 + x2) // 2, (y1 if by2 < y1 else y2 if by1 > y2 else (y1 + y2) // 2)
                if bx1 > x2:
                    tx, ty = x2, (y1 + y2) // 2
                elif bx2 < x1:
                    tx, ty = x1, (y1 + y2) // 2
            else:
                tx, ty = s(t[0]), s(t[1])
                for d in (gd, md):
                    d.ellipse((tx - s(9), ty - s(9), tx + s(9), ty + s(9)), outline=col + (255,), width=s(2))
                    d.ellipse((tx - s(3), ty - s(3), tx + s(3), ty + s(3)), fill=col + (255,))
            # leader: from the nearest side of the label, with an elbow
            ax = bx1 if tx < bx1 else bx2 if tx > bx2 else (bx1 + bx2) // 2
            ay = (by1 + by2) // 2 if (tx < bx1 or tx > bx2) else (by1 if ty < by1 else by2)
            path = [(ax, ay), ((ax + tx) // 2, ay), (tx + (s(10) if tx < ax else -s(10)) if style == "dot" else tx, ty)] if style == "dot" \
                else [(ax, ay), (tx, ty)]
            for d in (gd, md):
                d.line(path, fill=col + (230,), width=s(2), joint="curve")
        # label box: dark glass, accent bar, corner ticks
        bd.rectangle((bx1, by1, bx2, by2), fill=(10, 10, 9, 200), outline=col + (120,), width=K)
        bd.rectangle((bx1, by1, bx1 + s(4), by2), fill=col + (255,))
        bd.text((bx1 + s(14), by1 + s(7)), label, font=f1, fill=col + (255,))
        for i, line in enumerate(subs):
            bd.text((bx1 + s(14), by1 + s(33 + 22 * i)), line, font=f2, fill=INK + (235,))

    big = Image.alpha_composite(big, glow.filter(ImageFilter.GaussianBlur(s(4))))
    big = Image.alpha_composite(big, marks)
    big = Image.alpha_composite(big, boxes)

    # frame: corner brackets and the title strip
    fd = ImageDraw.Draw(big)
    m, c = s(14), s(46)
    for (px, py, dx, dy) in ((m, m, 1, 1), (big.width - m, m, -1, 1), (m, big.height - m, 1, -1), (big.width - m, big.height - m, -1, -1)):
        fd.line([(px, py + dy * c), (px, py), (px + dx * c, py)], fill=AMBER + (220,), width=s(2))
    ft = font("Bold", 19)
    tw = ft.getlength(title)
    tx, ty = big.width - m - s(22) - tw, big.height - m - s(48)
    fd.rectangle((tx - s(16), ty - s(8), big.width - m - s(10), ty + s(34)), fill=(10, 10, 9, 210))
    fd.rectangle((tx - s(16), ty - s(8), tx - s(12), ty + s(34)), fill=AMBER + (255,))
    fd.text((tx, ty), title, font=ft, fill=AMBER + (255,))
    fd.text((m + s(16), big.height - m - s(40)), "PROBE DECK", font=font("Medium", 14), fill=(200, 198, 190, 170))
    return big.convert("RGB").resize((W, H), Image.LANCZOS)


def main():
    src = sorted(glob.glob(os.path.join(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/Dropbox/Claude"), "*.jpg")))
    os.makedirs(OUT, exist_ok=True)
    for num, name, title, qr, callouts in SHOTS:
        im = load(src[num - 1])
        blur(im, qr)
        out = hud(im, title, callouts)
        out.save(os.path.join(OUT, name + ".jpg"), quality=84, optimize=True, progressive=True)
        print(name, out.size, os.path.getsize(os.path.join(OUT, name + ".jpg")) // 1024, "KB")


if __name__ == "__main__":
    main()
