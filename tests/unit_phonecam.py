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
import json
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
pair_q = deque()
hub = camui.PhoneCamHub(q, pair_q)
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
# (read1() hace una sola lectura de lo que ya esté en el buffer -- a veces eso llega partido en
# más de un paquete y la primera lectura no trae todavía la cabecera entera; se repite unas
# pocas veces, acumulando, en vez de fiarlo todo a una sola lectura que puede llegar justa)
with _get(local_url) as r:
    assert "multipart/x-mixed-replace" in r.headers.get("Content-Type", "")
    data = b""
    for _ in range(20):
        data += r.read1(8192)
        if b"--camuiphone" in data and b"Content-Type: image/jpeg" in data:
            break
    assert b"--camuiphone" in data and b"Content-Type: image/jpeg" in data, data
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

# 10) «/add» (la dirección fija de emparejamiento) se sirve sin pedir ningún token -- por sí
#     sola no añade ninguna cámara, así que no hace falta -- y trae jsQR embebido (sin CDN)
with _get("https://127.0.0.1:{0}/add".format(port), https=True) as r:
    assert r.status == 200
    add_body = r.read().decode("utf-8")
assert "jsQR" in add_body and "/add/ready" in add_body and "getUserMedia" in add_body
print("10) «/add» se sirve sin ningún token (por sí sola no añade nada) y trae jsQR embebido OK")

# 11) el móvil que está en «/add», en cuanto tiene el permiso de su cámara, avisa con un POST a
#     «/add/ready» -- eso acuña una sesión nueva (todavía sin ningún fotograma) y la anota en
#     pair_q con su URL de emparejamiento, para que CamApp la muestre como QR
assert len(pair_q) == 0
req_ready = urllib.request.Request(
    "https://127.0.0.1:{0}/add/ready".format(port), data=b"", method="POST")
with _get(req_ready, https=True) as r:
    assert r.status == 200
assert len(pair_q) == 1, pair_q
pair_url = pair_q.popleft()
assert pair_url.startswith("https://") and "/cam?t=" in pair_url and "&sid=" in pair_url
pair_sid = re.search(r"[?&]sid=([^&]+)", pair_url).group(1)
print("11) «/add/ready» acuña una sesión nueva y avisa con su URL de emparejamiento (QR) OK")

# 12) esa URL exacta (la que el móvil lee del QR y a la que navega él solo) reutiliza ESE
#     mismo sid -- no una sesión nueva -- mientras que un sid que no existe (o ya caducó) cae
#     en el comportamiento de siempre: se acuña una sesión nueva, sin romper nada
# (esta caja de pruebas no siempre deja conectar a su propia IP "pública" vista desde dentro --
# no es lo que se está probando aquí, así que se habla con el mismo servidor por 127.0.0.1, con
# el mismo token y sid reales que llevaba pair_url -- ver el mismo comentario en ui_phonecam.py)
pair_url_local = "https://127.0.0.1:{0}/cam?t={1}&sid={2}".format(port, token, pair_sid)
with _get(pair_url_local, https=True) as r:
    assert r.status == 200
    body2 = r.read().decode("utf-8")
assert ('var sid = "' + pair_sid + '"') in body2, \
    "debería reutilizar el sid de /add/ready, no acuñar uno nuevo"
with _get("https://127.0.0.1:{0}/cam?t={1}&sid=no-existe".format(port, token), https=True) as r:
    assert r.status == 200
    body3 = r.read().decode("utf-8")
sid_fallback = re.search(r'var sid = "([^"]+)"', body3).group(1)
assert sid_fallback not in (pair_sid, "no-existe")
print("12) «/cam?...&sid=» reutiliza esa sesión exacta; con un sid que no existe, acuña una nueva OK")

hub.stop()
assert not hub.active

# 13) el certificado (firmado por la CA local de cam-ui, para poder usar la cámara desde HTTPS
#     -- ver el comentario junto a PHONECAM_PORT_DEFAULT) se reutiliza mientras siga cubriendo
#     la IP del equipo, y se regenera solo si esta cambia -- así el aviso de "conexión no
#     privada" del navegador no sale más veces de las necesarias, pero tampoco se queda con un
#     certificado que ya no vale
camui.PHONECAM_CERT_FILE = os.path.join(HERE, "_unit_phonecam_cert.pem")
camui.PHONECAM_KEY_FILE = os.path.join(HERE, "_unit_phonecam_key.pem")
camui.PHONECAM_CA_CERT_FILE = os.path.join(HERE, "_unit_phonecam_ca_cert.pem")
camui.PHONECAM_CA_KEY_FILE = os.path.join(HERE, "_unit_phonecam_ca_key.pem")
_phonecam_pem_files = (camui.PHONECAM_CERT_FILE, camui.PHONECAM_KEY_FILE,
                       camui.PHONECAM_CA_CERT_FILE, camui.PHONECAM_CA_KEY_FILE)
