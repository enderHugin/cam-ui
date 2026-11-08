"""«Cámara del móvil» de verdad, de punta a punta, dentro de la propia app (ver PhoneCamHub y
CamApp._phonecam_* en camui.py): a diferencia de tests/unit_phonecam.py (que solo prueba el
servidor), aquí se activa desde la propia CamApp (como el botón «Añadir un dispositivo móvil
como cámara») y se simula el emparejamiento por «/add» (POST a «/add/ready», QR mostrado en la
ventana, URL del QR reutilizando esa sesión) y el envío de un fotograma real por HTTPS, igual
que haría el navegador de un móvil tras leer ese QR con su propia cámara -- y se comprueba que
la cámara aparece sola en la lista (Treeview), marcada y lista para grabar. El propio JS del
navegador (jsQR leyendo un QR de verdad en un Chromium real) se valida aparte, con Playwright,
no aquí. También comprueba que el QR de emparejamiento aparece (y se cierra solo) en un diálogo
flotante aparte, y que el botón «Añadir cámara del móvil» de la página «En la red» enciende
«Cámara del móvil» por sí sola -- de verdad, con el servidor HTTP real y el bucle normal de la
ventana -- en vez de decir que hay que ir antes a Ajustes a mano. Hace falta un display (Xvfb en
Linux: xvfb-run -a python3 tests/ui_phonecam.py), porque lo que se comprueba es precisamente la
ventana real, no solo la lógica de más abajo."""
import os
import io
import json
import re
import ssl
import sys
import threading
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


# 1) activar «Cámara del móvil» desde la propia app (como el botón de Ajustes, ya renombrado a
#    «Añadir un dispositivo móvil como cámara») arranca de verdad el servidor HTTPS y deja en la
#    etiqueta las instrucciones con la dirección fija «/add» (sin ningún token: por sí sola no
#    añade nada, ver el comentario junto a «/add» en _PhoneCamHandler)
assert not app.phonecam_on and not app.phonecam.active
app.v_phonecamport.set("58620")
ok = app._phonecam_enable()
assert ok and app.phonecam_on and app.phonecam.active
add_url = app._phonecam_add_url()
assert add_url.startswith("https://") and add_url.endswith("/add") and "?" not in add_url
assert add_url in app.lbl_phonecamurl.cget("text")
assert "No está activa" not in app.lbl_phonecamurl.cget("text")
# la URL de verdad usa la IP detectada del equipo (para que un móvil de la wifi la encuentre),
# pero esta caja de pruebas no siempre deja conectar a su propia IP "pública" vista desde dentro
# -- no es lo que se está probando aquí (ya se prueba a fondo en unit_phonecam.py), así que esta
# prueba habla con el mismo servidor por 127.0.0.1, con el mismo token y puerto reales
ctx = ssl._create_unverified_context()
print("1) «Añadir un dispositivo móvil como cámara» (como el botón real) arranca el servidor "
      "HTTPS de verdad y deja la dirección de «/add» en la etiqueta OK")

# 2) todavía no hay ninguna cámara de móvil en la lista, ni ningún QR de emparejamiento
assert not any(lab.startswith("📱 ") for lab in app.vdev)
assert app.lbl_phonecamqr.cget("image") == ""
print("2) sin ningún móvil conectado aún, no hay cámara ni QR de emparejamiento OK")

# 3) el móvil que está en «/add» ya tiene el permiso de su cámara y avisa con un POST a
#    «/add/ready» -- eso hace que la ventana (por su bucle normal, _tick -> _phonecam_pair_q)
#    muestre un QR nuevo, y cambie el texto para decir que hay un móvil esperando
req_ready = urllib.request.Request(
    "https://127.0.0.1:{0}/add/ready".format(app.phonecam.port), data=b"", method="POST")
with urllib.request.urlopen(req_ready, timeout=5, context=ctx) as r:
    assert r.status == 200
assert pump(lambda: app.lbl_phonecamqr.cget("image") != ""), \
    "el QR de emparejamiento nunca apareció en la ventana"
assert "esperando" in app.lbl_phonecamurl.cget("text").lower()
# también debe aparecer, al instante, en un diálogo flotante aparte -- no solo dentro de la
# pestaña «Ajustes» (que, en una ventana real, podría no ser la que está abierta en ese momento)
dlg = getattr(app, "_phonecam_qr_dialog", None)
assert dlg is not None, "el diálogo flotante del QR debería haberse abierto"
assert dlg.alive and dlg.top.winfo_exists(), \
    "el diálogo flotante del QR debería ser una ventana real, abierta de verdad"
