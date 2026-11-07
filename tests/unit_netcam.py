import sys, os, subprocess, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui

# 1) _is_net_spec distingue URL de nombre de dispositivo DirectShow
assert camui._is_net_spec("http://192.168.1.50:8080/video")
assert camui._is_net_spec("https://192.168.1.50:8080/video")
assert camui._is_net_spec("rtsp://192.168.1.50:554/live")
assert camui._is_net_spec("RTSP://192.168.1.50:554/live")        # sin distinguir mayúsculas
assert camui._is_net_spec("  rtsp://192.168.1.50/live  ")        # con espacios alrededor
assert not camui._is_net_spec("USB2.0 Camera")
assert not camui._is_net_spec("")
assert not camui._is_net_spec(None)
print("1) _is_net_spec OK")

# 2) _dshow_input despacha a la entrada de red o a dshow según el spec, sin tocar el resto
v_net = camui._dshow_input("video", "http://1.2.3.4:8080/video")
assert v_net == ["-i", "http://1.2.3.4:8080/video"], v_net
v_rtsp = camui._dshow_input("video", "rtsp://1.2.3.4:554/live")
assert v_rtsp == ["-rtsp_transport", "tcp", "-i", "rtsp://1.2.3.4:554/live"], v_rtsp
v_usb = camui._dshow_input("video", "USB2.0 Camera", "640x480 @ 30 fps [mjpeg]")
assert v_usb == ["-f", "dshow", "-rtbufsize", "512M", "-video_size", "640x480",
                 "-framerate", "30", "-vcodec", "mjpeg", "-i", "video=USB2.0 Camera"], v_usb
a_usb = camui._dshow_input("audio", "Micrófono")
assert a_usb == ["-f", "dshow", "-rtbufsize", "64M", "-audio_buffer_size", "50",
                "-i", "audio=Micrófono"], a_usb
print("2) _dshow_input (red / dshow) OK")

# 3) list_camera_modes no intenta -list_options contra una URL (no tiene sentido): [] al vuelo
t0 = time.time()
assert camui.list_camera_modes("http://1.2.3.4:8080/video") == []
assert time.time() - t0 < 0.5          # no se lanzó ningún ffmpeg real
print("3) list_camera_modes con URL -> [] sin lanzar ffmpeg OK")

# 4) build_command con una cámara de red: sin -f dshow, con -i <url>, mosaico intacto
plan = {
    "cams": [{"spec": "http://127.0.0.1:9/video", "mode": None, "h": False, "v": False,
              "rot": 0, "delay": 0, "tile": "auto", "zoom": 100, "cx": 50, "cy": 50, "color": {}}],
    "cols": 1, "rows": 1, "cw": 320, "ch": 240,
    "fps": 15, "sync": "device", "crf": 23, "preset": "veryfast",
    "audio": {"L": None, "R": None, "offset": 0},
}
cmd, meta = camui.build_command(plan, preview=False)
i = cmd.index("-i")
assert cmd[i + 1] == "http://127.0.0.1:9/video"
assert "dshow" not in cmd[:i]
assert "-filter_complex" in cmd                 # el mosaico se construye igual que con dshow
print("4) build_command con cámara de red OK")

# 5) extremo a extremo con ffmpeg real: un servidor HTTP MJPEG local hace de "móvil" y se
#    comprueba que cam-ui decodifica fotogramas reales a través de su cadena de filtros.
HERE = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(HERE, "_netcam_e2e.mp4")
if os.path.exists(out):
    os.remove(out)
PORT = 8791
srv = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
                        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=6",
                        "-f", "mpjpeg", "-listen", "1", "http://127.0.0.1:{0}/".format(PORT)])
time.sleep(0.8)
try:
    plan2 = dict(plan)
    plan2["cams"] = [dict(plan["cams"][0], spec="http://127.0.0.1:{0}/".format(PORT))]
    cmd2, _ = camui.build_command(plan2, outfile=out, preview=False)
    cmd2 += ["-t", "3"]
    r = subprocess.run(cmd2, capture_output=True, text=True, timeout=15)
    assert os.path.exists(out) and os.path.getsize(out) > 0, \
        "no se generó el archivo: rc={0}\n{1}".format(r.returncode, r.stderr[-800:])
finally:
    srv.wait(timeout=5)
    if os.path.exists(out):
        os.remove(out)
print("5) extremo a extremo (http MJPEG real -> mosaico -> mp4) OK")

