"""Diseño de ventanas: dos fallos reales reportados (con captura) en una sesión real de Windows.
Como tests/ui_netcam_edit.py, hace falta un display real (Xvfb en Linux:
xvfb-run -a python3 tests/ui_window_layout.py).

Importante para quien toque este archivo: NO uses root.withdraw() -- bajo Xvfb sin gestor de
ventanas, una ventana Toplevel «transient» de una raíz retirada (withdraw) nunca llega a
mapearse de verdad, y entonces TODAS las medidas de geometría (winfo_width/height) salen a 1px
sin que signifique nada (ni es el fallo real, ni demuestra que esté arreglado). En su lugar se dej
a la raíz como una ventana real minúscula fuera de la vista (root.geometry("1x1+0+0")).
"""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import os
import sys
import time
import types

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_window_layout_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
# OJO: lo que de verdad lee/escribe _read_config()/save_config() es la constante CONFIG, no
# «CFG_FILE» (ese nombre no existe en camui.py -- fijarlo no aísla nada y esta prueba acaba
# leyendo/escribiendo el config.json real del proyecto).
camui.CONFIG = os.path.join(HOME, "config.json")
if os.path.isfile(camui.CONFIG):            # arranque limpio: sin restos de una ejecución anterior
    os.remove(camui.CONFIG)
camui.LOGFILE = os.path.join(HOME, "camui.log")

root = tk.Tk()
root.geometry("1x1+0+0")

# ---------------------------------------------------------------------------------------------
# 1) Ventana "Actualizaciones" (la común de py-installer, pyi_update): con las novedades largas
#    (NOVEDADES.md real), los botones de abajo siguen viéndose enteros y las novedades tienen su
#    barra; el atajo secreto Ctrl+Shift+Alt+F12 se recibe desde la ventana principal de cam-ui
#    (abre Actualizaciones con «vigilar sola» a la vista) y NO dispara el reinicio de emergencia
#    de F12.
# ---------------------------------------------------------------------------------------------
import pyi_update  # noqa: E402
APPDIR = os.path.join(HOME, "app")
os.makedirs(APPDIR, exist_ok=True)
fake_file = os.path.join(APPDIR, "camui.py")
open(fake_file, "w").write("x = 1\n")
camui._UPDATE_ST = {}
camui.APP_FILE = fake_file
camui.UPDATE_BACKUPS = os.path.join(HOME, "backups")
panic = []
fake_app = types.SimpleNamespace(root=root, recording=False, busy=False, on_close=lambda: None,
                                 _set_status=lambda t: None, panic_reset=lambda: panic.append(1),
                                 _bg=lambda fn, cb: cb(fn()))
fake_app._updates_ctl = lambda: camui.CamApp._updates_ctl(fake_app)
fake_app.show_updates = lambda *a, check=True: camui.CamApp.show_updates(fake_app, check=check)
root.bind("<F12>", lambda e: None if e.state & 0x5 else fake_app.panic_reset())   # como en CamApp
ctl = fake_app._updates_ctl()
dlg = fake_app.show_updates(check=False)
notas = open(os.path.join(os.path.dirname(HERE), "NOVEDADES.md"), encoding="utf-8").read()
assert notas.count("\n") > 12, "NOVEDADES.md ya no sirve para probar el recorte (muy corto)"
dlg._w_notes(notas)
top = dlg.top
for _ in range(30):
    top.update_idletasks()
    top.update()
    if top.winfo_width() > 1:
        break
    time.sleep(0.1)
assert top.winfo_width() > 1, "la ventana de Actualizaciones no llegó a mostrarse"
for name, btn in [("Buscar de nuevo", dlg.b_check), ("Volver a la versión anterior", dlg.b_back),
                  ("Actualizar y reiniciar", dlg.b_go)]:
    assert btn.winfo_height() >= 15 and btn.winfo_width() >= 15, \
        "el botón «{0}» no se ve (tamaño {1}x{2})".format(name, btn.winfo_width(), btn.winfo_height())
    assert btn.winfo_rooty() + btn.winfo_height() <= top.winfo_rooty() + top.winfo_height(), \
        "el botón «{0}» queda fuera del borde inferior de la ventana".format(name)
print("1) ventana «Actualizaciones» (común): con novedades largas, los botones se ven enteros -- OK")
tv0 = dlg.notes.yview()
dlg.notes.yview_moveto(1.0)
top.update()
tv1 = dlg.notes.yview()
assert tv1[1] >= 0.999 and tv1[0] > tv0[0], "la barra de Novedades no llega hasta el final"
print("2) las novedades se recorren enteras con su barra -- OK")
assert not dlg.watch_visible(), "«vigilar sola» debería empezar escondida"
top.destroy()
root.update()
root.focus_force()
root.update()
root.event_generate("<Control-Shift-Alt-F12>")
root.update()
dlg = ctl.win
assert dlg.alive() and dlg.watch_visible(), "el atajo desde la ventana principal no abre Actualizaciones con la casilla"
assert not panic, "el atajo secreto no debe disparar el reinicio de emergencia (F12)"
print("3) el atajo secreto desde la ventana principal abre Actualizaciones con «vigilar sola», "
      "sin disparar F12 -- OK")
