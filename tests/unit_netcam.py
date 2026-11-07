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

print("TODO OK unit_netcam")