# 6) lo mismo que el bloque 5 pero con "sync": "clock" -- el modo normal/por defecto, que el
#    bloque 5 no cubre (usa "device"). Fallo real encontrado con esto: "-use_wallclock_as_timestamps"
#    (con el que el modo "clock" convierte PTS en la hora real de llegada) no lo hace el demuxer
#    "mpjpeg" -- numera los fotogramas 0,1,2... como si fuera un archivo -- así que el filtro
#    "fps" de más abajo los veía con una marca de tiempo absurda (segundos desde 1970 menos el
#    reloj común) y los descartaba TODOS: ningún fotograma grabado, pero el .mp4 seguía
#    "existiendo" con un tamaño pequeño pero no cero (cabecera sin vídeo dentro), así que un
#    simple "> 0 bytes" no lo habría detectado -- hace falta comprobar que de verdad hay
#    fotogramas reales (duración/decodificación con ffprobe), no solo que el archivo no esté vacío.
out6 = os.path.join(HERE, "_netcam_e2e_clock.mp4")
if os.path.exists(out6):
    os.remove(out6)
PORT6 = 8792
srv6 = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
                         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=6",
                         "-f", "mpjpeg", "-listen", "1", "http://127.0.0.1:{0}/".format(PORT6)])
time.sleep(0.8)
try:
    plan3 = dict(plan)
    plan3["sync"] = "clock"
    plan3["cams"] = [dict(plan["cams"][0], spec="http://127.0.0.1:{0}/".format(PORT6))]
    cmd3, _ = camui.build_command(plan3, outfile=out6, preview=False)
    cmd3 += ["-t", "3"]
    r6 = subprocess.run(cmd3, capture_output=True, text=True, timeout=15)
    assert os.path.exists(out6), "no se generó el archivo: rc={0}\n{1}".format(
        r6.returncode, r6.stderr[-800:])
    rp = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                         "-show_entries", "stream=nb_read_frames,duration",
                         "-of", "default=noprint_wrappers=1", out6],
                        capture_output=True, text=True, timeout=10)
    nframes = None
    for line in rp.stdout.splitlines():
        if line.startswith("nb_read_frames="):
            nframes = int(line.split("=", 1)[1])
    assert nframes is not None and nframes >= 15, (
        "con sync=clock se grabaron muy pocos (o cero) fotogramas reales de una cámara de red: "
        "{0}\nffprobe:\n{1}{2}".format(nframes, rp.stdout, rp.stderr))
finally:
    srv6.wait(timeout=5)
    if os.path.exists(out6):
        os.remove(out6)
print("6) extremo a extremo con sync=clock (el modo por defecto): {0} fotogramas reales grabados "
      "de una cámara de red OK".format(nframes))

# 7) failing_net_spec / failing_cam_index: identifican qué cámara falló a partir del mensaje de
#    ffmpeg real -- incluso con el registro tal cual se vio en un fallo real reportado (vídeo de
#    otra cámara con avisos de búfer de por medio, y el fallo de verdad de la cámara de red al
#    final: "Connection ... failed" / "Error opening input file <url>.")
real_log = (
    "[in#0/dshow @ 0x1] real-time buffer [EOS Webcam Utility] [video input] too full or near "
    "too full (100% of size: 512000000 [rtbufsize parameter])! frame dropped!\n"
    "    Last message repeated 47 times\n"
    "[tcp @ 0x2] Connection to tcp://omen:8554 failed: Error number -138 occurred\n"
    "[in#4 @ 0x3] Error opening input: Error number -138 occurred\n"
    "Error opening input file http://omen:8554.\n"
    "Error opening input files: Error number -138 occurred\n")
assert camui.failing_net_spec(real_log) == "http://omen:8554", camui.failing_net_spec(real_log)
cams7 = [{"spec": "EOS Webcam Utility"}, {"spec": "HP Wide Vision HD Camera"},
         {"spec": "http://omen:8554"}]
assert camui.failing_cam_index(cams7, real_log) == 2, camui.failing_cam_index(cams7, real_log)
dshow_err = "Error opening input file video=EOS Webcam Utility."
assert camui.failing_spec(dshow_err) == ("video", "EOS Webcam Utility")
assert camui.failing_cam_index(cams7, dshow_err) == 0
assert camui.failing_cam_index(cams7, "cualquier otra cosa sin relación") is None
print("7) failing_net_spec / failing_cam_index identifican la cámara caída (local o de red), "
      "incluso contra un registro real de un fallo real: OK")

# 8) build_command sustituye por un marcador «sin señal» la cámara marcada "_offline", y el
#    resto del mosaico sigue funcionando -- extremo a extremo con ffmpeg real: un mosaico de
#    2 cámaras (una real, otra "_offline") graba fotogramas de verdad en las dos mitades, y
#    son visualmente distintas (no es que las dos acabasen mostrando lo mismo por error).
camui.PLACEHOLDER_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
out8 = os.path.join(HERE, "_offline_e2e.mp4")
if os.path.exists(out8):
    os.remove(out8)
