import sys, time, subprocess, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui

REAL1 = r'''[dshow @ 000001a8a1bc32c0] DirectShow video device options (from video devices)
[dshow @ 000001a8a1bc32c0]  Pin "Capturar" (alternative pin name "0")
[dshow @ 000001a8a1bc32c0]   pixel_format=yuyv422  min s=640x480 fps=15 max s=640x480 fps=30
[dshow @ 000001a8a1bc32c0]   pixel_format=yuyv422  min s=640x480 fps=15 max s=640x480 fps=30 (tv, bt470bg/bt709/unknown, topleft)
[dshow @ 000001a8a1bc32c0]   pixel_format=yuyv422  min s=352x288 fps=15 max s=352x288 fps=30
[dshow @ 000001a8a1bc32c0]   pixel_format=yuyv422  min s=320x240 fps=15 max s=320x240 fps=30
[dshow @ 000001a8a1bc32c0]   pixel_format=yuyv422  min s=176x144 fps=15 max s=176x144 fps=30
[dshow @ 000001a8a1bc32c0]   pixel_format=yuyv422  min s=160x120 fps=15 max s=160x120 fps=30
Error opening input file video=@device_pnp_\\?\usb#vid_1908&pid_2311.
'''
m = camui.parse_camera_modes(REAL1)
print(m)
assert m[0] == "640x480 @ 30 fps [yuyv422]" and "640x480 @ 15 fps [yuyv422]" in m and "640x480 @ 20 fps [yuyv422]" in m
assert len(m) == 5 * 4 and len(set(m)) == len(m)
assert [camui.mode_size(x) for x in m[::4]] == [(640, 480), (352, 288), (320, 240), (176, 144), (160, 120)]
assert camui.pick_auto_mode(m, 30) == "640x480 @ 30 fps [yuyv422]"
assert camui.pick_auto_mode(m, 15) == "640x480 @ 30 fps [yuyv422]"          # misma elección: más fps a igual tamaño
RANGE = "[dshow @ 0x1]   vcodec=mjpeg  min s=160x120 fps=5 max s=1920x1080 fps=30\n[dshow @ 0x1]   pixel_format=yuyv422  min s=640x480 fps=5 max s=640x480 fps=29.97\n"
r = camui.parse_camera_modes(RANGE)
print(r[:6], len(r))
assert "1920x1080 @ 30 fps [mjpeg]" in r and "1280x720 @ 30 fps [mjpeg]" in r and "320x240 @ 30 fps [mjpeg]" in r
assert "640x480 @ 29.97 fps [yuyv422]" in r and "640x480 @ 20 fps [yuyv422]" in r and "640x480 @ 30 fps [yuyv422]" not in r
assert camui.pick_auto_mode(r, 30) == "1280x720 @ 30 fps [mjpeg]"
# capture_args de las nuevas variantes
assert camui.capture_args("640x480 @ 15 fps [yuyv422]") == ["-video_size", "640x480", "-framerate", "15", "-pixel_format", "yuyv422"]
print("1) parser OK")

ERR = r'''Input #0, dshow, from 'video=EOS Webcam Utility':
[dshow @ 000001aa0a35e0c0] Could not set video options
[in#1 @ 000001aa0a357b80] Error opening input: I/O error
Error opening input file video=@device_pnp_\\?\usb#vid_1908&pid_2311&mi_00#9&22d0f0b0&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global.
Error opening input files: I/O error'''
assert camui.is_mode_error(ERR) and not camui.is_busy_error(ERR)
assert camui.is_busy_error("[dshow @ 0x1] Could not run graph (sometimes caused by a device already in use)\nError opening input: I/O error")
assert not camui.is_mode_error("Could not run graph")
k, spec = camui.failing_spec(ERR); assert k == "video" and spec.startswith("@device_pnp_")
print("2) clasificación de errores OK")

# 3) pick con medidas reales
M = camui.parse_camera_modes(REAL1)
ok = {"640x480 @ 30 fps [yuyv422]": 10.0, "640x480 @ 15 fps [yuyv422]": 14.5, "320x240 @ 30 fps [yuyv422]": 28.0}
assert camui.pick_auto_mode(M, 30, ok=ok) == "320x240 @ 30 fps [yuyv422]"      # fluidez antes que tamaño
ok2 = {k: v for k, v in ok.items() if not k.startswith("320")}
assert camui.pick_auto_mode(M, 30, ok=ok2) == "640x480 @ 15 fps [yuyv422]"   # 25 y 20 sin probar: heredan el límite de luz
assert camui.pick_auto_mode(M, 30, ok={"640x480 @ 30 fps [yuyv422]": 29.4, "320x240 @ 30 fps [yuyv422]": 29.9}) == "640x480 @ 30 fps [yuyv422]"
# solo funciona 320x240 -> nunca se elige 640x480 aunque exista en la lista
assert camui.pick_auto_mode(M, 30, ok={"320x240 @ 30 fps [yuyv422]": 29.0}) == "320x240 @ 30 fps [yuyv422]"
print("3) selección con fps medidos OK")

# 4) probe_modes con ffmpeg simulado
calls = []
def runner(cmd):
    j = " ".join(cmd); calls.append(j)
    mm = re.search(r"-video_size (\d+x\d+) -framerate ([\d.]+)", j)
    size, f = mm.group(1), float(mm.group(2))
    if size in ("352x288", "160x120"):
        return "[dshow @ 0x1] Could not set video options\nError opening input file video=X.\n"
    real = {"640x480": {30: 10, 25: 10, 20: 10, 15: 14.5}, "320x240": {30: 28.8}, "176x144": {30: 29.5}}[size].get(f, f)
    return "Stream #0:0: Video: rawvideo, yuyv422, %s, %g fps\nframe=%4d fps=0.0\n" % (size, f, int(real * 3))
prog = []
res = camui.probe_modes("ffmpeg", "CAM", M, secs=3.0, runner=runner, progress=lambda i, n, m: prog.append((i, n)))
for r in res: print("   ", r["mode"], "%.1f" % r["fps"], r["err"][:30])
by = {r["mode"]: r["fps"] for r in res}
assert by["640x480 @ 30 fps [yuyv422]"] == 10.0 and abs(by["640x480 @ 15 fps [yuyv422]"] - 14.33) < 0.1
assert by["352x288 @ 30 fps [yuyv422]"] == 0 and by["160x120 @ 30 fps [yuyv422]"] == 0
assert "640x480 @ 25 fps [yuyv422]" in by and "640x480 @ 20 fps [yuyv422]" in by      # etapa 2: fps bajos hasta que llega
assert prog[0] == (1, 5) and "-pixel_format yuyv422" in calls[0] and "video=CAM" in calls[0]
print("4) probe_modes OK;", len(res), "pruebas")
print("TODO OK unit")
