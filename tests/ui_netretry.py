"""Motor de siempre con cámaras de red «otro cam-ui», en la aplicación de verdad (CamApp con su
ventana Tk; hace falta un display: xvfb-run -a python3 tests/ui_netretry.py). El caso real del
usuario (camui.log, pack 43): sincronía «Marcas del dispositivo», otro PC a ~12 fps, y ese otro PC
reiniciándose justo cuando arrancaba este.

1) con «Marcas del dispositivo», una cámara de red a 12 fps ya no frena el mosaico entero a la
   mitad (los 25 fps supuestos de «mpjpeg» marcaban su ritmo);
2) la que no respondía al arrancar queda «sin señal»... y entra sola en cuanto responde;
3) la que se corta con todo en marcha pasa a «sin señal» en su rótulo (no «imagen fija» ni los
   fps de su vecina) y, cuando vuelve, entra sola otra vez."""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import os
import sys
import json
import time
import socket
import threading

import numpy as np
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_netretry_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")
camui.NET_RETRY_SECONDS = 2.0
with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "hub": False, "hub_v": 2, "fps": 25, "sync": "device"}, f)

W, H = 320, 240


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class OtherCamUI:
    """La salida «En la red» de otro cam-ui (IpServer real), alimentada a fps."""

    def __init__(self, port, fps, level):
        self.port, self.fps, self.level = port, fps, level
        self.srv = None
        self.stop = threading.Event()

    def up(self):
        self.stop = threading.Event()
        self.srv = camui.IpServer()
        self.srv.open(W, H, fps=self.fps, port=self.port)
        so = socket.create_connection(("127.0.0.1", self.srv.arm(W, H, self.fps)), timeout=5)
        stop = self.stop

        def feed():
            k, t0 = 0, time.time()
            while not stop.is_set():
                k += 1
                time.sleep(max(0, t0 + k / float(self.fps) - time.time()))
                fr = np.full((H, W, 3), self.level, np.uint8)
                fr[:, :, 0] = (k * 37) % 256            # cambia en cada fotograma
                try:
                    so.sendall(fr.tobytes())
                except OSError:
                    return
            so.close()
        threading.Thread(target=feed, daemon=True).start()

    def down(self):
        self.stop.set()
        if self.srv is not None:
            self.srv.close()
            self.srv = None


A = OtherCamUI(free_port(), 12, 90)        # el otro PC: ~12 fps, como el del usuario
B = OtherCamUI(free_port(), 12, 200)       # otro más, que al arrancar no responde
A.up()
URL_A = "http://127.0.0.1:{0}/stream.mjpg".format(A.port)
URL_B = "http://127.0.0.1:{0}/stream.mjpg".format(B.port)

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
app._tray_asked = True
app.root.geometry("1100x760")


def pump(cond, secs, step=0.05):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond():
            return True
        time.sleep(step)
    return False


def info(lab):
    for name, parts, warn in app._tile_infos():
        if name == lab:
            return parts, warn
    return None, None


def out_fps(secs=3.0, tries=4):
    """Fotogramas por segundo que llegan a la vista previa, con el mismo ffmpeg de principio a
    fin (si entre medias se reinicia el mosaico -- p. ej. al conocer el tamaño real de una
    cámara --, se vuelve a medir)."""
    best = 0.0
    for _ in range(tries):
        pump(lambda: app.previewing and not app.busy, 10)
        p0, n0, t0 = app.eng.proc, app.eng.frame_count, time.time()
        pump(lambda: False, secs)
        if app.eng.proc is p0 and app.eng.frame_count >= n0:
            best = max(best, (app.eng.frame_count - n0) / (time.time() - t0))
            if best >= 16:
                break
    return best


LA, LB = "🌐 PC A", "🌐 PC B"
try:
    assert app.v_hub.get() is False
    assert camui._pair_value(camui.SYNCS, app.v_sync.get()) == "device", app.v_sync.get()
    for lab, url in ((LA, URL_A), (LB, URL_B)):
        app.net_cams[lab] = {"name": lab[2:], "alt": None, "label": lab, "spec": url, "net": True}
    app._refresh_devices()
    assert pump(lambda: LA in app.vdev and LB in app.vdev and not app.busy, 8)
    for lab in (LA, LB):
        app.cams[lab].use = True
        if lab not in app.order:
            app.order.append(lab)
    app.sel = LA
    app._on_change()
    assert pump(lambda: app.previewing and app.eng.frame_count > 10, 25), app.eng.last_errors()
    # que se asiente: al conocer el tamaño real de A el mosaico se recoloca (y se reinicia) una
    # vez; a partir de ahí, si B entra, solo puede ser por el reintento en caliente
    st = {"p": None, "t": 0.0}

    def settled(secs=5.0):
        p = app.eng.proc if app.previewing and not app.busy else None
        if p is None or p is not st["p"]:
            st["p"], st["t"] = p, time.time()
            return False
        return time.time() - st["t"] >= secs
    assert pump(settled, 40), "el mosaico no se asienta"

    # 2a) B no respondía al arrancar: «sin señal»
    assert pump(lambda: info(LB)[1] is True and "sin señal" in info(LB)[0], 5), app._tile_infos()
    assert URL_B not in app.eng.cmd
    print("2a) la que no responde al arrancar: «{0}» OK".format(" · ".join(info(LB)[0])))

    # 1) «Marcas del dispositivo» + cámara de red a 12 fps: el mosaico va a su ritmo (25 fps de
    #    mosaico, vista previa a 20), no a la mitad
    pv = out_fps()
    assert pv >= 16, ("el mosaico va frenado", pv, app.eng.cmd)
    assert pump(lambda: info(LA)[0] and any(p.startswith("~1") for p in info(LA)[0]), 6), info(LA)
    print("1) «Marcas del dispositivo» con otro cam-ui a 12 fps: vista previa a {0:.1f} fps, «{1}» OK".format(
        pv, " · ".join(info(LA)[0])))

    # 2b) B empieza a responder: entra sola
    B.up()
    assert pump(lambda: URL_B in app.eng.cmd and app.previewing and not info(LB)[1] and
                any("fps" in p for p in (info(LB)[0] or [])), 30), (app._tile_infos(), app.eng.cmd)
    print("2b) en cuanto responde, entra sola en el mosaico: «{0}» OK".format(" · ".join(info(LB)[0])))

    # 3a) A se corta con todo en marcha: «sin señal» en su rótulo (no la imagen congelada)
    A.down()
    assert pump(lambda: info(LA)[1] is True and "sin señal" in info(LA)[0], 20), app._tile_infos()
    assert "se ha cortado" in app.status.cget("text"), app.status.cget("text")
    assert not info(LB)[1], app._tile_infos()
    print("3a) la que se corta en marcha: «{0}» y aviso «{1}» OK".format(
        " · ".join(info(LA)[0]), app.status.cget("text")))

    # 3b) A vuelve: entra sola otra vez
    A.up()
    assert pump(lambda: app.previewing and not info(LA)[1] and
                any("fps" in p for p in (info(LA)[0] or [])), 40), (app._tile_infos(), app.eng.cmd)
    print("3b) cuando vuelve, entra sola otra vez: «{0}» OK".format(" · ".join(info(LA)[0])))
finally:
    A.down()
    B.down()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    root.destroy()
print("TODO OK ui_netretry")
