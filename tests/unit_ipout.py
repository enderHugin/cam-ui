"""Salida en red (verlo desde otro equipo/móvil): IpServer de verdad (socket local + servidor
HTTP MJPEG propio) contra un emisor de verdad (un socket que manda fotogramas, como haría
ffmpeg) y peticiones HTTP de verdad. No usa ffmpeg: lo que hace falta probar es el servidor
HTTP propio, no el "tcp://" de ffmpeg (ese mecanismo ya lo prueba unit_update.py/la cámara
virtual en producción; aquí el cliente lo simula un socket normal)."""
import sys, os, io, re, time, socket, threading, http.client
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui
import numpy as np
from PIL import Image

# 1) build_command añade la salida "ip" (tcp://127.0.0.1:<puerto>) cuando hay plan["ipout"] ----
plan = {"cams": [{"spec": "video=Foo", "mode": None, "h": False, "v": False, "rot": 0, "delay": 0,
                  "tile": "auto", "zoom": 100.0, "cx": 50.0, "cy": 50.0, "color": None}],
       "geom": {"W": 640, "H": 480, "tiles": [(0, 0, 640, 480)]}, "fps": 30, "sync": "clock",
       "crf": 23, "preset": "veryfast", "audio": {"L": None, "R": None, "offset": 0},
       "ipout": {"w": 320, "h": 240, "fps": 10, "pix": "rgb24", "port": 55123}}
cmd, meta = camui.build_command(plan, outfile=None, preview=True,
                                input_fn=lambda kind, spec, opt=None: ["-f", "lavfi", "-i", "testsrc"])
cmdline = " ".join(cmd)
assert "tcp://127.0.0.1:55123" in cmdline, cmdline
assert "[vc]" not in cmdline, "no debería haber cámara virtual en este plan"
# sin puerto (aún no armado), build_command no debe intentar una salida "ip" rota
plan2 = dict(plan, ipout={"w": 320, "h": 240, "fps": 10, "pix": "rgb24"})         # sin "port"
cmd2, _m2 = camui.build_command(plan2, outfile=None, preview=True,
                                input_fn=lambda kind, spec, opt=None: ["-f", "lavfi", "-i", "testsrc"])
assert "tcp://127.0.0.1" not in " ".join(cmd2)
# con "aport" armado pero SIN micrófono (plan["audio"] vacío): no debe intentar oír nada -------
plan3 = dict(plan, ipout=dict(plan["ipout"], aport=55124))
cmd3, _m3 = camui.build_command(plan3, outfile=None, preview=True,
                                input_fn=lambda kind, spec, opt=None: ["-f", "lavfi", "-i", "testsrc"])
s3 = " ".join(cmd3)
assert "libmp3lame" not in s3 and "tcp://127.0.0.1:55124" not in s3, s3
# con "aport" armado Y micrófono: sí debe mandar el audio a su propio tcp:// en MP3 ------------
plan4 = dict(plan3, audio={"L": {"spec": "audio=Mic", "chan": "mix", "gain": 0, "delay": 0},
                            "R": None, "offset": 0})
infn4 = lambda kind, spec, opt=None: (["-f", "lavfi", "-i", "testsrc"] if kind == "video"
                                       else ["-f", "lavfi", "-i", "sine=frequency=440"])
cmd4, _m4 = camui.build_command(plan4, outfile=None, preview=True, input_fn=infn4)
s4 = " ".join(cmd4)
assert "libmp3lame" in s4 and "tcp://127.0.0.1:55124" in s4, s4
assert re.search(r"-map \[\w+\] -c:a libmp3lame", s4), s4   # se mapea una etiqueta real del grafo, no vacía
print("1) build_command (salida «ip», vídeo y audio) OK")

# 2) IpServer de verdad: abre el HTTP, arma el socket, un emisor de verdad manda fotogramas, y
#    un cliente HTTP de verdad recibe MJPEG válido (JPEG que decodifica, del tamaño esperado) --
W, H = 64, 48
HTTP_PORT = 58123
srv = camui.IpServer()
srv.open(W, H, fps=10, port=HTTP_PORT)
assert srv.active
port = srv.arm()
assert port > 0