for f in _phonecam_pem_files:
    if os.path.exists(f):
        os.remove(f)
try:
    cert1, key1 = camui._phonecam_ensure_cert()
    mtime1 = os.path.getmtime(cert1)
    ca_bytes1 = open(camui.PHONECAM_CA_CERT_FILE, "rb").read()
    cert2, _ = camui._phonecam_ensure_cert()
    assert os.path.getmtime(cert2) == mtime1, "no debería regenerarlo con la misma IP"
    ips = [ip for ip in camui._local_ips() if ip]
    assert camui._phonecam_cert_covers(cert1, ips)
    assert not camui._phonecam_cert_covers(cert1, ["203.0.113.77"])
    assert camui._phonecam_cert_fresh(cert1), "un certificado recién creado debería estar fresco"
    time.sleep(1.1)                    # para que el mtime cambie de verdad si se regenera
    orig = camui._local_ips
    camui._local_ips = lambda: ["203.0.113.77"]
    try:
        cert3, _ = camui._phonecam_ensure_cert()
    finally:
        camui._local_ips = orig
    assert os.path.getmtime(cert3) != mtime1, "debería regenerarlo al cambiar de IP"
    assert camui._phonecam_cert_covers(cert3, ["203.0.113.77"])
    assert open(camui.PHONECAM_CA_CERT_FILE, "rb").read() == ca_bytes1, \
        "la CA no debería regenerarse nunca solo por un cambio de IP"
finally:
    for f in _phonecam_pem_files:
        if os.path.exists(f):
            os.remove(f)
print("13) el certificado se reutiliza si la IP sigue cubierta, y se regenera solo si cambia -- "
      "sin tocar la CA, que es la que habría que reinstalar en un iPhone/iPad si cambiara OK")

# 13b) el certificado de cada IP ya NO está autofirmado: lo firma la CA local de cam-ui (ver
#      _phonecam_ensure_ca), con las extensiones que exige Apple para confiar en él en
#      iOS13+/macOS 10.15+ (soporte.apple.com/103769) una vez esa CA se instala en el
#      dispositivo -- sin esto, por mucho que se instalara la CA, Safari lo seguiría rechazando
#      por motivos distintos (p.ej. validez demasiado larga, o sin «serverAuth» en el EKU).
from cryptography import x509
from cryptography.hazmat.primitives import hashes as _hashes
from cryptography.hazmat.primitives.asymmetric import padding as _rsa_padding
from cryptography.x509.oid import ExtendedKeyUsageOID

for f in _phonecam_pem_files:
    if os.path.exists(f):
        os.remove(f)
try:
    cert_path, key_path = camui._phonecam_ensure_cert()
    with open(cert_path, "rb") as f:
        leaf = x509.load_pem_x509_certificate(f.read())
    with open(camui.PHONECAM_CA_CERT_FILE, "rb") as f:
        ca_cert = x509.load_pem_x509_certificate(f.read())
    assert leaf.issuer == ca_cert.subject and leaf.subject != leaf.issuer, \
        "el cert de cada IP debe estar firmado por la CA, no autofirmado"
    ca_cert.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes,
                                _rsa_padding.PKCS1v15(), leaf.signature_hash_algorithm)
    ca_bc = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    leaf_bc = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert ca_bc.ca is True and leaf_bc.ca is False, \
        "la CA debe poder firmar certificados; el de cada IP, no (si no, también sería una CA)"
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in list(eku), \
        "sin «serverAuth» en el EKU, Safari lo rechaza igual aunque la CA esté instalada"
    try:
        exp, nvb = leaf.not_valid_after_utc, leaf.not_valid_before_utc           # cryptography >= 42
    except AttributeError:
        exp, nvb = leaf.not_valid_after, leaf.not_valid_before                   # versiones anteriores
    assert (exp - nvb).days <= 825, \
        "más de 825 días de validez y Safari deja de confiar en él, aunque la CA sea de fiar"
    assert leaf.public_key().key_size >= 2048 and ca_cert.public_key().key_size >= 2048
