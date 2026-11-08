"""«Cámara del móvil» de verdad, de punta a punta, dentro de la propia app (ver PhoneCamHub y
CamApp._phonecam_* en camui.py): a diferencia de tests/unit_phonecam.py (que solo prueba el
servidor), aquí se activa desde la propia CamApp (como el botón «Activar cámara del móvil») y se
manda un fotograma real por HTTPS, igual que haría el navegador de un móvil -- y se comprueba que
la cámara aparece sola en la lista (Treeview), marcada y lista para grabar. Hace falta un display
(Xvfb en Linux: xvfb-run -a python3 tests/ui_phonecam.py), porque lo que se comprueba es
precisamente la ventana real, no solo la lógica de más abajo."""
import os
import io
import ssl
import sys
import time
import urllib.request

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_phonecam_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
# OJO: lo que de verdad lee/escribe _read_config()/save_config() es la constante CONFIG, no un
# «CFG_FILE» (ese nombre no existe en camui.py -- fijarlo, como hacía algún test anterior, no
# aisla nada y acaba escribiendo en el config.json real del proyecto). Se deja aquí el nombre
# correcto para que esta prueba quede de verdad aislada en su propia carpeta.
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")
camui.PHONECAM_CERT_FILE = os.path.join(HOME, "phonecam_cert.pem")
camui.PHONECAM_KEY_FILE = os.path.join(HOME, "phonecam_key.pem")
if os.path.isfile(camui.CONFIG):            # arranque limpio: sin restos de una ejecución anterior
    os.remove(camui.CONFIG)                 # (_phonecam_enable guarda "on": true de verdad)

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
# sin esto, a los ~900ms CamApp._tray_boot() (al no estar pystray instalado, ni haber ya una
# respuesta guardada) abre un messagebox.askyesno() de verdad -- un diálogo modal que bajo Xvfb,
# sin nadie para pulsarlo, se queda colgado para siempre y nunca llega a soltar root.update().
app._tray_asked = True
# sin cámaras físicas en este entorno (ver el mismo comentario en ui_netcam_edit.py)
app.previewing = False
app._start_preview = lambda *a, **kw: None
root.update()

t0 = time.time()
while not app.vdev and time.time() - t0 < 5:
    root.update()
    time.sleep(0.05)


def pump(cond, timeout=6):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        root.update()
        time.sleep(0.05)
    return cond()


# 1) activar «Cámara del móvil» desde la propia app (como el botón de Ajustes) arranca de
#    verdad el servidor HTTPS y rellena la etiqueta con la dirección + token
assert not app.phonecam_on and not app.phonecam.active
app.v_phonecamport.set("58620")
ok = app._phonecam_enable()
assert ok and app.phonecam_on and app.phonecam.active
url = app._phonecam_url()
assert url.startswith("https://") and "?t=" in url
assert "No está activa" not in app.lbl_phonecamurl.cget("text")
# la URL de verdad usa la IP detectada del equipo (para que un móvil de la wifi la encuentre),
# pero esta caja de pruebas no siempre deja conectar a su propia IP "pública" vista desde dentro
# -- no es lo que se está probando aquí (ya se prueba a fondo en unit_phonecam.py), así que esta
# prueba habla con el mismo servidor por 127.0.0.1, con el mismo token real
local_url = "https://127.0.0.1:{0}/cam?t={1}".format(app.phonecam.port, app.phonecam.token)
print("1) «Activar cámara del móvil» (como el botón real) arranca el servidor HTTPS de verdad OK")

# 2) todavía no hay ninguna cámara de móvil en la lista
assert not any(lab.startswith("📱 ") for lab in app.vdev)
print("2) sin ningún móvil conectado aún, no hay ninguna cámara «📱» en la lista OK")

# 3) un móvil «de verdad» (HTTPS, token correcto, fotograma real) se conecta -- justo lo que
#    haría el navegador del móvil tras tocar «Empezar» y conceder el permiso de cámara
ctx = ssl._create_unverified_context()
with urllib.request.urlopen(local_url, timeout=5, context=ctx) as r:
    body = r.read().decode("utf-8")
import re
sid = re.search(r'var sid = "([^"]+)"', body).group(1)
arr = np.zeros((240, 320, 3), np.uint8)
arr[:, :, 1] = 180
buf = io.BytesIO()
Image.fromarray(arr).save(buf, format="JPEG", quality=80)
jpeg = buf.getvalue()
req = urllib.request.Request(
    "https://127.0.0.1:{0}/cam/ingest/{1}".format(app.phonecam.port, sid),
    data=jpeg, method="POST")
with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
    assert r.status == 200
print("3) el móvil (de verdad: HTTPS + token + POST de un JPEG) manda su primer fotograma OK")

# 4) la propia CamApp (por su bucle normal, _tick -> _phonecam_q) la añade sola a la lista,
#    marcada, lista para grabar -- sin que el usuario haya tocado nada en el PC
label = "📱 Móvil"
assert pump(lambda: label in app.vdev), "la cámara del móvil nunca apareció en app.vdev"
assert pump(lambda: label in app.order), "la cámara del móvil nunca se marcó/añadió a app.order"
assert app.cams[label].use is True
assert app.vdev[label]["spec"].startswith("http://127.0.0.1:")
assert app.vdev[label].get("phone") is True
print("4) la cámara del móvil se añade sola a app.order, marcada (use=True) OK")

# 5) también aparece en el Treeview de verdad (la lista que ve la persona), marcada con ☑
row = app.tree.item(label) if label in app.tree.get_children() else None
assert row is not None, "la cámara del móvil no aparece en la fila del Treeview"
assert row["values"][0] == "☑", row["values"]
print("5) también aparece marcada (☑) en la lista real (Treeview) de la ventana OK")

# 6) una cámara de móvil NO se guarda en el config.json (su URL no sobrevive a reiniciar
#    cam-ui -- ver el comentario en save_config): solo las añadidas a mano sí
app.save_config()
with open(camui.CONFIG, encoding="utf-8") as f:
    import json
    saved = json.load(f)
assert label not in saved.get("net_cams", {}), \
    "la cámara del móvil no debería guardarse en config.json (quedaría huérfana)"
print("6) save_config() no guarda la cámara del móvil (no sobreviviría a un reinicio) OK")

# 7) desactivar «Cámara del móvil» para el servidor de verdad
app._phonecam_disable()
assert not app.phonecam_on and not app.phonecam.active
assert app.lbl_phonecamurl.cget("text") == "No está activa."
print("7) «Apagar cámara del móvil» para el servidor de verdad y limpia la etiqueta OK")

root.destroy()
print("TODO OK ui_phonecam")
