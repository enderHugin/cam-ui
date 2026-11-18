"""Dos cam-ui encadenados, como en casa del usuario (packs 49-50): «Omen» tiene un móvil y su
salida «En la red»; «ROG» tiene a Omen como cámara. Cambia la forma del móvil en Omen: su mosaico
cambia en el acto, y con él lo que manda. En ROG, la casilla de Omen NUNCA debe verse estirada («un
vídeo muy alargado»): mientras no se recoloca, entera y con bandas negras; sin grabar, se recoloca
sola en unos segundos. Grabando: si solo cambia de forma (16:9 → 4:3), se queda con bandas negras y
un aviso; si se GIRA (apaisada → vertical), el mosaico se recoloca sin dejar de grabar (trozos que
al parar se juntan en un solo vídeo, del mismo tamaño).

Hace falta un display (xvfb-run -a python3 tests/ui_chain_reshape.py); Omen corre como otro
proceso (este mismo archivo con «--omen») en el mismo display."""
import io
import os
import sys
import json
import time
import socket
import subprocess
import threading

import numpy as np
import tkinter as tk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROLE = "omen" if "--omen" in sys.argv else "rog"
HOME = os.path.join(HERE, "_ui_chain_reshape_" + ROLE + "_home")
os.makedirs(HOME, exist_ok=True)
SIG = os.path.join(HERE, "_ui_chain_reshape_rog_home")    # señales entre los dos procesos
for n_ in ("PHONECAM_CERT_FILE", "PHONECAM_KEY_FILE", "PHONECAM_CA_CERT_FILE", "PHONECAM_CA_KEY_FILE"):
    setattr(camui, n_, os.path.join(HOME, n_.lower() + ".pem"))
camui.PHONECAM_DEVICES_FILE = os.path.join(HOME, "devices.json")
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sig(name):
    return os.path.join(SIG, name)


BAND = 40           # franja roja horizontal de 40 px en lo que manda el móvil