print("3) «/add/ready» hace que la ventana muestre un QR de emparejamiento nuevo, también en "
      "un diálogo flotante aparte OK")

# 4) ese QR lleva dentro una URL de «/cam?t=...&sid=...» -- el móvil la lee con su propia
#    cámara (eso ya se probó de verdad, con jsQR en un navegador real, aparte de esta prueba) y
#    navega solo ahí; aquí se simula ese paso final con la misma URL, hablando por 127.0.0.1.
# El sid exacto no se guardó al mostrar el QR (CamApp solo necesita la imagen, no el texto), así
# que se saca directamente de la única sesión de PhoneCamHub que todavía no ha mandado nada.
with app.phonecam._lk:
    pending = [sid for sid, s in app.phonecam._sessions.items() if not s["announced"]]
assert len(pending) == 1, pending
sid = pending[0]
local_url = "https://127.0.0.1:{0}/cam?t={1}&sid={2}".format(
    app.phonecam.port, app.phonecam.token, sid)
with urllib.request.urlopen(local_url, timeout=5, context=ctx) as r:
    body = r.read().decode("utf-8")
sid_in_page = re.search(r'var sid = "([^"]+)"', body).group(1)
assert sid_in_page == sid, "debería reutilizar el sid del QR, no acuñar uno nuevo"
print("4) la URL leída del QR reutiliza esa misma sesión (no una nueva) OK")

# 5) el móvil manda su primer fotograma de verdad, por POST (como mandaría el navegador desde
#    canvas.toBlob, ya en la página de captura de siempre, tras haber navegado solo hasta ahí)
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
print("5) el móvil (de verdad: HTTPS + sid del QR + POST de un JPEG) manda su primer fotograma OK")

# 6) la propia CamApp (por su bucle normal, _tick -> _phonecam_q) la añade sola a la lista,
#    marcada, lista para grabar -- sin que el usuario haya tocado nada en el PC -- y el QR de
#    emparejamiento, ya usado, se retira solo de la ventana
label = "📱 Móvil"
assert pump(lambda: label in app.vdev), "la cámara del móvil nunca apareció en app.vdev"
assert pump(lambda: label in app.order), "la cámara del móvil nunca se marcó/añadió a app.order"
assert app.cams[label].use is True
assert app.vdev[label]["spec"].startswith("http://127.0.0.1:")
assert app.vdev[label].get("phone") is True
assert pump(lambda: app.lbl_phonecamqr.cget("image") == ""), \
    "el QR de emparejamiento debería retirarse solo en cuanto el móvil se añade"
assert getattr(app, "_phonecam_qr_dialog", None) is None, \
    "el diálogo flotante del QR debería cerrarse solo en cuanto el móvil se añade"
print("6) la cámara del móvil se añade sola a app.order, marcada (use=True), y el QR se retira "
      "(también el diálogo flotante) OK")

# 7) también aparece en el Treeview de verdad (la lista que ve la persona), marcada con ☑
row = app.tree.item(label) if label in app.tree.get_children() else None
assert row is not None, "la cámara del móvil no aparece en la fila del Treeview"
assert row["values"][0] == "☑", row["values"]
print("7) también aparece marcada (☑) en la lista real (Treeview) de la ventana OK")

# 8) una cámara de móvil NO se guarda en el config.json (su URL no sobrevive a reiniciar
#    cam-ui -- ver el comentario en save_config): solo las añadidas a mano sí
app.save_config()
with open(camui.CONFIG, encoding="utf-8") as f:
    saved = json.load(f)
assert label not in saved.get("net_cams", {}), \
    "la cámara del móvil no debería guardarse en config.json (quedaría huérfana)"
print("8) save_config() no guarda la cámara del móvil (no sobreviviría a un reinicio) OK")

# 9) un QR de emparejamiento que nadie escanea a tiempo se retira solo (ver PHONECAM_PAIR_TIMEOUT)
req_ready2 = urllib.request.Request(
    "https://127.0.0.1:{0}/add/ready".format(app.phonecam.port), data=b"", method="POST")
with urllib.request.urlopen(req_ready2, timeout=5, context=ctx) as r:
    assert r.status == 200
