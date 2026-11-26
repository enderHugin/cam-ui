"""Con el motor nuevo (concentrador), el sonido va con la imagen IGUAL en cada grabación, aunque en
«Avanzado» esté «Sincronía: Marcas del dispositivo» (pack 58). En la aplicación de verdad (CamApp con
su ventana Tk; hace falta un display: xvfb-run -a python3 tests/ui_hub_audio_device.py).

Fallo real (camui.log y grabación de un usuario, pack 57): tras «Sincronizar», las grabaciones
seguían «sin terminar de estar bien sincronizadas». Tenía «Marcas del dispositivo», y con esa opción
el sonido empezaba en el 0 del vídeo -- pero el micrófono se abre cuando arranca ffmpeg, unas
décimas DESPUÉS, y cada vez un poco distinto (y el vídeo del concentrador lleva un margen D que
también cambia de una vez a otra). «Sincronizar» había medido ese desfase en SU grabación de prueba
(«Ajuste A/V» de +1270 ms) y en las siguientes ya era otro.

Aquí: una cámara (llega 0,2 s tarde) y un micrófono que tarda en abrirse 0,1 s en la primera
grabación y 1 s en la segunda. En las dos se ve y se oye a la vez el mismo código de la claqueta, y
se mide (analyze_sync, lo mismo que usa «Sincronizar») cuánto va la imagen detrás del sonido: tiene
que salir lo mismo en las dos (± 40 ms) y lo que es de verdad (0,2 s - lo que tarda el micro en
oír). Antes del arreglo, las dos medidas se llevaban ~0,9 s (lo que cambió la apertura del micro).

Y el «Ajuste A/V» guardado por un cam-ui de antes con «Marcas del dispositivo» (medido con la
cuenta vieja) se pone a 0 una vez, avisando de que hay que volver a «Sincronizar»."""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import io
import os
import sys
import json
import time
import logging
import shutil
import socket
import threading

import numpy as np
import tkinter as tk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_hub_audio_device_home")
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
OUT = os.path.join(HOME, "grabaciones")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# un config.json como el del usuario: guardado por un cam-ui de antes (sin "av_ref"), con
# «Marcas del dispositivo» y el «Ajuste A/V» que le dejó «Sincronizar» con la cuenta vieja
with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "fps": 30, "sync": "device", "av_offset": 1270, "container": "mkv",
               "dir": OUT, "phonecam": {"port": free_port()},
               "audio": {"L": {"dev": "Micro de prueba", "chan": "mix", "gain": 0, "delay": 0},
                         "R": {"dev": camui.NO_AUDIO, "chan": "mix", "gain": 0, "delay": 0}}}, f)


class World:
    """Cuándo empieza el código (hora del PC) y qué se ve / oye a cada hora."""
    epoch = None
    nbits = int(6.0 / BIT)

    @classmethod
    def at(cls, t):
        if cls.epoch is None:
            return None, False
        x = t - cls.epoch
        k = int(x // BIT)
        if x < 0 or k >= cls.nbits:
            return None, False
        return x, CODE[k] == 1


LAT, FPS = 0.20, 30                     # la cámara: llega 0,2 s tarde, a 30 fps
A_LAT = 0.03                            # el micrófono oye 0,03 s tarde
MIC_OPEN = [0.1]                        # lo que tarda en abrirse el micrófono (cambia en la 2ª)
stop = threading.Event()


def jpeg(val):
    a = np.full((180, 320, 3), 70, np.uint8)
    if val is not None:
        a[50:130, 110:210] = val
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=85)
    return b.getvalue()


def camera(app):
    sid = app.phonecam.new_session("Cam1")
    k = 0
    t_next = time.time()
    while not stop.is_set():
        k += 1
        now = time.time()
        ts = np.linspace(now - LAT - 1.0 / FPS, now - LAT, 8, endpoint=False)
        lit = [World.at(t) for t in ts]
        val = None
        if any(x is not None for x, _on in lit):
            val = int(15 + 225 * np.mean([1.0 if on else 0.0 for _x, on in lit]))
        app.phonecam.feed(sid, jpeg(val), seq=k)
        t_next += 1.0 / FPS
        time.sleep(max(0.0, t_next - time.time()))


APORT = free_port()