def phone_jpeg(w, h, k):
    a = np.full((h, w, 3), 70, np.uint8)
    a[:, w // 3:2 * w // 3, 2] = 200
    a[h // 2 - BAND // 2:h // 2 + BAND // 2, :, :] = (250, 40, 40)
    a[0:4, 0:4, 1] = k % 255
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=70)
    return b.getvalue()


root = tk.Tk()
root.withdraw()


def pump(secs, cond=None, step=0.03):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond is not None and cond():
            return True
        time.sleep(step)
    return False


if ROLE == "omen":
    IPPORT = int(sys.argv[sys.argv.index("--omen") + 1])
    with open(camui.CONFIG, "w", encoding="utf-8") as f:
        json.dump({"version": 3, "fps": 30, "phonecam": {"port": free_port()},
                   "ipout": {"on": False, "port": IPPORT, "size": [1280, 720]}}, f)
    app = camui.CamApp(root)
    app._tray_asked = True
    pump(1)
    app._ip_enable(quiet=True)
    sid = app.phonecam.new_session("iPhone")
    shape = {"wh": (1280, 720)}

    def wanted():
        try:
            with open(sig("FORMA")) as f:
                w, h = f.read().strip().split("x")
            return int(w), int(h)
        except Exception:
            return 1280, 720

    def phone():
        k = 0
        while True:
            k += 1
            app.phonecam.feed(sid, phone_jpeg(shape["wh"][0], shape["wh"][1], k), seq=k)
            time.sleep(1 / 15.0)
    threading.Thread(target=phone, daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < 300 and not os.path.exists(sig("FIN")):
        pump(0.3)
        shape["wh"] = wanted()
        if app.previewing and app.ipout_on and not app.busy and app.eng.frame_count > 10:
            open(sig("OMEN_LISTO"), "w").close()
    app.eng.release_devices()
    app._ip_disable()
    app._phonecam_disable()
    root.destroy()
    sys.exit(0)

# ---------------------------------------------------------------- ROG
for n in ("FORMA", "FIN", "OMEN_LISTO"):
    if os.path.exists(sig(n)):
        os.remove(sig(n))
IPPORT = free_port()
omen = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--omen", str(IPPORT)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "fps": 30, "phonecam": {"port": free_port()}, "dir": HOME}, f)
app = camui.CamApp(root)
app._tray_asked = True
LAB = "🌐 Omen"


def omen_view():
    """En la vista previa de ROG, dentro de la casilla de Omen: la forma de la imagen que se ve
    (sin las bandas negras) y el grosor de la franja roja respecto a su alto."""
    fr, m = app.eng.latest, app.eng.meta
    if fr is None or LAB not in app._run_labels or not m.get("tiles"):
        return None
    t = m["tiles"][app._run_labels.index(LAB)]
    sc = fr.shape[1] / float(m["W"])
    x0, y0, w, h = [int(round(v * sc)) for v in t]
    reg = fr[y0 + 2:y0 + h - 2, x0 + 2:x0 + w - 2].astype(int)
    lit = reg.sum(axis=2) > 60
    cols, rows = np.where(lit.any(axis=0))[0], np.where(lit.any(axis=1))[0]
    if not len(cols) or not len(rows):
        return None
    bw, bh = cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1
    red = (reg[:, :, 2] > 180) & (reg[:, :, 1] < 110) & (reg[:, :, 0] < 110)      # BGR
    band = int((red[:, cols[0]:cols[-1] + 1].mean(axis=1) > 0.5).sum())
    return {"tile": (w, h), "aspect": bw / float(bh), "band": band / float(bh)}


def forma(w, h):
    with open(sig("FORMA") + ".tmp", "w") as f:
        f.write("{0}x{1}".format(w, h))
    os.replace(sig("FORMA") + ".tmp", sig("FORMA"))


def check_not_stretched(v):
    """Apaisada a lo ancho con la franja gruesa (como estaba), o en vertical con la franja fina
    (girado y bien): nunca apaisada con la franja fina (el vertical estirado a lo ancho)."""
    if v is None:
        return True
    if v["aspect"] > 1.2:
        return abs(v["band"] - BAND / 720.0) < 0.02, v
    return abs(v["band"] - BAND / 1280.0) < 0.015, v


try:
    t_end = time.time() + 60
    while not os.path.exists(sig("OMEN_LISTO")) and time.time() < t_end:
        time.sleep(0.2)
    assert os.path.exists(sig("OMEN_LISTO")), "Omen no arrancó"
    app.net_cams[LAB] = {"name": "Omen", "alt": None, "label": LAB, "net": True,
                         "spec": "http://127.0.0.1:{0}/stream.mjpg".format(IPPORT)}
    app._refresh_devices()
    assert pump(8, lambda: LAB in app.vdev and not app.busy)
    app.cams[LAB].use = True
    app.order.append(LAB)
    app._on_change()
    assert pump(40, lambda: app.previewing and app.eng.frame_count > 10 and not app.busy and
                (omen_view() or {}).get("aspect", 0) > 1.5)
    v0 = omen_view()
    print("0) ROG ve a Omen apaisado: casilla {0}, imagen {1:.2f}, franja {2:.3f} OK".format(
        v0["tile"], v0["aspect"], v0["band"]))

    # 1) sin grabar: gira el móvil en Omen -> en ROG, nunca estirado, y se recoloca solo
    forma(720, 1280)
    seen = []

    def portrait_ok():
        v = omen_view()
        ok, _v = check_not_stretched(v) if v else (True, v)
        seen.append((round(time.time(), 1), v, ok))
        assert ok, ("estirada", v)
        return v is not None and v["tile"][1] > v["tile"][0] and v["aspect"] < 0.8
    assert pump(30, portrait_ok, step=0.2), seen[-3:]
    v1 = omen_view()
    print("1) sin grabar, se gira el móvil en Omen: en ROG la casilla pasa a {0} en vertical, sin "
          "verse estirada en ninguna de {1} miradas OK".format(v1["tile"], len(seen)))

    # 2) grabando, solo cambia de forma (16:9 -> 4:3, sin girar): misma casilla, bandas, aviso
    forma(1280, 720)
    assert pump(30, lambda: (omen_view() or {}).get("aspect", 0) > 1.5 and
                omen_view()["tile"][0] > omen_view()["tile"][1] and not app.busy, step=0.2)
    app.toggle_rec()
    assert pump(15, lambda: app.recording and not app.busy and app.eng.frame_count > 10)
    t_rec0 = time.time()
    WH = (app.eng.meta["W"], app.eng.meta["H"])
    tile_rec = omen_view()["tile"]
    pump(2)
    forma(960, 720)
    assert pump(20, lambda: abs((omen_view() or {}).get("aspect", 0) - 4 / 3.0) < 0.1, step=0.2), \
        omen_view()
    pump(4)
    vr = omen_view()
    assert vr["tile"] == tile_rec and abs(vr["aspect"] - 4 / 3.0) < 0.1, (vr, tile_rec)
    assert "ha cambiado de forma" in app.status.cget("text"), app.status.cget("text")
    assert len(app._rec_parts) == 1
    print("2) grabando, de 16:9 a 4:3 (sin girar): la casilla sigue {0} con Omen entero ({1:.2f}) "
          "y bandas negras; aviso «{2}» OK".format(vr["tile"], vr["aspect"],
                                                   app.status.cget("text")[:55]))

    # 3) grabando, se GIRA: se recoloca sin dejar de grabar, con el mismo tamaño de vídeo
    forma(720, 1280)
    seen = []

    def turned():
        v = omen_view()
        ok, _v = check_not_stretched(v) if v else (True, v)
        seen.append((v, ok))
        assert ok, ("estirada grabando", v)
        return app.recording and not app.busy and v is not None and v["tile"][1] > v["tile"][0] \
            and v["aspect"] < 0.8 and app.eng.frame_count > 10
    assert pump(30, turned, step=0.2), (seen[-3:], app.status.cget("text"))
    assert (app.eng.meta["W"], app.eng.meta["H"]) == WH, (app.eng.meta["W"], app.eng.meta["H"], WH)
    assert len(app._rec_parts) == 2 and "girada" in app.status.cget("text"), \
        (app._rec_parts, app.status.cget("text"))
    vt = omen_view()
    print("3) grabando, se gira: mosaico recolocado sin dejar de grabar (casilla {0}, vídeo {1}×{2} "
          "igual), «…{3}» OK".format(vt["tile"], WH[0], WH[1], app.status.cget("text")[-45:]))
    pump(4)

    # 4) al parar: un solo archivo, del mismo tamaño, con los dos repartos; sin trozos sueltos
    parts = list(app._rec_parts)
    final = app.rec_final
    app.toggle_rec()
    t_rec = time.time() - t_rec0
    assert pump(60, lambda: not app.recording and not app.busy and os.path.isfile(final) and
                "Grabado" in app.status.cget("text"), step=0.2), app.status.cget("text")
    assert not any(os.path.exists(p) for p in parts if p != final), parts
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "stream=width,height:format=duration", "-of", "json", final],
                        capture_output=True, text=True)
    info = json.loads(pr.stdout)
    st = info["streams"][0]
    dur = float(info["format"]["duration"])
    assert (st["width"], st["height"]) == WH, (st, WH)
    assert t_rec - 6 < dur < t_rec + 2, (dur, t_rec)

    def frame_at(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "{0:.2f}".format(t), "-i", final,
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        a = np.frombuffer(raw, np.uint8).reshape(WH[1], WH[0], 3).astype(int)
        lit = a.sum(axis=2) > 60
        cols, rows = np.where(lit.any(axis=0))[0], np.where(lit.any(axis=1))[0]
        return (cols[-1] - cols[0] + 1) / float(rows[-1] - rows[0] + 1)
    a0, a1 = frame_at(0.5), frame_at(dur - 1.0)
    assert a0 > 1.2 and a1 < 0.8, (a0, a1)
    print("4) al parar: un solo vídeo {0}×{1} de {2:.1f} s (grabados {3:.1f} s), que empieza "
          "apaisado ({4:.2f}) y acaba en vertical ({5:.2f}); sin trozos sueltos OK".format(
              WH[0], WH[1], dur, t_rec, a0, a1))
finally:
    open(sig("FIN"), "w").close()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    root.destroy()
    try:
        omen.wait(20)
    except Exception:
        omen.kill()
print("TODO OK ui_chain_reshape")
