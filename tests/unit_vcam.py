import sys, os, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import camui
camui.setup_logging()

class FakeFmt: RGB = "RGB"; BGR = "BGR"; RGBA = "RGBA"
class FakeCam:
    inst = []
    def __init__(self, w, h, fps, fmt=None, **kw):
        if fmt in FakeCam.reject: raise RuntimeError("Unsupported image format (%s)" % fmt)
        self.w, self.h, self.fps, self.fmt = w, h, fps, fmt
        self.bpp = 4 if fmt == "RGBA" else 3
        self.frames = []; self.closed = False; self.device = "Fake Virtual Camera"
        FakeCam.inst.append(self)
    def send(self, f):
        assert f.shape == (self.h, self.w, self.bpp) and f.dtype == np.uint8, (f.shape, f.dtype)
        self.frames.append(f.copy())
    def close(self): self.closed = True
FakeCam.reject = set()
class FakeMod:
    PixelFormat = FakeFmt
    Camera = FakeCam

def lavfi(kind, spec, opt=None):
    return ["-re", "-f", "lavfi", "-i", spec]

def mkplan(vc=True, size=(1280, 720)):
    p = {"labels": ["A"], "cams": [{"spec": "testsrc2=size=640x480:rate=30", "mode": None, "h": False, "v": False,
         "rot": 0, "tile": "auto", "zoom": 100, "cx": 50, "cy": 50, "color": {}, "delay": 0, "src": (640, 480)}],
         "rows": (1,), "W": 640, "H": 480, "fps": 30, "sync": "device", "crf": 23, "preset": "ultrafast",
         "meter": False, "audio": {"L": None, "R": None, "offset": 0}, "tiles": [(0, 0, 640, 480)],
         "geom": {"W": 640, "H": 480, "tiles": [(0, 0, 640, 480)]}, "run_color": {}, "transpose": False}
    if vc: p["vcam"] = {"w": size[0], "h": size[1], "fps": 30}
    return p

# 1) comando: sin puerto no hay salida; con puerto, una sola salida tcp
cmd, _ = camui.build_command(mkplan(), None, 0, 0, True, lavfi, "ffmpeg")
assert not any("tcp://" in c for c in cmd)
p = mkplan(); p["vcam"]["port"] = 4242
cmd, _ = camui.build_command(p, None, 0, 0, True, lavfi, "ffmpeg")
assert cmd[-1].startswith("tcp://127.0.0.1:4242") or any(c == "tcp://127.0.0.1:4242" for c in cmd)
fc = cmd[cmd.index("-filter_complex") + 1]
assert "[mix]split=2[mp0][mvc0]" in fc, fc
cmd, _ = camui.build_command(p, "/tmp/x.mkv", 1.0, 0, True, lavfi, "ffmpeg")
fc = cmd[cmd.index("-filter_complex") + 1]
assert "[mix]split=3[mrec0][mp0][mvc0]" in fc, fc
print("comando OK")

# 2) extremo a extremo: ffmpeg real -> socket -> FakeCam
vc = camui.VirtualCam(module=FakeMod)
vc.open(1280, 720, 30)
assert vc.active and vc.device == "Fake Virtual Camera"
eng = camui.FFmpegEngine(); eng.vcam = vc
t = time.time()
ok, err = eng.start(mkplan(), input_fn=lavfi)
print("start", ok, err[-200:], "%.1fs" % (time.time() - t))
assert ok
time.sleep(3.0)
fr, n = eng.get_latest()
assert fr is not None and n > 5, "la vista previa sigue funcionando"
cam = FakeCam.inst[-1]
print("recibidos", vc.received, "enviados", vc.sent, "descartados", vc.dropped, "en cámara", len(cam.frames))
assert len(cam.frames) > 30
f = cam.frames[-1]
# 4:3 dentro de 16:9 -> barras negras a izquierda y derecha (960 px de imagen, 160 de barra)
assert f[:, :150, :3].max() == 0 and f[:, -150:, :3].max() == 0, "barras negras"
assert f[:, 200:1080, :3].max() > 100, "hay imagen en el centro"
assert cam.fmt == "RGB" and vc.pix == "rgb24", (cam.fmt, vc.pix)
eng.stop()
print("OK extremo a extremo: %d fotogramas %dx%d RGBA, letterbox correcto" % (len(cam.frames), 1280, 720))

# 3) grabando a la vez: el MKV queda completo y la cámara virtual también recibe
out = "/tmp/vcam_rec.mkv"
if os.path.exists(out): os.remove(out)
sent0 = vc.sent
ok, err = eng.start(mkplan(), outfile=out, warmup=1.0, input_fn=lavfi)
assert ok, err
time.sleep(4.0)
eng.stop()
sz = os.path.getsize(out)
dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", out]).decode().strip())
print("grabación %d bytes, %.1f s; enviados durante la grabación: %d" % (sz, dur, vc.sent - sent0))
assert dur > 2.0 and vc.sent - sent0 > 60

# 4) si la cámara se cierra (apagar) ffmpeg no se bloquea: se sigue vaciando el socket
ok, err = eng.start(mkplan(), input_fn=lavfi)
assert ok
time.sleep(1.5)
vc.release()
n0 = eng.get_latest()[1]
time.sleep(2.0)
n1 = eng.get_latest()[1]
assert n1 - n0 > 20, "la vista previa no debe pararse al cerrar la cámara virtual (%d)" % (n1 - n0)
assert FakeCam.inst[-1].closed
eng.stop()
print("OK: cerrar la cámara virtual en marcha no bloquea ffmpeg")

# 5) sin cámara activa el plan pierde la salida
eng2 = camui.FFmpegEngine(); eng2.vcam = vc          # vc ya está cerrada
p = mkplan(); ok, err = eng2.start(p, input_fn=lavfi)
assert ok and "vcam" not in p
eng2.stop()
vc.close()

# 6) formatos: el backend rechaza RGB y BGR -> usa RGBA (Unity Capture); rechaza todo -> error claro
FakeCam.reject = {"RGB", "BGR"}
v2 = camui.VirtualCam(module=FakeMod); v2.open(1280, 720, 30)
assert v2.pix == "rgba" and v2.bpp == 4 and FakeCam.inst[-1].fmt == "RGBA"
e2 = camui.FFmpegEngine(); e2.vcam = v2
p = mkplan(); p["vcam"]["pix"] = v2.pix
assert e2.start(p, input_fn=lavfi)[0]; time.sleep(2.5)
c2 = FakeCam.inst[-1]; assert len(c2.frames) > 20 and (c2.frames[-1][:, :, 3] == 255).all()
e2.stop(); v2.close()
FakeCam.reject = {"RGB", "BGR", "RGBA"}
try:
    camui.VirtualCam(module=FakeMod).open(1280, 720, 30); assert False
except RuntimeError as e:
    assert "Unsupported image format (RGB)" in str(e), e
FakeCam.reject = set()
print("OK formatos")
print("TODO OK")
