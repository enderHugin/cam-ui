"""«Sincronizar» (packs 52 y 55) de principio a fin, en la aplicación de verdad (CamApp con su ventana
Tk y el motor nuevo; hace falta un display: xvfb-run -a python3 tests/ui_sync.py).

Un «mundo» de mentira con un reloj: OCHO cámaras que llegan al PC con retrasos distintos (de 0,1 a
1 s, como USB, móviles por wifi u otro cam-ui encadenado) y a 30, 25, 20, 15 o 10 fps; a cada una se
le enseña el móvil-claqueta solo unos 3 s, una detrás de otra; y un micrófono (llega en 0,03 s) que
la oye al principio y al final. El móvil-claqueta es un cliente HTTPS de verdad contra el servidor
de cam-ui: abre la página del QR, toca «Empezar», espera a que el PC grabe, da su código (en el
mundo) y termina: la primera vez con «Terminar» en el PC, la segunda con su propio botón.

1) la ventana «Sincronizar», el QR y la página del móvil (con el token; sin él, nada);
2) primera medida: cam-ui pone solo el «Retardo ms» de las ocho y el «Ajuste A/V», lo que de
   verdad hace falta (± lo que el propio mosaico redondea), y lo guarda;
3) se vuelve a medir con lo puesto: ahora todas a la vez que el sonido (± un fotograma);
4) «Deshacer» vuelve a lo de antes; al cerrar la ventana, la página del móvil ya no vale.

(Más fino que ± un fotograma del mosaico no puede quedar: cada imagen solo puede salir en uno.)"""
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
CODE = camui.SYNC_CODE
BIT = camui.SYNC_BIT_S


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
    """El reloj del mundo: cuándo empieza la claqueta (hora del PC), hasta qué trozo llegó, y qué
    se ve / oye a cada hora."""
    epoch = None
    nbits = 0

    @classmethod
    def at(cls, t):
        """(segundo de la claqueta, encendida) a la hora t; (None, False) fuera de ella."""
        if cls.epoch is None:
            return None, False
        x = t - cls.epoch
        k = int(x // BIT)
        if x < 0 or k >= cls.nbits:
            return None, False
        return x, CODE[k] == 1


CAMS = [  # (nombre, retraso, fps): a la i-ésima se le enseña en los segundos [2,5·i + 1, 2,5·i + 4]
    ("Cam1", 0.10, 30), ("Cam2", 0.18, 30), ("Cam3", 0.30, 20), ("Cam4", 0.45, 15),
    ("Cam5", 0.60, 10), ("Cam6", 0.80, 30), ("Cam7", 1.00, 15), ("Cam8", 0.25, 25)]
SHOW = [(2.5 * i + 1.0, 2.5 * i + 4.0) for i in range(len(CAMS))]
HEAR = [(0.0, 8.0), (16.0, 23.0)]
LENGTH = 23.0                                    # se termina a los 23 s (de 90 posibles)
A_LAT = 0.03
stop = threading.Event()


def jpeg(val, k, pos):
    a = np.full((180, 320, 3), 70, np.uint8)
    a[140:165, (k * 5) % 280:(k * 5) % 280 + 30] = 170            # algo que se mueve
    if val is not None:
        y, x = pos
        a[y:y + 28, x:x + 14] = val                                 # el móvil
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=85)
    return b.getvalue()


def camera(app, i, name, lat, fps):
    sid = app.phonecam.new_session(name)
    k = 0
    t_next = time.time()
    a, b = SHOW[i]
    while not stop.is_set():
        k += 1
        now = time.time()
        # lo que recoge esta imagen: la claqueta en su último 1/fps (la luz de su intervalo)
        val = None
        x, _on = World.at(now - lat)
        if x is not None and a - 0.3 <= x <= b + 0.3:
            ts = np.linspace(now - lat - 1.0 / fps, now - lat, 8, endpoint=False)
            lit = np.mean([1.0 if (World.at(t)[1] and a <= World.at(t)[0] <= b) else 0.0 for t in ts])
            val = int(15 + 225 * lit)
            pos = (40 + int(10 * np.sin(x)), 120 + int(8 * (x - a)))   # el móvil se mueve un poco
        app.phonecam.feed(sid, jpeg(val, k, pos if val is not None else None), seq=k)
        t_next += 1.0 / fps
        time.sleep(max(0.0, t_next - time.time()))


APORT = free_port()


def mic_server():
    """El «micrófono»: ffmpeg se conecta aquí (en vez de a un dispositivo de Windows) y recibe
    sonido al ritmo real: ruido, y el pitido cuando la claqueta pita y se oye (A_LAT después)."""
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
                tt = t_next - A_LAT + np.arange(n) / float(sr)
                x = 0.01 * rng.standard_normal(n)
                for i in range(0, n, 24):                          # a trozos de 0,5 ms
                    xx, on = World.at(tt[i])
                    if on and any(a <= xx < b for a, b in HEAR):
                        x[i:i + 24] += 0.3 * np.sin(2 * np.pi * camui.SYNC_TONE_HZ *
                                                    (pos + i + np.arange(24)) / float(sr))
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


