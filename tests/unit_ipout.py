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
fake._ip_url_list = lambda port: camui.CamApp._ip_url_list(fake, port)

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

# 10) ip_fit_size: la salida en red lleva la MISMA forma que el mosaico, lo más grande que quepa
#     en el máximo elegido y nunca mayor que el propio mosaico. Casos reales del aviso: un PC con
#     una sola cámara 4:3 (1280×960) y con dos cámaras lado a lado (1280×548), ambos con el máximo
#     recomendado (640×360) -- antes llegaban los dos como 640×360 con bandas negras.
assert camui.ip_fit_size(1280, 960, (640, 360)) == (480, 360)
assert camui.ip_fit_size(1280, 548, (640, 360)) == (640, 274)
assert camui.ip_fit_size(1280, 720, (640, 360)) == (640, 360)          # 16:9 cabe tal cual
assert camui.ip_fit_size(1280, 960, (1920, 1080)) == (1280, 960)        # máximo grande: entero
assert camui.ip_fit_size(640, 480, (1920, 1080)) == (640, 480)          # nunca lo agranda
assert camui.ip_fit_size(720, 1280, (640, 360)) == (202, 360)           # vertical (móvil)
for W_, H_ in ((1280, 960), (1280, 548), (1918, 1078), (333, 777), (4000, 10)):
    for box_ in camui.IP_SIZES:
        fw_, fh_ = camui.ip_fit_size(W_, H_, box_[1])
        assert fw_ % 2 == 0 and fh_ % 2 == 0 and fw_ <= box_[1][0] and fh_ <= box_[1][1], \
            (W_, H_, box_, fw_, fh_)
        if min(fw_, fh_) > 40:              # misma forma (salvo el redondeo a pares)
            assert abs(fw_ / float(fh_) - W_ / float(H_)) / (W_ / float(H_)) < 0.02, (W_, H_, fw_, fh_)
print("10) ip_fit_size: misma forma que el mosaico, dentro del máximo, sin agrandar, pares OK")


def _first_jpeg(http_port, timeout=5):
    c = http.client.HTTPConnection("127.0.0.1", http_port, timeout=timeout)
    c.request("GET", "/stream.mjpg")
    r = c.getresponse()
    buf, t = b"", time.time() + timeout
    try:
        while time.time() < t:
            ch = r.fp.read1(4096)
            if not ch:
                break
            buf += ch
            i = buf.find(b"Content-Length: ")
            j = buf.find(b"\r\n\r\n", i) if i >= 0 else -1
            if j < 0:
                continue
            n = int(buf[i + 16:j].split(b"\r\n")[0])
            if len(buf) >= j + 4 + n:
                return Image.open(io.BytesIO(buf[j + 4:j + 4 + n]))
    finally:
        c.close()


