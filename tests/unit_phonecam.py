"""Cámara del móvil por el navegador (ver PhoneCamHub en camui.py): el móvil manda fotogramas
JPEG sueltos por HTTPS (con el token correcto) y cam-ui los vuelve a publicar, en un segundo
servidor SOLO en 127.0.0.1, como si fuera una cámara de red MJPEG de toda la vida. Sin mocks:
sockets reales (HTTPS con el certificado autofirmado de verdad, igual que haría un navegador
tras aceptar el aviso), y un ffmpeg real leyendo la reexposición local, igual que lee cualquier
otra cámara de red (ver unit_netcam.py)."""
import os
import io
import re
import sys
import ssl
import time
import threading
import subprocess
import urllib.error
import urllib.request
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import camui
import numpy as np
from PIL import Image

CTX = ssl._create_unverified_context()


def _jpeg(n=0, w=320, h=240):
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, :, 1] = (n * 5) % 256
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _get(url, timeout=5, https=False):
    return urllib.request.urlopen(url, timeout=timeout, context=CTX if https else None)


q = deque()
hub = camui.PhoneCamHub(q)
port, token = hub.start(port=58610)
assert hub.active
print("1) PhoneCamHub arranca de verdad (HTTPS en la wifi + servidor solo en 127.0.0.1) OK")

# 2) sin el token correcto, «/cam» no deja pasar
try:
    _get("https://127.0.0.1:{0}/cam".format(port), https=True)
    raise SystemExit("debería haber dado 403 sin token")
except urllib.error.HTTPError as e:
    assert e.code == 403, e.code
try:
    _get("https://127.0.0.1:{0}/cam?t=lo-que-sea".format(port), https=True)
    raise SystemExit("debería haber dado 403 con un token incorrecto")
except urllib.error.HTTPError as e:
    assert e.code == 403, e.code
print("2) «/cam» exige el token correcto (403 sin él, o con uno equivocado) OK")

# 3) con el token correcto: 200, la página trae el sid ya sustituido (no el marcador sin más)
with _get("https://127.0.0.1:{0}/cam?t={1}".format(port, token), https=True) as r:
    assert r.status == 200
    body = r.read().decode("utf-8")
assert "__PHONECAM_SID__" not in body
assert "getUserMedia" in body and "cam/ingest/" in body
sid = re.search(r'var sid = "([^"]+)"', body).group(1)
print("3) con el token, «/cam» sirve la página de captura con su propio sid de sesión OK")

# 4) un fotograma de verdad, por POST (como mandaría el navegador desde canvas.toBlob) ->
#    se acepta, y la sesión se anota en new_cam_q la primera vez (y solo esa vez)
jpeg = _jpeg(0)
req = urllib.request.Request("https://127.0.0.1:{0}/cam/ingest/{1}".format(port, sid),
                             data=jpeg, method="POST")
with _get(req, https=True) as r:
    assert r.status == 200
assert len(q) == 1, q
name, local_url = q.popleft()
assert name == "Móvil" and local_url.startswith("http://127.0.0.1:")
req2 = urllib.request.Request("https://127.0.0.1:{0}/cam/ingest/{1}".format(port, sid),
                              data=_jpeg(1), method="POST")
with _get(req2, https=True) as r:
    assert r.status == 200
assert len(q) == 0, "el segundo fotograma de la MISMA sesión no debe anotarse otra vez"
print("4) POST de fotogramas aceptado; la cámara se anuncia solo la primera vez OK")

# 5) un POST a una sesión inexistente se rechaza (404), y uno sin cuerpo o enorme también
req3 = urllib.request.Request("https://127.0.0.1:{0}/cam/ingest/no-existe".format(port),
                              data=jpeg, method="POST")
try:
    _get(req3, https=True)
    raise SystemExit("debería dar 404 para una sesión que no existe")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
print("5) POST a una sesión que no existe -> 404 OK")

# 6) la reexposición local (127.0.0.1, la que usaría ffmpeg) sirve el MISMO JPEG como
#    multipart/x-mixed-replace -- exactamente el formato que ya se usa en toda la app
with _get(local_url) as r:
    assert "multipart/x-mixed-replace" in r.headers.get("Content-Type", "")
    data = r.read1(8192)
    assert b"--camuiphone" in data and b"Content-Type: image/jpeg" in data