finally:
    for f in _phonecam_pem_files:
        if os.path.exists(f):
            os.remove(f)
print("13b) el certificado de cada IP está firmado de verdad por la CA local (no autofirmado), "
      "con «serverAuth» en el EKU y <= 825 días de validez -- los requisitos reales de Apple "
      "para que Safari confíe en él tras instalar esa CA OK")

# 13c) con la CA de verdad instalada como único certificado de confianza (así es como queda un
#      iPhone/iPad tras los 2 pasos de PhoneCamCertDialog), un cliente TLS de verdad completa el
#      saludo con el servidor HTTPS real de PhoneCamHub -- y, sin instalarla, lo rechaza (si
#      esto último fallara, el "arreglo" no protegería nada: cualquiera confiaría en cam-ui).
import ssl as _ssl

for f in _phonecam_pem_files:
    if os.path.exists(f):
        os.remove(f)
try:
    hub13c = camui.PhoneCamHub(deque(), deque())
    port13c, _tok = hub13c.start(58630)
    try:
        ctx_trusts_ca = _ssl.create_default_context(cafile=camui.PHONECAM_CA_CERT_FILE)
        with urllib.request.urlopen("https://127.0.0.1:{0}/add".format(port13c), timeout=5,
                                    context=ctx_trusts_ca) as r:
            assert r.status == 200
        ctx_no_ca = _ssl.create_default_context()
        try:
            urllib.request.urlopen("https://127.0.0.1:{0}/add".format(port13c), timeout=5,
                                   context=ctx_no_ca)
            raise AssertionError("sin confiar en la CA, el saludo TLS no debería completarse")
        except urllib.error.URLError:
            pass
    finally:
        hub13c.stop()
finally:
    for f in _phonecam_pem_files:
        if os.path.exists(f):
            os.remove(f)
print("13c) un cliente TLS de verdad que solo confía en la CA local completa el saludo con el "
      "servidor HTTPS real; uno que no confía en ella, no -- la cadena de confianza funciona de "
      "verdad, no solo sobre el papel OK")

# 13d) la CA se sirve de verdad por el tercer servidor de PhoneCamHub, en HTTP normal (sin
#      cifrar a propósito -- ver el comentario junto a _PhoneCamCertHandler) y visible en la
#      wifi (no solo en 127.0.0.1, a diferencia del re-publicado MJPEG): con el tipo correcto
#      (application/x-x509-ca-cert) para que Safari, al descargarlo, ofrezca "Instalar perfil"
#      en vez de un archivo suelto cualquiera.
for f in _phonecam_pem_files:
    if os.path.exists(f):
        os.remove(f)
try:
    hub13d = camui.PhoneCamHub(deque(), deque())
    hub13d.start(58631)
    try:
        assert hub13d.cert_port and hub13d.cert_port != hub13d.local_port
        with urllib.request.urlopen(
                "http://127.0.0.1:{0}/".format(hub13d.cert_port), timeout=5) as r:
            landing = r.read().decode("utf-8")
            assert r.status == 200 and "text/html" in r.headers.get("Content-Type", "")
        assert "iPhone/iPad" in landing and "/certificado" in landing
        with urllib.request.urlopen(
                "http://127.0.0.1:{0}/certificado".format(hub13d.cert_port), timeout=5) as r:
            der = r.read()
            assert r.headers.get("Content-Type") == "application/x-x509-ca-cert"
        served = x509.load_der_x509_certificate(der)
        with open(camui.PHONECAM_CA_CERT_FILE, "rb") as f:
            on_disk = x509.load_pem_x509_certificate(f.read())
        assert served.fingerprint(_hashes.SHA256()) == on_disk.fingerprint(_hashes.SHA256()), \
            "lo que se descarga debe ser exactamente la misma CA que firma los certificados"
    finally:
        hub13d.stop()
finally:
    for f in _phonecam_pem_files:
        if os.path.exists(f):
            os.remove(f)
print("13d) la CA se sirve de verdad por HTTP normal (sin cifrar, visible en la wifi, puerto "
      "aparte del de 127.0.0.1), con el tipo exacto que hace que Safari ofrezca instalarla como "
      "perfil, y es byte a byte la misma que firma los certificados OK")

