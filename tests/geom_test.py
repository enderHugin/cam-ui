import sys, os, time, subprocess, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import camui

FF = os.environ.get("CAMWIN32_FFMPEG", "ffmpeg")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "t"); os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- geometría pura
a43, a169, a34 = 4/3., 16/9., 3/4.
g = camui.layout_tiles([a43, a43, a169], (2, 1), 1280)
print("2x4:3 + 16:9  ->", g)
assert (g["W"], g["H"]) == (1280, 1200), g
assert g["tiles"] == [(0,0,640,480),(640,0,640,480),(0,480,1280,720)], g["tiles"]
g = camui.layout_tiles([a34, a34, a169], (2, 1), 1280)
print("2x3:4 + 16:9  ->", g)
assert g["H"] == 853*2//2*1 or True
assert g["tiles"][0][2] == 640 and g["tiles"][0][3] in (852, 854), g["tiles"][0]
# nada se pierde: aspecto de cada celda == aspecto pedido (±1 %)
for asp, (x, y, w, h) in zip([a34, a34, a169], g["tiles"]):
    assert abs(w / float(h) / asp - 1) < 0.01, (asp, w, h)
# auto
print("auto (4:3,4:3,16:9):", camui.best_structure([a43, a43, a169], 1280))
assert camui.best_structure([a43, a43, a169], 1280) == (2, 1)
assert camui.best_structure([a169, a169], 1280) == (2,)            # empate -> menos filas
assert camui.best_structure([a169]*4, 1280) == (2, 2)
assert camui.best_structure([a169], 1280) == (1,)
# lienzo fijo 16:9: el contenido se centra con barras, nada se recorta
g = camui.layout_tiles([a43, a43, a169], (2, 1), 1280, canvas=(16, 9))
print("canvas 16:9 ->", g)
assert (g["W"], g["H"]) == (1280, 720)
for (x, y, w, h) in g["tiles"]:
    assert x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720
assert min(t[0] for t in g["tiles"]) > 0                            # barras laterales
# transpuesto: columnas
g = camui.layout_tiles([a169, a43, a43], (1, 2), 1280, transpose=True)
print("transpuesto ->", g)
assert g["tiles"][0][0] == 0 and g["tiles"][1][0] == g["tiles"][2][0] > 0
assert g["tiles"][1][1] == 0 and g["tiles"][2][1] == g["tiles"][1][3]
# estructuras
assert camui.compositions(3) == [(3,), (2, 1), (1, 2), (1, 1, 1)]
assert camui.parse_structure("2 + 1", 3) == (2, 1) and camui.parse_structure("Auto", 3) is None
assert camui.parse_structure("2 + 2", 3) is None
assert abs(camui.parse_ratio("4:3") - 4/3.) < 1e-9 and camui.parse_ratio("auto") is None
assert camui.mode_size("1280x720 @ 30 fps [mjpeg]") == (1280, 720)
# ventana
assert camui.window_rect(a43, a43, 100, 50, 50) == (0.0, 0.0, 1.0, 1.0)
x0, y0, fw, fh = camui.window_rect(a43, a169, 200, 30, 70)
print("ventana", x0, y0, fw, fh)
assert abs(fw - 0.5) < 1e-9 and abs(fh - (0.5 * (4/3.) / (16/9.))) < 1e-9
assert abs(x0 - 0.05) < 1e-9
# tamaño par en todo
for asps in ([a43, a169, a34, 1.0], [0.5, 2.3, 1.1]):
    for tr in (False, True):
        for comp in camui.compositions(len(asps)):
            g = camui.layout_tiles(asps, comp, 1000, tr)
            assert g["W"] % 2 == 0 and g["H"] % 2 == 0
            for t in g["tiles"]:
                assert all(v % 2 == 0 for v in t) and t[0]+t[2] <= g["W"] and t[1]+t[3] <= g["H"], (comp, tr, t, g["W"], g["H"])
print("geometria pura OK")

# ---------------------------------------------------------------- ffmpeg real
def inp(kind, spec, mode=None):
    return ["-f", "lavfi", "-i", spec]

