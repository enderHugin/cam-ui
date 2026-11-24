"""«Sincronizar» (packs 52, 55 y 56) de principio a fin, en la aplicación de verdad (CamApp con su
ventana Tk y el motor nuevo; hace falta un display: xvfb-run -a python3 tests/ui_sync.py).

Un «mundo» de mentira con un reloj: OCHO cámaras que llegan al PC con retrasos distintos (de 0,1 a
1 s, como USB, móviles por wifi u otro cam-ui encadenado) y a 30, 25, 20, 15 o 10 fps; a cada una se
le enseña el móvil-claqueta solo unos 3 s, una detrás de otra; y un micrófono (llega en 0,03 s) que
la oye al principio y al final. El móvil-claqueta es un cliente HTTPS de verdad contra el servidor
de cam-ui: abre la página del QR, toca «Empezar», espera a que el PC grabe, da su código (en el
mundo) y para cuando el PC se lo pide.

1) la ventana «Sincronizar», el QR y la página del móvil (con el token; sin él, nada);
2) primera prueba: en directo, sobre cada casilla del mosaico sale «✓ lista» en cuanto se le ha
   enseñado el móvil (una tras otra, ninguna antes de tiempo) y «falta» en las demás; el móvil
   recibe «Listas k de 8»; con las ocho, termina sola. cam-ui pone el «Retardo ms» de las ocho y el
   «Ajuste A/V», lo que de verdad hace falta (± lo que el propio mosaico redondea), y lo guarda;
3) segunda prueba, con lo puesto, y «Terminar» en el PC con seis listas: las medidas, a la vez que
   el sonido (± un fotograma); las demás siguen como estaban respecto al sonido;
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


def phone_clapper(token, out):
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
        msgs = out["msgs"] = []
        devs = out["devs"] = []
        while time.time() < World.epoch + 80:                      # hasta que el PC pida parar
            j = json.loads(req("GET", "/sync/state?t={0}".format(token))[1])
            if j.get("msg") and (not msgs or msgs[-1] != j["msg"]):
                msgs.append(j["msg"])
            if j.get("devs") and (not devs or devs[-1] != j["devs"]):
                devs.append(j["devs"])
            if j["state"] == "terminando":
                break
            time.sleep(0.3)
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


def run_sync(token, stop_at=None):
    """Una prueba. stop_at: con cuántas listas se toca «Terminar» en el PC (si no, termina sola)."""
    out = {}
    World.epoch = None
    th = threading.Thread(target=phone_clapper, args=(token, out), daemon=True)
    th.start()
    assert pump(lambda: out.get("started") or "error" in out, 60), (out, app.phonecam.sync.state, app.phonecam.sync.msg)
    assert "error" not in out, (out, app.phonecam.sync.state, app.phonecam.sync.msg)
    marks, shown, pressed = {}, set(), False
    while th.is_alive():
        root.update()
        s_ = app._sync or {}
        live = s_.get("live")
        if live is not None and World.epoch is not None:
            for lab in list(live.ok):
                marks.setdefault(lab, time.time() - World.epoch)
            if s_.get("recording"):
                for i in app.view.find_withtag("syncmark"):
                    shown.add(app.view.itemcget(i, "text"))
            if stop_at and not pressed and len(live.ok) >= stop_at:
                assert app._sync_dlg.btn_start.cget("text") == "Terminar"
                app._sync_dlg.btn_start.invoke()                   # «Terminar» en el PC
                pressed = True
        time.sleep(0.03)
    assert "error" not in out, out
    assert pump(lambda: app.previewing and not app.busy and app._sync is None, 60)
    out["marks"], out["shown"] = marks, shown
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

    assert pump(lambda: not app.busy and app.previewing, 30)

    # 2) primera prueba: ✓ en directo, y termina sola
    seen = {}
    real_analyze = camui.analyze_sync

    def spy(*a, **kw):
        if os.environ.get("CAMUI_SYNC_KEEP"):        # (para mirarla luego)
            import pickle
            k_ = len([f for f in os.listdir(os.environ["CAMUI_SYNC_KEEP"]) if f.endswith(".mkv")])
            shutil.copy(a[0], os.path.join(os.environ["CAMUI_SYNC_KEEP"], "prueba%d.mkv" % k_))
            with open(os.path.join(os.environ["CAMUI_SYNC_KEEP"], "prueba%d.pkl" % k_), "wb") as f_:
                pickle.dump((a[1:], kw), f_)
        r = real_analyze(*a, **kw)
        seen["res"], seen["kw"] = r, kw
        for l_, v_ in r["cams"].items():           # (si alguna no sale, por qué: para el registro)
            if v_ is None:
                print("   («{0}» no salió: {1})".format(l_, r["info"].get(l_)))
        return r
    camui.analyze_sync = spy
    out = run_sync(token)
    assert out["page"][0] == 200 and b"Claqueta" in out["page"][1]
    marks = out["marks"]
    assert sorted(marks) == sorted(labs), (marks, labs)
    for i, l in enumerate(labs):                     # cada una, en cuanto se le enseña (y no antes)
        a, b = SHOW[i]
        assert a + 1.0 <= marks[l] - CAMS[i][1] <= b + 6.0, (l, marks[l], SHOW[i])
    assert {"✓ lista", "falta"} <= out["shown"], out["shown"]
    assert any(m.startswith("Listas 3 de 8") for m in out["msgs"]), out["msgs"]
    assert out["bits"] * BIT < max(b for _a, b in SHOW) + 8, out["bits"]   # terminó sola, pronto
    fin = out["final"]
    assert fin["state"] == "hecho", fin
    assert seen["kw"].get("nbits") == out["bits"], (seen["kw"], out["bits"])
    new = dict((l, int(app.cams[l].delay.get())) for l in labs)
    av1 = int(app.v_avoff.get())
    print("   resultado: " + fin["msg"].replace("\n", " | "))
    # el mapa de aparatos en el móvil (y en la ventana): en directo, cuántas faltan (cada vez
    # menos) y cuánto va cada una detrás de la primera; al final, lo medido respecto al sonido
    dv = [d for d in out["devs"] if d.get("phase") == "directo"]
    assert dv and len(dv[0]["rows"]) == 8, out["devs"][:2]
    oks = [sum(1 for r in d["rows"] if r["st"] == "ok") for d in dv]
    assert oks == sorted(oks) and oks[0] < 8 and oks[-1] >= 7, oks
    last = dict((r["n"], r["ms"]) for r in dv[-1]["rows"] if r["st"] == "ok")
    first = min(c[1] for c in CAMS if "📱 " + c[0] in last)
    rel_err = dict((l, ms - (dict(("📱 " + c[0], c[1]) for c in CAMS)[l] - first) * 1000)
                   for l, ms in last.items())
    assert all(abs(e) <= 80 for e in rel_err.values()), rel_err
    fd = fin.get("devs") or {}
    assert fd.get("phase") == "medido" and fd.get("sound") == 0, fd
    for r in fd["rows"]:                             # (con retardos 0 y ajuste 0 al medir:
        if r["st"] == "ok":                          # de por sí = ajuste A/V - Retardo nuevos)
            assert abs(r["ms"] - (av1 - new[r["n"]])) <= 1, (r, av1, new[r["n"]])
            assert abs(r["now"] - av1) <= 1, (r, av1)   # ahora: todas en la raya, con el sonido
    assert abs(fd["sound_now"] - av1) <= 1, fd
    nat1 = dict((r["n"], r["ms"]) for r in fd["rows"] if r["st"] == "ok")
    k_ok = sum(1 for r in fd["rows"] if r["st"] == "ok")
    assert app._sync_dlg.devs_head == "Medidas {0} de 8".format(k_ok) + \
        ("" if k_ok == 8 else " · sin medir: {0}".format(8 - k_ok)), app._sync_dlg.devs_head
    print("2c) el mapa (en el móvil y en la ventana): en directo «Faltan k de 8», cada vez menos "
          "({0}), y cuánto va cada una detrás de la primera (error ≤ {1:.0f} ms); al final, «{2}» "
          "y lo que llegaba tarde cada una respecto al sonido OK".format(
              " → ".join(str(8 - k) for k in oks), max(abs(e) for e in rel_err.values()),
              app._sync_dlg.devs_head))
    print("2a) en directo: «✓ lista» sobre cada casilla en cuanto se le enseña el móvil (a los " +
          ", ".join("{0:.1f}".format(marks[l] - CAMS[i][1] - SHOW[i][0]) for i, l in enumerate(labs)) +
          " s), «falta» en las demás; el móvil recibe «{0}»…; con las ocho, termina sola a los "
          "{1:.0f} s OK".format(out["msgs"][0], out["bits"] * BIT))
    true_late = dict((l, (c[1] - A_LAT) * 1000) for l, c in zip(labs, CAMS))
    slow = max(true_late.values())
    for l in labs:
        want = slow - true_late[l]
        assert abs(new[l] - want) <= 45, (l, new[l], want, fin["msg"])
    assert abs(av1 - slow) <= 45, (av1, slow)
    with open(camui.CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["av_offset"] == av1 and all(cfg["cams"][l]["delay"] == new[l] for l in labs)
    print("2b) medida: Retardo " + ", ".join("{0} {1} ({2:.0f})".format(
            l[2:], new[l], slow - true_late[l]) for l in labs) +
          " ms (entre paréntesis, lo justo); Ajuste A/V {0} ms ({1:.0f}); guardado OK".format(av1, slow))

    # 3) otra vez, con lo puesto, y «Terminar» en el PC con seis listas
    out2 = run_sync(token, stop_at=6)
    camui.analyze_sync = real_analyze
    res = seen["res"]
    assert res["audio"] is not None, res
    got = [l for l in labs if res["cams"][l]]
    assert len(got) >= 6, res["cams"]
    resid = dict((l, (res["cams"][l][0] - res["audio"][0]) * 1000) for l in got)
    # cada imagen sale en su fotograma del mosaico (30 fps) y cada cámara da las suyas a sus fps:
    # ± medio fotograma de cada (una a 10 fps solo cambia cada 100 ms)
    fps_of = dict(("📱 " + c[0], c[2]) for c in CAMS)
    assert all(abs(v) <= 17 + 500.0 / fps_of[l] for l, v in resid.items()), resid
    new2 = dict((l, int(app.cams[l].delay.get())) for l in labs)
    av2 = int(app.v_avoff.get())
    # lo que tarda cada una «de por sí» no depende de los ajustes con que se mida: el de esta
    # segunda (con todo ya puesto) es el de la primera (con todo a 0)
    fd2 = out2["final"].get("devs") or {}
    nat2 = dict((r["n"], r["ms"]) for r in fd2.get("rows", []) if r["st"] == "ok")
    dn = dict((l, nat2[l] - nat1[l]) for l in nat2 if l in nat1)
    assert len(dn) >= 5 and all(abs(v) <= 17 + 500.0 / fps_of[l] for l, v in dn.items()), dn
    for l in labs:
        if l not in got:                             # no la vio: igual respecto al sonido
            assert abs((new2[l] - av2) - (new[l] - av1)) <= 1, (l, new2[l], av2, new[l], av1)
    print("3) con lo puesto, «Terminar» en el PC con {0} listas: las medidas, respecto al sonido ".format(
        len(out2["marks"])) + ", ".join("{0} {1:+.0f}".format(l[2:], v) for l, v in resid.items()) +
        " ms; las demás, igual respecto al sonido; y lo que tarda cada una de por sí, lo mismo que "
        "en la primera (" + ", ".join("{0:+d}".format(int(v)) for v in dn.values()) + " ms) OK")
    app._sync_undo()
    assert pump(lambda: not app.busy, 10)
    assert dict((l, int(app.cams[l].delay.get())) for l in labs) == new and \
        int(app.v_avoff.get()) == av1

    # 4) cerrar
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
