"""«Reloj común» entre PCs encadenados (ChainClock / CamApp._chain_net_cams): sin hardware,
con IpServer, ffmpeg y servidores HTTP reales en 127.0.0.1."""
import os
import sys
import time
import json
import types
import socket
import threading
import subprocess
import http.server
import http.client

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

# 1) qué cámaras de red podrían ser otro cam-ui (solo http, que es lo que sirve «En la red»)
assert camui.chain_clock_url("http://192.168.1.129:8554/stream.mjpg") == ("192.168.1.129", 8554, "/camui/clock")
assert camui.chain_clock_url("http://ender-omen/stream.mjpg") == ("ender-omen", 80, "/camui/clock")
assert camui.chain_clock_url("rtsp://1.2.3.4:554/live") is None
assert camui.chain_clock_url("https://1.2.3.4/x") is None
assert camui.chain_clock_url("USB2.0 Camera") is None
print("1) chain_clock_url: solo cámaras http OK")


def _feeder(srv, w, h, fps, stop, val=lambda k: (k * 7) % 255):
    port = srv.arm(w, h, fps)
    so = socket.create_connection(("127.0.0.1", port), timeout=5)
    k = 0
    t0 = time.time()
    try:
        while not stop.is_set():
            so.sendall(np.full((h, w, 3), val(k), np.uint8).tobytes())
            k += 1
            time.sleep(max(0, t0 + k / float(fps) - time.time()))
    except OSError:
        pass
    finally:
        so.close()


# 2) el reloj de un cam-ui de verdad (IpServer) contesta; un MJPEG cualquiera y un puerto sin
#    nada, no -- y esos se quedan exactamente como antes
s2 = camui.IpServer()
s2.open(160, 120, fps=30, port=58501)
r2 = camui.chain_probe_clock("http://127.0.0.1:58501/stream.mjpg")
assert r2 is not None and abs(r2[0]) < 0.01 and 0 <= r2[1] < 0.5, r2
other2 = subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                           "-t", "6", "-f", "mpjpeg", "-listen", "1", "http://127.0.0.1:58502/"],
                          stderr=subprocess.DEVNULL)
time.sleep(0.8)
assert camui.chain_probe_clock("http://127.0.0.1:58502/") is None
other2.kill()
other2.wait(5)
assert camui.chain_probe_clock("http://127.0.0.1:58503/") is None
s2.close()
print("2) chain_probe_clock: contesta un cam-ui, no un MJPEG cualquiera ni un puerto vacío OK")


