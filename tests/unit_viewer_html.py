"""Página «En la red» (navegador): sin Tk ni navegador real -- arranca un IpServer de verdad,
pide «/» con urllib (como haría cualquier navegador) y comprueba el HTML servido de verdad
(no solo la constante en memoria). El comportamiento real del botón «Chromecast» en un
navegador de verdad (Chromium vía Playwright) se comprobó a mano al añadirlo; aquí se deja una
comprobación ligera, sin esa dependencia extra, de que el HTML/CSS/JS servidos siguen teniendo
las piezas que hacen falta para que funcione."""
import os
import sys
import time
import socket
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui
import numpy as np

PORT = 58402
srv = camui.IpServer()
srv.open(320, 240, fps=5, port=PORT)
sock_port = srv.arm()

stop = threading.Event()


def _sender():
    s = socket.create_connection(("127.0.0.1", sock_port), timeout=5)
    try:
        while not stop.is_set():
            s.sendall(np.zeros((240, 320, 3), np.uint8).tobytes())
            time.sleep(0.1)
    except Exception:
        pass


threading.Thread(target=_sender, daemon=True).start()
time.sleep(0.3)

try:
    with urllib.request.urlopen("http://127.0.0.1:{0}/".format(PORT), timeout=5) as resp:
        body = resp.read().decode("utf-8")
        ctype = resp.headers.get("Content-Type", "")
finally:
    stop.set()
    srv.release()

assert "text/html" in ctype, ctype
assert body.encode("utf-8") == camui._VIEWER_HTML, \
    "lo que sirve de verdad el servidor no coincide con la constante _VIEWER_HTML"
print("1) «/» sirve de verdad el HTML del visor (IpServer real, sin mocks) OK")

assert 'id="cast"' in body and "Chromecast" in body, \
    "falta el botón de ayuda para enviarlo a un Chromecast"
assert 'id="casthelp"' in body, "falta el cuadro de ayuda del botón Chromecast"
# oculto por CSS de entrada (nadie debería verlo sin pulsar el botón antes)
assert "#casthelp{display:none" in body.replace(" ", ""), \
    "la ayuda de Chromecast debería empezar oculta"
print("2) botón «Chromecast» y su cuadro de ayuda están en la página, oculto al cargar OK")

for frase in ("Convertir en Chromecast", "Firefox", "Safari"):
    assert frase in body, "falta «{0}» en el texto de ayuda del botón Chromecast".format(frase)
print("3) el texto de ayuda explica el camino (navegador) y avisa de qué navegadores no lo "
      "traen OK")

assert 'casthelp.classList.toggle("show")' in body, \
    "el botón «Chromecast» ya no abre/cierra su ayuda al pulsarlo"
assert "cast.onclick" in body
print("4) el JS sigue enlazando el botón con mostrar/ocultar la ayuda OK")

print("TODO OK unit_viewer_html")