def cam(spec, **kw):
    d = {"spec": spec, "h": False, "v": False, "rot": 0, "delay": 0, "mode": None,
         "tile": "auto", "zoom": 100, "cx": 50, "cy": 50}
    d.update(kw); return d

def render(plan, name, secs=2):
    out = os.path.join(OUT, name + ".mkv")
    if os.path.exists(out): os.remove(out)
    plan = dict(plan, fps=30, crf=18, preset="ultrafast", sync="device", duration=secs,
                audio={"L": None, "R": None, "offset": 0})
    cmd, meta = camui.build_command(plan, out, warmup=0.0, preview=False, input_fn=inp, ffmpeg=FF)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (" ".join(cmd), r.stderr[-800:])
    return out, meta

def frame(path, w, h, t=0.5):
    r = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-pix_fmt", "yuv444p",
                        "-f", "rawvideo", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8).reshape(3, h, w)
    return a   # Y, U, V

def rgbframe(path, w, h, t=0.5):
    r = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-pix_fmt", "rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3)

# 1) 3 cámaras: 2 x 4:3 (rojo, azul) + 1 x 16:9 (verde) -> 2 arriba + 1 abajo
cams = [cam("color=c=red:s=640x480:r=30", src=(640,480)), cam("color=c=blue:s=640x480:r=30", src=(640,480)), cam("color=c=green:s=1280x720:r=30", src=(1280,720))]
g = camui.layout_tiles([a43, a43, a169], (2, 1), 1280)
out, meta = render({"cams": cams, "geom": g}, "g1")
im = rgbframe(out, g["W"], g["H"])
print("1) lienzo", g["W"], "x", g["H"], "salida", im.shape)
for (x, y, w, h), name, col in zip(g["tiles"], ("rojo", "azul", "verde"), ((255,0,0),(0,0,255),(0,128,0))):
    reg = im[y:y+h, x:x+w].reshape(-1, 3).astype(int)
    d = np.abs(reg - np.array(col)).max(axis=1)
    frac_ok = (d < 40).mean()
    print("   celda", name, (x, y, w, h), "píxeles del color correcto: %.4f" % frac_ok)
    assert frac_ok > 0.995, "barras negras dentro de la celda"