# 14-16) Si falta el paquete «cryptography» (hace falta para el certificado HTTPS, ver
#        _phonecam_ensure_cert más arriba), antes se veía solo el mensaje interno de Python ("No
#        module named 'cryptography'") y sin ninguna salida -- caso real, reportado por un
#        usuario con cam-ui actualizado solo (la auto-actualización solo cambia camui.py, nunca
#        los paquetes de Python, así que un paquete añadido después de instalar cam-ui por
#        primera vez nunca llega a instalarse solo). Ahora, igual que con pystray/pyvirtualcam
#        (ver _tray_boot/_vcam_offer_install), se ofrece instalarlo con un clic.
class _FakePhonecam:
    def __init__(self, fail_times=1):
        self.active = False
        self.port = None
        self._fail_times = fail_times
        self._calls = 0

    def start(self, port):
        self._calls += 1
        if self._calls <= self._fail_times:
            raise ModuleNotFoundError("No module named 'cryptography'", name="cryptography")
        self.active = True
        self.port = port
        return self.port, "tok"


class _FakeCamApp(camui.CamApp):            # solo para heredar los métodos reales; sin __init__
    def __init__(self, fail_times=1):
        self.phonecam = _FakePhonecam(fail_times)
        self.phonecam_on = False
        self._tray_q = deque()
        self.calls = {"status": [], "later_status": [], "askyesno": [], "showwarning": [],
                      "changed": []}

    def _phonecam_port(self):
        return 58620

    def _set_status(self, txt):
        self.calls["status"].append(txt)

    def _later_status(self, txt):
        self.calls["later_status"].append(txt)       # sin root.after real: se anota sin más

    def _refresh_buttons(self):
        pass

    def _phonecam_changed(self, quiet):
        self.calls["changed"].append(quiet)


class _FakeMsgBoxPkg:
    def __init__(self, calls, answer):
        self._calls, self._answer = calls, answer

    def askyesno(self, title, msg):
        self._calls["askyesno"].append((title, msg))
        return self._answer

    def showwarning(self, title, msg):
        self._calls["showwarning"].append((title, msg))


def _patch_phonecam_pkg(answer, pip_ok=True, pip_msg=""):
    orig_mb, orig_pip = camui.messagebox, camui.pip_install

    def fake_pip_install(pkg, timeout=240):
        return pip_ok, pip_msg

    def apply(app):
        camui.messagebox = _FakeMsgBoxPkg(app.calls, answer)
        camui.pip_install = fake_pip_install

    def restore():
        camui.messagebox, camui.pip_install = orig_mb, orig_pip
    return apply, restore


# 14) con quiet=True (p.ej. al arrancar, o encendida en remoto por el propio móvil) no debe
#     preguntar nada -- solo deja un aviso en el estado que, por fin, dice de verdad qué paquete
#     falta (antes era el mensaje genérico "No se pudo activar la cámara del móvil").
app14 = _FakeCamApp(fail_times=99)
apply14, undo14 = _patch_phonecam_pkg(answer=True)
apply14(app14)
try:
    ok = camui.CamApp._phonecam_enable(app14, quiet=True)
finally:
    undo14()
assert ok is False
assert app14.calls["askyesno"] == [], "con quiet=True no debía preguntar nada"
assert any("cryptography" in t for t in app14.calls["later_status"]), \
    "el aviso debería decir qué paquete falta de verdad: {0}".format(app14.calls["later_status"])
print("14) sin «cryptography», con quiet=True no pregunta nada -- solo avisa en el estado, y "
      "dice el paquete real que falta (no el mensaje genérico de siempre) OK")

# 15) sin quiet: se ofrece instalarlo; si se dice que no, no se instala nada ni queda encendida
app15 = _FakeCamApp(fail_times=99)
apply15, undo15 = _patch_phonecam_pkg(answer=False)
apply15(app15)
try:
    ok = camui.CamApp._phonecam_enable(app15, quiet=False)
finally:
    undo15()
assert ok is False
assert len(app15.calls["askyesno"]) == 1
assert "cryptography" in app15.calls["askyesno"][0][1]
assert app15.phonecam_on is False and not app15.phonecam.active
print("15) sin «cryptography», sin quiet, ofrece instalarlo -- y si se dice que no, no fuerza "
      "nada ni queda encendida OK")

