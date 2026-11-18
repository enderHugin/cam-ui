"""Motor nuevo, cámara de red MJPEG por HTTP que no es cam-ui (IP Webcam, ESP32-CAM...): se lee
en Python (pack 48), sin ffmpeg, para saber el tamaño de cada fotograma. Antes, si la cámara
cambiaba de forma a mitad (girar el móvil, otra resolución en su app), ffmpeg seguía dándola al
tamaño del principio, deformada, y el mosaico no se recolocaba. Servidores HTTP de verdad, con
las variantes que se ven por ahí: con y sin Content-Length, separador al estilo ESP32-CAM,
usuario y contraseña en la dirección, y una dirección que no es MJPEG (entonces, ffmpeg)."""
import io
import os
import sys
import time
import socket
import base64
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

STATE = {"wh": (640, 360), "clen": True, "bnd": "frame", "auth": None, "plain": False,
         "conns": 0}


def jpeg(w, h, k):
    a = np.full((h, w, 3), 90, np.uint8)
    a[h // 3:2 * h // 3, :, 0] = (k * 13) % 255
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="JPEG", quality=70)
    return b.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        STATE["conns"] += 1
        if STATE["auth"]:
            want = "Basic " + base64.b64encode(STATE["auth"].encode()).decode()
            if self.headers.get("Authorization") != want:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="cam"')
                self.end_headers()
                return
        if STATE["plain"]:                        # una página cualquiera, no un vídeo
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>hola</html>")
            return
        bnd = STATE["bnd"]
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + bnd)
        self.end_headers()
        k = 0
        try:
            while True:
                k += 1
                w, h = STATE["wh"]
                d = jpeg(w, h, k)
                head = "--{0}\r\nContent-Type: image/jpeg\r\n".format(bnd)
                if STATE["clen"]:
                    head += "Content-Length: {0}\r\nX-Timestamp: {1}.000\r\n".format(len(d), k)
                self.wfile.write(head.encode() + b"\r\n" + d + b"\r\n")
                time.sleep(1 / 20.0)
        except Exception:
            return


def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    srv = ThreadingHTTPServer(("127.0.0.1", p), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, p


def wait(cond, secs):
    t_end = time.time() + secs
    while time.time() < t_end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def run(case, url, **st):
    STATE.update({"wh": (640, 360), "clen": True, "bnd": "frame", "auth": None, "plain": False})
    STATE.update(st)
    hub = camui.CamHub("ffmpeg")
    src = camui._NetSource(hub, ("net", url), url, case)
    src.start()
    try:
        if st.get("plain"):                        # no es MJPEG: se lo pasa a ffmpeg
            assert wait(lambda: src.reader == "ffmpeg", 10), (case, src.reader, src.err)
            return {"reader": src.reader}
        assert wait(lambda: src.size == (640, 360), 10), (case, src.size, src.reader, src.err)
        out = {"reader": src.reader}
        if not st.get("plain"):
            STATE["wh"] = (360, 640)               # la cámara cambia de forma
            assert wait(lambda: src.size == (360, 640), 5), (case, src.size)
            assert wait(lambda: (src.fps() or 0) >= 15, 5), (case, src.fps())
            out["fps"] = round(src.fps() or 0)
        return out
    finally:
        src.close()
        src.done.wait(5)


srv, port = server()
base = "http://127.0.0.1:{0}/video".format(port)
try:
    r = run("IP Webcam", base)
    assert r["reader"] == "mjpeg", r
    print("1) MJPEG con Content-Length: se lee aquí ({0}) y sigue el cambio 640×360 → 360×640 "
          "en el acto, a {1} fps OK".format(r["reader"], r["fps"]))
    r = run("sin largo", base, clen=False)
    assert r["reader"] == "mjpeg", r
    print("2) sin Content-Length (hasta el siguiente separador): igual OK")
    r = run("ESP32-CAM", base, bnd="123456789000000000000987654321")
    assert r["reader"] == "mjpeg", r
    print("3) separador al estilo ESP32-CAM: igual OK")
    r = run("con clave", "http://ana:p%40ss@127.0.0.1:{0}/video".format(port), auth="ana:p@ss")
    assert r["reader"] == "mjpeg", r
    print("4) usuario y contraseña en la dirección (Basic): igual OK")
    r = run("página", base, plain=True)
    assert r["reader"] == "ffmpeg", r
    print("5) una dirección que no es MJPEG: la lee ffmpeg, como antes OK")
finally:
    srv.shutdown()
print("TODO OK unit_mjpeg")
