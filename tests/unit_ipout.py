"""Salida en red (verlo desde otro equipo/móvil): IpServer de verdad (socket local + servidor
HTTP MJPEG propio) contra un emisor de verdad (un socket que manda fotogramas, como haría
ffmpeg) y peticiones HTTP de verdad. No usa ffmpeg: lo que hace falta probar es el servidor
HTTP propio, no el "tcp://" de ffmpeg (ese mecanismo ya lo prueba unit_update.py/la cámara
virtual en producción; aquí el cliente lo simula un socket normal)."""
import sys, os, io, time, socket, threading, http.client
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
print("1) build_command (salida «ip») OK")

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
conn.request("GET", "/")
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
conn2.request("GET", "/")
resp2 = conn2.getresponse()
assert resp2.status == 200
conn2.close()                                  # desconexión brusca, sin leer el cuerpo
time.sleep(0.3)
assert srv.active, "el servidor se cayó al desconectarse un visor"

conn3 = http.client.HTTPConnection("127.0.0.1", HTTP_PORT, timeout=5)
conn3.request("GET", "/")
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
conn6.request("GET", "/")
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

print("TODO OK unit_ipout")