# un "emisor" de mentira con el protocolo de IpServer, pero con SU reloj adelantado SKEW s (dos
# PCs nunca tienen la hora igual) y retrasos de wifi simulados al mandar cada fotograma
class _FakeSender(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        o = self.server.opts
        if self.path == "/camui/clock":
            body = json.dumps({"camui": 1, "t": time.time() + o["skew"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=x")
        self.end_headers()
        k = 0
        t0 = time.time()
        rnd = np.random.RandomState(7)
        try:
            while not o["stop"].is_set():
                k += 1
                t_send = t0 + k / 30.0                           # sale "de su ffmpeg", a ritmo fijo
                time.sleep(max(0, t_send - time.time()))
                if o.get("pause") and o["pause"][0] <= k < o["pause"][1]:
                    continue                                     # el emisor se calla un rato
                jpeg = o["jpeg"]
                d = rnd.uniform(0, o["jitter"]) if o["jitter"] else 0
                time.sleep(max(0, t_send + d - time.time()))     # la wifi lo retrasa (en orden)
                self.wfile.write(b"--x\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write("X-Camui-T: {0:.6f}\r\n".format(t_send + o["skew"]).encode())
                self.wfile.write("Content-Length: {0}\r\n\r\n".format(len(jpeg)).encode())
                self.wfile.write(jpeg + b"\r\n")
                o["sent"].append((k, t_send))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def _fake_sender(port, skew, jitter, pause=None):
    import io as _io
    from PIL import Image
    b = _io.BytesIO()
    Image.new("RGB", (64, 48), (90, 90, 90)).save(b, "JPEG")
    srv = camui._MjpegHTTPServer(("127.0.0.1", port), _FakeSender)
    srv.opts = {"skew": skew, "jitter": jitter, "stop": threading.Event(), "jpeg": b.getvalue(),
                "sent": [], "pause": pause}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _read_local(url, secs):
    """Lee el MJPEG local de un ChainClock como lo haría ffmpeg: hora de llegada de cada parte."""
    hp = url.split("//")[1].split("/")[0]
    c = http.client.HTTPConnection(hp.split(":")[0], int(hp.split(":")[1]), timeout=10)
    c.request("GET", "/chain.mjpg")
    r = c.getresponse()
    got, t_end = [], time.time() + secs
    try:
        while time.time() < t_end:
            line = r.fp.readline()
            if not line:
                break
            if line.startswith(b"Content-Length:"):
                n = int(line.split(b":")[1])
                r.fp.readline()
                r.fp.read(n)
                got.append(time.time())
    except OSError:
        pass
    c.close()
    return got


# 3) relojes distintos (+2,5 s) y wifi irregular (0-80 ms por fotograma): la diferencia de
#    relojes se calcula sola, y cada fotograma se entrega con el MISMO retraso fijo desde que
#    salió del emisor -- los altibajos de la wifi desaparecen
fs3 = _fake_sender(58504, skew=2.5, jitter=0.08)
ch3 = camui.ChainClock("http://127.0.0.1:58504/stream.mjpg")
try:
    assert ch3.wait_ready(3.0), "no se sincronizó"
    time.sleep(1.5)
    assert abs(ch3.offset - 2.5) < 0.01, ch3.offset
    lat3, pipe3 = ch3.compensation_ms()
    assert camui.CHAIN_LATENCY_MIN_MS <= lat3 <= 80 + camui.CHAIN_LATENCY_MARGIN_MS + 20, lat3
    assert 95 <= pipe3 <= 120, pipe3                 # 3,2 fotogramas a 30 fps
    with ch3._cond:
        tr3 = sorted(ch3._transit)
    arr_spread = (tr3[int(0.95 * (len(tr3) - 1))] - tr3[int(0.05 * (len(tr3) - 1))]) * 1000
    time.sleep(0.5)                                  # que lo encolado antes del nuevo retraso salga
    got3 = _read_local(ch3.local_url, 3.0)
    sent3 = list(fs3.opts["sent"])
    # empareja cada entrega con el fotograma enviado: la entrega es su salida + retraso fijo
    sent_t = np.array([t for _, t in sent3])
    lags = []
    for tg in got3[3:]:
        j = np.searchsorted(sent_t, tg - lat3 / 1000.0)
        cand = [sent_t[k] for k in (j - 1, j) if 0 <= k < len(sent_t)]
        lags.append(min(abs(tg - c - lat3 / 1000.0) for c in cand) * 1000)
    lags.sort()
    assert ch3.late <= 3, ch3.late
    assert lags[int(0.9 * (len(lags) - 1))] < 12, lags[-10:]
    assert len(got3) >= 75, len(got3)
finally:
    ch3.close()
    fs3.opts["stop"].set()
    fs3.shutdown()
    fs3.server_close()
print("3) relojes con +2,5 s de diferencia y wifi de 0-80 ms: diferencia calculada {0:.3f} s; "
      "llegaban con {1:.0f} ms de altibajos y se entregan con el retraso fijo ({2} ms) a ±{3:.0f} ms "
      "OK".format(ch3.offset, arr_spread, lat3, lags[int(0.9 * (len(lags) - 1))]))

# 4) el emisor se calla 3 s (p. ej. reinicia su ffmpeg al empezar a grabar): el intermediario
#    sigue mandando el último fotograma para que el ffmpeg de aquí no se rinda (con 5 s de tope
#    de red, ver NET_IO_TIMEOUT_US, se daría por caída); si se calla de más, deja de mandar
camui_hold = camui.CHAIN_HOLD_SECONDS
fs4 = _fake_sender(58505, skew=0.0, jitter=0.0, pause=(60, 150))     # fotogramas 60..150: 3 s
ch4 = camui.ChainClock("http://127.0.0.1:58505/stream.mjpg")
try:
    assert ch4.wait_ready(3.0)
    got4 = _read_local(ch4.local_url, 6.5)
    gaps4 = [b - a for a, b in zip(got4, got4[1:])]
    assert max(gaps4) < 1.3, "hueco de {0:.1f} s sin nada para ffmpeg".format(max(gaps4))
finally:
    ch4.close()
    fs4.opts["stop"].set()
    fs4.shutdown()
    fs4.server_close()
camui.CHAIN_HOLD_SECONDS = 1.5
fs4b = _fake_sender(58506, skew=0.0, jitter=0.0, pause=(45, 10 ** 9))   # se calla para siempre
ch4b = camui.ChainClock("http://127.0.0.1:58506/stream.mjpg")
try:
    assert ch4b.wait_ready(3.0)
    t4b = time.time()
    got4b = _read_local(ch4b.local_url, 8.0)
    assert got4b and time.time() - t4b < 7.5, "no dejó de mandar"
finally:
    camui.CHAIN_HOLD_SECONDS = camui_hold
    ch4b.close()
    fs4b.opts["stop"].set()
    fs4b.shutdown()
    fs4b.server_close()
print("4) el emisor se calla 3 s: el intermediario aguanta repitiendo el último (hueco máximo "
      "{0:.1f} s); si se calla de más, corta para que salga «Sin señal» OK".format(max(gaps4)))


# 5) CamApp._chain_net_cams con un "self" de mentira: solo la cámara que es otro cam-ui pasa por
#    su intermediario y se le adelanta lo medido; una cámara local y un MJPEG cualquiera se quedan
#    igual; con «marcas del dispositivo», nada
def _fake_app():
    return types.SimpleNamespace(eng=types.SimpleNamespace(_abort=False), _chains={},
                                 _chain_lk=threading.Lock(), _chain_no={}, _chain_info={})


s5 = camui.IpServer()
s5.open(160, 120, fps=30, port=58507)
stop5 = threading.Event()
th5 = threading.Thread(target=_feeder, args=(s5, 160, 120, 30, stop5), daemon=True)
th5.start()
other5 = subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                           "-t", "15", "-f", "mpjpeg", "-listen", "1", "http://127.0.0.1:58508/"],
                          stderr=subprocess.DEVNULL)
time.sleep(0.8)
app5 = _fake_app()
try:
    plan5 = {"sync": "clock", "labels": ["USB", "🌐 Omen", "🌐 Móvil"],
             "cams": [{"spec": "USB2.0 Camera", "delay": 0},
                      {"spec": "http://127.0.0.1:58507/stream.mjpg", "delay": 20},
                      {"spec": "http://127.0.0.1:58508/", "delay": 0}]}
    camui.CamApp._chain_net_cams(app5, plan5)
    c5 = plan5["cams"]
    assert c5[0] == {"spec": "USB2.0 Camera", "delay": 0}
    assert c5[2] == {"spec": "http://127.0.0.1:58508/", "delay": 0}
    ch5 = app5._chains["http://127.0.0.1:58507/stream.mjpg"]
    assert c5[1]["spec"] == ch5.local_url
    adv5 = app5._chain_info["🌐 Omen"]
    assert 150 <= adv5 <= 260, adv5
    assert abs(c5[1]["delay"] - (20 - adv5)) < 1e-6, c5[1]
    # un segundo arranque reutiliza el mismo intermediario; y sin esa cámara en el plan, se cierra
    plan5b = {"sync": "clock", "labels": ["🌐 Omen"],
              "cams": [{"spec": "http://127.0.0.1:58507/stream.mjpg", "delay": 0}]}
    camui.CamApp._chain_net_cams(app5, plan5b)
    assert app5._chains["http://127.0.0.1:58507/stream.mjpg"] is ch5
    plan5c = {"sync": "device", "labels": ["🌐 Omen"],
              "cams": [{"spec": "http://127.0.0.1:58507/stream.mjpg", "delay": 0}]}
    camui.CamApp._chain_net_cams(app5, plan5c)
    assert plan5c["cams"][0] == {"spec": "http://127.0.0.1:58507/stream.mjpg", "delay": 0}
    assert not app5._chains and ch5._closed
finally:
    stop5.set()
    other5.kill()
    for ch_ in app5._chains.values():
        ch_.close()
    s5.close()
print("5) _chain_net_cams: solo la cámara que es otro cam-ui se alinea (se adelanta {0} ms); el "
      "resto, igual; con «marcas del dispositivo», nada OK".format(adv5))


# 6) LA PRUEBA DE VERDAD: un receptor con «reloj común» y, en el mismo mosaico, una cámara local
#    y otra encadenada (comando real de cam-ui en los dos PCs). Un generador hace una rampa de
#    brillo que llega a la vez a la local y al emisor: en cada fotograma grabado, la diferencia de
#    brillo entre las dos mitades dice cuántos ms va desfasada la encadenada. Antes de esto,
#    medido igual: +88 ms, +275 ms, y más de un segundo, según la vez.
def _align_run(port, fps=30):
    W, H, RAMP = 160, 120, 1.0
    raw_in = lambda: ["-f", "rawvideo", "-pix_fmt", "gray", "-s", "%dx%d" % (W, H),
                      "-framerate", str(fps), "-i", "pipe:0"]
    cam = lambda spec, delay=0: {"spec": spec, "mode": None, "h": False, "v": False, "rot": 0,
                                 "delay": delay, "tile": "auto", "zoom": 100.0, "cx": 50.0,
                                 "cy": 50.0, "color": {}}
    s = camui.IpServer()
    s.open(W, H, fps=fps, port=port)
    p = s.arm(W, H, fps)
    base = {"fps": fps, "sync": "clock", "crf": 18, "preset": "ultrafast",
            "audio": {"L": None, "R": None, "offset": 0}}
    cmd_s, _ = camui.build_command(
        dict(base, cams=[cam("gen")], geom={"W": W, "H": H, "tiles": [(0, 0, W, H)]},
             ipout={"w": W, "h": H, "fps": fps, "pix": "rgb24", "port": p}),
        outfile=None, preview=False, input_fn=lambda k, sp, o=None: raw_in())
    snd = subprocess.Popen(cmd_s, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    T, stop, outs = [None], threading.Event(), [snd]

    def gen():
        t0, k = time.time(), 0
        while not stop.is_set():
            k += 1
            lv = 0 if T[0] is None or time.time() < T[0] else int(min(255, (time.time() - T[0]) / RAMP * 255))
            fr = bytes([lv]) * (W * H)
            for pr in list(outs):
                try:
                    pr.stdin.write(fr)
                    pr.stdin.flush()
                except Exception:
                    pass
            time.sleep(max(0, t0 + k / float(fps) - time.time()))

    threading.Thread(target=gen, daemon=True).start()
    time.sleep(1.5)
    app = _fake_app()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_chain_align_%d.mp4" % port)
    rec = None
    try:
        plan = dict(base, labels=["local", "🌐 encadenada"],
                    cams=[cam("gen"), cam("http://127.0.0.1:%d/stream.mjpg" % port)],
                    geom=camui.layout_tiles([W / float(H)] * 2, [2], 2 * W), duration=7)
        camui.CamApp._chain_net_cams(app, plan)
        assert plan["cams"][1]["spec"].startswith("http://127.0.0.1:") and \
            plan["cams"][1]["spec"].endswith("/chain.mjpg"), plan["cams"][1]
        cmd_r, _ = camui.build_command(plan, outfile=out, preview=False, input_fn=lambda k, sp, o=None: (
            raw_in() if sp == "gen" else camui._dshow_input(k, sp, o)))
        rec = subprocess.Popen(cmd_r, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        outs.append(rec)
        time.sleep(3.0)
        T[0] = time.time()
        rec.wait(timeout=25)
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vf", "scale=2:1:flags=area",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=20)
        a = np.frombuffer(r.stdout, np.uint8).reshape(-1, 2).astype(float)
        m = (a[:, 0] > 30) & (a[:, 0] < 220) & (a[:, 1] > 30) & (a[:, 1] < 220)
        assert m.sum() >= 5, "las dos mitades no llegan a coincidir en la rampa"
        return float(((a[m, 0] - a[m, 1]) / 255.0 * RAMP * 1000).mean()), app._chain_info.get("🌐 encadenada")
    finally:
        stop.set()
        snd.kill()
        if rec is not None and rec.poll() is None:
            rec.kill()
        for ch_ in app._chains.values():
            ch_.close()
        s.close()
        if os.path.exists(out):
            os.remove(out)


res6 = [_align_run(58510 + i) for i in range(2)]
for d6, adv6 in res6:
    assert abs(d6) < 45, "la encadenada va {0:+.0f} ms respecto a la local".format(d6)
print("6) grabación real con «reloj común», cámara local + encadenada en el mismo mosaico: "
      "desfase {0} ms (se adelanta {1} ms) OK".format(
          ", ".join("{0:+.0f}".format(d) for d, _ in res6), res6[0][1]))

print("TODO OK unit_chain")
