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
                "http://127.0.0.1:{0}/certificado.cer".format(hub13d.cert_port), timeout=5) as r:
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

# 13e) caso real (iPad, 15-11-2026): quien ya usaba «Cámara del móvil» antes del pack 24 tenía un
#      certificado AUTOFIRMADO que seguía cubriendo su IP y no caducaba, así que se reutilizaba
#      tal cual: la CA nunca llegaba a crearse y «Instalar certificado» descargaba un archivo
#      vacío («certificado.txt», 0 bytes). Ahora solo se reutiliza si lo firmó la CA actual.
import datetime as _dt
import ipaddress as _ipa
from cryptography.hazmat.primitives import serialization as _ser
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
from cryptography.x509.oid import NameOID as _NameOID

for f in _phonecam_pem_files:
    if os.path.exists(f):
        os.remove(f)
try:
    _k = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _n = x509.Name([x509.NameAttribute(_NameOID.COMMON_NAME, "cam-ui (red local)")])
    _now = _dt.datetime.now(_dt.timezone.utc)
    _alt = [x509.DNSName("localhost"), x509.IPAddress(_ipa.ip_address("127.0.0.1"))] + [
        x509.IPAddress(_ipa.ip_address(ip)) for ip in camui._local_ips() if ip]
    _old = (x509.CertificateBuilder().subject_name(_n).issuer_name(_n).public_key(_k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now - _dt.timedelta(days=1)).not_valid_after(_now + _dt.timedelta(days=700))
            .add_extension(x509.SubjectAlternativeName(_alt), critical=False)
            .sign(_k, _hashes.SHA256()))
    with open(camui.PHONECAM_KEY_FILE, "wb") as f:
        f.write(_k.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.TraditionalOpenSSL,
                                 _ser.NoEncryption()))
    with open(camui.PHONECAM_CERT_FILE, "wb") as f:
        f.write(_old.public_bytes(_ser.Encoding.PEM))
    assert not os.path.exists(camui.PHONECAM_CA_CERT_FILE)
    hub13e = camui.PhoneCamHub(deque(), deque())
    hub13e.start(58632)
    try:
        assert os.path.exists(camui.PHONECAM_CA_CERT_FILE), "la CA tiene que existir al arrancar"
        with open(camui.PHONECAM_CERT_FILE, "rb") as f:
            leaf13e = x509.load_pem_x509_certificate(f.read())
        with open(camui.PHONECAM_CA_CERT_FILE, "rb") as f:
            ca13e = x509.load_pem_x509_certificate(f.read())
        assert leaf13e.issuer == ca13e.subject and leaf13e.issuer != leaf13e.subject, \
            "el certificado autofirmado de antes debe rehacerse firmado por la CA"
        with urllib.request.urlopen("http://127.0.0.1:{0}/certificado.cer".format(hub13e.cert_port),
                                    timeout=5) as r:
            der13e = r.read()
            disp13e = r.headers.get("Content-Disposition", "")
        assert len(der13e) > 300 and x509.load_der_x509_certificate(der13e) == ca13e, len(der13e)
        assert "attachment" in disp13e and disp13e.endswith(".cer"), disp13e
        # 13f) el QR lo abre el navegador predeterminado: en iPhone/iPad, si no es Safari, la
        #      página lo dice (y qué escribir en Safari) -- en Safari o Android, no
        ua = {"chrome_ios": "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1",
              "safari_ios": "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
              "chrome_android": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"}
        pages = {}
        for k_, v_ in ua.items():
            req = urllib.request.Request("http://127.0.0.1:{0}/".format(hub13e.cert_port),
                                         headers={"User-Agent": v_})
            with urllib.request.urlopen(req, timeout=5) as r:
                pages[k_] = r.read().decode("utf-8")
        want = "http://127.0.0.1:{0}/".format(hub13e.cert_port)
        assert "Esto no es Safari" in pages["chrome_ios"] and want in pages["chrome_ios"]
        assert "Esto no es Safari" not in pages["safari_ios"] and "%%NOSAFARI%%" not in pages["safari_ios"]
        assert "Esto no es Safari" not in pages["chrome_android"]
    finally:
        hub13e.stop()
finally:
    for f in _phonecam_pem_files:
        if os.path.exists(f):
            os.remove(f)
print("13e) un certificado autofirmado de antes del pack 24 se rehace firmado por la CA, y "
      "«/certificado» sirve esa CA (no un archivo vacío) como .cer OK")
print("13f) en iPhone/iPad con Chrome u otro navegador que no es Safari, la página avisa y dice "
      "qué dirección escribir en Safari; en Safari y en Android, no OK")

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
# (pack 39) el nombre, como «nombre.local»: el que entienden iPhone/iPad sin más
assert urls20[-1] == "https://{0}:58699/add".format(camui._host_local()), urls20
assert urls20[0] == camui.CamApp._phonecam_add_url(app20), \
    (urls20[0], camui.CamApp._phonecam_add_url(app20))
print("20) la lista de direcciones de «/add» es exhaustiva (una por IP real + el nombre del "
      "equipo), y la primera coincide con _phonecam_add_url() (la que de verdad se usa en "
      "«/addcam/start») OK")

# 21) diagnóstico (pack 36): cada PHONECAM_STATS_SECONDS, camui.log dice cómo llega cada móvil
#     -- fps y mayor hueco, resolución, KB y Mbit/s, y lo que el móvil dice que tardó en
#     codificar y en enviar (cabecera X-Camui-Stats que pone su página) -- y, una vez, desde qué
#     navegador; y la página de verdad manda esa cabecera
import logging  # noqa: E402


class _Cap(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, rec):
        self.lines.append(rec.getMessage())


cap = _Cap()
camui.log.addHandler(cap)
camui.log.setLevel(logging.DEBUG)
_old_st = camui.PHONECAM_STATS_SECONDS
camui.PHONECAM_STATS_SECONDS = 0.6
try:
    hub21 = camui.PhoneCamHub(deque(), deque())
    p21, t21 = hub21.start(58690)
    try:
        with _get("https://127.0.0.1:{0}/cam?t={1}".format(p21, t21), https=True) as r:
            page21 = r.read().decode("utf-8")
        sid21 = re.search(r'var sid = "([^"]+)"', page21).group(1)
        assert '"X-Camui-Stats": lastStats' in page21 and "enc=" in page21 and ";post=" in page21
        jp21 = _jpeg(0, 640, 360)
        t_end = time.time() + 1.5
        while time.time() < t_end:
            req21 = urllib.request.Request(
                "https://127.0.0.1:{0}/cam/ingest/{1}".format(p21, sid21), data=jp21, method="POST",
                headers={"X-Camui-Stats": "enc=31;post=87;w=1920;h=1080",
                         "User-Agent": "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) Safari/604.1"})
            with _get(req21, https=True) as r:
                assert r.status == 200
            time.sleep(0.05)
    finally:
        hub21.stop()
finally:
    camui.PHONECAM_STATS_SECONDS = _old_st
    camui.log.removeHandler(cap)
    for f in _phonecam_pem_files + (camui.PHONECAM_DEVICES_FILE,):
        if os.path.exists(f):
            os.remove(f)
got21 = [x for x in cap.lines if x.startswith("cámara del móvil «Móvil»")]
assert any("conectado desde «Mozilla/5.0 (iPad" in x for x in got21), got21
st21 = [x for x in got21 if "fps recibidos" in x]
assert st21, got21
m21 = re.search(r"([\d.]+) fps recibidos \(hueco máx\. (\d+) ms\), 1920x1080, (\d+) KB por fotograma "
                r"\(([\d.]+) Mbit/s\); en el móvil, de media: codificar 31 ms, enviar 87 ms", st21[0])
assert m21 and 3 < float(m21.group(1)) < 25 and int(m21.group(3)) == len(jp21) // 1024, st21[0]
print("21) camui.log anota cada pocos segundos cómo llega el móvil: «{0}» OK".format(
    st21[0].split(": ", 1)[1]))

# 22) (pack 37) caso real: un iPad mandaba ~3 fps con 220-400 ms por envío, tardara lo mismo
#     uno de 200 KB que uno de 500 -- cada fotograma abría una conexión nueva (HTTP/1.0: TCP +
#     saludo TLS + arranque lento, varias idas y vueltas por la wifi) y esperaba al anterior.
#     Ahora la conexión se reutiliza (HTTP/1.1, keep-alive), van hasta 2 a la vez, numerados, y
#     uno viejo que llegue tarde no tapa a uno nuevo
import http.client  # noqa: E402

hub22 = camui.PhoneCamHub(deque(), deque())
p22, t22 = hub22.start(58692)
opened22 = [0]
_orig_setup22 = camui._PhoneCamHandler.setup


def _count_setup22(self):
    opened22[0] += 1
    return _orig_setup22(self)


camui._PhoneCamHandler.setup = _count_setup22
try:
    c22 = http.client.HTTPSConnection("127.0.0.1", p22, context=CTX, timeout=5)
    c22.request("GET", "/cam?t=" + t22)
    r22 = c22.getresponse()
    page22 = r22.read().decode("utf-8")
    assert r22.version == 11 and not r22.will_close, (r22.version, r22.will_close)
    assert '"__PHONECAM_PCNAME__"' not in page22 and 'var pcName = "cam-ui' in page22
    assert "var MAX_INFLIGHT = 2;" in page22 and '"X-Camui-Seq": String(seq)' in page22
    sid22 = re.search(r'var sid = "([^"]+)"', page22).group(1)
    # un POST con cuerpo a /add/ready (que no lo usa) no debe estropear la conexión
    c22.request("POST", "/add/ready", body=b"x" * 100)
    r22 = c22.getresponse()
    r22.read()
    assert r22.status == 200 and not r22.will_close
    for k22, seq22 in enumerate((1, 2, 4, 3)):          # el 3 llega después del 4
        c22.request("POST", "/cam/ingest/" + sid22, body=_jpeg(seq22),
                    headers={"X-Camui-Seq": str(seq22)})
        r22 = c22.getresponse()
        r22.read()
        assert r22.status == 200 and not r22.will_close, (k22, r22.status, r22.will_close)
    c22.close()
    assert opened22[0] == 1, "todo por una sola conexión: {0}".format(opened22[0])
    jp22, gen22, _ts = hub22.latest(sid22)
    assert jp22 == _jpeg(4) and gen22 == 3, gen22       # el 3, tarde: descartado
finally:
    camui._PhoneCamHandler.setup = _orig_setup22
    hub22.stop()
print("22) la página y 5 peticiones (con cuerpo) por UNA sola conexión que se reutiliza; uno "
      "viejo que llega tarde no tapa al nuevo OK")

# 23) (pack 37) la prueba previa del motor de siempre (y el «Diagnóstico») medía «-t 1.5» en
#     fotogramas a 25 fps supuestos: un móvil a 3 fps tardaba 12 s, «tiempo agotado» y «Sin
#     señal» aunque la imagen sí llegaba. Ahora cuenta segundos de verdad
hub23 = camui.PhoneCamHub(deque(), deque())
hub23.start(58694)
try:
    sid23 = hub23.new_session("Lento")
    stop23 = threading.Event()

    def feed23():
        k = 0
        while not stop23.is_set():
            hub23.feed(sid23, _jpeg(k, 1080, 1920))
            k += 1
            time.sleep(1 / 3.0)
    threading.Thread(target=feed23, daemon=True).start()
    time.sleep(0.8)
    spec23 = "http://127.0.0.1:{0}/phonecam/{1}.mjpg".format(hub23.local_port, sid23)
    t23 = time.time()
    res23 = camui._preflight_probe([("m", "video", spec23, None)], "ffmpeg")["m"]
    el23 = time.time() - t23
    stop23.set()
finally:
    hub23.stop()
assert res23.get("frames", 0) >= 2 and el23 < 4.0, (res23, el23)
assert 1.5 <= res23["fps"] <= 5, res23["fps"]
print("23) un móvil a 3 fps pasa la prueba previa en {0:.1f} s ({1} fotogramas, {2:.1f} fps "
      "medidos) OK".format(el23, res23["frames"], res23["fps"]))
# 24) (pack 37) el nombre por defecto dice qué es: «iPad» (también el que Safari presenta como
#     un Mac), «iPhone», «Android · <modelo>» -- lo calcula la propia página (/add) o, si no, el
#     User-Agent --; uno ya emparejado como «Móvil #3» pasa a llamarse así al reconectar (uno
#     renombrado a mano, no), y se anota su última conexión
assert camui._phonecam_device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X)") == "iPhone"
assert camui._phonecam_device_label("Mozilla/5.0 (Linux; Android 14; SM-A546B) Chrome/121") == "Android · SM-A546B"
assert camui._phonecam_device_label("Mozilla/5.0 (Linux; Android 10; K) Chrome/131") == "Android"
assert camui._phonecam_device_label("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "iPad") == "iPad"
assert camui._phonecam_device_label("x", "Android%20%C2%B7%20Redmi%2013") == "Android · Redmi 13"
assert "camuiDeviceLabel" in camui._PHONECAM_ADD_HTML.decode("utf-8")
camui.PHONECAM_DEVICES_FILE = os.path.join(HERE, "_unit_phonecam_devices24.json")
hub24 = camui.PhoneCamHub(deque(), pair_q24 := deque())
p24, t24 = hub24.start(58696)
try:
    req24 = urllib.request.Request("https://127.0.0.1:{0}/add/ready?label=iPad".format(p24),
                                   data=b"", method="POST")
    with _get(req24, https=True) as r:
        assert r.status == 200
    sid24 = re.search(r"sid=([^&]+)", pair_q24.popleft()).group(1)
    assert hub24.session_name(sid24) == "iPad"
    with _get("https://127.0.0.1:{0}/cam?t={1}&sid={2}".format(p24, t24, sid24), https=True) as r:
        dev24 = re.search(r'var dev = "([^"]+)";', r.read().decode("utf-8")).group(1)
    info24 = hub24.known_device(dev24)
    assert info24["name"] == "iPad" and info24["auto"] == "iPad" and info24["last"] > 0
    # uno de antes (sin «auto», nombre genérico) se pone al día al reconectar...
    hub24.add_device("viejo", "Móvil #3")
    devs = camui._phonecam_load_devices()
    devs["viejo"] = {"name": "Móvil #3", "added": 1.0}
    camui._phonecam_save_devices(devs)
    req24 = urllib.request.Request(
        "https://127.0.0.1:{0}/add/reconnect?dev=viejo&label=Android%20%C2%B7%20SM-A546B".format(p24),
        data=b"", method="POST")
    with _get(req24, https=True) as r:
        url24 = json.loads(r.read().decode("utf-8"))["url"]
    assert hub24.known_device("viejo")["name"] == "Android · SM-A546B"
    assert hub24.session_name(re.search(r"sid=([^&]+)", url24).group(1)) == "Android · SM-A546B"
    t_before = time.time()
    with _get("https://127.0.0.1:{0}{1}".format(p24, url24), https=True) as r:
        r.read()
    assert hub24.known_device("viejo")["last"] >= t_before
    # ...pero uno renombrado a mano no
    assert hub24.rename_device("viejo", "Tablet de la cocina")
    with _get(urllib.request.Request(
            "https://127.0.0.1:{0}/add/reconnect?dev=viejo&label=iPad".format(p24),
            data=b"", method="POST"), https=True) as r:
        r.read()
    assert hub24.known_device("viejo")["name"] == "Tablet de la cocina"
    assert not hub24.rename_device("no-existe", "x") and not hub24.rename_device("viejo", " ")
finally:
    hub24.stop()
print("24) nombre por defecto según el móvil («iPad», «iPhone», «Android · SM-A546B»); los "
      "«Móvil #3» de antes se ponen al día al reconectar, los renombrados a mano no; última "
      "conexión anotada OK")
# 25) (pack 38) «Ver lo que sale del PC» en la página de la cámara: lleva a «En la red» de ese
#     mismo PC, con el mismo nombre/IP con que el móvil llegó; si «En la red» está apagada, la
#     página lo sabe (dirección vacía) y lo dice en vez de llevar a ninguna parte
class _Viewer25:
    active, port = True, 8554


hub25 = camui.PhoneCamHub(deque(), deque())
p25, t25 = hub25.start(58698)
try:
    for viewer25, host25, want25 in ((_Viewer25(), None, "http://127.0.0.1:8554/"),
                                     (_Viewer25(), "ender-rog.local:{0}".format(p25),
                                      "http://ender-rog.local:8554/"),
                                     (None, None, "")):
        hub25.viewer = viewer25
        h25 = {"Host": host25} if host25 else {}
        req25 = urllib.request.Request("https://127.0.0.1:{0}/cam?t={1}".format(p25, t25), headers=h25)
        with _get(req25, https=True) as r:
            page25 = r.read().decode("utf-8")
        got25 = re.search(r'var viewerUrl = "([^"]*)";', page25).group(1)
        assert got25 == want25, (got25, want25)
    assert page25.count('class="view"') == 2 and "location.href = viewerUrl" in page25
finally:
    hub25.stop()
print("25) «Ver lo que sale del PC» lleva a «En la red» del mismo PC (por IP o por nombre), y "
      "sabe cuándo está apagada OK")

# 26) (pack 39) direcciones sin número de puerto: FrontDoor escucha en el «80» y el «443» (aquí,
#     puertos de prueba) y pasa cada conexión, tal cual, al servidor de siempre: por http según
#     la ruta (/certificado → el del certificado; /movil escrito sin https → redirige; lo demás →
#     «En la red», incluido /camui/clock); por https, todo a «Cámara del móvil», sin descifrar
import socket as _socket  # noqa: E402


def _free_port():
    s_ = _socket.socket()
    s_.bind(("127.0.0.1", 0))
    p_ = s_.getsockname()[1]
    s_.close()
    return p_


ip26 = camui.IpServer()
ip26.open(320, 240, port=_free_port())
hub26 = camui.PhoneCamHub(deque(), deque())
p26, t26 = hub26.start(58700)
hub26.viewer = ip26
ip26.phonecam = hub26
fake26 = type("A", (), {})()
fake26.phonecam, fake26.ipsrv = hub26, ip26
fd26 = camui.FrontDoor(_free_port(), _free_port())
fd26.http_route = lambda path: camui.CamApp._front_http_route(fake26, path)
fd26.https_target = lambda: hub26.port if hub26.active else None
old_front26 = camui.FRONT
camui.FRONT = fd26
fd26.start()
try:
    assert fd26.http_ok and fd26.https_ok
    H, S = fd26.http_port, fd26.https_port
    with urllib.request.urlopen("http://127.0.0.1:{0}/".format(H), timeout=5) as r:
        assert r.status == 200 and "Añadir cámara del móvil" in r.read().decode("utf-8")
    with urllib.request.urlopen("http://127.0.0.1:{0}/camui/clock".format(H), timeout=5) as r:
        assert json.loads(r.read().decode("utf-8")).get("camui") == 1
    with urllib.request.urlopen("http://127.0.0.1:{0}/certificado".format(H), timeout=5) as r:
        cert_page26 = r.read().decode("utf-8")
    assert "Instalar certificado" in cert_page26 and 'href="/certificado.cer"' in cert_page26
    with urllib.request.urlopen("http://127.0.0.1:{0}/certificado.cer".format(H), timeout=5) as r:
        der26 = r.read()
        assert r.headers.get("Content-Disposition", "").endswith(".cer") and len(der26) > 300
    x509.load_der_x509_certificate(der26)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        urllib.request.build_opener(_NoRedirect).open(
            urllib.request.Request("http://127.0.0.1:{0}/movil".format(H),
                                   headers={"Host": "ender-rog.local"}), timeout=5)
        raise SystemExit("debería redirigir a https")
    except urllib.error.HTTPError as e:
        assert e.code == 302 and e.headers["Location"] == "https://ender-rog.local:{0}/movil".format(S), \
            (e.code, e.headers.get("Location"))
    # https: la página de añadir y la cámara, con keep-alive de punta a punta por el proxy
    c26 = http.client.HTTPSConnection("127.0.0.1", S, context=CTX, timeout=5)
    c26.request("GET", "/movil")
    r26 = c26.getresponse()
    assert r26.status == 200 and "camuiDeviceLabel" in r26.read().decode("utf-8")
    c26.request("GET", "/cam?t=" + t26, headers={"Host": "ender-rog.local:{0}".format(S)})
    page26 = c26.getresponse().read().decode("utf-8")
    assert 'var viewerUrl = "http://ender-rog.local:{0}/";'.format(H) in page26, \
        re.search(r"var viewerUrl = [^;]*;", page26).group(0)
    sid26 = re.search(r'var sid = "([^"]+)"', page26).group(1)
    for k26 in range(3):
        c26.request("POST", "/cam/ingest/" + sid26, body=_jpeg(k26), headers={"X-Camui-Seq": str(k26 + 1)})
        r26 = c26.getresponse()
        r26.read()
        assert r26.status == 200 and not r26.will_close
    c26.close()
    assert hub26.latest(sid26)[1] == 3
    # las direcciones que enseña cam-ui ya van sin el número de siempre
    assert camui._add_path() == "/movil"
    assert camui._https_url("192.168.1.129", 8555, "/movil") == "https://192.168.1.129:{0}/movil".format(S)
    assert camui._http_url("192.168.1.129", 8554, "/") == "http://192.168.1.129:{0}/".format(H)
    # el certificado HTTPS también vale para el nombre del PC (y su .local)
    with open(camui.PHONECAM_CERT_FILE, "rb") as f:
        san26 = x509.load_pem_x509_certificate(f.read()).extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert all(n in san26 for n in camui._phonecam_host_names()), san26
    # un puerto ya ocupado por otro programa: esa parte no se enciende y todo lleva su número
    busy26 = _socket.socket()
    busy26.bind(("0.0.0.0", 0))
    busy26.listen(1)
    fd26b = camui.FrontDoor(busy26.getsockname()[1], None)
    camui.FRONT = fd26b
    fd26b.start()
    assert not fd26b.http_ok and camui._http_url("10.0.0.2", 8554) == "http://10.0.0.2:8554/"
    assert camui._add_path() == "/add"
    busy26.close()
finally:
    camui.FRONT = old_front26
    fd26.stop()
    hub26.stop()
    ip26.close()
print("26) sin número de puerto: «/», «/camui/clock», «/certificado» (página) y «.cer», «/movil» "
      "por http redirige a https, y por https la página y los fotogramas (keep-alive) llegan "
      "tal cual; certificado con el nombre del PC; con el puerto ocupado, todo como antes OK")

# 27) (pack 46) contraseña como alternativa al QR en «/add»: por HTTPS real, en el cuerpo (nunca
#     en la dirección); sin contraseña puesta, «nopass»; mal, «wrong», y tras 5 fallos seguidos ese
#     móvil espera 30 s (que se doblan) -- mientras espera, ni la buena entra --; bien, una sesión
#     nueva que lleva a la página de captura de siempre, que guarda el móvil para la próxima vez
#     (igual que el QR). Entre todos, más de 20 fallos en 10 min la cierran a todo el mundo. La
#     contraseña no aparece nunca en camui.log.
camui.PHONECAM_DEVICES_FILE = os.path.join(HERE, "_unit_phonecam_devices27.json")
if os.path.exists(camui.PHONECAM_DEVICES_FILE):
    os.remove(camui.PHONECAM_DEVICES_FILE)


class _Grab(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, rec):
        self.lines.append(rec.getMessage())


grab27 = _Grab()
camui.log.addHandler(grab27)
camui.log.setLevel(logging.DEBUG)
PW27 = "pato azul ñ 7 🦆"
hub27 = camui.PhoneCamHub(deque(), deque())
port27, tok27 = hub27.start(_free_port())
try:
    def _pw(given, label="Android · Pixel 7"):
        body = json.dumps({"pass": given, "label": label}).encode("utf-8")
        req = urllib.request.Request("https://127.0.0.1:{0}/add/password".format(port27), data=body,
                                     method="POST", headers={"Content-Type": "application/json"})
        with _get(req, https=True) as r:
            return json.loads(r.read().decode("utf-8"))

    assert _pw("lo-que-sea") == {"ok": False, "error": "nopass", "wait": 0}
    hub27.password = PW27
    for k in range(camui.PHONECAM_PW_FREE - 1):
        assert _pw("pato azul") == {"ok": False, "error": "wrong", "wait": 0}, k
    r27 = _pw("PATO AZUL Ñ 7 🦆")
    assert r27["error"] == "wrong" and r27["wait"] == int(camui.PHONECAM_PW_LOCK_S), r27
    r27b = _pw(PW27)                                    # bloqueado: ni con la buena
    assert r27b["ok"] is False and r27b["error"] == "wait" and 0 < r27b["wait"] <= 30, r27b
    hub27._pw_fail.clear()                              # (como si hubieran pasado los 30 s)
    ok27 = _pw(PW27)
    assert ok27["ok"] is True and ok27["url"].startswith("/cam?t={0}&sid=".format(tok27)), ok27
    sid27 = ok27["url"].split("sid=")[1]
    assert hub27.session_name(sid27) == "Android · Pixel 7"
    with _get("https://127.0.0.1:{0}{1}".format(port27, ok27["url"]), https=True) as r:
        body27 = r.read().decode("utf-8")
    dev27 = re.search(r'var dev = "([^"]+)";', body27).group(1)
    assert hub27.known_device(dev27) is not None, "debería recordarse como con el QR"
    # el doblado de la espera y la puerta común
    waits = []
    for _ in range(camui.PHONECAM_PW_FREE + 6):
        stw, w = hub27.check_password("10.0.0.9", "mal")
        assert stw == "wrong", stw
        waits.append(round(w))
        n_, _u = hub27._pw_fail["10.0.0.9"]
        hub27._pw_fail["10.0.0.9"] = (n_, 0.0)        # (como si ya hubiera esperado)
    assert waits == [0, 0, 0, 0, 30, 60, 120, 240, 480, 900, 900], waits
    hub27._pw_fail.clear()
    hub27._pw_global.clear()
    for k in range(camui.PHONECAM_PW_GLOBAL_MAX):
        assert hub27.check_password("10.0.1.{0}".format(k), "mal")[0] == "wrong"
    stg, wg = hub27.check_password("10.0.2.1", PW27)
    assert stg == "wait" and wg > 500, (stg, wg)
    hub27._pw_global.clear()
    assert hub27.check_password("10.0.2.1", PW27)[0] == "ok"
    # la propia página: el botón, y la contraseña por el cuerpo (no en la dirección)
    with _get("https://127.0.0.1:{0}/movil".format(port27), https=True) as r:
        add27 = r.read().decode("utf-8")
    assert 'id="pwbtn"' in add27 and "Usar contrase" in add27 and 'fetch("/add/password"' in add27
    assert "body: JSON.stringify({pass: pw.value" in add27 and "?pass=" not in add27
    assert not any(PW27 in l or "pato azul" in l.lower() for l in grab27.lines), \
        [l for l in grab27.lines if "pato" in l.lower()]
    assert any("contraseña incorrecta desde 127.0.0.1" in l for l in grab27.lines)
    assert any("entra con la contraseña" in l for l in grab27.lines)
finally:
    hub27.stop()
    camui.log.removeHandler(grab27)
print("27) contraseña en «/add»: sin poner, «nopass»; 5 fallos → espera de 30 s (y ni la buena "
      "entra), que se dobla; 20 fallos entre todos la cierran; bien → página de captura y móvil "
      "recordado; por el cuerpo, nunca en la dirección ni en camui.log OK")

import glob  # noqa: E402
for f in list(_phonecam_pem_files) + glob.glob(os.path.join(HERE, "_unit_phonecam_devices*.json")):
    if os.path.exists(f):
        os.remove(f)

print("TODO OK unit_phonecam")