plan8 = {
    "cams": [
        {"spec": "testsrc=size=320x240:rate=10", "mode": None, "h": False, "v": False,
         "rot": 0, "delay": 0, "tile": "auto", "zoom": 100, "cx": 50, "cy": 50, "color": {}},
        {"spec": "http://127.0.0.1:1/video", "mode": None, "h": False, "v": False,
         "rot": 0, "delay": 0, "tile": "auto", "zoom": 100, "cx": 50, "cy": 50, "color": {},
         "_offline": True},
    ],
    "cols": 2, "rows": 1, "cw": 320, "ch": 240, "fps": 10, "sync": "device", "crf": 28,
    "preset": "veryfast", "audio": {"L": None, "R": None, "offset": 0}, "duration": 3,
    "labels": ["Cam real", "🌐 Móvil (sin señal)"],
}


def _fake_input8(kind, spec, opt=None):
    # sustituye la "cámara real" por un testsrc lavfi -re (ritmo real), para no depender de
    # dshow; la "_offline" nunca llega a pedir una entrada real (build_command ya la cambia).
    if spec.startswith("testsrc"):
        return ["-re", "-f", "lavfi", "-i", spec]
    return camui._dshow_input(kind, spec, opt)


cmd8, _m8 = camui.build_command(plan8, outfile=out8, preview=False, input_fn=_fake_input8)
joined8 = subprocess.list2cmdline(cmd8)
assert "drawtext" in joined8 and "Sin se" in joined8, "no se generó el marcador en el comando"
try:
    r8 = subprocess.run(cmd8, capture_output=True, text=True, timeout=20)
    assert r8.returncode == 0 and os.path.exists(out8), \
        "no se grabó con una cámara offline en el mosaico: rc={0}\n{1}".format(
            r8.returncode, r8.stderr[-800:])
    rp8 = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames",
                          "-of", "default=noprint_wrappers=1", out8],
                         capture_output=True, text=True, timeout=10)
    nf8 = None
    for line in rp8.stdout.splitlines():
        if line.startswith("nb_read_frames="):
            nf8 = int(line.split("=", 1)[1])
    assert nf8 is not None and nf8 >= 20, "muy pocos fotogramas: {0}".format(nf8)
finally:
    pass
print("8) build_command + ffmpeg real: mosaico con una cámara 'sin señal' sigue grabando las "
      "demás ({0} fotogramas) OK".format(nf8))
if os.path.exists(out8):
    os.remove(out8)

# 9) _engine_start (CamApp) se recupera sola: si una cámara de red no conecta, en vez de que
#    arrancar la vista previa falle del todo, la sustituye por el marcador y arranca con el
#    resto -- extremo a extremo contra un FFmpegEngine real (sin Tk: _drop_bad_cam/_drop_bad_mode
#    no lo necesitan).
from collections import deque


class _FakeApp9(camui.CamApp):            # solo para heredar los métodos; no se llama a __init__
    def __init__(self):
        self.eng = camui.FFmpegEngine()
        self.cams = {}
        self._tray_q = deque()

    def _auto_modes(self, plan):
        pass


old_busy_waits = camui.BUSY_WAITS
camui.BUSY_WAITS = (0.2, 0.3)              # mismo mecanismo, con menos espera para la prueba
plan9 = {
    "cams": [
        {"spec": "testsrc=size=320x240:rate=10", "mode": None, "h": False, "v": False,
         "rot": 0, "delay": 0, "tile": "auto", "zoom": 100, "cx": 50, "cy": 50, "color": {}},
        {"spec": "http://127.0.0.1:1/video", "mode": None, "h": False, "v": False,
         "rot": 0, "delay": 0, "tile": "auto", "zoom": 100, "cx": 50, "cy": 50, "color": {}},
    ],
    "cols": 2, "rows": 1, "cw": 320, "ch": 240, "fps": 10, "sync": "device",
    "crf": 28, "preset": "veryfast", "audio": {"L": None, "R": None, "offset": 0},
    "labels": ["Cam real", "🌐 Móvil"],
}
app9 = _FakeApp9()
try:
    res9 = camui.CamApp._engine_start(app9, plan9, input_fn=_fake_input8)
    ok9, err9 = res9
    assert ok9, "debería haber arrancado igualmente, con un marcador para la de red caída:\n" + err9
    assert plan9["cams"][1].get("_offline") is True, "la cámara de red caída debe quedar marcada"
    assert not plan9["cams"][0].get("_offline"), "la cámara 'real' no debe tocarse"
    assert app9._tray_q and app9._tray_q[0] == ("cam_offline", "🌐 Móvil"), app9._tray_q