# 16) si se acepta instalarlo y la instalación sale bien, cam-ui reintenta sola la activación --
#     de punta a punta, con un hilo de verdad (igual que pip_install en la vida real) -- y esta
#     vez (el paquete ya puesto) sí que enciende de verdad, sin tener que volver a pulsar nada.
app16 = _FakeCamApp(fail_times=1)           # falla la primera vez (como ahora); la segunda, ya no
apply16, undo16 = _patch_phonecam_pkg(answer=True, pip_ok=True)
apply16(app16)
try:
    ok = camui.CamApp._phonecam_enable(app16, quiet=False)
    assert ok is False, "la primera vez (sin el paquete) debe fallar todavía"
    assert len(app16.calls["askyesno"]) == 1
    for _ in range(100):                    # el hilo de instalar+reintentar corre de verdad aparte
        if app16._tray_q:
            camui.CamApp._tray_drain(app16)
        if app16.phonecam.active:
            break
        time.sleep(0.05)
finally:
    undo16()
assert app16.phonecam.active, "tras instalar bien el paquete, debería reintentar sola y encender"
assert app16.phonecam_on is True
assert app16.calls["changed"] == [False], "la activación de verdad debe avisar del cambio (no quiet)"
print("16) tras aceptar instalarlo y salir bien, cam-ui reintenta sola la activación -- sin "
      "tener que volver a pulsar nada -- y esta vez enciende de verdad OK")

# 17) más fluido y con mejor resolución (pedido explícito del usuario): no basta con subir la
#     constante si no llega hasta la etiqueta <video> real que usa el móvil para grabar.
assert camui.PHONECAM_FPS == 20 and camui.PHONECAM_JPEG_QUALITY == 0.82
assert camui.PHONECAM_JPEG_MAX == 6 * 1024 * 1024
assert b"width: {ideal: 1920}" in camui._PHONECAM_HTML
assert b"height: {ideal: 1080}" in camui._PHONECAM_HTML
assert b"frameRate: {ideal: 20}" in camui._PHONECAM_HTML
assert b"audio: false" in camui._PHONECAM_HTML
print("17) más fluido y con mejor resolución: 1920x1080 a 20fps llega de verdad hasta la página "
      "que carga el móvil, no solo a la constante OK")

# 18) los móviles ya emparejados antes se guardan de verdad en disco (PHONECAM_DEVICES_FILE,
#     aparte de config.json a propósito -- ver el comentario junto a la constante), y
#     sobreviven a "reiniciar" PhoneCamHub (una instancia nueva, como tras apagar/encender la
#     función o cerrar cam-ui del todo) -- "olvidar" uno lo borra también de verdad.
camui.PHONECAM_DEVICES_FILE = os.path.join(HERE, "_unit_phonecam_devices.json")
if os.path.exists(camui.PHONECAM_DEVICES_FILE):
    os.remove(camui.PHONECAM_DEVICES_FILE)
try:
    hub18a = camui.PhoneCamHub(deque(), deque())
    assert hub18a.known_device("no-existe") is None
    hub18a.add_device("secreto-1", "iPhone de Ana")
    assert hub18a.known_device("secreto-1")["name"] == "iPhone de Ana"
    hub18b = camui.PhoneCamHub(deque(), deque())        # otra instancia -- "tras reiniciar"
    info = hub18b.known_device("secreto-1")
    assert info is not None and info["name"] == "iPhone de Ana", \
        "debe sobrevivir a una instancia nueva de PhoneCamHub, no solo quedarse en memoria"
    names = [d.get("name") for _secret, d in hub18b.list_devices()]
    assert "iPhone de Ana" in names
    assert hub18b.forget_device("secreto-1") is True
    assert hub18b.known_device("secreto-1") is None
    assert hub18b.forget_device("secreto-1") is False, \
        "ya no está: la segunda vez no hay nada que olvidar"
finally:
    if os.path.exists(camui.PHONECAM_DEVICES_FILE):
        os.remove(camui.PHONECAM_DEVICES_FILE)
print("18) los móviles ya emparejados se guardan de verdad en disco, sobreviven a una "
      "«PhoneCamHub» nueva (como tras reiniciar) y «olvidar» uno los borra de verdad, no solo "
      "en memoria OK")

# 19) de punta a punta por HTTP real: «/cam» sin «dev» (primera vez, recién leído el QR) acuña un
#     secreto nuevo, lo persiste YA MISMO y lo manda dentro de la propia página; con ese secreto,
#     «/add/reconnect» (lo que hace el script de «/add» cuando ya lo tiene guardado) responde con
#     una sesión nueva sin pedir QR ni nada; con un secreto que no existe, cae a {"ok": false};
#     y tras "olvidarlo" (como desde PhoneCamDevicesDialog), ese mismo secreto deja de servir --
#     se nota en la siguiente petición, sin ninguna caché de por medio.
camui.PHONECAM_DEVICES_FILE = os.path.join(HERE, "_unit_phonecam_devices2.json")
if os.path.exists(camui.PHONECAM_DEVICES_FILE):
    os.remove(camui.PHONECAM_DEVICES_FILE)
