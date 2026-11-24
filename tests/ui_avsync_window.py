"""La ventanita «Sincronía audio ↔ vídeo» (pack 58), en la aplicación de verdad (CamApp con su
ventana Tk; hace falta un display: xvfb-run -a python3 tests/ui_avsync_window.py).

1) en la pestaña «Audio» ya no están el «Ajuste A/V» ni el «Búfer audio»: hay un botón que abre
   una ventanita con los dos (y su explicación) y, debajo, cómo están ahora;
2) en la ventanita, los números son los de siempre: lo que se pone ahí se aplica (y se guarda), y lo
   que cambia por otro lado (p. ej. «Sincronizar») se ve ahí al momento; abrirla otra vez no abre
   otra, trae la misma delante;
3) desde la ventanita, «Sincronizar…» abre la ventana de la claqueta; y desde ésta, «Sonido ↔
   vídeo…» trae la ventanita (o la vuelve a abrir si se cerró); las dos, una al lado de la otra
   (la ventanita no tapa el QR);
4) el mapa de «Sincronizar» tras medir: ○ lo que tarda cada una de por sí, ● ahora (con su
   «Retardo»), todas en la raya de «a la vez»; el punto azul (el sonido) se arrastra con el ratón y
   eso cambia el «Ajuste A/V» (al soltar, de 5 en 5 ms)."""
import os
import sys
import json
import time
import shutil
import socket

import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_avsync_window_home")
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME)
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
    json.dump({"version": 3, "fps": 30, "av_offset": 120, "abuf": 50, "av_ref": "clock",
               "phonecam": {"port": free_port()}}, f)
camui.list_devices = lambda: ([], [])

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
app._tray_asked = True


def pump(cond, secs, step=0.03):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond():
            return True
        time.sleep(step)
    return False


def walk(w):
    yield w
    for c in w.winfo_children():
        for x in walk(c):
            yield x


def spin_vars(top):
    """Nombres de las variables de los Spinbox que cuelgan de top (sin contar otras Toplevel)."""
    out = []

    def go(w):
        if isinstance(w, ttk.Spinbox):
            out.append(str(w.cget("textvariable")))
        for c in w.winfo_children():
            if not isinstance(c, tk.Toplevel):
                go(c)
    go(top)
    return out


