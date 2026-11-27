"""La vista previa (pack 60), en la aplicación de verdad (CamApp con su ventana Tk; hace falta un
display: xvfb-run -a python3 tests/ui_preview_menu.py). Tres cámaras («📱 Rojo», «📱 Verde»,
«📱 Azul»: móviles de prueba, cada uno de un color).

1) arrastrar una casilla de la vista previa sobre otra las cambia de sitio (como en el mapa del
   panel), y la vista previa sale con el orden nuevo; mientras se arrastra, el rótulo dice cuál
   con cuál; un clic que apenas se mueve solo elige; en pantalla completa no se arrastra nada;
2) clic derecho sobre una casilla: el menú de ESA cámara (también en pantalla completa), con
   color, driver, encuadre (proporción con «Personalizada…», zoom, centrar, quitar barras,
   encuadrar), voltear, girar, captura, destacada, quitar del mosaico y restablecer; y lo que se
   elige cambia esa cámara;
3) «Color…» abre una ventanita con los deslizadores de esa cámara (aunque esté elegida otra), que
   en pantalla completa va encima de todo; abrirla para otra cámara cambia a la otra;
4) grabando, solo se puede el color (lo demás, apagado: cortaría la grabación);
5) «Quitar del mosaico» la desmarca;
6) «Propiedades del driver» sin parar la imagen: si la cámara deja enseñar su cuadro mientras se
   usa (el ffmpeg del cuadro sigue abierto), la vista previa no se toca y se dice que los cambios
   se ven al momento; si no deja (sale enseguida: «BindToObject», o sin decir nada), se hace como
   antes (pausar, con su aviso). (Aquí, con un ffmpeg de mentira que hace de cada caso.)
7) los deslizadores (zoom, color, ganancia, y los de la ventanita de color): la rueda del ratón
   encima los mueve (con Mayúsculas, cinco veces más), las flechas y RePág/AvPág también, y en su
   casilla se puede escribir el número (con %, coma o signo; fuera de rango, al tope; si no es
   un número, se queda como estaba); grabando, el zoom no se mueve y el color sí."""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import io
import os
import sys
import json
import time
import shutil
import socket
import threading

import numpy as np
import tkinter as tk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_preview_menu_home")
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
    json.dump({"version": 3, "fps": 30, "phonecam": {"port": free_port()}}, f)
camui.list_devices = lambda: ([], [])
COLORS = {"Rojo": (220, 30, 30), "Verde": (30, 200, 30), "Azul": (30, 60, 220)}
stop = threading.Event()


def jpeg(rgb):
    a = np.zeros((180, 320, 3), np.uint8)
    a[:] = rgb
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=85)
    return b.getvalue()


def camera(app, name):
    sid = app.phonecam.new_session(name)
    data = jpeg(COLORS[name])
    k = 0
    while not stop.is_set():
        k += 1
        app.phonecam.feed(sid, data, seq=k)
        time.sleep(1.0 / 15)


root = tk.Tk()
root.geometry("1280x800+0+0")
app = camui.CamApp(root)
app._tray_asked = True
LABS = ["📱 " + n for n in COLORS]


def pump(cond, secs, step=0.03):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond():
            return True
        time.sleep(step)
    return False


def tile_center(lab):
    x0, y0, x1, y1 = app._tile_rect(lab)
    return int((x0 + x1) / 2), int((y0 + y1) / 2)


def color_at(x, y):
    """el color de la vista previa en (x, y) de la vista (de la imagen que se pinta)"""
    assert pump(lambda: app.eng.get_latest()[0] is not None and app.previewing and
                not app.busy, 60)
    ix, iy, nw, nh = app._img_rect
    a = np.asarray(app.eng.get_latest()[0])             # (BGR)
    h, w = a.shape[:2]
    px = int((x - ix) / float(nw) * w)
    py = int((y - iy) / float(nh) * h)
    b, g, r = [int(v) for v in a[min(h - 1, max(0, py)), min(w - 1, max(0, px))][:3]]
    return r, g, b


def which(rgb):
    r, g, b = rgb
    return "Rojo" if r > max(g, b) else ("Verde" if g > max(r, b) else "Azul")


def menu_labels(m):
    out = []
    end = m.index("end")
    for i in range(0, (end or 0) + 1):
        if m.type(i) == "separator":
            continue
        out.append(m.entrycget(i, "label"))
    return out


def menu_find(m, label):
    end = m.index("end")
    for i in range(0, (end or 0) + 1):
        if m.type(i) != "separator" and m.entrycget(i, "label").startswith(label):
            return i
    raise AssertionError((label, menu_labels(m)))


