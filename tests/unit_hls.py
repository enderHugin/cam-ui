"""HLS real para el botón nativo de Chromecast de «En la red» (ver IpServer.hls_start() en
camui.py): necesita ffmpeg de verdad (con libx264 y el muxer hls) en el PATH -- igual que
geom_test.py y los demás que arrancan ffmpeg. Arranca un IpServer real, le manda fotogramas
crudos de verdad por el socket (igual que haría ffmpeg en producción), y pide /cast.m3u8 y un
trozo .ts por HTTP de verdad -- sin mocks en ningún punto. El trozo descargado se comprueba con
ffprobe (programa aparte, no el propio camui.py) para confirmar que de verdad lleva vídeo H.264,
no solo que «tiene buena pinta»."""
import os
import re
import sys
import time
import socket
import threading
import subprocess
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 320, 240, 10
PORT = 58403
srv = camui.IpServer()
srv.open(W, H, fps=FPS, port=PORT)
sock_port = srv.arm()

stop = threading.Event()
n_sent = [0]


def _sender():
    s = socket.create_connection(("127.0.0.1", sock_port), timeout=5)
    try:
        while not stop.is_set():
            frame = np.zeros((H, W, 3), np.uint8)
            t = n_sent[0]
            frame[:, :, 0] = (t * 3) % 256
            y0 = max(0, (t * 4) % H - 20)
            frame[y0:(t * 4) % H, :, 1] = 255          # una banda que se mueve: no todo plano
            s.sendall(frame.tobytes())
            n_sent[0] += 1
            time.sleep(1.0 / FPS)
    except Exception as e:
        print("sender terminó:", e)


threading.Thread(target=_sender, daemon=True).start()
time.sleep(0.5)


def _get(path, timeout=3):
    return urllib.request.urlopen("http://127.0.0.1:{0}{1}".format(PORT, path), timeout=timeout)


# 1) el MJPEG de siempre sigue funcionando exactamente igual (el HLS es un añadido aparte)
with _get("/stream.mjpg", timeout=5) as r:
    assert r.status == 200
    assert "multipart" in r.headers.get("Content-Type", "")
    assert len(r.read(2000)) > 0
print("1) /stream.mjpg sigue funcionando sin que lo toque nada de esto OK")

# 2) /cast.m3u8: la primera vez arranca el ffmpeg de HLS solo (lo dispara la propia petición,
#    como haría el Chromecast al recibir la orden de reproducir) -- puede tardar unos segundos
#    en tener trozos de verdad, así que se reintenta ante 503 (Retry-After), como haría un
#    cliente HLS real
t0 = time.time()
body = None
while time.time() - t0 < 25:
    try:
        with _get("/cast.m3u8", timeout=12) as r:
            if r.status == 200:
                body = r.read().decode("utf-8")
                break
    except urllib.error.HTTPError:
        pass
    time.sleep(1)
assert body, "la lista de HLS nunca llegó a estar lista"
assert "#EXTM3U" in body, body
n_segs = body.count("#EXTINF")
assert n_segs >= 2, body
print("2) /cast.m3u8 arranca el ffmpeg de HLS solo (lazy) y sirve una lista con {0} trozos "
      "tras {1:.1f}s OK".format(n_segs, time.time() - t0))

# 3) las rutas de los trozos cuelgan de /cast/ (gracias a -hls_base_url), no exponen la carpeta
#    temporal real del sistema de archivos
seg_urls = re.findall(r"(/cast/seg_\d+\.ts)", body)
assert seg_urls, "no se encontraron rutas /cast/seg_*.ts en la lista:\n" + body
print("3) las rutas de los trozos son /cast/seg_*.ts, sin exponer la carpeta temporal real OK")

# 4) un trozo de verdad: cabecera correcta, byte de sincronía 0x47 de MPEG-TS, y ffprobe (aparte
#    de camui.py) confirma que es vídeo H.264 de verdad, no basura
seg_file = os.path.join(HERE, "_unit_hls_seg_test.ts")
with _get(seg_urls[0], timeout=5) as r:
    assert r.status == 200
    assert r.headers.get("Content-Type") == "video/mp2t", r.headers.get("Content-Type")
    data = r.read()
assert len(data) > 500, len(data)
assert data[0] == 0x47, "no empieza por el byte de sincronía 0x47 de MPEG-TS: %r" % data[:4]
with open(seg_file, "wb") as f:
    f.write(data)
try:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type",
                          "-of", "csv=p=0", seg_file], capture_output=True, text=True, timeout=10)
finally:
    os.remove(seg_file)
assert "h264" in out.stdout and "video" in out.stdout, \
    "ffprobe no ve vídeo H.264 en el trozo descargado: {0} / {1}".format(out.stdout, out.stderr)
print("4) un trozo descargado es un .ts real con vídeo H.264 de verdad (confirmado por "
      "ffprobe) OK")

# 5) sin nadie pidiendo trozos durante un rato, se apaga solo (se fuerza el umbral para no
#    esperar los HLS_IDLE_SECONDS de verdad)
assert srv._hls_proc is not None and srv._hls_proc.poll() is None, \
    "el ffmpeg de HLS debería seguir vivo en este punto"
old_dir = srv._hls_dir
srv._hls_last_req = time.time() - 9999
srv.hls_idle_check()
t0 = time.time()
while srv._hls_proc is not None and time.time() - t0 < 5:
    time.sleep(0.1)
assert srv._hls_proc is None, "el ffmpeg de HLS debería haberse parado por inactividad"
time.sleep(0.3)
assert not os.path.isdir(old_dir), "la carpeta temporal de HLS debería quedar borrada"
print("5) sin nadie pidiendo trozos, el ffmpeg de HLS se para solo y borra su carpeta OK")

# 6) tras pararse, una nueva petición lo vuelve a arrancar desde cero y funciona igual
t0 = time.time()
body2 = None
while time.time() - t0 < 25:
    try:
        with _get("/cast.m3u8", timeout=12) as r:
            if r.status == 200:
                body2 = r.read().decode("utf-8")
                break
    except urllib.error.HTTPError:
        pass
    time.sleep(1)
assert body2 and body2.count("#EXTINF") >= 2, body2
assert srv._hls_dir and srv._hls_dir != old_dir, "debería ser una carpeta NUEVA, no la de antes"
print("6) tras pararse por inactividad, una nueva petición lo arranca otra vez y funciona OK")

# 7) IpServer.release() (apagar «En la red» del todo) para también el ffmpeg de HLS y limpia
#    su carpeta, sin dejar nada huérfano de fondo
dir2, proc2 = srv._hls_dir, srv._hls_proc
srv.release()
assert srv._hls_proc is None
assert proc2.poll() is not None, "el ffmpeg de HLS debería haber terminado al llamar release()"
assert not os.path.isdir(dir2), "release() debería limpiar también la carpeta temporal de HLS"
print("7) release() para el ffmpeg de HLS y limpia su carpeta, sin dejar nada de fondo OK")

stop.set()
print("TODO OK unit_hls")