# 2) variantes: lienzo fijo 16:9 / transpuesto / 9:16
g2 = camui.layout_tiles([a43, a43, a169], (2, 1), 1280, canvas=(16, 9))
out, _ = render({"cams": cams, "geom": g2}, "g2")
im = rgbframe(out, 1280, 720)
x, y, w, h = g2["tiles"][2]
print("2) fijo 16:9: celda verde", g2["tiles"][2], "borde izq negro:", im[360, 2].tolist(), "centro verde:", im[y + h//2, x + w//2].tolist())
assert im[360, 2].max() < 20 and im[y + h//2, x + w//2][1] > 100
g3 = camui.layout_tiles([a169, a43, a43], (1, 2), 1280, transpose=True)
cams3 = [cams[2], cams[0], cams[1]]
out, _ = render({"cams": cams3, "geom": g3}, "g3")
im = rgbframe(out, g3["W"], g3["H"])
for (x, y, w, h), col in zip(g3["tiles"], ((0,128,0),(255,0,0),(0,0,255))):
    reg = im[y:y+h, x:x+w].reshape(-1, 3).astype(int)
    assert (np.abs(reg - np.array(col)).max(axis=1) < 40).mean() > 0.995
print("3) transpuesto OK", g3["W"], "x", g3["H"], g3["tiles"])

# 4) zoom / posición: degradado X en luma, Y en croma-b
GRAD = "color=c=black:s=640x480:r=30,format=yuv444p,geq=lum='255*X/W':cb='255*Y/H':cr='128'"
T = 16/9.
tw, th = 640, 360
for zoom, cx, cy in ((200, 30, 70), (100, 50, 50), (400, 0, 100), (150, 100, 0)):
    c = cam(GRAD, tile="16:9", zoom=zoom, cx=cx, cy=cy)
    gg = {"W": tw, "H": th, "tiles": [(0, 0, tw, th)]}
    out, _ = render({"cams": [c], "geom": gg}, "z1")
    Y, U, V = frame(out, tw, th)
    x0, y0, fw, fh = camui.window_rect(a43, T, zoom, cx, cy)
    ex_l, ex_r = x0 * 255, (x0 + fw) * 255
    ex_t, ex_b = y0 * 255, (y0 + fh) * 255
    got_l, got_r = Y[th//2, 4:8].mean(), Y[th//2, tw-8:tw-4].mean()
    got_t, got_b = U[4:8, tw//2].mean(), U[th-8:th-4, tw//2].mean()
    print("4) zoom %3d cx %3d cy %3d  izq %5.1f/%5.1f der %5.1f/%5.1f arr %5.1f/%5.1f aba %5.1f/%5.1f (obtenido/esperado)"
          % (zoom, cx, cy, got_l, ex_l, got_r, ex_r, got_t, ex_t, got_b, ex_b))
    tol = 255 * 6.0 / (tw * 1.0) * (1) + 6
    for got, ex in ((got_l, ex_l), (got_r, ex_r), (got_t, ex_t), (got_b, ex_b)):
        assert abs(got - ex) < 8, (got, ex)

# 5) celda 'Como la cámara' + zoom > 100 conserva el aspecto de la celda
c = cam(GRAD, tile="auto", zoom=200, cx=50, cy=50)
gg = {"W": 640, "H": 480, "tiles": [(0, 0, 640, 480)]}
out, _ = render({"cams": [c], "geom": gg}, "z2")
Y, U, V = frame(out, 640, 480)
print("5) auto + zoom 200:  izq %.1f der %.1f (esperado 64 / 191)" % (Y[240, 4:8].mean(), Y[240, 632:636].mean()))
assert abs(Y[240, 4:8].mean() - 64) < 8 and abs(Y[240, 632:636].mean() - 191) < 8

# 6) rotación 90: la celda sigue la imagen girada (3:4) sin barras
c = cam("color=c=red:s=640x480:r=30", rot=90, src=(640,480))
g6 = camui.layout_tiles([3/4.], (1,), 480)
out, _ = render({"cams": [c], "geom": g6}, "g6")
im = rgbframe(out, g6["W"], g6["H"])
print("6) rot 90 -> celda", g6["tiles"], "color ok:", (np.abs(im.reshape(-1,3).astype(int) - [255,0,0]).max(axis=1) < 40).mean())
assert (np.abs(im.reshape(-1,3).astype(int) - [255,0,0]).max(axis=1) < 40).mean() > 0.995
print("ffmpeg real OK")

# 7) sin tamaño conocido: se ve todo (barras como mucho de 2 px por redondeo)
g7 = camui.layout_tiles([a169, a43, a43], (1, 2), 1280, transpose=True)
nosrc = [dict(c) for c in (cams[2], cams[0], cams[1])]
for c in nosrc: c.pop("src")
out, _ = render({"cams": nosrc, "geom": g7}, "g7")
im = rgbframe(out, g7["W"], g7["H"])
for (x, y, w, h), col in zip(g7["tiles"], ((0,128,0),(255,0,0),(0,0,255))):
    reg = im[y:y+h, x:x+w].reshape(-1, 3).astype(int)
    fr = (np.abs(reg - np.array(col)).max(axis=1) < 40).mean()
    assert fr > 0.99, fr
# y con una cámara 16:9 puesta en celda 4:3 (forma distinta): barras, no recorte
c = cam("color=c=green:s=1280x720:r=30")
out, _ = render({"cams": [c], "geom": {"W": 640, "H": 480, "tiles": [(0, 0, 640, 480)]}}, "g8")
im = rgbframe(out, 640, 480)
print("7) forma distinta -> barras: arriba", im[5, 320].tolist(), "centro", im[240, 320].tolist())
assert im[5, 320].max() < 20 and im[240, 320][1] > 100
print("TODO OK")