def sender():
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    frame = np.zeros((H, W, 3), np.uint8)
    frame[:, :, 0] = 200                      # rojo: color reconocible en el JPEG recibido
    try:
        for _ in range(8):
            s.sendall(frame.tobytes())
            time.sleep(0.05)
    finally:
        s.close()


th = threading.Thread(target=sender, daemon=True)
th.start()
th.join(3)

deadline = time.time() + 3
while time.time() < deadline and srv._jpeg is None:
    time.sleep(0.05)
assert srv._jpeg is not None, "el servidor no codificó ningún fotograma"
assert srv.received > 0, "el servidor no recibió fotogramas del emisor"

conn = http.client.HTTPConnection("127.0.0.1", HTTP_PORT, timeout=5)
conn.request("GET", "/stream.mjpg")
resp = conn.getresponse()
assert resp.status == 200, resp.status
ctype = resp.getheader("Content-Type", "")
assert "multipart/x-mixed-replace" in ctype, ctype

buf = b""
jpeg = None
deadline = time.time() + 5
while time.time() < deadline:
    # read1 (no read): devuelve lo que haya, sin esperar a llenar el tamaño pedido -- el
    # flujo MJPEG llega en trocitos pequeños y seguidos, no en bloques de 4096
    chunk = resp.fp.read1(4096)
    if not chunk:
        break
    buf += chunk
    marker = b"Content-Length: "
    i = buf.find(marker)
    if i < 0:
        continue
    j = buf.find(b"\r\n\r\n", i)
    if j < 0:
        continue
    n = int(buf[i + len(marker):j].split(b"\r\n")[0])
    start = j + 4
    if len(buf) >= start + n:
        jpeg = buf[start:start + n]
        break