dlg.v["watch"].set(True)
dlg.toggled_watch(True)
assert camui.update_state().get("silent_watch") is True
dlg.v["watch"].set(False)
dlg.toggled_watch(False)
assert camui.update_state().get("silent_watch") is False and not dlg.watch_visible()
print("4) activar/desactivar «vigilar sola» se guarda en el actualizacion.json de cam-ui -- OK")
assert ctl.watch_every_ms == camui.SILENT_WATCH_INTERVAL_MS
fake_app.recording = True
assert ctl.busy() is True, "grabando, el actualizador no debe reiniciar"
fake_app.recording = False
print("5) cam-ui le pasa al actualizador su intervalo y «ocupado = grabando» -- OK")
dlg.top.destroy()
camui._UPDATE_ST = {"silent_watch": True}
ctl.u._st = camui.update_state()
dlg3 = fake_app.show_updates(check=False)
root.update()
assert dlg3.watch_visible(), "si ya estaba activada, la ventana nueva debe mostrarla"
print("6) si ya estaba activada de antes, se ve sin el atajo -- OK")
dlg3.top.destroy()

# ---------------------------------------------------------------------------------------------
# 2) Lista de cámaras: hacer scroll (rueda del ratón) sobre ella debe mover SOLO la lista, no
#    toda la pestaña -- y la lista debe tener su propia barra de desplazamiento.
# ---------------------------------------------------------------------------------------------
app = camui.CamApp(root)
# sin esto, a los ~900ms CamApp._tray_boot() (al no estar pystray instalado, ni haber ya una
# respuesta guardada) abre un messagebox.askyesno() de verdad -- un diálogo modal que bajo Xvfb,
# sin nadie para pulsarlo, se queda colgado para siempre y nunca llega a soltar root.update().
app._tray_asked = True
# sin cámaras de verdad en este entorno, la vista previa "siempre activa" de cam-ui no consigue
# arrancar nunca y _tick() reintenta sola una y otra vez (_on_died -> _start_preview), lo que
# deja app.busy=True en momentos impredecibles -- esto no es lo que se está probando aquí (ver
# tests/unit_netcam.py para eso), así que se desactiva para no depender de ganarle la carrera.
app.previewing = False
app._start_preview = lambda *a, **kw: None
root.update()
t0 = time.time()
while not app.vdev and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)

for i in range(8):
    lab = "🌐 Cam{0}".format(i)
    app.net_cams[lab] = {"name": "Cam{0}".format(i), "alt": None, "label": lab,
                         "spec": "http://1.2.3.{0}:8080/video".format(i), "net": True}
app._refresh_devices()
t0 = time.time()
while "🌐 Cam7" not in app.vdev and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)
app.nb.select(0)
root.update()
app.tree.update_idletasks()

assert len(app.tree.get_children()) > int(app.tree["height"]), \
    "hacen falta más filas que las visibles para probar el scroll de verdad"
tv0 = app.tree.yview()
assert tv0[1] < 1.0, "con 8+ cámaras debería haber recorte en la lista (hace falta scroll)"

cams_cv = next((cv for cv in getattr(app, "_tab_canvases", []) if cv.winfo_ismapped()), None)
assert cams_cv is not None, "no se encontró el canvas con scroll de la pestaña activa"
cv0 = cams_cv.yview()

bbox = app.tree.bbox(app.tree.get_children()[0])
assert bbox, "no se ve ninguna fila de la lista de cámaras"
x_root = app.tree.winfo_rootx() + bbox[0] + bbox[2] // 2
y_root = app.tree.winfo_rooty() + bbox[1] + bbox[3] // 2
app.tree.event_generate("<MouseWheel>", delta=-120, x=bbox[0] + 2, y=bbox[1] + 2,
                        rootx=x_root, rooty=y_root)
root.update()
tv1, cv1 = app.tree.yview(), cams_cv.yview()
assert tv1[0] > tv0[0], "la lista de cámaras no se desplazó con la rueda del ratón"
assert abs(cv1[0] - cv0[0]) < 1e-9, \
    "la rueda sobre la lista desplazó TODA la pestaña, no solo la lista (el fallo reportado)"
print("3) la rueda del ratón sobre la lista de cámaras mueve SOLO la lista, no todo el "
      "contenedor -- OK")

sbs = [c for c in app.tree.master.winfo_children() if c.winfo_class() == "TScrollbar"]
assert len(sbs) == 1, "la lista de cámaras debería tener su propia barra de desplazamiento"
app.tree.yview_moveto(1.0)
root.update()
assert app.tree.yview()[1] >= 0.999, "la barra de la lista no llega hasta el final"
print("4) la lista de cámaras tiene su propia barra de desplazamiento y llega al final -- OK")

# ---------------------------------------------------------------------------------------------
# 5) Ventana de informe/diagnóstico (_show_report, p. ej. desde "Diagnosticar"): mismo problema
#    que la de Actualizaciones -- con un informe largo, "Copiar informe" y "Cerrar" quedaban
#    reducidos a una rendija de 8px, prácticamente invisibles e imposibles de pulsar.
# ---------------------------------------------------------------------------------------------
texto_largo = "\n".join(
    "línea de diagnóstico número {0}: todo correcto, ffmpeg responde".format(i)
    for i in range(200))
app._show_report(texto_largo, title="Diagnóstico")
root.update()
rep = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
assert rep, "no se abrió el diálogo de informe"
rtop = rep[-1]
for _ in range(20):
    rtop.update_idletasks()
    rtop.update()
    if rtop.winfo_width() > 1:
        break
    time.sleep(0.1)

rbtns = []
def _find_buttons(w):
    for c in w.winfo_children():
        if c.winfo_class() == "TButton":
            rbtns.append(c)
        _find_buttons(c)
_find_buttons(rtop)
assert len(rbtns) == 2, rbtns
for b in rbtns:
    assert b.winfo_width() >= 10 and b.winfo_height() >= 15, \
        "el botón «{0}» del informe no se ve bien (tamaño {1}x{2})".format(
            b["text"], b.winfo_width(), b.winfo_height())
rtop.destroy()
print("5) ventana de informe/diagnóstico: con un informe largo, los botones siguen viéndose "
      "enteros -- OK")

root.destroy()
print("TODO OK ui_window_layout")
