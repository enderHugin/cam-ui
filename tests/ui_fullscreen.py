"""Pantalla completa (pack 47), en la aplicación de verdad (CamApp con su ventana Tk; hace falta un
display: xvfb-run -a python3 tests/ui_fullscreen.py; si hay «openbox», se arranca para que la
ventana de verdad ocupe la pantalla). Botón «⛶ Pantalla completa» sobre la imagen (aparece al
mover el ratón y se esconde solo), entrada en el menú Modo, solo el mosaico a pantalla completa y
con la vista previa pedida al tamaño de la pantalla (no a 880), salir con Esc / doble clic /
botón, y vuelta a la vista previa pequeña. También el rótulo «cam-ui@nombre» de otro cam-ui."""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import os
import sys
import json
import time
import shutil
import socket
import threading
import subprocess

import numpy as np
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_fullscreen_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")
with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "fps": 30}, f)

wm = None
if shutil.which("openbox"):
    wm = subprocess.Popen(["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

W, H = 640, 360
other = camui.IpServer()
s_ = socket.socket()
s_.bind(("127.0.0.1", 0))
PORT = s_.getsockname()[1]
s_.close()
other.open(W, H, fps=30, port=PORT)
stop = threading.Event()


def feeder():
    so = socket.create_connection(("127.0.0.1", other.arm(W, H, 30)), timeout=5)
    k, t0 = 0, time.time()
    while not stop.is_set():
        k += 1
        time.sleep(max(0, t0 + k / 30.0 - time.time()))
        fr = np.full((H, W, 3), 60, np.uint8)
        fr[100:260, :, 1] = (k * 9) % 255
        try:
            so.sendall(fr.tobytes())
        except OSError:
            return


threading.Thread(target=feeder, daemon=True).start()
URL = "http://127.0.0.1:{0}/stream.mjpg".format(PORT)

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
app._tray_asked = True
root.deiconify()                      # la ventana de verdad, a la vista (pantalla completa)
root.update()


def pump(cond, secs, step=0.05):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond():
            return True
        time.sleep(step)
    return False


def overlay_texts():
    return [app.view.itemcget(i, "text") for i in app.view.find_withtag("ov")
            if app.view.type(i) == "text"]


def motion():
    app.view.event_generate("<Motion>", x=200, y=150, warp=False)
    root.update()


def mapped(w):
    root.update()
    return bool(w.winfo_ismapped())


SW, SH = root.winfo_screenwidth(), root.winfo_screenheight()
try:
    LAB = "🌐 Otro PC"
    app.net_cams[LAB] = {"name": "Otro PC", "alt": None, "label": LAB, "spec": URL, "net": True}
    app._refresh_devices()
    assert pump(lambda: LAB in app.vdev and not app.busy, 8)
    app.cams[LAB].use = True
    if LAB not in app.order:
        app.order.append(LAB)
    app.sel = LAB
    app._on_change()
    assert pump(lambda: app.previewing and app.eng.frame_count > 10 and not app.busy, 25), \
        app.eng.last_errors()
    assert app.mode == "normal"
    pw0 = app.eng.meta["pw"]
    assert pw0 <= 880, app.eng.meta
    assert pump(lambda: any(t.startswith("cam-ui@Otro PC") for t in overlay_texts()), 10), \
        overlay_texts()
    print("0) rótulo de otro cam-ui: «{0}» OK".format(
        [t for t in overlay_texts() if t.startswith("cam-ui@")][0].replace("\n", " | ")))

    # 1) el botón sobre la imagen aparece al mover el ratón y se va solo; y está en el menú Modo
    assert pump(lambda: not app.btn_viewfs.winfo_ismapped(), 4)    # (el puntero de Xvfb puede estar encima)
    motion()
    assert mapped(app.btn_viewfs) and "Pantalla completa" in str(app.btn_viewfs.cget("text"))
    assert pump(lambda: not app.btn_viewfs.winfo_ismapped(), 4), "no se esconde solo"
    labels = [app.mode_menu.entrycget(i, "label") for i in range(app.mode_menu.index("end") + 1)
              if app.mode_menu.type(i) == "radiobutton"]
    assert "Pantalla completa" in labels, labels
    print("1) «⛶ Pantalla completa» sobre la imagen al mover el ratón (y se esconde solo); y en el "
          "menú Modo OK")

    # 2) entrar: solo el mosaico, a pantalla completa, y la vista previa al tamaño de la pantalla
    motion()
    app.btn_viewfs.invoke()
    assert app.mode == "fullscreen"
    if wm is not None:            # en X11 lo aplica el gestor de ventanas, un instante después
        assert pump(lambda: int(root.attributes("-fullscreen")) == 1, 5)
    for w in (app.bar, app.sbar, app.left, app.mrow):
        assert not mapped(w), w
    want = camui.prev_size(app.eng.meta["W"], app.eng.meta["H"], *map(float, app._pv_max()))
    assert want[0] > 880, (want, app._pv_max())
    assert pump(lambda: app.previewing and app.eng.meta.get("pw") == want[0] and
                app.eng.frame_count > 5, 20), (app.eng.meta.get("pw"), want)
    if wm is not None:
        assert pump(lambda: app.view.winfo_width() >= SW - 2 and app.view.winfo_height() >= SH - 2,
                    5), (app.view.winfo_width(), app.view.winfo_height(), SW, SH)
        assert pump(lambda: app._tkimg is not None and
                    (app._tkimg.width() >= SW - 2 or app._tkimg.height() >= SH - 2), 5), \
            (app._tkimg.width(), app._tkimg.height(), SW, SH)
    assert pump(lambda: any(t.startswith("cam-ui@Otro PC") for t in overlay_texts()), 5)
    print("2) pantalla completa ({0}×{1}): solo el mosaico; vista previa pedida a {2}×{3} (en la "
          "ventana, {4}){5} OK".format(SW, SH, want[0], want[1], pw0,
                                      "; imagen de {0}×{1}".format(app._tkimg.width(), app._tkimg.height())
                                      if wm is not None else " (sin gestor de ventanas: tamaño no comprobado)"))

    # 3) el botón ahora es «Salir…», y al esconderse se esconde también el puntero
    motion()
    assert mapped(app.btn_viewfs) and "Salir de pantalla completa" in str(app.btn_viewfs.cget("text"))
    assert pump(lambda: not app.btn_viewfs.winfo_ismapped(), 4)
    assert str(app.view.cget("cursor")) == "none", app.view.cget("cursor")
    motion()
    assert str(app.view.cget("cursor")) == ""
    print("3) en pantalla completa: «✕ Salir de pantalla completa (Esc)», y sin moverse el ratón "
          "se esconden botón y puntero OK")

    # 4) no se guarda como modo de arranque
    app.save_config()
    with open(camui.CONFIG, encoding="utf-8") as f:
        assert json.load(f)["ui"]["mode"] == "normal"
    assert camui.read_ui_cfg({"v": 2, "mode": "fullscreen"})[0] == "normal"

    # 5) Esc: vuelve a la ventana, con todo, y la vista previa vuelve a la pequeña
    app._on_escape()
    assert app.mode == "normal"
    assert pump(lambda: int(root.attributes("-fullscreen")) == 0, 5)
    for w in (app.bar, app.sbar, app.left, app.mrow, app.lbl_info, app.lbl_hint):
        assert mapped(w), w
    assert pump(lambda: app.previewing and app.eng.meta.get("pw") == pw0 and
                app.eng.frame_count > 5, 20), (app.eng.meta.get("pw"), pw0)
    print("4) Esc: vuelve la ventana normal con todo y la vista previa a {0} de ancho; nunca "
          "arranca en pantalla completa OK".format(pw0))

    # 6) doble clic en pantalla completa también sale (no abre el encuadre)
    app.set_mode("fullscreen")
    assert app.mode == "fullscreen"
    ev = type("Ev", (), {"x": 200, "y": 150, "widget": app.view})()
    app._view_double(ev)   # (Tk no deja simularlo)
    root.update()
    assert app.mode == "normal" and not app.editing
    print("5) doble clic en pantalla completa: sale (sin abrir el encuadre) OK")
finally:
    stop.set()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    other.close()
    root.destroy()
    if wm is not None:
        wm.terminate()
print("TODO OK ui_fullscreen")
