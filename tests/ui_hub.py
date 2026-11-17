"""Motor experimental «concentrador» en la aplicación de verdad (CamApp con su ventana Tk; hace falta
un display: xvfb-run -a python3 tests/ui_hub.py). Una cámara de red que es "otro cam-ui" (un
IpServer real alimentado aquí) entra por el concentrador: vista previa en marcha, la etiqueta
dice que se alinea por la hora de captura, reiniciar no la reabre, pausar la suelta y desmarcar la
opción vuelve al motor de siempre."""
import os
import re
import sys
import json
import time
import socket
import threading

import numpy as np
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_hub_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")
with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "fps": 30}, f)           # sin «hub»: el motor nuevo, por defecto

# "otro cam-ui": su salida «En la red» (IpServer real), alimentada a 30 fps
W, H = 320, 240
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
        try:
            so.sendall(np.full((H, W, 3), (k * 7) % 255, np.uint8).tobytes())
        except OSError:
            return


threading.Thread(target=feeder, daemon=True).start()
URL = "http://127.0.0.1:{0}/stream.mjpg".format(PORT)

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


try:
    assert app.v_hub.get() is True, "el motor nuevo no sale por defecto"
    # un "hub": false de antes del pack 45 (era el valor de serie) se ignora una vez; elegido
    # después (con "hub_v"), se respeta
    assert camui.hub_from_cfg({}) is True
    assert camui.hub_from_cfg({"hub": False}) is True
    assert camui.hub_from_cfg({"hub": False, "hub_v": 2}) is False
    assert camui.hub_from_cfg({"hub": True, "hub_v": 2}) is True

    def find_check(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Checkbutton) and str(c.cget("text")).startswith("Motor nuevo"):
                return c
            r = find_check(c)
            if r is not None:
                return r
        return None
    chk = find_check(root)
    assert chk is not None and str(chk.cget("variable")) == str(app.v_hub), "falta la casilla en Ajustes"
    print("1) motor nuevo por defecto (también donde quedó desmarcado de serie), casilla «{0}» "
          "en Ajustes → Avanzado OK".format(chk.cget("text")))

    LAB = "🌐 Otro PC"
    app.net_cams[LAB] = {"name": "Otro PC", "alt": None, "label": LAB, "spec": URL, "net": True}
    app._refresh_devices()
    assert pump(lambda: LAB in app.vdev and not app.busy, 8)
    app.cams[LAB].use = True
    if LAB not in app.order:
        app.order.append(LAB)
    app.sel = LAB
    app._on_change()
    assert pump(lambda: app.previewing and app.eng.frame_count > 10, 20), \
        (app.previewing, app.eng.frame_count, app.eng.last_errors())
    assert app.eng.hub_on and ("net", URL) in app.hub.sources
    src = app.hub.sources[("net", URL)]
    assert pump(lambda: src.reader == "camui" and src.state == "live", 5), (src.reader, src.state)
    assert pump(lambda: "alineada con su PC por la hora de captura" in (app._mode_now_txt or ""), 8), \
        app._mode_now_txt
    print("2) la cámara de otro cam-ui entra por el concentrador: «{0}» OK".format(app._mode_now_txt))
    assert pump(lambda: app.awake.on, 2), "con la vista previa en marcha, el PC no debe suspenderse"

    # 2b) (pack 40) rótulo sobre su casilla, solo en la vista previa: nombre, cómo llega, tamaño
    #     y fps de verdad -- un elemento del lienzo de la ventana, no del vídeo
    def overlay_texts():
        return [app.view.itemcget(i, "text") for i in app.view.find_withtag("ov")
                if app.view.type(i) == "text"]
    app.root.geometry("1100x760")
    assert pump(lambda: any("otro cam-ui" in t and "320×240" in t and " fps" in t and "~" not in t
                            for t in overlay_texts()), 10), (overlay_texts(), app._tile_infos())
    fps2b = re.search(r"(\d+) fps", [t for t in overlay_texts() if "otro cam-ui" in t][0])
    assert fps2b and 25 <= int(fps2b.group(1)) <= 32, overlay_texts()
    print("2b) rótulo sobre la casilla, solo en la vista previa: «{0}» OK".format(
        " / ".join(overlay_texts()).replace("\n", " | ")))

    # 3) cambiar un ajuste reinicia el mosaico sin reabrir la cámara
    n0 = app.eng.frame_count
    app.v_fps.set("25")
    assert pump(lambda: app.eng.cmd and "-framerate" in app.eng.cmd and
                app.eng.cmd[app.eng.cmd.index("-framerate") + 1] == "25" and app.eng.frame_count > 5, 15)
    assert app.hub.sources.get(("net", URL)) is src and not src.done.is_set(), "se reabrió la cámara"
    print("3) cambiar los fps reinicia el mosaico con la misma cámara abierta OK")

    # 4) pausar suelta la cámara
    app.toggle_preview()
    assert pump(lambda: not app.busy and not app.previewing, 10)
    assert pump(lambda: not app.hub.sources and src.done.is_set(), 8), app.hub.sources
    assert pump(lambda: not app.awake.on, 2), "en pausa, el PC ya puede suspenderse"
    print("4) pausar la vista previa suelta también las cámaras del concentrador OK")

    # 5) sin la opción: el motor de siempre
    app.v_hub.set(False)
    app.toggle_preview()
    assert pump(lambda: app.previewing and app.eng.frame_count > 10, 25), app.eng.last_errors()
    assert not app.eng.hub_on and not app.hub.sources and URL in app.eng.cmd, \
        (app.eng.hub_on, app.hub.sources, app.eng.cmd)       # ffmpeg vuelve a abrir la cámara él
    app.save_config()
    with open(camui.CONFIG, encoding="utf-8") as f:
        cfg5 = json.load(f)
    assert cfg5.get("hub") is False and camui.hub_from_cfg(cfg5) is False, cfg5
    assert app.awake.on
    print("5) desmarcada: vuelve el motor de siempre, y se guarda OK")
    print("   (y el PC no se suspende con la vista previa en marcha; en pausa, sí puede) OK")
    # 5b) con el motor de siempre, los fps («~») se cuentan en lo que llega de ffmpeg, en CADA
    #     fotograma: aunque la ventana se redibuje bastante menos (aquí ~8 veces por segundo; en
    #     Windows nunca llega a 30), una cámara de 30 fps en un mosaico de 25 no sale a «~8»
    def _approx_fps():
        for t in overlay_texts():
            m5 = re.search(r"red .*~(\d+) fps", t.replace("\n", " "))
            if m5:
                return int(m5.group(1))
        return -1
    seen5 = []
    assert pump(lambda: seen5.append(_approx_fps()) or _approx_fps() >= 18, 12, step=0.12), \
        (seen5[-6:], overlay_texts(), app.eng.cmd)
    print("5b) con el motor de siempre, el rótulo da los fps vistos en la vista previa: «{0}» OK".format(
        " / ".join(overlay_texts()).replace("\n", " | ")))
finally:
    stop.set()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    other.close()
    root.destroy()
print("TODO OK ui_hub")
