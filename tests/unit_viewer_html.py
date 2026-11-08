"""Página «En la red» (navegador): sin Tk ni navegador real -- arranca un IpServer de verdad,
pide «/» con urllib (como haría cualquier navegador) y comprueba el HTML servido de verdad (no
solo la constante en memoria), incluida la dirección real que se le pasa al botón nativo de
Chromecast (se sustituye por petición, ver _serve_page). El comportamiento real de los botones
en un navegador de verdad (Chromium vía Playwright) se comprobó a mano al añadirlos; aquí se
deja una comprobación ligera, sin esa dependencia extra ni ffmpeg, de que el HTML/CSS/JS
servidos siguen teniendo las piezas que hacen falta. El propio ffmpeg de HLS (lo que hay detrás
de /cast.m3u8) se prueba aparte, de verdad, en tests/unit_hls.py."""
import json
import os
import sys
import time
import socket
import threading
import urllib.error
import urllib.request
from collections import deque

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


def _get_root():
    with urllib.request.urlopen("http://127.0.0.1:{0}/".format(PORT), timeout=5) as resp:
        return resp.read().decode("utf-8"), resp.headers.get("Content-Type", "")


body, ctype = _get_root()

assert "text/html" in ctype, ctype
print("1) «/» sirve el HTML del visor (IpServer real, sin mocks) OK")

# la constante tiene «huecos» (__CAM_UI_LABEL__, __CAM_CAST_URL__, __PHONECAM_ADD_URL__) que
# _serve_page debe rellenar en cada petición con algo de verdad -- ni deben quedar los huecos
# literales, ni debe servirse la constante tal cual sin tocar
raw = camui._VIEWER_HTML.decode("utf-8")
for hueco in ("__CAM_UI_LABEL__", "__CAM_CAST_URL__", "__PHONECAM_ADD_URL__"):
    assert hueco in raw, "la plantilla ya no tiene el hueco «{0}» (revisar el test)".format(hueco)
    assert hueco not in body, \
        "el hueco «{0}» se quedó sin rellenar en lo que se sirve de verdad".format(hueco)
assert body != raw, "se está sirviendo la plantilla tal cual, sin rellenar los huecos"
# la misma lógica que _serve_page: una IP real de la red si se encuentra una, si no 127.0.0.1
ips = camui._local_ips()
expect_host = ips[0] if ips else "127.0.0.1"
expect_url = "http://{0}:{1}/cast.m3u8".format(expect_host, PORT)
assert expect_url in body, \
    "la URL de /cast.m3u8 servida no es la esperada ({0})".format(expect_url)
expect_label = "cam-ui@{0}".format(expect_host)
assert expect_label in body, \
    "el nombre («cam-ui@host») servido no es el esperado ({0})".format(expect_label)
# sin ningún PhoneCamHub enlazado (srv.phonecam sigue en None, ver IpServer.__init__), el hueco
# del botón «Añadir cámara del móvil» se deja vacío -- el botón sigue a la vista igualmente (ver
# el paso 8 de abajo), solo cambia lo que hace al pulsarlo
assert body == (raw.replace("__CAM_UI_LABEL__", expect_label)
                    .replace("__CAM_CAST_URL__", expect_url)
                    .replace("__PHONECAM_ADD_URL__", "")), \
    "el resto de la página cambió al rellenar los huecos (debería ser solo esos tres trozos)"
print("2) los huecos de nombre, Chromecast y «/add» se rellenan con algo de verdad ({0}, "
      "puerto {1}) OK".format(expect_label, PORT))

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

# 8) el botón «Añadir cámara del móvil» está SIEMPRE a la vista (antes se escondía del todo si
#    esa función no estaba encendida -- pero entonces, en el móvil, parecía que el botón no
#    existía); sin ningún PhoneCamHub enlazado (srv.phonecam sigue en None), el hueco de su
#    dirección se sirve vacío y el propio botón, al pulsarlo, pide por su cuenta que se encienda
#    («/addcam/start») en vez de solo decir que hay que ir antes a Ajustes a mano
assert 'id="addcam"' in body, "falta el botón «Añadir cámara del móvil»"
assert "#addcam{display:none" not in body.replace(" ", ""), \
    "el botón de añadir cámara debería estar siempre a la vista, encendida o no esa función"
assert 'addCamUrl = "";' in body, \
    "sin ningún móvil que añadir (PhoneCamHub no enlazado), el hueco debería quedar vacío"