# 11) extremo a extremo, los dos casos reales del aviso: mosaico blanco puro -> el comando real
#     de cam-ui (build_command, con el tamaño que calcula ip_fit_size como haría CamApp._plan) ->
#     ffmpeg de verdad -> IpServer armado con ese tamaño -> HTTP -> JPEG. Debe llegar con la forma
#     del mosaico y SIN ninguna banda negra (antes: 160 columnas negras con la cámara 4:3 y 86
#     filas negras con las dos cámaras, medido igual que aquí).
def _bars_e2e(W_, H_, http_port):
    s_ = camui.IpServer()
    s_.open(640, 360, fps=10, port=http_port)
    try:
        fw_, fh_ = camui.ip_fit_size(W_, H_, s_.box)
        p_ = s_.arm(fw_, fh_)
        plan_ = {"cams": [{"spec": "x", "mode": None, "h": False, "v": False, "rot": 0, "delay": 0,
                           "tile": "auto", "zoom": 100.0, "cx": 50.0, "cy": 50.0, "color": None}],
                 "geom": {"W": W_, "H": H_, "tiles": [(0, 0, W_, H_)]}, "fps": 10, "sync": "clock",
                 "crf": 23, "preset": "veryfast", "audio": {"L": None, "R": None, "offset": 0},
                 "ipout": {"w": fw_, "h": fh_, "fps": 10, "pix": "rgb24", "port": p_}}
        cmd_, _ = camui.build_command(plan_, outfile=None, preview=False, input_fn=lambda k, s, o=None: [
            "-f", "lavfi", "-i", "color=c=white:size={0}x{1}:rate=10".format(W_, H_)])
        k_ = next(i for i, x in enumerate(cmd_) if x.startswith("tcp://"))
        cmd_[k_:k_] = ["-t", "2"]
        r_ = subprocess.run(cmd_, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        assert r_.returncode == 0, r_.stderr[-600:]
        im_ = _first_jpeg(http_port)
        assert im_ is not None, "no llegó ningún JPEG"
        a_ = np.asarray(im_.convert("L"))
        return im_.size, int((a_.mean(axis=0) < 40).sum()), int((a_.mean(axis=1) < 40).sum())
    finally:
        s_.close()


for (W_, H_), want_, hp_ in (((1280, 960), (480, 360), 58911), ((1280, 548), (640, 274), 58912)):
    size_, dcols_, drows_ = _bars_e2e(W_, H_, hp_)
    assert size_ == want_, (W_, H_, size_)
    assert dcols_ == 0 and drows_ == 0, "bandas negras: {0} columnas, {1} filas ({2}x{3})".format(
        dcols_, drows_, W_, H_)
print("11) extremo a extremo (ffmpeg real -> IpServer -> HTTP): mosaico 4:3 de una cámara y "
      "apaisado de dos llegan con su forma real y sin ninguna banda negra OK")

# 12) IpServer.arm(w, h): el tamaño puede cambiar de un arranque de ffmpeg a otro (el emisor
#     cambia su mosaico) y se sirve el nuevo; si el HLS del Chromecast estaba en marcha (con el
#     tamaño anterior fijado al arrancarlo), se para -- un proceso real hace de ese ffmpeg aquí.
s12 = camui.IpServer()
s12.open(640, 360, fps=10, port=58913)
try:
    for (w_, h_), val_ in (((480, 360), 200), ((640, 274), 60)):
        p_ = s12.arm(w_, h_)
        assert (s12.w, s12.h) == (w_, h_) and s12.box == (640, 360)
        so_ = socket.create_connection(("127.0.0.1", p_), timeout=5)
        fr_ = np.full((h_, w_, 3), val_, np.uint8).tobytes()
        for _ in range(5):
            so_.sendall(fr_)
            time.sleep(0.05)
        im_ = None
        t_ = time.time() + 5
        while time.time() < t_:
            im_ = _first_jpeg(58913)
            if im_ is not None and im_.size == (w_, h_):
                break
            time.sleep(0.1)
        so_.close()
        assert im_ is not None and im_.size == (w_, h_), (w_, h_, im_ and im_.size)
    fake_hls = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                stdin=subprocess.PIPE)
    s12._hls_proc = fake_hls
    s12.arm(640, 274)                               # mismo tamaño: el HLS sigue
    assert s12._hls_proc is fake_hls
    s12.arm(480, 360)                               # otro tamaño: el HLS se para
    assert s12._hls_proc is None
    fake_hls.wait(timeout=5)
finally:
    s12.close()
print("12) IpServer.arm(w, h): cambia de tamaño entre arranques y para el HLS si cambia OK")


# 13) CamApp._plan: el tamaño que pide para la salida en red sale de la forma de SU mosaico
#     (_geometry) dentro del máximo elegido, no del máximo tal cual. Con un "self" de mentira.
class _Var13:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


class _Cam13:
    def to_plan(self, spec):
        return {"spec": spec}

    def color_values(self):
        return {}


def _plan13(geom):
    srv_ = types.SimpleNamespace(active=True, box=(640, 360), w=640, h=360, fps=10, pix="rgb24")
    f_ = types.SimpleNamespace(
        editing=None, order=list(geom["labels"]), vdev={l: {"spec": l} for l in geom["labels"]},
        cams={l: _Cam13() for l in geom["labels"]}, v_quality=_Var13("Media"),
        v_sync=_Var13(""), v_preset=_Var13("veryfast"), vcam_on=False, vcam=None,
        ipout_on=True, ipsrv=srv_)
    f_._geometry = lambda: geom
    f_._fps = lambda: 30
    f_._audio_plan = lambda: {"L": None, "R": None, "offset": 0}
    return camui.CamApp._plan(f_)


ip13a = _plan13({"W": 1280, "H": 960, "tiles": [(0, 0, 1280, 960)], "labels": ["HP"]})["ipout"]
assert (ip13a["w"], ip13a["h"]) == (480, 360), ip13a
ip13b = _plan13({"W": 1280, "H": 548, "tiles": [(0, 0, 732, 548), (732, 0, 548, 548)],
                 "labels": ["HP", "EOS"]})["ipout"]
