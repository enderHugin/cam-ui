"""Motor experimental «concentrador» (CamHub, pack 34): sin hardware, con procesos de ffmpeg,
sockets, IpServer y PhoneCamHub reales en 127.0.0.1.

Las cámaras DirectShow se simulan con un "driver" que capta a SU búfer desde que se abre (como
DirectShow: si nadie lee, se acumula) y tarda lo que se le diga en abrirse; cada fotograma lleva
en código de barras su número de captura en un reloj común (n = (t - T0) * 30 mod 256), así que
en la grabación se lee qué fotograma de cada cámara se ve en cada momento.

Por defecto, con el ffmpeg del sistema; CAMUI_FFMPEGS="ruta1:ruta2" añade otros (p. ej. un 7.x)."""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import io
import os
import sys
import time
import socket
import threading
import subprocess
from collections import deque

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

FFMPEGS = ["ffmpeg"] + [p for p in os.environ.get("CAMUI_FFMPEGS", "").split(":") if p]
W, H = 320, 240
T0 = time.time() + 0.5


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def barcode(n, w=W, h=H):
    fr = np.zeros((h, w), np.uint8)
    for b in range(8):
        if (n >> b) & 1:
            fr[:, b * w // 8:(b + 1) * w // 8] = 255
    return fr


def code_now():
    return int((time.time() - T0) * 30) % 256


def decode(gray, tile):
    x, y, w, h = tile
    row = gray[y + h // 2, x:x + w]
    n = 0
    for b in range(8):
        seg = row[b * w // 8 + w // 32:(b + 1) * w // 8 - w // 32]
        if seg.mean() > 128:
            n |= 1 << b
    return n


def unwrap(v):
    u = np.asarray(v).astype(int).copy()
    for i in range(1, len(u)):
        d = (u[i] - u[i - 1]) % 256
        u[i] = u[i - 1] + (d if d < 128 else d - 256)
    return u


def read_gray(path, w, h):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    return a[: (len(a) // (w * h)) * w * h].reshape(-1, h, w)


class FakeDriver:
    """Una cámara DirectShow de mentira: el ffmpeg de captura escucha en «port» (rawvideo gris)
    y esto se conecta (y se reconecta si ese ffmpeg se cierra), tarda open_delay en "abrirse" y
    capta a fps a su propio búfer. cut=(desde, duración) en s desde que capta: se calla."""

    def __init__(self, fps=30, open_delay=0.0, cut=None):
        self.port = free_port()
        self.fps, self.open_delay, self.cut = fps, open_delay, cut
        self.buf, self.maxbuf, self.opens = deque(), 0, 0
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def args(self):
        return ["-f", "rawvideo", "-pix_fmt", "gray", "-s", "{0}x{1}".format(W, H),
                "-framerate", str(self.fps), "-i", "tcp://127.0.0.1:{0}?listen=1".format(self.port)]

    def _run(self):
        while not self.stop.is_set():
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
            except OSError:
                time.sleep(0.01)
                continue
            self.opens += 1
            time.sleep(self.open_delay)
            self.buf.clear()
            alive = threading.Event()
            alive.set()
            threading.Thread(target=self._capture, args=(alive,), daemon=True).start()
            try:
                while not self.stop.is_set():
                    if not self.buf:
                        time.sleep(0.002)
                        continue
                    s.sendall(self.buf.popleft())
            except OSError:
                pass
            alive.clear()
            s.close()

    def _capture(self, alive):
        k, t0 = 0, time.time()
        while alive.is_set() and not self.stop.is_set():
            k += 1
            time.sleep(max(0, t0 + k / float(self.fps) - time.time()))
            el = time.time() - t0
            if self.cut and self.cut[0] <= el < self.cut[0] + self.cut[1]:
                continue
            self.buf.append(barcode(code_now()).tobytes())
            self.maxbuf = max(self.maxbuf, len(self.buf))


def cam(spec, **kw):
    c = {"spec": spec, "mode": None, "h": False, "v": False, "rot": 0, "delay": 0, "tile": "auto",
         "zoom": 100.0, "cx": 50.0, "cy": 50.0, "color": {}, "src": (W, H)}
    c.update(kw)
    return c


# ---- un "otro PC" con cam-ui: IpServer real cuyo reloj va SKEW s por delante del de aquí -------
class SkewedIpServer(camui.IpServer):
    skew = 0.0

    @property
    def _latest_t(self):
        return self.__dict__.get("_lt", 0.0)

    @_latest_t.setter
    def _latest_t(self, v):
        self.__dict__["_lt"] = v + self.skew if v else v


_orig_send_json = camui._MjpegHandler._send_json


def _skew_send_json(self, code, obj):
    if isinstance(obj, dict) and obj.get("camui") == 1:
        obj = dict(obj, t=obj["t"] + getattr(self.server.owner, "skew", 0.0))
    return _orig_send_json(self, code, obj)


camui._MjpegHandler._send_json = _skew_send_json

# =====================================================================================
# 1) piezas sueltas: tamaños yuv420p, reescalado, JPEG -> yuv (rango de vídeo), marcador
assert camui._yuv_len(4, 2) == 8 + 2 * 2 and camui._yuv_len(5, 3) == 15 + 2 * 3 * 2
y = np.arange(16 * 8, dtype=np.uint8).reshape(8, 16)
src_ = y.tobytes() + bytes(range(32)) + bytes(range(100, 132))
out_ = camui._yuv_resize(src_, 16, 8, 8, 4)
assert len(out_) == camui._yuv_len(8, 4)
yo, uo, vo = camui._yuv_planes(out_, 8, 4)
assert (yo == y[::2, ::2]).all() and uo[0, 0] == 0 and vo[0, 0] == 100
bufj = io.BytesIO()
Image.fromarray(np.concatenate([np.zeros((64, 64, 3), np.uint8), np.full((64, 64, 3), 255, np.uint8)], 1)
                ).save(bufj, "JPEG", quality=95)
wj, hj, yuvj = camui._jpeg_to_yuv(bufj.getvalue())
yj, uj, vj = camui._yuv_planes(yuvj, wj, hj)
assert (wj, hj) == (128, 64) and abs(int(yj[32, 10]) - 16) <= 2 and abs(int(yj[32, 120]) - 235) <= 2, \
    (yj[32, 10], yj[32, 120])
assert abs(int(uj[16, 5]) - 128) <= 2 and abs(int(vj[16, 60]) - 128) <= 2
big = io.BytesIO()
Image.new("RGB", (3840, 2160), (40, 90, 160)).save(big, "JPEG", quality=80)
t_ = time.time()
wb, hb, yb = camui._jpeg_to_yuv(big.getvalue(), camui.HUB_JPEG_MAX_SIDE)
assert max(wb, hb) <= camui.HUB_JPEG_MAX_SIDE and wb >= 960 and len(yb) == camui._yuv_len(wb, hb), (wb, hb)
ph = camui._hub_placeholder(320, 180, "Sin señal: 🌐 Cámara rara")
assert len(ph) == camui._yuv_len(320, 180)
py_ = np.frombuffer(ph, np.uint8, count=320 * 180).reshape(180, 320)
assert (py_ > 150).sum() > 200 and py_[5, 5] == 57          # hay texto, y el fondo es gris
print("1) piezas: yuv420p, reescalado, JPEG en rango de vídeo (4K reducido en {0:.0f} ms), "
      "marcador con texto OK".format((time.time() - t_) * 1000))

# 1b) quitar el temblor de la hora de llegada: una cámara de 30 fps cuya fase cae justo entre dos
#     tics del reloj de 30 fps, con ±4 ms de temblor (hilos, tuberías), simulada sin ffmpeg
import random  # noqa: E402


class _H1b:
    released_at, _freed = 0.0, {}


def _judder(fn, phase, jit, n=900, lost=()):
    rnd = random.Random(7)
    cap = [phase + i / 30.0 for i in range(n) if i not in lost]
    st = np.array([fn(c + 0.002 + rnd.uniform(-jit, jit)) for c in cap])
    idx = []
    for tg in np.arange(1.0, cap[-1] - 1.0, 1 / 30.0):
        j = np.searchsorted(st, tg, side="right") - 1
        if j + 1 < len(st) and st[j + 1] - tg < tg - st[j]:
            j += 1
        idx.append(j)
    d = np.diff(idx)
    return int((d == 0).sum()), st, cap


LOST1b = (300, 301, 600)                     # 3 fotogramas perdidos: 3 repeticiones inevitables
base1b = _judder(lambda t: t, 1 / 60.0, 0.0, lost=LOST1b)[0]
raw1b = _judder(lambda t: t, 1 / 60.0, 0.004, lost=LOST1b)[0]
dj1b, st1b, cap1b = _judder(camui._HubSource(_H1b(), ("x",), "x", "x").dejitter, 1 / 60.0, 0.004,
                            lost=LOST1b)
assert base1b == 3 and raw1b >= 30 and dj1b <= base1b + 3, (base1b, raw1b, dj1b)
assert abs(np.mean(st1b[100:] - np.array(cap1b[100:])) - 0.002) < 0.001     # sin sesgo
assert sum(1 for x, y in zip(st1b[1:], st1b[:-1]) if x <= y) == 0             # siempre en orden
print("1b) hora de llegada sin temblor: en la peor fase, {0} repeticiones en 30 s -> {1} (3 de "
      "ellas, fotogramas que de verdad se perdieron) OK".format(raw1b, dj1b))

# 2) build_command con el concentrador: cada cámara es una entrada cruda por TCP local, sin
#    marca de reloj al leer (la hora ya la puso el concentrador); el audio sigue igual
plan2 = {"sync": "clock", "fps": 25, "crf": 23, "preset": "ultrafast",
         "audio": {"L": {"spec": "Mic", "chan": "left", "gain": 100, "delay": 0}, "R": None, "offset": 0},
         "labels": ["A", "B"], "cams": [cam("A", _hub=True), cam("http://x/y.mjpg", _hub=True, delay=300)],
         "geom": camui.layout_tiles([4 / 3.0] * 2, [2], 640),
         "hub": {"ports": [41001, 41002], "sizes": [(640, 480), (320, 240)], "pix": "yuv420p"}}
cmd2, _m2 = camui.build_command(plan2, outfile="x.mkv", t0=1000.0)
s2 = " ".join(cmd2)
assert "-f rawvideo -pix_fmt yuv420p -s 640x480 -framerate 25 -i tcp://127.0.0.1:41001" in s2, s2
assert "-s 320x240 -framerate 25 -i tcp://127.0.0.1:41002" in s2
i_a = cmd2.index("tcp://127.0.0.1:41001")
assert "-use_wallclock_as_timestamps" not in cmd2[i_a - 12:i_a]
assert "[0:v]setpts=PTS-STARTPTS," in s2 and "[1:v]setpts=PTS-STARTPTS," in s2 and "RTCTIME" not in s2
assert "-use_wallclock_as_timestamps 1 -f dshow" in s2 and "-copyts" in s2          # el audio, igual
assert "audio=Mic" in s2
print("2) build_command: entradas crudas del concentrador, el audio como siempre OK")


def fake_input(drivers):
    def fn(kind, spec, opt=None):
        if spec in drivers:
            d = drivers[spec]
            if opt == "malo":
                return ["-i", "could not set video options"]     # como «Could not set video options»
            return d.args()
        return camui._dshow_input(kind, spec, opt)
    return fn


def two_pc_run(ff, record_s=13.0):
    """El caso del usuario: aquí una EOS (30 fps, búfer de driver), una USB lenta (10 fps, tarda
    1,4 s en abrirse), otra que se corta 3 s y la de otro PC (cam-ui con el concentrador, reloj
    543 ms por delante). Devuelve un dict con las medidas."""
    res = {}
    # --- el otro PC: su cámara y su cam-ui (motor nuevo) con salida «En la red» ---
    drvA = FakeDriver(30, 0.3)
    hubA = camui.CamHub(ff, input_fn=fake_input({"camA": drvA}))
    engA = camui.FFmpegEngine(ff)
    srvA = SkewedIpServer()
    srvA.skew = 0.543
    portA = free_port()
    srvA.open(W, H, fps=30, port=portA)
    engA.hub, engA.ipsrv = hubA, srvA
    planA = {"sync": "clock", "fps": 30, "crf": 23, "preset": "ultrafast",
             "audio": {"L": None, "R": None, "offset": 0}, "labels": ["camA"], "cams": [cam("camA")],
             "geom": {"W": W, "H": H, "tiles": [(0, 0, W, H)]}, "hub": True,
             "ipout": {"w": W, "h": H, "fps": 30, "pix": "rgb24"}}
    okA, errA = engA.start(planA)
    assert okA, errA
    res["lag_header"] = srvA.content_lag
    # --- este PC ---
    drv = {"eos": FakeDriver(30, 0.5), "lenta": FakeDriver(10, 1.4), "corte": FakeDriver(30, 1.4, cut=(6.0, 3.0))}
    hub = camui.CamHub(ff, input_fn=fake_input(drv))
    events = []
    hub.on_event = lambda k, lab, det: events.append((k, lab))
    eng = camui.FFmpegEngine(ff)
    eng.hub = hub
    url = "http://127.0.0.1:{0}/stream.mjpg".format(portA)
    labels = ["EOS", "lenta", "corte", "🌐 encadenada"]
    out = os.path.join(TMP, "hub_{0}.mkv".format(abs(hash(ff)) % 1000))
    if os.path.exists(out):
        os.remove(out)
    plan = {"sync": "clock", "fps": 30, "crf": 18, "preset": "ultrafast",
            "audio": {"L": None, "R": None, "offset": 0}, "labels": labels,
            # (la de otro PC, con su tamaño ya conocido, como la tiene guardada la aplicación tras
            # verla una vez: desde el pack 49 una forma distinta de la prevista se ve entera con
            # bandas negras -- antes, estirada -- hasta que la aplicación recoloca el mosaico, y
            # aquí, sin aplicación, nadie lo recolocaría y el código de barras no se leería)
            "cams": [cam("eos"), cam("lenta"), cam("corte"), cam(url, src=(W, H))],
            "geom": camui.layout_tiles([4 / 3.0] * 4, [4], 4 * W), "duration": record_s, "hub": True}
    t_s = time.time()
    ok, err = eng.start(plan, outfile=out)
    assert ok, err
    t_first = None
    while eng.is_running() and time.time() - t_s < record_s + 15:
        if t_first is None and eng.frame_count:
            t_first = time.time() - t_s
        time.sleep(0.05)
    res["first_preview_s"] = t_first
    srcs = hub.ensure(plan["cams"], labels)
    res["opened_s"] = [round(s.t_first - s.t_open, 2) if s.t_first else None for s in srcs]
    res["readers"] = [s.reader for s in srcs]
    res["fps"] = [s.fps() for s in srcs]
    res["D"] = eng.hub_delay
    res["events"] = list(events)
    res["eos_buf"] = drv["eos"].maxbuf
    res["opens"] = dict((k, d.opens) for k, d in drv.items())
    pids0 = sorted(p.pid for p in [s.proc for s in srcs] if p is not None)
    ids0 = [id(s) for s in srcs]
    # --- 3) reiniciar el mosaico (cambiar un ajuste, empezar a grabar) no reabre las cámaras ---
    plan_r = dict(plan, duration=None)
    plan_r["cams"] = [dict(c) for c in plan["cams"]]
    t_r = time.time()
    ok, err = eng.start(plan_r)
    assert ok, err
    while eng.frame_count == 0 and time.time() - t_r < 5:
        time.sleep(0.02)
    res["restart_first_s"] = time.time() - t_r      # desde que se pide hasta la primera imagen
    srcs2 = hub.ensure(plan_r["cams"], labels)
    res["same_sources"] = [id(s) for s in srcs2] == ids0
    res["same_pids"] = sorted(p.pid for p in [s.proc for s in srcs2] if p is not None) == pids0
    res["opens_after_restart"] = dict((k, d.opens) for k, d in drv.items())
    # --- 4) soltar: no queda ningún proceso de captura y los "drivers" quedan libres ---
    procs = [s.proc for s in srcs2 if s.proc is not None]
    eng.release_devices()
    res["procs_alive_after_release"] = sum(1 for p in procs if p.poll() is None)
    res["hub_empty"] = not hub.sources and hub.session is None
    engA.release_devices()
    srvA.close()
    for d in list(drv.values()) + [drvA]:
        d.stop.set()
    # --- la grabación ---
    g = plan["geom"]
    a = read_gray(out, g["W"], g["H"])
    codes = np.array([[decode(f, t) for t in g["tiles"]] for f in a])
    res["frames"] = len(codes)
    c = codes[60:]                       # fuera el arranque
    n = len(c)
    u = [unwrap(c[:, i]) for i in range(4)]
    res["upd"] = [round((np.diff(x) != 0).sum() / (n / 30.0), 1) for x in u]
    res["eos_jump"] = int(np.max(np.diff(u[0])))
    d_ch = ((c[:, 0].astype(int) - c[:, 3].astype(int) + 128) % 256) - 128
    res["chain_minus_eos_ms"] = int(np.median(d_ch) * 33.3)
    # durante el corte de «corte» (6..9 s de su captura), la EOS sigue a 30
    return res


TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "t")
os.makedirs(TMP, exist_ok=True)
for ff in FFMPEGS:
    ver = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.split("\n")[0][:40]
    r = two_pc_run(ff)
    print("   ({0}) {1}".format(ver, r))
    # todas a la vez: cada una tarda lo suyo (0,5 / 1,4 / 1,4 s), no la suma (3,3 s)
    assert all(o is not None and o < 2.3 for o in r["opened_s"][:3]), r["opened_s"]
    assert r["readers"] == ["dshow", "dshow", "dshow", "camui"], r["readers"]
    # ninguna frena a las demás: la EOS a 30 (y su búfer de driver vacío), la lenta a 10, la
    # encadenada a 30 -- con un corte de 3 s en otra cámara a mitad de grabación
    assert r["upd"][0] >= 29.0 and r["eos_jump"] <= 2 and r["eos_buf"] <= 4, r
    assert 8.5 <= r["upd"][1] <= 11, r["upd"]
    # (aquí los "dos PCs" comparten 2 CPU: el emisor pierde alguno al codificar; con el motor de
    # siempre, la encadenada bajaba a 10-15 por culpa de la lenta)
    assert r["upd"][3] >= 20.0, r["upd"]
    # la de otro PC (reloj 543 ms adelantado) cae a la vez que las de aquí: ±2 fotogramas
    assert abs(r["chain_minus_eos_ms"]) <= 70, r["chain_minus_eos_ms"]
    # la que se calló 3 s no obligó a reabrir nada (ni ella ni las demás)
    assert r["opens"] == {"eos": 1, "lenta": 1, "corte": 1}, r["opens"]
    print("3) ({0}) concentrador: abren a la vez (s: {1}), EOS {2}/s sin acumular búfer, lenta "
          "{3}/s, encadenada {4}/s a {5:+d} ms de la EOS con relojes a 543 ms, un corte de 3 s no "
          "frena a nadie OK".format(ver, r["opened_s"][:3], r["upd"][0], r["upd"][1], r["upd"][3],
                                    r["chain_minus_eos_ms"]))
    assert r["same_sources"] and r["same_pids"] and r["opens_after_restart"] == r["opens"], r
    assert r["restart_first_s"] < 1.0, r["restart_first_s"]
    print("4) ({0}) reiniciar el mosaico no reabre ninguna cámara (primera imagen en {1:.2f} s) "
          "OK".format(ver, r["restart_first_s"]))
    assert r["procs_alive_after_release"] == 0 and r["hub_empty"], r
    print("5) ({0}) release_devices: sin procesos de captura, cámaras libres OK".format(ver))

# 6) control, motor de siempre con las mismas cámaras: las abre una detrás de otra
drv6 = {"eos": FakeDriver(30, 0.5), "lenta": FakeDriver(10, 1.4), "corte": FakeDriver(30, 1.4)}
eng6 = camui.FFmpegEngine("ffmpeg")
plan6 = {"sync": "clock", "fps": 30, "crf": 23, "preset": "ultrafast",
         "audio": {"L": None, "R": None, "offset": 0}, "labels": ["EOS", "lenta", "corte"],
         "cams": [cam("eos"), cam("lenta"), cam("corte")], "geom": camui.layout_tiles([4 / 3.0] * 3, [3], 3 * W)}
t6 = time.time()
ok6, err6 = eng6.start(plan6, input_fn=fake_input(drv6))
assert ok6, err6
while eng6.frame_count == 0 and time.time() - t6 < 15:
    time.sleep(0.02)
first6 = time.time() - t6
eng6.stop()
for d in drv6.values():
    d.stop.set()
assert first6 >= 3.0, first6
print("6) control, motor de siempre: primera imagen a los {0:.1f} s (cámaras una tras otra)".format(first6))

# 7) una cámara que no abre: avisa una vez, sigue reintentando, «Sin señal» en su sitio, y las
#    demás tan tranquilas; un modo que la cámara rechaza pasa al de fábrica
drv7 = {"ok": FakeDriver(30, 0.2), "rara": FakeDriver(30, 0.2)}
hub7 = camui.CamHub("ffmpeg", input_fn=fake_input(drv7))
ev7 = []
hub7.on_event = lambda k, lab, det: ev7.append((k, lab, det))
srcs7 = hub7.ensure([cam("ok"), cam("no-existe-xyz", mode=None), cam("rara", mode="malo")],
                    ["ok", "fantasma", "rara"])
time.sleep(4.5)
slots7 = [{"src": s, "w": W, "h": H, "delay": 0.0, "label": s.label} for s in srcs7]
ports7 = hub7.start_session(slots7, 30, 0.0, time.time())
conns = [socket.create_connection(("127.0.0.1", p)) for p in ports7]
time.sleep(0.5)
got7 = []
for cn in conns:
    b = b""
    n7 = camui._yuv_len(W, H)
    while len(b) < n7:
        b += cn.recv(n7 - len(b))
    got7.append(np.frombuffer(b, np.uint8, count=W * H).reshape(H, W))
assert srcs7[0].state == "live" and srcs7[1].state == "down" and srcs7[2].state == "live", \
    [s.state for s in srcs7]
assert [e[:2] for e in ev7 if e[0] == "down"] == [("down", "fantasma")], ev7
assert ("badmode", "rara", "malo") in ev7 and srcs7[2].mode == "", (ev7, srcs7[2].mode)
assert got7[1][5, 5] == 57 and (got7[1] > 150).sum() > 100       # «Sin señal: fantasma»
for cn in conns:
    cn.close()
hub7.close()
for d in drv7.values():
    d.stop.set()
print("7) cámara que no abre: un aviso, «Sin señal» en su sitio y se reintenta sola; un modo "
      "rechazado pasa al de fábrica OK")

# 8) cambiar el modo de una cámara: la fuente vieja se cierra y la nueva espera a que el driver
#    la suelte antes de abrirla (DirectShow no deja abrir dos veces la misma)
drv8 = {"c": FakeDriver(30, 0.1)}
hub8 = camui.CamHub("ffmpeg", input_fn=fake_input(drv8))
a8 = hub8.ensure([cam("c", mode=None)], ["c"])[0]
while a8.state != "live":
    time.sleep(0.05)
b8 = hub8.ensure([cam("c", mode="640x480@30 mjpeg")], ["c"])[0]
assert b8 is not a8 and b8.after is a8 and a8.closed.is_set()
t8 = time.time()
while b8.state != "live" and time.time() - t8 < 8:
    time.sleep(0.05)
assert a8.done.is_set() and b8.state == "live" and b8.t_open >= t8 - 0.01, (a8.done.is_set(), b8.state)
# soltarla es inmediato (su ffmpeg no se queda bloqueado escribiendo lo que ya nadie lee: antes,
# 2 s hasta matarlo) + RELEASE_GAP para que el driver la suelte
assert b8.t_open - t8 < camui.RELEASE_GAP + 0.8, b8.t_open - t8
hub8.close()
drv8["c"].stop.set()
print("8) cambio de modo: la nueva espera a que se suelte la vieja ({0:.1f} s) OK".format(b8.t_open - t8))

# 9) «Cámara del móvil» directa: los JPEG que llegan a PhoneCamHub entran al concentrador sin
#    servidor local ni otro ffmpeg, con su hora de llegada
pc = camui.PhoneCamHub(deque(), deque())
pc.start(port=free_port())
try:
    sid = pc.new_session("Móvil")
    spec9 = "http://127.0.0.1:{0}/phonecam/{1}.mjpg".format(pc.local_port, sid)
    hub9 = camui.CamHub("ffmpeg", phonecam=pc)
    s9 = hub9.ensure([cam(spec9)], ["📱 Móvil"])[0]
    assert isinstance(s9, camui._PhoneSource) and s9.reader == "móvil"
    stop9 = threading.Event()
    lat9 = []

    def feed9():
        k = 0
        while not stop9.is_set():
            b = io.BytesIO()
            Image.fromarray(barcode(k % 256, 640, 360)).convert("RGB").save(b, "JPEG", quality=85)
            pc.feed(sid, b.getvalue())
            k += 1
            time.sleep(1 / 30.0)
    threading.Thread(target=feed9, daemon=True).start()
    time.sleep(2.5)
    it9 = s9.pick(time.time())
    assert it9 is not None and (it9[1], it9[2]) == (640, 360) and s9.state == "live"
    f9 = s9.fps()
    assert f9 and 25 <= f9 <= 33, f9
    assert time.time() - it9[0] < 0.2
    stop9.set()
    hub9.close()
finally:
    pc.stop()
print("9) «Cámara del móvil» directa al concentrador: {0:.0f} fps, sin servidor ni ffmpeg de por "
      "medio OK".format(f9))

# 10) un móvil que manda 4K se decodifica ya reducido (si no, en Python iría a saltos)
assert camui.HUB_JPEG_MAX_SIDE == 1920
print("10) JPEG 4K -> {0}x{1} OK".format(wb, hb))

# 11) CamApp con el concentrador (sin Tk: un "self" de mentira que hereda los métodos de verdad)
class _StubSrc(camui._HubSource):
    def _run(self):
        self.closed.wait()


def _stub(hub_, name, size, fps):
    s_ = _StubSrc(hub_, ("dshow", name, ""), name, name)
    s_.size, s_.t_first, s_.state = size, time.time() - 5, "live"
    now_ = time.time()
    for i in range(int(fps * 1.5)):
        s_._arr.append(now_ - 1.5 + i / float(fps))
    return s_


class _Cs11:
    def __init__(self, src):
        self.src = src


class _App11(camui.CamApp):
    def __init__(self, eng, hub_, cams):
        self.eng, self.hub, self.cams = eng, hub_, cams
        self._run_labels = list(cams.keys())
        self.previewing, self.recording, self.editing, self.busy = True, False, False, False
        self._slow_warned, self._hub_restart_t = set(), 0.0
        self.calls = {"status": [], "save": 0, "on_change": 0, "restart": 0, "bg": [], "preflight": 0,
                      "chain": [], "start": 0}

    def _set_status(self, txt):
        self.calls["status"].append(txt)

    def save_config(self):
        self.calls["save"] += 1

    def _on_change(self, *_a):
        self.calls["on_change"] += 1

    def _schedule_restart(self, *a, **kw):
        self.calls["restart"] += 1

    def _bg(self, fn, done):
        self.calls["bg"].append(fn)

    def _auto_modes(self, plan):
        pass

    def _preflight_net_cams(self, plan):
        self.calls["preflight"] += 1

    def _chain_net_cams(self, plan):
        self.calls["chain"].append(plan.get("sync"))


hub11 = camui.CamHub("ffmpeg")
sa, sb = _stub(hub11, "A", (1280, 720), 30), _stub(hub11, "B", (640, 480), 10)
hub11.start_session([{"src": sa, "w": 1280, "h": 720, "delay": 0.0, "label": "A"},
                     {"src": sb, "w": 1280, "h": 720, "delay": 0.0, "label": "B"}], 30, 0.05, time.time())
eng11 = camui.FFmpegEngine()
eng11.hub, eng11.hub_on, eng11.frame_count = hub11, True, 1
app11 = _App11(eng11, hub11, {"A": _Cs11((1280, 720)), "B": _Cs11((1280, 720))})
app11._check_sources()
assert app11.cams["B"].src == (640, 480) and app11.cams["A"].src == (1280, 720)
assert app11.calls["on_change"] == 1 and app11.calls["save"] == 1, app11.calls
assert any("«B» solo da 10 fps" in t_ and "no se frena" in t_ for t_ in app11.calls["status"]), app11.calls["status"]
app11b = _App11(eng11, hub11, {"A": _Cs11((1280, 720)), "B": _Cs11((1280, 720))})
app11b.recording = True
app11b._check_sources()
assert app11b.calls["on_change"] == 0, "grabando no se recoloca (se ve reescalada hasta parar)"
print("11a) llega otro tamaño: se recoloca el mosaico (sin reabrir nada); la lenta avisa sin "
      "decir que frena OK")
sb.need = lambda: 0.4                    # p. ej. la de otro PC llega más tarde que D
app11._hub_restart_t = 0.0               # (el recolocado de arriba también cuenta como reinicio)
app11._hub_check_delay()
assert app11.calls["restart"] == 1
app11._hub_check_delay()
assert app11.calls["restart"] == 1, "no reinicia en bucle"
app11c = _App11(eng11, hub11, {"A": _Cs11(None)})
app11c.recording = True
app11c._hub_check_delay()
assert app11c.calls["restart"] == 0, "grabando nunca (cambiaría la sincronía con el audio)"
print("11b) la de otro PC llega más tarde que D: un reinicio con D mayor, nunca grabando OK")
app11._stop_preview()
assert app11.calls["bg"] == [eng11.release_devices], app11.calls["bg"]
print("11c) pausar la vista previa suelta también las cámaras del concentrador OK")


class _Eng11d:
    def __init__(self):
        self.calls, self._abort = [], False

    def stop(self):
        self.calls.append("stop")

    def start(self, plan, **kw):
        self.calls.append(("start", bool(plan.get("hub"))))
        return True, ""


app11d = _App11(_Eng11d(), hub11, {})
app11d._chain_info = {"x": 1}
r11d = app11d._engine_start({"hub": True, "sync": "clock", "cams": [], "labels": []})
assert r11d == (True, "") and app11d.eng.calls == ["stop", ("start", True)], app11d.eng.calls
assert app11d.calls["preflight"] == 0 and app11d.calls["chain"] == ["-"] and app11d._chain_info == {}
app11e = _App11(_Eng11d(), hub11, {})
app11e._engine_start({"sync": "clock", "cams": [], "labels": []})
assert app11e.calls["preflight"] == 1 and app11e.calls["chain"] == ["clock"]
hub11.close()
print("11d) _engine_start con el concentrador: sin prueba previa de red ni intermediarios; sin "
      "él, como siempre OK")


# 12) imagen y sonido siguen juntos con el concentrador (el audio aún lo lee el ffmpeg del mosaico,
#     con «reloj común»; t0 = el instante cuya imagen es el fotograma 0): una cámara que destella
#     100 ms cada 2 s y un "micrófono" que pita a la vez, en el mismo reloj real
def av_run(ff):
    SR = 48000
    stop_ = threading.Event()
    PV_, PA_ = free_port(), free_port()
    T0_ = time.time() + 1.0
    flash = lambda t: t > T0_ and ((t - T0_) % 2.0) < 0.1

    def vid():
        so = None
        while so is None and not stop_.is_set():
            try:
                so = socket.create_connection(("127.0.0.1", PV_), timeout=1)
            except OSError:
                time.sleep(0.01)
        k, t0 = 0, time.time()
        while not stop_.is_set():
            k += 1
            time.sleep(max(0, t0 + k / 30.0 - time.time()))
            try:
                so.sendall(bytes([255 if flash(time.time()) else 0]) * (W * H))
            except OSError:
                return

    def aud():
        so = None
        while so is None and not stop_.is_set():
            try:
                so = socket.create_connection(("127.0.0.1", PA_), timeout=1)
            except OSError:
                time.sleep(0.005)
        n, k, t0 = SR // 100, 0, time.time()
        while not stop_.is_set():
            k += 1
            time.sleep(max(0, t0 + k * 0.01 - time.time()))
            tt = t0 + (k - 1) * 0.01 + np.arange(n) / float(SR)
            on = np.array([flash(x) for x in tt])
            a = (np.sin(2 * np.pi * 1000 * tt) * 12000 * on).astype(np.int16)
            try:
                so.sendall(np.repeat(a[:, None], 2, 1).tobytes())
            except OSError:
                return
    threading.Thread(target=vid, daemon=True).start()
    threading.Thread(target=aud, daemon=True).start()

    def inp(kind, spec, opt=None):
        if kind == "video":
            return ["-f", "rawvideo", "-pix_fmt", "gray", "-s", "{0}x{1}".format(W, H), "-framerate", "30",
                    "-i", "tcp://127.0.0.1:{0}?listen=1".format(PV_)]
        return ["-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "tcp://127.0.0.1:{0}?listen=1".format(PA_)]
    eng_ = camui.FFmpegEngine(ff)
    eng_.hub = camui.CamHub(ff, input_fn=inp)
    plan_ = {"sync": "clock", "fps": 30, "crf": 23, "preset": "ultrafast", "hub": True, "duration": 11,
             "audio": {"L": {"spec": "mic", "chan": "left", "gain": 100, "delay": 0},
                       "R": {"spec": "mic", "chan": "right", "gain": 100, "delay": 0}, "offset": 0, "buf": 50},
             "labels": ["cam"], "cams": [cam("cam")], "geom": {"W": W, "H": H, "tiles": [(0, 0, W, H)]}}
    out_ = os.path.join(TMP, "hub_av.mkv")
    if os.path.exists(out_):
        os.remove(out_)
    ok_, err_ = eng_.start(plan_, outfile=out_, warmup=1.0, input_fn=inp)
    assert ok_, err_
    t_ = time.time()
    while eng_.is_running() and time.time() - t_ < 30:
        time.sleep(0.1)
    eng_.release_devices()
    stop_.set()
    v_ = subprocess.run(["ffmpeg", "-v", "error", "-i", out_, "-vf", "scale=8:8", "-f", "rawvideo",
                         "-pix_fmt", "gray", "-"], capture_output=True).stdout
    v_ = np.frombuffer(v_, np.uint8).reshape(-1, 64).mean(1)
    a_ = subprocess.run(["ffmpeg", "-v", "error", "-i", out_, "-ac", "1", "-f", "s16le", "-ar", str(SR), "-"],
                        capture_output=True).stdout
    env_ = np.convolve(np.abs(np.frombuffer(a_, np.int16).astype(float)), np.ones(48) / 48, "same") > 3000
    vt_ = [i / 30.0 for i in range(1, len(v_)) if v_[i] > 128 >= v_[i - 1]]
    at_ = [i / float(SR) for i in np.flatnonzero(env_[1:] & ~env_[:-1]) + 1]
    return [int(round((min(at_, key=lambda y: abs(y - x)) - x) * 1000)) for x in vt_
            if at_ and min(abs(y - x) for y in at_) < 0.8]


for ff in FFMPEGS:
    ver = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.split()[2]
    d12 = av_run(ff)
    assert len(d12) >= 4, d12
    med12 = sorted(d12)[len(d12) // 2]
    assert abs(med12) <= 80 and max(d12) - min(d12) <= 40, d12
    print("12) (ffmpeg {0}) con el concentrador, el sonido va a {1:+d} ms de la imagen (mediana de "
          "{2} destellos) OK".format(ver, med12, len(d12)))
print("TODO OK unit_hub")