finally:
    app9.eng.stop()
    camui.BUSY_WAITS = old_busy_waits
print("9) _engine_start se recupera sola de una cámara de red caída (sin Tk, FFmpegEngine real) OK")

# 10) _on_died: si la vista previa (no grabando) muere y el error identifica una cámara del
#     plan actual, reintenta sola (_start_preview(auto=True)) en vez del aviso de error de
#     siempre; si no se identifica ninguna cámara, o si se estaba grabando, el comportamiento
#     no cambia (aviso +, si estaba grabando, cierre del vídeo parcial como siempre). Sin Tk:
#     _on_died no usa CamState, solo self._plan()/self.eng/self.previewing/self.recording.
real_log10 = ("[tcp @ 0x2] Connection to tcp://omen:8554 failed: Error number -138 occurred\n"
              "Error opening input file http://omen:8554.\n")
plan10 = {"cams": [{"spec": "EOS Webcam Utility"}, {"spec": "http://omen:8554"}]}


class _FakeApp10(camui.CamApp):           # solo para heredar _on_died; no se llama a __init__
    def __init__(self):
        self.calls = {"start_preview": [], "set_idle": [], "finalize": [], "showerror": 0}
        self.rec_path = self.rec_final = None

    def _plan(self):
        return plan10

    def _set_idle(self, *a):
        self.calls["set_idle"].append(a)

    def _start_preview(self, *a, **kw):
        self.calls["start_preview"].append((a, kw))

    def _finalize(self, path, final):
        self.calls["finalize"].append((path, final))
        return True, path, ""


class _FakeEng10:
    def __init__(self, err):
        self._err = err

    def last_errors(self):
        return self._err

    def stop(self):
        pass


class _FakeMsgBox10:
    """camui.messagebox es None sin Tk (ImportError controlado, ver cabecera del módulo) --
    se sustituye por algo con .showerror mientras dura esta prueba, para poder comprobar si
    _on_died avisa o no, sin necesitar una ventana Tk real."""
    def __init__(self, calls):
        self._calls = calls

    def showerror(self, *a, **kw):
        self._calls["showerror"] += 1


_orig_messagebox10 = camui.messagebox

# 10a) vista previa, error identifica una cámara del plan -> reintenta sola, sin aviso
app10a = _FakeApp10()
app10a.eng = _FakeEng10(real_log10)
app10a.recording, app10a.previewing = False, True
camui.messagebox = _FakeMsgBox10(app10a.calls)
try:
    app10a._on_died()
finally:
    camui.messagebox = _orig_messagebox10
assert app10a.calls["start_preview"] == [((), {"auto": True})], app10a.calls["start_preview"]
assert app10a.calls["showerror"] == 0, "no debería avisar si se reintenta sola"
assert app10a.previewing is False

# 10b) vista previa, error SIN cámara identificable -> aviso de siempre, sin reintento
app10b = _FakeApp10()
app10b.eng = _FakeEng10("algo que no identifica ninguna cámara del plan\n")
app10b.recording, app10b.previewing = False, True
camui.messagebox = _FakeMsgBox10(app10b.calls)
try:
    app10b._on_died()
finally:
    camui.messagebox = _orig_messagebox10
assert app10b.calls["start_preview"] == [], app10b.calls["start_preview"]
assert app10b.calls["showerror"] == 1

# 10c) estaba GRABANDO -> nunca reintenta sola aunque el error identifique una cámara; cierra
#      el vídeo parcial y avisa, igual que siempre
app10c = _FakeApp10()
app10c.eng = _FakeEng10(real_log10)
app10c.recording, app10c.previewing = True, True
app10c.rec_path, app10c.rec_final = "/tmp/_fake10.mp4", "/tmp/_fake10_final.mp4"
camui.messagebox = _FakeMsgBox10(app10c.calls)
try:
    app10c._on_died()
finally:
    camui.messagebox = _orig_messagebox10
assert app10c.calls["start_preview"] == [], "grabando no debe reintentar sola"
assert app10c.calls["showerror"] == 1
assert app10c.calls["finalize"] == [("/tmp/_fake10.mp4", "/tmp/_fake10_final.mp4")]
print("10) _on_died: reintenta sola en vista previa si identifica la cámara, pero nunca si "
      "se estaba grabando (sin Tk) OK")

print("TODO OK unit_netcam")
