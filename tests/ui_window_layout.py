"""Diseño de ventanas: dos fallos reales reportados (con captura) en una sesión real de Windows.
Como tests/ui_netcam_edit.py, hace falta un display real (Xvfb en Linux:
xvfb-run -a python3 tests/ui_window_layout.py).

Importante para quien toque este archivo: NO uses root.withdraw() -- bajo Xvfb sin gestor de
ventanas, una ventana Toplevel «transient» de una raíz retirada (withdraw) nunca llega a
mapearse de verdad, y entonces TODAS las medidas de geometría (winfo_width/height) salen a 1px
sin que signifique nada (ni es el fallo real, ni demuestra que esté arreglado). En su lugar se dej
a la raíz como una ventana real minúscula fuera de la vista (root.geometry("1x1+0+0")).
"""
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
# 1) Ventana "Actualizaciones": con las novedades largas (NOVEDADES.md real, que ya pasa de las
#    12 líneas que cabían antes), los botones de abajo (Buscar de nuevo / Volver a la versión
#    anterior / Actualizar y reiniciar / Cerrar) deben seguir viéndose enteros -- antes
#    quedaban completamente recortados fuera de la ventana (0 píxeles de alto), ver captura del
#    usuario. Y el cuadro de Novedades debe poder recorrerse entero con su propia barra.
# ---------------------------------------------------------------------------------------------
fake_app = types.SimpleNamespace(root=root, recording=False, _bg=lambda fn, cb: None)
dlg = camui.UpdateDialog(fake_app)
notas = open(os.path.join(os.path.dirname(HERE), "NOVEDADES.md"), encoding="utf-8").read()
assert notas.count("\n") > 12, "NOVEDADES.md ya no sirve para probar el recorte (muy corto)"
dlg.res = {"ok": True, "state": "same", "local": {"pack": 1},
          "info": {"pack": 1, "version": "0", "notes": notas}}
dlg._fill_local()
dlg.notes.config(state="normal")
dlg.notes.delete("1.0", "end")
dlg.notes.insert("1.0", notas)
dlg.notes.config(state="disabled")
dlg._buttons()
top = dlg.top
top.deiconify()
for _ in range(20):
    top.update_idletasks()
    top.update()
    if top.winfo_width() > 1:
        break
    time.sleep(0.1)
assert top.winfo_width() > 1, "la ventana de Actualizaciones no llegó a mostrarse"
top.focus_force()
top.update()

for name, btn in [("Buscar de nuevo", dlg.b_check), ("Volver a la versión anterior", dlg.b_back),
                  ("Actualizar y reiniciar", dlg.b_go)]:
    assert btn.winfo_height() >= 15 and btn.winfo_width() >= 15, \
        "el botón «{0}» no se ve (tamaño {1}x{2})".format(
            name, btn.winfo_width(), btn.winfo_height())
    # dentro de los límites visibles de la ventana, no por debajo de su borde inferior
    assert btn.winfo_rooty() + btn.winfo_height() <= top.winfo_rooty() + top.winfo_height(), \
        "el botón «{0}» queda fuera del borde inferior de la ventana".format(name)
print("1) ventana «Actualizaciones»: con novedades largas, los 3 botones siguen viéndose "
      "enteros dentro de la ventana -- OK")

tv0 = dlg.notes.yview()
dlg.notes.yview_moveto(1.0)
top.update()
tv1 = dlg.notes.yview()
assert tv1[1] >= 0.999 and tv1[0] > tv0[0], "la barra de Novedades no llega hasta el final"
print("2) la barra de desplazamiento de «Novedades» funciona y llega hasta el final -- OK")

# ---------------------------------------------------------------------------------------------
# 1c) La misma ventana trae una casilla oculta («vigilar sola, sin avisos») que solo debería
#     verse tras un atajo secreto (Ctrl+Shift+Alt+F12, con la tecla Win pulsada) mientras esta
#     ventana tenga el foco -- y que, una vez activada, se queda visible (para poder apagarla
#     sin tener que volver a buscarla), y al apagarla se esconde de nuevo, al instante.
# ---------------------------------------------------------------------------------------------
assert not dlg.chk_silent_watch.winfo_ismapped(), \
    "la casilla de «vigilar sola» no debería verse de entrada, sin el atajo secreto"
print("3) la casilla oculta de «vigilar sola, sin avisos» empieza invisible, como debe ser OK")

top.event_generate("<Control-Shift-Alt-F12>")
top.update()
assert not dlg.chk_silent_watch.winfo_ismapped(), \
    "el atajo no debería revelar nada sin la tecla Win pulsada también"

top.event_generate("<KeyPress-Super_L>")
top.event_generate("<Control-Shift-Alt-F12>")
top.update()
assert dlg.chk_silent_watch.winfo_ismapped(), \
    "Ctrl+Shift+Alt+F12 con Win pulsada debería revelar la casilla oculta"
top.event_generate("<KeyRelease-Super_L>")
print("4) el atajo secreto (Win + Ctrl+Shift+Alt+F12) revela la casilla oculta -- y sin la "
      "tecla Win pulsada, no hace nada por sí solo OK")

dlg.var_silent_watch.set(True)
dlg._silent_watch_toggled()
assert camui.update_state().get("silent_watch") is True, \
    "activar la casilla debería guardarse de verdad (persistente)"
assert dlg.chk_silent_watch.winfo_ismapped(), \
    "activada, la casilla debería quedarse visible (no esconderse sola)"
print("5) activar la casilla la guarda de verdad (persistente) y se queda visible OK")

dlg.var_silent_watch.set(False)
dlg._silent_watch_toggled()
top.update()
assert camui.update_state().get("silent_watch") is False
assert not dlg.chk_silent_watch.winfo_ismapped(), \
    "al desactivarla debería esconderse de nuevo, sin esperar a cerrar la ventana"
print("6) desactivar la casilla la guarda como apagada y la esconde de nuevo, al instante OK")
top.destroy()

# si ya estaba activada de antes (de una sesión anterior), una ventana nueva debe mostrarla sin
# necesitar el atajo secreto -- si no, quien la activó no podría volver a encontrarla para
# apagarla
camui._UPDATE_ST = {"silent_watch": True}
dlg3 = camui.UpdateDialog(fake_app)
top3 = dlg3.top
top3.deiconify()
for _ in range(20):
    top3.update_idletasks()
    top3.update()
    if top3.winfo_width() > 1:
        break
    time.sleep(0.1)
assert top3.winfo_width() > 1, "la ventana de Actualizaciones no llegó a mostrarse"
assert dlg3.chk_silent_watch.winfo_ismapped(), \
    "si ya estaba activada de antes, una ventana nueva debería mostrarla sin el atajo secreto"
assert dlg3.var_silent_watch.get() is True
top3.destroy()
camui._UPDATE_ST = {}
print("7) si ya estaba activada de antes, una ventana «Actualizaciones» nueva la muestra sin "
      "necesitar el atajo secreto de nuevo OK")

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