assert jpeg is not None, "no llegó ningún fotograma JPEG completo por HTTP"
img = Image.open(io.BytesIO(jpeg))
assert img.size == (W, H), img.size
arr = np.asarray(img.convert("RGB"))
assert int(arr[H // 2, W // 2, 0]) > 150, "el color del fotograma recibido no es el esperado"
conn.close()
print("2) IpServer: socket -> HTTP MJPEG (de verdad) OK")

# 3) un visor que se desconecta de golpe no tumba el servidor ni afecta a otro visor -----------
conn2 = http.client.HTTPConnection("127.0.0.1", HTTP_PORT, timeout=5)
conn2.request("GET", "/stream.mjpg")
resp2 = conn2.getresponse()
assert resp2.status == 200
conn2.close()                                  # desconexión brusca, sin leer el cuerpo
time.sleep(0.3)
assert srv.active, "el servidor se cayó al desconectarse un visor"

conn3 = http.client.HTTPConnection("127.0.0.1", HTTP_PORT, timeout=5)
conn3.request("GET", "/stream.mjpg")
resp3 = conn3.getresponse()
assert resp3.status == 200
conn3.close()
srv.close()
assert not srv.active
print("3) un visor que se cuelga/desconecta no afecta a ffmpeg ni a otros visores OK")

# 4) puerto ocupado: abrir dos veces el mismo puerto falla con un error claro (OSError) --------
srv_a = camui.IpServer()
srv_a.open(W, H, port=HTTP_PORT + 1)
try:
    srv_b = camui.IpServer()
    failed = False
    try:
        srv_b.open(W, H, port=HTTP_PORT + 1)
    except OSError:
        failed = True
    assert failed, "abrir dos veces el mismo puerto debería fallar"
finally:
    srv_a.close()
print("4) puerto ocupado -> OSError OK")

# 5) ip_size_label / ip_size_value van y vuelven -------------------------------------------------
for lab, sz in camui.IP_SIZES:
    assert camui.ip_size_value(lab) == sz
    assert camui.ip_size_label(sz) == lab
print("5) ip_size_label / ip_size_value OK")

# 6) extremo a extremo con ffmpeg de verdad: el comando exacto que generaría cam-ui (vía
#    build_command, con una cámara de prueba en vez de una real) manda fotogramas al socket
#    armado por IpServer, y un cliente HTTP de verdad recibe un JPEG real -----------------------
import subprocess

E2E_W, E2E_H = 96, 64
srv6 = camui.IpServer()
HTTP6 = 58996
srv6.open(E2E_W, E2E_H, fps=10, port=HTTP6)
port6 = srv6.arm()
plan6 = {"cams": [{"spec": "testsrc", "mode": None, "h": False, "v": False, "rot": 0, "delay": 0,
                   "tile": "auto", "zoom": 100.0, "cx": 50.0, "cy": 50.0, "color": None}],
        "geom": {"W": E2E_W, "H": E2E_H, "tiles": [(0, 0, E2E_W, E2E_H)]}, "fps": 10,
        "sync": "clock", "crf": 23, "preset": "veryfast",
        "audio": {"L": None, "R": None, "offset": 0},
        "ipout": {"w": E2E_W, "h": E2E_H, "fps": 10, "pix": "rgb24", "port": port6}}
cmd6, _m6 = camui.build_command(
    plan6, outfile=None, preview=False,
    input_fn=lambda kind, spec, opt=None: ["-f", "lavfi", "-i", "testsrc=size={0}x{1}:rate=10".format(
        E2E_W, E2E_H)])
# "-t" tiene que ir ANTES de la URL de salida (es una opción de esa salida): puesto al final,
# colgando sin ninguna salida detrás, ffmpeg lo ignora sin avisar y el proceso no termina nunca
# solo (confirmado probándolo de verdad: con "-t" al final seguía vivo pasados 15s; puesto justo
# antes de "tcp://" corta a los ~2s con rc=0).
_tcp_idx = next(i for i, x in enumerate(cmd6) if x.startswith("tcp://"))
cmd6[_tcp_idx:_tcp_idx] = ["-t", "2"]
r6 = subprocess.run(cmd6, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
assert r6.returncode == 0, "ffmpeg (comando real de cam-ui) falló: rc={0}\n{1}".format(
    r6.returncode, r6.stderr[-800:])
assert srv6.received > 0, "IpServer no recibió nada del ffmpeg real"

conn6 = http.client.HTTPConnection("127.0.0.1", HTTP6, timeout=5)
conn6.request("GET", "/stream.mjpg")
resp6 = conn6.getresponse()
assert resp6.status == 200
buf6, jpeg6, deadline6 = b"", None, time.time() + 5
while time.time() < deadline6:
    chunk = resp6.fp.read1(4096)
    if not chunk:
        break
    buf6 += chunk
    marker = b"Content-Length: "
    i = buf6.find(marker)
    if i < 0:
        continue
    j = buf6.find(b"\r\n\r\n", i)
    if j < 0:
        continue
    n = int(buf6[i + len(marker):j].split(b"\r\n")[0])
    start = j + 4
    if len(buf6) >= start + n:
        jpeg6 = buf6[start:start + n]
        break
assert jpeg6 is not None, "no llegó un JPEG real tras el comando completo de cam-ui"
img6 = Image.open(io.BytesIO(jpeg6))
assert img6.size == (E2E_W, E2E_H), img6.size
conn6.close()
srv6.close()
print("6) extremo a extremo con el comando real de cam-ui (ffmpeg -> IpServer -> HTTP) OK")

# 7) direcciones mostradas al usuario: barra de estado y bandeja (CamApp._ip_urls / _tray_title /
#    _ip_primary_url / _ip_dual_url) para que sepa adónde ir sin tener que abrir Ajustes. Se
#    llaman tal cual las llamaría la app, con un "self" de mentira (no hace falta Tk para probar
#    esta lógica) ----------------------------------------------------------------------------
import socket
import types

fake = types.SimpleNamespace(ipsrv=types.SimpleNamespace(port=8554), ipout_on=True, recording=False)
fake._ip_primary_url = lambda: camui.CamApp._ip_primary_url(fake)
fake._ip_dual_url = lambda: camui.CamApp._ip_dual_url(fake)

urls = camui.CamApp._ip_urls(fake, 8554)
assert socket.gethostname() in urls, urls               # el nombre del equipo, como alternativa a la IP
assert urls.count("http://") >= 2, urls                 # al menos una IP y el nombre, en formato URL

primary = fake._ip_primary_url()
assert primary.startswith("http://") and primary.endswith(":8554/"), primary

dual = fake._ip_dual_url()
assert dual.count("http://") == 2, dual                 # exactamente IP + nombre, los dos en URL
assert dual.startswith(primary), dual                   # la IP (o el nombre si no hay IP) va primero
assert "http://{0}:8554/".format(socket.gethostname()) in dual, dual   # y el nombre siempre está

t_rec_ip = camui.CamApp._tray_title(fake)
assert t_rec_ip == "cam-ui · red: " + dual, t_rec_ip
fake.recording = True
t_rec_ip2 = camui.CamApp._tray_title(fake)
assert t_rec_ip2 == "cam-ui · grabando · red: " + dual, t_rec_ip2
fake.ipout_on = False
t_rec = camui.CamApp._tray_title(fake)
assert t_rec == "cam-ui · grabando", t_rec
fake.recording = False
t_off = camui.CamApp._tray_title(fake)
assert t_off == "cam-ui", t_off
print("7) direcciones en la barra de estado y la bandeja (_ip_urls / _ip_primary_url / "
      "_ip_dual_url / _tray_title) OK")

# 8) la página en "/" trae lo necesario (vídeo, sonido, pantalla completa) y una ruta que no
#    existe da 404, sin tumbar el servidor -------------------------------------------------
HTTP8 = 58997
srv8 = camui.IpServer()
srv8.open(64, 48, fps=10, port=HTTP8)
conn8 = http.client.HTTPConnection("127.0.0.1", HTTP8, timeout=5)
conn8.request("GET", "/")
resp8 = conn8.getresponse()
assert resp8.status == 200, resp8.status
assert "text/html" in resp8.getheader("Content-Type", ""), resp8.getheader("Content-Type")
page = resp8.read().decode("utf-8")
conn8.close()
for needle in ("/stream.mjpg", "/audio.mp3", "requestFullscreen", "Activar sonido"):
    assert needle in page, "falta «{0}» en la página servida en «/»".format(needle)
conn8b = http.client.HTTPConnection("127.0.0.1", HTTP8, timeout=5)
conn8b.request("GET", "/no-existe")
resp8b = conn8b.getresponse()
assert resp8b.status == 404, resp8b.status
resp8b.read()
conn8b.close()
assert srv8.active, "una ruta desconocida no debe tumbar el servidor"
srv8.close()
print("8) página en «/» (vídeo + sonido + pantalla completa) y 404 en rutas desconocidas OK")

# 9) sonido de verdad, extremo a extremo: el comando real de cam-ui con un micrófono simulado
#    (sine de lavfi) manda el audio por su propio socket, y un cliente HTTP de verdad recibe en
#    «/audio.mp3» bytes MP3 reales -- se comprueban decodificando con ffprobe, no a ojo. A
#    diferencia del vídeo (que guarda el último fotograma, así que en el bloque 6 el cliente HTTP
#    se conecta DESPUÉS de que ffmpeg ya ha terminado), el audio se reparte "en directo" a quien
#    esté escuchando en ese momento -- no guarda nada para quien llegue tarde -- así que aquí hay
#    que conectarse por HTTP MIENTRAS ffmpeg todavía está mandando, igual que haría un navegador
#    real abriendo la página mientras cam-ui ya está emitiendo ---------------------------------
import subprocess
import tempfile
import threading

E2E_W9, E2E_H9 = 96, 64
HTTP9 = 58998
srv9 = camui.IpServer()
srv9.open(E2E_W9, E2E_H9, fps=10, port=HTTP9)
vport9 = srv9.arm()
aport9 = srv9.arm_audio()
plan9 = {"cams": [{"spec": "testsrc", "mode": None, "h": False, "v": False, "rot": 0, "delay": 0,
                   "tile": "auto", "zoom": 100.0, "cx": 50.0, "cy": 50.0, "color": None}],
        "geom": {"W": E2E_W9, "H": E2E_H9, "tiles": [(0, 0, E2E_W9, E2E_H9)]}, "fps": 10,
        "sync": "clock", "crf": 23, "preset": "veryfast",
        "audio": {"L": {"spec": "audio=Mic", "chan": "mix", "gain": 0, "delay": 0},
                  "R": None, "offset": 0},
        "ipout": {"w": E2E_W9, "h": E2E_H9, "fps": 10, "pix": "rgb24", "port": vport9, "aport": aport9}}


def infn9(kind, spec, opt=None):
    if kind == "video":
        return ["-f", "lavfi", "-i", "testsrc=size={0}x{1}:rate=10".format(E2E_W9, E2E_H9)]
    return ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]


cmd9, _m9 = camui.build_command(plan9, outfile=None, preview=False, input_fn=infn9)
assert "libmp3lame" in " ".join(cmd9), " ".join(cmd9)
# "-t" tiene que ir pegado delante de CADA url de salida (vídeo Y audio): ver el comentario del
# bloque 6 -- aquí hay dos "tcp://" (uno de vídeo, otro de audio) y a los dos hace falta cortarlos.
# Se insertan de atrás hacia adelante para que los índices de las anteriores no se desplacen. Se
# deja correr 5s (más que el bloque 6) para dar tiempo de sobra a conectarse por HTTP "en vivo".
tcp_idx = [i for i, x in enumerate(cmd9) if x.startswith("tcp://")]
assert len(tcp_idx) == 2, cmd9          # un tcp:// de vídeo y otro de audio
for i in reversed(tcp_idx):
    cmd9[i:i] = ["-t", "5"]

proc9 = subprocess.Popen(cmd9, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, text=True)
err9_lines = []
threading.Thread(target=lambda: err9_lines.extend(proc9.stderr), daemon=True).start()

mp3 = b""
try:
    # esperar a que ffmpeg empiece de verdad a mandar sonido antes de conectarnos (si tardara más
    # de la cuenta, el "assert" de abajo lo diría con un mensaje claro en vez de colgarse)
    deadline9a = time.time() + 8
    while time.time() < deadline9a and srv9.a_received == 0:
        time.sleep(0.05)
    assert srv9.a_received > 0, "ffmpeg no llegó a mandar nada de audio a tiempo"

    conn9 = http.client.HTTPConnection("127.0.0.1", HTTP9, timeout=2)
    conn9.request("GET", "/audio.mp3")
    resp9 = conn9.getresponse()
    assert resp9.status == 200
    assert resp9.getheader("Content-Type") == "audio/mpeg", resp9.getheader("Content-Type")
    deadline9b = time.time() + 6
    while time.time() < deadline9b and len(mp3) < 8000:
        try:
            chunk = resp9.fp.read1(4096)
        except (socket.timeout, TimeoutError):
            continue
        if not chunk:
            break
        mp3 += chunk
    conn9.close()
finally:
    try:
        proc9.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc9.kill()
        proc9.wait(timeout=5)

assert proc9.returncode == 0, "ffmpeg (vídeo+sonido reales) falló: rc={0}\n{1}".format(
    proc9.returncode, "".join(err9_lines)[-800:])
assert srv9.received > 0, "no llegó vídeo"
assert srv9.a_received > 0, "no llegó sonido"
srv9.close()
assert len(mp3) > 500, "muy pocos bytes de audio recibidos por HTTP: {0}".format(len(mp3))

with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
    tf.write(mp3)
    mp3_path = tf.name
try:
    rp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,duration",
                        "-of", "default=noprint_wrappers=1", mp3_path],
                        capture_output=True, text=True, timeout=10)
    out = rp.stdout
    assert "codec_name=mp3" in out, "ffprobe no ve un MP3 válido:\n" + out + rp.stderr
finally:
    os.unlink(mp3_path)
print("9) sonido de verdad, extremo a extremo (ffmpeg -> IpServer -> HTTP -> MP3 que decodifica "
      "ffprobe) OK")

print("TODO OK unit_ipout")