for f in _phonecam_pem_files:
    if os.path.exists(f):
        os.remove(f)
try:
    hub19 = camui.PhoneCamHub(deque(), deque())
    port19, tok19 = hub19.start(58632)
    try:
        with _get("https://127.0.0.1:{0}/cam?t={1}".format(port19, tok19), https=True) as r:
            assert r.status == 200
            body19 = r.read().decode("utf-8")
        m = re.search(r'var dev = "([^"]+)";', body19)
        assert m, "la página debería traer ya el secreto persistente del móvil, sustituido de verdad"
        dev19 = m.group(1)
        assert hub19.known_device(dev19) is not None, \
            "debería quedar guardado en el momento mismo de servir «/cam», no más tarde"

        def _post_json(url):
            req = urllib.request.Request(url, data=b"", method="POST")
            with _get(req, https=True) as r:
                return json.loads(r.read().decode("utf-8"))

        resp19 = _post_json(
            "https://127.0.0.1:{0}/add/reconnect?dev={1}".format(port19, dev19))
        assert resp19["ok"] is True and resp19["url"].startswith("/cam?")
        assert "dev={0}".format(dev19) in resp19["url"]

        resp19b = _post_json(
            "https://127.0.0.1:{0}/add/reconnect?dev=lo-que-sea".format(port19))
        assert resp19b == {"ok": False}

        # el mismo móvil, volviendo a «/cam» con su propio secreto -- no debería acuñar uno
        # nuevo ni perder el que ya tenía
        with _get("https://127.0.0.1:{0}/cam?t={1}&dev={2}".format(port19, tok19, dev19),
                  https=True) as r:
            body19c = r.read().decode("utf-8")
        assert ('var dev = "' + dev19 + '"') in body19c, \
            "con un secreto ya conocido, debería reutilizarlo tal cual, no acuñar otro"

        hub19.forget_device(dev19)
        resp19d = _post_json(
            "https://127.0.0.1:{0}/add/reconnect?dev={1}".format(port19, dev19))
        assert resp19d == {"ok": False}, \
            "tras «olvidar» el móvil, debería caer al QR de siempre, no reconocerlo más"
    finally:
        hub19.stop()
finally:
    for f in _phonecam_pem_files:
        if os.path.exists(f):
            os.remove(f)
    if os.path.exists(camui.PHONECAM_DEVICES_FILE):
        os.remove(camui.PHONECAM_DEVICES_FILE)
print("19) de punta a punta por HTTP real: «/cam» acuña y guarda el secreto del móvil dentro de "
      "la propia página, «/add/reconnect» lo reconoce y manda una sesión nueva sin QR, un "
      "secreto que no existe cae a {\"ok\": false}, uno ya conocido no cambia solo, y tras "
      "«olvidarlo» deja de servir al instante OK")

# 20) la lista exhaustiva de direcciones de «/add» (pedido explícito del usuario: "una lista mas
#     exhaustiva de hostnames / ips") -- una por cada IP real de este equipo, más el nombre del
#     equipo al final, igual que _ip_url_list() ya hace para "En la red"; y _phonecam_add_url()
#     (la dirección única que de verdad se manda en «/addcam/start», ver unit_viewer_html.py)
#     sigue siendo, tal cual, la primera de esa misma lista -- la mejor suposición.
app20 = _FakeCamApp()
app20.phonecam.port = 58699
ips20 = camui._local_ips()
urls20 = camui.CamApp._phonecam_add_url_list(app20)
assert len(urls20) == (len(ips20) if ips20 else 1) + 1, (urls20, ips20)
assert all(u.startswith("https://") and u.endswith(":58699/add") for u in urls20), urls20
assert urls20[-1] == "https://{0}:58699/add".format(camui.socket.gethostname()), urls20
assert urls20[0] == camui.CamApp._phonecam_add_url(app20), \
    (urls20[0], camui.CamApp._phonecam_add_url(app20))
print("20) la lista de direcciones de «/add» es exhaustiva (una por IP real + el nombre del "
      "equipo), y la primera coincide con _phonecam_add_url() (la que de verdad se usa en "
      "«/addcam/start») OK")

print("TODO OK unit_phonecam")
