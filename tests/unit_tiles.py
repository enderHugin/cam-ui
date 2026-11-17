"""_tile_changes: los fps «~» del rótulo (motor de siempre) miran si cambia lo que se ve en cada
casilla. Al reducir el mosaico a la vista previa, los píxeles de la junta entre dos casillas
mezclan las dos: una cámara parada junto a otra que se mueve no debe contar como «cambia»
(pasaba: una cámara de red cortada, congelada, salía con los fps de su vecina). Con ffmpeg de
verdad: el mismo mosaico reducido a la vista previa como lo hace cam-ui."""
import os
import sys
import subprocess

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

W, H = 960, 240                       # tres casillas de 320×240, en fila
TILES = [(0, 0, 320, 240), (320, 0, 320, 240), (640, 0, 320, 240)]
PW, PH = 880, 220                     # vista previa (como la de cam-ui: 880 de ancho)


def mosaic(k):
    """La de en medio, quieta; las de los lados cambian en cada fotograma (con detalle hasta
    el mismo borde)."""
    f = np.zeros((H, W, 3), np.uint8)
    rng = np.random.default_rng(k)
    f[:, :320] = rng.integers(0, 255, (H, 320, 3), dtype=np.uint8)
    f[:, 320:640] = 128
    f[:, 640:] = rng.integers(0, 255, (H, 320, 3), dtype=np.uint8)
    return f


def preview(frames):
    """Reduce los fotogramas con ffmpeg igual que la vista previa de cam-ui (fast_bilinear)."""
    raw = b"".join(f.tobytes() for f in frames)
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                        "-pix_fmt", "bgr24", "-s", "{0}x{1}".format(W, H), "-i", "-",
                        "-vf", "scale={0}:{1}:flags=fast_bilinear".format(PW, PH),
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                       input=raw, capture_output=True, check=True)
    n = len(p.stdout) // (PW * PH * 3)
    return [np.frombuffer(p.stdout, np.uint8, PW * PH * 3, i * PW * PH * 3).reshape(PH, PW, 3)
            for i in range(n)]


pv = preview([mosaic(k) for k in range(12)])
assert len(pv) == 12, len(pv)
sig = [None] * 3
counts = [0, 0, 0]
for fr in pv:
    for i in camui._tile_changes(fr, TILES, W, sig):
        counts[i] += 1
assert counts[0] == 12 and counts[2] == 12, counts
assert counts[1] == 1, ("la quieta cuenta como que cambia (bordes de las vecinas)", counts)
print("1) casilla quieta entre dos que se mueven: {0} cambios en 12 fotogramas (solo el primero; "
      "las vecinas, {1} y {2}) OK".format(counts[1], counts[0], counts[2]))

# 2) algo pequeño que se mueve DENTRO de la casilla sí cuenta (no se pierde por mirar menos)
f0 = np.full((PH, PW, 3), 50, np.uint8)
f1 = f0.copy()
f1[100:106, 400:406] = 200                  # un punto de 6×6 en mitad de la casilla del centro
sig = [None] * 3
camui._tile_changes(f0, TILES, W, sig)
assert camui._tile_changes(f1, TILES, W, sig) == [1]
print("2) un detalle de 6×6 píxeles que cambia en mitad de la casilla sí cuenta OK")
print("TODO OK unit_tiles")
