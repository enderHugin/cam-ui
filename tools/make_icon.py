#!/usr/bin/env python3
"""Genera assets/cam-ui.ico: mismo estilo que el icono de la bandeja (make_tray_image en
camui.py), pero con un anillo (objetivo de cámara) para que se reconozca mejor a tamaños
grandes (acceso del escritorio, menú Inicio, "Aplicaciones instaladas").

Se ejecuta a mano cuando se quiera regenerar el icono (no en cada build_site.sh):
    python3 tools/make_icon.py
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "cam-ui.ico")

BG = (31, 35, 42, 255)
RING = (90, 97, 110, 255)
LENS = (190, 196, 206, 255)
HILITE = (235, 238, 242, 255)


def draw(size):
    S = 4  # supersample para bordes limpios
    big = size * S
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = big // 16
    d.rounded_rectangle((pad, pad, big - 1 - pad, big - 1 - pad),
                         radius=big // 5, fill=BG, outline=RING, width=max(2 * S, big // 24))
    cx = cy = big // 2
    r_outer = big * 0.30
    r_inner = big * 0.17
    d.ellipse((cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer), fill=RING)
    d.ellipse((cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner), fill=(20, 23, 28, 255))
    hr = r_inner * 0.42
    hx, hy = cx - r_inner * 0.38, cy - r_inner * 0.38
    d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=HILITE)
    return im.resize((size, size), Image.LANCZOS)


sizes = [16, 24, 32, 48, 64, 128, 256]
# el plugin ICO de Pillow parte de UNA imagen base (la más grande) y la reduce para cada
# tamaño de "sizes"; no admite escalar hacia arriba, así que la base tiene que ser la 256.
base = draw(max(sizes))
os.makedirs(os.path.dirname(OUT), exist_ok=True)
base.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
print("escrito:", OUT, os.path.getsize(OUT), "bytes")