assert "Actívala en Ajustes" not in body, \
    "ya no debería decir eso: ahora debe encenderla ella sola, sin ir a Ajustes a mano"
assert '"/addcam/start"' in body, \
    "al pulsarlo sin esa función encendida, debería pedir que se encienda sola"
# «mute»/«vol» sí siguen ocultos por una regla de la hoja de estilos hasta que se activan --
# ocultos por esa regla, no por un estilo en línea: vaciar el estilo en línea (style.display =
# "") NO la quita -- hace falta un valor de verdad (comprobado con un Chromium real: con "" "
# seguían sin verse tras "activarlos"). Llevaban así desde antes de hoy; quedó sin notar hasta
# ahora.
for nombre, patron_malo, patron_bueno in (
        ("mute", 'mute.style.display = "";', 'mute.style.display = "inline-block";'),
        ("vol", 'vol.style.display = "";', 'vol.style.display = "inline-block";')):
    assert patron_malo not in body, \
        "«{0}» seguiría oculto para siempre: vaciar el estilo en línea no basta".format(nombre)
    assert patron_bueno in body, "falta el valor de verdad que de verdad lo muestra ({0})".format(nombre)
print("8) el botón «Añadir cámara del móvil» está siempre a la vista (lo diga o no, según esa "
      "función esté encendida) -- y, junto con «mute»/«vol», con un valor de display que de "
      "verdad los muestra (no el \"\" que se quedaba sin efecto) OK")

# 9) con un PhoneCamHub de verdad enlazado y encendido (como hace CamApp.__init__ con
#    self.ipsrv.phonecam = self.phonecam), «/» rellena ese mismo hueco con la dirección real de
#    «/add», y el JS la usa para mostrar el botón -- de principio a fin, sin mocks
PHONECAM_PORT = 58403
hub = camui.PhoneCamHub(deque(), deque())
hub.start(port=PHONECAM_PORT)
try:
    srv.phonecam = hub
    body2, _ = _get_root()
finally:
    hub.stop()
    srv.phonecam = None
expect_add_url = "https://{0}:{1}/add".format(expect_host, PHONECAM_PORT)
assert expect_add_url in body2, \
    "la dirección de «/add» servida no es la esperada ({0})".format(expect_add_url)
assert 'addCamUrl = "{0}";'.format(expect_add_url) in body2, \
    "el JS no quedó con la dirección real de «/add»"
print("9) con la cámara del móvil encendida de verdad, «/» lleva al botón su dirección real de "
      "«/add» ({0}) OK".format(expect_add_url))

# 10) una dirección que no existe en este servidor (como «/add», si alguien la escribe aquí por
#     confundirla con el puerto de la cámara del móvil -- justo lo que pasó de verdad) ya no se
#     queda sin Content-Type ni cuerpo: antes, eso hacía que Safari/Chrome en el iPhone ofrecieran
#     "descargar" un archivo vacío con un nombre inventado, en vez de avisar de que esa dirección
#     no existe. Ahora da un 404 de verdad, con HTML y un enlace de vuelta.
try:
    urllib.request.urlopen("http://127.0.0.1:{0}/add".format(PORT), timeout=5)
    raise SystemExit("«/add» no existe en este servidor -- debería dar 404")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
    assert "text/html" in e.headers.get("Content-Type", ""), \
        "el 404 debería llevar Content-Type text/html (antes no llevaba ninguno)"
    err_body = e.read().decode("utf-8")
assert err_body, "el 404 debería traer algo de HTML, no un cuerpo vacío"
assert 'href="/"' in err_body, "el 404 debería enlazar de vuelta a «/»"
# el enlace dice qué PC es ("cam-ui@host"), no el genérico "En la red" de antes -- si hay más de
# un cam-ui en la wifi, eso sí distingue cuál es cuál (misma IP detectada que en los pasos de
# arriba: _send_404 usa la misma _local_ips(), no la dirección por la que llegó esta petición)
assert expect_label in err_body, \
    "el enlace del 404 debería decir «{0}», no el genérico «En la red» de antes".format(expect_label)
print("10) una dirección que no existe aquí da un 404 de verdad (HTML, con enlace a «cam-ui@"
      "<host>»), no un archivo vacío sin Content-Type OK")

# 11) «/addcam/start» (lo que pide el botón «Añadir cámara del móvil» cuando esa función está
#     apagada) sin ningún PhoneCamHub enlazado no puede encender nada -- debe dar un error claro
#     (503 con algo de JSON), no un 200 vacío ni un 404 como si la dirección no existiera
req11 = urllib.request.Request("http://127.0.0.1:{0}/addcam/start".format(PORT), data=b"",
                                method="POST")