try:
    pump(lambda: not app.busy, 10)
    root.update()
    # 1) la pestaña «Audio»: botón en vez de los números
    av, buf = str(app.v_avoff), str(app.v_abuf)
    main_spins = spin_vars(root)
    assert av not in main_spins and buf not in main_spins, main_spins
    assert app.btn_avsync.winfo_exists()
    assert "Sincronía audio" in app.btn_avsync.cget("text")
    assert app.v_avsum.get() == "Ahora: ajuste A/V +120 ms · búfer 50 ms", app.v_avsum.get()
    assert app._avsync_dlg is None
    print("1) en «Audio», en lugar del «Ajuste A/V» y el «Búfer audio», un botón que abre su "
          "ventanita, y debajo cómo están («{0}») OK".format(app.v_avsum.get()))

    # 2) la ventanita
    app.btn_avsync.invoke()
    root.update()
    d = app._avsync_dlg
    assert d is not None and d.alive and d.top.winfo_exists()
    assert spin_vars(d.top) == [av, buf], (spin_vars(d.top), av, buf)
    texts = " ".join(str(w.cget("text")) for w in walk(d.top) if isinstance(w, ttk.Label))
    assert "el sonido se oye más tarde" in texts and "Menos búfer" in texts, texts
    d.sp_av.delete(0, "end")
    d.sp_av.insert(0, "250")
    root.update()
    assert app.v_avoff.get() == 250
    assert app.v_avsum.get() == "Ahora: ajuste A/V +250 ms · búfer 50 ms", app.v_avsum.get()
    app._sync_set({}, -40)                         # como hace «Sincronizar» al terminar
    root.update()
    assert d.sp_av.get() == "-40", d.sp_av.get()
    with open(camui.CONFIG, encoding="utf-8") as f:
        assert json.load(f)["av_offset"] == -40
    app.btn_avsync.invoke()
    root.update()
    assert app._avsync_dlg is d and d.alive
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and
            "Sincronía audio" in w.title()]
    assert len(tops) == 1, tops
    print("2) la ventanita tiene los dos números y su explicación; lo que se pone ahí se aplica "
          "(y el resumen de «Audio» cambia), lo que pone «Sincronizar» se ve ahí al momento y se "
          "guarda; abrirla otra vez trae la misma OK")

    # 3) ida y vuelta con «Sincronizar»
    d.btn_sync.invoke()
    assert pump(lambda: app._sync_dlg is not None and app._sync_dlg.alive, 20)
    sd = app._sync_dlg

    def apart(a, b):
        root.update_idletasks()
        ax0, ax1 = a.winfo_rootx(), a.winfo_rootx() + a.winfo_width()
        bx0, bx1 = b.winfo_rootx(), b.winfo_rootx() + b.winfo_width()
        return ax0 >= bx1 or ax1 <= bx0
    root.update()
    assert apart(d.top, sd.top), "la ventanita tapa la de «Sincronizar»"
    assert sd.btn_audio is not None and "Sonido" in sd.btn_audio.cget("text")
    sd.btn_audio.invoke()
    root.update()
    assert app._avsync_dlg is d and d.alive
    d.close()
    assert app._avsync_dlg is None
    sd.btn_audio.invoke()
    root.update()
    d2 = app._avsync_dlg
    assert d2 is not None and d2.alive and d2 is not d
    root.update_idletasks()
    assert apart(d2.top, sd.top), "la ventanita tapa la de «Sincronizar»"
    d2.btn_sync.invoke()                           # ya abierta: solo la trae delante
    root.update()
    assert app._sync_dlg is sd and sd.alive
    print("3) desde la ventanita, «Sincronizar…» abre la de la claqueta y quedan una al lado de "
          "la otra; desde ésta, «Sonido ↔ vídeo…» trae la ventanita, o la vuelve a abrir (al lado, "
          "sin taparle el QR) si se había cerrado OK")
    sd.close()
    d2.close()

    # 4) el mapa tras medir, y el punto azul arrastrado
    class _O(object):
        pass
    fake = _O()
    fake._sync = None
    fake._sync_map = {"phase": "medido", "sound": 0, "rows": [
        {"n": "A", "st": "ok", "ms": -17}, {"n": "B", "st": "ok", "ms": 56},
        {"n": "C", "st": "no", "ms": None}]}
    fake._sync_map_base = 0.0
    fake.cams = {"A": _O(), "B": _O()}
    fake.cams["A"].delay = tk.IntVar(value=73)
    fake.cams["B"].delay = tk.IntVar(value=0)
    fake.v_avoff = tk.IntVar(value=56)
    devs = camui.CamApp._sync_devs(fake)
    assert [r.get("now") for r in devs["rows"]] == [56, 56, None], devs
    assert devs["sound_now"] == 56, devs
    got = []
    holder = tk.Toplevel(root)                     # (root está oculta: sus «transient» también)
    sd2 = camui.SyncDialog(holder, None, "https://x/sync", lambda: 0, lambda: 0, lambda: 0,
                           on_av=got.append)
    sd2.set_devices(devs)
    root.update()
    c = sd2.cv
    assert c.itemcget("sndtxt", "text") == "a la vez"
    x0, y0, x1, y1 = c.coords("snd")
    cx, cy = int((x0 + x1) / 2), int((y0 + y1) / 2)
    lo, hi, X0, X1 = sd2._xmap
    dx = int(round(40 * (X1 - X0) / (hi - lo)))
    c.event_generate("<Motion>", x=cx, y=cy)
    c.event_generate("<ButtonPress-1>", x=cx, y=cy, button=1)
    c.event_generate("<B1-Motion>", x=cx + dx, y=cy, state=0x100)
    root.update()
    assert c.itemcget("sndtxt", "text") == "+40 ms (más tarde)", c.itemcget("sndtxt", "text")
    sd2.set_devices(dict(devs, sound_now=10))      # (mientras se arrastra no se redibuja)
    assert c.find_withtag("snd") and c.itemcget("sndtxt", "text") == "+40 ms (más tarde)"
    c.event_generate("<ButtonRelease-1>", x=cx + dx, y=cy, button=1, state=0x100)
    root.update()
    assert got == [40], got
    sd2.close()
    av0 = int(app.v_avoff.get())
    app._sync_drag_av(40)
    assert int(app.v_avoff.get()) == av0 + 40
    print("4) el mapa tras medir: ○ de por sí y ● ahora (las dos en la raya de «a la vez»); el "
          "punto azul arrastrado 40 ms a la derecha dice «+40 ms (más tarde)» y al soltar sube el "
          "«Ajuste A/V» 40 ms OK")
finally:
    try:
        app._phonecam_disable()
    except Exception:
        pass
    root.destroy()
    shutil.rmtree(HOME, ignore_errors=True)
print("TODO OK ui_avsync_window")