def phone_clapper(token, out, stop_from_pc):
    """La página del móvil, hecha a mano: lo mismo que hace su JavaScript."""
    try:
        st, body = req("GET", "/sync?t=" + token)
        out["page"] = (st, body)
        req("GET", "/sync/state?t={0}&ready=1".format(token))
        req("POST", "/sync/go?t={0}&run=0".format(token))
        t_end = time.time() + 30
        run = None
        while time.time() < t_end:
            j = json.loads(req("GET", "/sync/state?t={0}".format(token))[1])
            if j["state"] == "grabando":
                run = j["run"]
                break
            time.sleep(0.2)
        assert run is not None, "el PC no empezó a grabar la prueba"
        time.sleep(1.0)                                            # «3, 2, 1» (aquí, 1 s)
        World.nbits = len(CODE)
        World.epoch = time.time() + 0.3
        req("POST", "/sync/started?t={0}&run={1}".format(token, run))
        out["started"] = True
        if stop_from_pc:                    # «Terminar» en el PC: el móvil lo ve y para
            while time.time() < World.epoch + 120:
                j = json.loads(req("GET", "/sync/state?t={0}".format(token))[1])
                if j["state"] == "terminando":
                    break
                time.sleep(0.3)
        else:                               # «Terminar» en el móvil
            time.sleep(0.3 + LENGTH)
        bits = int((time.time() - World.epoch) // BIT)
        World.nbits = bits
        req("POST", "/sync/done?t={0}&run={1}&bits={2}".format(token, run, bits))
        out["bits"] = bits
        t_end = time.time() + 180
        while time.time() < t_end:
            j = json.loads(req("GET", "/sync/state?t={0}".format(token))[1])
            if j["state"] in ("hecho", "error"):
                out["final"] = j
                return
            time.sleep(0.3)
    except Exception as e:
        out["error"] = repr(e)


def run_sync(token, stop_from_pc):
    out = {}
    World.epoch = None
    th = threading.Thread(target=phone_clapper, args=(token, out, stop_from_pc), daemon=True)
    th.start()
    if stop_from_pc:
        assert pump(lambda: out.get("started"), 60)
        assert pump(lambda: World.epoch is not None and time.time() > World.epoch + LENGTH, 60)
        assert app._sync_dlg.btn_start.cget("text") == "Terminar" and \
            app._sync_dlg.btn_start.instate(["!disabled"])
        app._sync_dlg.btn_start.invoke()                           # «Terminar» en el PC
    assert pump(lambda: not th.is_alive(), 240), "la claqueta no terminó"
    assert "error" not in out, out
    assert pump(lambda: app.previewing and not app.busy and app._sync is None, 30)
    return out


try:
    assert pump(lambda: app.phonecam.active and not app.busy, 15)
    for i, (name, lat, fps) in enumerate(CAMS):
        threading.Thread(target=camera, args=(app, i, name, lat, fps), daemon=True).start()
    labs = ["📱 " + c[0] for c in CAMS]
    assert pump(lambda: all(l in app.order for l in labs) and app.previewing and not app.busy and
                app.eng.frame_count > 30, 90), (app.order, app.eng.last_errors())
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
          "(sin él: 403) OK")

    # 2) primera medida y ajuste (se termina con «Terminar» en el PC)
    seen = {}
    real_analyze = camui.analyze_sync

    def spy(*a, **kw):
        r = real_analyze(*a, **kw)
        seen["res"], seen["kw"] = r, kw
        return r
    camui.analyze_sync = spy
    out = run_sync(token, stop_from_pc=True)
    assert out["page"][0] == 200 and b"Claqueta" in out["page"][1]
    fin = out["final"]
    assert fin["state"] == "hecho", fin
    assert seen["kw"].get("nbits") == out["bits"], (seen["kw"], out["bits"])
    new = dict((l, int(app.cams[l].delay.get())) for l in labs)
    av1 = int(app.v_avoff.get())
    print("   resultado: " + fin["msg"].replace("\n", " | "))
    true_late = dict((l, (c[1] - A_LAT) * 1000) for l, c in zip(labs, CAMS))
    slow = max(true_late.values())
    for l in labs:
        want = slow - true_late[l]
        assert abs(new[l] - want) <= 45, (l, new[l], want, fin["msg"])
    assert abs(av1 - slow) <= 45, (av1, slow)
    with open(camui.CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["av_offset"] == av1 and all(cfg["cams"][l]["delay"] == new[l] for l in labs)
    print("2) primera medida («Terminar» en el PC, a los {0:.0f} s): Retardo ".format(
        out["bits"] * BIT) + ", ".join("{0} {1} ({2:.0f})".format(
            l[2:], new[l], slow - true_late[l]) for l in labs) +
        " ms (entre paréntesis, lo justo); Ajuste A/V {0} ms ({1:.0f}); guardado OK".format(av1, slow))

    # 3) otra vez, con lo puesto (y «Terminar» en el móvil): ya a la vez
    out2 = run_sync(token, stop_from_pc=False)
    camui.analyze_sync = real_analyze
    res = seen["res"]
    assert res["audio"] is not None, res
    resid = dict((l, (res["cams"][l][0] - res["audio"][0]) * 1000) for l in labs if res["cams"][l])
    assert len(resid) == 8, res
    # (el mosaico va a 30 fps: cada imagen sale en su fotograma, ± medio fotograma de 33 ms)
    assert all(abs(v) <= 34 for v in resid.values()), resid
    assert abs(sum(resid.values()) / len(resid)) <= 12, resid
    print("3) se vuelve a medir con lo puesto («Terminar» en el móvil): cada cámara respecto al "
          "sonido " + ", ".join("{0} {1:+.0f}".format(l[2:], v) for l, v in resid.items()) + " ms OK")

    # 4) deshacer, y cerrar
    app._sync_undo()
    assert pump(lambda: not app.busy, 10)
    back = dict((l, int(app.cams[l].delay.get())) for l in labs)
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