try:
    urllib.request.urlopen(req11, timeout=5)
    raise SystemExit("«/addcam/start» sin ninguna cámara de móvil enlazada debería fallar")
except urllib.error.HTTPError as e:
    assert e.code == 503, e.code
    err11 = json.loads(e.read().decode("utf-8"))
assert err11.get("error"), "el error debería explicar algo, no venir vacío"
print("11) «/addcam/start» sin ninguna cámara de móvil enlazada da un error claro (503), no un "
      "200 vacío ni un 404 OK")

# 12) con un PhoneCamHub de verdad enlazado pero todavía apagado, y sin nadie que atienda
#     phonecam_remote_q (aquí no hay ninguna CamApp real, solo este IpServer suelto),
#     «/addcam/start» debe dejar de verdad el aviso en esa cola -- para que quien sí la
#     atienda (CamApp._tick) lo encuentre -- y, al no encenderse nadie, rendirse con el mismo
#     error claro del paso 11 tras esperar un rato de verdad, no quedarse colgado para siempre
hub2 = camui.PhoneCamHub(deque(), deque())
remote_q = deque()
srv.phonecam = hub2
srv.phonecam_remote_q = remote_q
t0 = time.time()
try:
    req12 = urllib.request.Request("http://127.0.0.1:{0}/addcam/start".format(PORT), data=b"",
                                    method="POST")
    try:
        urllib.request.urlopen(req12, timeout=8)
        raise SystemExit("«/addcam/start» debería fallar si nadie la enciende de verdad")
    except urllib.error.HTTPError as e:
        assert e.code == 503, e.code
    elapsed = time.time() - t0
finally:
    srv.phonecam = None
    srv.phonecam_remote_q = None
assert remote_q and remote_q[0] is True, \
    "«/addcam/start» debería haber dejado el aviso en phonecam_remote_q para que _tick() lo " \
    "recoja (aquí no hay ninguna CamApp real que lo haga por sí sola)"
assert 3.0 <= elapsed <= 6.0, \
    "debería esperar unos segundos de verdad antes de rendirse, no fallar al instante ni " \
    "quedarse colgado ({0:.1f}s)".format(elapsed)
print("12) «/addcam/start», si nadie la enciende a tiempo, deja de verdad el aviso en la cola "
      "para quien la atienda y se rinde con un error claro tras esperar un rato (no se queda "
      "colgado para siempre) OK")

# 13) con ese mismo mecanismo, pero esta vez con «alguien» atendiendo la cola de verdad (un hilo
#     que hace exactamente lo que hace CamApp._tick() -- encenderla en cuanto llega el aviso --
#     sin necesitar ninguna CamApp ni Tk para probarlo), «/addcam/start» sí debe acabar
#     devolviendo la dirección real de «/add», de punta a punta
PHONECAM_PORT3 = 58405
hub3 = camui.PhoneCamHub(deque(), deque())
remote_q3 = deque()
srv.phonecam = hub3
srv.phonecam_remote_q = remote_q3
stop3 = threading.Event()


def _fake_tick():
    while not stop3.is_set():
        while remote_q3:
            remote_q3.popleft()
            if not hub3.active:
                hub3.start(PHONECAM_PORT3)
        time.sleep(0.02)


th3 = threading.Thread(target=_fake_tick, daemon=True)
th3.start()
try:
    req13 = urllib.request.Request("http://127.0.0.1:{0}/addcam/start".format(PORT), data=b"",
                                    method="POST")
    with urllib.request.urlopen(req13, timeout=8) as r:
        assert r.status == 200
        body13 = json.loads(r.read().decode("utf-8"))
finally:
    stop3.set()
    th3.join(timeout=2)
    hub3.stop()
    srv.phonecam = None
    srv.phonecam_remote_q = None
expect_url3 = "https://{0}:{1}/add".format(expect_host, PHONECAM_PORT3)
assert body13.get("url") == expect_url3, (body13, expect_url3)
print("13) «/addcam/start», con alguien atendiendo esa cola de verdad (como haría CamApp._tick, "
      "aunque aquí sin ninguna CamApp ni Tk), sí la enciende y devuelve la dirección real de "
      "«/add», de punta a punta OK")

stop.set()
srv.release()
print("TODO OK unit_viewer_html")