assert pump(lambda: app.lbl_phonecamqr.cget("image") != ""), "el segundo QR nunca apareció"
dlg2 = app._phonecam_qr_dialog
assert dlg2 is not None and dlg2.alive, "el diálogo flotante del segundo QR debería estar abierto"
app._phonecam_pair_shown_at = time.time() - camui.PHONECAM_PAIR_TIMEOUT - 1   # como si hiciera rato
assert pump(lambda: app.lbl_phonecamqr.cget("image") == ""), \
    "el QR caducado debería retirarse solo en el siguiente _tick()"
assert not dlg2.alive and app._phonecam_qr_dialog is None, \
    "el diálogo flotante debería cerrarse solo también al caducar, no solo al emparejar"
print("9) un QR de emparejamiento sin escanear a tiempo se retira solo (también su diálogo "
      "flotante) OK")

# 10) desactivar «Cámara del móvil» para el servidor de verdad
app._phonecam_disable()
assert not app.phonecam_on and not app.phonecam.active
assert app.lbl_phonecamurl.cget("text") == "No está activa."
print("10) «Apagar cámara del móvil» para el servidor de verdad y limpia la etiqueta OK")

# 11) el botón «Añadir cámara del móvil» de la página «En la red» (:8554), con «Cámara del
#     móvil» todavía apagada (como la pulsaría un móvil que llega ahí por su cuenta): ya no debe
#     decir «ve a Ajustes, en el PC» -- debe encenderla él mismo, de verdad, y devolver la
#     dirección real de «/add», todo con el servidor HTTP real de «En la red» y el bucle normal
#     de la ventana (_tick -> _phonecam_remote_q -> _phonecam_enable)
assert not app.phonecam_on and not app.phonecam.active
app.v_ipport.set("58622")
assert app._ip_enable(), "no se pudo abrir «En la red» de verdad para esta prueba"
_addcam_result = {}


def _post_addcam_start():
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:{0}/addcam/start".format(app.ipsrv.port), data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            _addcam_result["status"] = r.status
            _addcam_result["body"] = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        _addcam_result["error"] = repr(e)


th = threading.Thread(target=_post_addcam_start, daemon=True)
th.start()
assert pump(lambda: "status" in _addcam_result or "error" in _addcam_result, timeout=8), \
    "la petición POST a «/addcam/start» nunca contestó"
th.join(timeout=1)
assert "error" not in _addcam_result, _addcam_result.get("error")
assert _addcam_result["status"] == 200, _addcam_result
assert app.phonecam_on and app.phonecam.active, \
    "el propio _tick() debería haber encendido «Cámara del móvil» sola, sin ir a Ajustes"
assert _addcam_result["body"].get("url") == app._phonecam_add_url(), \
    (_addcam_result["body"], app._phonecam_add_url())
print("11) el botón «Añadir cámara del móvil» de «En la red», con esa función todavía apagada, "
      "la enciende ella sola (sin «ve a Ajustes, en el PC») y devuelve la dirección real de "
      "«/add» OK")

# 12) ese encendido remoto queda guardado como encendida, igual que si se hubiera tocado el
#     interruptor de Ajustes a mano -- para que siga encendida la próxima vez que se abra cam-ui
with open(camui.CONFIG, encoding="utf-8") as f:
    saved2 = json.load(f)
assert saved2.get("phonecam", {}).get("on") is True, saved2.get("phonecam")
print("12) ese encendido remoto se guarda como encendida (persiste igual que el interruptor de "
      "Ajustes) OK")

# 13) con «Cámara del móvil» ya encendida, ese mismo botón no vuelve a pasar por «/addcam/start»
#     -- _serve_page ya le da la dirección final de «/add» de antemano (esto ya se prueba sin
#     Tk, con un IpServer suelto, en tests/unit_viewer_html.py; aquí solo se confirma que, con
#     la ventana real, esa dirección servida coincide con la de verdad)
with urllib.request.urlopen("http://127.0.0.1:{0}/".format(app.ipsrv.port), timeout=5) as r:
    home_body = r.read().decode("utf-8")
assert 'addCamUrl = "{0}";'.format(app._phonecam_add_url()) in home_body, \
    "con la cámara del móvil ya encendida, «/» debería servir ya su dirección real de «/add»"
print("13) con «Cámara del móvil» ya encendida, «/» sirve directamente su dirección real de "
      "«/add» (sin pasar por «/addcam/start») OK")

app._phonecam_disable()
app._ip_disable()
root.destroy()
print("TODO OK ui_phonecam")