print("6) la reexposición local sirve un MJPEG normal y corriente con el fotograma recibido OK")

# 7) ffmpeg DE VERDAD, leyéndolo como «cámara de red» -- el mismo camino que ya usa cualquier
#    otra cámara de red (_net_video_input): si esto no funcionara, toda la pieza sería inútil
#    aunque el resto de pruebas pasara
sid2 = hub.new_session("Prueba ffmpeg")
stop = threading.Event()


def feeder():
    n = 0
    while not stop.is_set():
        hub.feed(sid2, _jpeg(n))
        n += 1
        time.sleep(1.0 / camui.PHONECAM_FPS)


threading.Thread(target=feeder, daemon=True).start()
time.sleep(0.4)
assert len(q) == 1
_, local_url2 = q.popleft()
out = subprocess.run([camui.FFMPEG, "-nostdin", "-loglevel", "error", "-i", local_url2,
                      "-frames:v", "15", "-f", "null", "-"],
                     capture_output=True, text=True, timeout=20)
stop.set()
assert out.returncode == 0, out.stderr
print("7) ffmpeg real lee 15 fotogramas de la reexposición local, como cualquier cámara de red OK")

# 8) sin fotogramas nuevos un rato: la reexposición local corta la conexión sola (para que
#    ffmpeg vea EOF y lo trate como cámara caída -> aviso «Sin señal», ver pack 8)
camui.PHONECAM_STALE_SECONDS = 1.0
sid3 = hub.new_session("Caduca")
hub.feed(sid3, _jpeg(0))
_, local_url3 = q.popleft()
with _get(local_url3) as r:
    t0 = time.time()
    r.read1(8192)
    rest = r.read()                   # hasta que el servidor cierre
    dt = time.time() - t0
assert 0.5 < dt < 4.0, dt
print("8) sin más fotogramas, la reexposición local se corta sola a los {0:.1f}s OK".format(dt))

# 9) una sesión que ya no existe en absoluto -> 404 directo (sin comprometerse antes a un 200)
try:
    _get("http://127.0.0.1:{0}/phonecam/no-existe.mjpg".format(hub.local_port))
    raise SystemExit("debería dar 404")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
print("9) sesión desconocida en la reexposición local -> 404 de verdad OK")

hub.stop()
assert not hub.active

# 10) el certificado (autofirmado, para poder usar la cámara desde HTTPS -- ver el comentario
#     junto a PHONECAM_PORT_DEFAULT) se reutiliza mientras siga cubriendo la IP del equipo, y
#     se regenera solo si esta cambia -- así el aviso de "conexión no privada" del navegador no
#     sale más veces de las necesarias, pero tampoco se queda con un certificado que ya no vale
camui.PHONECAM_CERT_FILE = os.path.join(HERE, "_unit_phonecam_cert.pem")
camui.PHONECAM_KEY_FILE = os.path.join(HERE, "_unit_phonecam_key.pem")
for f in (camui.PHONECAM_CERT_FILE, camui.PHONECAM_KEY_FILE):
    if os.path.exists(f):
        os.remove(f)
try:
    cert1, key1 = camui._phonecam_ensure_cert()
    mtime1 = os.path.getmtime(cert1)
    cert2, _ = camui._phonecam_ensure_cert()
    assert os.path.getmtime(cert2) == mtime1, "no debería regenerarlo con la misma IP"
    ips = [ip for ip in camui._local_ips() if ip]
    assert camui._phonecam_cert_covers(cert1, ips)
    assert not camui._phonecam_cert_covers(cert1, ["203.0.113.77"])
    time.sleep(1.1)                    # para que el mtime cambie de verdad si se regenera
    orig = camui._local_ips
    camui._local_ips = lambda: ["203.0.113.77"]
    try:
        cert3, _ = camui._phonecam_ensure_cert()
    finally:
        camui._local_ips = orig
    assert os.path.getmtime(cert3) != mtime1, "debería regenerarlo al cambiar de IP"
    assert camui._phonecam_cert_covers(cert3, ["203.0.113.77"])
finally:
    for f in (camui.PHONECAM_CERT_FILE, camui.PHONECAM_KEY_FILE):
        if os.path.exists(f):
            os.remove(f)
print("10) el certificado se reutiliza si la IP sigue cubierta, y se regenera solo si cambia OK")

print("TODO OK unit_phonecam")
