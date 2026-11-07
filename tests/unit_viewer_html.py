"""Página «En la red» (navegador): sin Tk ni navegador real -- arranca un IpServer de verdad,
pide «/» con urllib (como haría cualquier navegador) y comprueba el HTML servido de verdad (no
solo la constante en memoria), incluida la dirección real que se le pasa al botón nativo de
Chromecast (se sustituye por petición, ver _serve_page). El comportamiento real de los botones
en un navegador de verdad (Chromium vía Playwright) se comprobó a mano al añadirlos; aquí se
deja una comprobación ligera, sin esa dependencia extra ni ffmpeg, de que el HTML/CSS/JS
servidos siguen teniendo las piezas que hacen falta. El propio ffmpeg de HLS (lo que hay detrás
de /cast.m3u8) se prueba aparte, de verdad, en tests/unit_hls.py."""
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
print("1) «/» sirve el HTML del visor (IpServer real, sin mocks) OK")

# la constante tiene un «hueco» (__CAM_CAST_URL__) que _serve_page debe rellenar en cada
# petición con una dirección de verdad -- ni debe quedar el hueco literal, ni debe servirse la
# constante tal cual sin tocar
raw = camui._VIEWER_HTML.decode("utf-8")
assert "__CAM_CAST_URL__" in raw, "la plantilla ya no tiene el hueco esperado (revisar el test)"
assert "__CAM_CAST_URL__" not in body, \
    "el hueco de la URL de Chromecast se quedó sin rellenar en lo que se sirve de verdad"
assert body != raw, "se está sirviendo la plantilla tal cual, sin rellenar el hueco"
# la misma lógica que _serve_page: una IP real de la red si se encuentra una, si no 127.0.0.1
ips = camui._local_ips()
expect_host = ips[0] if ips else "127.0.0.1"
expect_url = "http://{0}:{1}/cast.m3u8".format(expect_host, PORT)
assert expect_url in body, \
    "la URL de /cast.m3u8 servida no es la esperada ({0})".format(expect_url)
assert body == raw.replace("__CAM_CAST_URL__", expect_url), \
    "el resto de la página cambió al rellenar el hueco (debería ser solo ese trozo)"
print("2) el hueco de la URL de Chromecast se rellena con una dirección real ({0}:{1}) "
      "OK".format(expect_host, PORT))

assert 'id="castBtn"' in body and "Chromecast" in body, \
    "falta el botón de ayuda para enviarlo a un Chromecast"
assert 'id="castLauncher"' in body, "falta el icono nativo de Chromecast (google-cast-launcher)"
assert 'id="casthelp"' in body, "falta el cuadro de ayuda del botón Chromecast"
# oculto por CSS de entrada (nadie debería verlo sin pulsar el botón antes, ni el icono nativo
# antes de que el SDK confirme que está disponible)
assert "#casthelp{display:none" in body.replace(" ", ""), \
    "la ayuda de Chromecast debería empezar oculta"
assert "google-cast-launcher{display:none" in body.replace(" ", ""), \
    "el icono nativo de Chromecast debería empezar oculto (hasta que el SDK confirme que hay)"
print("3) botón de ayuda + icono nativo de Chromecast están en la página, ambos ocultos al "
      "cargar OK")

for frase in ("Convertir en Chromecast", "Firefox", "Safari"):
    assert frase in body, "falta «{0}» en el texto de ayuda del botón Chromecast".format(frase)
print("4) el texto de ayuda explica el camino (navegador) y avisa de qué navegadores no lo "
      "traen OK")

assert 'casthelp.classList.toggle("show")' in body, \
    "el botón de ayuda ya no abre/cierra su ayuda al pulsarlo"
assert "castBtn.onclick" in body
print("5) el JS sigue enlazando el botón de ayuda con mostrar/ocultar la ayuda OK")

for frase in ("__onGCastApiAvailable", "cast_sender.js", "DEFAULT_MEDIA_RECEIVER_APP_ID",
             "SESSION_STATE_CHANGED", "loadMedia", "application/x-mpegURL"):
    assert frase in body, "falta «{0}» del arranque del SDK nativo de Cast".format(frase)
print("6) el arranque del SDK nativo de Chromecast (botón de un clic) sigue en la página OK")

# "async": si no hay internet (habitual en una red solo-LAN), ese script no debe bloquear ni
# retrasar el resto de la página (vídeo, sonido, pantalla completa, el botón de ayuda) -- se
# comprobó de verdad con un navegador real (Chromium/Playwright) al añadirlo
assert '<script async src="https://www.gstatic.com/cv/js/sender/v1/cast_sender.js' in body, \
    "el script del SDK de Cast debería cargarse en segundo plano (async), para no bloquear la " \
    "página si no hay internet"
print("7) el script del SDK de Cast es «async» (no bloquea la página sin internet) OK")

print("TODO OK unit_viewer_html")
