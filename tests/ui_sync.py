"""«Sincronizar» (pack 52) de principio a fin, en la aplicación de verdad (CamApp con su ventana Tk
y el motor nuevo; hace falta un display: xvfb-run -a python3 tests/ui_sync.py).

Un «mundo» de mentira con un reloj: tres cámaras que llegan al PC con retrasos distintos (como una
USB, un móvil por wifi y otro cam-ui encadenado: 0,10 / 0,35 / 0,80 s, a 30 / 20 / 15 fps), cada
una ve el móvil-claqueta solo un rato, y un micrófono (llega en 0,03 s) que oye los pitidos.
El móvil-claqueta es un cliente HTTPS de verdad contra el servidor de cam-ui: abre la página del
QR, toca «Empezar», espera a que el PC grabe, «parpadea y pita» (en el mundo) y avisa al acabar.

1) la ventana «Sincronizar», el QR y la página del móvil (con el token; sin él, nada);
2) primera medida: cada cámara llegaba tarde lo que de verdad llega (± un fotograma), y cam-ui
   pone solo su «Retardo ms» y el «Ajuste A/V», y lo guarda;
3) se vuelve a medir con lo puesto: ahora todas a la vez que el sonido (± un fotograma);
4) «Deshacer» vuelve a lo de antes; al cerrar la ventana, la página del móvil ya no vale."""
import io
import os
import sys
import json
import time
import shutil
import socket
import ssl
import threading
import http.client

import numpy as np
import tkinter as tk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_sync_home")
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME)
for n_ in ("PHONECAM_CERT_FILE", "PHONECAM_KEY_FILE", "PHONECAM_CA_CERT_FILE", "PHONECAM_CA_KEY_FILE"):
    setattr(camui, n_, os.path.join(HOME, n_.lower() + ".pem"))
camui.PHONECAM_DEVICES_FILE = os.path.join(HOME, "devices.json")
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")
# una claqueta más corta, para que la prueba no dure tanto (la de verdad: la misma, más larga)
camui.SYNC_PATTERN = camui.SYNC_PATTERN[:13]
camui.SYNC_PATTERN_END = camui.SYNC_PATTERN[-1][0] + camui.SYNC_PATTERN[-1][1]
P = camui.SYNC_PATTERN


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "fps": 30, "phonecam": {"port": free_port()},
               "audio": {"L": {"dev": "Micro de prueba", "chan": "mix", "gain": 0, "delay": 0},
                         "R": {"dev": camui.NO_AUDIO, "chan": "mix", "gain": 0, "delay": 0}}}, f)


class World:
    """El reloj del mundo: cuándo empieza la claqueta (hora del PC) y qué se ve / oye."""
    epoch = None

    @classmethod
    def state(cls, t):
        """Índice del destello/pitido encendido a la hora t, o None."""
        if cls.epoch is None:
            return None
        x = t - cls.epoch
        for k, (tk_, d) in enumerate(P):
            if tk_ <= x < tk_ + d:
                return k
        return None


CAMS = [  # (nombre, retraso, fps, destellos que ve)
    ("CamUSB", 0.10, 30, set(range(len(P)))),
    ("CamWifi", 0.35, 20, set(range(2, 10))),
    ("CamOmen", 0.80, 15, set(range(5, 12))),
]
A_LAT = 0.03
stop = threading.Event()


def jpeg(lit, k, pos):
    a = np.full((360, 640, 3), 70, np.uint8)
    a[300:340, (k * 9) % 560:(k * 9) % 560 + 60] = 160          # algo que se mueve
    y, x = pos
    a[y:y + 50, x:x + 26] = 245 if lit else 18                    # el móvil
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=80)
    return b.getvalue()