assert (ip13b["w"], ip13b["h"]) == (640, 274), ip13b
print("13) CamApp._plan pide a «En la red» la forma de su mosaico (480×360 / 640×274), no el "
      "máximo tal cual OK")

# 14) lo nuevo que esto puede provocar en el PC que lo recibe: el tamaño que llega ya no es
#     siempre el mismo, cambia si el emisor cambia su mosaico (p. ej. añade una cámara) mientras
#     el otro lo está recibiendo. Con el comando real del receptor (esta URL como cámara de red,
#     grabando), el emisor pasa de 480×360 (gris claro) a 640×274 (gris oscuro) a
#     mitad: el receptor no debe caerse y debe seguir entrando vídeo después del cambio.
#     El emisor sigue mandando hasta que el receptor termina (como en la realidad): si dejara de
#     mandar antes, el receptor se quedaría esperando a esa cámara -- eso es otra cosa (una
#     cámara de red que deja de responder), no lo que se prueba aquí.
HTTP14 = 58914
s14 = camui.IpServer()
s14.open(640, 360, fps=10, port=HTTP14)
stop14 = threading.Event()


def _feed14(w_, h_, secs_, val_):
    p_ = s14.arm(w_, h_)
    so_ = None
    for _ in range(50):
        try:
            so_ = socket.create_connection(("127.0.0.1", p_), timeout=2)
            break
        except OSError:
            time.sleep(0.05)
    fr_ = np.full((h_, w_, 3), val_, np.uint8).tobytes()
    t_ = time.time()
    try:
        while (secs_ is None or time.time() - t_ < secs_) and not stop14.is_set():
            so_.sendall(fr_)
            time.sleep(0.1)
    except OSError:
        pass
    finally:
        so_.close()


def _sender14():
    _feed14(480, 360, 3.0, 200)
    time.sleep(0.3)                  # el hueco de un reinicio de ffmpeg en el emisor
    _feed14(640, 274, None, 90)      # hasta que el receptor termine


th14 = threading.Thread(target=_sender14, daemon=True)
th14.start()
time.sleep(0.8)
out14 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ipout_resize_rx.mp4")
if os.path.exists(out14):
    os.remove(out14)
_cam14 = lambda spec: {"spec": spec, "mode": None, "h": False, "v": False, "rot": 0, "delay": 0,
                       "tile": "auto", "zoom": 100.0, "cx": 50.0, "cy": 50.0, "color": {}}
# (solo la cámara encadenada en el receptor: con otra cámara sintética «-re» al lado, el
#  receptor a veces no arranca -- comprobado que eso pasa igual con el código anterior y sin
#  ningún cambio de tamaño, así que no es lo que se prueba aquí)
plan14 = {"cams": [_cam14("http://127.0.0.1:{0}/stream.mjpg".format(HTTP14))],
          "geom": camui.layout_tiles([4 / 3.0], [1], 320), "fps": 10, "sync": "clock",
          "crf": 28, "preset": "veryfast", "audio": {"L": None, "R": None, "offset": 0},
          "duration": 6}
cmd14, _ = camui.build_command(plan14, outfile=out14, preview=False, input_fn=camui._dshow_input)
try:
    r14 = subprocess.run(cmd14, capture_output=True, text=True, timeout=40)
    assert r14.returncode == 0, "el receptor se cayó con el cambio de tamaño:\n" + r14.stderr[-800:]
    nf14 = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                           "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out14],
                          capture_output=True, text=True, timeout=15).stdout.strip()
    assert nf14.isdigit() and int(nf14) >= 50, "muy pocos fotogramas en el receptor: " + nf14

    def _net_gray14(ts_):
        q_ = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(ts_), "-i", out14, "-frames:v", "1",
                             "-vf", "scale=1:1", "-f", "rawvideo",
                             "-pix_fmt", "gray", "-"], capture_output=True, timeout=15)
        return q_.stdout[0] if q_.stdout else None

    g_before, g_after = _net_gray14(1.5), _net_gray14(5.5)
    assert g_before is not None and g_before > 150, g_before
    assert g_after is not None and g_after < 120, ("tras el cambio de tamaño no siguió entrando "
                                                   "vídeo de la cámara de red", g_after)
finally:
    stop14.set()
    th14.join(10)
    s14.close()
    if os.path.exists(out14):
        os.remove(out14)
print("14) el receptor (comando real de cam-ui) sigue recibiendo, sin caerse, cuando el emisor "
      "cambia de tamaño a mitad ({0} fotogramas; gris {1} -> {2}) OK".format(nf14, g_before, g_after))

print("TODO OK unit_ipout")
