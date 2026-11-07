"""Doble clic en una cámara de red (añadida a mano) para editarla: a diferencia del resto de
tests/*.py (que no necesitan pantalla), este SÍ crea una ventana Tk real -- hace falta un
display (Xvfb en Linux: xvfb-run -a python3 tests/ui_netcam_edit.py), porque lo que se
comprueba es precisamente el diálogo y la fila de la lista (Treeview), no solo la lógica de
más abajo. CamApp se crea de verdad (sin ffmpeg: no llega a arrancar vista previa ni grabar).
"""
import os
import sys
import time
import types

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_netcam_edit_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CFG_FILE = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
# sin cámaras de verdad en este entorno, la vista previa "siempre activa" de cam-ui no consigue
# arrancar nunca y _tick() reintenta sola una y otra vez (_on_died -> _start_preview), lo que
# deja app.busy=True en momentos impredecibles -- esto no es lo que se está probando aquí (ver
# tests/unit_netcam.py para eso), así que se desactiva para que la prueba del diálogo no dependa
# de ganarle la carrera a ese reintento de fondo.
app.previewing = False
app._start_preview = lambda *a, **kw: None
root.update()

t0 = time.time()
while not app.vdev and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)

LAB = "🌐 Móvil"
app.net_cams[LAB] = {"name": "Móvil", "alt": None, "label": LAB,
                      "spec": "http://1.2.3.4:8080/video", "net": True}
app._refresh_devices()
t0 = time.time()
while LAB not in app.vdev and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)
assert LAB in app.vdev, "no se añadió la cámara de red de prueba"
# _refresh_devices() deja app.busy=True mientras escanea en segundo plano; _net_cam_dialog no
# abre nada mientras tanto (mismo motivo que más abajo, tras guardar). Sin esperar aquí, el
# primer doble clic podía fallar o no según cuánto hubiera tardado ya el bucle de arriba --
# mejor no depender de esa suerte.
t0 = time.time()
while app.busy and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)
app.cams[LAB].use = True
if LAB not in app.order:
    app.order.append(LAB)
app.sel = LAB
app.cams[LAB].zoom.set(180.0)
app.cams[LAB].color["bri"].set(12.5)
root.update()


def _entries(w):
    out = []
    for c in w.winfo_children():
        if isinstance(c, tk.Entry) or c.winfo_class() == "TEntry":
            out.append(c)
        out += _entries(c)
    return out


def _button(w, text):
    for c in w.winfo_children():
        if c.winfo_class() == "TButton" and c["text"] == text:
            return c
        found = _button(c, text)
        if found:
            return found
    return None


def _topmost():
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    assert tops, "no se abrió ningún diálogo"
    return tops[-1]


# 1) doble clic sobre el NOMBRE (columna "#2") de una cámara de red -> abre el diálogo de
#    edición, pre-rellenado con su nombre y URL actuales.
app.tree.update_idletasks()
bbox = app.tree.bbox(LAB, "#2")
assert bbox, "la fila de la cámara de red no está visible en el árbol"
x, y = bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2
assert app._tree_double_click(types.SimpleNamespace(x=x, y=y)) == "break"
root.update()
top = _topmost()
assert top.title() == "Editar cámara de red", top.title()
name_e, url_e = _entries(top)
assert name_e.get() == "Móvil", name_e.get()
assert url_e.get() == "http://1.2.3.4:8080/video", url_e.get()
print("1) doble clic en el nombre de una cámara de red: abre \"Editar cámara de red\" "
      "pre-rellenado OK")

# 2) ... y guardar SOLO con la URL cambiada no renombra nada: conserva el CamState (zoom/color)
url_e.delete(0, "end")
url_e.insert(0, "http://1.2.3.4:8080/video2")
_button(top, "Guardar").invoke()
root.update()
assert app.net_cams[LAB]["spec"] == "http://1.2.3.4:8080/video2", app.net_cams[LAB]
assert app.cams[LAB].zoom.get() == 180.0, "no debería tocarse el zoom si no cambia el nombre"
print("2) guardar sin cambiar el nombre: actualiza la URL, conserva zoom/color OK")

# 3) ... y guardar con el nombre CAMBIADO migra el CamState (zoom/color) a la nueva etiqueta,
#    y actualiza order/sel -- doble clic de nuevo para reabrir el diálogo ya editado antes.
# (guardar en el paso 2 relanzó _refresh_devices en segundo plano: espera a que acabe --
# self.busy sigue a True mientras tanto, y _net_cam_dialog no abre nada si lo está)
t0 = time.time()
while app.busy and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)
app.tree.update_idletasks()
bbox = app.tree.bbox(LAB, "#2")
assert bbox, "la fila de la cámara de red ya no está visible tras guardar la URL"
x, y = bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2
assert app.tree.identify_row(y) == LAB, (app.tree.identify_row(y), LAB)
assert app.tree.identify_column(x) == "#2", app.tree.identify_column(x)
app._tree_double_click(types.SimpleNamespace(x=x, y=y))
root.update()
top2 = _topmost()
name_e2, _url_e2 = _entries(top2)
name_e2.delete(0, "end")
name_e2.insert(0, "Patio")
_button(top2, "Guardar").invoke()
root.update()
NEW = "🌐 Patio"
assert LAB not in app.net_cams and NEW in app.net_cams, app.net_cams.keys()
assert LAB not in app.cams and NEW in app.cams, "el CamState viejo no debe quedar huérfano"
assert app.cams[NEW].zoom.get() == 180.0
assert app.cams[NEW].color["bri"].get() == 12.5
assert NEW in app.order and LAB not in app.order, app.order
assert app.sel == NEW, app.sel
print("3) guardar con el nombre cambiado: migra el CamState (zoom/color) y order/sel OK")

# 4) doble clic en la columna del CHECKBOX ("#1") no abre nada (solo "#2", el nombre, edita)
tops_before = len(root.winfo_children())
bbox1 = app.tree.bbox(NEW, "#1")
x1, y1 = bbox1[0] + bbox1[2] // 2, bbox1[1] + bbox1[3] // 2
app._tree_double_click(types.SimpleNamespace(x=x1, y=y1))
root.update()
assert len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]) == 0, \
    "doble clic en la casilla no debería abrir el diálogo de edición"
print("4) doble clic en la casilla de la lista (no el nombre): no abre nada OK")

# 5) doble clic sobre una cámara física (no de red) no abre nada, aunque sea en su nombre
phys = next((lab for lab in app.vdev if not app.vdev[lab].get("net")), None)
if phys:
    bbox5 = app.tree.bbox(phys, "#2")
    if bbox5:
        x5, y5 = bbox5[0] + bbox5[2] // 2, bbox5[1] + bbox5[3] // 2
        app._tree_double_click(types.SimpleNamespace(x=x5, y=y5))
        root.update()
        assert len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]) == 0, \
            "doble clic en una cámara física no debería abrir el diálogo de editar cámara de red"
        print("5) doble clic en el nombre de una cámara física (no de red): no abre nada OK")
    else:
        print("5) (sin cámara física visible en el árbol para probarlo; omitida)")
else:
    print("5) (sin cámaras físicas detectadas en este entorno; omitida)")

root.destroy()
print("TODO OK ui_netcam_edit")