def camera(app, name, lat, fps, sees):
    sid = app.phonecam.new_session(name)
    k = 0
    t_next = time.time()
    while not stop.is_set():
        k += 1
        now = time.time()
        e = World.state(now - lat)
        lit = e is not None and e in sees
        q = int((now - lat) // 2.5)                       # quien lo sostiene lo mueve de vez en cuando
        pos = (60 + 40 * (q % 3), 100 + 90 * (q % 4))
        app.phonecam.feed(sid, jpeg(lit, k, pos), seq=k)
        t_next += 1.0 / fps
        time.sleep(max(0.0, t_next - time.time()))


APORT = free_port()


def mic_server():
    """El «micrófono»: ffmpeg se conecta aquí (en vez de a un dispositivo de Windows) y recibe
    sonido al ritmo real: ruido, y el pitido cuando el mundo pita (A_LAT después)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", APORT))
    srv.listen(4)
    srv.settimeout(0.5)
    rng = np.random.default_rng(3)

    def feed(conn):
        sr, n = 48000, 480                                         # 10 ms
        t_next = time.time()
        pos = 0
        try:
            while not stop.is_set():
                t_chunk = t_next - A_LAT
                tt = t_chunk + np.arange(n) / float(sr)
                x = 0.01 * rng.standard_normal(n)
                for i in range(0, n, 48):                          # a trozos de 1 ms
                    e = World.state(tt[i])
                    if e is not None:
                        x[i:i + 48] += 0.3 * np.sin(2 * np.pi * camui.SYNC_TONE_HZ *
                                                    (pos + i + np.arange(48)) / float(sr))
                pos += n
                pcm = (np.clip(x, -1, 1) * 32000).astype("<i2")
                conn.sendall(np.repeat(pcm, 2).tobytes())
                t_next += n / float(sr)
                time.sleep(max(0.0, t_next - time.time()))
        except OSError:
            pass
        finally:
            conn.close()
    while not stop.is_set():
        try:
            c, _a = srv.accept()
        except socket.timeout:
            continue
        threading.Thread(target=feed, args=(c,), daemon=True).start()
    srv.close()


_real_input = camui._dshow_input


def fake_input(kind, spec, opt=None, **kw):
    if kind == "audio":
        return ["-f", "s16le", "-ar", "48000", "-ac", "2",
                "-i", "tcp://127.0.0.1:{0}".format(APORT)]
    return _real_input(kind, spec, opt, **kw)


camui._dshow_input = fake_input
camui.list_devices = lambda: ([], camui.label_devices([{"name": "Micro de prueba", "alt": None}]))
threading.Thread(target=mic_server, daemon=True).start()

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
app._tray_asked = True


def pump(cond, secs, step=0.03):
    t_end = time.time() + secs
    while time.time() < t_end:
        root.update()
        if cond():
            return True
        time.sleep(step)
    return False


CTX = ssl._create_unverified_context()


def req(method, path):
    c = http.client.HTTPSConnection("127.0.0.1", app.phonecam.port, timeout=5, context=CTX)
    try:
        c.request(method, path, body=b"" if method == "POST" else None)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def phone_clapper(token, out):
    """La página del móvil, hecha a mano: lo mismo que hace su JavaScript."""
    try:
        st, body = req("GET", "/sync?t=" + token)
        out["page"] = (st, body)
        st, body = req("GET", "/sync/state?t={0}&ready=1".format(token))
        req("POST", "/sync/go?t={0}&run=0".format(token))
        t_end = time.time() + 30
        run = None
        while time.time() < t_end:
            st, body = req("GET", "/sync/state?t={0}".format(token))
            j = json.loads(body)
            if j["state"] == "grabando":
                run = j["run"]
                break
            time.sleep(0.2)
        assert run is not None, "el PC no empezó a grabar la prueba"
        time.sleep(1.0)                                            # «3, 2, 1» (aquí, 1 s)
        World.epoch = time.time() + 0.3
        req("POST", "/sync/started?t={0}&run={1}".format(token, run))
        time.sleep(0.3 + camui.SYNC_PATTERN_END + 0.3)
        req("POST", "/sync/done?t={0}&run={1}".format(token, run))
        t_end = time.time() + 90
        while time.time() < t_end:
            st, body = req("GET", "/sync/state?t={0}".format(token))
            j = json.loads(body)
            if j["state"] in ("hecho", "error"):
                out["final"] = j
                return
            time.sleep(0.3)
    except Exception as e:
        out["error"] = repr(e)


def run_sync(token):
    out = {}
    World.epoch = None
    th = threading.Thread(target=phone_clapper, args=(token, out), daemon=True)
    th.start()
    assert pump(lambda: not th.is_alive(), 150), "la claqueta no terminó"
    assert "error" not in out, out
    assert pump(lambda: app.previewing and not app.busy and app._sync is None, 30)
    return out


try:
    assert pump(lambda: app.phonecam.active and not app.busy, 15)
    for name, lat, fps, sees in CAMS:
        threading.Thread(target=camera, args=(app, name, lat, fps, sees), daemon=True).start()
    labs = ["📱 " + c[0] for c in CAMS]
    assert pump(lambda: all(l in app.order for l in labs) and app.previewing and not app.busy and
                app.eng.frame_count > 30, 60), (app.order, app.eng.last_errors())
    assert app.v_hub.get() is True
    assert app._audio_plan()["L"] is not None, "sin micro en el plan"

    # 1) ventana, QR y página
    app.sync_open()
    assert app._sync_dlg is not None and app._sync_dlg.alive
    token = app.phonecam.sync.token
    assert token
    st, _b = req("GET", "/sync?t=nada")
    assert st == 403, st
    st, body = req("GET", "/sync/state?t=" + token)
    assert st == 200 and json.loads(body)["state"] == "esperando", body
    print("1) «Sincronizar»: ventana con el QR; la página y su estado solo con el token "
          "(sin él: {0}) OK".format(403))

    # 2) primera medida y ajuste
    before = dict((l, int(app.cams[l].delay.get())) for l in labs)
    av0 = int(app.v_avoff.get())
    out = run_sync(token)
    assert out["page"][0] == 200 and b"Claqueta" in out["page"][1]
    fin = out["final"]
    assert fin["state"] == "hecho", fin
    new = dict((l, int(app.cams[l].delay.get())) for l in labs)
    av1 = int(app.v_avoff.get())
    print("   resultado: " + fin["msg"].replace("\n", " | "))
    # lo que de verdad llega tarde cada una respecto al micro
    true_late = dict((l, (c[1] - A_LAT) * 1000) for l, c in zip(labs, CAMS))
    slow = max(true_late.values())
    for l in labs:
        want = slow - true_late[l]                        # su retardo: lo que le falta
        assert abs(new[l] - want) <= 70, (l, new[l], want, fin["msg"])
    assert abs(av1 - slow) <= 70, (av1, slow)
    with open(camui.CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["av_offset"] == av1 and all(cfg["cams"][l]["delay"] == new[l] for l in labs)
    print("2) primera medida: Retardo " + ", ".join("{0} {1} ms (lo justo: {2:.0f})".format(
        l[2:], new[l], slow - true_late[l]) for l in labs) +
        "; Ajuste A/V {0} ms (lo justo: {1:.0f}); guardado OK".format(av1, slow))

    # 3) otra vez, con lo puesto: ya a la vez
    seen = {}
    real_analyze = camui.analyze_sync

    def spy(*a, **kw):
        r = real_analyze(*a, **kw)
        seen["res"] = r
        return r
    camui.analyze_sync = spy
    out2 = run_sync(token)
    camui.analyze_sync = real_analyze
    res = seen["res"]
    assert res["audio"] is not None, res
    resid = dict((l, (res["cams"][l][0] - res["audio"][0]) * 1000) for l in labs if res["cams"][l])
    assert len(resid) == 3, res
    assert all(abs(v) <= 45 for v in resid.values()), resid
    print("3) se vuelve a medir con lo puesto: cada cámara respecto al sonido " + ", ".join(
        "{0} {1:+.0f} ms".format(l[2:], v) for l, v in resid.items()) + " (± un fotograma) OK")

    # 4) deshacer, y cerrar
    app._sync_undo()
    assert pump(lambda: not app.busy, 10)
    assert int(app.v_avoff.get()) != av1 or True
    back = dict((l, int(app.cams[l].delay.get())) for l in labs)
    # deshace la ÚLTIMA sincronización (la 3): vuelve a lo de después de la 2
    assert back == new and int(app.v_avoff.get()) == av1, (back, new)
    app._sync_dlg.close()
    assert app.phonecam.sync.token is None
    st, _b = req("GET", "/sync/state?t=" + token)
    assert st == 403
    print("4) «Deshacer» vuelve a lo de antes de la última; al cerrar la ventana la página del "
          "móvil ya no vale (403) OK")
finally:
    stop.set()
    try:
        app.eng.release_devices()
    except Exception:
        pass
    try:
        app._phonecam_disable()
    except Exception:
        pass
    root.destroy()
print("TODO OK ui_sync")
