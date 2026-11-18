"""Disposición del mosaico en la aplicación de verdad (CamApp con su ventana Tk; hace falta un
display: xvfb-run -a python3 tests/ui_layout_auto.py). Pack 48:

1) una cámara de red MJPEG «cualquiera» (IP Webcam, ESP32-CAM...) que cambia de forma a mitad
   (640×360 → 360×640) recoloca el mosaico sola -- antes seguía al tamaño del principio,
   deformada --; y lo mismo la cámara del móvil al girarlo;
2) ★ cámaras destacadas: con «Auto», van primero y bastante más grandes que las demás (hasta
   dos; una tercera no se deja), se guardan, se ven en el mapa y en la galería, y el mosaico de
   verdad sale así; con una disposición elegida a mano, no cambian nada."""
import io
import os
import sys
import json
import time
import socket
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import numpy as np
import tkinter as tk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_layout_auto_home")
os.makedirs(HOME, exist_ok=True)
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


with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "fps": 30, "phonecam": {"port": free_port()}}, f)

SHAPES = {}


def jpeg(w, h, k):
    a = np.full((h, w, 3), 80, np.uint8)
    a[h // 3:2 * h // 3, :, 1] = (k * 11) % 255
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=70)
    return b.getvalue()


class H(BaseHTTPRequestHandler):
    """Cámara MJPEG «cualquiera» (no cam-ui): /<nombre> con su forma en SHAPES."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.strip("/")
        if name not in SHAPES:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        k = 0
        try:
            while True:
                k += 1
                w, h = SHAPES[name]
                d = jpeg(w, h, k)
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                 % len(d) + d + b"\r\n")
                time.sleep(1 / 20.0)
        except Exception:
            return


PORT = free_port()
srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
app._tray_asked = True


def pump(cond, secs, step=0.05):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond():
            return True
        time.sleep(step)
    return False


def add_net(name, w, h):
    SHAPES[name] = (w, h)
    lab = "🌐 " + name
    app.net_cams[lab] = {"name": name, "alt": None, "label": lab, "net": True,
                         "spec": "http://127.0.0.1:{0}/{1}".format(PORT, name)}
    return lab


def use(labs):
    app._refresh_devices()
    assert pump(lambda: all(l in app.vdev for l in labs) and not app.busy, 10)
    for l in labs:
        app.cams[l].use = True
        if l not in app.order:
            app.order.append(l)
    app._on_change()


def tile_of(lab):
    m = app.eng.meta
    i = app._run_labels.index(lab)
    return m["tiles"][i]


phone_stop = threading.Event()
try:
    assert app.v_hub.get() is True
    # ---- 1) cambio de forma de una cámara de red cualquiera y del móvil
    A, B = add_net("ipwebcam", 640, 360), add_net("esp32", 640, 360)
    use([A, B])
    assert pump(lambda: app.previewing and app.eng.frame_count > 10 and not app.busy, 30), \
        app.eng.last_errors()
    assert pump(lambda: app.hub.sources and all(s.reader == "mjpeg" for s in app.hub.sources.values()), 10), \
        [(s.label, s.reader) for s in app.hub.sources.values()]
    tA0 = tile_of(A)
    assert tA0[2] > tA0[3], tA0                            # apaisada
    SHAPES["ipwebcam"] = (360, 640)                        # la cámara se pone en vertical
    assert pump(lambda: app.previewing and not app.busy and app.eng.meta.get("tiles") and
                A in app._run_labels and tile_of(A)[3] > tile_of(A)[2], 25), \
        (app.eng.meta.get("tiles"), app.cams[A].src)
    tA1 = tile_of(A)
    print("1a) cámara MJPEG cualquiera que pasa de 640×360 a 360×640: el mosaico se recoloca "
          "solo (su casilla {0}×{1} → {2}×{3}) OK".format(tA0[2], tA0[3], tA1[2], tA1[3]))

    # el móvil que se gira
    assert pump(lambda: app.phonecam.active, 10)
    sid = app.phonecam.new_session("iPhone")
    pshape = {"wh": (640, 480)}

    def phone():
        k = 0
        while not phone_stop.is_set():
            k += 1
            app.phonecam.feed(sid, jpeg(pshape["wh"][0], pshape["wh"][1], k), seq=k)
            time.sleep(1 / 15.0)
    threading.Thread(target=phone, daemon=True).start()
    assert pump(lambda: any("iPhone" in l for l in app.order) and app.previewing and
                not app.busy and any("iPhone" in l for l in app._run_labels), 30), app.order
    P = [l for l in app.order if "iPhone" in l][0]
    assert pump(lambda: P in app._run_labels and tile_of(P)[2] > tile_of(P)[3], 15)
    pshape["wh"] = (480, 640)
    assert pump(lambda: P in app._run_labels and not app.busy and tile_of(P)[3] > tile_of(P)[2], 25), \
        (app.eng.meta.get("tiles"), app.cams[P].src)
    print("1b) el móvil al girarlo (640×480 → 480×640): el mosaico se recoloca solo OK")
    phone_stop.set()

    # ---- 2) destacadas
    SHAPES["ipwebcam"] = (640, 360)
    C, D = add_net("salon", 640, 480), add_net("patio", 640, 480)
    app.cams[P].use = False
    if P in app.order:
        app.order.remove(P)
    use([A, B, C, D])
    for l in (A, B, C, D):
        app.cams[l].src = SHAPES[l[2:]]
    app._redraw_map()
    assert app.v_struct.get() == camui.AUTO
    g0 = app._geometry()
    assert not g0.get("feat")
    # ★ a «salon» con su casilla en «Ajustes de la cámara»
    app.sel = C
    app._bind_detail()
    assert str(app.w_feat.cget("variable")) == str(app.cams[C].feat)
    app.w_feat.invoke()
    assert app.cams[C].feat.get() is True
    g1 = app._geometry()
    assert g1["labels"][0] == C and g1["feat"][0] == 1, g1["labels"]
    areas = [w * h for (_x, _y, w, h) in g1["tiles"]]
    assert areas[0] >= camui.FEAT_MIN_RATIO * max(areas[1:]), areas
    assert "★" in " ".join(app.map.itemcget(i, "text") for i in app.map.find_all()
                           if app.map.type(i) == "text")
    assert any("AUTO ★" == app.gal.itemcget(i, "text") for i in app.gal.find_all()
               if app.gal.type(i) == "text")
    assert "destacada" in app.lbl_size.cget("text"), app.lbl_size.cget("text")
    # el mosaico de verdad sale así
    assert pump(lambda: app.previewing and not app.busy and app._run_labels[:1] == [C] and
                app.eng.frame_count > 5, 25), app._run_labels
    ma = [w * h for (_x, _y, w, h) in app.eng.meta["tiles"]]
    assert ma[0] >= camui.FEAT_MIN_RATIO * max(ma[1:]), ma
    print("2a) ★ «salon»: con «Auto» va primera y {0:.0f}× más grande que la mayor de las demás; "
          "★ en el mapa y «AUTO ★» en la galería; el mosaico de verdad, igual OK".format(
              ma[0] / float(max(ma[1:]))))
    # otra más: dos destacadas, las dos más grandes
    app.cams[A].feat.set(True)
    g2 = app._geometry()
    assert set(g2["labels"][:2]) == {A, C} and g2["feat"][0] == 2, g2["labels"]
    a2 = [w * h for (_x, _y, w, h) in g2["tiles"]]
    assert min(a2[:2]) >= camui.FEAT_MIN_RATIO * max(a2[2:]), a2
    # una tercera no se deja
    app.sel = B
    app._bind_detail()
    app.w_feat.invoke()
    assert app.cams[B].feat.get() is False
    assert "Como mucho 2" in app.status.cget("text"), app.status.cget("text")
    print("2b) dos ★ (las dos más grandes que cualquiera de las demás); una tercera no se deja: "
          "«{0}» OK".format(app.status.cget("text")[:70]))
    # se guardan
    app.save_config()
    with open(camui.CONFIG, encoding="utf-8") as f:
        cc = json.load(f)["cams"]
    assert cc[C]["feat"] is True and cc[A]["feat"] is True and not cc[B].get("feat"), cc[C]
    # con una disposición elegida a mano, no cambian nada
    app.v_struct.set("2 + 2")
    g3 = app._geometry()
    assert not g3.get("feat") and g3["labels"] == [l for l in app.order if l in app.cams and
                                                   app.cams[l].use]
    a3 = [w * h for (_x, _y, w, h) in g3["tiles"]]
    assert max(a3) < 1.6 * min(a3), a3
    app.v_struct.set(camui.AUTO)
    print("2c) se guardan en config.json; con «2 + 2» elegida a mano, todas iguales OK")
finally:
    phone_stop.set()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    try:
        app._phonecam_disable()
    except Exception:
        pass
    srv.shutdown()
    root.destroy()
print("TODO OK ui_layout_auto")