def submenu(m, label):
    return root.nametowidget(m.entrycget(menu_find(m, label), "menu"))


try:
    assert pump(lambda: app.phonecam.active and not app.busy, 15)
    for n in COLORS:
        threading.Thread(target=camera, args=(app, n), daemon=True).start()
        time.sleep(0.3)
    assert pump(lambda: all(l in app.order for l in LABS) and app.previewing and not app.busy and
                app.eng.frame_count > 10 and app._img_rect and
                list(app._run_labels) == list(app.order), 90), (app.order, app._run_labels)
    pump(lambda: False, 1.0)
    order0 = list(app.order)
    a, b = order0[0], order0[2]
    ca, cb = tile_center(a), tile_center(b)
    assert which(color_at(*ca)) == a[2:] and which(color_at(*cb)) == b[2:]

    # 1) arrastrar
    v = app.view
    v.event_generate("<ButtonPress-1>", x=ca[0], y=ca[1], button=1)
    v.event_generate("<B1-Motion>", x=ca[0] + 3, y=ca[1] + 2, state=0x100)
    root.update()
    assert not v.find_withtag("pvdrag")                       # (aún es un clic)
    v.event_generate("<B1-Motion>", x=(ca[0] + cb[0]) // 2, y=(ca[1] + cb[1]) // 2, state=0x100)
    v.event_generate("<B1-Motion>", x=cb[0], y=cb[1], state=0x100)
    root.update()
    texts = [v.itemcget(i, "text") for i in v.find_withtag("pvdrag") if v.type(i) == "text"]
    assert texts and "«{0}»  ↔  «{1}»".format(a, b) in texts[0], texts
    assert str(v.cget("cursor")) == "fleur"
    v.event_generate("<ButtonRelease-1>", x=cb[0], y=cb[1], button=1)
    root.update()
    assert not v.find_withtag("pvdrag") and str(v.cget("cursor")) == ""
    assert app.order[0] == b and app.order[2] == a, app.order
    assert pump(lambda: list(app._run_labels) == list(app.order) and app.previewing and
                not app.busy and app.eng.frame_count > 10, 60), (app._run_labels, app.order)
    pump(lambda: False, 1.0)
    assert which(color_at(*tile_center(b))) == b[2:] and which(color_at(*tile_center(a))) == a[2:]
    assert tile_center(b) == ca and tile_center(a) == cb, (tile_center(b), ca)
    # un clic que apenas se mueve: solo elige
    o1 = list(app.order)
    c1 = tile_center(o1[1])
    v.event_generate("<ButtonPress-1>", x=c1[0], y=c1[1], button=1)
    v.event_generate("<B1-Motion>", x=c1[0] + 4, y=c1[1], state=0x100)
    v.event_generate("<ButtonRelease-1>", x=c1[0] + 4, y=c1[1], button=1)
    root.update()
    assert app.order == o1 and app.sel == o1[1]
    print("1) arrastrar «{0}» sobre «{1}» en la vista previa: cambian de sitio (el rótulo lo dice "
          "mientras tanto) y la vista previa sale así; un clic que apenas se mueve solo elige "
          "OK".format(a, b))

    # 2) clic derecho: el menú de esa cámara
    got = []
    real_popup = app._popup
    app._popup = lambda m, e: got.append(m)
    lab = app.order[2]
    c2 = tile_center(lab)
    app._select(app.order[0])
    v.event_generate("<ButtonPress-3>", x=c2[0], y=c2[1], button=3)
    root.update()
    assert got and app.sel == lab, (got, app.sel, lab)
    m = got[-1]
    L = menu_labels(m)
    assert L[0] == "«{0}»".format(lab), L
    for want in ("🎨  Color…", "⚙  Propiedades del driver…", "Encuadre", "Voltear horizontal",
                 "Voltear vertical", "Girar", "Captura", "★ Destacada", "Quitar del mosaico",
                 "Restablecer esta cámara"):
        assert any(x.startswith(want) for x in L), (want, L)
    assert m.entrycget(menu_find(m, "⚙  Propiedades"), "state") == "disabled"   # (un móvil)
    cs = app.cams[lab]
    m.invoke(menu_find(m, "Voltear horizontal"))
    assert cs.h.get() is True
    gm = submenu(m, "Girar")
    gm.invoke(menu_find(gm, "90°"))
    assert cs.rot.get() == "90°"
    gm.invoke(menu_find(gm, "0°"))
    fr = submenu(m, "Encuadre")
    FL = menu_labels(fr)
    for want in ("Proporción", "Zoom", "Centrar y quitar zoom", "Quitar barras negras",
                 "✂  Encuadrar sobre la imagen"):
        assert any(x.startswith(want) for x in FL), (want, FL)
    pr = submenu(fr, "Proporción")
    pr.invoke(menu_find(pr, "1:1"))
    assert cs.tile.get() == "1:1" and cs.tile_ratio() == 1.0
    from tkinter import simpledialog
    simpledialog.askstring = lambda *a, **k: "2,2:1"
    pr.invoke(menu_find(pr, "Personalizada…"))
    assert abs(cs.tile_ratio() - 2.2) < 1e-6, cs.tile.get()
    zm = submenu(fr, "Zoom")
    zm.invoke(menu_find(zm, "200 %"))
    assert abs(cs.zoom.get() - 200.0) < 1e-6
    cs.cx.set(30.0)
    fr.invoke(menu_find(fr, "Centrar y quitar zoom"))
    assert cs.zoom.get() == 100.0 and cs.cx.get() == 50.0 and abs(cs.tile_ratio() - 2.2) < 1e-6
    m.invoke(menu_find(m, "★ Destacada"))
    assert cs.feat.get() is True
    m.invoke(menu_find(m, "Restablecer esta cámara"))
    assert not cs.h.get() and not cs.feat.get() and cs.tile_ratio() is None
    # en pantalla completa: también (y apunta a la casilla que hay debajo)
    app.set_mode("fullscreen")
    assert pump(lambda: app.previewing and not app.busy and app._img_rect and
                app.eng.frame_count > 5, 60)
    pump(lambda: False, 0.5)
    lab2 = app.order[1]
    c3 = tile_center(lab2)
    got.clear()
    v.event_generate("<ButtonPress-3>", x=c3[0], y=c3[1], button=3)
    root.update()
    assert got and menu_labels(got[-1])[0] == "«{0}»".format(lab2), got
    o2 = list(app.order)                                 # en pantalla completa no se arrastra
    c4 = tile_center(o2[0])
    v.event_generate("<ButtonPress-1>", x=c3[0], y=c3[1], button=1)
    v.event_generate("<B1-Motion>", x=c4[0], y=c4[1], state=0x100)
    v.event_generate("<ButtonRelease-1>", x=c4[0], y=c4[1], button=1)
    root.update()
    assert app.order == o2 and not v.find_withtag("pvdrag")
    print("2) clic derecho sobre «{0}»: su menú (color, driver -apagado: es un móvil-, encuadre "
          "con proporción -también personalizada-, zoom, centrar, quitar barras y encuadrar; "
          "voltear, girar, captura, destacada, quitar, restablecer), y lo elegido cambia esa "
          "cámara; en pantalla completa, también (y allí no se arrastra) OK".format(lab))

    # 3) «Color…»
    got[-1].invoke(menu_find(got[-1], "🎨  Color…"))
    root.update()
    d = app._color_dlg
    assert d is not None and d.alive and d.lab == lab2
    assert d.top.attributes("-topmost") in (1, True, "1")           # en pantalla completa
    app._select(app.order[0])                                       # aunque se elija otra
    d.sc["bri"].set(40)
    root.update()
    assert abs(app.cams[lab2].color["bri"].get() - 40) < 0.6
    assert abs(app.cams[app.order[0]].color["bri"].get()) < 0.6
    assert d.lbl["bri"].get() == "+40", d.lbl["bri"].get()
    app._cam_color_open(app.order[0])
    root.update()
    assert not d.alive and app._color_dlg.lab == app.order[0]
    app._color_dlg.close()
    assert app._color_dlg is None
    print("3) «Color…»: ventanita con los deslizadores de esa cámara (encima de todo en pantalla "
          "completa); mueve los de esa aunque esté elegida otra; para otra cámara, cambia OK")

    # 4) grabando: solo el color
    app.recording = True
    try:
        mr = app._cam_menu(lab2, app.cams[lab2])
        for want in ("Encuadre", "Voltear horizontal", "Voltear vertical", "Girar", "Captura",
                     "★ Destacada", "Quitar del mosaico", "Restablecer esta cámara"):
            assert mr.entrycget(menu_find(mr, want), "state") == "disabled", want
        assert mr.entrycget(menu_find(mr, "🎨  Color…"), "state") == "normal"
    finally:
        app.recording = False
    print("4) grabando, en el menú solo se puede el color OK")

    # quitar del mosaico
    app.set_mode("normal")
    pump(lambda: app.previewing and not app.busy, 60)
    labq = app.order[2]
    app._cam_menu(labq, app.cams[labq]).invoke(
        menu_find(app._cam_menu(labq, app.cams[labq]), "Quitar del mosaico"))
    assert labq not in app.order and app.cams[labq].use is False
    assert pump(lambda: list(app._run_labels) == list(app.order) and app.previewing and
                not app.busy, 60)
    print("5) «Quitar del mosaico»: se desmarca y la vista previa sale sin ella OK")
    app._popup = real_popup

    # 6) propiedades del driver sin parar la imagen
    for a_, e_, w_ in (("", "Could not run graph (sometimes caused by a device already in use)", True),
                       ("", "Unable to BindToObject for X", False), ("", "", False),
                       ("x", "Could not run graph", True), ("x", "", True)):
        el = 2.0 if a_ else 0.3
        assert camui.driver_dialog_was_shown(e_, el) is w_, (e_, el)
    fakes = {}
    for name, body in (("vivo", "import sys, time\ntime.sleep(2.6)\n"
                                "sys.stderr.write('Could not run graph (sometimes caused by a device "
                                "already in use by other application)\\n')\nsys.exit(1)\n"),
                       ("nodeja", "import sys\nsys.stderr.write('Unable to BindToObject for "
                                  "X\\n')\nsys.exit(1)\n"),
                       ("calla", "import sys\nsys.exit(1)\n")):
        fp = os.path.join(HOME, "ffmpeg_" + name + ".py")
        with open(fp, "w") as f_:
            f_.write(body)
        fakes[name] = fp
    asked = []
    camui.messagebox.askokcancel = lambda title, msg, **k: asked.append(msg) or False
    released = []
    real_rel = app.eng.release_devices
    app.eng.release_devices = lambda *a, **k: released.append(1) or real_rel(*a, **k)
    labd = app.order[0]
    app._select(labd)
    assert pump(lambda: app.previewing and not app.busy and not app.paused, 30)

    def run_drv(name):
        camui.driver_dialog_cmd = lambda ff, spec, fp=fakes[name]: [sys.executable, fp]
        del asked[:]
        fc0 = app.eng.frame_count
        app._driver_dialog()
        sts = []
        pump(lambda: (sts.append(app.status.cget("text")) and False) or
             getattr(app, "_drv_proc", None) is None, 15)
        pump(lambda: False, 0.3)
        return sts, fc0
    sts, fc0 = run_drv("vivo")
    assert not asked and not released, (asked, released)
    assert any("los cambios se ven al momento" in t for t in sts), sts[-5:]
    assert any("Propiedades del driver cerradas." in t_ for t_ in sts), sts[-6:]
    assert app.previewing and not app.paused and app.eng.frame_count > fc0 + 20, \
        (app.previewing, app.paused, app.eng.frame_count, fc0)
    for name in ("nodeja", "calla"):
        run_drv(name)
        assert len(asked) == 1 and asked[0].startswith("«{0}» no deja abrir".format(labd)), asked
        assert not released
    app.paused = True                                  # (con la imagen parada: como siempre)
    try:
        camui.driver_dialog_cmd = lambda ff, spec: [sys.executable, fakes["vivo"]]
        del asked[:]
        app._driver_dialog()
        assert len(asked) == 1 and asked[0].startswith("La vista previa se pausa"), asked
    finally:
        app.paused = False
    app.eng.release_devices = real_rel
    print("6) «Propiedades del driver»: si la cámara deja, el cuadro sale sin parar la imagen (la "
          "vista previa sigue, y se dice que los cambios se ven al momento); si no deja, como "
          "antes, avisando OK")

    # 7) deslizadores: rueda, teclado y casilla
    labz = app.order[0]
    app._select(labz)
    csz = app.cams[labz]
    csz.zoom.set(100.0)
    root.update()
    z = app.sc_zoom
    z.event_generate("<MouseWheel>", delta=120)
    assert abs(csz.zoom.get() - 105) < 1e-6, csz.zoom.get()
    z.event_generate("<MouseWheel>", delta=120, state=0x0001)
    assert abs(csz.zoom.get() - 130) < 1e-6, csz.zoom.get()
    z.event_generate("<Button-5>")
    assert abs(csz.zoom.get() - 125) < 1e-6, csz.zoom.get()
    app.lbl_zoom.event_generate("<MouseWheel>", delta=-120)          # (también sobre la casilla)
    assert abs(csz.zoom.get() - 120) < 1e-6, csz.zoom.get()
    app.nb.select(app.tab_frame)                        # (las teclas van a lo que tiene el foco)
    root.update()
    z.focus_force()
    root.update()
    assert root.focus_get() is z, root.focus_get()
    for k_, want in (("<Right>", 125), ("<Prior>", 175), ("<Left>", 170), ("<Next>", 120)):
        z.event_generate(k_)
        assert abs(csz.zoom.get() - want) < 1e-6, (k_, csz.zoom.get())
    root.update()
    assert app.lbl_zoom.get() == "120%", app.lbl_zoom.get()
    box = app.lbl_zoom
    for typed, want, shown in (("150 %", 150, "150%"), ("abc", 150, "150%"), ("999", 600, "600%"),
                               ("", 600, "600%")):
        box.focus_force()
        root.update()
        box.delete(0, "end")
        box.insert(0, typed)
        box.event_generate("<Return>")
        root.update()
        assert abs(csz.zoom.get() - want) < 1e-6 and box.get() == shown, (typed, csz.zoom.get(),
                                                                       box.get())
    cb_ = app.color_lbl["bri"]
    app.nb.select(app.nb.tabs()[2])                     # (la pestaña «Color»)
    root.update()
    cb_.focus_force()
    root.update()
    cb_.delete(0, "end")
    cb_.insert(0, "-7,5")
    cb_.event_generate("<Return>")
    assert abs(csz.color["bri"].get() + 7.5) < 1e-6, csz.color["bri"].get()
    app.color_sc["bri"].event_generate("<MouseWheel>", delta=120)     # (la casilla, con el foco)
    assert abs(csz.color["bri"].get() + 6.5) < 1e-6, csz.color["bri"].get()
    root.update()
    assert cb_.get() == "-6", cb_.get()                            # se ve el valor nuevo
    cb_.event_generate("<FocusOut>")                               # y salir no lo cambia
    assert abs(csz.color["bri"].get() + 6.5) < 1e-6, csz.color["bri"].get()
    # la ganancia de un canal de audio
    ar = app.arow["L"]
    gboxes = []

    def find_boxes(w):
        for c_ in w.winfo_children():
            if isinstance(c_, camui.SliderBox) and str(c_._scale.cget("variable")) == str(ar.gain):
                gboxes.append(c_)
            find_boxes(c_)
    find_boxes(root)
    assert len(gboxes) == 1, gboxes
    ar.gain.set(0)
    gboxes[0]._scale.event_generate("<MouseWheel>", delta=120)
    root.update()
    assert int(float(ar.gain.get())) == 1 and gboxes[0].get() == "1", (ar.gain.get(),
                                                                       gboxes[0].get())
    # grabando: el zoom no, el color sí
    app.recording = True
    try:
        app._lock(True)
        root.update()
        z0 = csz.zoom.get()
        z.event_generate("<MouseWheel>", delta=120)
        app.nb.select(app.tab_frame)
        root.update()
        box.focus_force()
        root.update()
        box.delete(0, "end")
        box.insert(0, "300")
        box.event_generate("<Return>")
        assert csz.zoom.get() == z0, csz.zoom.get()
        app.color_sc["bri"].event_generate("<MouseWheel>", delta=120)
        assert abs(csz.color["bri"].get() + 5.5) < 1e-6, csz.color["bri"].get()
        assert not cb_.instate(["disabled"])
    finally:
        app.recording = False
        app._lock(False)
    # la ventanita de color
    app._cam_color_open(labz)
    root.update()
    d = app._color_dlg
    d.sc["sat"].event_generate("<MouseWheel>", delta=120)
    d.lbl["con"].focus_force()
    root.update()
    d.lbl["con"].delete(0, "end")
    d.lbl["con"].insert(0, "+12")
    d.lbl["con"].event_generate("<Return>")
    root.update()
    assert abs(csz.color["sat"].get() - 1) < 1e-6 and abs(csz.color["con"].get() - 12) < 1e-6
    assert d.lbl["sat"].get() == "+1", d.lbl["sat"].get()
    d.close()
    print("7) deslizadores: la rueda mueve (también sobre la casilla; con Mayúsculas, x5), las "
          "flechas y RePág/AvPág también; en la casilla se escribe (150 %, -7,5, +12; 999 -> 600; "
          "si no es un número, nada); la ganancia del audio y la ventanita de color, igual; "
          "grabando, el zoom quieto y el color sí OK")
finally:
    stop.set()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    try:
        app._phonecam_disable()
    except Exception:
        pass
    root.destroy()
    shutil.rmtree(HOME, ignore_errors=True)
print("TODO OK ui_preview_menu")