def mic_server():
    """El «micrófono»: ffmpeg se conecta aquí; el primer sonido llega MIC_OPEN s después (lo que
    tarda en abrirse de verdad un micrófono de Windows) y desde ahí, al ritmo real."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", APORT))
    srv.listen(4)
    srv.settimeout(0.5)
    rng = np.random.default_rng(5)

    def feed(conn, delay):
        time.sleep(delay)
        sr, n = 48000, 480
        t_next = time.time()
        pos = 0
        try:
            while not stop.is_set():
                tt = t_next - A_LAT + np.arange(n) / float(sr)
                x = 0.01 * rng.standard_normal(n)
                for i in range(0, n, 24):
                    if World.at(tt[i])[1]:
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
        threading.Thread(target=feed, args=(c, MIC_OPEN[0]), daemon=True).start()
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

LOGGED = []


class _Keep(logging.Handler):
    def emit(self, record):
        LOGGED.append(record.getMessage())


camui.log.addHandler(_Keep())
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


def record(mic_open):
    """Graba ~10 s con el código a mitad; devuelve lo que la imagen va detrás del sonido (s)."""
    MIC_OPEN[0] = mic_open
    World.epoch = None
    app.toggle_rec()
    assert pump(lambda: app.recording and not app.busy and app.eng.frame_count > 5, 30), \
        app.status.cget("text")
    m = dict(app.eng.meta)
    cmd = " ".join(app.eng.cmd)
    World.epoch = time.time() + 2.5
    pump(lambda: False, 2.5 + World.nbits * BIT + 2.0)
    app.toggle_rec()
    final = app.rec_final
    assert pump(lambda: not app.recording and not app.busy and os.path.isfile(final) and
                app.previewing, 60), app.status.cget("text")
    res = camui.analyze_sync(final, m["tiles"], m["W"], m["H"], ["📱 Cam1"], True, app.eng.ffmpeg,
                             World.nbits)
    assert res["audio"] is not None, res["info"]
    assert res["cams"].get("📱 Cam1") is not None, res["info"]
    return res["cams"]["📱 Cam1"][0] - res["audio"][0], cmd


try:
    assert pump(lambda: app.phonecam.active and not app.busy, 15)
    # 0) el «Ajuste A/V» de la cuenta vieja: a 0 una vez, con aviso
    assert app.v_avoff.get() == 0, app.v_avoff.get()
    log = "\n".join(LOGGED)
    assert "Ajuste A/V" in log and "1270" in log and "Sincronizar" in log, log[-2000:]
    threading.Thread(target=camera, args=(app,), daemon=True).start()
    assert pump(lambda: "📱 Cam1" in app.order and app.previewing and not app.busy and
                app.eng.frame_count > 30, 90), (app.order, app.eng.last_errors())
    assert app.v_hub.get() is True
    assert camui._pair_value(camui.SYNCS, app.v_sync.get()) == "device"
    assert app._audio_plan()["L"] is not None, "sin micro en el plan"
    assert "Sincronizar" in app.status.cget("text"), app.status.cget("text")
    app.save_config()
    with open(camui.CONFIG, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved.get("av_offset") == 0 and saved.get("av_ref") == "clock", saved
    print("0) el «Ajuste A/V» medido con la cuenta de antes (1270 ms) se pone a 0 una vez, y el "
          "registro dice que hay que volver a «Sincronizar» OK")

    d1, cmd1 = record(0.1)
    d2, cmd2 = record(1.0)
    print("   imagen detrás del sonido: {0:+.0f} ms (micro en 0,1 s), {1:+.0f} ms (micro en 1 s)"
          .format(d1 * 1000, d2 * 1000))
    assert "-use_wallclock_as_timestamps" in cmd1 and "-copyts" in cmd1, cmd1
    assert abs(d1 - d2) <= 0.040, (d1, d2)
    want = LAT - A_LAT
    assert abs(d1 - want) <= 0.080 and abs(d2 - want) <= 0.080, (d1, d2, want)
    print("1) con «Marcas del dispositivo» y el motor nuevo, el sonido va por la hora del reloj del "
          "PC igual que la imagen: abra el micro antes o después, la imagen va detrás del sonido lo "
          "mismo en las dos grabaciones ({0:+.0f} / {1:+.0f} ms; de verdad, {2:+.0f}) OK".format(
              d1 * 1000, d2 * 1000, want * 1000))
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
    shutil.rmtree(HOME, ignore_errors=True)
print("TODO OK ui_hub_audio_device")
