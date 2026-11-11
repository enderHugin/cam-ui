# -*- coding: utf-8 -*-
"""
CamWin32 GUI v2 - cámaras / multivista para Windows.
Backend FFmpeg (DirectShow) + GUI Tkinter.

Novedades respecto a la v1
--------------------------
SINCRONÍA
  * Todas las entradas (cámaras y audio) se sellan con el MISMO reloj de pared y se
    reubican en una línea de tiempo común (T0 = instante de lanzamiento), así el vídeo
    de cada cámara y el audio quedan alineados aunque cada dispositivo tarde distinto
    en arrancar. Modo alternativo: "marcas del dispositivo".
  * Audio con aresample async (corrige la deriva entre relojes de dispositivos).
  * Ajustes finos: retardo por cámara (ms), ajuste general audio<->vídeo (ms) y
    retardo por canal de audio.
  * Los primeros segundos de "arranque" (dispositivos abriéndose) se recortan del MP4.
RECORTE / FORMATO
  * Por cámara: voltear H/V, girar 90/180/270, recorte por lados (%), ajuste a la
    celda (barras / rellenar recortando / estirar), modo de captura (resolución, fps,
    MJPEG...) detectado desde el propio dispositivo.
  * Formato de celda: 16:9, 4:3, 3:2, 1:1, 9:16 (vertical), 21:9 + ancho elegible.
AUDIO L/R
  * Cada canal de salida (Izquierdo / Derecho) se alimenta de CUALQUIER dispositivo y
    de cualquier canal de origen (izq / der / mezcla), con ganancia y retardo propios.
    Ejemplo: micro A -> canal izquierdo, micro B -> canal derecho.
  * Vúmetro L/R en vivo.
INTERFAZ
  * La vista previa NO se corta al grabar (un único proceso ffmpeg con doble salida).
  * Cambios en vivo: al tocar un ajuste la vista previa se reinicia sola.
  * Mapa del mosaico: clic para elegir cámara, arrastrar para reordenar.
  * Temporizador/tamaño de la grabación, foto de la vista previa, atajos F5/F8/F9.
  * Detecta cámaras duplicadas (mismo nombre) usando el nombre alternativo de DirectShow.
CÁMARA VIRTUAL (opcional)
  * El mosaico se ofrece como cámara a Teams / Zoom / Meet: ffmpeg lo envía en RGB por un
    socket local y pyvirtualcam lo pasa a la cámara virtual de OBS Studio (hay que tener OBS
    instalado y «pip install pyvirtualcam»; la aplicación ofrece instalarlo). Solo vídeo.

Uso:  python camui.py  |  pythonw camui.py  |  python camui.py list  |  python camui.py multi -h
"""
import os
import io
import re
import sys
import json
import ssl
import math
import time
import secrets
import argparse
import queue
import socket
import hashlib
import http.server
import shutil
import tempfile
import webbrowser
import threading
import subprocess
import datetime
import logging
import faulthandler
import traceback
from collections import deque

import numpy as np

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import Image, ImageTk
except ImportError:          # sin Tk: solo funciona la consola (y los tests)
    tk = ttk = filedialog = messagebox = ImageTk = None
    from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
DEF_DIR = os.path.join(HERE, "Capturas")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ---- actualizaciones: mismo sistema que widget-arena (huella sha256, copia de seguridad,
# vuelta atrás, reinicio solo), adaptado a un único archivo -----------------------------
APP_VERSION = "2026.11.08"            # ha de coincidir con el archivo VERSION del repositorio
APP_PACK = 30                         # crece en cada actualización publicada: así se sabe qué es más nuevo
UPDATE_BASE = "https://enderhugin.github.io/cam-ui/dl/"
UPDATE_STATE = os.path.join(HERE, "actualizacion.json")
UPDATE_BACKUPS = os.path.join(HERE, "backups_programa")
APP_FILE = os.path.abspath(__file__)
UPDATE_FILES = ("camui.py",)
# «vigilar sola, sin avisos» (casilla oculta, ver UpdateDialog): comprobación aparte de la
# normal (update_auto_check, una vez al abrir, con su propia hora de descanso), para quien
# quiera ver aplicarse una publicación nueva sin tener que cerrar y abrir cam-ui a mano
SILENT_WATCH_INTERVAL_MS = 60000


def _resolve_tool(name, env_var, default):
    """Busca una herramienta: variable de entorno > bin\\ junto al script > carpeta
    del script > ruta por defecto > PATH. Así el kit se puede mover/copiar."""
    if os.name != "nt":
        name = name.replace(".exe", "")
    for cand in (os.environ.get(env_var), os.path.join(HERE, "bin", name),
                 os.path.join(HERE, name), default):
        if cand and os.path.isfile(cand):
            return cand
    return name  # confía en el PATH del sistema


LOGFILE = os.path.join(HERE, "camui.log")
log = logging.getLogger("camui")
log.addHandler(logging.NullHandler())
log.propagate = False
_hang_fh = None
_log_ready = False


def setup_logging():
    """Registro en camui.log (junto al script) + volcado de pilas de TODOS los hilos si la
    interfaz se queda colgada (vigilante en CamApp._tick) o el proceso falla."""
    global _hang_fh, _log_ready
    if _log_ready:
        return
    try:
        if os.path.isfile(LOGFILE) and os.path.getsize(LOGFILE) > 1500000:
            open(LOGFILE, "w").close()
        h = logging.FileHandler(LOGFILE, mode="a", encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(threadName)s] %(levelname)s %(message)s",
            "%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.DEBUG)
        _hang_fh = open(LOGFILE, "a", encoding="utf-8")
        faulthandler.enable(file=_hang_fh, all_threads=True)
    except Exception:
        return
    _log_ready = True

    def hook(et, ev, tb):
        log.error("EXCEPCION no controlada:\n%s", "".join(traceback.format_exception(et, ev, tb)))
    sys.excepthook = hook
    threading.excepthook = lambda a: log.error(
        "EXCEPCION en hilo %s:\n%s", a.thread.name if a.thread else "?",
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    log.info("=== CamWin32 v2 | Python %s | %s | ffmpeg=%s | tk=%s | exe=%s",
             sys.version.split()[0], sys.platform, FFMPEG, getattr(tk, "TkVersion", None),
             sys.executable)


FFMPEG = _resolve_tool("ffmpeg.exe", "CAMWIN32_FFMPEG", r"C:\io\Tools\ffmpeg.exe")
FFPLAY = _resolve_tool("ffplay.exe", "CAMWIN32_FFPLAY", r"C:\io\Tools\ffplay.exe")
# fuente para el aviso «Sin señal» (ver _offline_lavfi): de serie en Windows desde siempre,
# no depende de que haya fontconfig en el ffmpeg instalado. En pruebas (Linux) se sustituye
# por una fuente real del sistema.
PLACEHOLDER_FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                "Fonts", "arial.ttf").replace("\\", "/")

ASPECTS = [("16:9", 16, 9), ("4:3", 4, 3), ("3:2", 3, 2), ("5:4", 5, 4), ("1:1", 1, 1),
           ("4:5 (vertical)", 4, 5), ("9:16 (vertical)", 9, 16), ("21:9", 21, 9), ("2:1", 2, 1)]
ADAPT = "Adaptado al contenido"
CANVASES = [ADAPT] + [a[0] for a in ASPECTS]
TILES = [("Como la cámara (sin recorte)", "auto"), ("4:3", "4:3"), ("3:4 (vertical)", "3:4"),
         ("16:9", "16:9"), ("9:16 (vertical)", "9:16"), ("3:2", "3:2"),
         ("2:3 (vertical)", "2:3"), ("5:4", "5:4"), ("4:5 (vertical)", "4:5"), ("1:1", "1:1"),
         ("21:9", "21:9"), ("2:1", "2:1"), ("2.39:1 (cine)", "2.39:1")]
COLOR_KEYS = ("bri", "con", "sat", "gam", "temp")
COLOR_LABELS = (("bri", "Brillo"), ("con", "Contraste"), ("sat", "Saturación"),
                ("gam", "Gamma"), ("temp", "Temperatura"))
AUTO = "Auto"
CHANS = [("Canal izquierdo", "left"), ("Canal derecho", "right"), ("Mezcla L+R", "mix")]
QUALITIES = [("Alta (archivo grande)", 18), ("Media", 23), ("Baja (archivo pequeño)", 28)]
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "medium"]
SYNCS = [("Reloj común (recomendado)", "clock"), ("Marcas del dispositivo", "device")]
ROTS = ["0°", "90°", "180°", "270°"]
NO_AUDIO = "(Silencio)"


# =====================================================================
#  Dispositivos
# =====================================================================
def _run_ffmpeg(args, ffmpeg=None):
    cmd = [ffmpeg or FFMPEG, "-hide_banner"] + args
    t = time.time()
    log.info("run_ffmpeg: %s", subprocess.list2cmdline(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=CREATE_NO_WINDOW, timeout=20,
                           stdin=subprocess.DEVNULL)
        txt = (r.stderr or "") + (r.stdout or "")
        log.info("run_ffmpeg: rc=%s en %.2fs, %d lineas", r.returncode, time.time() - t,
                 len(txt.splitlines()))
        log.debug("salida de ffmpeg:\n%s", txt[:6000])
        return txt
    except Exception as e:
        log.error("run_ffmpeg fallo tras %.2fs: %r", time.time() - t, e)
        return ""


_DEV_RE = re.compile(r'"(.+?)"\s+\(([^)]*)\)\s*$')
_ALT_RE = re.compile(r'Alternative name\s+"(.+?)"')


def parse_dshow_devices(txt):
    """Devuelve (videos, audios): listas de dicts {name, alt}."""
    entries, last = [], None
    for line in txt.splitlines():
        m = _DEV_RE.search(line)
        if m:
            kinds = [k.strip() for k in m.group(2).split(",")]
            last = {"name": m.group(1), "alt": None, "kinds": kinds}
            entries.append(last)
            continue
        m = _ALT_RE.search(line)
        if m and last is not None and last["alt"] is None:
            last["alt"] = m.group(1)
    video = [{"name": e["name"], "alt": e["alt"]} for e in entries if "video" in e["kinds"]]
    audio = [{"name": e["name"], "alt": e["alt"]} for e in entries if "audio" in e["kinds"]]
    return video, audio


def label_devices(devs):
    """Añade label (único para la UI) y spec (cadena que se pasa a ffmpeg).
    Si hay dos dispositivos con el mismo nombre se usa el nombre alternativo."""
    total = {}
    for d in devs:
        total[d["name"]] = total.get(d["name"], 0) + 1
    seen = {}
    for d in devs:
        n = d["name"]
        seen[n] = seen.get(n, 0) + 1
        d["dup"] = total[n] > 1
        d["label"] = n if not d["dup"] else "{0} #{1}".format(n, seen[n])
        d["spec"] = d["alt"] if (d["dup"] and d["alt"]) else n
    return devs


def list_devices():
    txt = _run_ffmpeg(["-list_devices", "true", "-f", "dshow", "-i", "dummy"])
    video, audio = parse_dshow_devices(txt)
    log.info("dispositivos: %d video, %d audio", len(video), len(audio))
    return label_devices(video), label_devices(audio)


def list_video_pygrabber():
    """Plan B (COM): llamar SOLO desde el hilo principal."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return [{"name": n, "alt": None} for n in FilterGraph().get_input_devices()]
    except Exception as e:
        log.error("pygrabber fallo: %r", e)
        return []


_MODE_RE = re.compile(r'(?:pixel_format|vcodec)=(\S+)\s+min s=(\d+)x(\d+) fps=([\d.]+) '
                      r'max s=(\d+)x(\d+) fps=([\d.]+)')
_MODE_PARSE = re.compile(r'^\s*(\d+x\d+)\s*@\s*([\d.]+)\s*fps\s*\[(\w+)\]')
STD_SIZES = [(160, 120), (176, 144), (320, 240), (352, 288), (640, 360), (640, 480), (800, 600),
             (960, 540), (1024, 768), (1280, 720), (1280, 960), (1280, 1024), (1600, 1200),
             (1920, 1080), (2560, 1440), (3840, 2160)]
STD_FPS = (60, 30, 25, 20, 15, 10, 5)


def _fps_steps(lo, hi):
    """fps que ofrecer dentro de [lo, hi]: el máximo y los habituales por debajo (una cámara con
    poca luz alarga la exposición a 30 fps; a 15 o 10 suele dar una imagen fluida)."""
    out = [hi]
    for f in STD_FPS:
        if lo - 0.6 <= f < hi - 0.6 and all(abs(f - o) > 0.6 for o in out):
            out.append(f)
    return out


def parse_camera_modes(txt):
    """Modos «WxH @ F fps [formato]» a partir de `-list_options`. Cada línea de DirectShow es
    un RANGO (tamaño mín–máx y fps mín–máx): se ofrecen todos los fps habituales de ese rango y,
    si el tamaño también es un rango, los tamaños estándar que caben en él."""
    modes = []
    for line in txt.splitlines():
        m = _MODE_RE.search(line)
        if not m:
            continue
        codec = m.group(1)
        minw, minh, maxw, maxh = int(m.group(2)), int(m.group(3)), int(m.group(5)), int(m.group(6))
        f1, f2 = float(m.group(4)), float(m.group(7))
        lo, hi = min(f1, f2), max(f1, f2)
        if hi <= 0 or maxw <= 0 or maxh <= 0:
            continue
        sizes = [(maxw, maxh)]
        steps = _fps_steps(lo, hi)
        if (minw, minh) != (maxw, maxh):
            sizes += [(w, h) for (w, h) in STD_SIZES
                      if minw <= w <= maxw and minh <= h <= maxh and (w, h) != (maxw, maxh)]
            steps = steps[:3]
        for w, h in sizes:
            for f in steps:
                sm = "{0}x{1} @ {2:g} fps [{3}]".format(w, h, f, codec)
                if sm not in modes:
                    modes.append(sm)

    def key(sm):
        mm = _MODE_PARSE.match(sm)
        w, h = (int(x) for x in mm.group(1).split("x"))
        return (-(w * h), -float(mm.group(2)))
    return sorted(modes, key=key)


def mode_key(mode):
    """'640x480 @ 30 fps [yuyv422]' -> '640x480|yuyv422' (tamaño + formato, sin fps)."""
    mm = _MODE_PARSE.match(mode or "")
    return "{0}|{1}".format(mm.group(1), mm.group(3)) if mm else ""


def list_camera_modes(spec):
    if _is_net_spec(spec):
        return []                          # una URL no anuncia modos DirectShow; se usa tal cual
    return parse_camera_modes(_run_ffmpeg(["-list_options", "true", "-f", "dshow",
                                           "-i", "video=" + spec]))


def pick_auto_mode(modes, fps=30, max_pixels=1280 * 720, ok=None):
    """Mejor modo cuando la cámara está en «Auto». Sin opciones, DirectShow abre el que el driver
    tiene por defecto (una de tus cámaras abre en 320×240 a 4 fps). Se prefiere: llegar a los fps
    pedidos, la mayor resolución hasta 720p y, a igual tamaño, MJPEG (gasta mucho menos USB).
    ok = {modo: fps medidos} de «Probar modos»: con ellos solo se consideran modos que funcionan
    y se cuenta lo que la cámara entrega de verdad."""
    cand = []
    for m in modes or []:
        mm = _MODE_PARSE.match(m)
        if not mm:
            continue
        w, h = (int(x) for x in mm.group(1).split("x"))
        cand.append((w * h, float(mm.group(2)), mm.group(3), m))
    if not cand:
        return None
    if ok:
        works = {mode_key(m) for m, f in ok.items() if f > 0}
        c2 = []
        for px, f, codec, m in cand:
            if m in ok:
                c2.append((px, float(ok[m]), codec, m))
            elif mode_key(m) in works:
                # fps sin probar de un tamaño que sí funciona: si el modo probado inmediatamente por
                # encima ya iba justo de luz (entregaba menos de lo que pedía), este tampoco llega
                eff = f
                higher = [(c[1], ok[c[3]]) for c in cand
                          if mode_key(c[3]) == mode_key(m) and c[3] in ok and c[1] > f]
                if higher:
                    hf, hx = min(higher)
                    if hx < 0.9 * hf:
                        eff = min(f, hx)
                c2.append((px, eff, codec, m))
        if c2:
            target = 0.8 * min(float(fps), 30.0)
            fast = ([c for c in c2 if c[1] >= target] or [c for c in c2 if c[1] >= 12]
                    or [max(c2, key=lambda c: (c[1], c[0]))])
            small = [c for c in fast if c[0] <= max_pixels] or [min(fast, key=lambda c: c[0])]
            return max(small, key=lambda c: (c[0], c[2] in ("mjpeg", "h264"), c[1]))[3]
    want = min(float(fps), 30.0) - 1.0
    fast = [c for c in cand if c[1] >= want] or [max(cand, key=lambda c: (c[1], c[0]))]
    small = [c for c in fast if c[0] <= max_pixels] or [min(fast, key=lambda c: c[0])]
    best = max(small, key=lambda c: (c[0], c[2] in ("mjpeg", "h264"), c[1]))
    return best[3]


def probe_modes(ffmpeg, spec, modes, secs=3.0, input_fn=None, progress=None, runner=None,
                max_pixels=1280 * 720):
    """Abre de verdad los modos de una cámara y mide los fps que entrega. Devuelve
    [{'mode', 'fps', 'err'}]. Etapa 1: el modo de más fps de cada tamaño/formato. Etapa 2: si el
    mejor tamaño (hasta 720p) no llega a sus fps, prueba sus fps más bajos."""
    parsed = []
    for m in modes or []:
        mm = _MODE_PARSE.match(m)
        if mm:
            w, h = (int(x) for x in mm.group(1).split("x"))
            parsed.append((w * h, float(mm.group(2)), mm.group(3), m))
    base = {}
    for t in parsed:
        k = mode_key(t[3])
        if k not in base or t[1] > base[k][1]:
            base[k] = t
    order = sorted(base.values(), key=lambda t: (-t[0], -t[1]))[:8]
    results = []
    total = len(order)

    def run_one(m):
        cmd = _probe_cmd(ffmpeg, "video", spec, m, secs, input_fn)
        log.info("probar modo %s: %s", m, subprocess.list2cmdline(cmd))
        if runner:
            err = runner(cmd)
        else:
            try:
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                                   timeout=secs + 25)
                err = r.stderr.decode("utf-8", "ignore")
            except Exception as e:
                err = "tiempo agotado ({0!r})".format(e)
        pr = _parse_probe("video", err, secs)
        ok = pr.get("frames", 0) > 0
        res = {"mode": m, "fps": pr["fps"] if ok else 0.0, "err": "" if ok else pr["err"]}
        results.append(res)
        if progress:
            progress(len(results), total, m)
        return res
    for t in order:
        run_one(t[3])
    good = [r for r in results if r["fps"] > 0 and mode_size(r["mode"])
            and mode_size(r["mode"])[0] * mode_size(r["mode"])[1] <= max_pixels]
    if good:
        best = max(good, key=lambda r: (mode_size(r["mode"])[0] * mode_size(r["mode"])[1], r["fps"]))
        nominal = float(_MODE_PARSE.match(best["mode"]).group(2))
        if best["fps"] < 0.85 * nominal:
            k = mode_key(best["mode"])
            lower = sorted((t for t in parsed if mode_key(t[3]) == k and t[3] != best["mode"]),
                           key=lambda t: -t[1])[:3]
            for t in lower:
                r = run_one(t[3])
                if r["fps"] >= 0.85 * t[1]:
                    break
    return results


def detect_bars(ffmpeg, spec, mode, secs=3, input_fn=None):
    """Mide las barras negras que la propia cámara mete en la imagen (cropdetect, el área más
    grande vista durante unos segundos). Devuelve (x, y, ancho, alto, ancho_img, alto_img) o None."""
    input_fn = input_fn or _dshow_input
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y"] + input_fn("video", spec, mode)
    cmd += ["-t", str(secs), "-an", "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"]
    log.info("barras: %s", subprocess.list2cmdline(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=secs + 40)
    txt = (r.stderr or "").replace("\r", "\n")
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", txt)
    m = re.search(r"Stream #0:0.*?Video:.*?, (\d{2,5})x(\d{2,5})", txt)
    if not crops or not m:
        log.info("barras: sin resultado:\n%s", txt[-400:])
        return None
    w, h, x, y = (int(v) for v in crops[-1])
    return x, y, w, h, int(m.group(1)), int(m.group(2))


def bars_to_frame(rect, h=False, v=False, rot=0):
    """Rectángulo de contenido (en la imagen original) -> (ratio 'ancho:alto', zoom %, cx %, cy %)
    para el modelo de encuadre, que actúa DESPUÉS de voltear y girar. None si no hay barras."""
    x, y, w, hh, iw, ih = rect
    if h:
        x = iw - x - w
    if v:
        y = ih - y - hh
    if rot == 90:
        x, y, w, hh, iw, ih = ih - (y + hh), x, hh, w, ih, iw
    elif rot == 180:
        x, y = iw - x - w, ih - y - hh
    elif rot == 270:
        x, y, w, hh, iw, ih = y, iw - (x + w), hh, w, ih, iw
    if w >= iw * 0.98 and hh >= ih * 0.98:
        return None
    S, T = iw / float(ih), w / float(hh)
    fw0 = min(1.0, T / S)
    zoom = max(100.0, min(600.0, 100.0 * fw0 / (w / float(iw))))
    return ("{0}:{1}".format(w, hh), zoom, 100.0 * (x + w / 2.0) / iw, 100.0 * (y + hh / 2.0) / ih)


def capture_args(mode):
    m = _MODE_PARSE.match(mode or "")
    if not m:
        return []
    size, fps, codec = m.groups()
    a = ["-video_size", size, "-framerate", fps]
    a += ["-vcodec", codec] if codec in ("mjpeg", "h264") else ["-pixel_format", codec]
    return a


# =====================================================================
#  Geometría
# =====================================================================
def _even(n):
    n = int(round(n))
    return max(2, n - (n % 2))


def grid_for(n, cols=0, rows=0):
    n = max(1, n)
    if cols <= 0 or rows <= 0:
        c = int(math.ceil(math.sqrt(n)))
        return c, int(math.ceil(n / float(c)))
    return cols, max(rows, int(math.ceil(n / float(cols))))


def cell_size(aw, ah, width):
    w = _even(width)
    return w, _even(w * ah / float(aw))


def prev_size(W, H, max_w=880.0, max_h=560.0):
    s = min(1.0, max_w / W, max_h / H)
    return _even(W * s), _even(H * s)


def fit_size(w, h, vw, vh):
    """Tamaño en pantalla de un fotograma w×h dentro de una vista vw×vh. Nunca se amplía
    (ampliar en Python y pasarle megas a Tk en cada fotograma es lo que más cuesta)."""
    s = min(1.0, max(1, vw) / float(w), max(1, vh) / float(h))
    return max(2, int(w * s)), max(2, int(h * s))


def _near_even(v):
    return int(round(v / 2.0)) * 2


def parse_ratio(s):
    """'4:3' -> 1.333; 'auto' / texto no válido -> None."""
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*[:x/]\s*(\d+(?:[.,]\d+)?)", s or "")
    if not m:
        return None
    a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    return a / b if a > 0 and b > 0 else None


def mode_size(mode):
    """'1280x720 @ 30 fps [mjpeg]' -> (1280, 720); sin modo -> None."""
    m = _MODE_PARSE.match(mode or "")
    if not m:
        return None
    w, h = (int(x) for x in m.group(1).split("x"))
    return (w, h)


def compositions(n, max_rows=4):
    """Todas las formas de repartir n cámaras en filas: (2, 1) = 2 arriba y 1 abajo."""
    out = []

    def rec(left, cur):
        if left == 0:
            out.append(tuple(cur))
            return
        if len(cur) >= max_rows:
            return
        for k in range(1, left + 1):
            rec(left - k, cur + [k])
    rec(max(1, n), [])
    out.sort(key=lambda c: (len(c), tuple(-x for x in c)))
    return out


def structure_label(rows):
    return " + ".join(str(r) for r in rows)


def parse_structure(s, n):
    """'2 + 1' -> (2, 1) si suma n; 'Auto' o inválido -> None."""
    try:
        rows = tuple(int(x) for x in re.split(r"[+,\s]+", (s or "").strip()) if x)
    except ValueError:
        return None
    return rows if rows and all(r > 0 for r in rows) and sum(rows) == n else None


def layout_tiles(aspects, rows, width, transpose=False, canvas=None):
    """Reparte las celdas SIN perder nada de ninguna imagen.

    aspects: ancho/alto de cada celda, en orden. rows: cámaras por fila (o por columna si
    transpose). Todas las filas ocupan el mismo ancho, así que la altura de cada una se
    adapta: una fila con dos 4:3 y otra con un 16:9 dan filas de distinta altura.
    canvas None = el lienzo es el contenido; (aw, ah) = lienzo fijo y el contenido se
    centra con barras negras. Devuelve {"W", "H", "tiles": [(x, y, w, h)]}."""
    asp = [max(0.05, min(20.0, float(a))) for a in aspects]
    if transpose:
        asp = [1.0 / a for a in asp]
    rects, y, k = [], 0.0, 0
    for cnt in rows:
        grp = asp[k:k + cnt]
        k += cnt
        hr = 1.0 / sum(grp)
        x = 0.0
        for a in grp:
            rects.append((x, y, a * hr, hr))
            x += a * hr
        y += hr
    uw, uh = 1.0, y
    if transpose:
        rects = [(ry, rx, rh, rw) for (rx, ry, rw, rh) in rects]
        uw, uh = uh, uw
    W = _even(width)
    if canvas is None:
        s = W / uw
        H = max(2, _near_even(uh * s))
        ox = oy = 0.0
    else:
        H = max(2, _near_even(W * canvas[1] / float(canvas[0])))
        s = min(W / uw, H / uh)
        ox, oy = (W - uw * s) / 2.0, (H - uh * s) / 2.0
    tiles = []
    for (rx, ry, rw, rh) in rects:
        x0, y0 = _near_even(ox + rx * s), _near_even(oy + ry * s)
        x1 = min(W, _near_even(ox + (rx + rw) * s))
        y1 = min(H, _near_even(oy + (ry + rh) * s))
        tiles.append((x0, y0, max(2, x1 - x0), max(2, y1 - y0)))
    return {"W": W, "H": H, "tiles": tiles}


def best_structure(aspects, width, transpose=False, canvas=None):
    """Elige el reparto que mejor encaja: con lienzo libre, el más cercano a 16:9; con
    lienzo fijo, el que menos barras negras deja. A igualdad, el de menos filas."""
    best, best_key = None, None
    for comp in compositions(len(aspects)):
        g = layout_tiles(aspects, comp, width, transpose, canvas)
        if canvas is None:
            score = abs(math.log((g["W"] / float(g["H"])) / (16 / 9.0)))
        else:
            used = sum(w * h for (_x, _y, w, h) in g["tiles"])
            score = 1.0 - used / float(g["W"] * g["H"])
        key = (round(score, 2), len(comp))
        if best_key is None or key < best_key:
            best, best_key = comp, key
    return best


def _layout_score(g, canvas):
    if canvas is None:
        return abs(math.log((g["W"] / float(g["H"])) / (16 / 9.0)))
    used = sum(w * h for (_x, _y, w, h) in g["tiles"])
    return 1.0 - used / float(g["W"] * g["H"])


def layout_options(aspects, width, canvas=None, limit=24):
    """Todas las disposiciones distintas: [(comp, transpose, geometría)], filas y columnas.
    Con muchas cámaras se quedan las `limit` que mejor encajan."""
    out, seen = [], set()
    for comp in compositions(len(aspects)):
        for trans in (False, True):
            g = layout_tiles(aspects, comp, width, trans, canvas)
            key = tuple(g["tiles"])
            if key in seen:
                continue
            seen.add(key)
            out.append((comp, trans, g))
    if len(out) > limit:
        out = sorted(out, key=lambda o: _layout_score(o[2], canvas))[:limit]
    return out


def best_layout(aspects, width, canvas=None):
    """(comp, transpose) que mejor encaja mirando filas Y columnas; a igualdad, filas y menos líneas."""
    best, best_key = None, None
    for comp in compositions(len(aspects)):
        for trans in (False, True):
            g = layout_tiles(aspects, comp, width, trans, canvas)
            key = (round(_layout_score(g, canvas), 2), trans, len(comp))
            if best_key is None or key < best_key:
                best, best_key = (comp, trans), key
    return best


def window_rect(S, T, zoom, cx, cy):
    """Zona de la imagen que se ve en la celda (coordenadas 0..1).
    S: aspecto de la imagen (ya girada); T: aspecto de la celda; zoom en % (>=100);
    cx, cy: centro en % (50 = centrado). Devuelve (x0, y0, ancho, alto)."""
    z = max(1.0, zoom / 100.0)
    fw0 = min(1.0, T / S)
    fh0 = fw0 * S / T
    fw, fh = fw0 / z, fh0 / z
    x0 = min(max(cx / 100.0 - fw / 2.0, 0.0), 1.0 - fw)
    y0 = min(max(cy / 100.0 - fh / 2.0, 0.0), 1.0 - fh)
    return x0, y0, fw, fh


def color_params(c):
    """Ajustes -100..100 de la interfaz -> parámetros de ffmpeg (eq / colortemperature)."""
    def g(k):
        try:
            return max(-100.0, min(100.0, float((c or {}).get(k, 0))))
        except (TypeError, ValueError):
            return 0.0
    return {"brightness": g("bri") / 200.0,
            "contrast": 1.0 + g("con") / 100.0,
            "saturation": 1.0 + g("sat") / 100.0,
            "gamma": 2.0 ** (g("gam") / 50.0),
            "temperature": int(round(6500.0 * 0.5 ** (g("temp") / 100.0))),
            "temp_on": abs(g("temp")) > 0.5}


def plan_geometry(plan):
    """(W, H, tiles) de un plan: usa plan['geom'] o, si no, una cuadrícula uniforme."""
    g = plan.get("geom")
    if g:
        return g["W"], g["H"], [tuple(t) for t in g["tiles"]]
    n = len(plan["cams"])
    cols, rows, cw, ch = plan["cols"], plan["rows"], plan["cw"], plan["ch"]
    tiles = [((i % cols) * cw, (i // cols) * ch, cw, ch) for i in range(n)]
    return cols * cw, rows * ch, tiles


# =====================================================================
#  Construcción del comando ffmpeg (puro: se puede probar sin cámaras)
# =====================================================================
_NET_SCHEMES = ("http://", "https://", "rtsp://", "rtsps://")

# Tope de conexión/lectura (microsegundos) para una cámara de red, con -rw_timeout (genérico,
# vale para http/https) y, en rtsp, también con el -timeout propio del demuxer. Sin esto, una
# fuente que acepta la conexión pero deja de contestar (wifi, cámara apagada, un móvil que se
# bloquea...) puede colgar a ffmpeg bastante más de lo que tarda en desbordarse el búfer en
# tiempo real de OTRA cámara del mismo proceso (-rtbufsize, ver _dshow_input): comprobado de
# verdad, sin -rw_timeout un servidor que acepta la conexión y no manda nada deja a ffmpeg
# colgado más de 8s sin más; con -rw_timeout falla sola, limpia, al momento. 5s deja margen para
# un bache de wifi normal sin arriesgarse a tanto (una EOS Webcam Utility real, 1280x720 sin
# comprimir a 30fps, llena sus 512 MB en unos 9s si nadie la vacía).
NET_IO_TIMEOUT_US = 5_000_000
# Igual, pero para la prueba de red previa (ver CamApp._preflight_net_cams): ahí interesa fallar
# pronto, no esperar lo mismo que ya se tolera una vez integrada de verdad en el mosaico.
NET_PREFLIGHT_TIMEOUT_US = 2_000_000
NET_PREFLIGHT_SECS = 1.5


def _is_net_spec(spec):
    """True si «spec» es una URL (cámara de red: el móvil con una app como IP Webcam) en vez de
    un nombre de dispositivo DirectShow."""
    return (spec or "").strip().lower().startswith(_NET_SCHEMES)


def _net_video_input(spec, opt=None, timeout_us=None):
    """Entrada de vídeo por red (http/https MJPEG o rtsp): spec es la URL completa. opt se
    ignora (no hay «modo de captura» que elegir en una URL). timeout_us: ver NET_IO_TIMEOUT_US
    (None = el de siempre; CamApp._preflight_net_cams pasa el suyo, más corto). En pruebas: sin
    cámara real que probar desde aquí, validado contra servidores locales de prueba (incluida una
    fuente que acepta la conexión y no contesta nada, para el propio -rw_timeout)."""
    spec = spec.strip()
    t = str(NET_IO_TIMEOUT_US if timeout_us is None else timeout_us)
    pre = ["-rw_timeout", t]
    if spec.lower().startswith(("rtsp://", "rtsps://")):
        pre += ["-rtsp_transport", "tcp", "-timeout", t]   # evita paquetes perdidos por UDP en wifi
    return pre + ["-i", spec]


def _dt_escape(s):
    """Escapa texto para el valor text= de drawtext dentro de una entrada «-f lavfi -i ...»
    (ver _offline_lavfi). Probado de verdad con acentos, emoji, comillas, dos puntos, % y
    corchetes -- todo lo que un nombre de cámara puesto a mano podría llevar."""
    s = (s or "").replace("\\", "\\\\")
    for ch in (":", "'", "%", "[", "]", ",", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


def _offline_lavfi(w, h, fps, label):
    """Entrada sintética (fondo + «Sin señal: <nombre>») para poner en el sitio de una cámara
    que ffmpeg no pudo abrir, en vez de dejar que todo el proceso (vista previa + grabación,
    que comparten uno solo) se caiga por una sola cámara. Con fontfile= de verdad (no depende
    de que el ffmpeg instalado traiga fontconfig, solo libfreetype -- lo trae hasta el build
    «essentials» de gyan.dev que usa el instalador)."""
    w, h = max(16, int(w)), max(16, int(h))
    fs = max(11, min(26, h // 11))
    text = _dt_escape("Sin señal: {0}".format(label or "cámara"))
    font = "fontfile='{0}':".format(PLACEHOLDER_FONT.replace("\\", "/").replace(":", "\\:")) \
        if PLACEHOLDER_FONT else ""
    return ("color=c=0x303030:s={0}x{1}:r={2},"
           "drawtext={3}text='{4}':fontcolor=0xdddddd:fontsize={5}:"
           "x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=0x000000a0:boxborderw=10".format(
               w, h, fps, font, text, fs))


def _dshow_input(kind, spec, opt=None, timeout_us=None):
    """opt: modo de captura (vídeo) o tamaño del búfer en ms (audio; 0 = el del dispositivo).
    timeout_us: solo para una cámara de red (ver _net_video_input) -- se ignora en una
    DirectShow local, que no tiene «conexión» que pueda quedarse colgada igual."""
    if kind == "video":
        if _is_net_spec(spec):
            return _net_video_input(spec, opt, timeout_us)
        return (["-f", "dshow", "-rtbufsize", "512M"] + capture_args(opt) +
                ["-i", "video=" + spec])
    buf = 50 if opt is None else int(opt)
    return (["-f", "dshow", "-rtbufsize", "64M"] +
            (["-audio_buffer_size", str(buf)] if buf > 0 else []) + ["-i", "audio=" + spec])


def _rebase(clock, t0, offset_s, audio=False, net=False):
    """Expresión setpts/asetpts. clock: línea de tiempo común (T0); device: cada
    flujo empieza en 0. offset_s>0 retrasa el flujo.
    net=True (solo vídeo, ver video_chain): "-use_wallclock_as_timestamps" -- con el que esto
    normalmente convierte PTS en la hora real de llegada -- no lo hacen todos los demuxers.
    Comprobado de verdad: el de «mpjpeg» (una cámara de red MJPEG, sea un móvil o la propia
    salida en red de otra instancia de cam-ui) ignora esa opción y numera los fotogramas 0,1,2...
    como si fuera un archivo, así que PTS-T0/TB da un número enorme y negativo y el filtro "fps"
    de más abajo los descarta TODOS (0 fotogramas de salida, probado con ffmpeg real). Para estas
    fuentes se usa en su lugar RTCTIME -- la hora real del reloj del sistema en el momento en que
    el propio filtro procesa cada fotograma, no la que diga el demuxer -- que no depende de que el
    demuxer de turno sepa hacerlo bien."""
    if clock and audio:
        # Audio: línea de tiempo CONTINUA contando muestras, anclada solo a la hora de la primera.
        # Con la hora de llegada de cada paquete (reloj de pared) cualquier parón de ffmpeg
        # desordena las marcas y aresample rellena con silencio / descarta audio.
        return "STARTPTS-({0:.6f}-{1:.6f})/TB+NB_CONSUMED_SAMPLES/SR/TB".format(t0, offset_s)
    if clock and net:
        return "(RTCTIME/1000000-({0:.6f}-{1:.6f}))/TB".format(t0, offset_s)
    if clock:
        return "PTS-({0:.6f}-{1:.6f})/TB".format(t0, offset_s)
    return "PTS-STARTPTS+{0:.6f}/TB".format(offset_s)


def _src_matches_tile(cam, cw, ch):
    """True si se conoce el tamaño de la cámara (tras girar) y su forma coincide con la celda."""
    src = cam.get("src")
    if not src or not src[0] or not src[1]:
        return False
    a = src[0] / float(src[1])
    if int(cam.get("rot", 0)) % 180 == 90:
        a = 1.0 / a
    return abs(a / (cw / float(ch)) - 1.0) < 0.03


def _same_shape(a, b):
    """True si dos tamaños (ancho, alto) tienen prácticamente el mismo aspecto (<3%, la misma
    tolerancia que _src_matches_tile) -- aunque los píxeles exactos no coincidan. Una cámara de
    red (el móvil, otro PC encadenado, una app tipo IP Webcam...) puede anunciar un tamaño
    ligeramente distinto de una conexión a otra (redondeo de bloque del propio códec MJPEG,
    getUserMedia con «ideal» en vez de un tamaño exacto, etc.) sin que de verdad haya cambiado
    nada que afecte al mosaico -- ver _check_sources, que usa esto para no reiniciar por esto."""
    if not a or not b or not a[0] or not a[1] or not b[0] or not b[1]:
        return False
    return abs((a[0] / float(a[1])) / (b[0] / float(b[1])) - 1.0) < 0.03


def video_chain(i, cam, cw, ch, fps, clock, t0):
    # el marcador «sin señal» (ver _offline_lavfi, build_command) es, igual que una cámara de
    # red, una fuente que no se puede fiar de "-use_wallclock_as_timestamps" (es "lavfi", que
    # numera sus propios fotogramas igual que hacía "mpjpeg" -- el mismo fallo ya arreglado
    # antes para las cámaras de red, ver _rebase): necesita el mismo rebase por RTCTIME.
    net = _is_net_spec(cam.get("spec")) or bool(cam.get("_offline"))
    f = ["setpts=" + _rebase(clock, t0, cam.get("delay", 0) / 1000.0, net=net),
         "fps=fps={0}:start_time=0".format(fps)]
    if cam.get("h"):
        f.append("hflip")
    if cam.get("v"):
        f.append("vflip")
    rot = int(cam.get("rot", 0)) % 360
    if rot == 90:
        f.append("transpose=1")
    elif rot == 180:
        f.append("hflip,vflip")
    elif rot == 270:
        f.append("transpose=2")
    zoom = max(100.0, float(cam.get("zoom", 100) or 100))
    framed = cam.get("tile", "auto") != "auto" or zoom > 100.5 or cam.get("fit") == "fill"
    if framed:
        # Ventana que cubre la celda (aspecto cw:ch), con zoom y centro elegidos. Se calcula
        # en ffmpeg con iw/ih, así que vale aunque no se conozca el tamaño real de la cámara.
        T = cw / float(ch)
        cx = max(0.0, min(100.0, float(cam.get("cx", 50)))) / 100.0
        cy = max(0.0, min(100.0, float(cam.get("cy", 50)))) / 100.0
        w0 = "min(iw,{0:.6f}*ih)/{1:.5f}".format(T, zoom / 100.0)
        f.append("crop=w='floor({w}+0.001)':h='floor({w}/{T:.6f}+0.001)':"
                 "x='max(0,min(iw-out_w,{cx:.5f}*iw-out_w/2))':"
                 "y='max(0,min(ih-out_h,{cy:.5f}*ih-out_h/2))'".format(
                     w=w0, T=T, cx=cx, cy=cy))
        f.append("scale={0}:{1}".format(cw, ch))
    elif cam.get("fit") == "stretch" or _src_matches_tile(cam, cw, ch):
        f.append("scale={0}:{1}".format(cw, ch))      # misma forma (±3 %): sin franjas por redondeo
    else:       # forma desconocida o distinta: se ve todo, con barras
        f.append("scale={0}:{1}:force_original_aspect_ratio=decrease,"
                 "pad={0}:{1}:(ow-iw)/2:(oh-ih)/2:color=black".format(cw, ch))
    cp = color_params(cam.get("color"))
    f.append("format=yuv420p")
    f.append("eq@c{0}=brightness={1:.4f}:contrast={2:.4f}:saturation={3:.4f}:gamma={4:.4f}".format(
        i, cp["brightness"], cp["contrast"], cp["saturation"], cp["gamma"]))
    if cp["temp_on"]:
        f += ["format=gbrp", "colortemperature@t{0}=temperature={1}".format(i, cp["temperature"]),
              "format=yuv420p"]
    f.append("setsar=1")
    return "[{0}:v]{1}[v{0}]".format(i, ",".join(f))


def build_command(plan, outfile=None, warmup=0.0, t0=None, preview=True,
                  input_fn=None, ffmpeg=None):
    """plan = {cams:[{spec,mode,h,v,rot,delay,tile,zoom,cx,cy,color}],
               geom:{W,H,tiles:[(x,y,w,h)]}  (o bien cols, rows, cw, ch = cuadrícula uniforme),
               fps, sync, crf, preset, duration,
               audio:{L:{spec,chan,gain,delay}|None, R:..., offset}}
    Devuelve (cmd, meta)."""
    ffmpeg = ffmpeg or FFMPEG
    input_fn = input_fn or _dshow_input
    t0 = time.time() if t0 is None else t0
    cams = plan["cams"]
    n = len(cams)
    clock = plan.get("sync", "clock") == "clock"
    fps = int(plan["fps"])
    W, H, tiles = plan_geometry(plan)
    pw, ph = prev_size(W, H, 640.0, 440.0) if outfile else prev_size(W, H)

    cmd = [ffmpeg, "-hide_banner", "-y"]
    cmd += ["-loglevel", "info", "-nostats"] if preview else ["-loglevel", "warning"]
    if clock:
        cmd.append("-copyts")
    pre = ["-thread_queue_size", "1024"]
    if clock:
        pre += ["-use_wallclock_as_timestamps", "1"]

    labels = plan.get("labels") or []
    fc = []
    # ---- vídeo ----
    for i, c in enumerate(cams):
        if c.get("_offline"):
            # la cámara no se pudo abrir en un intento anterior (ver _engine_start /
            # failing_cam_index): se sustituye por un marcador «sin señal» para que el resto
            # del mosaico siga funcionando en vez de que todo el proceso se caiga por ella.
            lab = labels[i] if i < len(labels) else None
            cmd += ["-f", "lavfi", "-i", _offline_lavfi(tiles[i][2], tiles[i][3], fps, lab)]
        else:
            cmd += pre + input_fn("video", c["spec"], c.get("mode"))
        fc.append(video_chain(i, c, tiles[i][2], tiles[i][3], fps, clock, t0))
    if n == 1:
        fc.append("[v0]null[mix0]")
        fc.append("[mix0]pad={0}:{1}:{2}:{3}:color=black[mix]".format(W, H, tiles[0][0], tiles[0][1]))
    else:
        layout = "|".join("{0}_{1}".format(t[0], t[1]) for t in tiles[:n])
        fc.append("{0}xstack=inputs={1}:layout={2}:fill=black[mix0]".format(
            "".join("[v{0}]".format(i) for i in range(n)), n, layout))
        fc.append("[mix0]pad={0}:{1}:0:0:color=black[mix]".format(W, H))   # márgenes / huecos

    # ---- audio ----
    ip = plan.get("ipout")
    if ip and not ip.get("port"):
        ip = None                                  # sin socket de vídeo no hay salida en red
    ipa_map = None                                  # etiqueta del filtro con el audio para la red (si hace falta)
    audio = plan.get("audio") or {}
    arows = [audio.get("L"), audio.get("R")]
    act = [r for r in arows if r and r.get("spec")]
    if len(act) == 1:
        # Un canal en silencio: se deriva del mismo dispositivo con ganancia 0 (así
        # comparte reloj; un generador de silencio libre bloquea el grafo de ffmpeg).
        arows[0 if arows[0] is not act[0] else 1] = {
            "spec": act[0]["spec"], "chan": "left", "gain": 0, "delay": 0, "mute": True}
    specs = []
    for r in arows:
        if r and r.get("spec") and r["spec"] not in specs:
            specs.append(r["spec"])
    has_audio = bool(specs)
    # ip con puerto de audio armado Y de verdad hay micrófono: solo entonces se oye en la red
    need_ip_audio = bool(ip and ip.get("aport") and has_audio)
    if has_audio:
        off = audio.get("offset", 0) / 1000.0
        uses = dict((s, sum(1 for r in arows if r and r.get("spec") == s)) for s in specs)
        for pos, s in enumerate(specs):
            cmd += pre + input_fn("audio", s, audio.get("buf", 50))
            chain = ("[{0}:a]asetpts={1},aresample=48000:first_pts=0,"
                     "aformat=sample_fmts=fltp:channel_layouts=stereo".format(
                         n + pos, _rebase(clock, t0, off, True)))
            if uses[s] == 1:
                chain += "[d{0}u0]".format(pos)
            else:
                chain += ",asplit=2[d{0}u0][d{0}u1]".format(pos)
            fc.append(chain)
        used = dict((s, 0) for s in specs)
        for ri, r in enumerate(arows):
            lab = "ar{0}".format(ri)
            if r and r.get("spec"):
                s = r["spec"]
                u = used[s]
                used[s] += 1
                g = 10 ** (float(r.get("gain", 0)) / 20.0)
                chan = r.get("chan", "mix")
                if r.get("mute"):
                    ex = "0*c0"
                elif chan == "left":
                    ex = "{0:.4f}*c0".format(g)
                elif chan == "right":
                    ex = "{0:.4f}*c1".format(g)
                else:
                    ex = "{0:.4f}*c0+{0:.4f}*c1".format(g / 2.0)
                chain = "[d{0}u{1}]pan=mono|c0={2}".format(specs.index(s), u, ex)
                dl = int(r.get("delay", 0))
                if dl > 0:
                    chain += ",adelay={0}".format(dl)
                fc.append(chain + "[{0}]".format(lab))
        fc.append("[ar0][ar1]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[aoutJ]")

    use_meter = preview and plan.get("meter", True)
    null_meter = plan.get("meter_mode", "null") == "null"
    meter = ("asetnsamples=n=4800:p=0,astats=metadata=1:reset=1,"
             "ametadata=mode=print:key=lavfi.astats.1.Peak_level,"
             "ametadata=mode=print:key=lavfi.astats.2.Peak_level" +
             ("[ameter]" if null_meter else ",anullsink"))
    if has_audio:
        # el audio final ([aoutJ]) puede tener varios consumidores a la vez (grabación, vúmetro,
        # salida en red): igual que con el vídeo, solo se reparte con asplit entre los que hagan
        # falta -- así nunca queda una salida de asplit sin usar (ffmpeg no lo permite)
        a_outs = [k for k, on in (("rec", bool(outfile)), ("meter", use_meter),
                                  ("ip", need_ip_audio)) if on]
        a_names = {"rec": "aout_rec", "meter": "aout_meter", "ip": "aout_ip"}
        if len(a_outs) > 1:
            fc.append("[aoutJ]asplit={0}{1}".format(
                len(a_outs), "".join("[{0}]".format(a_names[k]) for k in a_outs)))
            a_src = dict((k, "[{0}]".format(a_names[k])) for k in a_outs)
        elif len(a_outs) == 1:
            a_src = {a_outs[0]: "[aoutJ]"}
        else:
            a_src = {}                   # nadie quiere el audio (plan sin grabar/medir/red): no se usa
        if "rec" in a_src:
            fc.append("{0}atrim=start={1:.3f},asetpts=PTS-STARTPTS[arec]".format(a_src["rec"], warmup))
        if "meter" in a_src:
            fc.append(a_src["meter"] + meter)
        if "ip" in a_src:
            ipa_map = a_src["ip"]        # "[aout_ip]" si se repartió, o "[aoutJ]" si era el único

    # ---- salidas de vídeo ----
    vc = plan.get("vcam")
    if vc and not vc.get("port"):
        vc = None                                  # sin socket de destino no hay cámara virtual
    outs = [k for k, on in (("rec", bool(outfile)), ("pv", bool(preview)), ("vc", bool(vc)),
                            ("ip", bool(ip))) if on]
    names = {"rec": "mrec0", "pv": "mp0", "vc": "mvc0", "ip": "mip0"}
    if len(outs) > 1:
        fc.append("[mix]split={0}{1}".format(len(outs), "".join("[{0}]".format(names[k]) for k in outs)))
        src = dict((k, "[{0}]".format(names[k])) for k in outs)
    else:
        src = dict((k, "[mix]") for k in outs)
    if outfile:
        fc.append("{0}trim=start={1:.3f},setpts=PTS-STARTPTS[mrec]".format(src["rec"], warmup))
    if preview:
        fc.append("{0}fps={1},scale={2}:{3}:flags=fast_bilinear[pv]".format(
            src["pv"], min(fps, 12 if outfile else 20), pw, ph))
    if vc:
        vw, vh = int(vc["w"]), int(vc["h"])
        fc.append("{0}fps={1},scale={2}:{3}:force_original_aspect_ratio=decrease:flags=bilinear,"
                  "pad={2}:{3}:(ow-iw)/2:(oh-ih)/2:color=black,format={4}[vc]".format(
                      src["vc"], max(1, min(fps, int(vc.get("fps", 30)))), vw, vh,
                      vc.get("pix", "rgb24")))
    if ip:
        # w×h ya trae la forma del propio mosaico (CamApp._plan -> ip_fit_size): se escala tal
        # cual, sin rellenar con negro hasta otro formato (antes se encajaba siempre en el tamaño
        # fijo elegido, con bandas negras a los lados o arriba/abajo según la forma del mosaico)
        iw, ih = int(ip["w"]), int(ip["h"])
        fc.append("{0}fps={1},scale={2}:{3}:flags=bilinear,setsar=1,format={4}[ip]".format(
                      src["ip"], max(1, min(fps, int(ip.get("fps", IP_FPS)))), iw, ih,
                      ip.get("pix", "rgb24")))

    cmd += ["-filter_complex", ";".join(fc)]
    if outfile:
        cmd += ["-map", "[mrec]"]
        if has_audio:
            cmd += ["-map", "[arec]"]
        cmd += ["-c:v", "libx264", "-preset", plan.get("preset", "veryfast"),
                "-crf", str(plan.get("crf", 23)), "-pix_fmt", "yuv420p",
                "-g", str(max(1, fps * 2))]
        if has_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        if outfile.lower().endswith(".mp4"):
            cmd += ["-movflags", "+faststart"]
        if plan.get("duration"):
            cmd += ["-t", str(plan["duration"])]
        cmd.append(outfile)
    if preview:
        cmd += ["-map", "[pv]", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    if vc:
        # la cámara virtual recibe RGB por un socket local (el único «pipe» libre es el de la vista previa)
        cmd += ["-map", "[vc]", "-f", "rawvideo", "-pix_fmt", vc.get("pix", "rgb24"),
                "tcp://127.0.0.1:{0}".format(int(vc["port"]))]
    if ip:
        # la salida en red recibe RGB por un socket local; nuestro propio servidor HTTP lo
        # convierte en MJPEG (así un visor que se cuelga o se desconecta no afecta a ffmpeg)
        cmd += ["-map", "[ip]", "-f", "rawvideo", "-pix_fmt", ip.get("pix", "rgb24"),
                "tcp://127.0.0.1:{0}".format(int(ip["port"]))]
    if ipa_map:
        # el audio de la salida en red sí lo codifica ffmpeg (a MP3: lo entiende cualquier
        # navegador directamente, sin plugins) y va por su propio socket local, hasta nuestro
        # servidor HTTP, que simplemente reenvía los bytes tal cual a quien esté escuchando
        cmd += ["-map", ipa_map, "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3",
                "tcp://127.0.0.1:{0}".format(int(ip["aport"]))]
    if has_audio and use_meter and null_meter:
        cmd += ["-map", "[ameter]", "-f", "null", "-"]
    meta = {"pw": pw, "ph": ph, "W": W, "H": H, "has_audio": has_audio, "tiles": tiles[:n],
            "temp_on": [color_params(c.get("color"))["temp_on"] for c in cams]}
    return cmd, meta


def remux_recording(src, dst, ffmpeg=None):
    """Copia sin recodificar (-c copy) src -> dst. Se graba en MKV (resiste cortes y fallos de
    ffmpeg) y al terminar se pasa a MP4 con el índice al principio. Devuelve (ok, mensaje)."""
    cmd = [ffmpeg or FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-map", "0", "-c", "copy"]
    if dst.lower().endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(dst)
    log.info("remux: %s", subprocess.list2cmdline(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=CREATE_NO_WINDOW, timeout=900,
                           stdin=subprocess.DEVNULL)
    except Exception as e:
        log.error("remux fallo: %r", e)
        return False, str(e)
    ok = r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0
    log.info("remux: rc=%s ok=%s %s", r.returncode, ok, (r.stderr or "")[-300:])
    if ok:
        try:
            os.remove(src)
        except Exception:
            pass
    return ok, (r.stderr or "")[-300:]


# =====================================================================
#  Cámara virtual (para videollamadas): ffmpeg -> socket local -> pyvirtualcam -> cámara de OBS
# =====================================================================
VCAM_SIZES = (("1280×720  (recomendado)", (1280, 720)), ("1920×1080", (1920, 1080)),
              ("854×480", (854, 480)))
# formatos que se prueban, en orden: (nombre en pyvirtualcam, pix_fmt de ffmpeg, bytes por píxel).
# RGB es el de por defecto de pyvirtualcam y el que admite el backend de OBS; RGBA lo rechaza
# con «Unsupported image format», así que va el último (Unity Capture sí lo admite).
VCAM_FORMATS = (("RGB", "rgb24", 3), ("BGR", "bgr24", 3), ("RGBA", "rgba", 4))
VCAM_INSTALL_HELP = ("Hace falta OBS Studio instalado (gratis, obsproject.com): CamWin32 usa su "
                     "cámara virtual, no hay que abrir OBS. Si ya lo tienes y falla, cierra la "
                     "cámara virtual de OBS si la tenías iniciada (solo puede usarla un programa) "
                     "y, si no aparece, ejecuta como administrador "
                     "«obs-studio\\data\\obs-plugins\\win-dshow\\virtualcam-install.bat».")


def vcam_size_label(size):
    for lab, s in VCAM_SIZES:
        if tuple(s) == tuple(size):
            return lab
    return VCAM_SIZES[0][0]


def vcam_size_value(label):
    for lab, s in VCAM_SIZES:
        if lab == label:
            return s
    return VCAM_SIZES[0][1]


def load_pyvirtualcam():
    """El módulo pyvirtualcam o None (no instalado / sin su parte nativa)."""
    try:
        import pyvirtualcam
        return pyvirtualcam
    except Exception as e:
        log.info("pyvirtualcam no disponible: %r", e)
        return None


class VirtualCam:
    """Recibe de ffmpeg fotogramas RGB por un socket local y los envía a la cámara virtual.
    Tres hilos como mucho: aceptar la conexión, leer (SIEMPRE vacía el socket, para que ffmpeg
    nunca se bloquee y arrastre la grabación) y enviar (solo el último fotograma; si va lento
    se descartan los anteriores). La cámara virtual se queda abierta aunque ffmpeg se reinicie."""

    def __init__(self, module=None):
        self._module = module                 # pyvirtualcam (o un sustituto en las pruebas)
        self.cam = None
        self.w = self.h = 0
        self.fps = 30
        self.pix, self.bpp = "rgb24", 3        # formato que se pide a ffmpeg (según lo que acepte el backend)
        self.device = ""
        self.sent = self.dropped = self.received = 0
        self.errors = 0
        self._gen = 0
        self._srv = None
        self._latest = None
        self._lk = threading.Lock()
        self._ev = threading.Event()
        self._closed = False
        self._sender = None

    @property
    def active(self):
        return self.cam is not None

    def open(self, w, h, fps=30):
        """Abre la cámara virtual (w×h fijo). Lanza RuntimeError con un texto claro si no se puede."""
        self.release()
        mod = self._module or load_pyvirtualcam()
        if mod is None:
            raise ImportError("pyvirtualcam")
        cam, first = None, None
        for name, pix, bpp in VCAM_FORMATS:
            fmt = getattr(mod.PixelFormat, name, None)
            if fmt is None:
                continue
            try:
                cam = mod.Camera(int(w), int(h), float(fps), fmt=fmt)
            except Exception as e:
                log.warning("cámara virtual: formato %s rechazado: %s", name, e)
                first = first or e
                continue
            self.pix, self.bpp = pix, bpp
            break
        if cam is None:
            raise RuntimeError(str(first or "sin formato de imagen compatible"))
        self.cam, self.w, self.h, self.fps = cam, int(w), int(h), int(fps)
        self.device = str(getattr(cam, "device", "") or "")
        self.sent = self.dropped = self.received = self.errors = 0
        self._closed = False
        self._latest = None
        self._ev.clear()
        self._sender = threading.Thread(target=self._send_loop, args=(cam,), daemon=True)
        self._sender.start()
        log.info("cámara virtual abierta: %s %dx%d @%d (%s)", self.device, self.w, self.h, self.fps, self.pix)

    def arm(self):
        """Prepara un socket de escucha para la próxima conexión de ffmpeg; devuelve su puerto."""
        self._drop_srv()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(0.4)
        self._srv = srv
        self._gen += 1
        threading.Thread(target=self._serve, args=(srv, self._gen), daemon=True).start()
        return srv.getsockname()[1]

    def _drop_srv(self):
        srv, self._srv = self._srv, None
        self._gen += 1                       # el hilo que esperaba conexión termina solo
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass

    def _serve(self, srv, gen):
        conn = None
        t_end = time.time() + 30
        while gen == self._gen and not self._closed and time.time() < t_end:
            try:
                conn, _a = srv.accept()
                break
            except socket.timeout:
                continue
            except OSError:
                break
        try:
            srv.close()
        except Exception:
            pass
        if conn is None:
            return
        conn.settimeout(None)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        log.info("cámara virtual: ffmpeg conectado")
        try:
            self._read(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            log.info("cámara virtual: ffmpeg desconectado (%d recibidos, %d enviados, %d descartados)",
                     self.received, self.sent, self.dropped)

    def _read(self, conn):
        w, h, bpp = self.w, self.h, self.bpp
        fb = w * h * bpp
        while not self._closed:
            buf = bytearray(fb)
            mv = memoryview(buf)
            pos = 0
            while pos < fb:
                try:
                    n = conn.recv_into(mv[pos:])
                except Exception:
                    return
                if not n:
                    return
                pos += n
            self.received += 1
            if self.cam is None:
                continue                       # cerrada: solo se vacía el socket
            arr = np.frombuffer(buf, np.uint8).reshape((h, w, bpp))
            with self._lk:
                if self._latest is not None:
                    self.dropped += 1          # el anterior no llegó a enviarse
                self._latest = arr
            self._ev.set()

    def _send_loop(self, cam):
        while not self._closed and self.cam is cam:
            if not self._ev.wait(0.5):
                continue
            self._ev.clear()
            with self._lk:
                arr, self._latest = self._latest, None
            if arr is None or arr.shape[0] != self.h or arr.shape[1] != self.w or arr.shape[2] != self.bpp:
                continue
            try:
                cam.send(arr)
                self.sent += 1
            except Exception:
                self.errors += 1
                if self.errors <= 3:
                    log.warning("cámara virtual: error al enviar:\n%s", traceback.format_exc())
                time.sleep(0.05)

    def release(self):
        """Cierra la cámara (los fotogramas que sigan llegando se descartan sin bloquear ffmpeg)."""
        cam, self.cam = self.cam, None
        self._ev.set()
        if cam is not None:
            t = self._sender
            if t is not None and t is not threading.current_thread():
                t.join(1.0)
            try:
                cam.close()
            except Exception:
                log.warning("cámara virtual: al cerrar: %r", sys.exc_info()[1])
            log.info("cámara virtual cerrada")

    def close(self):
        self._closed = True
        self._drop_srv()
        self.release()


# =====================================================================
#  Salida en red (ver el mosaico desde otro equipo/móvil de la misma wifi): ffmpeg -> socket
#  local (igual mecanismo que la cámara virtual) -> servidor HTTP MJPEG propio, no ffmpeg, para
#  que un visor que se cuelga o se desconecta nunca afecte a ffmpeg ni a la grabación.
# =====================================================================
# Tamaño MÁXIMO de la salida en red: se manda con la forma real del mosaico (ver ip_fit_size), lo
# más grande que quepa aquí -- nunca rellenando con negro hasta este formato (antes sí: un mosaico
# 4:3 llegaba a quien lo viera, o a otro cam-ui que lo usara como cámara de red, con bandas negras a
# los lados, y uno apaisado con bandas arriba y abajo).
IP_SIZES = (("hasta 640×360  (recomendado)", (640, 360)), ("hasta 960×540", (960, 540)),
           ("hasta 1280×720", (1280, 720)), ("hasta 1920×1080", (1920, 1080)))
IP_FPS = 10                 # de sobra para verlo: ahorra wifi y CPU (va aparte de los fps de grabación)
IP_PORT_DEFAULT = 8554
IP_JPEG_QUALITY = 70

# ---- HLS para el botón nativo de Chromecast («En la red»): un segundo ffmpeg, aparte del
# MJPEG de siempre (que sigue igual), solo se enciende mientras alguien lo está viendo en una
# tele -- ver IpServer.hls_start()/_hls_stop()/hls_idle_check(). Sin sonido de verdad (como las
# cámaras de red): mezclar el micrófono de verdad metía riesgo de que ffmpeg se quedara colgado
# si alguna vez no llega sonido real. Pero SÍ lleva una pista de audio muda (silencio puro,
# generado por el propio ffmpeg con «anullsrc», sin depender de ningún micrófono) -- algunos
# receptores de Chromecast (sobre todo los más antiguos, como el de 1.ª generación) se quedan en
# blanco, sin avisar de ningún error, ante un HLS que no trae NINGUNA pista de audio.
HLS_SEGMENT_SECONDS = 2
HLS_LIST_SIZE = 6
HLS_BITRATE = "2500k"
HLS_IDLE_SECONDS = 25         # sin pedir ni un trozo en todo este tiempo -> se para solo


def ip_fit_size(W, H, box):
    """Tamaño real de la salida en red para un mosaico de W×H: la MISMA forma que el mosaico
    (sin barras negras añadidas), lo más grande que quepa en box=(ancho, alto) máximos, y nunca
    mayor que el propio mosaico. Pares (los pide el H.264 del Chromecast y el receptor)."""
    W, H = max(2, int(W)), max(2, int(H))
    bw, bh = max(2, int(box[0])), max(2, int(box[1]))
    s = min(1.0, bw / float(W), bh / float(H))
    w = max(2, min(bw - bw % 2, int(round(W * s / 2.0)) * 2))
    h = max(2, min(bh - bh % 2, int(round(H * s / 2.0)) * 2))
    return w, h


def ip_size_label(size):
    for lab, s in IP_SIZES:
        if tuple(s) == tuple(size):
            return lab
    return IP_SIZES[0][0]


def ip_size_value(label):
    for lab, s in IP_SIZES:
        if lab == label:
            return s
    return IP_SIZES[0][1]


def _local_ips():
    """IPs de este equipo en la(s) red(es) local(es) (las que verían otros dispositivos de la
    misma wifi). La primera es la mejor suposición; puede haber más de una tarjeta de red."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))           # no llega a enviar nada: solo elige la ruta/tarjeta
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _find_vlc():
    """Ruta a vlc(.exe) si está instalado (PATH, o las rutas típicas de instalación en Windows
    cuando no se añadió al PATH); None si no se encuentra. Se usa para el botón «Abrir en VLC»
    de «En la red»: VLC decodifica MJPEG de toda la vida y, desde su propio menú Reproducción ->
    Renderizador, ya sabe enviarlo a un Chromecast sin que cam-ui tenga que programar nada de
    eso (comprobado con un flujo MJPEG real de cam-ui: VLC decodifica cada fotograma JPEG tal
    cual)."""
    exe = shutil.which("vlc") or shutil.which("vlc.exe")
    if exe:
        return exe
    if os.name == "nt":
        for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(env_var)
            if not base:
                continue
            p = os.path.join(base, "VideoLAN", "VLC", "vlc.exe")
            if os.path.isfile(p):
                return p
    return None


_VIEWER_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__CAM_UI_LABEL__</title>
<style>
html,body{margin:0;height:100%;background:#000;color:#ddd;font-family:system-ui,sans-serif;overflow:hidden}
/* 100vh en el móvil cuenta el hueco de la barra del navegador aunque esté oculta en ese
   momento -- la barra de abajo (#bar), pegada al fondo de #stage, quedaba por debajo de lo
   que se ve de verdad. 100dvh (el alto real visible) corrige eso; vh se deja antes como
   repuesto para un navegador que no conozca dvh todavía. */
#stage{position:relative;width:100%;height:100vh;height:100dvh;display:flex;align-items:center;
       justify-content:center;background:#000}
#v{width:100%;height:100%;object-fit:contain}
#bar{position:absolute;left:0;right:0;bottom:0;display:flex;flex-wrap:wrap;gap:10px;
     justify-content:center;padding:12px;padding-bottom:calc(12px + env(safe-area-inset-bottom));
     background:linear-gradient(transparent,rgba(0,0,0,.65))}
button{background:#2b2f36;color:#eee;border:1px solid #4a4f58;border-radius:7px;
       padding:9px 16px;font-size:15px;cursor:pointer}
button:active{background:#3a3f48}
button:disabled{opacity:.6}
#mute{display:none;padding:9px 12px}
#vol{display:none;width:90px;vertical-align:middle;accent-color:#6aa0ff}
#casthelp{display:none;position:absolute;left:50%;bottom:64px;transform:translateX(-50%);
  max-width:320px;background:#20242b;border:1px solid #4a4f58;border-radius:8px;
  padding:10px 14px;font-size:13px;line-height:1.45;color:#ddd;text-align:left;z-index:2}
#casthelp.show{display:block}
#casterr{display:none;position:absolute;left:50%;bottom:64px;transform:translateX(-50%);
  max-width:320px;background:#4a1f24;border:1px solid #8a3038;border-radius:8px;
  padding:10px 14px;font-size:13px;line-height:1.45;color:#ffd9dc;text-align:left;z-index:3}
#casterr.show{display:block}
google-cast-launcher{display:none;width:34px;height:34px;vertical-align:middle;cursor:pointer;
  --connected-color:#6aa0ff;--disconnected-color:#eee}
</style></head>
<body>
<div id="stage">
  <img id="v" src="/stream.mjpg" alt="cam-ui">
  <div id="bar">
    <button id="snd">&#128266; Activar sonido</button>
    <button id="mute">&#128266;</button>
    <input id="vol" type="range" min="0" max="1" step="0.05" value="1">
    <button id="full">&#10021; Pantalla completa</button>
    <button id="castBtn">&#128250; Chromecast</button>
    <google-cast-launcher id="castLauncher"></google-cast-launcher>
    <button id="addcam">&#128241; A&ntilde;adir c&aacute;mara del m&oacute;vil</button>
  </div>
  <div id="casthelp">Usa el icono <b>&laquo;Convertir en Chromecast&raquo;</b> de tu propio
    navegador &mdash; cam-ui no necesita nada más, le pasa esta misma pantalla. Lo traen
    Chrome y Edge, en el men&uacute; &#8942; (en el PC, dentro de «Guardar y compartir»).
    <b>Muchos navegadores NO lo traen</b> &mdash; Firefox, Safari, y tambi&eacute;n el propio
    de algunos m&oacute;viles (como el de Xiaomi/MIUI). Si el tuyo no lo tiene: abre esta
    misma direcci&oacute;n con Chrome, o hazlo desde el PC con cam-ui (&laquo;Abrir en
    VLC&raquo; &rarr; Renderizador).</div>
  <div id="casterr"></div>
</div>
<audio id="a" src="/audio.mp3" preload="none"></audio>
<script>
var a = document.getElementById("a"), snd = document.getElementById("snd");
var mute = document.getElementById("mute"), vol = document.getElementById("vol");
function setMuteIcon() {
  mute.innerHTML = (a.muted || a.volume === 0) ? "&#128263;" : "&#128266;";
}
snd.onclick = function () {
  a.play().then(function () {
    snd.style.display = "none";
    // "" no basta: #mute/#vol se ocultan por una regla de la hoja de estilos (#mute{display:
    // none}), no por un estilo en línea, así que vaciar el estilo en línea no la quita -- hace
    // falta un valor de verdad que pese más (comprobado con un navegador real, ver el mismo
    // arreglo en el botón «Añadir cámara del móvil» de más abajo)
    mute.style.display = "inline-block";
    vol.style.display = "inline-block";
    setMuteIcon();
  }).catch(function () {
    snd.textContent = "\\u26A0 Sin sonido (reinicia la pagina)";
  });
};
mute.onclick = function () {
  a.muted = !a.muted;
  setMuteIcon();
};
vol.oninput = function () {
  a.volume = parseFloat(vol.value);
  if (a.volume > 0 && a.muted) { a.muted = false; }
  setMuteIcon();
};
var stage = document.getElementById("stage"), full = document.getElementById("full");
function inFs() { return !!(document.fullscreenElement || document.webkitFullscreenElement); }
full.onclick = function () {
  if (inFs()) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  } else {
    (stage.requestFullscreen || stage.webkitRequestFullscreen).call(stage);
  }
};
document.addEventListener("fullscreenchange", function () {
  full.textContent = inFs() ? "\\u2715 Salir de pantalla completa" : "\\u2721 Pantalla completa";
});
document.addEventListener("webkitfullscreenchange", function () {
  full.textContent = inFs() ? "\\u2715 Salir de pantalla completa" : "\\u2721 Pantalla completa";
});
var castBtn = document.getElementById("castBtn"), casthelp = document.getElementById("casthelp");
castBtn.onclick = function (e) {
  e.stopPropagation();
  casthelp.classList.toggle("show");
};
document.addEventListener("click", function (e) {
  if (e.target !== castBtn && !casthelp.contains(e.target)) { casthelp.classList.remove("show"); }
});
// Si el navegador trae de verdad el SDK de Cast (Chrome/Edge con internet), se cambia el botón
// de ayuda de arriba por el icono nativo de Chromecast -- un clic, sin menús. Si no llega a
// cargar (sin internet, u otro navegador que no lo trae), se queda tal cual el botón de ayuda
// de siempre: no hace falta nada más para eso, es lo que ya había.
var castErr = document.getElementById("casterr");
function showCastErr(msg) {
  castErr.textContent = "Chromecast: " + msg;
  castErr.classList.add("show");
  casthelp.classList.remove("show");
}
document.addEventListener("click", function (e) {
  if (!castErr.contains(e.target)) { castErr.classList.remove("show"); }
});
// botón «Añadir cámara del móvil»: siempre a la vista -- si la función ya está encendida (ver
// _serve_page), lleva directo a la dirección de «/add»; si no, le pide al propio PC que se
// encienda sola (/addcam/start) y, en cuanto está lista, lleva igualmente a esa dirección --
// nunca hace falta ir antes a Ajustes a mano
var addcam = document.getElementById("addcam"), addCamUrl = "__PHONECAM_ADD_URL__";
var addcamLabel = addcam.textContent;
addcam.onclick = function () {
  if (addCamUrl) {
    location.href = addCamUrl;
    return;
  }
  addcam.disabled = true;
  addcam.textContent = "Encendiendo…";
  // la primera vez que se enciende en todo el PC (o si cambió de red) hace falta generar un
  // certificado propio nuevo, y eso sí puede tardar unos segundos de verdad -- si se alarga,
  // que el botón lo diga, en vez de parecer que se ha quedado colgado sin más
  var tardando = setTimeout(function () {
    addcam.textContent = "Encendiendo… (la primera vez puede tardar un poco)";
  }, 3000);
  fetch("/addcam/start", {method: "POST"}).then(function (r) {
    return r.json();
  }).then(function (data) {
    clearTimeout(tardando);
    if (!data || !data.url) { throw new Error("sin url"); }
    location.href = data.url;
  }).catch(function () {
    clearTimeout(tardando);
    addcam.disabled = false;
    addcam.textContent = "No se pudo encender. Reintentar";
    setTimeout(function () { addcam.textContent = addcamLabel; }, 3000);
  });
};
window["__onGCastApiAvailable"] = function (isAvailable) {
  if (!isAvailable || typeof cast === "undefined") { return; }
  try {
    var ctx = cast.framework.CastContext.getInstance();
    ctx.setOptions({
      receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
      autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED
    });
    document.getElementById("castLauncher").style.display = "inline-block";
    castBtn.style.display = "none";
    ctx.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED,
      function (ev) {
        var st = ev.sessionState;
        if (st !== cast.framework.SessionState.SESSION_STARTED &&
            st !== cast.framework.SessionState.SESSION_RESUMED) {
          return;
        }
        var session = ctx.getCurrentSession();
        if (!session) { return; }
        var info = new chrome.cast.media.MediaInfo("__CAM_CAST_URL__", "application/x-mpegURL");
        info.streamType = chrome.cast.media.StreamType.LIVE;
        // para que la propia tele diga DE QUÉ PC es (si hay más de uno en la wifi, "En la red"
        // a secas no lo decía); location.hostname es el mismo que ya llevaba la propia dirección
        info.metadata = new chrome.cast.media.GenericMediaMetadata();
        info.metadata.title = "cam-ui@" + location.hostname;
        session.loadMedia(new chrome.cast.media.LoadRequest(info)).then(function () {
          // la tele aceptó la orden -- de todas formas, si falla DESPUÉS (p. ej. no consigue
          // reproducir ese formato), que no se quede callado sin más
          var media = session.getMediaSession();
          if (!media) { return; }
          media.addUpdateListener(function (isAlive) {
            if (!isAlive) { return; }
            if (media.playerState === chrome.cast.media.PlayerState.IDLE &&
                media.idleReason === chrome.cast.media.IdleReason.ERROR) {
              showCastErr("la tele no pudo reproducirlo (fallo al reproducir)");
            }
          });
        }).catch(function (err) {
          showCastErr("no se pudo empezar a reproducir (" + err + ")");
        });
      });
  } catch (e) {
    // sin SDK de verdad disponible pese a todo: se queda el botón de ayuda de siempre
  }
};
</script>
<script async src="https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1"></script>
</body></html>
""".encode("utf-8")


class _MjpegHandler(http.server.BaseHTTPRequestHandler):
    """Página sencilla (vídeo + sonido + pantalla completa) en «/», el vídeo en vivo como
    multipart/x-mixed-replace en «/stream.mjpg» (lo que entiende un navegador sin más) y, si hay
    micrófono, el sonido como MP3 en «/audio.mp3». Cada petición va en su propio hilo
    (ThreadingHTTPServer); un visor lento o colgado solo se queda sin fotogramas o sonido nuevos,
    nunca bloquea a los demás ni a ffmpeg."""

    def log_message(self, *_a):
        pass                                      # no ensuciar el registro con cada petición

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_page()
        elif path == "/stream.mjpg":
            self._serve_video()
        elif path == "/audio.mp3":
            self._serve_audio()
        elif path == "/cast.m3u8":
            self._serve_hls_playlist()
        elif path.startswith("/cast/"):
            self._serve_hls_segment(path[len("/cast/"):])
        else:
            self._send_404()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/addcam/start":
            self._addcam_start()
        else:
            try:
                self.send_response(404)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _this_host(self):
        ips = _local_ips()
        host = ips[0] if ips else (self.server.server_address[0] or "127.0.0.1")
        return "127.0.0.1" if host in ("0.0.0.0", "") else host

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _addcam_start(self):
        """El propio botón «Añadir cámara del móvil» de esta página, cuando «Cámara del
        móvil» todavía no está encendida en el PC: en vez de decir «actívala a mano en
        Ajustes», pide aquí que se encienda ella sola. Este hilo (uno por petición, igual que
        el resto de _MjpegHandler) no puede tocar Tk directamente -- solo deja el aviso en
        phonecam_remote_q, que _tick() recoge en el hilo de Tk (mismo mecanismo que
        _phonecam_q/_phonecam_pair_q) -- y aquí se espera a que ese encendido termine para
        poder devolver ya mismo la dirección real de «/add» a la que el móvil debe ir. Casi
        siempre es instantáneo (solo abrir dos sockets), PERO la primera vez que se activa en
        todo el equipo (o si cambia la IP detectada) hace falta generar antes un certificado
        propio nuevo (clave RSA de verdad, ver _phonecam_ensure_cert) -- eso sí puede tardar
        unos segundos de verdad en un PC más lento, así que se le da un margen generoso antes
        de rendirse. El emparejamiento en sí sigue protegido igual que siempre: aunque
        cualquiera de la wifi pueda encenderla así, el QR para emparejarse solo aparece en la
        propia pantalla del PC (ver PhoneCamQrDialog), así que sigue haciendo falta estar
        delante de ella para leerlo."""
        owner = self.server.owner
        q = getattr(owner, "phonecam_remote_q", None)
        phonecam = getattr(owner, "phonecam", None)
        if q is None or phonecam is None:
            self._send_json(503, {"error": "No disponible."})
            return
        if not phonecam.active:
            q.append(True)
        deadline = time.time() + 15.0
        while not phonecam.active and time.time() < deadline:
            time.sleep(0.05)
        if not phonecam.active:
            self._send_json(503, {"error": "No se pudo encender la cámara del móvil."})
            return
        url = "https://{0}:{1}/add".format(self._this_host(), phonecam.port)
        self._send_json(200, {"url": url})

    def _send_404(self):
        # antes esto no llevaba ni Content-Type ni cuerpo -- un navegador de móvil (Safari o
        # Chrome en iOS, p. ej. al escribir «/add» aquí por error, pensando en el puerto de la
        # cámara del móvil y no en este) no sabe qué hacer con eso y ofrece «descargar» un
        # archivo vacío con un nombre inventado, en vez de avisar sin más de que esa dirección
        # no existe -- de ahí este HTML mínimo, con enlace de vuelta a «/».
        label = "cam-ui@{0}".format(self._this_host())
        body = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                "<title>cam-ui</title></head><body style=\"background:#000;color:#ddd;"
                "font-family:system-ui,sans-serif;text-align:center;padding:18vh 16px\">"
                "<p>Esta dirección no existe aquí.</p>"
                "<p><a href=\"/\" style=\"color:#6aa0ff\">Ir a «{0}»</a></p>"
                "</body></html>").format(label).encode("utf-8")
        try:
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_OPTIONS(self):
        # El propio Chromecast (su receptor «de serie») es quien pide /cast.m3u8 y /cast/*.ts
        # directamente -- no pasa por el navegador del visor -- y por eso cuentan como petición
        # «de otro origen» (CORS) a ojos de Google: sin estas cabeceras, la tele puede llegar a
        # aceptar la orden de reproducir y aun así quedarse sin imagen ni sonido, sin avisar de
        # ningún error, porque el propio receptor no consigue bajarse ni la lista ni los trozos.
        path = self.path.split("?", 1)[0]
        try:
            if path == "/cast.m3u8" or path.startswith("/cast/"):
                self.send_response(204)
                self._send_cors_headers()
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        except Exception:
            pass

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")

    def _serve_page(self):
        try:
            owner = self.server.owner
            host = self._this_host()
            cast_url = "http://{0}:{1}/cast.m3u8".format(host, owner.port)
            # un nombre que de verdad dice QUÉ PC es (si hay más de uno en la wifi, "en la red"
            # a secas no distingue nada) -- se usa aquí (título de la pestaña) y en la propia tele
            # al enviar a Chromecast (ver __onGCastApiAvailable, con location.hostname)
            label = "cam-ui@{0}".format(host)
            # el botón «Añadir cámara del móvil» solo trae aquí la dirección final si esa función
            # ya está encendida; si no, se deja vacío y es el propio botón, al pulsarlo, quien
            # pide por su cuenta que se encienda sola (ver /addcam/start, _addcam_start) en vez
            # de decir que hay que ir antes a Ajustes a mano
            phonecam = getattr(owner, "phonecam", None)
            add_url = ("https://{0}:{1}/add".format(host, phonecam.port)
                       if phonecam is not None and phonecam.active else "")
            body = (_VIEWER_HTML.replace(b"__CAM_UI_LABEL__", label.encode("utf-8"))
                                 .replace(b"__CAM_CAST_URL__", cast_url.encode("utf-8"))
                                 .replace(b"__PHONECAM_ADD_URL__", add_url.encode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, private")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _serve_hls_playlist(self):
        """El botón nativo de Chromecast manda al propio Chromecast a pedir esto directamente
        (no pasa por el navegador) -- si nadie lo había pedido antes, arranca aquí mismo el
        ffmpeg de HLS y espera un poco a que haya trozos de verdad antes de contestar."""
        owner = self.server.owner
        if not owner.hls_start():
            try:
                self.send_response(503)
                self.send_header("Retry-After", "2")
                self._send_cors_headers()
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        owner._hls_ready_ev.wait(10)
        owner.hls_touch()
        d = owner._hls_dir
        play_path = d and os.path.join(d, "cast.m3u8")
        try:
            if not play_path or not os.path.isfile(play_path):
                self.send_response(503)
                self.send_header("Retry-After", "2")
                self._send_cors_headers()
                self.end_headers()
                return
            with open(play_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            # El propio Chromecast pide esto directamente (CORS) -- ver do_OPTIONS más arriba
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _serve_hls_segment(self, name):
        owner = self.server.owner
        owner.hls_touch()
        d = owner._hls_dir
        if not d or not name or "/" in name or "\\" in name or ".." in name:
            try:
                self.send_response(404)
                self._send_cors_headers()
                self.end_headers()
            except Exception:
                pass
            return
        try:
            with open(os.path.join(d, name), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            # El propio Chromecast pide esto directamente (CORS) -- ver do_OPTIONS más arriba
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            try:
                self.send_response(404)
                self._send_cors_headers()
                self.end_headers()
            except Exception:
                pass
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _serve_video(self):
        owner = self.server.owner
        boundary = "camui"
        try:
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + boundary)
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        owner._client_delta(1)
        last = None
        try:
            while not owner._closed:
                with owner._lk:
                    frame, gen = owner._jpeg, owner._jgen
                if frame is None or gen == last:
                    time.sleep(0.04)
                    continue
                last = gen
                self.wfile.write(b"--" + boundary.encode() + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(("Content-Length: " + str(len(frame)) + "\r\n\r\n").encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            owner._client_delta(-1)

    def _serve_audio(self):
        """Reenvía tal cual los bytes MP3 que manda ffmpeg (nuestro servidor no los toca ni los
        decodifica): cada visor tiene su propia cola pequeña, así que uno lento o colgado se
        queda sin trozos de sonido sin afectar a los demás ni a ffmpeg."""
        owner = self.server.owner
        q = queue.Queue(maxsize=200)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache, private")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        owner._aclient_add(q)
        try:
            while not owner._closed:
                try:
                    chunk = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if chunk:
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            owner._aclient_remove(q)


class _MjpegHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class IpServer:
    """Recibe de ffmpeg fotogramas RGB por un socket local (igual mecanismo que la cámara
    virtual) y los ofrece como MJPEG por HTTP a cualquier navegador de la misma red. El servidor
    HTTP es propio, no ffmpeg: un visor que se cuelga o se desconecta no afecta ni a ffmpeg ni a
    la grabación, solo se queda sin imagen ese visor. El servidor HTTP se mantiene entre
    reinicios de ffmpeg (p. ej. al empezar/parar una grabación), igual que la cámara virtual."""

    def __init__(self):
        self.w = self.h = 0
        self.fps = IP_FPS
        self.pix, self.bpp = "rgb24", 3
        self.port = IP_PORT_DEFAULT
        self.sent = self.dropped = self.received = 0
        self.errors = 0
        self.clients = 0
        self._gen = 0
        self._srv = None
        self._http = None
        self._encoder = None
        self._latest = None
        self._lk = threading.Lock()
        self._ev = threading.Event()
        self._jpeg = None
        self._jgen = 0
        self._closed = False
        self.phonecam = None         # PhoneCamHub, si CamApp lo enlaza (ver CamApp.__init__) --
                                      # para el botón «Añadir cámara del móvil» de esta página
        self.phonecam_remote_q = None   # cola para encenderla sola al pulsar ese botón sin
                                         # tenerla ya encendida (ver _MjpegHandler._addcam_start)
        # ---- sonido (opcional: solo si hay micrófono en el plan) --------------------------
        self.aport = 0
        self.a_received = self.a_dropped = 0
        self._agen = 0
        self._asrv = None
        self._aclients = []
        self._aclients_lk = threading.Lock()
        # ---- HLS para Chromecast nativo (aparte del MJPEG de siempre; ver constantes HLS_*) --
        self._hls_lock = threading.Lock()
        self._hls_proc = None
        self._hls_dir = None
        self._hls_q = None
        self._hls_gen = 0
        self._hls_ready_ev = threading.Event()
        self._hls_last_req = 0.0

    @property
    def active(self):
        return self._http is not None

    def open(self, w, h, fps=IP_FPS, port=IP_PORT_DEFAULT):
        """Abre el servidor HTTP (w×h fijo). Lanza OSError (puerto ocupado) u otra excepción con
        un texto claro si no se puede."""
        self.release()
        http_srv = _MjpegHTTPServer(("0.0.0.0", int(port)), _MjpegHandler)
        http_srv.owner = self
        self.w, self.h, self.fps, self.port = int(w), int(h), int(fps), int(port)
        self.box = (self.w, self.h)   # el máximo elegido; w/h es lo que de verdad llega (ver arm)
        self.sent = self.dropped = self.received = self.errors = self.clients = 0
        self.a_received = self.a_dropped = 0
        self._closed = False
        self._latest = None
        self._jpeg, self._jgen = None, 0
        self._ev.clear()
        self._http = http_srv
        threading.Thread(target=http_srv.serve_forever, daemon=True).start()
        self._encoder = threading.Thread(target=self._encode_loop, daemon=True)
        self._encoder.start()
        log.info("salida en red abierta: puerto %d, %dx%d @%d", self.port, self.w, self.h, self.fps)

    def arm(self, w=None, h=None):
        """Prepara un socket de escucha para la próxima conexión de ffmpeg; devuelve su puerto.
        w, h: tamaño de los fotogramas que mandará ESE ffmpeg (la forma real del mosaico, ver
        ip_fit_size) -- puede cambiar de un arranque a otro si cambia el mosaico. Si cambia con el
        HLS de Chromecast en marcha, ese se para (lleva el tamaño fijado al arrancar); se vuelve a
        encender solo con la siguiente petición de la tele."""
        if w and h and (int(w), int(h)) != (self.w, self.h):
            self.w, self.h = int(w), int(h)
            log.info("salida en red: %dx%d (forma real del mosaico)", self.w, self.h)
            if self._hls_proc is not None:
                self._hls_stop()
        self._drop_srv()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(0.4)
        self._srv = srv
        self._gen += 1
        threading.Thread(target=self._serve, args=(srv, self._gen), daemon=True).start()
        return srv.getsockname()[1]

    def _drop_srv(self):
        srv, self._srv = self._srv, None
        self._gen += 1                       # el hilo que esperaba conexión termina solo
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass

    def arm_audio(self):
        """Igual que arm(), pero para el socket del sonido (puerto distinto, independiente del
        de vídeo: a ffmpeg no le hace falta tener micrófono para que el vídeo funcione)."""
        self._drop_asrv()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(0.4)
        self._asrv = srv
        self._agen += 1
        threading.Thread(target=self._aserve, args=(srv, self._agen), daemon=True).start()
        self.aport = srv.getsockname()[1]
        return self.aport

    def _drop_asrv(self):
        srv, self._asrv = self._asrv, None
        self._agen += 1
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass

    def _aserve(self, srv, gen):
        conn = None
        t_end = time.time() + 30
        while gen == self._agen and not self._closed and time.time() < t_end:
            try:
                conn, _a = srv.accept()
                break
            except socket.timeout:
                continue
            except OSError:
                break
        try:
            srv.close()
        except Exception:
            pass
        if conn is None:
            return
        conn.settimeout(None)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        log.info("salida en red: audio conectado")
        try:
            self._aread(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            log.info("salida en red: audio desconectado (%d recibidos, %d descartados)",
                     self.a_received, self.a_dropped)

    def _aread(self, conn):
        """Reenvía tal cual (sin decodificar) cada trozo de MP3 que llega a cada oyente conectado
        a /audio.mp3; nunca espera a que alguno lo recoja, así que ffmpeg no se bloquea."""
        while not self._closed:
            try:
                chunk = conn.recv(4096)
            except Exception:
                return
            if not chunk:
                return
            self.a_received += 1
            if self._http is None:
                continue                      # cerrada: solo se vacía el socket
            with self._aclients_lk:
                qs = list(self._aclients)
            for q in qs:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    self.a_dropped += 1       # ese oyente se queda sin este trozo; no bloquea a nadie

    def _aclient_add(self, q):
        with self._aclients_lk:
            self._aclients.append(q)

    def _aclient_remove(self, q):
        with self._aclients_lk:
            try:
                self._aclients.remove(q)
            except ValueError:
                pass

    def _serve(self, srv, gen):
        conn = None
        t_end = time.time() + 30
        while gen == self._gen and not self._closed and time.time() < t_end:
            try:
                conn, _a = srv.accept()
                break
            except socket.timeout:
                continue
            except OSError:
                break
        try:
            srv.close()
        except Exception:
            pass
        if conn is None:
            return
        conn.settimeout(None)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        log.info("salida en red: ffmpeg conectado")
        try:
            self._read(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            log.info("salida en red: ffmpeg desconectado (%d recibidos, %d codificados, %d descartados)",
                     self.received, self.sent, self.dropped)

    def _read(self, conn):
        w, h, bpp = self.w, self.h, self.bpp
        fb = w * h * bpp
        while not self._closed:
            buf = bytearray(fb)
            mv = memoryview(buf)
            pos = 0
            while pos < fb:
                try:
                    n = conn.recv_into(mv[pos:])
                except Exception:
                    return
                if not n:
                    return
                pos += n
            self.received += 1
            if self._http is None:
                continue                       # cerrada: solo se vacía el socket
            arr = np.frombuffer(buf, np.uint8).reshape((h, w, bpp))
            with self._lk:
                if self._latest is not None:
                    self.dropped += 1          # el anterior no llegó a codificarse
                self._latest = arr
            self._ev.set()

    def _encode_loop(self):
        http_srv = self._http
        while not self._closed and self._http is http_srv:
            if not self._ev.wait(0.5):
                continue
            self._ev.clear()
            with self._lk:
                arr, self._latest = self._latest, None
            if arr is None or arr.shape[0] != self.h or arr.shape[1] != self.w or arr.shape[2] != self.bpp:
                continue
            try:
                buf = io.BytesIO()
                Image.fromarray(arr, "RGB").save(buf, format="JPEG", quality=IP_JPEG_QUALITY)
                data = buf.getvalue()
                with self._lk:
                    self._jpeg = data
                    self._jgen += 1
                self.sent += 1
            except Exception:
                self.errors += 1
                if self.errors <= 3:
                    log.warning("salida en red: error al codificar:\n%s", traceback.format_exc())
            # ---- de paso, si hay alguien viéndolo por Chromecast (HLS armado), le pasamos el
            # mismo fotograma crudo -- sin bloquear nunca esto: si va con retraso, se descarta
            # (igual que con el audio en _aread) y ya le tocará con el siguiente.
            q = self._hls_q
            if q is not None:
                try:
                    q.put_nowait(arr.tobytes())
                except queue.Full:
                    pass

    def _client_delta(self, d):
        with self._lk:
            self.clients = max(0, self.clients + d)

    # ---- HLS / Chromecast nativo ------------------------------------------------------------
    def hls_touch(self):
        """Alguien acaba de pedir la lista o un trozo: se sigue necesitando, no apagar por
        inactividad todavía."""
        self._hls_last_req = time.time()

    def hls_idle_check(self):
        """Llamar de vez en cuando (desde _tick de la app): si nadie pide trozos nuevos desde
        hace rato, es que se dejó de emitir a la tele (o nunca llegó a verse), así que se apaga
        solo -- si no, ese ffmpeg de más se quedaría encendido para siempre de fondo."""
        if self._hls_proc is not None and time.time() - self._hls_last_req > HLS_IDLE_SECONDS:
            log.info("salida en red: HLS de Chromecast parado por inactividad")
            self._hls_stop()

    def hls_start(self):
        """Arranca (si no lo estaba ya) un segundo ffmpeg que convierte el mosaico a H.264/HLS
        para el botón nativo de Chromecast -- el MJPEG de siempre sigue exactamente igual, esto
        es solo un añadido. Se alimenta de los mismos fotogramas crudos que ya se reciben por el
        socket (no del MJPEG ya comprimido, para no perder calidad ni CPU recodificando dos
        veces). Devuelve True si queda en marcha (ya lo estaba, o se acaba de lanzar)."""
        self.hls_touch()
        with self._hls_lock:
            if self._hls_proc is not None and self._hls_proc.poll() is None:
                return True
            if not self.active or self.w <= 0 or self.h <= 0:
                return False
            self._hls_cleanup_dir()
            try:
                self._hls_dir = tempfile.mkdtemp(prefix="camui_hls_")
            except Exception:
                log.warning("salida en red: no se pudo crear la carpeta temporal para HLS:\n%s",
                           traceback.format_exc())
                return False
            cmd = [FFMPEG, "-nostdin", "-loglevel", "error",
                  "-f", "rawvideo", "-pix_fmt", "rgb24",
                  "-s", "{0}x{1}".format(self.w, self.h), "-r", str(self.fps), "-i", "-",
                  # pista de audio muda (silencio generado por ffmpeg, no un micrófono real) --
                  # ver el comentario junto a HLS_SEGMENT_SECONDS sobre por qué hace falta
                  "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                  "-map", "0:v", "-map", "1:a",
                  "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                  "-profile:v", "baseline", "-pix_fmt", "yuv420p",
                  "-force_key_frames", "expr:gte(t,n_forced*{0})".format(HLS_SEGMENT_SECONDS),
                  "-b:v", HLS_BITRATE, "-maxrate", HLS_BITRATE, "-bufsize", "4000k",
                  "-c:a", "aac", "-b:a", "64k", "-ar", "44100",
                  "-f", "hls", "-hls_time", str(HLS_SEGMENT_SECONDS),
                  "-hls_list_size", str(HLS_LIST_SIZE),
                  "-hls_flags", "delete_segments+independent_segments+append_list",
                  "-hls_base_url", "/cast/",
                  "-hls_segment_filename", "seg_%05d.ts", "cast.m3u8"]
            try:
                proc = subprocess.Popen(cmd, cwd=self._hls_dir, stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        creationflags=CREATE_NO_WINDOW)
            except Exception:
                log.warning("salida en red: no se pudo lanzar ffmpeg para HLS (Chromecast):\n%s",
                           traceback.format_exc())
                self._hls_cleanup_dir()
                return False
            self._hls_proc = proc
            self._hls_q = queue.Queue(maxsize=2)
            self._hls_ready_ev = threading.Event()
            self._hls_gen += 1
            gen = self._hls_gen
            threading.Thread(target=self._hls_feed, args=(proc, self._hls_q, gen),
                             daemon=True).start()
            threading.Thread(target=self._hls_wait_ready, args=(gen,), daemon=True).start()
            log.info("salida en red: HLS de Chromecast arrancado (%dx%d @%d)",
                    self.w, self.h, self.fps)
            return True

    def _hls_feed(self, proc, q, gen):
        """Pasa cada fotograma crudo a la entrada de ese ffmpeg; si se queda sin aire (CPU lenta)
        se descartan los que lleguen de más, nunca se bloquea _encode_loop por esto."""
        try:
            while gen == self._hls_gen and proc.poll() is None:
                try:
                    data = q.get(timeout=1.0)
                except queue.Empty:
                    continue
                try:
                    proc.stdin.write(data)
                except Exception:
                    break
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def _hls_wait_ready(self, gen):
        """Hasta que la lista de reproducción tenga ya un par de trozos de verdad escritos (o
        se agote el tiempo): así el primer visor no se encuentra con una lista vacía."""
        t_end = time.time() + 10
        while gen == self._hls_gen and time.time() < t_end:
            d = self._hls_dir
            play_path = d and os.path.join(d, "cast.m3u8")
            try:
                if play_path and os.path.isfile(play_path):
                    with open(play_path, "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                    if txt.count("#EXTINF") >= 2:
                        self._hls_ready_ev.set()
                        return
            except Exception:
                pass
            time.sleep(0.25)

    def _hls_stop(self, wait=False):
        """Para el ffmpeg de HLS (si lo hay) y borra su carpeta temporal. Por defecto no espera
        -- se limpia en un hilo aparte -- porque normalmente se llama desde hls_idle_check() y
        no debe retrasar nada. release() sí espera (acotado: como mucho ~1.5s) para que, al
        apagar «En la red» del todo, no quede un ffmpeg de más de fondo ni a medio cerrar."""
        with self._hls_lock:
            self._hls_gen += 1                  # los hilos de la tanda anterior se paran solos
            proc, self._hls_proc = self._hls_proc, None
            self._hls_q = None
            old_dir, self._hls_dir = self._hls_dir, None
        if proc is None:
            if old_dir:
                shutil.rmtree(old_dir, ignore_errors=True)
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        if wait:
            self._hls_reap(proc, old_dir, timeout=1.5)
        else:
            threading.Thread(target=self._hls_reap, args=(proc, old_dir), daemon=True).start()

    @staticmethod
    def _hls_reap(proc, old_dir, timeout=5):
        """Espera (acotado) a que ese ffmpeg termine de verdad -- y si no, lo mata -- antes de
        borrar su carpeta: borrarla mientras todavía escribe en ella es una carrera que puede
        dejar restos (shutil.rmtree con ignore_errors se calla el error y listo, sin limpiar)."""
        try:
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        if old_dir:
            shutil.rmtree(old_dir, ignore_errors=True)

    def _hls_cleanup_dir(self):
        """Solo para el arranque: por si quedó algo suelto de una tanda anterior que no pasó por
        _hls_stop() (no debería ocurrir, pero mejor no dejar basura)."""
        d, self._hls_dir = self._hls_dir, None
        if d:
            shutil.rmtree(d, ignore_errors=True)

    def release(self):
        """Para el servidor HTTP (los fotogramas que sigan llegando se descartan sin bloquear
        ffmpeg)."""
        self._hls_stop(wait=True)
        http_srv, self._http = self._http, None
        self._ev.set()
        if http_srv is not None:
            try:
                http_srv.shutdown()
                http_srv.server_close()
            except Exception:
                pass
            log.info("salida en red cerrada")
        t = self._encoder
        if t is not None and t is not threading.current_thread():
            t.join(1.0)

    def close(self):
        self._closed = True
        self._drop_srv()
        self._drop_asrv()
        self.release()


_METER_RE = re.compile(r"lavfi\.astats\.(\d)\.Peak_level=(\S+)")
_INPUT_RE = re.compile(r"^Input #(\d+),")
_OUTPUT_RE = re.compile(r"^Output #\d+,")
_VFPS_RE = re.compile(r", ([\d.]+) fps")
_VSTREAM_RE = re.compile(r"^\s*Stream #(\d+):\d+.*?: Video: .*?, (\d{2,5})x(\d{2,5})(?:[ ,\[]|$)")


PIPE_BYTES = 8 * 1024 * 1024     # búfer de las tuberías de ffmpeg (Windows: 4 KB por defecto)


def _popen_big(cmd):
    """Lanza ffmpeg con stdout/stderr por tuberías de 8 MB. Con las de 4 KB que crea subprocess,
    ffmpeg se queda bloqueado escribiendo la vista previa cada vez que Python tarda unos ms en
    leer, y eso congela TODO el grafo (vídeo repetido, huecos de audio, grabación entrecortada)."""
    kw = dict(stdin=subprocess.PIPE, bufsize=0, creationflags=CREATE_NO_WINDOW)
    if os.name == "nt" and not os.environ.get("CAMWIN32_SMALLPIPE"):
        fds = []
        try:
            import _winapi
            import msvcrt
            ends = []
            for _ in range(2):
                rh, wh = _winapi.CreatePipe(None, PIPE_BYTES)
                rfd = msvcrt.open_osfhandle(rh, os.O_RDONLY)
                wfd = msvcrt.open_osfhandle(wh, 0)
                fds.append((rfd, wfd))
                ends.append((rfd, wfd))
            p = subprocess.Popen(cmd, stdout=ends[0][1], stderr=ends[1][1], **kw)
            for _r, w in ends:
                os.close(w)                   # la copia del hijo ya está heredada
            p.stdout = os.fdopen(ends[0][0], "rb", buffering=0)
            p.stderr = os.fdopen(ends[1][0], "rb", buffering=0)
            log.info("tuberías grandes (%d MB) activas", PIPE_BYTES >> 20)
            return p
        except Exception as e:
            log.warning("no se pudieron crear tuberías grandes (%r); uso las normales", e)
            for r, w in fds:
                for fd in (r, w):
                    try:
                        os.close(fd)
                    except Exception:
                        pass
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


BUSY_PAT = re.compile(r"could not run graph|already in use|device or resource busy|"
                      r"i/o error|could not (?:open|find)|error opening input|"
                      r"cannot open|permission denied", re.I)
MODE_ERR_PAT = re.compile(r"could not set (?:video|audio) options", re.I)
BUSY_WAITS = (0.8, 1.5, 2.5, 3.5)     # pausa entre intentos (los USB tardan en soltar la cámara)
RELEASE_GAP = 1.0                      # mínimo entre cerrar ffmpeg y volver a abrir las cámaras
# FFmpegEngine.start() solo espera 1.6s a que ffmpeg NO se caiga para darlo por arrancado (ver
# "if p.poll() is None: return True" más abajo) -- eso basta para detectar un fallo inmediato
# (cámara ocupada, modo rechazado...), pero no para detectar un ffmpeg que se queda colgado sin
# llegar a entregar ni un fotograma (p.ej. negociando con una cámara lenta en iniciar, como el
# software de una réflex/EOS, o atascado con otra del mosaico): is_running() solo mira si el
# proceso sigue vivo, no si de verdad está produciendo algo, así que ese caso se queda sin
# detectar -- ver CamApp._on_stuck. Generoso a propósito: más vale tarde que un falso aviso con
# un mosaico de verdad grande que solo tarda en arrancar.
STUCK_PREVIEW_SECONDS = 15.0


def is_mode_error(text):
    """La cámara rechaza el modo pedido (tamaño/fps/formato): no es que esté ocupada."""
    return bool(MODE_ERR_PAT.search(text or ""))


def is_busy_error(text):
    return bool(BUSY_PAT.search(text or "")) and not is_mode_error(text)


def kill_pid(pid):
    """Termina un proceso (y sus hijos) a la fuerza. True si se pudo."""
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                               timeout=8, creationflags=CREATE_NO_WINDOW)
            return r.returncode == 0
        os.kill(int(pid), 9)
        return True
    except Exception as e:
        log.warning("kill %s: %r", pid, e)
        return False


def failing_spec(text):
    """Dispositivo de vídeo/audio que ffmpeg no pudo abrir, a partir de su mensaje de error."""
    m = re.findall(r"Error opening input file (video|audio)=(.+?)\.\s*$", text or "", re.M)
    return (m[-1][0], m[-1][1].strip()) if m else (None, None)


def failing_net_spec(text):
    """URL de una cámara de red que ffmpeg no pudo abrir (p. ej. «Connection ... failed» o
    «Error opening input file http://host:puerto/ruta.»): a diferencia de failing_spec (las
    DirectShow locales, con el prefijo video=/audio=), aquí ffmpeg pone la URL tal cual."""
    m = re.findall(r"Error opening input file ((?:https?|rtsps?)://\S+?)\.\s*$", text or "", re.M)
    return m[-1].strip() if m else None


def failing_cam_index(cams, text):
    """Índice (en la lista de cámaras del plan) de la que ffmpeg no pudo abrir, según su
    mensaje de error -- tanto una DirectShow local (video=NOMBRE) como una de red (la URL
    tal cual, sin el prefijo video=). None si no se identifica ninguna."""
    kind, spec = failing_spec(text)
    if kind == "video" and spec:
        for i, c in enumerate(cams):
            if c.get("spec") == spec:
                return i
    url = failing_net_spec(text)
    if url:
        for i, c in enumerate(cams):
            if c.get("spec") == url:
                return i
    return None


def ffmpeg_pids():
    """PIDs de todos los ffmpeg.exe del sistema (Windows)."""
    if os.name != "nt":
        return []
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=8,
                           creationflags=CREATE_NO_WINDOW)
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower().startswith("ffmpeg"):
            try:
                out.append(int(parts[1]))
            except ValueError:
                pass
    return out


# =====================================================================
#  Diagnóstico de rendimiento: cámaras y micro, solos y a la vez
# =====================================================================
_DV_RE = re.compile(r"Stream #0:0.*?Video: (\w+).*?, (\d{2,5})x(\d{2,5}).*?, ([\d.]+) fps")
_FRAMES_RE = re.compile(r"frame=\s*(\d+)")
_TIME_RE = re.compile(r"time=(\d+):(\d+):([\d.]+)")
_SIL_RE = re.compile(r"silence_duration: ([\d.]+)")


def _probe_cmd(ffmpeg, kind, spec, opt, secs, input_fn=None):
    input_fn = input_fn or _dshow_input
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-stats", "-y"]
    cmd += input_fn(kind, spec, opt)
    cmd += ["-t", str(secs)]
    if kind == "video":
        cmd += ["-an", "-fps_mode", "passthrough", "-f", "null", "-"]
    else:
        cmd += ["-vn", "-af", "silencedetect=noise=-90dB:d=0.02", "-f", "null", "-"]
    return cmd


def _parse_probe(kind, text, secs):
    r = {"kind": kind, "warn": len(re.findall(r"real-time buffer|dropped|too full", text, re.I)),
         "err": ""}
    txt = text.replace("\r", "\n")
    if kind == "video":
        m = _DV_RE.search(txt)
        if m:
            r.update(codec=m.group(1), size=(int(m.group(2)), int(m.group(3))), nominal=float(m.group(4)))
        fr = _FRAMES_RE.findall(txt)
        n = int(fr[-1]) if fr else 0
        r["frames"] = n
        r["fps"] = n / float(secs)
    else:
        tm = _TIME_RE.findall(txt)
        got = (int(tm[-1][0]) * 3600 + int(tm[-1][1]) * 60 + float(tm[-1][2])) if tm else 0.0
        sil = [float(x) for x in _SIL_RE.findall(txt)]
        r.update(got=got, silences=len(sil), sil_s=sum(sil), pct=100.0 * sum(sil) / max(0.1, got))
    if not (r.get("frames") or r.get("got")):
        r["err"] = " | ".join([l.strip() for l in txt.split("\n") if l.strip()][-3:])
        r["raw"] = txt[-1500:]
    return r


def _run_probes(jobs, secs, ffmpeg, input_fn=None):
    """jobs: [(clave, kind, spec, opt)] → se lanzan TODOS a la vez; devuelve {clave: resultado}."""
    procs = []
    for key, kind, spec, opt in jobs:
        cmd = _probe_cmd(ffmpeg, kind, spec, opt, secs, input_fn)
        log.info("diag %s: %s", key, subprocess.list2cmdline(cmd))
        procs.append((key, kind, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                  stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)))
    out = {}
    for key, kind, p in procs:
        try:
            err = p.communicate(timeout=secs + 25)[1].decode("utf-8", "ignore")
        except Exception:
            p.kill()
            err = "tiempo agotado"
        out[key] = _parse_probe(kind, err, secs)
    return out


def _preflight_probe(jobs, ffmpeg, secs=None):
    """Como _run_probes, pero para la prueba de red RÁPIDA antes de integrar una cámara nueva en
    el mosaico de verdad (ver CamApp._preflight_net_cams): aquí interesa fallar pronto, así que
    el margen de guarda tras el propio plazo es mucho más corto que los +25s de _run_probes (esos
    pensados para el botón «Diagnóstico» a mano, donde esperar un poco más no cuesta nada) -- el
    límite real lo pone el -rw_timeout, más corto, de la propia cámara (ver NET_PREFLIGHT_TIMEOUT_US
    e input_fn); este margen solo cubre que, por lo que sea (versión de ffmpeg rara...), no lo
    respetara."""
    secs = NET_PREFLIGHT_SECS if secs is None else secs
    input_fn = lambda k, s, o: _dshow_input(k, s, o, timeout_us=NET_PREFLIGHT_TIMEOUT_US)
    procs = []
    for key, kind, spec, opt in jobs:
        cmd = _probe_cmd(ffmpeg, kind, spec, opt, secs, input_fn)
        log.info("prueba de red %s: %s", key, subprocess.list2cmdline(cmd))
        procs.append((key, kind, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                  stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)))
    out = {}
    for key, kind, p in procs:
        try:
            err = p.communicate(timeout=secs + 3.0)[1].decode("utf-8", "ignore")
        except Exception:
            p.kill()
            try:
                p.communicate(timeout=3.0)
            except Exception:
                pass
            err = "tiempo agotado"
        out[key] = _parse_probe(kind, err, secs)
    return out


def run_diagnostics(ffmpeg, cams, audios, input_fn=None, secs=6, solo_secs=4):
    """cams: [(etiqueta, spec, modo)], audios: [(etiqueta, spec, búfer_ms)]. Devuelve el informe (texto)."""
    jobs_all = [(("v", i), "video", c[1], c[2]) for i, c in enumerate(cams)]
    jobs_all += [(("a", i), "audio", a[1], a[2]) for i, a in enumerate(audios)]
    together = _run_probes(jobs_all, secs, ffmpeg, input_fn) if len(jobs_all) > 1 else {}
    solo = {}
    for j in jobs_all:
        solo.update(_run_probes([j], solo_secs, ffmpeg, input_fn))
    notes = {}
    for i, c in enumerate(cams):             # modo que la cámara rechaza: se mide con el de fábrica
        k = ("v", i)
        if c[2] and is_mode_error(solo[k].get("raw", "")):
            notes[k] = c[2]
            solo[k] = _run_probes([(k, "video", c[1], None)], solo_secs, ffmpeg, input_fn)[k]
            if k in together:
                together[k] = solo[k]
    if len(jobs_all) == 1:
        together = solo
    L = ["DIAGNÓSTICO  ·  cada dispositivo solo ({0} s) y todos a la vez ({1} s)".format(solo_secs, secs), ""]
    verdict = []
    for i, c in enumerate(cams):
        k = ("v", i)
        s_, t_ = solo[k], together.get(k, solo[k])
        fmt = "{0} {1}x{2}".format(s_.get("codec", "?"), *s_.get("size", (0, 0))) if s_.get("size") else "formato desconocido"
        L.append("CÁMARA «{0}»  ({1}, anuncia {2:g} fps)".format(c[0], fmt, s_.get("nominal", 0)))
        L.append("    sola: {0:5.1f} fps     a la vez: {1:5.1f} fps     avisos de búfer: {2}".format(
            s_["fps"], t_["fps"], t_["warn"]))
        if k in notes:
            L.append("    (la cámara no admite el modo «{0}»: medida con el modo de fábrica del driver)".format(notes[k]))
            verdict.append("«{0}» rechaza el modo «{1}»: ponla en «Auto» o pulsa «Probar modos».".format(
                c[0], notes[k]))
        if s_["err"]:
            L.append("    ERROR: " + s_["err"])
            verdict.append("«{0}» no se pudo abrir ({1}).".format(c[0], s_["err"][:120]))
            continue
        nom = s_.get("nominal") or 30.0
        if s_["fps"] < 0.75 * nom:
            verdict.append("«{0}» solo da {1:.0f} fps incluso sola (anuncia {2:g}): la propia cámara "
                           "(poca luz → exposición larga, o el modo elegido). Prueba más luz, desactivar "
                           "la exposición automática en «Propiedades del driver» o elegir otro modo.".format(
                               c[0], s_["fps"], nom))
        elif t_["fps"] < 0.75 * s_["fps"]:
            verdict.append("«{0}» pasa de {1:.0f} a {2:.0f} fps cuando todo funciona a la vez: se comparte "
                           "ancho de banda USB o CPU.".format(c[0], s_["fps"], t_["fps"]))
    for i, a in enumerate(audios):
        k = ("a", i)
        s_, t_ = solo[k], together.get(k, solo[k])
        L.append("AUDIO «{0}»".format(a[0]))
        L.append("    solo: {0:.1f}% en silencio digital ({1} cortes)     a la vez: {2:.1f}% ({3} cortes)".format(
            s_.get("pct", 0), s_.get("silences", 0), t_.get("pct", 0), t_.get("silences", 0)))
        L.append("    recibido: {0:.1f} s de {1} s (solo) · {2:.1f} s de {3} s (a la vez)".format(
            s_.get("got", 0), solo_secs, t_.get("got", 0), secs))
        if s_["err"]:
            L.append("    ERROR: " + s_["err"])
            verdict.append("El audio «{0}» no se pudo abrir.".format(a[0]))
        elif s_.get("pct", 0) > 1.0:
            verdict.append("El micro «{0}» ya pierde audio SOLO ({1:.0f}% en silencio): es el driver o el "
                           "búfer; sube «Búfer de audio» o cambia de dispositivo/puerto.".format(a[0], s_["pct"]))
        elif t_.get("pct", 0) > s_.get("pct", 0) + 1.0 or t_.get("got", 0) < 0.9 * secs:
            verdict.append("El micro «{0}» solo falla cuando las cámaras trabajan a la vez ({1:.0f}% en "
                           "silencio): el sistema no da abasto abriendo todo a la vez.".format(a[0], t_.get("pct", 0)))
    L.append("")
    L.append("CONCLUSIÓN")
    if verdict:
        L.extend("  • " + v for v in verdict)
    else:
        L.append("  • Nada anómalo en los dispositivos: el problema estaría en el proceso de mezcla/codificación.")
    return "\n".join(L)


# ---- Cámara del móvil, por el navegador (sin instalar nada): el móvil abre una página HTTPS
# que sirve el propio cam-ui, da permiso de cámara y manda fotogramas sueltos (JPEG, uno tras
# otro -- no un vídeo contenedor como WebM: una foto suelta que se pierde no rompe nada, con
# una wifi real de por medio es mucho más robusto que depender de un flujo continuo sin huecos).
# cam-ui los vuelve a publicar en un segundo servidor, este SOLO en 127.0.0.1 (invisible desde
# la wifi: nadie más que el propio ffmpeg de este equipo puede pedirlo), como si fuera una
# cámara de red MJPEG de toda la vida -- el resto del programa (mosaico, grabación, el aviso
# «Sin señal» si se cae…) no sabe ni le importa que el origen sea un móvil y no una app como
# IP Webcam: es literalmente el mismo camino de siempre (ver _is_net_spec/_net_video_input).
#
# El servidor que SÍ mira hacia la wifi (el que sirve la página y recibe las fotos) va por
# HTTPS con un certificado autofirmado -- no es capricho: los navegadores no dejan usar
# getUserMedia() (la cámara) desde una página servida por http:// normal salvo que sea
# "localhost", así que no hay otra forma de que esto funcione desde el móvil. La primera vez
# el navegador avisará de que la conexión "no es privada" (un clic en Avanzado -> Continuar),
# pero solo esa vez por aparato mientras el certificado no cambie -- por eso se genera una sola
# vez y se reutiliza, en vez de uno nuevo cada arranque. Además hace falta saber la dirección
# exacta (con un token largo al final) para poder entrar: no es un sitio abierto a cualquiera
# en la wifi que se la encuentre por casualidad, porque aquí sí que añadiría una cámara de
# verdad al mosaico (a diferencia de «En la red», que solo deja ver).
PHONECAM_PORT_DEFAULT = 8555
# 8fps/720p/calidad 0.75 se quedaba corto para verlo fluido -- la propia página ya se autolimita
# sola (sendFrame() no manda el siguiente fotograma hasta que el anterior termina de subir, ver
# la variable "sending"), así que pedir más aquí no puede desbordar nada: en una wifi floja o un
# móvil lento, se queda corto de este número solo, igual que antes; en una buena, ahora sí llega
# más cerca de fluido y con más detalle.
PHONECAM_FPS = 20
PHONECAM_JPEG_QUALITY = 0.82
PHONECAM_JPEG_MAX = 6 * 1024 * 1024    # tope de tamaño por fotograma recibido, contra abusos
PHONECAM_STALE_SECONDS = 8             # sin fotogramas nuevos este tiempo: la cámara se da por caída
PHONECAM_SESSION_MAX_AGE = 120         # sesiones así de caducadas se olvidan del todo (no acumular)
PHONECAM_PAIR_TIMEOUT = 90             # sin escanear este QR de emparejamiento en este tiempo, se retira solo
# Dispositivos ya emparejados antes (ver PhoneCamHub.add_device/known_device y PhoneCamDevicesDialog):
# un móvil que ya haya pasado una vez por el QR de «/add» guarda un secreto propio (distinto del
# token de la sesión, que cambia cada vez que se enciende «Cámara del móvil») en su propio
# localStorage; la próxima vez que abra «/add», lo manda solo y entra directo, sin que nadie
# tenga que mirar la pantalla del PC otra vez. Se guarda aparte del resto de la configuración
# (no en config.json) para poder "olvidar" uno sin tocar nada más.
PHONECAM_DEVICES_FILE = os.path.join(HERE, "phonecam_devices.json")
PHONECAM_CERT_FILE = os.path.join(HERE, "phonecam_cert.pem")
PHONECAM_KEY_FILE = os.path.join(HERE, "phonecam_key.pem")
# CA local (ver _phonecam_ensure_ca): firma el certificado de arriba en vez de que se firme a sí
# mismo -- eso es lo único que permite que un iPhone/iPad llegue a confiar en él de verdad (ver
# el comentario junto a PhoneCamCertDialog). Se genera una sola vez y, a diferencia del
# certificado de arriba, nunca se regenera sola (si cambiara, cualquiera que ya la hubiera
# instalado en un móvil tendría que repetirlo -- por eso se guarda aparte y para siempre).
PHONECAM_CA_CERT_FILE = os.path.join(HERE, "phonecam_ca_cert.pem")
PHONECAM_CA_KEY_FILE = os.path.join(HERE, "phonecam_ca_key.pem")
PHONECAM_CA_DAYS = 3650        # la CA no tiene límite de validez según Apple -- solo el de abajo
PHONECAM_LEAF_DAYS = 800       # <= 825 días: lo que exige iOS 13+/macOS 10.15+ en un cert de servidor
                                # (soporte.apple.com/103769), o Safari deja de confiar en él sin más


def _phonecam_cert_covers(cert_path, ips):
    """True si el certificado ya guardado cubre (en su SAN) todas las IPs dadas -- si el
    equipo cambió de red/IP desde que se generó, hay que rehacerlo o el navegador se queja de
    un fallo distinto (nombre no coincide) en vez del aviso normal de "no privada"."""
    try:
        from cryptography import x509
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = set(str(ip) for ip in san.get_values_for_type(x509.IPAddress))
        return all(ip in have for ip in ips)
    except Exception:
        return False


def _phonecam_cert_fresh(cert_path, days_left=60):
    """True si al certificado ya guardado todavía le quedan más de «days_left» días de vida --
    con PHONECAM_LEAF_DAYS (800) de margen no debería hacer falta casi nunca en la práctica, pero
    sin esto un cam-ui que llevara meses sin reiniciarse (o sin cambiar de IP) se quedaría con un
    certificado caducado para siempre, y con él vuelve justo el mismo problema que esto arregla."""
    try:
        from cryptography import x509
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        try:
            exp = cert.not_valid_after_utc                 # cryptography >= 42
        except AttributeError:
            exp = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)  # versiones anteriores
        now = datetime.datetime.now(datetime.timezone.utc)
        return (exp - now) > datetime.timedelta(days=days_left)
    except Exception:
        return False


def _phonecam_ensure_ca():
    """La CA local de cam-ui (ver el comentario junto a PHONECAM_CA_CERT_FILE): se reutiliza
    para siempre si ya existe y se puede leer -- solo se genera una nueva si falta o está
    dañada (entonces, quien ya la hubiera instalado en un iPhone/iPad tendría que reinstalarla,
    pero es la única opción razonable frente a dejar «Cámara del móvil» rota del todo)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    if os.path.isfile(PHONECAM_CA_CERT_FILE) and os.path.isfile(PHONECAM_CA_KEY_FILE):
        try:
            with open(PHONECAM_CA_KEY_FILE, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=None)
            with open(PHONECAM_CA_CERT_FILE, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())
            return cert, key
        except Exception as e:
            log.warning("CA de «Cámara del móvil» dañada o ilegible, se genera una nueva (%r) "
                       "-- quien ya la hubiera instalado en un iPhone/iPad tendrá que repetirlo", e)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cam-ui (red local)"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "cam-ui")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=PHONECAM_CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                          critical=False)
            .sign(key, hashes.SHA256()))
    with open(PHONECAM_CA_KEY_FILE, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(PHONECAM_CA_CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def _phonecam_ensure_cert():
    """Certificado para la página de la cámara del móvil (hace falta para poder usar
    getUserMedia() -- ver el comentario de más arriba), firmado por la CA local de cam-ui (ver
    _phonecam_ensure_ca) en vez de autofirmado -- eso es lo que permite instalar esa CA una vez
    en un iPhone/iPad (ver PhoneCamCertDialog) y que Safari confíe en esto de verdad, no solo
    "lo acepto esta vez". Se guarda junto a camui.py y se reutiliza mientras siga cubriendo la
    IP actual del equipo y no esté a punto de caducar, para no hacer saltar el aviso de
    "conexión no privada" del navegador (en Android/Chrome, que no necesita la CA instalada)
    más veces de las necesarias."""
    import ipaddress
    ips = [ip for ip in _local_ips() if ip]
    if (os.path.isfile(PHONECAM_CERT_FILE) and os.path.isfile(PHONECAM_KEY_FILE) and
            _phonecam_cert_covers(PHONECAM_CERT_FILE, ips) and
            _phonecam_cert_fresh(PHONECAM_CERT_FILE)):
        return PHONECAM_CERT_FILE, PHONECAM_KEY_FILE
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    ca_cert, ca_key = _phonecam_ensure_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cam-ui (red local)")])
    alt_names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in ips:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=PHONECAM_LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                          critical=False)
            .sign(ca_key, hashes.SHA256()))
    with open(PHONECAM_KEY_FILE, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(PHONECAM_CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return PHONECAM_CERT_FILE, PHONECAM_KEY_FILE


# jsQR (https://github.com/cozmo/jsQR), de Cozmo Space / colaboradores, licencia Apache-2.0
# (texto completo en LICENSE-jsQR, junto a este archivo): se usa para leer el QR de emparejamiento con
# la propia cámara del móvil en la página «/add» (ver mas abajo), minificado con terser a
# partir del paquete «jsqr» de npm -- va incluido en el propio .py (nada de CDN ni internet)
# para que funcione igual en una wifi sin salida a internet, como el resto de cam-ui.
_JSQR_JS = """var o,e;o="undefined"!=typeof self?self:this,e=function(){return function(o){var e={};function r(t){if(e[t])return e[t].exports;var c=e[t]={i:t,l:!1,exports:{}};return o[t].call(c.exports,c,c.exports,r),c.l=!0,c.exports}return r.m=o,r.c=e,r.d=function(o,e,t){r.o(o,e)||Object.defineProperty(o,e,{configurable:!1,enumerable:!0,get:t})},r.n=function(o){var e=o&&o.__esModule?function(){return o.default}:function(){return o};return r.d(e,"a",e),e},r.o=function(o,e){return Object.prototype.hasOwnProperty.call(o,e)},r.p="",r(r.s=3)}([function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=function(){function o(o,e){this.width=e,this.height=o.length/e,this.data=o}return o.createEmpty=function(e,r){return new o(new Uint8ClampedArray(e*r),e)},o.prototype.get=function(o,e){return!(o<0||o>=this.width||e<0||e>=this.height||!this.data[e*this.width+o])},o.prototype.set=function(o,e,r){this.data[e*this.width+o]=r?1:0},o.prototype.setRegion=function(o,e,r,t,c){for(var s=e;s<e+t;s++)for(var a=o;a<o+r;a++)this.set(a,s,!!c)},o}();e.BitMatrix=t},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(2);e.addOrSubtractGF=function(o,e){return o^e};var c=function(){function o(o,e,r){this.primitive=o,this.size=e,this.generatorBase=r,this.expTable=new Array(this.size),this.logTable=new Array(this.size);for(var c=1,s=0;s<this.size;s++)this.expTable[s]=c,(c*=2)>=this.size&&(c=(c^this.primitive)&this.size-1);for(s=0;s<this.size-1;s++)this.logTable[this.expTable[s]]=s;this.zero=new t.default(this,Uint8ClampedArray.from([0])),this.one=new t.default(this,Uint8ClampedArray.from([1]))}return o.prototype.multiply=function(o,e){return 0===o||0===e?0:this.expTable[(this.logTable[o]+this.logTable[e])%(this.size-1)]},o.prototype.inverse=function(o){if(0===o)throw new Error("Can't invert 0");return this.expTable[this.size-this.logTable[o]-1]},o.prototype.buildMonomial=function(o,e){if(o<0)throw new Error("Invalid monomial degree less than 0");if(0===e)return this.zero;var r=new Uint8ClampedArray(o+1);return r[0]=e,new t.default(this,r)},o.prototype.log=function(o){if(0===o)throw new Error("Can't take log(0)");return this.logTable[o]},o.prototype.exp=function(o){return this.expTable[o]},o}();e.default=c},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(1),c=function(){function o(o,e){if(0===e.length)throw new Error("No coefficients.");this.field=o;var r=e.length;if(r>1&&0===e[0]){for(var t=1;t<r&&0===e[t];)t++;if(t===r)this.coefficients=o.zero.coefficients;else{this.coefficients=new Uint8ClampedArray(r-t);for(var c=0;c<this.coefficients.length;c++)this.coefficients[c]=e[t+c]}}else this.coefficients=e}return o.prototype.degree=function(){return this.coefficients.length-1},o.prototype.isZero=function(){return 0===this.coefficients[0]},o.prototype.getCoefficient=function(o){return this.coefficients[this.coefficients.length-1-o]},o.prototype.addOrSubtract=function(e){var r;if(this.isZero())return e;if(e.isZero())return this;var c=this.coefficients,s=e.coefficients;c.length>s.length&&(c=(r=[s,c])[0],s=r[1]);for(var a=new Uint8ClampedArray(s.length),d=s.length-c.length,n=0;n<d;n++)a[n]=s[n];for(n=d;n<s.length;n++)a[n]=t.addOrSubtractGF(c[n-d],s[n]);return new o(this.field,a)},o.prototype.multiply=function(e){if(0===e)return this.field.zero;if(1===e)return this;for(var r=this.coefficients.length,t=new Uint8ClampedArray(r),c=0;c<r;c++)t[c]=this.field.multiply(this.coefficients[c],e);return new o(this.field,t)},o.prototype.multiplyPoly=function(e){if(this.isZero()||e.isZero())return this.field.zero;for(var r=this.coefficients,c=r.length,s=e.coefficients,a=s.length,d=new Uint8ClampedArray(c+a-1),n=0;n<c;n++)for(var l=r[n],i=0;i<a;i++)d[n+i]=t.addOrSubtractGF(d[n+i],this.field.multiply(l,s[i]));return new o(this.field,d)},o.prototype.multiplyByMonomial=function(e,r){if(e<0)throw new Error("Invalid degree less than 0");if(0===r)return this.field.zero;for(var t=this.coefficients.length,c=new Uint8ClampedArray(t+e),s=0;s<t;s++)c[s]=this.field.multiply(this.coefficients[s],r);return new o(this.field,c)},o.prototype.evaluateAt=function(o){var e=0;if(0===o)return this.getCoefficient(0);var r=this.coefficients.length;if(1===o)return this.coefficients.forEach(function(o){e=t.addOrSubtractGF(e,o)}),e;e=this.coefficients[0];for(var c=1;c<r;c++)e=t.addOrSubtractGF(this.field.multiply(o,e),this.coefficients[c]);return e},o}();e.default=c},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(4),c=r(5),s=r(11),a=r(12);function d(o){var e=a.locate(o);if(!e)return null;for(var r=0,t=e;r<t.length;r++){var d=t[r],n=s.extract(o,d),l=c.decode(n.matrix);if(l)return{binaryData:l.bytes,data:l.text,chunks:l.chunks,version:l.version,location:{topRightCorner:n.mappingFunction(d.dimension,0),topLeftCorner:n.mappingFunction(0,0),bottomRightCorner:n.mappingFunction(d.dimension,d.dimension),bottomLeftCorner:n.mappingFunction(0,d.dimension),topRightFinderPattern:d.topRight,topLeftFinderPattern:d.topLeft,bottomLeftFinderPattern:d.bottomLeft,bottomRightAlignmentPattern:d.alignmentPattern}}}return null}var n={inversionAttempts:"attemptBoth"};function l(o,e,r,c){void 0===c&&(c={});var s=n;Object.keys(s||{}).forEach(function(o){s[o]=c[o]||s[o]});var a="attemptBoth"===s.inversionAttempts||"invertFirst"===s.inversionAttempts,l="onlyInvert"===s.inversionAttempts||"invertFirst"===s.inversionAttempts,i=t.binarize(o,e,r,a),B=i.binarized,k=i.inverted,u=d(l?k:B);return u||"attemptBoth"!==s.inversionAttempts&&"invertFirst"!==s.inversionAttempts||(u=d(l?B:k)),u}l.default=l,e.default=l},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(0);function c(o,e,r){return o<e?e:o>r?r:o}var s=function(){function o(o,e){this.width=o,this.data=new Uint8ClampedArray(o*e)}return o.prototype.get=function(o,e){return this.data[e*this.width+o]},o.prototype.set=function(o,e,r){this.data[e*this.width+o]=r},o}();e.binarize=function(o,e,r,a){if(o.length!==e*r*4)throw new Error("Malformed data passed to binarizer.");for(var d=new s(e,r),n=0;n<e;n++)for(var l=0;l<r;l++){var i=o[4*(l*e+n)+0],B=o[4*(l*e+n)+1],k=o[4*(l*e+n)+2];d.set(n,l,.2126*i+.7152*B+.0722*k)}for(var u=Math.ceil(e/8),C=Math.ceil(r/8),m=new s(u,C),f=0;f<C;f++)for(var w=0;w<u;w++){var P=0,v=1/0,h=0;for(l=0;l<8;l++)for(n=0;n<8;n++){var y=d.get(8*w+n,8*f+l);P+=y,v=Math.min(v,y),h=Math.max(h,y)}var p=P/Math.pow(8,2);if(h-v<=24&&(p=v/2,f>0&&w>0)){var b=(m.get(w,f-1)+2*m.get(w-1,f)+m.get(w-1,f-1))/4;v<b&&(p=b)}m.set(w,f,p)}var g=t.BitMatrix.createEmpty(e,r),x=null;for(a&&(x=t.BitMatrix.createEmpty(e,r)),f=0;f<C;f++)for(w=0;w<u;w++){for(var M=c(w,2,u-3),L=c(f,2,C-3),N=(P=0,-2);N<=2;N++)for(var I=-2;I<=2;I++)P+=m.get(M+N,L+I);var O=P/25;for(N=0;N<8;N++)for(I=0;I<8;I++){n=8*w+N,l=8*f+I;var z=d.get(n,l);g.set(n,l,z<=O),a&&x.set(n,l,!(z<=O))}}return a?{binarized:g,inverted:x}:{binarized:g}}},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(0),c=r(6),s=r(9),a=r(10);function d(o,e){for(var r=o^e,t=0;r;)t++,r&=r-1;return t}function n(o,e){return e<<1|o}var l=[{bits:21522,formatInfo:{errorCorrectionLevel:1,dataMask:0}},{bits:20773,formatInfo:{errorCorrectionLevel:1,dataMask:1}},{bits:24188,formatInfo:{errorCorrectionLevel:1,dataMask:2}},{bits:23371,formatInfo:{errorCorrectionLevel:1,dataMask:3}},{bits:17913,formatInfo:{errorCorrectionLevel:1,dataMask:4}},{bits:16590,formatInfo:{errorCorrectionLevel:1,dataMask:5}},{bits:20375,formatInfo:{errorCorrectionLevel:1,dataMask:6}},{bits:19104,formatInfo:{errorCorrectionLevel:1,dataMask:7}},{bits:30660,formatInfo:{errorCorrectionLevel:0,dataMask:0}},{bits:29427,formatInfo:{errorCorrectionLevel:0,dataMask:1}},{bits:32170,formatInfo:{errorCorrectionLevel:0,dataMask:2}},{bits:30877,formatInfo:{errorCorrectionLevel:0,dataMask:3}},{bits:26159,formatInfo:{errorCorrectionLevel:0,dataMask:4}},{bits:25368,formatInfo:{errorCorrectionLevel:0,dataMask:5}},{bits:27713,formatInfo:{errorCorrectionLevel:0,dataMask:6}},{bits:26998,formatInfo:{errorCorrectionLevel:0,dataMask:7}},{bits:5769,formatInfo:{errorCorrectionLevel:3,dataMask:0}},{bits:5054,formatInfo:{errorCorrectionLevel:3,dataMask:1}},{bits:7399,formatInfo:{errorCorrectionLevel:3,dataMask:2}},{bits:6608,formatInfo:{errorCorrectionLevel:3,dataMask:3}},{bits:1890,formatInfo:{errorCorrectionLevel:3,dataMask:4}},{bits:597,formatInfo:{errorCorrectionLevel:3,dataMask:5}},{bits:3340,formatInfo:{errorCorrectionLevel:3,dataMask:6}},{bits:2107,formatInfo:{errorCorrectionLevel:3,dataMask:7}},{bits:13663,formatInfo:{errorCorrectionLevel:2,dataMask:0}},{bits:12392,formatInfo:{errorCorrectionLevel:2,dataMask:1}},{bits:16177,formatInfo:{errorCorrectionLevel:2,dataMask:2}},{bits:14854,formatInfo:{errorCorrectionLevel:2,dataMask:3}},{bits:9396,formatInfo:{errorCorrectionLevel:2,dataMask:4}},{bits:8579,formatInfo:{errorCorrectionLevel:2,dataMask:5}},{bits:11994,formatInfo:{errorCorrectionLevel:2,dataMask:6}},{bits:11245,formatInfo:{errorCorrectionLevel:2,dataMask:7}}],i=[function(o){return(o.y+o.x)%2==0},function(o){return o.y%2==0},function(o){return o.x%3==0},function(o){return(o.y+o.x)%3==0},function(o){return(Math.floor(o.y/2)+Math.floor(o.x/3))%2==0},function(o){return o.x*o.y%2+o.x*o.y%3==0},function(o){return(o.y*o.x%2+o.y*o.x%3)%2==0},function(o){return((o.y+o.x)%2+o.y*o.x%3)%2==0}];function B(o){var e=function(o){var e=o.height,r=Math.floor((e-17)/4);if(r<=6)return a.VERSIONS[r-1];for(var t=0,c=5;c>=0;c--)for(var s=e-9;s>=e-11;s--)t=n(o.get(s,c),t);var l=0;for(s=5;s>=0;s--)for(c=e-9;c>=e-11;c--)l=n(o.get(s,c),l);for(var i,B=1/0,k=0,u=a.VERSIONS;k<u.length;k++){var C=u[k];if(C.infoBits===t||C.infoBits===l)return C;var m=d(t,C.infoBits);m<B&&(i=C,B=m),(m=d(l,C.infoBits))<B&&(i=C,B=m)}return B<=3?i:void 0}(o);if(!e)return null;var r=function(o){for(var e=0,r=0;r<=8;r++)6!==r&&(e=n(o.get(r,8),e));for(var t=7;t>=0;t--)6!==t&&(e=n(o.get(8,t),e));var c=o.height,s=0;for(t=c-1;t>=c-7;t--)s=n(o.get(8,t),s);for(r=c-8;r<c;r++)s=n(o.get(r,8),s);for(var a=1/0,i=null,B=0,k=l;B<k.length;B++){var u=k[B],C=u.bits,m=u.formatInfo;if(C===e||C===s)return m;var f=d(e,C);f<a&&(i=m,a=f),e!==s&&(f=d(s,C))<a&&(i=m,a=f)}return a<=3?i:null}(o);if(!r)return null;var B=function(o,e,r){var t=e.errorCorrectionLevels[r],c=[],s=0;if(t.ecBlocks.forEach(function(o){for(var e=0;e<o.numBlocks;e++)c.push({numDataCodewords:o.dataCodewordsPerBlock,codewords:[]}),s+=o.dataCodewordsPerBlock+t.ecCodewordsPerBlock}),o.length<s)return null;o=o.slice(0,s);for(var a=t.ecBlocks[0].dataCodewordsPerBlock,d=0;d<a;d++)for(var n=0,l=c;n<l.length;n++)l[n].codewords.push(o.shift());if(t.ecBlocks.length>1){var i=t.ecBlocks[0].numBlocks,B=t.ecBlocks[1].numBlocks;for(d=0;d<B;d++)c[i+d].codewords.push(o.shift())}for(;o.length>0;)for(var k=0,u=c;k<u.length;k++)u[k].codewords.push(o.shift());return c}(function(o,e,r){for(var c=i[r.dataMask],s=o.height,a=function(o){var e=17+4*o.versionNumber,r=t.BitMatrix.createEmpty(e,e);r.setRegion(0,0,9,9,!0),r.setRegion(e-8,0,8,9,!0),r.setRegion(0,e-8,9,8,!0);for(var c=0,s=o.alignmentPatternCenters;c<s.length;c++)for(var a=s[c],d=0,n=o.alignmentPatternCenters;d<n.length;d++){var l=n[d];6===a&&6===l||6===a&&l===e-7||a===e-7&&6===l||r.setRegion(a-2,l-2,5,5,!0)}return r.setRegion(6,9,1,e-17,!0),r.setRegion(9,6,e-17,1,!0),o.versionNumber>6&&(r.setRegion(e-11,0,3,6,!0),r.setRegion(0,e-11,6,3,!0)),r}(e),d=[],l=0,B=0,k=!0,u=s-1;u>0;u-=2){6===u&&u--;for(var C=0;C<s;C++)for(var m=k?s-1-C:C,f=0;f<2;f++){var w=u-f;if(!a.get(w,m)){B++;var P=o.get(w,m);c({y:m,x:w})&&(P=!P),l=n(P,l),8===B&&(d.push(l),B=0,l=0)}}k=!k}return d}(o,e,r),e,r.errorCorrectionLevel);if(!B)return null;for(var k=B.reduce(function(o,e){return o+e.numDataCodewords},0),u=new Uint8ClampedArray(k),C=0,m=0,f=B;m<f.length;m++){var w=f[m],P=s.decode(w.codewords,w.codewords.length-w.numDataCodewords);if(!P)return null;for(var v=0;v<w.numDataCodewords;v++)u[C++]=P[v]}try{return c.decode(u,e.versionNumber)}catch(o){return null}}e.decode=function(o){if(null==o)return null;var e=B(o);if(e)return e;for(var r=0;r<o.width;r++)for(var t=r+1;t<o.height;t++)o.get(r,t)!==o.get(t,r)&&(o.set(r,t,!o.get(r,t)),o.set(t,r,!o.get(t,r)));return B(o)}},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t,c,s=r(7),a=r(8);function d(o,e){for(var r=[],t="",c=[10,12,14][e],s=o.readBits(c);s>=3;){if((l=o.readBits(10))>=1e3)throw new Error("Invalid numeric value above 999");var a=Math.floor(l/100),d=Math.floor(l/10)%10,n=l%10;r.push(48+a,48+d,48+n),t+=a.toString()+d.toString()+n.toString(),s-=3}if(2===s){if((l=o.readBits(7))>=100)throw new Error("Invalid numeric value above 99");a=Math.floor(l/10),d=l%10,r.push(48+a,48+d),t+=a.toString()+d.toString()}else if(1===s){var l;if((l=o.readBits(4))>=10)throw new Error("Invalid numeric value above 9");r.push(48+l),t+=l.toString()}return{bytes:r,text:t}}!function(o){o.Numeric="numeric",o.Alphanumeric="alphanumeric",o.Byte="byte",o.Kanji="kanji",o.ECI="eci"}(t=e.Mode||(e.Mode={})),function(o){o[o.Terminator=0]="Terminator",o[o.Numeric=1]="Numeric",o[o.Alphanumeric=2]="Alphanumeric",o[o.Byte=4]="Byte",o[o.Kanji=8]="Kanji",o[o.ECI=7]="ECI"}(c||(c={}));var n=["0","1","2","3","4","5","6","7","8","9","A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z"," ","$","%","*","+","-",".","/",":"];function l(o,e){for(var r=[],t="",c=[9,11,13][e],s=o.readBits(c);s>=2;){var a=o.readBits(11),d=Math.floor(a/45),l=a%45;r.push(n[d].charCodeAt(0),n[l].charCodeAt(0)),t+=n[d]+n[l],s-=2}return 1===s&&(d=o.readBits(6),r.push(n[d].charCodeAt(0)),t+=n[d]),{bytes:r,text:t}}function i(o,e){for(var r=[],t="",c=[8,16,16][e],s=o.readBits(c),a=0;a<s;a++){var d=o.readBits(8);r.push(d)}try{t+=decodeURIComponent(r.map(function(o){return"%"+("0"+o.toString(16)).substr(-2)}).join(""))}catch(o){}return{bytes:r,text:t}}function B(o,e){for(var r=[],t="",c=[8,10,12][e],s=o.readBits(c),d=0;d<s;d++){var n=o.readBits(13),l=Math.floor(n/192)<<8|n%192;l+=l<7936?33088:49472,r.push(l>>8,255&l),t+=String.fromCharCode(a.shiftJISTable[l])}return{bytes:r,text:t}}e.decode=function(o,e){for(var r,a,n,k,u=new s.BitStream(o),C=e<=9?0:e<=26?1:2,m={text:"",bytes:[],chunks:[],version:e};u.available()>=4;){var f=u.readBits(4);if(f===c.Terminator)return m;if(f===c.ECI)0===u.readBits(1)?m.chunks.push({type:t.ECI,assignmentNumber:u.readBits(7)}):0===u.readBits(1)?m.chunks.push({type:t.ECI,assignmentNumber:u.readBits(14)}):0===u.readBits(1)?m.chunks.push({type:t.ECI,assignmentNumber:u.readBits(21)}):m.chunks.push({type:t.ECI,assignmentNumber:-1});else if(f===c.Numeric){var w=d(u,C);m.text+=w.text,(r=m.bytes).push.apply(r,w.bytes),m.chunks.push({type:t.Numeric,text:w.text})}else if(f===c.Alphanumeric){var P=l(u,C);m.text+=P.text,(a=m.bytes).push.apply(a,P.bytes),m.chunks.push({type:t.Alphanumeric,text:P.text})}else if(f===c.Byte){var v=i(u,C);m.text+=v.text,(n=m.bytes).push.apply(n,v.bytes),m.chunks.push({type:t.Byte,bytes:v.bytes,text:v.text})}else if(f===c.Kanji){var h=B(u,C);m.text+=h.text,(k=m.bytes).push.apply(k,h.bytes),m.chunks.push({type:t.Kanji,bytes:h.bytes,text:h.text})}}if(0===u.available()||0===u.readBits(u.available()))return m}},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=function(){function o(o){this.byteOffset=0,this.bitOffset=0,this.bytes=o}return o.prototype.readBits=function(o){if(o<1||o>32||o>this.available())throw new Error("Cannot read "+o.toString()+" bits");var e=0;if(this.bitOffset>0){var r=8-this.bitOffset,t=o<r?o:r,c=255>>8-t<<(s=r-t);e=(this.bytes[this.byteOffset]&c)>>s,o-=t,this.bitOffset+=t,8===this.bitOffset&&(this.bitOffset=0,this.byteOffset++)}if(o>0){for(;o>=8;)e=e<<8|255&this.bytes[this.byteOffset],this.byteOffset++,o-=8;var s;o>0&&(c=255>>(s=8-o)<<s,e=e<<o|(this.bytes[this.byteOffset]&c)>>s,this.bitOffset+=o)}return e},o.prototype.available=function(){return 8*(this.bytes.length-this.byteOffset)-this.bitOffset},o}();e.BitStream=t},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0}),e.shiftJISTable={32:32,33:33,34:34,35:35,36:36,37:37,38:38,39:39,40:40,41:41,42:42,43:43,44:44,45:45,46:46,47:47,48:48,49:49,50:50,51:51,52:52,53:53,54:54,55:55,56:56,57:57,58:58,59:59,60:60,61:61,62:62,63:63,64:64,65:65,66:66,67:67,68:68,69:69,70:70,71:71,72:72,73:73,74:74,75:75,76:76,77:77,78:78,79:79,80:80,81:81,82:82,83:83,84:84,85:85,86:86,87:87,88:88,89:89,90:90,91:91,92:165,93:93,94:94,95:95,96:96,97:97,98:98,99:99,100:100,101:101,102:102,103:103,104:104,105:105,106:106,107:107,108:108,109:109,110:110,111:111,112:112,113:113,114:114,115:115,116:116,117:117,118:118,119:119,120:120,121:121,122:122,123:123,124:124,125:125,126:8254,33088:12288,33089:12289,33090:12290,33091:65292,33092:65294,33093:12539,33094:65306,33095:65307,33096:65311,33097:65281,33098:12443,33099:12444,33100:180,33101:65344,33102:168,33103:65342,33104:65507,33105:65343,33106:12541,33107:12542,33108:12445,33109:12446,33110:12291,33111:20189,33112:12293,33113:12294,33114:12295,33115:12540,33116:8213,33117:8208,33118:65295,33119:92,33120:12316,33121:8214,33122:65372,33123:8230,33124:8229,33125:8216,33126:8217,33127:8220,33128:8221,33129:65288,33130:65289,33131:12308,33132:12309,33133:65339,33134:65341,33135:65371,33136:65373,33137:12296,33138:12297,33139:12298,33140:12299,33141:12300,33142:12301,33143:12302,33144:12303,33145:12304,33146:12305,33147:65291,33148:8722,33149:177,33150:215,33152:247,33153:65309,33154:8800,33155:65308,33156:65310,33157:8806,33158:8807,33159:8734,33160:8756,33161:9794,33162:9792,33163:176,33164:8242,33165:8243,33166:8451,33167:65509,33168:65284,33169:162,33170:163,33171:65285,33172:65283,33173:65286,33174:65290,33175:65312,33176:167,33177:9734,33178:9733,33179:9675,33180:9679,33181:9678,33182:9671,33183:9670,33184:9633,33185:9632,33186:9651,33187:9650,33188:9661,33189:9660,33190:8251,33191:12306,33192:8594,33193:8592,33194:8593,33195:8595,33196:12307,33208:8712,33209:8715,33210:8838,33211:8839,33212:8834,33213:8835,33214:8746,33215:8745,33224:8743,33225:8744,33226:172,33227:8658,33228:8660,33229:8704,33230:8707,33242:8736,33243:8869,33244:8978,33245:8706,33246:8711,33247:8801,33248:8786,33249:8810,33250:8811,33251:8730,33252:8765,33253:8733,33254:8757,33255:8747,33256:8748,33264:8491,33265:8240,33266:9839,33267:9837,33268:9834,33269:8224,33270:8225,33271:182,33276:9711,33359:65296,33360:65297,33361:65298,33362:65299,33363:65300,33364:65301,33365:65302,33366:65303,33367:65304,33368:65305,33376:65313,33377:65314,33378:65315,33379:65316,33380:65317,33381:65318,33382:65319,33383:65320,33384:65321,33385:65322,33386:65323,33387:65324,33388:65325,33389:65326,33390:65327,33391:65328,33392:65329,33393:65330,33394:65331,33395:65332,33396:65333,33397:65334,33398:65335,33399:65336,33400:65337,33401:65338,33409:65345,33410:65346,33411:65347,33412:65348,33413:65349,33414:65350,33415:65351,33416:65352,33417:65353,33418:65354,33419:65355,33420:65356,33421:65357,33422:65358,33423:65359,33424:65360,33425:65361,33426:65362,33427:65363,33428:65364,33429:65365,33430:65366,33431:65367,33432:65368,33433:65369,33434:65370,33439:12353,33440:12354,33441:12355,33442:12356,33443:12357,33444:12358,33445:12359,33446:12360,33447:12361,33448:12362,33449:12363,33450:12364,33451:12365,33452:12366,33453:12367,33454:12368,33455:12369,33456:12370,33457:12371,33458:12372,33459:12373,33460:12374,33461:12375,33462:12376,33463:12377,33464:12378,33465:12379,33466:12380,33467:12381,33468:12382,33469:12383,33470:12384,33471:12385,33472:12386,33473:12387,33474:12388,33475:12389,33476:12390,33477:12391,33478:12392,33479:12393,33480:12394,33481:12395,33482:12396,33483:12397,33484:12398,33485:12399,33486:12400,33487:12401,33488:12402,33489:12403,33490:12404,33491:12405,33492:12406,33493:12407,33494:12408,33495:12409,33496:12410,33497:12411,33498:12412,33499:12413,33500:12414,33501:12415,33502:12416,33503:12417,33504:12418,33505:12419,33506:12420,33507:12421,33508:12422,33509:12423,33510:12424,33511:12425,33512:12426,33513:12427,33514:12428,33515:12429,33516:12430,33517:12431,33518:12432,33519:12433,33520:12434,33521:12435,33600:12449,33601:12450,33602:12451,33603:12452,33604:12453,33605:12454,33606:12455,33607:12456,33608:12457,33609:12458,33610:12459,33611:12460,33612:12461,33613:12462,33614:12463,33615:12464,33616:12465,33617:12466,33618:12467,33619:12468,33620:12469,33621:12470,33622:12471,33623:12472,33624:12473,33625:12474,33626:12475,33627:12476,33628:12477,33629:12478,33630:12479,33631:12480,33632:12481,33633:12482,33634:12483,33635:12484,33636:12485,33637:12486,33638:12487,33639:12488,33640:12489,33641:12490,33642:12491,33643:12492,33644:12493,33645:12494,33646:12495,33647:12496,33648:12497,33649:12498,33650:12499,33651:12500,33652:12501,33653:12502,33654:12503,33655:12504,33656:12505,33657:12506,33658:12507,33659:12508,33660:12509,33661:12510,33662:12511,33664:12512,33665:12513,33666:12514,33667:12515,33668:12516,33669:12517,33670:12518,33671:12519,33672:12520,33673:12521,33674:12522,33675:12523,33676:12524,33677:12525,33678:12526,33679:12527,33680:12528,33681:12529,33682:12530,33683:12531,33684:12532,33685:12533,33686:12534,33695:913,33696:914,33697:915,33698:916,33699:917,33700:918,33701:919,33702:920,33703:921,33704:922,33705:923,33706:924,33707:925,33708:926,33709:927,33710:928,33711:929,33712:931,33713:932,33714:933,33715:934,33716:935,33717:936,33718:937,33727:945,33728:946,33729:947,33730:948,33731:949,33732:950,33733:951,33734:952,33735:953,33736:954,33737:955,33738:956,33739:957,33740:958,33741:959,33742:960,33743:961,33744:963,33745:964,33746:965,33747:966,33748:967,33749:968,33750:969,33856:1040,33857:1041,33858:1042,33859:1043,33860:1044,33861:1045,33862:1025,33863:1046,33864:1047,33865:1048,33866:1049,33867:1050,33868:1051,33869:1052,33870:1053,33871:1054,33872:1055,33873:1056,33874:1057,33875:1058,33876:1059,33877:1060,33878:1061,33879:1062,33880:1063,33881:1064,33882:1065,33883:1066,33884:1067,33885:1068,33886:1069,33887:1070,33888:1071,33904:1072,33905:1073,33906:1074,33907:1075,33908:1076,33909:1077,33910:1105,33911:1078,33912:1079,33913:1080,33914:1081,33915:1082,33916:1083,33917:1084,33918:1085,33920:1086,33921:1087,33922:1088,33923:1089,33924:1090,33925:1091,33926:1092,33927:1093,33928:1094,33929:1095,33930:1096,33931:1097,33932:1098,33933:1099,33934:1100,33935:1101,33936:1102,33937:1103,33951:9472,33952:9474,33953:9484,33954:9488,33955:9496,33956:9492,33957:9500,33958:9516,33959:9508,33960:9524,33961:9532,33962:9473,33963:9475,33964:9487,33965:9491,33966:9499,33967:9495,33968:9507,33969:9523,33970:9515,33971:9531,33972:9547,33973:9504,33974:9519,33975:9512,33976:9527,33977:9535,33978:9501,33979:9520,33980:9509,33981:9528,33982:9538,34975:20124,34976:21782,34977:23043,34978:38463,34979:21696,34980:24859,34981:25384,34982:23030,34983:36898,34984:33909,34985:33564,34986:31312,34987:24746,34988:25569,34989:28197,34990:26093,34991:33894,34992:33446,34993:39925,34994:26771,34995:22311,34996:26017,34997:25201,34998:23451,34999:22992,35e3:34427,35001:39156,35002:32098,35003:32190,35004:39822,35005:25110,35006:31903,35007:34999,35008:23433,35009:24245,35010:25353,35011:26263,35012:26696,35013:38343,35014:38797,35015:26447,35016:20197,35017:20234,35018:20301,35019:20381,35020:20553,35021:22258,35022:22839,35023:22996,35024:23041,35025:23561,35026:24799,35027:24847,35028:24944,35029:26131,35030:26885,35031:28858,35032:30031,35033:30064,35034:31227,35035:32173,35036:32239,35037:32963,35038:33806,35039:34915,35040:35586,35041:36949,35042:36986,35043:21307,35044:20117,35045:20133,35046:22495,35047:32946,35048:37057,35049:30959,35050:19968,35051:22769,35052:28322,35053:36920,35054:31282,35055:33576,35056:33419,35057:39983,35058:20801,35059:21360,35060:21693,35061:21729,35062:22240,35063:23035,35064:24341,35065:39154,35066:28139,35067:32996,35068:34093,35136:38498,35137:38512,35138:38560,35139:38907,35140:21515,35141:21491,35142:23431,35143:28879,35144:32701,35145:36802,35146:38632,35147:21359,35148:40284,35149:31418,35150:19985,35151:30867,35152:33276,35153:28198,35154:22040,35155:21764,35156:27421,35157:34074,35158:39995,35159:23013,35160:21417,35161:28006,35162:29916,35163:38287,35164:22082,35165:20113,35166:36939,35167:38642,35168:33615,35169:39180,35170:21473,35171:21942,35172:23344,35173:24433,35174:26144,35175:26355,35176:26628,35177:27704,35178:27891,35179:27945,35180:29787,35181:30408,35182:31310,35183:38964,35184:33521,35185:34907,35186:35424,35187:37613,35188:28082,35189:30123,35190:30410,35191:39365,35192:24742,35193:35585,35194:36234,35195:38322,35196:27022,35197:21421,35198:20870,35200:22290,35201:22576,35202:22852,35203:23476,35204:24310,35205:24616,35206:25513,35207:25588,35208:27839,35209:28436,35210:28814,35211:28948,35212:29017,35213:29141,35214:29503,35215:32257,35216:33398,35217:33489,35218:34199,35219:36960,35220:37467,35221:40219,35222:22633,35223:26044,35224:27738,35225:29989,35226:20985,35227:22830,35228:22885,35229:24448,35230:24540,35231:25276,35232:26106,35233:27178,35234:27431,35235:27572,35236:29579,35237:32705,35238:35158,35239:40236,35240:40206,35241:40644,35242:23713,35243:27798,35244:33659,35245:20740,35246:23627,35247:25014,35248:33222,35249:26742,35250:29281,35251:20057,35252:20474,35253:21368,35254:24681,35255:28201,35256:31311,35257:38899,35258:19979,35259:21270,35260:20206,35261:20309,35262:20285,35263:20385,35264:20339,35265:21152,35266:21487,35267:22025,35268:22799,35269:23233,35270:23478,35271:23521,35272:31185,35273:26247,35274:26524,35275:26550,35276:27468,35277:27827,35278:28779,35279:29634,35280:31117,35281:31166,35282:31292,35283:31623,35284:33457,35285:33499,35286:33540,35287:33655,35288:33775,35289:33747,35290:34662,35291:35506,35292:22057,35293:36008,35294:36838,35295:36942,35296:38686,35297:34442,35298:20420,35299:23784,35300:25105,35301:29273,35302:30011,35303:33253,35304:33469,35305:34558,35306:36032,35307:38597,35308:39187,35309:39381,35310:20171,35311:20250,35312:35299,35313:22238,35314:22602,35315:22730,35316:24315,35317:24555,35318:24618,35319:24724,35320:24674,35321:25040,35322:25106,35323:25296,35324:25913,35392:39745,35393:26214,35394:26800,35395:28023,35396:28784,35397:30028,35398:30342,35399:32117,35400:33445,35401:34809,35402:38283,35403:38542,35404:35997,35405:20977,35406:21182,35407:22806,35408:21683,35409:23475,35410:23830,35411:24936,35412:27010,35413:28079,35414:30861,35415:33995,35416:34903,35417:35442,35418:37799,35419:39608,35420:28012,35421:39336,35422:34521,35423:22435,35424:26623,35425:34510,35426:37390,35427:21123,35428:22151,35429:21508,35430:24275,35431:25313,35432:25785,35433:26684,35434:26680,35435:27579,35436:29554,35437:30906,35438:31339,35439:35226,35440:35282,35441:36203,35442:36611,35443:37101,35444:38307,35445:38548,35446:38761,35447:23398,35448:23731,35449:27005,35450:38989,35451:38990,35452:25499,35453:31520,35454:27179,35456:27263,35457:26806,35458:39949,35459:28511,35460:21106,35461:21917,35462:24688,35463:25324,35464:27963,35465:28167,35466:28369,35467:33883,35468:35088,35469:36676,35470:19988,35471:39993,35472:21494,35473:26907,35474:27194,35475:38788,35476:26666,35477:20828,35478:31427,35479:33970,35480:37340,35481:37772,35482:22107,35483:40232,35484:26658,35485:33541,35486:33841,35487:31909,35488:21e3,35489:33477,35490:29926,35491:20094,35492:20355,35493:20896,35494:23506,35495:21002,35496:21208,35497:21223,35498:24059,35499:21914,35500:22570,35501:23014,35502:23436,35503:23448,35504:23515,35505:24178,35506:24185,35507:24739,35508:24863,35509:24931,35510:25022,35511:25563,35512:25954,35513:26577,35514:26707,35515:26874,35516:27454,35517:27475,35518:27735,35519:28450,35520:28567,35521:28485,35522:29872,35523:29976,35524:30435,35525:30475,35526:31487,35527:31649,35528:31777,35529:32233,35530:32566,35531:32752,35532:32925,35533:33382,35534:33694,35535:35251,35536:35532,35537:36011,35538:36996,35539:37969,35540:38291,35541:38289,35542:38306,35543:38501,35544:38867,35545:39208,35546:33304,35547:20024,35548:21547,35549:23736,35550:24012,35551:29609,35552:30284,35553:30524,35554:23721,35555:32747,35556:36107,35557:38593,35558:38929,35559:38996,35560:39e3,35561:20225,35562:20238,35563:21361,35564:21916,35565:22120,35566:22522,35567:22855,35568:23305,35569:23492,35570:23696,35571:24076,35572:24190,35573:24524,35574:25582,35575:26426,35576:26071,35577:26082,35578:26399,35579:26827,35580:26820,35648:27231,35649:24112,35650:27589,35651:27671,35652:27773,35653:30079,35654:31048,35655:23395,35656:31232,35657:32e3,35658:24509,35659:35215,35660:35352,35661:36020,35662:36215,35663:36556,35664:36637,35665:39138,35666:39438,35667:39740,35668:20096,35669:20605,35670:20736,35671:22931,35672:23452,35673:25135,35674:25216,35675:25836,35676:27450,35677:29344,35678:30097,35679:31047,35680:32681,35681:34811,35682:35516,35683:35696,35684:25516,35685:33738,35686:38816,35687:21513,35688:21507,35689:21931,35690:26708,35691:27224,35692:35440,35693:30759,35694:26485,35695:40653,35696:21364,35697:23458,35698:33050,35699:34384,35700:36870,35701:19992,35702:20037,35703:20167,35704:20241,35705:21450,35706:21560,35707:23470,35708:24339,35709:24613,35710:25937,35712:26429,35713:27714,35714:27762,35715:27875,35716:28792,35717:29699,35718:31350,35719:31406,35720:31496,35721:32026,35722:31998,35723:32102,35724:26087,35725:29275,35726:21435,35727:23621,35728:24040,35729:25298,35730:25312,35731:25369,35732:28192,35733:34394,35734:35377,35735:36317,35736:37624,35737:28417,35738:31142,35739:39770,35740:20136,35741:20139,35742:20140,35743:20379,35744:20384,35745:20689,35746:20807,35747:31478,35748:20849,35749:20982,35750:21332,35751:21281,35752:21375,35753:21483,35754:21932,35755:22659,35756:23777,35757:24375,35758:24394,35759:24623,35760:24656,35761:24685,35762:25375,35763:25945,35764:27211,35765:27841,35766:29378,35767:29421,35768:30703,35769:33016,35770:33029,35771:33288,35772:34126,35773:37111,35774:37857,35775:38911,35776:39255,35777:39514,35778:20208,35779:20957,35780:23597,35781:26241,35782:26989,35783:23616,35784:26354,35785:26997,35786:29577,35787:26704,35788:31873,35789:20677,35790:21220,35791:22343,35792:24062,35793:37670,35794:26020,35795:27427,35796:27453,35797:29748,35798:31105,35799:31165,35800:31563,35801:32202,35802:33465,35803:33740,35804:34943,35805:35167,35806:35641,35807:36817,35808:37329,35809:21535,35810:37504,35811:20061,35812:20534,35813:21477,35814:21306,35815:29399,35816:29590,35817:30697,35818:33510,35819:36527,35820:39366,35821:39368,35822:39378,35823:20855,35824:24858,35825:34398,35826:21936,35827:31354,35828:20598,35829:23507,35830:36935,35831:38533,35832:20018,35833:27355,35834:37351,35835:23633,35836:23624,35904:25496,35905:31391,35906:27795,35907:38772,35908:36705,35909:31402,35910:29066,35911:38536,35912:31874,35913:26647,35914:32368,35915:26705,35916:37740,35917:21234,35918:21531,35919:34219,35920:35347,35921:32676,35922:36557,35923:37089,35924:21350,35925:34952,35926:31041,35927:20418,35928:20670,35929:21009,35930:20804,35931:21843,35932:22317,35933:29674,35934:22411,35935:22865,35936:24418,35937:24452,35938:24693,35939:24950,35940:24935,35941:25001,35942:25522,35943:25658,35944:25964,35945:26223,35946:26690,35947:28179,35948:30054,35949:31293,35950:31995,35951:32076,35952:32153,35953:32331,35954:32619,35955:33550,35956:33610,35957:34509,35958:35336,35959:35427,35960:35686,35961:36605,35962:38938,35963:40335,35964:33464,35965:36814,35966:39912,35968:21127,35969:25119,35970:25731,35971:28608,35972:38553,35973:26689,35974:20625,35975:27424,35976:27770,35977:28500,35978:31348,35979:32080,35980:34880,35981:35363,35982:26376,35983:20214,35984:20537,35985:20518,35986:20581,35987:20860,35988:21048,35989:21091,35990:21927,35991:22287,35992:22533,35993:23244,35994:24314,35995:25010,35996:25080,35997:25331,35998:25458,35999:26908,36e3:27177,36001:29309,36002:29356,36003:29486,36004:30740,36005:30831,36006:32121,36007:30476,36008:32937,36009:35211,36010:35609,36011:36066,36012:36562,36013:36963,36014:37749,36015:38522,36016:38997,36017:39443,36018:40568,36019:20803,36020:21407,36021:21427,36022:24187,36023:24358,36024:28187,36025:28304,36026:29572,36027:29694,36028:32067,36029:33335,36030:35328,36031:35578,36032:38480,36033:20046,36034:20491,36035:21476,36036:21628,36037:22266,36038:22993,36039:23396,36040:24049,36041:24235,36042:24359,36043:25144,36044:25925,36045:26543,36046:28246,36047:29392,36048:31946,36049:34996,36050:32929,36051:32993,36052:33776,36053:34382,36054:35463,36055:36328,36056:37431,36057:38599,36058:39015,36059:40723,36060:20116,36061:20114,36062:20237,36063:21320,36064:21577,36065:21566,36066:23087,36067:24460,36068:24481,36069:24735,36070:26791,36071:27278,36072:29786,36073:30849,36074:35486,36075:35492,36076:35703,36077:37264,36078:20062,36079:39881,36080:20132,36081:20348,36082:20399,36083:20505,36084:20502,36085:20809,36086:20844,36087:21151,36088:21177,36089:21246,36090:21402,36091:21475,36092:21521,36160:21518,36161:21897,36162:22353,36163:22434,36164:22909,36165:23380,36166:23389,36167:23439,36168:24037,36169:24039,36170:24055,36171:24184,36172:24195,36173:24218,36174:24247,36175:24344,36176:24658,36177:24908,36178:25239,36179:25304,36180:25511,36181:25915,36182:26114,36183:26179,36184:26356,36185:26477,36186:26657,36187:26775,36188:27083,36189:27743,36190:27946,36191:28009,36192:28207,36193:28317,36194:30002,36195:30343,36196:30828,36197:31295,36198:31968,36199:32005,36200:32024,36201:32094,36202:32177,36203:32789,36204:32771,36205:32943,36206:32945,36207:33108,36208:33167,36209:33322,36210:33618,36211:34892,36212:34913,36213:35611,36214:36002,36215:36092,36216:37066,36217:37237,36218:37489,36219:30783,36220:37628,36221:38308,36222:38477,36224:38917,36225:39321,36226:39640,36227:40251,36228:21083,36229:21163,36230:21495,36231:21512,36232:22741,36233:25335,36234:28640,36235:35946,36236:36703,36237:40633,36238:20811,36239:21051,36240:21578,36241:22269,36242:31296,36243:37239,36244:40288,36245:40658,36246:29508,36247:28425,36248:33136,36249:29969,36250:24573,36251:24794,36252:39592,36253:29403,36254:36796,36255:27492,36256:38915,36257:20170,36258:22256,36259:22372,36260:22718,36261:23130,36262:24680,36263:25031,36264:26127,36265:26118,36266:26681,36267:26801,36268:28151,36269:30165,36270:32058,36271:33390,36272:39746,36273:20123,36274:20304,36275:21449,36276:21766,36277:23919,36278:24038,36279:24046,36280:26619,36281:27801,36282:29811,36283:30722,36284:35408,36285:37782,36286:35039,36287:22352,36288:24231,36289:25387,36290:20661,36291:20652,36292:20877,36293:26368,36294:21705,36295:22622,36296:22971,36297:23472,36298:24425,36299:25165,36300:25505,36301:26685,36302:27507,36303:28168,36304:28797,36305:37319,36306:29312,36307:30741,36308:30758,36309:31085,36310:25998,36311:32048,36312:33756,36313:35009,36314:36617,36315:38555,36316:21092,36317:22312,36318:26448,36319:32618,36320:36001,36321:20916,36322:22338,36323:38442,36324:22586,36325:27018,36326:32948,36327:21682,36328:23822,36329:22524,36330:30869,36331:40442,36332:20316,36333:21066,36334:21643,36335:25662,36336:26152,36337:26388,36338:26613,36339:31364,36340:31574,36341:32034,36342:37679,36343:26716,36344:39853,36345:31545,36346:21273,36347:20874,36348:21047,36416:23519,36417:25334,36418:25774,36419:25830,36420:26413,36421:27578,36422:34217,36423:38609,36424:30352,36425:39894,36426:25420,36427:37638,36428:39851,36429:30399,36430:26194,36431:19977,36432:20632,36433:21442,36434:23665,36435:24808,36436:25746,36437:25955,36438:26719,36439:29158,36440:29642,36441:29987,36442:31639,36443:32386,36444:34453,36445:35715,36446:36059,36447:37240,36448:39184,36449:26028,36450:26283,36451:27531,36452:20181,36453:20180,36454:20282,36455:20351,36456:21050,36457:21496,36458:21490,36459:21987,36460:22235,36461:22763,36462:22987,36463:22985,36464:23039,36465:23376,36466:23629,36467:24066,36468:24107,36469:24535,36470:24605,36471:25351,36472:25903,36473:23388,36474:26031,36475:26045,36476:26088,36477:26525,36478:27490,36480:27515,36481:27663,36482:29509,36483:31049,36484:31169,36485:31992,36486:32025,36487:32043,36488:32930,36489:33026,36490:33267,36491:35222,36492:35422,36493:35433,36494:35430,36495:35468,36496:35566,36497:36039,36498:36060,36499:38604,36500:39164,36501:27503,36502:20107,36503:20284,36504:20365,36505:20816,36506:23383,36507:23546,36508:24904,36509:25345,36510:26178,36511:27425,36512:28363,36513:27835,36514:29246,36515:29885,36516:30164,36517:30913,36518:31034,36519:32780,36520:32819,36521:33258,36522:33940,36523:36766,36524:27728,36525:40575,36526:24335,36527:35672,36528:40235,36529:31482,36530:36600,36531:23437,36532:38635,36533:19971,36534:21489,36535:22519,36536:22833,36537:23241,36538:23460,36539:24713,36540:28287,36541:28422,36542:30142,36543:36074,36544:23455,36545:34048,36546:31712,36547:20594,36548:26612,36549:33437,36550:23649,36551:34122,36552:32286,36553:33294,36554:20889,36555:23556,36556:25448,36557:36198,36558:26012,36559:29038,36560:31038,36561:32023,36562:32773,36563:35613,36564:36554,36565:36974,36566:34503,36567:37034,36568:20511,36569:21242,36570:23610,36571:26451,36572:28796,36573:29237,36574:37196,36575:37320,36576:37675,36577:33509,36578:23490,36579:24369,36580:24825,36581:20027,36582:21462,36583:23432,36584:25163,36585:26417,36586:27530,36587:29417,36588:29664,36589:31278,36590:33131,36591:36259,36592:37202,36593:39318,36594:20754,36595:21463,36596:21610,36597:23551,36598:25480,36599:27193,36600:32172,36601:38656,36602:22234,36603:21454,36604:21608,36672:23447,36673:23601,36674:24030,36675:20462,36676:24833,36677:25342,36678:27954,36679:31168,36680:31179,36681:32066,36682:32333,36683:32722,36684:33261,36685:33311,36686:33936,36687:34886,36688:35186,36689:35728,36690:36468,36691:36655,36692:36913,36693:37195,36694:37228,36695:38598,36696:37276,36697:20160,36698:20303,36699:20805,36700:21313,36701:24467,36702:25102,36703:26580,36704:27713,36705:28171,36706:29539,36707:32294,36708:37325,36709:37507,36710:21460,36711:22809,36712:23487,36713:28113,36714:31069,36715:32302,36716:31899,36717:22654,36718:29087,36719:20986,36720:34899,36721:36848,36722:20426,36723:23803,36724:26149,36725:30636,36726:31459,36727:33308,36728:39423,36729:20934,36730:24490,36731:26092,36732:26991,36733:27529,36734:28147,36736:28310,36737:28516,36738:30462,36739:32020,36740:24033,36741:36981,36742:37255,36743:38918,36744:20966,36745:21021,36746:25152,36747:26257,36748:26329,36749:28186,36750:24246,36751:32210,36752:32626,36753:26360,36754:34223,36755:34295,36756:35576,36757:21161,36758:21465,36759:22899,36760:24207,36761:24464,36762:24661,36763:37604,36764:38500,36765:20663,36766:20767,36767:21213,36768:21280,36769:21319,36770:21484,36771:21736,36772:21830,36773:21809,36774:22039,36775:22888,36776:22974,36777:23100,36778:23477,36779:23558,36780:23567,36781:23569,36782:23578,36783:24196,36784:24202,36785:24288,36786:24432,36787:25215,36788:25220,36789:25307,36790:25484,36791:25463,36792:26119,36793:26124,36794:26157,36795:26230,36796:26494,36797:26786,36798:27167,36799:27189,36800:27836,36801:28040,36802:28169,36803:28248,36804:28988,36805:28966,36806:29031,36807:30151,36808:30465,36809:30813,36810:30977,36811:31077,36812:31216,36813:31456,36814:31505,36815:31911,36816:32057,36817:32918,36818:33750,36819:33931,36820:34121,36821:34909,36822:35059,36823:35359,36824:35388,36825:35412,36826:35443,36827:35937,36828:36062,36829:37284,36830:37478,36831:37758,36832:37912,36833:38556,36834:38808,36835:19978,36836:19976,36837:19998,36838:20055,36839:20887,36840:21104,36841:22478,36842:22580,36843:22732,36844:23330,36845:24120,36846:24773,36847:25854,36848:26465,36849:26454,36850:27972,36851:29366,36852:30067,36853:31331,36854:33976,36855:35698,36856:37304,36857:37664,36858:22065,36859:22516,36860:39166,36928:25325,36929:26893,36930:27542,36931:29165,36932:32340,36933:32887,36934:33394,36935:35302,36936:39135,36937:34645,36938:36785,36939:23611,36940:20280,36941:20449,36942:20405,36943:21767,36944:23072,36945:23517,36946:23529,36947:24515,36948:24910,36949:25391,36950:26032,36951:26187,36952:26862,36953:27035,36954:28024,36955:28145,36956:30003,36957:30137,36958:30495,36959:31070,36960:31206,36961:32051,36962:33251,36963:33455,36964:34218,36965:35242,36966:35386,36967:36523,36968:36763,36969:36914,36970:37341,36971:38663,36972:20154,36973:20161,36974:20995,36975:22645,36976:22764,36977:23563,36978:29978,36979:23613,36980:33102,36981:35338,36982:36805,36983:38499,36984:38765,36985:31525,36986:35535,36987:38920,36988:37218,36989:22259,36990:21416,36992:36887,36993:21561,36994:22402,36995:24101,36996:25512,36997:27700,36998:28810,36999:30561,37e3:31883,37001:32736,37002:34928,37003:36930,37004:37204,37005:37648,37006:37656,37007:38543,37008:29790,37009:39620,37010:23815,37011:23913,37012:25968,37013:26530,37014:36264,37015:38619,37016:25454,37017:26441,37018:26905,37019:33733,37020:38935,37021:38592,37022:35070,37023:28548,37024:25722,37025:23544,37026:19990,37027:28716,37028:30045,37029:26159,37030:20932,37031:21046,37032:21218,37033:22995,37034:24449,37035:24615,37036:25104,37037:25919,37038:25972,37039:26143,37040:26228,37041:26866,37042:26646,37043:27491,37044:28165,37045:29298,37046:29983,37047:30427,37048:31934,37049:32854,37050:22768,37051:35069,37052:35199,37053:35488,37054:35475,37055:35531,37056:36893,37057:37266,37058:38738,37059:38745,37060:25993,37061:31246,37062:33030,37063:38587,37064:24109,37065:24796,37066:25114,37067:26021,37068:26132,37069:26512,37070:30707,37071:31309,37072:31821,37073:32318,37074:33034,37075:36012,37076:36196,37077:36321,37078:36447,37079:30889,37080:20999,37081:25305,37082:25509,37083:25666,37084:25240,37085:35373,37086:31363,37087:31680,37088:35500,37089:38634,37090:32118,37091:33292,37092:34633,37093:20185,37094:20808,37095:21315,37096:21344,37097:23459,37098:23554,37099:23574,37100:24029,37101:25126,37102:25159,37103:25776,37104:26643,37105:26676,37106:27849,37107:27973,37108:27927,37109:26579,37110:28508,37111:29006,37112:29053,37113:26059,37114:31359,37115:31661,37116:32218,37184:32330,37185:32680,37186:33146,37187:33307,37188:33337,37189:34214,37190:35438,37191:36046,37192:36341,37193:36984,37194:36983,37195:37549,37196:37521,37197:38275,37198:39854,37199:21069,37200:21892,37201:28472,37202:28982,37203:20840,37204:31109,37205:32341,37206:33203,37207:31950,37208:22092,37209:22609,37210:23720,37211:25514,37212:26366,37213:26365,37214:26970,37215:29401,37216:30095,37217:30094,37218:30990,37219:31062,37220:31199,37221:31895,37222:32032,37223:32068,37224:34311,37225:35380,37226:38459,37227:36961,37228:40736,37229:20711,37230:21109,37231:21452,37232:21474,37233:20489,37234:21930,37235:22766,37236:22863,37237:29245,37238:23435,37239:23652,37240:21277,37241:24803,37242:24819,37243:25436,37244:25475,37245:25407,37246:25531,37248:25805,37249:26089,37250:26361,37251:24035,37252:27085,37253:27133,37254:28437,37255:29157,37256:20105,37257:30185,37258:30456,37259:31379,37260:31967,37261:32207,37262:32156,37263:32865,37264:33609,37265:33624,37266:33900,37267:33980,37268:34299,37269:35013,37270:36208,37271:36865,37272:36973,37273:37783,37274:38684,37275:39442,37276:20687,37277:22679,37278:24974,37279:33235,37280:34101,37281:36104,37282:36896,37283:20419,37284:20596,37285:21063,37286:21363,37287:24687,37288:25417,37289:26463,37290:28204,37291:36275,37292:36895,37293:20439,37294:23646,37295:36042,37296:26063,37297:32154,37298:21330,37299:34966,37300:20854,37301:25539,37302:23384,37303:23403,37304:23562,37305:25613,37306:26449,37307:36956,37308:20182,37309:22810,37310:22826,37311:27760,37312:35409,37313:21822,37314:22549,37315:22949,37316:24816,37317:25171,37318:26561,37319:33333,37320:26965,37321:38464,37322:39364,37323:39464,37324:20307,37325:22534,37326:23550,37327:32784,37328:23729,37329:24111,37330:24453,37331:24608,37332:24907,37333:25140,37334:26367,37335:27888,37336:28382,37337:32974,37338:33151,37339:33492,37340:34955,37341:36024,37342:36864,37343:36910,37344:38538,37345:40667,37346:39899,37347:20195,37348:21488,37349:22823,37350:31532,37351:37261,37352:38988,37353:40441,37354:28381,37355:28711,37356:21331,37357:21828,37358:23429,37359:25176,37360:25246,37361:25299,37362:27810,37363:28655,37364:29730,37365:35351,37366:37944,37367:28609,37368:35582,37369:33592,37370:20967,37371:34552,37372:21482,37440:21481,37441:20294,37442:36948,37443:36784,37444:22890,37445:33073,37446:24061,37447:31466,37448:36799,37449:26842,37450:35895,37451:29432,37452:40008,37453:27197,37454:35504,37455:20025,37456:21336,37457:22022,37458:22374,37459:25285,37460:25506,37461:26086,37462:27470,37463:28129,37464:28251,37465:28845,37466:30701,37467:31471,37468:31658,37469:32187,37470:32829,37471:32966,37472:34507,37473:35477,37474:37723,37475:22243,37476:22727,37477:24382,37478:26029,37479:26262,37480:27264,37481:27573,37482:30007,37483:35527,37484:20516,37485:30693,37486:22320,37487:24347,37488:24677,37489:26234,37490:27744,37491:30196,37492:31258,37493:32622,37494:33268,37495:34584,37496:36933,37497:39347,37498:31689,37499:30044,37500:31481,37501:31569,37502:33988,37504:36880,37505:31209,37506:31378,37507:33590,37508:23265,37509:30528,37510:20013,37511:20210,37512:23449,37513:24544,37514:25277,37515:26172,37516:26609,37517:27880,37518:34411,37519:34935,37520:35387,37521:37198,37522:37619,37523:39376,37524:27159,37525:28710,37526:29482,37527:33511,37528:33879,37529:36015,37530:19969,37531:20806,37532:20939,37533:21899,37534:23541,37535:24086,37536:24115,37537:24193,37538:24340,37539:24373,37540:24427,37541:24500,37542:25074,37543:25361,37544:26274,37545:26397,37546:28526,37547:29266,37548:30010,37549:30522,37550:32884,37551:33081,37552:33144,37553:34678,37554:35519,37555:35548,37556:36229,37557:36339,37558:37530,37559:38263,37560:38914,37561:40165,37562:21189,37563:25431,37564:30452,37565:26389,37566:27784,37567:29645,37568:36035,37569:37806,37570:38515,37571:27941,37572:22684,37573:26894,37574:27084,37575:36861,37576:37786,37577:30171,37578:36890,37579:22618,37580:26626,37581:25524,37582:27131,37583:20291,37584:28460,37585:26584,37586:36795,37587:34086,37588:32180,37589:37716,37590:26943,37591:28528,37592:22378,37593:22775,37594:23340,37595:32044,37596:29226,37597:21514,37598:37347,37599:40372,37600:20141,37601:20302,37602:20572,37603:20597,37604:21059,37605:35998,37606:21576,37607:22564,37608:23450,37609:24093,37610:24213,37611:24237,37612:24311,37613:24351,37614:24716,37615:25269,37616:25402,37617:25552,37618:26799,37619:27712,37620:30855,37621:31118,37622:31243,37623:32224,37624:33351,37625:35330,37626:35558,37627:36420,37628:36883,37696:37048,37697:37165,37698:37336,37699:40718,37700:27877,37701:25688,37702:25826,37703:25973,37704:28404,37705:30340,37706:31515,37707:36969,37708:37841,37709:28346,37710:21746,37711:24505,37712:25764,37713:36685,37714:36845,37715:37444,37716:20856,37717:22635,37718:22825,37719:23637,37720:24215,37721:28155,37722:32399,37723:29980,37724:36028,37725:36578,37726:39003,37727:28857,37728:20253,37729:27583,37730:28593,37731:3e4,37732:38651,37733:20814,37734:21520,37735:22581,37736:22615,37737:22956,37738:23648,37739:24466,37740:26007,37741:26460,37742:28193,37743:30331,37744:33759,37745:36077,37746:36884,37747:37117,37748:37709,37749:30757,37750:30778,37751:21162,37752:24230,37753:22303,37754:22900,37755:24594,37756:20498,37757:20826,37758:20908,37760:20941,37761:20992,37762:21776,37763:22612,37764:22616,37765:22871,37766:23445,37767:23798,37768:23947,37769:24764,37770:25237,37771:25645,37772:26481,37773:26691,37774:26812,37775:26847,37776:30423,37777:28120,37778:28271,37779:28059,37780:28783,37781:29128,37782:24403,37783:30168,37784:31095,37785:31561,37786:31572,37787:31570,37788:31958,37789:32113,37790:21040,37791:33891,37792:34153,37793:34276,37794:35342,37795:35588,37796:35910,37797:36367,37798:36867,37799:36879,37800:37913,37801:38518,37802:38957,37803:39472,37804:38360,37805:20685,37806:21205,37807:21516,37808:22530,37809:23566,37810:24999,37811:25758,37812:27934,37813:30643,37814:31461,37815:33012,37816:33796,37817:36947,37818:37509,37819:23776,37820:40199,37821:21311,37822:24471,37823:24499,37824:28060,37825:29305,37826:30563,37827:31167,37828:31716,37829:27602,37830:29420,37831:35501,37832:26627,37833:27233,37834:20984,37835:31361,37836:26932,37837:23626,37838:40182,37839:33515,37840:23493,37841:37193,37842:28702,37843:22136,37844:23663,37845:24775,37846:25958,37847:27788,37848:35930,37849:36929,37850:38931,37851:21585,37852:26311,37853:37389,37854:22856,37855:37027,37856:20869,37857:20045,37858:20970,37859:34201,37860:35598,37861:28760,37862:25466,37863:37707,37864:26978,37865:39348,37866:32260,37867:30071,37868:21335,37869:26976,37870:36575,37871:38627,37872:27741,37873:20108,37874:23612,37875:24336,37876:36841,37877:21250,37878:36049,37879:32905,37880:34425,37881:24319,37882:26085,37883:20083,37884:20837,37952:22914,37953:23615,37954:38894,37955:20219,37956:22922,37957:24525,37958:35469,37959:28641,37960:31152,37961:31074,37962:23527,37963:33905,37964:29483,37965:29105,37966:24180,37967:24565,37968:25467,37969:25754,37970:29123,37971:31896,37972:20035,37973:24316,37974:20043,37975:22492,37976:22178,37977:24745,37978:28611,37979:32013,37980:33021,37981:33075,37982:33215,37983:36786,37984:35223,37985:34468,37986:24052,37987:25226,37988:25773,37989:35207,37990:26487,37991:27874,37992:27966,37993:29750,37994:30772,37995:23110,37996:32629,37997:33453,37998:39340,37999:20467,38e3:24259,38001:25309,38002:25490,38003:25943,38004:26479,38005:30403,38006:29260,38007:32972,38008:32954,38009:36649,38010:37197,38011:20493,38012:22521,38013:23186,38014:26757,38016:26995,38017:29028,38018:29437,38019:36023,38020:22770,38021:36064,38022:38506,38023:36889,38024:34687,38025:31204,38026:30695,38027:33833,38028:20271,38029:21093,38030:21338,38031:25293,38032:26575,38033:27850,38034:30333,38035:31636,38036:31893,38037:33334,38038:34180,38039:36843,38040:26333,38041:28448,38042:29190,38043:32283,38044:33707,38045:39361,38046:40614,38047:20989,38048:31665,38049:30834,38050:31672,38051:32903,38052:31560,38053:27368,38054:24161,38055:32908,38056:30033,38057:30048,38058:20843,38059:37474,38060:28300,38061:30330,38062:37271,38063:39658,38064:20240,38065:32624,38066:25244,38067:31567,38068:38309,38069:40169,38070:22138,38071:22617,38072:34532,38073:38588,38074:20276,38075:21028,38076:21322,38077:21453,38078:21467,38079:24070,38080:25644,38081:26001,38082:26495,38083:27710,38084:27726,38085:29256,38086:29359,38087:29677,38088:30036,38089:32321,38090:33324,38091:34281,38092:36009,38093:31684,38094:37318,38095:29033,38096:38930,38097:39151,38098:25405,38099:26217,38100:30058,38101:30436,38102:30928,38103:34115,38104:34542,38105:21290,38106:21329,38107:21542,38108:22915,38109:24199,38110:24444,38111:24754,38112:25161,38113:25209,38114:25259,38115:26e3,38116:27604,38117:27852,38118:30130,38119:30382,38120:30865,38121:31192,38122:32203,38123:32631,38124:32933,38125:34987,38126:35513,38127:36027,38128:36991,38129:38750,38130:39131,38131:27147,38132:31800,38133:20633,38134:23614,38135:24494,38136:26503,38137:27608,38138:29749,38139:30473,38140:32654,38208:40763,38209:26570,38210:31255,38211:21305,38212:30091,38213:39661,38214:24422,38215:33181,38216:33777,38217:32920,38218:24380,38219:24517,38220:30050,38221:31558,38222:36924,38223:26727,38224:23019,38225:23195,38226:32016,38227:30334,38228:35628,38229:20469,38230:24426,38231:27161,38232:27703,38233:28418,38234:29922,38235:31080,38236:34920,38237:35413,38238:35961,38239:24287,38240:25551,38241:30149,38242:31186,38243:33495,38244:37672,38245:37618,38246:33948,38247:34541,38248:39981,38249:21697,38250:24428,38251:25996,38252:27996,38253:28693,38254:36007,38255:36051,38256:38971,38257:25935,38258:29942,38259:19981,38260:20184,38261:22496,38262:22827,38263:23142,38264:23500,38265:20904,38266:24067,38267:24220,38268:24598,38269:25206,38270:25975,38272:26023,38273:26222,38274:28014,38275:29238,38276:31526,38277:33104,38278:33178,38279:33433,38280:35676,38281:36e3,38282:36070,38283:36212,38284:38428,38285:38468,38286:20398,38287:25771,38288:27494,38289:33310,38290:33889,38291:34154,38292:37096,38293:23553,38294:26963,38295:39080,38296:33914,38297:34135,38298:20239,38299:21103,38300:24489,38301:24133,38302:26381,38303:31119,38304:33145,38305:35079,38306:35206,38307:28149,38308:24343,38309:25173,38310:27832,38311:20175,38312:29289,38313:39826,38314:20998,38315:21563,38316:22132,38317:22707,38318:24996,38319:25198,38320:28954,38321:22894,38322:31881,38323:31966,38324:32027,38325:38640,38326:25991,38327:32862,38328:19993,38329:20341,38330:20853,38331:22592,38332:24163,38333:24179,38334:24330,38335:26564,38336:20006,38337:34109,38338:38281,38339:38491,38340:31859,38341:38913,38342:20731,38343:22721,38344:30294,38345:30887,38346:21029,38347:30629,38348:34065,38349:31622,38350:20559,38351:22793,38352:29255,38353:31687,38354:32232,38355:36794,38356:36820,38357:36941,38358:20415,38359:21193,38360:23081,38361:24321,38362:38829,38363:20445,38364:33303,38365:37610,38366:22275,38367:25429,38368:27497,38369:29995,38370:35036,38371:36628,38372:31298,38373:21215,38374:22675,38375:24917,38376:25098,38377:26286,38378:27597,38379:31807,38380:33769,38381:20515,38382:20472,38383:21253,38384:21574,38385:22577,38386:22857,38387:23453,38388:23792,38389:23791,38390:23849,38391:24214,38392:25265,38393:25447,38394:25918,38395:26041,38396:26379,38464:27861,38465:27873,38466:28921,38467:30770,38468:32299,38469:32990,38470:33459,38471:33804,38472:34028,38473:34562,38474:35090,38475:35370,38476:35914,38477:37030,38478:37586,38479:39165,38480:40179,38481:40300,38482:20047,38483:20129,38484:20621,38485:21078,38486:22346,38487:22952,38488:24125,38489:24536,38490:24537,38491:25151,38492:26292,38493:26395,38494:26576,38495:26834,38496:20882,38497:32033,38498:32938,38499:33192,38500:35584,38501:35980,38502:36031,38503:37502,38504:38450,38505:21536,38506:38956,38507:21271,38508:20693,38509:21340,38510:22696,38511:25778,38512:26420,38513:29287,38514:30566,38515:31302,38516:37350,38517:21187,38518:27809,38519:27526,38520:22528,38521:24140,38522:22868,38523:26412,38524:32763,38525:20961,38526:30406,38528:25705,38529:30952,38530:39764,38531:40635,38532:22475,38533:22969,38534:26151,38535:26522,38536:27598,38537:21737,38538:27097,38539:24149,38540:33180,38541:26517,38542:39850,38543:26622,38544:40018,38545:26717,38546:20134,38547:20451,38548:21448,38549:25273,38550:26411,38551:27819,38552:36804,38553:20397,38554:32365,38555:40639,38556:19975,38557:24930,38558:28288,38559:28459,38560:34067,38561:21619,38562:26410,38563:39749,38564:24051,38565:31637,38566:23724,38567:23494,38568:34588,38569:28234,38570:34001,38571:31252,38572:33032,38573:22937,38574:31885,38575:27665,38576:30496,38577:21209,38578:22818,38579:28961,38580:29279,38581:30683,38582:38695,38583:40289,38584:26891,38585:23167,38586:23064,38587:20901,38588:21517,38589:21629,38590:26126,38591:30431,38592:36855,38593:37528,38594:40180,38595:23018,38596:29277,38597:28357,38598:20813,38599:26825,38600:32191,38601:32236,38602:38754,38603:40634,38604:25720,38605:27169,38606:33538,38607:22916,38608:23391,38609:27611,38610:29467,38611:30450,38612:32178,38613:32791,38614:33945,38615:20786,38616:26408,38617:40665,38618:30446,38619:26466,38620:21247,38621:39173,38622:23588,38623:25147,38624:31870,38625:36016,38626:21839,38627:24758,38628:32011,38629:38272,38630:21249,38631:20063,38632:20918,38633:22812,38634:29242,38635:32822,38636:37326,38637:24357,38638:30690,38639:21380,38640:24441,38641:32004,38642:34220,38643:35379,38644:36493,38645:38742,38646:26611,38647:34222,38648:37971,38649:24841,38650:24840,38651:27833,38652:30290,38720:35565,38721:36664,38722:21807,38723:20305,38724:20778,38725:21191,38726:21451,38727:23461,38728:24189,38729:24736,38730:24962,38731:25558,38732:26377,38733:26586,38734:28263,38735:28044,38736:29494,38737:29495,38738:30001,38739:31056,38740:35029,38741:35480,38742:36938,38743:37009,38744:37109,38745:38596,38746:34701,38747:22805,38748:20104,38749:20313,38750:19982,38751:35465,38752:36671,38753:38928,38754:20653,38755:24188,38756:22934,38757:23481,38758:24248,38759:25562,38760:25594,38761:25793,38762:26332,38763:26954,38764:27096,38765:27915,38766:28342,38767:29076,38768:29992,38769:31407,38770:32650,38771:32768,38772:33865,38773:33993,38774:35201,38775:35617,38776:36362,38777:36965,38778:38525,38779:39178,38780:24958,38781:25233,38782:27442,38784:27779,38785:28020,38786:32716,38787:32764,38788:28096,38789:32645,38790:34746,38791:35064,38792:26469,38793:33713,38794:38972,38795:38647,38796:27931,38797:32097,38798:33853,38799:37226,38800:20081,38801:21365,38802:23888,38803:27396,38804:28651,38805:34253,38806:34349,38807:35239,38808:21033,38809:21519,38810:23653,38811:26446,38812:26792,38813:29702,38814:29827,38815:30178,38816:35023,38817:35041,38818:37324,38819:38626,38820:38520,38821:24459,38822:29575,38823:31435,38824:33870,38825:25504,38826:30053,38827:21129,38828:27969,38829:28316,38830:29705,38831:30041,38832:30827,38833:31890,38834:38534,38835:31452,38836:40845,38837:20406,38838:24942,38839:26053,38840:34396,38841:20102,38842:20142,38843:20698,38844:20001,38845:20940,38846:23534,38847:26009,38848:26753,38849:28092,38850:29471,38851:30274,38852:30637,38853:31260,38854:31975,38855:33391,38856:35538,38857:36988,38858:37327,38859:38517,38860:38936,38861:21147,38862:32209,38863:20523,38864:21400,38865:26519,38866:28107,38867:29136,38868:29747,38869:33256,38870:36650,38871:38563,38872:40023,38873:40607,38874:29792,38875:22593,38876:28057,38877:32047,38878:39006,38879:20196,38880:20278,38881:20363,38882:20919,38883:21169,38884:23994,38885:24604,38886:29618,38887:31036,38888:33491,38889:37428,38890:38583,38891:38646,38892:38666,38893:40599,38894:40802,38895:26278,38896:27508,38897:21015,38898:21155,38899:28872,38900:35010,38901:24265,38902:24651,38903:24976,38904:28451,38905:29001,38906:31806,38907:32244,38908:32879,38976:34030,38977:36899,38978:37676,38979:21570,38980:39791,38981:27347,38982:28809,38983:36034,38984:36335,38985:38706,38986:21172,38987:23105,38988:24266,38989:24324,38990:26391,38991:27004,38992:27028,38993:28010,38994:28431,38995:29282,38996:29436,38997:31725,38998:32769,38999:32894,39e3:34635,39001:37070,39002:20845,39003:40595,39004:31108,39005:32907,39006:37682,39007:35542,39008:20525,39009:21644,39010:35441,39011:27498,39012:36036,39013:33031,39014:24785,39015:26528,39016:40434,39017:20121,39018:20120,39019:39952,39020:35435,39021:34241,39022:34152,39023:26880,39024:28286,39025:30871,39026:33109,39071:24332,39072:19984,39073:19989,39074:20010,39075:20017,39076:20022,39077:20028,39078:20031,39079:20034,39080:20054,39081:20056,39082:20098,39083:20101,39084:35947,39085:20106,39086:33298,39087:24333,39088:20110,39089:20126,39090:20127,39091:20128,39092:20130,39093:20144,39094:20147,39095:20150,39096:20174,39097:20173,39098:20164,39099:20166,39100:20162,39101:20183,39102:20190,39103:20205,39104:20191,39105:20215,39106:20233,39107:20314,39108:20272,39109:20315,39110:20317,39111:20311,39112:20295,39113:20342,39114:20360,39115:20367,39116:20376,39117:20347,39118:20329,39119:20336,39120:20369,39121:20335,39122:20358,39123:20374,39124:20760,39125:20436,39126:20447,39127:20430,39128:20440,39129:20443,39130:20433,39131:20442,39132:20432,39133:20452,39134:20453,39135:20506,39136:20520,39137:20500,39138:20522,39139:20517,39140:20485,39141:20252,39142:20470,39143:20513,39144:20521,39145:20524,39146:20478,39147:20463,39148:20497,39149:20486,39150:20547,39151:20551,39152:26371,39153:20565,39154:20560,39155:20552,39156:20570,39157:20566,39158:20588,39159:20600,39160:20608,39161:20634,39162:20613,39163:20660,39164:20658,39232:20681,39233:20682,39234:20659,39235:20674,39236:20694,39237:20702,39238:20709,39239:20717,39240:20707,39241:20718,39242:20729,39243:20725,39244:20745,39245:20737,39246:20738,39247:20758,39248:20757,39249:20756,39250:20762,39251:20769,39252:20794,39253:20791,39254:20796,39255:20795,39256:20799,39257:20800,39258:20818,39259:20812,39260:20820,39261:20834,39262:31480,39263:20841,39264:20842,39265:20846,39266:20864,39267:20866,39268:22232,39269:20876,39270:20873,39271:20879,39272:20881,39273:20883,39274:20885,39275:20886,39276:20900,39277:20902,39278:20898,39279:20905,39280:20906,39281:20907,39282:20915,39283:20913,39284:20914,39285:20912,39286:20917,39287:20925,39288:20933,39289:20937,39290:20955,39291:20960,39292:34389,39293:20969,39294:20973,39296:20976,39297:20981,39298:20990,39299:20996,39300:21003,39301:21012,39302:21006,39303:21031,39304:21034,39305:21038,39306:21043,39307:21049,39308:21071,39309:21060,39310:21067,39311:21068,39312:21086,39313:21076,39314:21098,39315:21108,39316:21097,39317:21107,39318:21119,39319:21117,39320:21133,39321:21140,39322:21138,39323:21105,39324:21128,39325:21137,39326:36776,39327:36775,39328:21164,39329:21165,39330:21180,39331:21173,39332:21185,39333:21197,39334:21207,39335:21214,39336:21219,39337:21222,39338:39149,39339:21216,39340:21235,39341:21237,39342:21240,39343:21241,39344:21254,39345:21256,39346:30008,39347:21261,39348:21264,39349:21263,39350:21269,39351:21274,39352:21283,39353:21295,39354:21297,39355:21299,39356:21304,39357:21312,39358:21318,39359:21317,39360:19991,39361:21321,39362:21325,39363:20950,39364:21342,39365:21353,39366:21358,39367:22808,39368:21371,39369:21367,39370:21378,39371:21398,39372:21408,39373:21414,39374:21413,39375:21422,39376:21424,39377:21430,39378:21443,39379:31762,39380:38617,39381:21471,39382:26364,39383:29166,39384:21486,39385:21480,39386:21485,39387:21498,39388:21505,39389:21565,39390:21568,39391:21548,39392:21549,39393:21564,39394:21550,39395:21558,39396:21545,39397:21533,39398:21582,39399:21647,39400:21621,39401:21646,39402:21599,39403:21617,39404:21623,39405:21616,39406:21650,39407:21627,39408:21632,39409:21622,39410:21636,39411:21648,39412:21638,39413:21703,39414:21666,39415:21688,39416:21669,39417:21676,39418:21700,39419:21704,39420:21672,39488:21675,39489:21698,39490:21668,39491:21694,39492:21692,39493:21720,39494:21733,39495:21734,39496:21775,39497:21780,39498:21757,39499:21742,39500:21741,39501:21754,39502:21730,39503:21817,39504:21824,39505:21859,39506:21836,39507:21806,39508:21852,39509:21829,39510:21846,39511:21847,39512:21816,39513:21811,39514:21853,39515:21913,39516:21888,39517:21679,39518:21898,39519:21919,39520:21883,39521:21886,39522:21912,39523:21918,39524:21934,39525:21884,39526:21891,39527:21929,39528:21895,39529:21928,39530:21978,39531:21957,39532:21983,39533:21956,39534:21980,39535:21988,39536:21972,39537:22036,39538:22007,39539:22038,39540:22014,39541:22013,39542:22043,39543:22009,39544:22094,39545:22096,39546:29151,39547:22068,39548:22070,39549:22066,39550:22072,39552:22123,39553:22116,39554:22063,39555:22124,39556:22122,39557:22150,39558:22144,39559:22154,39560:22176,39561:22164,39562:22159,39563:22181,39564:22190,39565:22198,39566:22196,39567:22210,39568:22204,39569:22209,39570:22211,39571:22208,39572:22216,39573:22222,39574:22225,39575:22227,39576:22231,39577:22254,39578:22265,39579:22272,39580:22271,39581:22276,39582:22281,39583:22280,39584:22283,39585:22285,39586:22291,39587:22296,39588:22294,39589:21959,39590:22300,39591:22310,39592:22327,39593:22328,39594:22350,39595:22331,39596:22336,39597:22351,39598:22377,39599:22464,39600:22408,39601:22369,39602:22399,39603:22409,39604:22419,39605:22432,39606:22451,39607:22436,39608:22442,39609:22448,39610:22467,39611:22470,39612:22484,39613:22482,39614:22483,39615:22538,39616:22486,39617:22499,39618:22539,39619:22553,39620:22557,39621:22642,39622:22561,39623:22626,39624:22603,39625:22640,39626:27584,39627:22610,39628:22589,39629:22649,39630:22661,39631:22713,39632:22687,39633:22699,39634:22714,39635:22750,39636:22715,39637:22712,39638:22702,39639:22725,39640:22739,39641:22737,39642:22743,39643:22745,39644:22744,39645:22757,39646:22748,39647:22756,39648:22751,39649:22767,39650:22778,39651:22777,39652:22779,39653:22780,39654:22781,39655:22786,39656:22794,39657:22800,39658:22811,39659:26790,39660:22821,39661:22828,39662:22829,39663:22834,39664:22840,39665:22846,39666:31442,39667:22869,39668:22864,39669:22862,39670:22874,39671:22872,39672:22882,39673:22880,39674:22887,39675:22892,39676:22889,39744:22904,39745:22913,39746:22941,39747:20318,39748:20395,39749:22947,39750:22962,39751:22982,39752:23016,39753:23004,39754:22925,39755:23001,39756:23002,39757:23077,39758:23071,39759:23057,39760:23068,39761:23049,39762:23066,39763:23104,39764:23148,39765:23113,39766:23093,39767:23094,39768:23138,39769:23146,39770:23194,39771:23228,39772:23230,39773:23243,39774:23234,39775:23229,39776:23267,39777:23255,39778:23270,39779:23273,39780:23254,39781:23290,39782:23291,39783:23308,39784:23307,39785:23318,39786:23346,39787:23248,39788:23338,39789:23350,39790:23358,39791:23363,39792:23365,39793:23360,39794:23377,39795:23381,39796:23386,39797:23387,39798:23397,39799:23401,39800:23408,39801:23411,39802:23413,39803:23416,39804:25992,39805:23418,39806:23424,39808:23427,39809:23462,39810:23480,39811:23491,39812:23495,39813:23497,39814:23508,39815:23504,39816:23524,39817:23526,39818:23522,39819:23518,39820:23525,39821:23531,39822:23536,39823:23542,39824:23539,39825:23557,39826:23559,39827:23560,39828:23565,39829:23571,39830:23584,39831:23586,39832:23592,39833:23608,39834:23609,39835:23617,39836:23622,39837:23630,39838:23635,39839:23632,39840:23631,39841:23409,39842:23660,39843:23662,39844:20066,39845:23670,39846:23673,39847:23692,39848:23697,39849:23700,39850:22939,39851:23723,39852:23739,39853:23734,39854:23740,39855:23735,39856:23749,39857:23742,39858:23751,39859:23769,39860:23785,39861:23805,39862:23802,39863:23789,39864:23948,39865:23786,39866:23819,39867:23829,39868:23831,39869:23900,39870:23839,39871:23835,39872:23825,39873:23828,39874:23842,39875:23834,39876:23833,39877:23832,39878:23884,39879:23890,39880:23886,39881:23883,39882:23916,39883:23923,39884:23926,39885:23943,39886:23940,39887:23938,39888:23970,39889:23965,39890:23980,39891:23982,39892:23997,39893:23952,39894:23991,39895:23996,39896:24009,39897:24013,39898:24019,39899:24018,39900:24022,39901:24027,39902:24043,39903:24050,39904:24053,39905:24075,39906:24090,39907:24089,39908:24081,39909:24091,39910:24118,39911:24119,39912:24132,39913:24131,39914:24128,39915:24142,39916:24151,39917:24148,39918:24159,39919:24162,39920:24164,39921:24135,39922:24181,39923:24182,39924:24186,39925:40636,39926:24191,39927:24224,39928:24257,39929:24258,39930:24264,39931:24272,39932:24271,4e4:24278,40001:24291,40002:24285,40003:24282,40004:24283,40005:24290,40006:24289,40007:24296,40008:24297,40009:24300,40010:24305,40011:24307,40012:24304,40013:24308,40014:24312,40015:24318,40016:24323,40017:24329,40018:24413,40019:24412,40020:24331,40021:24337,40022:24342,40023:24361,40024:24365,40025:24376,40026:24385,40027:24392,40028:24396,40029:24398,40030:24367,40031:24401,40032:24406,40033:24407,40034:24409,40035:24417,40036:24429,40037:24435,40038:24439,40039:24451,40040:24450,40041:24447,40042:24458,40043:24456,40044:24465,40045:24455,40046:24478,40047:24473,40048:24472,40049:24480,40050:24488,40051:24493,40052:24508,40053:24534,40054:24571,40055:24548,40056:24568,40057:24561,40058:24541,40059:24755,40060:24575,40061:24609,40062:24672,40064:24601,40065:24592,40066:24617,40067:24590,40068:24625,40069:24603,40070:24597,40071:24619,40072:24614,40073:24591,40074:24634,40075:24666,40076:24641,40077:24682,40078:24695,40079:24671,40080:24650,40081:24646,40082:24653,40083:24675,40084:24643,40085:24676,40086:24642,40087:24684,40088:24683,40089:24665,40090:24705,40091:24717,40092:24807,40093:24707,40094:24730,40095:24708,40096:24731,40097:24726,40098:24727,40099:24722,40100:24743,40101:24715,40102:24801,40103:24760,40104:24800,40105:24787,40106:24756,40107:24560,40108:24765,40109:24774,40110:24757,40111:24792,40112:24909,40113:24853,40114:24838,40115:24822,40116:24823,40117:24832,40118:24820,40119:24826,40120:24835,40121:24865,40122:24827,40123:24817,40124:24845,40125:24846,40126:24903,40127:24894,40128:24872,40129:24871,40130:24906,40131:24895,40132:24892,40133:24876,40134:24884,40135:24893,40136:24898,40137:24900,40138:24947,40139:24951,40140:24920,40141:24921,40142:24922,40143:24939,40144:24948,40145:24943,40146:24933,40147:24945,40148:24927,40149:24925,40150:24915,40151:24949,40152:24985,40153:24982,40154:24967,40155:25004,40156:24980,40157:24986,40158:24970,40159:24977,40160:25003,40161:25006,40162:25036,40163:25034,40164:25033,40165:25079,40166:25032,40167:25027,40168:25030,40169:25018,40170:25035,40171:32633,40172:25037,40173:25062,40174:25059,40175:25078,40176:25082,40177:25076,40178:25087,40179:25085,40180:25084,40181:25086,40182:25088,40183:25096,40184:25097,40185:25101,40186:25100,40187:25108,40188:25115,40256:25118,40257:25121,40258:25130,40259:25134,40260:25136,40261:25138,40262:25139,40263:25153,40264:25166,40265:25182,40266:25187,40267:25179,40268:25184,40269:25192,40270:25212,40271:25218,40272:25225,40273:25214,40274:25234,40275:25235,40276:25238,40277:25300,40278:25219,40279:25236,40280:25303,40281:25297,40282:25275,40283:25295,40284:25343,40285:25286,40286:25812,40287:25288,40288:25308,40289:25292,40290:25290,40291:25282,40292:25287,40293:25243,40294:25289,40295:25356,40296:25326,40297:25329,40298:25383,40299:25346,40300:25352,40301:25327,40302:25333,40303:25424,40304:25406,40305:25421,40306:25628,40307:25423,40308:25494,40309:25486,40310:25472,40311:25515,40312:25462,40313:25507,40314:25487,40315:25481,40316:25503,40317:25525,40318:25451,40320:25449,40321:25534,40322:25577,40323:25536,40324:25542,40325:25571,40326:25545,40327:25554,40328:25590,40329:25540,40330:25622,40331:25652,40332:25606,40333:25619,40334:25638,40335:25654,40336:25885,40337:25623,40338:25640,40339:25615,40340:25703,40341:25711,40342:25718,40343:25678,40344:25898,40345:25749,40346:25747,40347:25765,40348:25769,40349:25736,40350:25788,40351:25818,40352:25810,40353:25797,40354:25799,40355:25787,40356:25816,40357:25794,40358:25841,40359:25831,40360:33289,40361:25824,40362:25825,40363:25260,40364:25827,40365:25839,40366:25900,40367:25846,40368:25844,40369:25842,40370:25850,40371:25856,40372:25853,40373:25880,40374:25884,40375:25861,40376:25892,40377:25891,40378:25899,40379:25908,40380:25909,40381:25911,40382:25910,40383:25912,40384:30027,40385:25928,40386:25942,40387:25941,40388:25933,40389:25944,40390:25950,40391:25949,40392:25970,40393:25976,40394:25986,40395:25987,40396:35722,40397:26011,40398:26015,40399:26027,40400:26039,40401:26051,40402:26054,40403:26049,40404:26052,40405:26060,40406:26066,40407:26075,40408:26073,40409:26080,40410:26081,40411:26097,40412:26482,40413:26122,40414:26115,40415:26107,40416:26483,40417:26165,40418:26166,40419:26164,40420:26140,40421:26191,40422:26180,40423:26185,40424:26177,40425:26206,40426:26205,40427:26212,40428:26215,40429:26216,40430:26207,40431:26210,40432:26224,40433:26243,40434:26248,40435:26254,40436:26249,40437:26244,40438:26264,40439:26269,40440:26305,40441:26297,40442:26313,40443:26302,40444:26300,40512:26308,40513:26296,40514:26326,40515:26330,40516:26336,40517:26175,40518:26342,40519:26345,40520:26352,40521:26357,40522:26359,40523:26383,40524:26390,40525:26398,40526:26406,40527:26407,40528:38712,40529:26414,40530:26431,40531:26422,40532:26433,40533:26424,40534:26423,40535:26438,40536:26462,40537:26464,40538:26457,40539:26467,40540:26468,40541:26505,40542:26480,40543:26537,40544:26492,40545:26474,40546:26508,40547:26507,40548:26534,40549:26529,40550:26501,40551:26551,40552:26607,40553:26548,40554:26604,40555:26547,40556:26601,40557:26552,40558:26596,40559:26590,40560:26589,40561:26594,40562:26606,40563:26553,40564:26574,40565:26566,40566:26599,40567:27292,40568:26654,40569:26694,40570:26665,40571:26688,40572:26701,40573:26674,40574:26702,40576:26803,40577:26667,40578:26713,40579:26723,40580:26743,40581:26751,40582:26783,40583:26767,40584:26797,40585:26772,40586:26781,40587:26779,40588:26755,40589:27310,40590:26809,40591:26740,40592:26805,40593:26784,40594:26810,40595:26895,40596:26765,40597:26750,40598:26881,40599:26826,40600:26888,40601:26840,40602:26914,40603:26918,40604:26849,40605:26892,40606:26829,40607:26836,40608:26855,40609:26837,40610:26934,40611:26898,40612:26884,40613:26839,40614:26851,40615:26917,40616:26873,40617:26848,40618:26863,40619:26920,40620:26922,40621:26906,40622:26915,40623:26913,40624:26822,40625:27001,40626:26999,40627:26972,40628:27e3,40629:26987,40630:26964,40631:27006,40632:26990,40633:26937,40634:26996,40635:26941,40636:26969,40637:26928,40638:26977,40639:26974,40640:26973,40641:27009,40642:26986,40643:27058,40644:27054,40645:27088,40646:27071,40647:27073,40648:27091,40649:27070,40650:27086,40651:23528,40652:27082,40653:27101,40654:27067,40655:27075,40656:27047,40657:27182,40658:27025,40659:27040,40660:27036,40661:27029,40662:27060,40663:27102,40664:27112,40665:27138,40666:27163,40667:27135,40668:27402,40669:27129,40670:27122,40671:27111,40672:27141,40673:27057,40674:27166,40675:27117,40676:27156,40677:27115,40678:27146,40679:27154,40680:27329,40681:27171,40682:27155,40683:27204,40684:27148,40685:27250,40686:27190,40687:27256,40688:27207,40689:27234,40690:27225,40691:27238,40692:27208,40693:27192,40694:27170,40695:27280,40696:27277,40697:27296,40698:27268,40699:27298,40700:27299,40768:27287,40769:34327,40770:27323,40771:27331,40772:27330,40773:27320,40774:27315,40775:27308,40776:27358,40777:27345,40778:27359,40779:27306,40780:27354,40781:27370,40782:27387,40783:27397,40784:34326,40785:27386,40786:27410,40787:27414,40788:39729,40789:27423,40790:27448,40791:27447,40792:30428,40793:27449,40794:39150,40795:27463,40796:27459,40797:27465,40798:27472,40799:27481,40800:27476,40801:27483,40802:27487,40803:27489,40804:27512,40805:27513,40806:27519,40807:27520,40808:27524,40809:27523,40810:27533,40811:27544,40812:27541,40813:27550,40814:27556,40815:27562,40816:27563,40817:27567,40818:27570,40819:27569,40820:27571,40821:27575,40822:27580,40823:27590,40824:27595,40825:27603,40826:27615,40827:27628,40828:27627,40829:27635,40830:27631,40832:40638,40833:27656,40834:27667,40835:27668,40836:27675,40837:27684,40838:27683,40839:27742,40840:27733,40841:27746,40842:27754,40843:27778,40844:27789,40845:27802,40846:27777,40847:27803,40848:27774,40849:27752,40850:27763,40851:27794,40852:27792,40853:27844,40854:27889,40855:27859,40856:27837,40857:27863,40858:27845,40859:27869,40860:27822,40861:27825,40862:27838,40863:27834,40864:27867,40865:27887,40866:27865,40867:27882,40868:27935,40869:34893,40870:27958,40871:27947,40872:27965,40873:27960,40874:27929,40875:27957,40876:27955,40877:27922,40878:27916,40879:28003,40880:28051,40881:28004,40882:27994,40883:28025,40884:27993,40885:28046,40886:28053,40887:28644,40888:28037,40889:28153,40890:28181,40891:28170,40892:28085,40893:28103,40894:28134,40895:28088,40896:28102,40897:28140,40898:28126,40899:28108,40900:28136,40901:28114,40902:28101,40903:28154,40904:28121,40905:28132,40906:28117,40907:28138,40908:28142,40909:28205,40910:28270,40911:28206,40912:28185,40913:28274,40914:28255,40915:28222,40916:28195,40917:28267,40918:28203,40919:28278,40920:28237,40921:28191,40922:28227,40923:28218,40924:28238,40925:28196,40926:28415,40927:28189,40928:28216,40929:28290,40930:28330,40931:28312,40932:28361,40933:28343,40934:28371,40935:28349,40936:28335,40937:28356,40938:28338,40939:28372,40940:28373,40941:28303,40942:28325,40943:28354,40944:28319,40945:28481,40946:28433,40947:28748,40948:28396,40949:28408,40950:28414,40951:28479,40952:28402,40953:28465,40954:28399,40955:28466,40956:28364,161:65377,162:65378,163:65379,164:65380,165:65381,166:65382,167:65383,168:65384,169:65385,170:65386,171:65387,172:65388,173:65389,174:65390,175:65391,176:65392,177:65393,178:65394,179:65395,180:65396,181:65397,182:65398,183:65399,184:65400,185:65401,186:65402,187:65403,188:65404,189:65405,190:65406,191:65407,192:65408,193:65409,194:65410,195:65411,196:65412,197:65413,198:65414,199:65415,200:65416,201:65417,202:65418,203:65419,204:65420,205:65421,206:65422,207:65423,208:65424,209:65425,210:65426,211:65427,212:65428,213:65429,214:65430,215:65431,216:65432,217:65433,218:65434,219:65435,220:65436,221:65437,222:65438,223:65439,57408:28478,57409:28435,57410:28407,57411:28550,57412:28538,57413:28536,57414:28545,57415:28544,57416:28527,57417:28507,57418:28659,57419:28525,57420:28546,57421:28540,57422:28504,57423:28558,57424:28561,57425:28610,57426:28518,57427:28595,57428:28579,57429:28577,57430:28580,57431:28601,57432:28614,57433:28586,57434:28639,57435:28629,57436:28652,57437:28628,57438:28632,57439:28657,57440:28654,57441:28635,57442:28681,57443:28683,57444:28666,57445:28689,57446:28673,57447:28687,57448:28670,57449:28699,57450:28698,57451:28532,57452:28701,57453:28696,57454:28703,57455:28720,57456:28734,57457:28722,57458:28753,57459:28771,57460:28825,57461:28818,57462:28847,57463:28913,57464:28844,57465:28856,57466:28851,57467:28846,57468:28895,57469:28875,57470:28893,57472:28889,57473:28937,57474:28925,57475:28956,57476:28953,57477:29029,57478:29013,57479:29064,57480:29030,57481:29026,57482:29004,57483:29014,57484:29036,57485:29071,57486:29179,57487:29060,57488:29077,57489:29096,57490:29100,57491:29143,57492:29113,57493:29118,57494:29138,57495:29129,57496:29140,57497:29134,57498:29152,57499:29164,57500:29159,57501:29173,57502:29180,57503:29177,57504:29183,57505:29197,57506:29200,57507:29211,57508:29224,57509:29229,57510:29228,57511:29232,57512:29234,57513:29243,57514:29244,57515:29247,57516:29248,57517:29254,57518:29259,57519:29272,57520:29300,57521:29310,57522:29314,57523:29313,57524:29319,57525:29330,57526:29334,57527:29346,57528:29351,57529:29369,57530:29362,57531:29379,57532:29382,57533:29380,57534:29390,57535:29394,57536:29410,57537:29408,57538:29409,57539:29433,57540:29431,57541:20495,57542:29463,57543:29450,57544:29468,57545:29462,57546:29469,57547:29492,57548:29487,57549:29481,57550:29477,57551:29502,57552:29518,57553:29519,57554:40664,57555:29527,57556:29546,57557:29544,57558:29552,57559:29560,57560:29557,57561:29563,57562:29562,57563:29640,57564:29619,57565:29646,57566:29627,57567:29632,57568:29669,57569:29678,57570:29662,57571:29858,57572:29701,57573:29807,57574:29733,57575:29688,57576:29746,57577:29754,57578:29781,57579:29759,57580:29791,57581:29785,57582:29761,57583:29788,57584:29801,57585:29808,57586:29795,57587:29802,57588:29814,57589:29822,57590:29835,57591:29854,57592:29863,57593:29898,57594:29903,57595:29908,57596:29681,57664:29920,57665:29923,57666:29927,57667:29929,57668:29934,57669:29938,57670:29936,57671:29937,57672:29944,57673:29943,57674:29956,57675:29955,57676:29957,57677:29964,57678:29966,57679:29965,57680:29973,57681:29971,57682:29982,57683:29990,57684:29996,57685:30012,57686:30020,57687:30029,57688:30026,57689:30025,57690:30043,57691:30022,57692:30042,57693:30057,57694:30052,57695:30055,57696:30059,57697:30061,57698:30072,57699:30070,57700:30086,57701:30087,57702:30068,57703:30090,57704:30089,57705:30082,57706:30100,57707:30106,57708:30109,57709:30117,57710:30115,57711:30146,57712:30131,57713:30147,57714:30133,57715:30141,57716:30136,57717:30140,57718:30129,57719:30157,57720:30154,57721:30162,57722:30169,57723:30179,57724:30174,57725:30206,57726:30207,57728:30204,57729:30209,57730:30192,57731:30202,57732:30194,57733:30195,57734:30219,57735:30221,57736:30217,57737:30239,57738:30247,57739:30240,57740:30241,57741:30242,57742:30244,57743:30260,57744:30256,57745:30267,57746:30279,57747:30280,57748:30278,57749:30300,57750:30296,57751:30305,57752:30306,57753:30312,57754:30313,57755:30314,57756:30311,57757:30316,57758:30320,57759:30322,57760:30326,57761:30328,57762:30332,57763:30336,57764:30339,57765:30344,57766:30347,57767:30350,57768:30358,57769:30355,57770:30361,57771:30362,57772:30384,57773:30388,57774:30392,57775:30393,57776:30394,57777:30402,57778:30413,57779:30422,57780:30418,57781:30430,57782:30433,57783:30437,57784:30439,57785:30442,57786:34351,57787:30459,57788:30472,57789:30471,57790:30468,57791:30505,57792:30500,57793:30494,57794:30501,57795:30502,57796:30491,57797:30519,57798:30520,57799:30535,57800:30554,57801:30568,57802:30571,57803:30555,57804:30565,57805:30591,57806:30590,57807:30585,57808:30606,57809:30603,57810:30609,57811:30624,57812:30622,57813:30640,57814:30646,57815:30649,57816:30655,57817:30652,57818:30653,57819:30651,57820:30663,57821:30669,57822:30679,57823:30682,57824:30684,57825:30691,57826:30702,57827:30716,57828:30732,57829:30738,57830:31014,57831:30752,57832:31018,57833:30789,57834:30862,57835:30836,57836:30854,57837:30844,57838:30874,57839:30860,57840:30883,57841:30901,57842:30890,57843:30895,57844:30929,57845:30918,57846:30923,57847:30932,57848:30910,57849:30908,57850:30917,57851:30922,57852:30956,57920:30951,57921:30938,57922:30973,57923:30964,57924:30983,57925:30994,57926:30993,57927:31001,57928:31020,57929:31019,57930:31040,57931:31072,57932:31063,57933:31071,57934:31066,57935:31061,57936:31059,57937:31098,57938:31103,57939:31114,57940:31133,57941:31143,57942:40779,57943:31146,57944:31150,57945:31155,57946:31161,57947:31162,57948:31177,57949:31189,57950:31207,57951:31212,57952:31201,57953:31203,57954:31240,57955:31245,57956:31256,57957:31257,57958:31264,57959:31263,57960:31104,57961:31281,57962:31291,57963:31294,57964:31287,57965:31299,57966:31319,57967:31305,57968:31329,57969:31330,57970:31337,57971:40861,57972:31344,57973:31353,57974:31357,57975:31368,57976:31383,57977:31381,57978:31384,57979:31382,57980:31401,57981:31432,57982:31408,57984:31414,57985:31429,57986:31428,57987:31423,57988:36995,57989:31431,57990:31434,57991:31437,57992:31439,57993:31445,57994:31443,57995:31449,57996:31450,57997:31453,57998:31457,57999:31458,58e3:31462,58001:31469,58002:31472,58003:31490,58004:31503,58005:31498,58006:31494,58007:31539,58008:31512,58009:31513,58010:31518,58011:31541,58012:31528,58013:31542,58014:31568,58015:31610,58016:31492,58017:31565,58018:31499,58019:31564,58020:31557,58021:31605,58022:31589,58023:31604,58024:31591,58025:31600,58026:31601,58027:31596,58028:31598,58029:31645,58030:31640,58031:31647,58032:31629,58033:31644,58034:31642,58035:31627,58036:31634,58037:31631,58038:31581,58039:31641,58040:31691,58041:31681,58042:31692,58043:31695,58044:31668,58045:31686,58046:31709,58047:31721,58048:31761,58049:31764,58050:31718,58051:31717,58052:31840,58053:31744,58054:31751,58055:31763,58056:31731,58057:31735,58058:31767,58059:31757,58060:31734,58061:31779,58062:31783,58063:31786,58064:31775,58065:31799,58066:31787,58067:31805,58068:31820,58069:31811,58070:31828,58071:31823,58072:31808,58073:31824,58074:31832,58075:31839,58076:31844,58077:31830,58078:31845,58079:31852,58080:31861,58081:31875,58082:31888,58083:31908,58084:31917,58085:31906,58086:31915,58087:31905,58088:31912,58089:31923,58090:31922,58091:31921,58092:31918,58093:31929,58094:31933,58095:31936,58096:31941,58097:31938,58098:31960,58099:31954,58100:31964,58101:31970,58102:39739,58103:31983,58104:31986,58105:31988,58106:31990,58107:31994,58108:32006,58176:32002,58177:32028,58178:32021,58179:32010,58180:32069,58181:32075,58182:32046,58183:32050,58184:32063,58185:32053,58186:32070,58187:32115,58188:32086,58189:32078,58190:32114,58191:32104,58192:32110,58193:32079,58194:32099,58195:32147,58196:32137,58197:32091,58198:32143,58199:32125,58200:32155,58201:32186,58202:32174,58203:32163,58204:32181,58205:32199,58206:32189,58207:32171,58208:32317,58209:32162,58210:32175,58211:32220,58212:32184,58213:32159,58214:32176,58215:32216,58216:32221,58217:32228,58218:32222,58219:32251,58220:32242,58221:32225,58222:32261,58223:32266,58224:32291,58225:32289,58226:32274,58227:32305,58228:32287,58229:32265,58230:32267,58231:32290,58232:32326,58233:32358,58234:32315,58235:32309,58236:32313,58237:32323,58238:32311,58240:32306,58241:32314,58242:32359,58243:32349,58244:32342,58245:32350,58246:32345,58247:32346,58248:32377,58249:32362,58250:32361,58251:32380,58252:32379,58253:32387,58254:32213,58255:32381,58256:36782,58257:32383,58258:32392,58259:32393,58260:32396,58261:32402,58262:32400,58263:32403,58264:32404,58265:32406,58266:32398,58267:32411,58268:32412,58269:32568,58270:32570,58271:32581,58272:32588,58273:32589,58274:32590,58275:32592,58276:32593,58277:32597,58278:32596,58279:32600,58280:32607,58281:32608,58282:32616,58283:32617,58284:32615,58285:32632,58286:32642,58287:32646,58288:32643,58289:32648,58290:32647,58291:32652,58292:32660,58293:32670,58294:32669,58295:32666,58296:32675,58297:32687,58298:32690,58299:32697,58300:32686,58301:32694,58302:32696,58303:35697,58304:32709,58305:32710,58306:32714,58307:32725,58308:32724,58309:32737,58310:32742,58311:32745,58312:32755,58313:32761,58314:39132,58315:32774,58316:32772,58317:32779,58318:32786,58319:32792,58320:32793,58321:32796,58322:32801,58323:32808,58324:32831,58325:32827,58326:32842,58327:32838,58328:32850,58329:32856,58330:32858,58331:32863,58332:32866,58333:32872,58334:32883,58335:32882,58336:32880,58337:32886,58338:32889,58339:32893,58340:32895,58341:32900,58342:32902,58343:32901,58344:32923,58345:32915,58346:32922,58347:32941,58348:20880,58349:32940,58350:32987,58351:32997,58352:32985,58353:32989,58354:32964,58355:32986,58356:32982,58357:33033,58358:33007,58359:33009,58360:33051,58361:33065,58362:33059,58363:33071,58364:33099,58432:38539,58433:33094,58434:33086,58435:33107,58436:33105,58437:33020,58438:33137,58439:33134,58440:33125,58441:33126,58442:33140,58443:33155,58444:33160,58445:33162,58446:33152,58447:33154,58448:33184,58449:33173,58450:33188,58451:33187,58452:33119,58453:33171,58454:33193,58455:33200,58456:33205,58457:33214,58458:33208,58459:33213,58460:33216,58461:33218,58462:33210,58463:33225,58464:33229,58465:33233,58466:33241,58467:33240,58468:33224,58469:33242,58470:33247,58471:33248,58472:33255,58473:33274,58474:33275,58475:33278,58476:33281,58477:33282,58478:33285,58479:33287,58480:33290,58481:33293,58482:33296,58483:33302,58484:33321,58485:33323,58486:33336,58487:33331,58488:33344,58489:33369,58490:33368,58491:33373,58492:33370,58493:33375,58494:33380,58496:33378,58497:33384,58498:33386,58499:33387,58500:33326,58501:33393,58502:33399,58503:33400,58504:33406,58505:33421,58506:33426,58507:33451,58508:33439,58509:33467,58510:33452,58511:33505,58512:33507,58513:33503,58514:33490,58515:33524,58516:33523,58517:33530,58518:33683,58519:33539,58520:33531,58521:33529,58522:33502,58523:33542,58524:33500,58525:33545,58526:33497,58527:33589,58528:33588,58529:33558,58530:33586,58531:33585,58532:33600,58533:33593,58534:33616,58535:33605,58536:33583,58537:33579,58538:33559,58539:33560,58540:33669,58541:33690,58542:33706,58543:33695,58544:33698,58545:33686,58546:33571,58547:33678,58548:33671,58549:33674,58550:33660,58551:33717,58552:33651,58553:33653,58554:33696,58555:33673,58556:33704,58557:33780,58558:33811,58559:33771,58560:33742,58561:33789,58562:33795,58563:33752,58564:33803,58565:33729,58566:33783,58567:33799,58568:33760,58569:33778,58570:33805,58571:33826,58572:33824,58573:33725,58574:33848,58575:34054,58576:33787,58577:33901,58578:33834,58579:33852,58580:34138,58581:33924,58582:33911,58583:33899,58584:33965,58585:33902,58586:33922,58587:33897,58588:33862,58589:33836,58590:33903,58591:33913,58592:33845,58593:33994,58594:33890,58595:33977,58596:33983,58597:33951,58598:34009,58599:33997,58600:33979,58601:34010,58602:34e3,58603:33985,58604:33990,58605:34006,58606:33953,58607:34081,58608:34047,58609:34036,58610:34071,58611:34072,58612:34092,58613:34079,58614:34069,58615:34068,58616:34044,58617:34112,58618:34147,58619:34136,58620:34120,58688:34113,58689:34306,58690:34123,58691:34133,58692:34176,58693:34212,58694:34184,58695:34193,58696:34186,58697:34216,58698:34157,58699:34196,58700:34203,58701:34282,58702:34183,58703:34204,58704:34167,58705:34174,58706:34192,58707:34249,58708:34234,58709:34255,58710:34233,58711:34256,58712:34261,58713:34269,58714:34277,58715:34268,58716:34297,58717:34314,58718:34323,58719:34315,58720:34302,58721:34298,58722:34310,58723:34338,58724:34330,58725:34352,58726:34367,58727:34381,58728:20053,58729:34388,58730:34399,58731:34407,58732:34417,58733:34451,58734:34467,58735:34473,58736:34474,58737:34443,58738:34444,58739:34486,58740:34479,58741:34500,58742:34502,58743:34480,58744:34505,58745:34851,58746:34475,58747:34516,58748:34526,58749:34537,58750:34540,58752:34527,58753:34523,58754:34543,58755:34578,58756:34566,58757:34568,58758:34560,58759:34563,58760:34555,58761:34577,58762:34569,58763:34573,58764:34553,58765:34570,58766:34612,58767:34623,58768:34615,58769:34619,58770:34597,58771:34601,58772:34586,58773:34656,58774:34655,58775:34680,58776:34636,58777:34638,58778:34676,58779:34647,58780:34664,58781:34670,58782:34649,58783:34643,58784:34659,58785:34666,58786:34821,58787:34722,58788:34719,58789:34690,58790:34735,58791:34763,58792:34749,58793:34752,58794:34768,58795:38614,58796:34731,58797:34756,58798:34739,58799:34759,58800:34758,58801:34747,58802:34799,58803:34802,58804:34784,58805:34831,58806:34829,58807:34814,58808:34806,58809:34807,58810:34830,58811:34770,58812:34833,58813:34838,58814:34837,58815:34850,58816:34849,58817:34865,58818:34870,58819:34873,58820:34855,58821:34875,58822:34884,58823:34882,58824:34898,58825:34905,58826:34910,58827:34914,58828:34923,58829:34945,58830:34942,58831:34974,58832:34933,58833:34941,58834:34997,58835:34930,58836:34946,58837:34967,58838:34962,58839:34990,58840:34969,58841:34978,58842:34957,58843:34980,58844:34992,58845:35007,58846:34993,58847:35011,58848:35012,58849:35028,58850:35032,58851:35033,58852:35037,58853:35065,58854:35074,58855:35068,58856:35060,58857:35048,58858:35058,58859:35076,58860:35084,58861:35082,58862:35091,58863:35139,58864:35102,58865:35109,58866:35114,58867:35115,58868:35137,58869:35140,58870:35131,58871:35126,58872:35128,58873:35148,58874:35101,58875:35168,58876:35166,58944:35174,58945:35172,58946:35181,58947:35178,58948:35183,58949:35188,58950:35191,58951:35198,58952:35203,58953:35208,58954:35210,58955:35219,58956:35224,58957:35233,58958:35241,58959:35238,58960:35244,58961:35247,58962:35250,58963:35258,58964:35261,58965:35263,58966:35264,58967:35290,58968:35292,58969:35293,58970:35303,58971:35316,58972:35320,58973:35331,58974:35350,58975:35344,58976:35340,58977:35355,58978:35357,58979:35365,58980:35382,58981:35393,58982:35419,58983:35410,58984:35398,58985:35400,58986:35452,58987:35437,58988:35436,58989:35426,58990:35461,58991:35458,58992:35460,58993:35496,58994:35489,58995:35473,58996:35493,58997:35494,58998:35482,58999:35491,59e3:35524,59001:35533,59002:35522,59003:35546,59004:35563,59005:35571,59006:35559,59008:35556,59009:35569,59010:35604,59011:35552,59012:35554,59013:35575,59014:35550,59015:35547,59016:35596,59017:35591,59018:35610,59019:35553,59020:35606,59021:35600,59022:35607,59023:35616,59024:35635,59025:38827,59026:35622,59027:35627,59028:35646,59029:35624,59030:35649,59031:35660,59032:35663,59033:35662,59034:35657,59035:35670,59036:35675,59037:35674,59038:35691,59039:35679,59040:35692,59041:35695,59042:35700,59043:35709,59044:35712,59045:35724,59046:35726,59047:35730,59048:35731,59049:35734,59050:35737,59051:35738,59052:35898,59053:35905,59054:35903,59055:35912,59056:35916,59057:35918,59058:35920,59059:35925,59060:35938,59061:35948,59062:35960,59063:35962,59064:35970,59065:35977,59066:35973,59067:35978,59068:35981,59069:35982,59070:35988,59071:35964,59072:35992,59073:25117,59074:36013,59075:36010,59076:36029,59077:36018,59078:36019,59079:36014,59080:36022,59081:36040,59082:36033,59083:36068,59084:36067,59085:36058,59086:36093,59087:36090,59088:36091,59089:36100,59090:36101,59091:36106,59092:36103,59093:36111,59094:36109,59095:36112,59096:40782,59097:36115,59098:36045,59099:36116,59100:36118,59101:36199,59102:36205,59103:36209,59104:36211,59105:36225,59106:36249,59107:36290,59108:36286,59109:36282,59110:36303,59111:36314,59112:36310,59113:36300,59114:36315,59115:36299,59116:36330,59117:36331,59118:36319,59119:36323,59120:36348,59121:36360,59122:36361,59123:36351,59124:36381,59125:36382,59126:36368,59127:36383,59128:36418,59129:36405,59130:36400,59131:36404,59132:36426,59200:36423,59201:36425,59202:36428,59203:36432,59204:36424,59205:36441,59206:36452,59207:36448,59208:36394,59209:36451,59210:36437,59211:36470,59212:36466,59213:36476,59214:36481,59215:36487,59216:36485,59217:36484,59218:36491,59219:36490,59220:36499,59221:36497,59222:36500,59223:36505,59224:36522,59225:36513,59226:36524,59227:36528,59228:36550,59229:36529,59230:36542,59231:36549,59232:36552,59233:36555,59234:36571,59235:36579,59236:36604,59237:36603,59238:36587,59239:36606,59240:36618,59241:36613,59242:36629,59243:36626,59244:36633,59245:36627,59246:36636,59247:36639,59248:36635,59249:36620,59250:36646,59251:36659,59252:36667,59253:36665,59254:36677,59255:36674,59256:36670,59257:36684,59258:36681,59259:36678,59260:36686,59261:36695,59262:36700,59264:36706,59265:36707,59266:36708,59267:36764,59268:36767,59269:36771,59270:36781,59271:36783,59272:36791,59273:36826,59274:36837,59275:36834,59276:36842,59277:36847,59278:36999,59279:36852,59280:36869,59281:36857,59282:36858,59283:36881,59284:36885,59285:36897,59286:36877,59287:36894,59288:36886,59289:36875,59290:36903,59291:36918,59292:36917,59293:36921,59294:36856,59295:36943,59296:36944,59297:36945,59298:36946,59299:36878,59300:36937,59301:36926,59302:36950,59303:36952,59304:36958,59305:36968,59306:36975,59307:36982,59308:38568,59309:36978,59310:36994,59311:36989,59312:36993,59313:36992,59314:37002,59315:37001,59316:37007,59317:37032,59318:37039,59319:37041,59320:37045,59321:37090,59322:37092,59323:25160,59324:37083,59325:37122,59326:37138,59327:37145,59328:37170,59329:37168,59330:37194,59331:37206,59332:37208,59333:37219,59334:37221,59335:37225,59336:37235,59337:37234,59338:37259,59339:37257,59340:37250,59341:37282,59342:37291,59343:37295,59344:37290,59345:37301,59346:37300,59347:37306,59348:37312,59349:37313,59350:37321,59351:37323,59352:37328,59353:37334,59354:37343,59355:37345,59356:37339,59357:37372,59358:37365,59359:37366,59360:37406,59361:37375,59362:37396,59363:37420,59364:37397,59365:37393,59366:37470,59367:37463,59368:37445,59369:37449,59370:37476,59371:37448,59372:37525,59373:37439,59374:37451,59375:37456,59376:37532,59377:37526,59378:37523,59379:37531,59380:37466,59381:37583,59382:37561,59383:37559,59384:37609,59385:37647,59386:37626,59387:37700,59388:37678,59456:37657,59457:37666,59458:37658,59459:37667,59460:37690,59461:37685,59462:37691,59463:37724,59464:37728,59465:37756,59466:37742,59467:37718,59468:37808,59469:37804,59470:37805,59471:37780,59472:37817,59473:37846,59474:37847,59475:37864,59476:37861,59477:37848,59478:37827,59479:37853,59480:37840,59481:37832,59482:37860,59483:37914,59484:37908,59485:37907,59486:37891,59487:37895,59488:37904,59489:37942,59490:37931,59491:37941,59492:37921,59493:37946,59494:37953,59495:37970,59496:37956,59497:37979,59498:37984,59499:37986,59500:37982,59501:37994,59502:37417,59503:38e3,59504:38005,59505:38007,59506:38013,59507:37978,59508:38012,59509:38014,59510:38017,59511:38015,59512:38274,59513:38279,59514:38282,59515:38292,59516:38294,59517:38296,59518:38297,59520:38304,59521:38312,59522:38311,59523:38317,59524:38332,59525:38331,59526:38329,59527:38334,59528:38346,59529:28662,59530:38339,59531:38349,59532:38348,59533:38357,59534:38356,59535:38358,59536:38364,59537:38369,59538:38373,59539:38370,59540:38433,59541:38440,59542:38446,59543:38447,59544:38466,59545:38476,59546:38479,59547:38475,59548:38519,59549:38492,59550:38494,59551:38493,59552:38495,59553:38502,59554:38514,59555:38508,59556:38541,59557:38552,59558:38549,59559:38551,59560:38570,59561:38567,59562:38577,59563:38578,59564:38576,59565:38580,59566:38582,59567:38584,59568:38585,59569:38606,59570:38603,59571:38601,59572:38605,59573:35149,59574:38620,59575:38669,59576:38613,59577:38649,59578:38660,59579:38662,59580:38664,59581:38675,59582:38670,59583:38673,59584:38671,59585:38678,59586:38681,59587:38692,59588:38698,59589:38704,59590:38713,59591:38717,59592:38718,59593:38724,59594:38726,59595:38728,59596:38722,59597:38729,59598:38748,59599:38752,59600:38756,59601:38758,59602:38760,59603:21202,59604:38763,59605:38769,59606:38777,59607:38789,59608:38780,59609:38785,59610:38778,59611:38790,59612:38795,59613:38799,59614:38800,59615:38812,59616:38824,59617:38822,59618:38819,59619:38835,59620:38836,59621:38851,59622:38854,59623:38856,59624:38859,59625:38876,59626:38893,59627:40783,59628:38898,59629:31455,59630:38902,59631:38901,59632:38927,59633:38924,59634:38968,59635:38948,59636:38945,59637:38967,59638:38973,59639:38982,59640:38991,59641:38987,59642:39019,59643:39023,59644:39024,59712:39025,59713:39028,59714:39027,59715:39082,59716:39087,59717:39089,59718:39094,59719:39108,59720:39107,59721:39110,59722:39145,59723:39147,59724:39171,59725:39177,59726:39186,59727:39188,59728:39192,59729:39201,59730:39197,59731:39198,59732:39204,59733:39200,59734:39212,59735:39214,59736:39229,59737:39230,59738:39234,59739:39241,59740:39237,59741:39248,59742:39243,59743:39249,59744:39250,59745:39244,59746:39253,59747:39319,59748:39320,59749:39333,59750:39341,59751:39342,59752:39356,59753:39391,59754:39387,59755:39389,59756:39384,59757:39377,59758:39405,59759:39406,59760:39409,59761:39410,59762:39419,59763:39416,59764:39425,59765:39439,59766:39429,59767:39394,59768:39449,59769:39467,59770:39479,59771:39493,59772:39490,59773:39488,59774:39491,59776:39486,59777:39509,59778:39501,59779:39515,59780:39511,59781:39519,59782:39522,59783:39525,59784:39524,59785:39529,59786:39531,59787:39530,59788:39597,59789:39600,59790:39612,59791:39616,59792:39631,59793:39633,59794:39635,59795:39636,59796:39646,59797:39647,59798:39650,59799:39651,59800:39654,59801:39663,59802:39659,59803:39662,59804:39668,59805:39665,59806:39671,59807:39675,59808:39686,59809:39704,59810:39706,59811:39711,59812:39714,59813:39715,59814:39717,59815:39719,59816:39720,59817:39721,59818:39722,59819:39726,59820:39727,59821:39730,59822:39748,59823:39747,59824:39759,59825:39757,59826:39758,59827:39761,59828:39768,59829:39796,59830:39827,59831:39811,59832:39825,59833:39830,59834:39831,59835:39839,59836:39840,59837:39848,59838:39860,59839:39872,59840:39882,59841:39865,59842:39878,59843:39887,59844:39889,59845:39890,59846:39907,59847:39906,59848:39908,59849:39892,59850:39905,59851:39994,59852:39922,59853:39921,59854:39920,59855:39957,59856:39956,59857:39945,59858:39955,59859:39948,59860:39942,59861:39944,59862:39954,59863:39946,59864:39940,59865:39982,59866:39963,59867:39973,59868:39972,59869:39969,59870:39984,59871:40007,59872:39986,59873:40006,59874:39998,59875:40026,59876:40032,59877:40039,59878:40054,59879:40056,59880:40167,59881:40172,59882:40176,59883:40201,59884:40200,59885:40171,59886:40195,59887:40198,59888:40234,59889:40230,59890:40367,59891:40227,59892:40223,59893:40260,59894:40213,59895:40210,59896:40257,59897:40255,59898:40254,59899:40262,59900:40264,59968:40285,59969:40286,59970:40292,59971:40273,59972:40272,59973:40281,59974:40306,59975:40329,59976:40327,59977:40363,59978:40303,59979:40314,59980:40346,59981:40356,59982:40361,59983:40370,59984:40388,59985:40385,59986:40379,59987:40376,59988:40378,59989:40390,59990:40399,59991:40386,59992:40409,59993:40403,59994:40440,59995:40422,59996:40429,59997:40431,59998:40445,59999:40474,6e4:40475,60001:40478,60002:40565,60003:40569,60004:40573,60005:40577,60006:40584,60007:40587,60008:40588,60009:40594,60010:40597,60011:40593,60012:40605,60013:40613,60014:40617,60015:40632,60016:40618,60017:40621,60018:38753,60019:40652,60020:40654,60021:40655,60022:40656,60023:40660,60024:40668,60025:40670,60026:40669,60027:40672,60028:40677,60029:40680,60030:40687,60032:40692,60033:40694,60034:40695,60035:40697,60036:40699,60037:40700,60038:40701,60039:40711,60040:40712,60041:30391,60042:40725,60043:40737,60044:40748,60045:40766,60046:40778,60047:40786,60048:40788,60049:40803,60050:40799,60051:40800,60052:40801,60053:40806,60054:40807,60055:40812,60056:40810,60057:40823,60058:40818,60059:40822,60060:40853,60061:40860,60062:40864,60063:22575,60064:27079,60065:36953,60066:29796,60067:20956,60068:29081}},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(1),c=r(2);e.decode=function(o,e){var r=new Uint8ClampedArray(o.length);r.set(o);for(var s=new t.default(285,256,0),a=new c.default(s,r),d=new Uint8ClampedArray(e),n=!1,l=0;l<e;l++){var i=a.evaluateAt(s.exp(l+s.generatorBase));d[d.length-1-l]=i,0!==i&&(n=!0)}if(!n)return r;var B=new c.default(s,d),k=function(o,e,r,t){var c;e.degree()<r.degree()&&(e=(c=[r,e])[0],r=c[1]);for(var s=e,a=r,d=o.zero,n=o.one;a.degree()>=t/2;){var l=s,i=d;if(d=n,(s=a).isZero())return null;a=l;for(var B=o.zero,k=s.getCoefficient(s.degree()),u=o.inverse(k);a.degree()>=s.degree()&&!a.isZero();){var C=a.degree()-s.degree(),m=o.multiply(a.getCoefficient(a.degree()),u);B=B.addOrSubtract(o.buildMonomial(C,m)),a=a.addOrSubtract(s.multiplyByMonomial(C,m))}if(n=B.multiplyPoly(d).addOrSubtract(i),a.degree()>=s.degree())return null}var f=n.getCoefficient(0);if(0===f)return null;var w=o.inverse(f);return[n.multiply(w),a.multiply(w)]}(s,s.buildMonomial(e,1),B,e);if(null===k)return null;var u=function(o,e){var r=e.degree();if(1===r)return[e.getCoefficient(1)];for(var t=new Array(r),c=0,s=1;s<o.size&&c<r;s++)0===e.evaluateAt(s)&&(t[c]=o.inverse(s),c++);return c!==r?null:t}(s,k[0]);if(null==u)return null;for(var C=function(o,e,r){for(var c=r.length,s=new Array(c),a=0;a<c;a++){for(var d=o.inverse(r[a]),n=1,l=0;l<c;l++)a!==l&&(n=o.multiply(n,t.addOrSubtractGF(1,o.multiply(r[l],d))));s[a]=o.multiply(e.evaluateAt(d),o.inverse(n)),0!==o.generatorBase&&(s[a]=o.multiply(s[a],d))}return s}(s,k[1],u),m=0;m<u.length;m++){var f=r.length-1-s.log(u[m]);if(f<0)return null;r[f]=t.addOrSubtractGF(r[f],C[m])}return r}},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0}),e.VERSIONS=[{infoBits:null,versionNumber:1,alignmentPatternCenters:[],errorCorrectionLevels:[{ecCodewordsPerBlock:7,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:19}]},{ecCodewordsPerBlock:10,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:16}]},{ecCodewordsPerBlock:13,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:13}]},{ecCodewordsPerBlock:17,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:9}]}]},{infoBits:null,versionNumber:2,alignmentPatternCenters:[6,18],errorCorrectionLevels:[{ecCodewordsPerBlock:10,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:34}]},{ecCodewordsPerBlock:16,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:28}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:22}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:16}]}]},{infoBits:null,versionNumber:3,alignmentPatternCenters:[6,22],errorCorrectionLevels:[{ecCodewordsPerBlock:15,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:55}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:44}]},{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:17}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:13}]}]},{infoBits:null,versionNumber:4,alignmentPatternCenters:[6,26],errorCorrectionLevels:[{ecCodewordsPerBlock:20,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:80}]},{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:32}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:24}]},{ecCodewordsPerBlock:16,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:9}]}]},{infoBits:null,versionNumber:5,alignmentPatternCenters:[6,30],errorCorrectionLevels:[{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:108}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:43}]},{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:15},{numBlocks:2,dataCodewordsPerBlock:16}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:11},{numBlocks:2,dataCodewordsPerBlock:12}]}]},{infoBits:null,versionNumber:6,alignmentPatternCenters:[6,34],errorCorrectionLevels:[{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:68}]},{ecCodewordsPerBlock:16,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:27}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:19}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:15}]}]},{infoBits:31892,versionNumber:7,alignmentPatternCenters:[6,22,38],errorCorrectionLevels:[{ecCodewordsPerBlock:20,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:78}]},{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:31}]},{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:14},{numBlocks:4,dataCodewordsPerBlock:15}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:13},{numBlocks:1,dataCodewordsPerBlock:14}]}]},{infoBits:34236,versionNumber:8,alignmentPatternCenters:[6,24,42],errorCorrectionLevels:[{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:97}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:38},{numBlocks:2,dataCodewordsPerBlock:39}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:18},{numBlocks:2,dataCodewordsPerBlock:19}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:14},{numBlocks:2,dataCodewordsPerBlock:15}]}]},{infoBits:39577,versionNumber:9,alignmentPatternCenters:[6,26,46],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:116}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:36},{numBlocks:2,dataCodewordsPerBlock:37}]},{ecCodewordsPerBlock:20,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:16},{numBlocks:4,dataCodewordsPerBlock:17}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:12},{numBlocks:4,dataCodewordsPerBlock:13}]}]},{infoBits:42195,versionNumber:10,alignmentPatternCenters:[6,28,50],errorCorrectionLevels:[{ecCodewordsPerBlock:18,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:68},{numBlocks:2,dataCodewordsPerBlock:69}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:43},{numBlocks:1,dataCodewordsPerBlock:44}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:19},{numBlocks:2,dataCodewordsPerBlock:20}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:15},{numBlocks:2,dataCodewordsPerBlock:16}]}]},{infoBits:48118,versionNumber:11,alignmentPatternCenters:[6,30,54],errorCorrectionLevels:[{ecCodewordsPerBlock:20,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:81}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:50},{numBlocks:4,dataCodewordsPerBlock:51}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:22},{numBlocks:4,dataCodewordsPerBlock:23}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:12},{numBlocks:8,dataCodewordsPerBlock:13}]}]},{infoBits:51042,versionNumber:12,alignmentPatternCenters:[6,32,58],errorCorrectionLevels:[{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:92},{numBlocks:2,dataCodewordsPerBlock:93}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:36},{numBlocks:2,dataCodewordsPerBlock:37}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:20},{numBlocks:6,dataCodewordsPerBlock:21}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:7,dataCodewordsPerBlock:14},{numBlocks:4,dataCodewordsPerBlock:15}]}]},{infoBits:55367,versionNumber:13,alignmentPatternCenters:[6,34,62],errorCorrectionLevels:[{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:107}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:8,dataCodewordsPerBlock:37},{numBlocks:1,dataCodewordsPerBlock:38}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:8,dataCodewordsPerBlock:20},{numBlocks:4,dataCodewordsPerBlock:21}]},{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:12,dataCodewordsPerBlock:11},{numBlocks:4,dataCodewordsPerBlock:12}]}]},{infoBits:58893,versionNumber:14,alignmentPatternCenters:[6,26,46,66],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:115},{numBlocks:1,dataCodewordsPerBlock:116}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:40},{numBlocks:5,dataCodewordsPerBlock:41}]},{ecCodewordsPerBlock:20,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:16},{numBlocks:5,dataCodewordsPerBlock:17}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:12},{numBlocks:5,dataCodewordsPerBlock:13}]}]},{infoBits:63784,versionNumber:15,alignmentPatternCenters:[6,26,48,70],errorCorrectionLevels:[{ecCodewordsPerBlock:22,ecBlocks:[{numBlocks:5,dataCodewordsPerBlock:87},{numBlocks:1,dataCodewordsPerBlock:88}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:5,dataCodewordsPerBlock:41},{numBlocks:5,dataCodewordsPerBlock:42}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:5,dataCodewordsPerBlock:24},{numBlocks:7,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:12},{numBlocks:7,dataCodewordsPerBlock:13}]}]},{infoBits:68472,versionNumber:16,alignmentPatternCenters:[6,26,50,74],errorCorrectionLevels:[{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:5,dataCodewordsPerBlock:98},{numBlocks:1,dataCodewordsPerBlock:99}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:7,dataCodewordsPerBlock:45},{numBlocks:3,dataCodewordsPerBlock:46}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:15,dataCodewordsPerBlock:19},{numBlocks:2,dataCodewordsPerBlock:20}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:15},{numBlocks:13,dataCodewordsPerBlock:16}]}]},{infoBits:70749,versionNumber:17,alignmentPatternCenters:[6,30,54,78],errorCorrectionLevels:[{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:107},{numBlocks:5,dataCodewordsPerBlock:108}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:10,dataCodewordsPerBlock:46},{numBlocks:1,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:22},{numBlocks:15,dataCodewordsPerBlock:23}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:14},{numBlocks:17,dataCodewordsPerBlock:15}]}]},{infoBits:76311,versionNumber:18,alignmentPatternCenters:[6,30,56,82],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:5,dataCodewordsPerBlock:120},{numBlocks:1,dataCodewordsPerBlock:121}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:9,dataCodewordsPerBlock:43},{numBlocks:4,dataCodewordsPerBlock:44}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:22},{numBlocks:1,dataCodewordsPerBlock:23}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:14},{numBlocks:19,dataCodewordsPerBlock:15}]}]},{infoBits:79154,versionNumber:19,alignmentPatternCenters:[6,30,58,86],errorCorrectionLevels:[{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:113},{numBlocks:4,dataCodewordsPerBlock:114}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:44},{numBlocks:11,dataCodewordsPerBlock:45}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:21},{numBlocks:4,dataCodewordsPerBlock:22}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:9,dataCodewordsPerBlock:13},{numBlocks:16,dataCodewordsPerBlock:14}]}]},{infoBits:84390,versionNumber:20,alignmentPatternCenters:[6,34,62,90],errorCorrectionLevels:[{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:107},{numBlocks:5,dataCodewordsPerBlock:108}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:41},{numBlocks:13,dataCodewordsPerBlock:42}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:15,dataCodewordsPerBlock:24},{numBlocks:5,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:15,dataCodewordsPerBlock:15},{numBlocks:10,dataCodewordsPerBlock:16}]}]},{infoBits:87683,versionNumber:21,alignmentPatternCenters:[6,28,50,72,94],errorCorrectionLevels:[{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:116},{numBlocks:4,dataCodewordsPerBlock:117}]},{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:42}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:22},{numBlocks:6,dataCodewordsPerBlock:23}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:19,dataCodewordsPerBlock:16},{numBlocks:6,dataCodewordsPerBlock:17}]}]},{infoBits:92361,versionNumber:22,alignmentPatternCenters:[6,26,50,74,98],errorCorrectionLevels:[{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:111},{numBlocks:7,dataCodewordsPerBlock:112}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:46}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:7,dataCodewordsPerBlock:24},{numBlocks:16,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:24,ecBlocks:[{numBlocks:34,dataCodewordsPerBlock:13}]}]},{infoBits:96236,versionNumber:23,alignmentPatternCenters:[6,30,54,74,102],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:121},{numBlocks:5,dataCodewordsPerBlock:122}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:47},{numBlocks:14,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:24},{numBlocks:14,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:16,dataCodewordsPerBlock:15},{numBlocks:14,dataCodewordsPerBlock:16}]}]},{infoBits:102084,versionNumber:24,alignmentPatternCenters:[6,28,54,80,106],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:117},{numBlocks:4,dataCodewordsPerBlock:118}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:45},{numBlocks:14,dataCodewordsPerBlock:46}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:24},{numBlocks:16,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:30,dataCodewordsPerBlock:16},{numBlocks:2,dataCodewordsPerBlock:17}]}]},{infoBits:102881,versionNumber:25,alignmentPatternCenters:[6,32,58,84,110],errorCorrectionLevels:[{ecCodewordsPerBlock:26,ecBlocks:[{numBlocks:8,dataCodewordsPerBlock:106},{numBlocks:4,dataCodewordsPerBlock:107}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:8,dataCodewordsPerBlock:47},{numBlocks:13,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:7,dataCodewordsPerBlock:24},{numBlocks:22,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:22,dataCodewordsPerBlock:15},{numBlocks:13,dataCodewordsPerBlock:16}]}]},{infoBits:110507,versionNumber:26,alignmentPatternCenters:[6,30,58,86,114],errorCorrectionLevels:[{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:10,dataCodewordsPerBlock:114},{numBlocks:2,dataCodewordsPerBlock:115}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:19,dataCodewordsPerBlock:46},{numBlocks:4,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:28,dataCodewordsPerBlock:22},{numBlocks:6,dataCodewordsPerBlock:23}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:33,dataCodewordsPerBlock:16},{numBlocks:4,dataCodewordsPerBlock:17}]}]},{infoBits:110734,versionNumber:27,alignmentPatternCenters:[6,34,62,90,118],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:8,dataCodewordsPerBlock:122},{numBlocks:4,dataCodewordsPerBlock:123}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:22,dataCodewordsPerBlock:45},{numBlocks:3,dataCodewordsPerBlock:46}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:8,dataCodewordsPerBlock:23},{numBlocks:26,dataCodewordsPerBlock:24}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:12,dataCodewordsPerBlock:15},{numBlocks:28,dataCodewordsPerBlock:16}]}]},{infoBits:117786,versionNumber:28,alignmentPatternCenters:[6,26,50,74,98,122],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:117},{numBlocks:10,dataCodewordsPerBlock:118}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:3,dataCodewordsPerBlock:45},{numBlocks:23,dataCodewordsPerBlock:46}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:24},{numBlocks:31,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:15},{numBlocks:31,dataCodewordsPerBlock:16}]}]},{infoBits:119615,versionNumber:29,alignmentPatternCenters:[6,30,54,78,102,126],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:7,dataCodewordsPerBlock:116},{numBlocks:7,dataCodewordsPerBlock:117}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:21,dataCodewordsPerBlock:45},{numBlocks:7,dataCodewordsPerBlock:46}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:1,dataCodewordsPerBlock:23},{numBlocks:37,dataCodewordsPerBlock:24}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:19,dataCodewordsPerBlock:15},{numBlocks:26,dataCodewordsPerBlock:16}]}]},{infoBits:126325,versionNumber:30,alignmentPatternCenters:[6,26,52,78,104,130],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:5,dataCodewordsPerBlock:115},{numBlocks:10,dataCodewordsPerBlock:116}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:19,dataCodewordsPerBlock:47},{numBlocks:10,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:15,dataCodewordsPerBlock:24},{numBlocks:25,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:23,dataCodewordsPerBlock:15},{numBlocks:25,dataCodewordsPerBlock:16}]}]},{infoBits:127568,versionNumber:31,alignmentPatternCenters:[6,30,56,82,108,134],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:13,dataCodewordsPerBlock:115},{numBlocks:3,dataCodewordsPerBlock:116}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:46},{numBlocks:29,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:42,dataCodewordsPerBlock:24},{numBlocks:1,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:23,dataCodewordsPerBlock:15},{numBlocks:28,dataCodewordsPerBlock:16}]}]},{infoBits:133589,versionNumber:32,alignmentPatternCenters:[6,34,60,86,112,138],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:115}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:10,dataCodewordsPerBlock:46},{numBlocks:23,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:10,dataCodewordsPerBlock:24},{numBlocks:35,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:19,dataCodewordsPerBlock:15},{numBlocks:35,dataCodewordsPerBlock:16}]}]},{infoBits:136944,versionNumber:33,alignmentPatternCenters:[6,30,58,86,114,142],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:115},{numBlocks:1,dataCodewordsPerBlock:116}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:14,dataCodewordsPerBlock:46},{numBlocks:21,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:29,dataCodewordsPerBlock:24},{numBlocks:19,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:11,dataCodewordsPerBlock:15},{numBlocks:46,dataCodewordsPerBlock:16}]}]},{infoBits:141498,versionNumber:34,alignmentPatternCenters:[6,34,62,90,118,146],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:13,dataCodewordsPerBlock:115},{numBlocks:6,dataCodewordsPerBlock:116}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:14,dataCodewordsPerBlock:46},{numBlocks:23,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:44,dataCodewordsPerBlock:24},{numBlocks:7,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:59,dataCodewordsPerBlock:16},{numBlocks:1,dataCodewordsPerBlock:17}]}]},{infoBits:145311,versionNumber:35,alignmentPatternCenters:[6,30,54,78,102,126,150],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:12,dataCodewordsPerBlock:121},{numBlocks:7,dataCodewordsPerBlock:122}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:12,dataCodewordsPerBlock:47},{numBlocks:26,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:39,dataCodewordsPerBlock:24},{numBlocks:14,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:22,dataCodewordsPerBlock:15},{numBlocks:41,dataCodewordsPerBlock:16}]}]},{infoBits:150283,versionNumber:36,alignmentPatternCenters:[6,24,50,76,102,128,154],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:121},{numBlocks:14,dataCodewordsPerBlock:122}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:6,dataCodewordsPerBlock:47},{numBlocks:34,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:46,dataCodewordsPerBlock:24},{numBlocks:10,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:2,dataCodewordsPerBlock:15},{numBlocks:64,dataCodewordsPerBlock:16}]}]},{infoBits:152622,versionNumber:37,alignmentPatternCenters:[6,28,54,80,106,132,158],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:17,dataCodewordsPerBlock:122},{numBlocks:4,dataCodewordsPerBlock:123}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:29,dataCodewordsPerBlock:46},{numBlocks:14,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:49,dataCodewordsPerBlock:24},{numBlocks:10,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:24,dataCodewordsPerBlock:15},{numBlocks:46,dataCodewordsPerBlock:16}]}]},{infoBits:158308,versionNumber:38,alignmentPatternCenters:[6,32,58,84,110,136,162],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:4,dataCodewordsPerBlock:122},{numBlocks:18,dataCodewordsPerBlock:123}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:13,dataCodewordsPerBlock:46},{numBlocks:32,dataCodewordsPerBlock:47}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:48,dataCodewordsPerBlock:24},{numBlocks:14,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:42,dataCodewordsPerBlock:15},{numBlocks:32,dataCodewordsPerBlock:16}]}]},{infoBits:161089,versionNumber:39,alignmentPatternCenters:[6,26,54,82,110,138,166],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:20,dataCodewordsPerBlock:117},{numBlocks:4,dataCodewordsPerBlock:118}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:40,dataCodewordsPerBlock:47},{numBlocks:7,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:43,dataCodewordsPerBlock:24},{numBlocks:22,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:10,dataCodewordsPerBlock:15},{numBlocks:67,dataCodewordsPerBlock:16}]}]},{infoBits:167017,versionNumber:40,alignmentPatternCenters:[6,30,58,86,114,142,170],errorCorrectionLevels:[{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:19,dataCodewordsPerBlock:118},{numBlocks:6,dataCodewordsPerBlock:119}]},{ecCodewordsPerBlock:28,ecBlocks:[{numBlocks:18,dataCodewordsPerBlock:47},{numBlocks:31,dataCodewordsPerBlock:48}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:34,dataCodewordsPerBlock:24},{numBlocks:34,dataCodewordsPerBlock:25}]},{ecCodewordsPerBlock:30,ecBlocks:[{numBlocks:20,dataCodewordsPerBlock:15},{numBlocks:61,dataCodewordsPerBlock:16}]}]}]},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=r(0);function c(o,e,r,t){var c=o.x-e.x+r.x-t.x,s=o.y-e.y+r.y-t.y;if(0===c&&0===s)return{a11:e.x-o.x,a12:e.y-o.y,a13:0,a21:r.x-e.x,a22:r.y-e.y,a23:0,a31:o.x,a32:o.y,a33:1};var a=e.x-r.x,d=t.x-r.x,n=e.y-r.y,l=t.y-r.y,i=a*l-d*n,B=(c*l-d*s)/i,k=(a*s-c*n)/i;return{a11:e.x-o.x+B*e.x,a12:e.y-o.y+B*e.y,a13:B,a21:t.x-o.x+k*t.x,a22:t.y-o.y+k*t.y,a23:k,a31:o.x,a32:o.y,a33:1}}e.extract=function(o,e){for(var r,s,a=(s=(k={x:e.dimension-3.5,y:3.5},u={x:e.dimension-6.5,y:e.dimension-6.5},C={x:3.5,y:e.dimension-3.5},m=void 0,{a11:(m=c({x:3.5,y:3.5},k,u,C)).a22*m.a33-m.a23*m.a32,a12:m.a13*m.a32-m.a12*m.a33,a13:m.a12*m.a23-m.a13*m.a22,a21:m.a23*m.a31-m.a21*m.a33,a22:m.a11*m.a33-m.a13*m.a31,a23:m.a13*m.a21-m.a11*m.a23,a31:m.a21*m.a32-m.a22*m.a31,a32:m.a12*m.a31-m.a11*m.a32,a33:m.a11*m.a22-m.a12*m.a21}),{a11:(r=c(e.topLeft,e.topRight,e.alignmentPattern,e.bottomLeft)).a11*s.a11+r.a21*s.a12+r.a31*s.a13,a12:r.a12*s.a11+r.a22*s.a12+r.a32*s.a13,a13:r.a13*s.a11+r.a23*s.a12+r.a33*s.a13,a21:r.a11*s.a21+r.a21*s.a22+r.a31*s.a23,a22:r.a12*s.a21+r.a22*s.a22+r.a32*s.a23,a23:r.a13*s.a21+r.a23*s.a22+r.a33*s.a23,a31:r.a11*s.a31+r.a21*s.a32+r.a31*s.a33,a32:r.a12*s.a31+r.a22*s.a32+r.a32*s.a33,a33:r.a13*s.a31+r.a23*s.a32+r.a33*s.a33}),d=t.BitMatrix.createEmpty(e.dimension,e.dimension),n=function(o,e){var r=a.a13*o+a.a23*e+a.a33;return{x:(a.a11*o+a.a21*e+a.a31)/r,y:(a.a12*o+a.a22*e+a.a32)/r}},l=0;l<e.dimension;l++)for(var i=0;i<e.dimension;i++){var B=n(i+.5,l+.5);d.set(i,l,o.get(Math.floor(B.x),Math.floor(B.y)))}var k,u,C,m;return{matrix:d,mappingFunction:n}}},function(o,e,r){"use strict";Object.defineProperty(e,"__esModule",{value:!0});var t=function(o,e){return Math.sqrt(Math.pow(e.x-o.x,2)+Math.pow(e.y-o.y,2))};function c(o){return o.reduce(function(o,e){return o+e})}function s(o,e,r,c){var s,a,d,n,l=[{x:Math.floor(o.x),y:Math.floor(o.y)}],i=Math.abs(e.y-o.y)>Math.abs(e.x-o.x);i?(s=Math.floor(o.y),a=Math.floor(o.x),d=Math.floor(e.y),n=Math.floor(e.x)):(s=Math.floor(o.x),a=Math.floor(o.y),d=Math.floor(e.x),n=Math.floor(e.y));for(var B=Math.abs(d-s),k=Math.abs(n-a),u=Math.floor(-B/2),C=s<d?1:-1,m=a<n?1:-1,f=!0,w=s,P=a;w!==d+C;w+=C){var v=i?P:w,h=i?w:P;if(r.get(v,h)!==f&&(f=!f,l.push({x:v,y:h}),l.length===c+1))break;if((u+=k)>0){if(P===n)break;P+=m,u-=B}}for(var y=[],p=0;p<c;p++)l[p]&&l[p+1]?y.push(t(l[p],l[p+1])):y.push(0);return y}function a(o,e,r,t){var c,a=e.y-o.y,d=e.x-o.x,n=s(o,e,r,Math.ceil(t/2)),l=s(o,{x:o.x-d,y:o.y-a},r,Math.ceil(t/2)),i=n.shift()+l.shift()-1;return(c=l.concat(i)).concat.apply(c,n)}function d(o,e){var r=c(o)/c(e),t=0;return e.forEach(function(e,c){t+=Math.pow(o[c]-e*r,2)}),{averageSize:r,error:t}}function n(o,e,r){try{var t=a(o,{x:-1,y:o.y},r,e.length),c=a(o,{x:o.x,y:-1},r,e.length),s=a(o,{x:Math.max(0,o.x-o.y)-1,y:Math.max(0,o.y-o.x)-1},r,e.length),n=a(o,{x:Math.min(r.width,o.x+o.y)+1,y:Math.min(r.height,o.y+o.x)+1},r,e.length),l=d(t,e),i=d(c,e),B=d(s,e),k=d(n,e),u=Math.sqrt(l.error*l.error+i.error*i.error+B.error*B.error+k.error*k.error),C=(l.averageSize+i.averageSize+B.averageSize+k.averageSize)/4;return u+(Math.pow(l.averageSize-C,2)+Math.pow(i.averageSize-C,2)+Math.pow(B.averageSize-C,2)+Math.pow(k.averageSize-C,2))/C}catch(o){return 1/0}}function l(o,e){for(var r=Math.round(e.x);o.get(r,Math.round(e.y));)r--;for(var t=Math.round(e.x);o.get(t,Math.round(e.y));)t++;for(var c=(r+t)/2,s=Math.round(e.y);o.get(Math.round(c),s);)s--;for(var a=Math.round(e.y);o.get(Math.round(c),a);)a++;return{x:c,y:(s+a)/2}}function i(o,e,r,s,d){var l,i,B;try{l=function(o,e,r,s){var d=(c(a(o,r,s,5))/7+c(a(o,e,s,5))/7+c(a(r,o,s,5))/7+c(a(e,o,s,5))/7)/4;if(d<1)throw new Error("Invalid module size");var n=Math.round(t(o,e)/d),l=Math.round(t(o,r)/d),i=Math.floor((n+l)/2)+7;switch(i%4){case 0:i++;break;case 2:i--}return{dimension:i,moduleSize:d}}(s,r,d,o),i=l.dimension,B=l.moduleSize}catch(o){return null}var k=r.x-s.x+d.x,u=r.y-s.y+d.y,C=(t(s,d)+t(s,r))/2/B,m=1-3/C,f={x:s.x+m*(k-s.x),y:s.y+m*(u-s.y)},w=e.map(function(e){var r=(e.top.startX+e.top.endX+e.bottom.startX+e.bottom.endX)/4,s=(e.top.y+e.bottom.y+1)/2;if(o.get(Math.floor(r),Math.floor(s)))return c([e.top.endX-e.top.startX,e.bottom.endX-e.bottom.startX,e.bottom.y-e.top.y+1]),{x:r,y:s,score:n({x:Math.floor(r),y:Math.floor(s)},[1,1,1],o)+t({x:r,y:s},f)}}).filter(function(o){return!!o}).sort(function(o,e){return o.score-e.score});return{alignmentPattern:C>=15&&w.length?w[0]:f,dimension:i}}e.locate=function(o){for(var e=[],r=[],s=[],a=[],d=function(t){for(var d=0,n=!1,l=[0,0,0,0,0],i=function(e){var s=o.get(e,t);if(s===n)d++;else{l=[l[1],l[2],l[3],l[4],d],d=1,n=s;var i=c(l)/7,B=Math.abs(l[0]-i)<i&&Math.abs(l[1]-i)<i&&Math.abs(l[2]-3*i)<3*i&&Math.abs(l[3]-i)<i&&Math.abs(l[4]-i)<i&&!s,k=c(l.slice(-3))/3,u=Math.abs(l[2]-k)<k&&Math.abs(l[3]-k)<k&&Math.abs(l[4]-k)<k&&s;if(B){var C=e-l[3]-l[4],m=C-l[2],f={startX:m,endX:C,y:t};(w=r.filter(function(o){return m>=o.bottom.startX&&m<=o.bottom.endX||C>=o.bottom.startX&&m<=o.bottom.endX||m<=o.bottom.startX&&C>=o.bottom.endX&&l[2]/(o.bottom.endX-o.bottom.startX)<1.5&&l[2]/(o.bottom.endX-o.bottom.startX)>.5})).length>0?w[0].bottom=f:r.push({top:f,bottom:f})}if(u){var w,P=e-l[4],v=P-l[3];f={startX:v,y:t,endX:P},(w=a.filter(function(o){return v>=o.bottom.startX&&v<=o.bottom.endX||P>=o.bottom.startX&&v<=o.bottom.endX||v<=o.bottom.startX&&P>=o.bottom.endX&&l[2]/(o.bottom.endX-o.bottom.startX)<1.5&&l[2]/(o.bottom.endX-o.bottom.startX)>.5})).length>0?w[0].bottom=f:a.push({top:f,bottom:f})}}},B=-1;B<=o.width;B++)i(B);e.push.apply(e,r.filter(function(o){return o.bottom.y!==t&&o.bottom.y-o.top.y>=2})),r=r.filter(function(o){return o.bottom.y===t}),s.push.apply(s,a.filter(function(o){return o.bottom.y!==t})),a=a.filter(function(o){return o.bottom.y===t})},B=0;B<=o.height;B++)d(B);e.push.apply(e,r.filter(function(o){return o.bottom.y-o.top.y>=2})),s.push.apply(s,a);var k=e.filter(function(o){return o.bottom.y-o.top.y>=2}).map(function(e){var r=(e.top.startX+e.top.endX+e.bottom.startX+e.bottom.endX)/4,t=(e.top.y+e.bottom.y+1)/2;if(o.get(Math.round(r),Math.round(t))){var s=[e.top.endX-e.top.startX,e.bottom.endX-e.bottom.startX,e.bottom.y-e.top.y+1],a=c(s)/s.length;return{score:n({x:Math.round(r),y:Math.round(t)},[1,1,3,1,1],o),x:r,y:t,size:a}}}).filter(function(o){return!!o}).sort(function(o,e){return o.score-e.score}).map(function(o,e,r){if(e>4)return null;var t=r.filter(function(o,r){return e!==r}).map(function(e){return{x:e.x,y:e.y,score:e.score+Math.pow(e.size-o.size,2)/o.size,size:e.size}}).sort(function(o,e){return o.score-e.score});if(t.length<2)return null;var c=o.score+t[0].score+t[1].score;return{points:[o].concat(t.slice(0,2)),score:c}}).filter(function(o){return!!o}).sort(function(o,e){return o.score-e.score});if(0===k.length)return null;var u=function(o,e,r){var c,s,a,d,n,l,i,B=t(o,e),k=t(e,r),u=t(o,r);return k>=B&&k>=u?(n=(c=[e,o,r])[0],l=c[1],i=c[2]):u>=k&&u>=B?(n=(s=[o,e,r])[0],l=s[1],i=s[2]):(n=(a=[o,r,e])[0],l=a[1],i=a[2]),(i.x-l.x)*(n.y-l.y)-(i.y-l.y)*(n.x-l.x)<0&&(n=(d=[i,n])[0],i=d[1]),{bottomLeft:n,topLeft:l,topRight:i}}(k[0].points[0],k[0].points[1],k[0].points[2]),C=u.topRight,m=u.topLeft,f=u.bottomLeft,w=i(o,s,C,m,f),P=[];w&&P.push({alignmentPattern:{x:w.alignmentPattern.x,y:w.alignmentPattern.y},bottomLeft:{x:f.x,y:f.y},dimension:w.dimension,topLeft:{x:m.x,y:m.y},topRight:{x:C.x,y:C.y}});var v=l(o,C),h=l(o,m),y=l(o,f),p=i(o,s,v,h,y);return p&&P.push({alignmentPattern:{x:p.alignmentPattern.x,y:p.alignmentPattern.y},bottomLeft:{x:y.x,y:y.y},topLeft:{x:h.x,y:h.y},topRight:{x:v.x,y:v.y},dimension:p.dimension}),0===P.length?null:P}}]).default},"object"==typeof exports&&"object"==typeof module?module.exports=e():"function"==typeof define&&define.amd?define([],e):"object"==typeof exports?exports.jsQR=e():o.jsQR=e();"""


_PHONECAM_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>cam-ui &middot; c&aacute;mara del m&oacute;vil</title>
<style>
html,body{margin:0;height:100%;background:#000;color:#ddd;font-family:system-ui,sans-serif;overflow:hidden}
#stage{position:relative;width:100%;height:100vh;display:flex;align-items:center;justify-content:center;background:#000}
video{width:100%;height:100%;object-fit:contain;background:#000}
#bar{position:absolute;left:0;right:0;bottom:0;display:none;gap:10px;justify-content:center;
     padding:12px;background:linear-gradient(transparent,rgba(0,0,0,.65))}
button{background:#2b2f36;color:#eee;border:1px solid #4a4f58;border-radius:7px;
       padding:10px 18px;font-size:15px;cursor:pointer}
button:active{background:#3a3f48}
button:disabled{opacity:.6}
#status{position:absolute;top:0;left:0;right:0;padding:10px 14px;font-size:14px;text-align:center;
        background:linear-gradient(rgba(0,0,0,.65),transparent);color:#ddd}
#status.err{color:#ffd9dc}
#status.ok{color:#bdf5c5}
#big{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);max-width:82%;
     text-align:center;font-size:15px;line-height:1.5;background:#20242b;
     border:1px solid #4a4f58;border-radius:10px;padding:18px 22px}
#big button{margin-top:14px}
</style></head>
<body>
<div id="stage">
  <video id="v" autoplay playsinline muted></video>
  <div id="status">Esta p&aacute;gina convierte la c&aacute;mara de este m&oacute;vil en una
    c&aacute;mara m&aacute;s del mosaico de cam-ui.</div>
  <div id="big">
    <div>Al tocar «Empezar» se pedir&aacute; permiso de c&aacute;mara; en cuanto lo des,
    aparecer&aacute;s solo en el mosaico de cam-ui, sin tocar nada m&aacute;s all&iacute;.
    Mant&eacute;n esta p&aacute;gina abierta mientras quieras seguir emitiendo.</div>
    <button id="start">&#9654;&#65039; Empezar</button>
  </div>
  <div id="bar">
    <button id="flip">&#128260; Cambiar c&aacute;mara</button>
    <button id="stop">&#9208;&#65039; Detener</button>
  </div>
</div>
<script>
var sid = "__PHONECAM_SID__";
var v = document.getElementById("v");
var statusEl = document.getElementById("status");
var big = document.getElementById("big");
var bar = document.getElementById("bar");
var canvas = document.createElement("canvas");
var ctx2d = canvas.getContext("2d");
var stream = null, timer = null, sending = false, facing = "environment", wakeLock = null;

// Guarda el secreto de este móvil (distinto del "sid" de esta sesión, y del token, que cambia
// cada vez que se enciende "Cámara del móvil") para que la próxima vez que se abra "/add" en
// este mismo móvil, cam-ui lo reconozca solo y no haga falta volver a leer ningún QR -- ver
// PhoneCamHub.add_device y el script de _PHONECAM_ADD_HTML.
(function () {
  var dev = "__PHONECAM_DEV_SECRET__";
  if (dev) {
    try { localStorage.setItem("camui_dev", dev); } catch (e) {}
  }
})();

function setStatus(txt, cls) {
  statusEl.textContent = txt;
  statusEl.className = cls || "";
}

function sendFrame() {
  if (sending || !stream || v.videoWidth === 0) { return; }
  canvas.width = v.videoWidth;
  canvas.height = v.videoHeight;
  ctx2d.drawImage(v, 0, 0, canvas.width, canvas.height);
  canvas.toBlob(function (blob) {
    if (!blob) { return; }
    sending = true;
    fetch("/cam/ingest/" + sid, {method: "POST", body: blob})
      .then(function (r) {
        setStatus(r.ok ? "Enviando a cam-ui ✅" : "cam-ui no aceptó el fotograma (" + r.status + ")",
                   r.ok ? "ok" : "err");
      })
      .catch(function () { setStatus("Sin conexión con cam-ui ahora mismo…", "err"); })
      .finally(function () { sending = false; });
  }, "image/jpeg", __PHONECAM_JPEG_QUALITY__);
}

function startCapture() {
  navigator.mediaDevices.getUserMedia({
    video: {facingMode: facing, width: {ideal: 1920}, height: {ideal: 1080},
           frameRate: {ideal: __PHONECAM_FPS__}},
    audio: false
  }).then(function (s) {
    stream = s;
    v.srcObject = s;
    big.style.display = "none";
    bar.style.display = "flex";
    setStatus("Conectado — enviando a cam-ui…", "ok");
    if (timer) { clearInterval(timer); }
    timer = setInterval(sendFrame, __PHONECAM_INTERVAL_MS__);
    if (navigator.wakeLock) {
      navigator.wakeLock.request("screen").then(function (wl) { wakeLock = wl; }).catch(function () {});
    }
  }).catch(function (err) {
    setStatus("No se pudo usar la cámara: " + err.message, "err");
  });
}

function stopCapture() {
  if (timer) { clearInterval(timer); timer = null; }
  if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
  if (wakeLock) { wakeLock.release().catch(function () {}); wakeLock = null; }
  v.srcObject = null;
  big.style.display = "block";
  bar.style.display = "none";
  setStatus("Detenido. Toca «Empezar» para seguir.");
}

document.getElementById("start").onclick = function () {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("Este navegador no deja usar la cámara aquí.", "err");
    return;
  }
  startCapture();
};
document.getElementById("stop").onclick = stopCapture;
document.getElementById("flip").onclick = function () {
  facing = (facing === "environment") ? "user" : "environment";
  if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); startCapture(); }
};
document.addEventListener("visibilitychange", function () {
  if (document.visibilityState === "visible" && stream && !wakeLock && navigator.wakeLock) {
    navigator.wakeLock.request("screen").then(function (wl) { wakeLock = wl; }).catch(function () {});
  }
});
</script>
</body></html>
"""
_PHONECAM_HTML = _PHONECAM_HTML.replace(
    "__PHONECAM_INTERVAL_MS__", str(int(round(1000.0 / PHONECAM_FPS)))
).replace("__PHONECAM_JPEG_QUALITY__", str(PHONECAM_JPEG_QUALITY)
).replace("__PHONECAM_FPS__", str(PHONECAM_FPS)).encode("utf-8")


# ---- «/add»: la puerta de entrada fácil de escribir a mano (dirección fija, sin ningún código
# al final) para emparejar un móvil nuevo. Al entrar, la propia página pide la cámara del móvil
# -- no para mandar vídeo todavía, sino para leer con ella un código QR que, en cuanto se da ese
# permiso, cam-ui enciende en su propia ventana (ver CamApp._phonecam_show_pair_qr); ese QR es de
# un solo uso y lleva de verdad el token+sid de siempre (ver _PhoneCamHandler), así que lo único
# que de verdad protege que no se añada cualquiera de la wifi sigue siendo lo mismo que antes:
# hace falta estar delante de la pantalla del PC para verlo. En cuanto el móvil lo lee (con su
# propia cámara, por dentro de esta misma página, sin salir de ella), navega solo a la URL que
# llevaba dentro -- esa URL es «/cam?t=...&sid=...», la misma página de captura de siempre
# (_PHONECAM_HTML), sin cambiar nada de cómo manda los fotogramas.
_PHONECAM_ADD_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>cam-ui &middot; a&ntilde;adir c&aacute;mara</title>
<style>
html,body{margin:0;height:100%;background:#000;color:#ddd;font-family:system-ui,sans-serif;overflow:hidden}
#stage{position:relative;width:100%;height:100vh;display:flex;align-items:center;justify-content:center;background:#000}
video{width:100%;height:100%;object-fit:contain;background:#000}
button{background:#2b2f36;color:#eee;border:1px solid #4a4f58;border-radius:7px;
       padding:10px 18px;font-size:15px;cursor:pointer}
button:active{background:#3a3f48}
#status{position:absolute;top:0;left:0;right:0;padding:10px 14px;font-size:14px;text-align:center;
        background:linear-gradient(rgba(0,0,0,.32),transparent);color:#ddd;
        text-shadow:0 1px 3px rgba(0,0,0,.9)}
#status.err{color:#ffd9dc}
#status.ok{color:#bdf5c5}
#retry{display:none;position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);max-width:82%;
     text-align:center;font-size:15px;line-height:1.5;background:#20242b;
     border:1px solid #4a4f58;border-radius:10px;padding:18px 22px}
</style></head>
<body>
<div id="stage">
  <video id="v" autoplay playsinline muted></video>
  <div id="status">Pidiendo permiso de c&aacute;mara&hellip;</div>
  <div id="retry">
    <button id="retrybtn">&#128247; Reintentar</button>
  </div>
</div>
<script>
__JSQR_JS_PLACEHOLDER__
(function () {
  var v = document.getElementById("v");
  var statusEl = document.getElementById("status");
  var retry = document.getElementById("retry");
  var canvas = document.createElement("canvas");
  var ctx2d = canvas.getContext("2d", {willReadFrequently: true});
  var scanning = false, readySent = false, timer = null;

  function setStatus(txt, cls) {
    statusEl.textContent = txt;
    statusEl.className = cls || "";
  }

  function scanOnce() {
    if (!scanning || v.videoWidth === 0) { return; }
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    ctx2d.drawImage(v, 0, 0, canvas.width, canvas.height);
    var img;
    try { img = ctx2d.getImageData(0, 0, canvas.width, canvas.height); } catch (e) { return; }
    var code = null;
    try { code = jsQR(img.data, img.width, img.height); } catch (e) {}
    if (code && code.data && /^https:\\/\\//i.test(code.data) && code.data.indexOf("/cam?") !== -1) {
      scanning = false;
      if (timer) { clearInterval(timer); timer = null; }
      setStatus("¡Código leído! Conectando…", "ok");
      window.location.href = code.data;
    }
  }

  function startCamera() {
    retry.style.display = "none";
    setStatus("Pidiendo permiso de cámara…");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Este navegador no deja usar la cámara aquí.", "err");
      retry.style.display = "block";
      return;
    }
    navigator.mediaDevices.getUserMedia({
      video: {facingMode: "environment", width: {ideal: 1280}, height: {ideal: 720}}, audio: false
    }).then(function (stream) {
      v.srcObject = stream;
      setStatus("Busca con la cámara el código QR que va a aparecer en el ordenador…", "ok");
      scanning = true;
      timer = setInterval(scanOnce, __PHONECAM_ADD_SCAN_MS__);
      if (!readySent) {
        readySent = true;
        fetch("/add/ready", {method: "POST"}).catch(function () {
          setStatus("No se pudo avisar a cam-ui; comprueba que sigue abierto.", "err");
        });
      }
    }).catch(function (err) {
      setStatus("No se pudo usar la cámara: " + err.message, "err");
      retry.style.display = "block";
    });
  }

  document.getElementById("retrybtn").onclick = startCamera;

  // Si este móvil ya se emparejó antes con este mismo cam-ui, tiene guardado su propio secreto
  // (ver _PHONECAM_HTML / PhoneCamHub.add_device) -- se intenta reconectar solo con él antes de
  // pedir la cámara para leer un QR nuevo. Si el PC no lo reconoce (p.ej. se "olvidó" desde
  // Ajustes, o es la configuración de otro cam-ui distinto), se cae al camino de siempre.
  var dev = null;
  try { dev = localStorage.getItem("camui_dev"); } catch (e) {}
  if (dev) {
    setStatus("Reconectando (ya emparejado antes)…");
    fetch("/add/reconnect?dev=" + encodeURIComponent(dev), {method: "POST"})
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok && j.url) {
          window.location.href = j.url;
        } else {
          try { localStorage.removeItem("camui_dev"); } catch (e2) {}
          startCamera();
        }
      })
      .catch(function () { startCamera(); });
  } else {
    startCamera();   // primera vez: se pide directamente al cargar, sin tocar nada antes
  }
})();
</script>
</body></html>
"""
_PHONECAM_ADD_HTML = _PHONECAM_ADD_HTML.replace(
    "__JSQR_JS_PLACEHOLDER__", _JSQR_JS
).replace("__PHONECAM_ADD_SCAN_MS__", str(int(round(1000.0 / PHONECAM_FPS)))).encode("utf-8")


def _phonecam_load_devices():
    """Móviles ya emparejados antes (ver el comentario junto a PHONECAM_DEVICES_FILE). Se lee
    de disco en cada llamada, a propósito -- es un archivo pequeño y así "olvidar" uno desde
    Ajustes se nota al instante, sin tener que mantener ninguna copia en memoria sincronizada."""
    try:
        with open(PHONECAM_DEVICES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _phonecam_save_devices(devices):
    try:
        with open(PHONECAM_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("no se pudo guardar la lista de móviles recordados: %r", e)


class PhoneCamHub:
    """Recibe, por HTTPS, los fotogramas JPEG que manda el navegador de un móvil (ver
    _PHONECAM_HTML) y los vuelve a publicar en un segundo servidor -- este solo en 127.0.0.1,
    invisible desde la wifi -- como si fuera una cámara de red MJPEG de toda la vida. Cada móvil
    que se conecta a «/cam» (con el token correcto) es una sesión nueva; en cuanto manda su
    primer fotograma de verdad, se anota en new_cam_q para que CamApp la añada como cámara (eso
    ocurre en el hilo de Tk, nunca aquí: estos servidores corren en sus propios hilos).

    pair_q: igual que new_cam_q pero para el emparejamiento por «/add» -- cuando un móvil que
    está ahí pide el permiso de cámara (para leer un QR, ver _PHONECAM_ADD_HTML), se anota aquí
    la URL de una sesión nueva (todavía sin ningún fotograma) para que CamApp muestre su QR."""

    def __init__(self, new_cam_q, pair_q):
        self._new_cam_q = new_cam_q
        self._pair_q = pair_q
        self._lk = threading.Lock()
        self._sessions = {}             # sid -> {"name", "jpeg", "ts", "gen", "announced"}
        self.token = None
        self.port = None
        self._https_srv = None
        self._local_srv = None
        self._cert_srv = None           # HTTP normal (sin cifrar): ver _PhoneCamCertHandler

    @property
    def active(self):
        return self._https_srv is not None

    @property
    def local_port(self):
        return self._local_srv.server_address[1] if self._local_srv is not None else None

    @property
    def cert_port(self):
        return self._cert_srv.server_address[1] if self._cert_srv is not None else None

    def n_sessions(self):
        cutoff = time.time() - PHONECAM_STALE_SECONDS
        with self._lk:
            return sum(1 for s in self._sessions.values() if s["ts"] and s["ts"] >= cutoff)

    def start(self, port=PHONECAM_PORT_DEFAULT):
        """Lanza los dos servidores. Devuelve (puerto_https, token). OSError si el puerto
        (o ninguno de los siguientes 20) está libre."""
        if self.active:
            return self.port, self.token
        cert, key = _phonecam_ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self.token = secrets.token_urlsafe(16)
        with self._lk:
            self._sessions = {}
        local_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PhoneCamLocalHandler)
        local_srv.hub = self
        threading.Thread(target=local_srv.serve_forever, daemon=True).start()
        https_srv, last_err, p = None, None, int(port)
        for _ in range(20):
            try:
                https_srv = http.server.ThreadingHTTPServer(("0.0.0.0", p), _PhoneCamHandler)
                break
            except OSError as e:
                last_err = e
                p += 1
        if https_srv is None:
            local_srv.shutdown()
            local_srv.server_close()
            raise last_err
        https_srv.socket = ctx.wrap_socket(https_srv.socket, server_side=True)
        https_srv.hub = self
        threading.Thread(target=https_srv.serve_forever, daemon=True).start()
        # servidor aparte, en HTTP normal (sin cifrar) y visible en la wifi: solo sirve la CA
        # local de cam-ui para que un iPhone/iPad pueda instalarla como perfil de confianza (ver
        # PhoneCamCertDialog) -- tiene que ser HTTP sin más, nunca HTTPS, porque antes de
        # instalar esa CA el propio certificado de arriba todavía no es de fiar para el
        # dispositivo: es el mismo problema del huevo y la gallina que resuelve mitmproxy con
        # su "http://mitm.it". No es sensible (una CA es pública por diseño; su clave privada
        # nunca sale de aquí), así que no hace falta ningún token para llegar a ella.
        cert_srv = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _PhoneCamCertHandler)
        cert_srv.hub = self
        threading.Thread(target=cert_srv.serve_forever, daemon=True).start()
        self._local_srv, self._https_srv, self._cert_srv, self.port = (
            local_srv, https_srv, cert_srv, p)
        return self.port, self.token

    def stop(self):
        for srv in (self._https_srv, self._local_srv, self._cert_srv):
            if srv is not None:
                try:
                    srv.shutdown()
                    srv.server_close()
                except Exception:
                    pass
        self._https_srv = self._local_srv = self._cert_srv = self.port = self.token = None
        with self._lk:
            self._sessions = {}

    def new_session(self, name):
        sid = secrets.token_urlsafe(9)
        with self._lk:
            used = {s["name"] for s in self._sessions.values()}
            nm, n = name, 2
            while nm in used:
                nm = "{0} #{1}".format(name, n)
                n += 1
            self._sessions[sid] = {"name": nm, "jpeg": None, "ts": 0.0, "gen": 0, "announced": False}
        return sid

    def feed(self, sid, data):
        """True si se aceptó (sesión válida). La primera vez que una sesión manda un fotograma
        de verdad, se anota en new_cam_q -- exactamente una vez por sesión."""
        announce = None
        with self._lk:
            s = self._sessions.get(sid)
            if s is None:
                return False
            s["jpeg"], s["ts"], s["gen"] = data, time.time(), s["gen"] + 1
            if not s["announced"]:
                s["announced"] = True
                announce = s["name"]
        if announce is not None:
            local_url = "http://127.0.0.1:{0}/phonecam/{1}.mjpg".format(self.local_port, sid)
            self._new_cam_q.append((announce, local_url))
        return True

    def latest(self, sid):
        with self._lk:
            s = self._sessions.get(sid)
            return None if s is None else (s["jpeg"], s["gen"], s["ts"])

    def session_name(self, sid):
        with self._lk:
            s = self._sessions.get(sid)
            return s["name"] if s else "Móvil"

    # ---- dispositivos ya emparejados antes (ver el comentario junto a PHONECAM_DEVICES_FILE) --
    # a diferencia de self._sessions (en memoria, se olvida al apagar «Cámara del móvil»), esto
    # vive en disco y sobrevive a apagar/encender e incluso a cerrar cam-ui del todo.

    def add_device(self, secret, name):
        with self._lk:
            devices = _phonecam_load_devices()
            devices[secret] = {"name": name, "added": time.time()}
            _phonecam_save_devices(devices)

    def known_device(self, secret):
        if not secret:
            return None
        with self._lk:
            return _phonecam_load_devices().get(secret)

    def forget_device(self, secret):
        with self._lk:
            devices = _phonecam_load_devices()
            if secret not in devices:
                return False
            del devices[secret]
            _phonecam_save_devices(devices)
            return True

    def list_devices(self):
        with self._lk:
            devices = _phonecam_load_devices()
        return sorted(devices.items(), key=lambda kv: kv[1].get("added", 0) if isinstance(kv[1], dict) else 0)

    def drop_stale(self):
        """A ratos (como hls_idle_check): olvida del todo las sesiones caducadas hace mucho,
        para no acumular móviles que se cerraron o cambiaron de wifi hace rato."""
        cutoff = time.time() - PHONECAM_SESSION_MAX_AGE
        with self._lk:
            dead = [sid for sid, s in self._sessions.items() if s["ts"] and s["ts"] < cutoff]
            for sid in dead:
                del self._sessions[sid]


class _PhoneCamHandler(http.server.BaseHTTPRequestHandler):
    """Servidor HTTPS, en la wifi: sirve la página de captura (solo con el token correcto en
    «?t=»), la página de emparejamiento «/add» (sin token: por sí sola no añade nada, ver más
    abajo) y recibe los fotogramas que manda el móvil."""

    def log_message(self, *_a):
        pass

    @staticmethod
    def _parse_qs(qs):
        out = {}
        for part in qs.split("&"):
            k, _, v = part.partition("=")
            if k:
                out[k] = v
        return out

    def do_GET(self):
        hub = self.server.hub
        path, _, qs = self.path.partition("?")
        if path == "/add":
            # dirección fija, fácil de teclear a mano -- no hace falta ningún token para
            # llegar hasta aquí porque, por sí sola, esta página no añade ninguna cámara: solo
            # pide el permiso de cámara del móvil para leer el QR que de verdad emparejará (ver
            # /add/ready más abajo, y el comentario junto a _PHONECAM_ADD_HTML).
            self._send(200, _PHONECAM_ADD_HTML, "text/html; charset=utf-8", cache=False)
            return
        if path != "/cam":
            self._send(404, b"", "text/plain")
            return
        params = self._parse_qs(qs)
        token = params.get("t", "")
        if not hub.token or token != hub.token:
            self._send(403, "Dirección no válida.".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return
        # «sid»: la sesión que ya se acuñó en /add/ready y que el QR llevaba dentro -- se
        # reutiliza esa (así el PC sabe que es justo el móvil que leyó su QR); sin «sid», o si
        # ya caducó, se acuña una sesión nueva (compatibilidad con el enlace de toda la vida,
        # copiado o guardado a mano, sin pasar por «/add»).
        sid = params.get("sid", "")
        if not sid or hub.latest(sid) is None:
            sid = hub.new_session("Móvil")
        # «dev»: el secreto persistente de este móvil (ver PhoneCamHub.add_device). Si ya viene
        # puesto y es uno que de verdad conocemos (vino de /add/reconnect, un móvil que ya se
        # había emparejado antes), se reutiliza tal cual. Si no (primera vez, recién leído el QR
        # en /add), se acuña uno nuevo y se guarda ya mismo -- así la próxima vez que este mismo
        # móvil abra «/add», entrará solo, sin tener que volver a leer nada.
        dev = params.get("dev", "")
        if not dev or hub.known_device(dev) is None:
            dev = secrets.token_urlsafe(24)
            hub.add_device(dev, hub.session_name(sid))
        body = (_PHONECAM_HTML.replace(b"__PHONECAM_SID__", sid.encode("ascii"))
                              .replace(b"__PHONECAM_DEV_SECRET__", dev.encode("ascii")))
        self._send(200, body, "text/html; charset=utf-8", cache=False)

    def do_POST(self):
        hub = self.server.hub
        path, _, qs = self.path.partition("?")
        if path == "/add/ready":
            # el móvil que está en /add ya tiene permiso de su cámara (para leer el QR, no
            # para mandar vídeo todavía): se acuña una sesión nueva y se avisa a CamApp para
            # que muestre su QR -- en cuanto ese QR se lea, el propio móvil navegará solo a
            # la URL que lleva dentro (/cam?t=...&sid=...), la misma página de captura de
            # siempre, sin nada más que hacer aquí.
            sid = hub.new_session("Móvil")
            ips = [ip for ip in _local_ips() if ip]
            host = ips[0] if ips else socket.gethostname()
            pair_url = "https://{0}:{1}/cam?t={2}&sid={3}".format(host, hub.port, hub.token, sid)
            hub._pair_q.append(pair_url)
            self._send(200, b"", "text/plain")
            return
        if path == "/add/reconnect":
            # el propio script de /add (ver _PHONECAM_ADD_HTML) manda aquí el secreto que ese
            # móvil guardó la primera vez que se emparejó (ver PhoneCamHub.add_device, en /cam).
            # Si se reconoce, se acuña una sesión nueva YA mismo -- atada al token de ESTA
            # puesta en marcha de «Cámara del móvil», que es nuevo cada vez -- y se devuelve la
            # URL final de golpe: sin QR, sin que nadie tenga que mirar la pantalla del PC. Si
            # no se reconoce (se "olvidó" desde Ajustes, o es otro cam-ui distinto), {"ok":
            # false} hace que ese mismo script caiga solo al camino de siempre.
            dev = self._parse_qs(qs).get("dev", "")
            info = hub.known_device(dev)
            if info is None:
                self._send(200, b'{"ok": false}', "application/json; charset=utf-8", cache=False)
                return
            sid = hub.new_session(info.get("name", "Móvil"))
            url = "/cam?t={0}&sid={1}&dev={2}".format(hub.token, sid, dev)
            body = json.dumps({"ok": True, "url": url}).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", cache=False)
            return
        if not path.startswith("/cam/ingest/"):
            self._send(404, b"", "text/plain")
            return
        sid = path[len("/cam/ingest/"):]
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > PHONECAM_JPEG_MAX:
            self._send(413, b"", "text/plain")
            return
        try:
            data = self.rfile.read(n)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        ok = hub.feed(sid, data)
        self._send(200 if ok else 404, b"", "text/plain")

    def _send(self, code, body, ctype, cache=True):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class _PhoneCamLocalHandler(http.server.BaseHTTPRequestHandler):
    """Servidor solo en 127.0.0.1 (nunca en la wifi): aquí es donde ffmpeg va a buscar el
    vídeo de cada móvil conectado, exactamente igual que pediría el de cualquier otra cámara
    de red (ver _net_video_input) -- por eso basta con servir un MJPEG normal y corriente."""

    def log_message(self, *_a):
        pass

    def do_GET(self):
        hub = self.server.hub
        path = self.path.split("?", 1)[0]
        if not (path.startswith("/phonecam/") and path.endswith(".mjpg")):
            try:
                self.send_response(404)
                self.end_headers()
            except Exception:
                pass
            return
        sid = path[len("/phonecam/"):-len(".mjpg")]
        if hub.latest(sid) is None:          # sesión que no existe (o ya olvidada): 404 de verdad,
            try:                              # antes de comprometerse a un 200 -- así un ffmpeg
                self.send_response(404)       # que reintente ve un fallo claro, no un 200 vacío
                self.end_headers()
            except Exception:
                pass
            return
        boundary = "camuiphone"
        try:
            self.send_response(200)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + boundary)
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        last = None
        try:
            while True:
                r = hub.latest(sid)
                if r is None:
                    break
                jpeg, gen, ts = r
                if jpeg is None or time.time() - ts > PHONECAM_STALE_SECONDS:
                    break                              # el móvil dejó de mandar: "Sin señal"
                if gen == last:
                    time.sleep(0.05)
                    continue
                last = gen
                self.wfile.write(b"--" + boundary.encode() + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(("Content-Length: " + str(len(jpeg)) + "\r\n\r\n").encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass


_PHONECAM_CERT_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Certificado de cam-ui</title>
<style>
body{background:#16181c;color:#e8e8e8;font:16px/1.5 -apple-system,"Segoe UI",sans-serif;
    max-width:460px;margin:32px auto;padding:0 20px}
h1{font-size:20px}
a.btn{display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;
      padding:10px 18px;border-radius:8px;font-weight:600;margin:12px 0}
.muted{color:#8d949e;font-size:14px}
ol{padding-left:20px}
li{margin-bottom:10px}
</style></head>
<body>
<h1>Certificado de cam-ui</h1>
<p>Esto hace falta <strong>solo en iPhone/iPad</strong> para que Safari deje usar la cámara del
móvil con «Cámara del móvil». <strong>En Android no hace falta nada de esto</strong> -- con
«Avanzado → Continuar» en el aviso del navegador ya funciona.</p>
<p><a class="btn" href="/certificado">⬇ Instalar certificado</a></p>
<ol>
<li>Toca el enlace de arriba y luego «Permitir» / «Instalar». Puede avisar de que el perfil «no
está firmado» o que su autenticidad «no se puede verificar» -- es normal aquí: lo genera tu
propio ordenador, no hace falta que lo firme nadie más. Sigue con «Instalar» (puede pedirlo dos
veces).</li>
<li>Ve a Ajustes → General → Información → Confianza de certificados, y activa el interruptor
junto a «cam-ui (red local)».</li>
</ol>
<p class="muted">Con esto hecho una sola vez, no hace falta repetirlo -- ni aunque el ordenador
cambie de red.</p>
</body></html>
"""
_PHONECAM_CERT_PAGE = _PHONECAM_CERT_PAGE.encode("utf-8")


class _PhoneCamCertHandler(http.server.BaseHTTPRequestHandler):
    """Servidor HTTP normal (sin cifrar) y visible en la wifi, aparte del de arriba: sirve la CA
    local de cam-ui para que un iPhone/iPad pueda instalarla como perfil de confianza (ver el
    comentario junto a PhoneCamHub.start y PhoneCamCertDialog, más abajo). Tiene que ser HTTP
    normal a propósito -- antes de instalar esa CA, el certificado HTTPS de siempre todavía no es
    de fiar para el dispositivo, así que servir esto por HTTPS sería el mismo problema que
    intenta resolver (el huevo y la gallina); mitmproxy resuelve esto exactamente igual, con su
    «http://mitm.it»."""

    def log_message(self, *_a):
        pass

    def do_GET(self):
        hub = self.server.hub
        path = self.path.split("?", 1)[0]
        if path == "/certificado":
            try:
                from cryptography import x509
                from cryptography.hazmat.primitives.serialization import Encoding
                with open(PHONECAM_CA_CERT_FILE, "rb") as f:
                    pem = f.read()
                der = x509.load_pem_x509_certificate(pem).public_bytes(Encoding.DER)
            except Exception:
                self._send(404, b"", "text/plain")
                return
            # application/x-x509-ca-cert: el tipo que hace que Safari, al descargarlo, ofrezca
            # "Instalar perfil" en vez de un archivo suelto cualquiera (mismo tipo que usa
            # mitmproxy para esto mismo, ver mitmproxy/addons/onboardingapp)
            self._send(200, der, "application/x-x509-ca-cert", cache=False)
            return
        self._send(200, _PHONECAM_CERT_PAGE, "text/html; charset=utf-8", cache=False)

    def _send(self, code, body, ctype, cache=True):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class FFmpegEngine:
    def __init__(self, ffmpeg=None):
        self.ffmpeg = ffmpeg or FFMPEG
        self.proc = None
        self.lock = threading.Lock()
        self.err = deque(maxlen=60)
        self.latest = None
        self.frame_count = 0
        self.levels = [-90.0, -90.0]
        self.startup_time = 2.5      # s hasta el primer fotograma (se mide)
        self.meta = {}
        self.cmd = []
        self.outfile = None
        self.last_rc = None
        self._spawn_t = 0.0
        self._threads = []
        self._nlog = 0
        self._nmeter = 0
        self.in_sizes = {}           # índice de entrada -> (ancho, alto) que informa ffmpeg
        self.in_fps = {}             # ... y los fps que anuncia
        self._in_output = False
        self._cmdq = None
        self._released = 0.0         # instante en que se cerró el último ffmpeg
        self._abort = False          # el botón de pánico cancela los reintentos en curso
        self.vcam = None             # VirtualCam que recibe la salida RGB (o None)
        self.ipsrv = None            # IpServer que recibe la salida en red (o None)

    # -- comandos en vivo (ffmpeg los lee por stdin: "c<filtro> -1 <orden> <valor>") --
    def send_filter_commands(self, cmds):
        """cmds: [(filtro, orden, valor)]. Se aplican sin reiniciar (brillo, color…)."""
        q = self._cmdq
        if q is None or not self.is_running():
            return False
        for target, name, value in cmds:
            q.put("c{0} -1 {1} {2}\n".format(target, name, value).encode("ascii", "ignore"))
        return True

    def _cmd_writer(self, p, q):
        while True:
            data = q.get()
            if data is None:
                return
            try:
                p.stdin.write(data)
                p.stdin.flush()
                log.debug("cmd> %s", data.decode("ascii", "ignore").strip())
            except Exception:
                return

    # -- arranque --
    def start(self, plan, outfile=None, warmup=0.0, input_fn=None, retries=1):
        self.stop()
        last = ""
        attempt = 0
        while True:
            if self._abort:
                return False, "cancelado"
            gap = RELEASE_GAP - (time.time() - self._released)
            if gap > 0:
                time.sleep(gap)            # las cámaras USB tardan en soltarse
            if self._abort:
                return False, "cancelado"
            self.err.clear()
            self.latest = None
            self.frame_count = 0
            self.levels = [-90.0, -90.0]
            if plan.get("vcam") and self.vcam is not None and self.vcam.active:
                plan["vcam"]["port"] = self.vcam.arm()       # un socket nuevo en cada intento
            else:
                plan.pop("vcam", None)
            if plan.get("ipout") and self.ipsrv is not None and self.ipsrv.active:
                plan["ipout"]["port"] = self.ipsrv.arm(      # un socket nuevo en cada intento,
                    plan["ipout"].get("w"), plan["ipout"].get("h"))   # con el tamaño de ESTE mosaico
                plan["ipout"]["aport"] = self.ipsrv.arm_audio()  # igual, para el sonido (si hay)
            else:
                plan.pop("ipout", None)
            cmd, meta = build_command(plan, outfile, warmup, time.time(), True,
                                      input_fn, self.ffmpeg)
            self.cmd, self.meta, self.outfile = cmd, meta, outfile
            self._nlog = 0
            log.info("ffmpeg intento %d: %s", attempt + 1, subprocess.list2cmdline(cmd))
            try:
                p = _popen_big(cmd)
            except Exception as e:
                log.error("No se pudo lanzar ffmpeg: %r", e)
                return False, "No se pudo lanzar ffmpeg: {0}".format(e)
            self.proc = p
            self._spawn_t = time.time()
            self.in_sizes = {}
            self.in_fps = {}
            self._in_output = False
            self._cmdq = queue.Queue()
            te = threading.Thread(target=self._drain_err, args=(p,), daemon=True)
            tr = threading.Thread(target=self._read_frames, args=(p, meta["pw"], meta["ph"]),
                                  daemon=True)
            tc = threading.Thread(target=self._cmd_writer, args=(p, self._cmdq), daemon=True)
            self._threads = [te, tr, tc]
            te.start()
            tr.start()
            tc.start()
            t_end = time.time() + 1.6
            while time.time() < t_end and p.poll() is None:
                time.sleep(0.1)
            if p.poll() is None:
                return True, ""
            te.join(1.0)
            last = "\n".join(list(self.err)[-8:])
            log.error("ffmpeg terminó al iniciar (rc=%s):\n%s", p.returncode, last)
            self._close(p)
            self.proc = None
            self._released = time.time()
            busy = is_busy_error(last)
            limit = len(BUSY_WAITS) if busy else retries
            if attempt >= limit:
                break
            time.sleep(BUSY_WAITS[min(attempt, len(BUSY_WAITS) - 1)] if busy else 0.6)
            attempt += 1
        return False, last or "ffmpeg terminó al iniciar."

    def _drain_err(self, p):
        try:
            for raw in iter(p.stderr.readline, b""):
                s = raw.decode("utf-8", "ignore").rstrip()
                if not s:
                    continue
                m = _METER_RE.search(s)
                if m:
                    self._nmeter += 1
                    if self._nmeter <= 5 or self._nmeter % 100 == 0:
                        log.debug("vumetro #%d: canal %s = %s", self._nmeter, m.group(1), m.group(2))
                    ch = int(m.group(1)) - 1
                    try:
                        v = float(m.group(2))
                    except ValueError:
                        v = -90.0
                    if v != v or v < -90.0:
                        v = -90.0
                    if 0 <= ch < 2:
                        self.levels[ch] = min(0.0, v)
                    continue
                if "Parsed_ametadata" in s or s.startswith("Enter command:"):
                    continue
                if s.startswith("Command reply"):
                    log.debug("ffmpeg> %s", s)
                    continue
                if _OUTPUT_RE.match(s):
                    self._in_output = True
                elif not self._in_output:
                    mv = _VSTREAM_RE.match(s)
                    if mv:
                        self.in_sizes[int(mv.group(1))] = (int(mv.group(2)), int(mv.group(3)))
                        mf = _VFPS_RE.search(s)
                        if mf:
                            self.in_fps[int(mv.group(1))] = float(mf.group(1))
                self.err.append(s)
                if self._nlog < 400:
                    self._nlog += 1
                    log.debug("ffmpeg> %s", s)
        except Exception:
            pass

    def _read_frames(self, p, w, h):
        fb = w * h * 3
        buf = bytearray(fb)
        mv = memoryview(buf)
        pos = 0
        while True:
            try:
                n = p.stdout.readinto(mv[pos:])
            except Exception:
                break
            if not n:
                break
            pos += n
            if pos >= fb:
                arr = np.frombuffer(buf, np.uint8).reshape((h, w, 3))
                buf = bytearray(fb)           # el fotograma entregado ya no se toca
                mv = memoryview(buf)
                with self.lock:
                    self.latest = arr
                    self.frame_count += 1
                    if self.frame_count == 1:
                        self.startup_time = time.time() - self._spawn_t
                pos = 0

    @staticmethod
    def _close(p):
        for st in (p.stdout, p.stderr, p.stdin):
            try:
                if st:
                    st.close()
            except Exception:
                pass

    # -- consulta --
    def get_latest(self):
        with self.lock:
            return self.latest, self.frame_count

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def last_errors(self, n=8):
        return "\n".join(list(self.err)[-n:])

    # -- parada limpia: 'q' y esperar a que ffmpeg cierre el MP4 --
    def stop(self, timeout_wait=20.0):
        p = self.proc
        self.proc = None
        if p is None:
            return True
        graceful = False
        q, self._cmdq = self._cmdq, None
        if q is not None:
            q.put(None)
        try:
            if p.poll() is None and p.stdin:
                p.stdin.write(b"q")
                p.stdin.flush()
        except Exception:
            pass
        try:
            p.wait(timeout=timeout_wait)     # los hilos lectores siguen vaciando las tuberías
            graceful = True
        except Exception:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        for t in self._threads:
            t.join(2.0)
        self._threads = []
        self.last_rc = p.poll()
        log.info("engine.stop: graceful=%s rc=%s", graceful, self.last_rc)
        self._close(p)
        self._released = time.time()
        if self.vcam is not None:
            self.vcam._drop_srv()
        if self.ipsrv is not None:
            self.ipsrv._drop_srv()
            self.ipsrv._drop_asrv()
        with self.lock:
            self.latest = None
        self.levels = [-90.0, -90.0]
        return graceful


# =====================================================================
#  GUI
# =====================================================================
BG, PANEL, PANEL2 = "#16181c", "#20232a", "#2a2e36"
FG, MUTED, LINE = "#e8e8e8", "#8d949e", "#3a3f48"
ACC, REC_RED, OK_GREEN, WARN = "#3b82f6", "#d9363e", "#2ea043", "#e3b341"


def _pair_label(pairs, value, default=None):
    for lab, val in pairs:
        if val == value:
            return lab
    return default if default is not None else pairs[0][0]


def tile_value(label):
    """Texto del combo de celda -> valor guardado: uno de la lista, o cualquier «ancho:alto» escrito."""
    for lab, val in TILES:
        if lab == label:
            return val
    r = parse_ratio(label)
    return (label or "").strip() if r and 0.1 < r < 10 else "auto"


def tile_label(value):
    for lab, val in TILES:
        if val == value:
            return lab
    r = parse_ratio(value)
    return str(value) if r and 0.1 < r < 10 else TILES[0][0]


def _pair_value(pairs, label, default=None):
    for lab, val in pairs:
        if lab == label:
            return val
    return default if default is not None else pairs[0][1]


def _geti(var, default=0):
    try:
        return int(float(str(var.get()).replace(",", ".")))
    except Exception:
        return default


def _clampf(v, lo, hi, default=0.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _getf(var, default=0.0):
    try:
        return float(str(var.get()).replace(",", "."))
    except Exception:
        return default


class CamState:
    """Ajustes de una cámara (variables Tk enlazadas a la interfaz).
    cb = {"change": f(*a), "frame": f(label), "color": f(label)}"""

    def __init__(self, label, cfg, cb):
        c = cfg or {}
        self.label = label
        self.use = bool(c.get("use", False))
        self.h = tk.BooleanVar(value=bool(c.get("h", False)))
        self.v = tk.BooleanVar(value=bool(c.get("v", False)))
        self.rot = tk.StringVar(value="{0}°".format(int(c.get("rot", 0)) % 360))
        self.tile = tk.StringVar(value=tile_label(c.get("tile", "auto")))
        self.zoom = tk.DoubleVar(value=_clampf(c.get("zoom", 100), 100, 600, 100))
        self.cx = tk.DoubleVar(value=_clampf(c.get("cx", 50), 0, 100, 50))
        self.cy = tk.DoubleVar(value=_clampf(c.get("cy", 50), 0, 100, 50))
        col = c.get("color") or {}
        self.color = dict((k, tk.DoubleVar(value=_clampf(col.get(k, 0), -100, 100)))
                          for k in COLOR_KEYS)
        self.delay = tk.IntVar(value=int(c.get("delay", 0)))
        self.mode = tk.StringVar(value=c.get("mode", "Auto") or "Auto")
        # los modos guardados con el lector antiguo (sin rangos ni fps intermedios) se vuelven a leer
        self.modes = list(c.get("modes", [])) if c.get("modes_v") == 2 else []
        ok = c.get("modes_ok") if isinstance(c.get("modes_ok"), dict) else {}
        self.ok_modes = {str(k): float(v) for k, v in ok.items() if isinstance(v, (int, float))}
        self.bad_modes = set(c.get("modes_bad") or [])
        self.modes_tried = False
        src = c.get("src")
        try:
            self.src = (int(src[0]), int(src[1])) if src and int(src[0]) > 0 and int(src[1]) > 0 else None
        except (TypeError, ValueError, IndexError):
            self.src = None
        for var in (self.h, self.v, self.rot, self.delay, self.mode):
            var.trace_add("write", cb["change"])
        for var in (self.tile, self.zoom, self.cx, self.cy):
            var.trace_add("write", lambda *a, lab=label: cb["frame"](lab))
        for var in self.color.values():
            var.trace_add("write", lambda *a, lab=label: cb["color"](lab))

    # -- geometría de la imagen --
    def rot_deg(self):
        try:
            return int(self.rot.get().replace("°", "") or 0) % 360
        except ValueError:
            return 0

    def src_size(self):
        """Tamaño real de la imagen (modo elegido o el que informó ffmpeg la última vez)."""
        return mode_size(self.mode.get()) or self.src

    def source_aspect(self):
        """Ancho/alto de la imagen ya girada. Sin datos todavía: 16:9 (se corrige solo al arrancar)."""
        s = self.src_size()
        a = (s[0] / float(s[1])) if s else 16 / 9.0
        return 1.0 / a if self.rot_deg() in (90, 270) else a

    def tile_ratio(self):
        """None = «como la cámara»; si no, el ancho/alto forzado de la celda."""
        return parse_ratio(tile_value(self.tile.get()))

    def tile_aspect(self):
        return self.tile_ratio() or self.source_aspect()

    def color_values(self):
        return dict((k, _getf(v)) for k, v in self.color.items())

    def to_cfg(self):
        return {"use": self.use, "h": bool(self.h.get()), "v": bool(self.v.get()),
                "rot": self.rot_deg(), "tile": tile_value(self.tile.get()),
                "zoom": round(_clampf(_getf(self.zoom, 100), 100, 600, 100), 1),
                "cx": round(_clampf(_getf(self.cx, 50), 0, 100, 50), 1),
                "cy": round(_clampf(_getf(self.cy, 50), 0, 100, 50), 1),
                "color": dict((k, round(v, 1)) for k, v in self.color_values().items()),
                "delay": _geti(self.delay), "mode": self.mode.get(), "modes": self.modes,
                "modes_v": 2, "modes_ok": dict(self.ok_modes), "modes_bad": sorted(self.bad_modes),
                "src": list(self.src) if self.src else None}

    def to_plan(self, spec):
        c = self.to_cfg()
        return {"spec": spec, "h": c["h"], "v": c["v"], "rot": c["rot"], "tile": c["tile"],
                "zoom": c["zoom"], "cx": c["cx"], "cy": c["cy"], "color": c["color"],
                "delay": c["delay"], "src": self.src_size(),
                "mode": None if c["mode"] in ("", "Auto") else c["mode"]}

    def reset(self):
        for var in (self.h, self.v):
            var.set(False)
        self.rot.set("0°")
        self.reset_frame()
        self.delay.set(0)
        self.mode.set("Auto")

    def reset_frame(self):
        self.tile.set(TILES[0][0])
        self.zoom.set(100.0)
        self.cx.set(50.0)
        self.cy.set(50.0)

    def reset_color(self):
        for v in self.color.values():
            v.set(0.0)


class AudioRow:
    def __init__(self, cfg, on_change):
        c = cfg or {}
        self.dev = tk.StringVar(value=c.get("dev", NO_AUDIO))
        self.chan = tk.StringVar(value=_pair_label(CHANS, c.get("chan", "mix")))
        self.gain = tk.IntVar(value=int(c.get("gain", 0)))
        self.delay = tk.IntVar(value=int(c.get("delay", 0)))
        for var in (self.dev, self.chan, self.gain, self.delay):
            var.trace_add("write", on_change)

    def to_cfg(self):
        return {"dev": self.dev.get(), "chan": _pair_value(CHANS, self.chan.get()),
                "gain": _geti(self.gain), "delay": max(0, _geti(self.delay))}


# =====================================================================
#  Acerca de  (visor de cámara profesional con HUD animado)
# =====================================================================
KOFI_URL = "https://ko-fi.com/jaimeluisolguinmesina"
KOFI_SHOW = "ko-fi.com/jaimeluisolguinmesina"
ABOUT_TAGLINE = "Tus cámaras y micrófonos, sincronizados y en un solo vídeo."
ABOUT_CHIPS = (("Multivista", "Encuadre por cámara", "Color en vivo"),
               ("Audio L/R", "Sincronía A/V", "Grabación segura"))
ABOUT_ASK = "Si CamWin32 te ahorra tiempo, invítame a un café y ayúdame a seguir mejorándola."
ABOUT_BTN = "☕   Invítame a un café en Ko-fi"
ABOUT_CREDIT = "Hecho con cariño por Jaime Luis Olguín Mesina"


def _hx(rgb):
    return "#{0:02x}{1:02x}{2:02x}".format(*[max(0, min(255, int(v))) for v in rgb])


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


class AboutDialog:
    """Tarjeta «Acerca de» con estética de visor de cámara: HUD, REC, timecode, lente con iris
    animado y enfoque que busca y fija. Mismo contenido y mismo Ko-fi que WidgetArena."""
    W, H = 460, 644
    VF = (18, 46, 442, 264)                     # visor
    AMBER, REC, GREEN, CYAN = (255, 176, 0), (255, 59, 48), (52, 211, 153), (79, 209, 255)

    def __init__(self, parent, on_close=None):
        self.on_close = on_close
        self.alive = True
        self.t0 = time.perf_counter()
        self._imgs = {}
        self._drag = None
        self._hover = None
        self._phase_seed = [(i * 1.37, 0.4 + 0.23 * i) for i in range(7)]
        top = self.top = tk.Toplevel(parent)
        top.withdraw()
        top.title("Acerca de CamWin32")
        top.overrideredirect(True)
        key = "#010203"
        top.configure(bg=key)
        try:
            top.attributes("-transparentcolor", key)     # esquinas redondeadas (Windows)
        except Exception:
            pass
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        import tkinter.font as tkfont
        # fuentes en píxeles (tamaño negativo): la tarjeta no se desborda con la escala de Windows
        self._fonts = {
            "mono": tkfont.Font(family="Consolas", size=-13, weight="bold"),
            "mono_s": tkfont.Font(family="Consolas", size=-11),
            "title": tkfont.Font(family="Segoe UI Semibold", size=-35),
            "tag": tkfont.Font(family="Segoe UI", size=-15),
            "chip": tkfont.Font(family="Consolas", size=-12, weight="bold"),
            "ask": tkfont.Font(family="Segoe UI Semibold", size=-15),
            "btn": tkfont.Font(family="Segoe UI Semibold", size=-17),
            "small": tkfont.Font(family="Segoe UI", size=-12),
            "x": tkfont.Font(family="Segoe UI", size=-17, weight="bold"),
        }
        cv = self.cv = tk.Canvas(top, width=self.W, height=self.H, bg=key, highlightthickness=0, bd=0)
        cv.pack()
        self._build_static()
        self._hit = {"btn": (38, 516, 422, 568), "close": (412, 12, 438, 38),
                     "link": (130, 576, 330, 594)}
        top.bind("<Escape>", lambda e: self.close())
        cv.bind("<Motion>", self._on_motion)
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        top.bind("<Destroy>", lambda e: setattr(self, "alive", False) if e.widget is top else None)
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x, y = px + max(0, (pw - self.W) // 2), py + max(0, (ph - self.H) // 2)
        except Exception:
            x, y = 200, 60
        top.geometry("{0}x{1}+{2}+{3}".format(self.W, self.H, x, y))
        top.deiconify()
        try:
            top.attributes("-alpha", 0.0)
        except Exception:
            pass
        top.lift()
        try:
            top.focus_force()
        except Exception:
            pass
        self._fade(0.0)
        self._frame()

    # ------------------------------------------------------------ imágenes prefabricadas
    def _build_static(self):
        W, H, S = self.W, self.H, 3
        rng = np.random.RandomState(5)
        # cuerpo de la cámara
        ys = np.linspace(0.0, 1.0, H * S)[:, None, None]
        arr = (np.array([24, 26, 31]) * (1 - ys) + np.array([10, 11, 14]) * ys) * np.ones((1, W * S, 1))
        body = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
        mask = Image.new("L", (W * S, H * S), 0)
        from PIL import ImageDraw, ImageFilter
        ImageDraw.Draw(mask).rounded_rectangle((3 * S, 3 * S, (W - 4) * S, (H - 4) * S),
                                               radius=24 * S, fill=255)
        body.putalpha(mask)
        d = ImageDraw.Draw(body)
        d.rounded_rectangle((3 * S, 3 * S, (W - 4) * S, (H - 4) * S), radius=24 * S,
                            outline=(58, 62, 70, 255), width=S)
        # visor: degradado con viñeta y grano
        x0, y0, x1, y1 = self.VF
        vw, vh = (x1 - x0) * S, (y1 - y0) * S
        yy, xx = np.mgrid[0:vh, 0:vw].astype(np.float32)
        yy /= vh
        xx /= vw
        base = np.array([14, 30, 44], np.float32) * (1 - yy[..., None]) + \
            np.array([30, 16, 38], np.float32) * yy[..., None]
        r = np.sqrt((xx - 0.5) ** 2 * 1.4 + (yy - 0.52) ** 2 * 2.2)
        glow = np.clip(1.0 - r * 1.5, 0, 1)[..., None] * np.array([18, 30, 40], np.float32)
        vig = np.clip(1.0 - np.clip(r - 0.35, 0, 1) * 1.15, 0.25, 1.0)[..., None]
        noise = rng.normal(0, 2.2, (vh, vw, 1)).astype(np.float32)
        vf = np.clip((base + glow) * vig + noise, 0, 255).astype(np.uint8)
        vf_img = Image.fromarray(vf).convert("RGBA")
        vmask = Image.new("L", (vw, vh), 0)
        ImageDraw.Draw(vmask).rounded_rectangle((0, 0, vw - 1, vh - 1), radius=14 * S, fill=255)
        body.paste(vf_img, (x0 * S, y0 * S), vmask)
        ImageDraw.Draw(body).rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=14 * S,
                                               outline=(8, 9, 11, 255), width=2 * S)
        self._imgs["body"] = ImageTk.PhotoImage(body.resize((W, H), Image.LANCZOS))
        self.cv.create_image(0, 0, anchor="nw", image=self._imgs["body"])
        # botón Ko-fi (normal / encima) y halo pulsante
        bx0, by0, bx1, by1 = self._hit_btn_default()
        bw, bh, T = bx1 - bx0, by1 - by0, 4
        for name, (c0, c1) in (("btn", ((255, 94, 91), (255, 154, 91))),
                               ("btn_h", ((255, 118, 115), (255, 179, 111)))):
            g = np.linspace(0, 1, bw * T)[None, :, None]
            col = np.array(c0)[None, None, :] * (1 - g) + np.array(c1)[None, None, :] * g
            col = np.ones((bh * T, 1, 1)) * col
            im = Image.fromarray(col.astype(np.uint8)).convert("RGBA")
            m = Image.new("L", (bw * T, bh * T), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, bw * T - 1, bh * T - 1), radius=bh * T // 2, fill=255)
            im.putalpha(m)
            self._imgs[name] = ImageTk.PhotoImage(im.resize((bw, bh), Image.LANCZOS))
        pad = 46
        gl = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle((pad, pad + 6, pad + bw, pad + bh + 6), radius=bh // 2,
                                             fill=(255, 94, 91, 255))
        gl = gl.filter(ImageFilter.GaussianBlur(14))
        ga = np.array(gl)
        self._glow = []
        for k in range(6):
            a = ga.copy()
            a[..., 3] = (a[..., 3] * (0.20 + 0.10 * k)).astype(np.uint8)
            self._glow.append(ImageTk.PhotoImage(Image.fromarray(a)))
        self._glow_pos = (bx0 - pad, by0 - pad)

    def _hit_btn_default(self):
        return (38, 516, 422, 568)

    # ------------------------------------------------------------ interacción
    def _hit_name(self, x, y):
        for name, (a, b, c, d) in self._hit.items():
            if a <= x <= c and b <= y <= d:
                return name
        return None

    def _on_motion(self, e):
        h = self._hit_name(e.x, e.y)
        if h != self._hover:
            self._hover = h
            self.cv.config(cursor="hand2" if h else "")

    def _on_press(self, e):
        h = self._hit_name(e.x, e.y)
        if h == "close":
            self.close()
        elif h in ("btn", "link"):
            self._open_kofi()
        else:
            self._drag = (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def _on_drag(self, e):
        if self._drag:
            self.top.geometry("+{0}+{1}".format(e.x_root - self._drag[0], e.y_root - self._drag[1]))

    def _on_release(self, _e):
        self._drag = None

    def _open_kofi(self):
        try:
            webbrowser.open(KOFI_URL)
        except Exception:
            pass

    def _fade(self, a):
        if not self.alive:
            return
        a = min(1.0, a + 0.12)
        try:
            self.top.attributes("-alpha", a)
        except Exception:
            return
        if a < 1.0:
            self.top.after(24, lambda: self._fade(a))

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.top.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close()

    def lift(self):
        try:
            self.top.deiconify()
            self.top.lift()
            self.top.focus_force()
        except Exception:
            pass

    # ------------------------------------------------------------ animación
    def _frame(self):
        if not self.alive:
            return
        try:
            self._draw(time.perf_counter() - self.t0)
        except Exception:
            log.error("about: error de dibujo\n%s", traceback.format_exc())
            return
        self.top.after(33, self._frame)

    def _draw(self, t):
        cv, F = self.cv, self._fonts
        cv.delete("dyn")
        W = self.W
        x0, y0, x1, y1 = self.VF
        cx, cy = 230, y0 + 110
        A, REC, GRN, CY = self.AMBER, self.REC, self.GREEN, self.CYAN

        def T(*a, **k):
            return cv.create_text(*a, tags="dyn", **k)

        def L(*a, **k):
            return cv.create_line(*a, tags="dyn", **k)

        def O(*a, **k):
            return cv.create_oval(*a, tags="dyn", **k)

        # ---- franja superior de la carcasa ----
        T(30, 25, text="▸ ACERCA DE", fill="#6b7380", font=F["mono_s"], anchor="w")
        hv = self._hover == "close"
        if hv:
            O(*self._hit["close"], fill="#2b2f36", outline="")
        T(425, 25, text="✕", fill="#ffffff" if hv else "#cfd3d9", font=F["x"])
        # ---- bokeh que deriva ----
        for i, (ph, sp) in enumerate(self._phase_seed):
            bx = x0 + 30 + (x1 - x0 - 60) * (0.5 + 0.46 * math.sin(t * 0.18 * sp + ph))
            by = y0 + 30 + (y1 - y0 - 60) * (0.5 + 0.42 * math.cos(t * 0.15 * sp + ph * 1.7))
            r = 10 + 7 * ((i * 5) % 4)
            O(bx - r, by - r, bx + r, by + r, outline=_hx(_mix((14, 30, 44), CY, 0.16)), width=1)
        # zona de seguridad
        L(x0 + 26, y0 + 26, x1 - 26, y0 + 26, x1 - 26, y1 - 26, x0 + 26, y1 - 26, x0 + 26, y0 + 26,
          fill="#20384a", dash=(2, 4))
        # ---- lente ----
        O(cx - 70, cy - 70, cx + 70, cy + 70, fill="#0f1114", outline="#2c3036", width=2)
        O(cx - 64, cy - 64, cx + 64, cy + 64, fill="#17191d", outline="#3b4048", width=3)
        rot = t * 0.12
        for k in range(72):                                  # anillo de enfoque estriado
            a = rot + k * math.pi / 36
            r0, r1 = 52, (58 if k % 6 else 61)
            L(cx + r0 * math.cos(a), cy + r0 * math.sin(a), cx + r1 * math.cos(a),
              cy + r1 * math.sin(a), fill="#4a5059" if k % 6 else "#aab2bd", width=1)
        O(cx - 48, cy - 48, cx + 48, cy + 48, fill="#05070a", outline="#262b32", width=2)
        O(cx - 40, cy - 40, cx + 40, cy + 40, outline="#0f2a3a", width=1)
        O(cx - 33, cy - 33, cx + 33, cy + 33, outline="#1a1030", width=1)
        # iris de 7 láminas que respira
        R = 13 + 10 * (0.5 + 0.5 * math.sin(t * 0.9))
        ar = t * 0.25
        pts = []
        for k in range(7):
            a = ar + k * 2 * math.pi / 7
            pts += [cx + R * math.cos(a), cy + R * math.sin(a)]
            o = a + 1.0
            L(cx + R * math.cos(a), cy + R * math.sin(a), cx + 44 * math.cos(o), cy + 44 * math.sin(o),
              fill="#25323d", width=2)
        cv.create_polygon(*pts, fill="#0d3550", outline=_hx(CY), width=1, tags="dyn")
        cv.create_arc(cx - 36, cy - 36, cx - 6, cy - 6, start=96, extent=62, style="arc",
                      outline="#9bdcff", width=2, tags="dyn")
        cv.create_arc(cx + 4, cy + 6, cx + 34, cy + 36, start=285, extent=48, style="arc",
                      outline="#ff8fab", width=2, tags="dyn")
        O(cx - 2, cy - 2, cx + 2, cy + 2, fill="#bfeaff", outline="")
        # ---- enfoque automático: busca y se fija ----
        ph = t % 5.0
        if ph < 3.0:
            s = 1.0 + 0.14 * (1 - ph / 3.0) * (0.6 + 0.4 * math.sin(ph * 9))
            col, lab = (255, 255, 255), "AF"
        else:
            s = 1.0 - 0.01 * math.sin((ph - 3.0) * 4)
            col, lab = GRN, "AF · LOCK"
        hw = 74 * s
        c = _hx(col)
        for sx in (-1, 1):
            for sy in (-1, 1):
                px, py = cx + sx * hw, cy + sy * hw
                L(px, py, px - sx * 15, py, fill=c, width=2)
                L(px, py, px, py - sy * 15, fill=c, width=2)
        T(cx, cy - 74 - 11, text=lab, fill=c, font=F["mono_s"])
        # ---- HUD ----
        for sx, sy in ((x0 + 10, y0 + 10), (x1 - 10, y0 + 10), (x0 + 10, y1 - 10), (x1 - 10, y1 - 10)):
            dx = 1 if sx < 200 else -1
            dy = 1 if sy < 150 else -1
            L(sx, sy, sx + dx * 18, sy, fill="#e8e8e8", width=2)
            L(sx, sy, sx, sy + dy * 18, fill="#e8e8e8", width=2)
        if int(t * 2) % 2 == 0:
            O(x0 + 28, y0 + 23, x0 + 38, y0 + 33, fill=_hx(REC), outline="")
        T(x0 + 44, y0 + 28, text="REC", fill=_hx(REC), font=F["mono"], anchor="w")
        fr = int((t * 30) % 30)
        T(x0 + 82, y0 + 28, text="00:{0:02d}:{1:02d}:{2:02d}".format(int(t // 60) % 60, int(t) % 60, fr),
          fill="#f2f2f2", font=F["mono"], anchor="w")
        bx = x1 - 74                                          # batería
        cv.create_rectangle(bx, y0 + 22, bx + 26, y0 + 33, outline="#cfd3d9", tags="dyn")
        cv.create_rectangle(bx + 26, y0 + 25, bx + 29, y0 + 30, fill="#cfd3d9", outline="", tags="dyn")
        for k in range(3):
            cv.create_rectangle(bx + 3 + k * 7, y0 + 25, bx + 8 + k * 7, y0 + 30, fill=_hx(GRN),
                                outline="", tags="dyn")
        for i, nm in enumerate("LR"):                         # vúmetros que se mueven
            lv = 0.55 + 0.22 * math.sin(t * 3.1 + i * 1.3) + 0.12 * math.sin(t * 7.3 + i * 0.4)
            lv = max(0.05, min(0.98, lv))
            yy = y1 - 52 + i * 12
            T(x0 + 28, yy + 4, text=nm, fill="#8d949e", font=F["mono_s"], anchor="w")
            cv.create_rectangle(x0 + 40, yy, x0 + 40 + 92, yy + 8, outline="#2b3038", tags="dyn")
            for k in range(18):
                p = (k + 1) / 18.0
                if p > lv:
                    break
                colr = GRN if p < 0.7 else (A if p < 0.9 else REC)
                cv.create_rectangle(x0 + 41 + k * 5, yy + 1, x0 + 44 + k * 5, yy + 7,
                                    fill=_hx(colr), outline="", tags="dyn")
        ex0, ex1, ey = 330, 424, y1 - 42                       # exposímetro
        L(ex0, ey, ex1, ey, fill="#3a3f48")
        for k in range(-3, 4):
            xx = (ex0 + ex1) / 2 + k * 14
            L(xx, ey - (6 if k == 0 else 3), xx, ey + (6 if k == 0 else 3), fill="#aab2bd")
        mk = (ex0 + ex1) / 2 + 30 * math.sin(t * 0.8)
        cv.create_polygon(mk, ey + 8, mk - 4, ey + 14, mk + 4, ey + 14, fill=_hx(A), outline="", tags="dyn")
        T(ex0 - 5, ey, text="-", fill="#6b7380", font=F["mono_s"], anchor="e")
        T(ex1 + 5, ey, text="+", fill="#6b7380", font=F["mono_s"], anchor="w")
        T((x0 + x1) / 2, y1 - 22, text="M    1/60    F1.8    ISO 400    WB 5600K", fill=_hx(A),
          font=F["mono"])
        # ---- texto de la tarjeta ----
        T(W / 2, 297, text="CamWin32", fill="#ffffff", font=F["title"])
        T(W / 2, 327, text=" ".join("MULTICAM · AUDIO · SYNC"), fill=_hx(A), font=F["mono_s"])
        L(60, 349, W - 60, 349, fill="#262a31")
        cv.create_text(W / 2, 375, text=ABOUT_TAGLINE, fill="#d3d7dd", font=F["tag"], width=370,
                       justify="center", tags="dyn")
        for ri, row in enumerate(ABOUT_CHIPS):
            ws = [F["chip"].measure(c) + 22 for c in row]
            xx = (W - (sum(ws) + 8 * (len(ws) - 1))) / 2.0
            yy = 404 + ri * 30
            for c, wv in zip(row, ws):
                self._pill(xx, yy, xx + wv, yy + 24, "#14171b", "#3a3f48")
                T(xx + wv / 2, yy + 12, text=c, fill=_hx(A), font=F["chip"])
                xx += wv + 8
        cv.create_text(W / 2, 490, text=ABOUT_ASK, fill="#ffffff", font=F["ask"], width=370,
                       justify="center", tags="dyn")
        # ---- botón Ko-fi con halo que late ----
        k = int(round(2.5 + 2.5 * math.sin(t * 2.4)))
        cv.create_image(self._glow_pos[0], self._glow_pos[1], anchor="nw", image=self._glow[k], tags="dyn")
        bx0, by0, bx1, by1 = self._hit["btn"]
        cv.create_image(bx0, by0, anchor="nw",
                        image=self._imgs["btn_h" if self._hover == "btn" else "btn"], tags="dyn")
        T((bx0 + bx1) / 2, (by0 + by1) / 2, text=ABOUT_BTN, fill="#ffffff", font=F["btn"])
        T(W / 2, 585, text=KOFI_SHOW, fill="#ff9a5b" if self._hover == "link" else "#c9a5ff",
          font=F["small"])
        T(W / 2, 615, text=ABOUT_CREDIT, fill="#6b7380", font=F["small"])

    def _pill(self, x0, y0, x1, y1, fill, outline):
        r = (y1 - y0) / 2.0
        pts = []
        for i in range(0, 13):                       # semicírculo derecho
            a = -math.pi / 2 + math.pi * i / 12
            pts += [x1 - r + r * math.cos(a), y0 + r + r * math.sin(a)]
        for i in range(0, 13):                       # semicírculo izquierdo
            a = math.pi / 2 + math.pi * i / 12
            pts += [x0 + r + r * math.cos(a), y0 + r + r * math.sin(a)]
        self.cv.create_polygon(*pts, fill=fill, outline=outline, tags="dyn")


GEO_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")
MODES = ("normal", "compact", "mini")
MODE_NAMES = {"normal": "Modo normal", "compact": "Modo compacto", "mini": "Modo mini"}
GEO_FULL = (1280, 790)                 # tamaño por defecto de la ventana normal
GEO_COMPACT = (640, 400)               # ... y del modo compacto
MIN_FULL = (1060, 740)
MIN_COMPACT = (480, 300)


def read_ui_cfg(ui):
    """(modo, {modo: geometría}) a partir de cfg['ui']; entiende también el formato anterior
    (v1: mode 'full'|'mini' con geo_full / geo_mini; allí «mini» era lo que ahora es «compacto»)."""
    if not isinstance(ui, dict):
        ui = {}
    if ui.get("v") == 2:
        mode = ui.get("mode") if ui.get("mode") in MODES else "normal"
        g = ui.get("geo") if isinstance(ui.get("geo"), dict) else {}
        return mode, {m: g.get(m) for m in MODES}
    mode = "compact" if ui.get("mode") == "mini" else "normal"
    return mode, {"normal": ui.get("geo_full"), "compact": ui.get("geo_mini"), "mini": None}


def make_tray_image(rec=False, size=64):
    """Icono de la bandeja: punto gris (listo) o rojo (grabando) sobre fondo oscuro."""
    from PIL import ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=size // 5, fill=(31, 35, 42, 255),
                        outline=(90, 97, 110, 255), width=2)
    m = size // 4
    d.ellipse((m, m, size - m, size - m), fill=(229, 57, 69, 255) if rec else (190, 196, 206, 255))
    return im


STARTUP_NAME = "cam-ui"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_cmd():
    """Comando para relanzar cam-ui igual que arrancó (mismo Python, mismo archivo), pero con
    pythonw.exe si lo hay al lado, para que no asome una consola en cada inicio de Windows."""
    exe = sys.executable or "python"
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(cand):
            exe = cand
    return '"{0}" "{1}"'.format(exe, APP_FILE)


def startup_get():
    """True si cam-ui está puesto para iniciarse con Windows y, además, apunta a este mismo
    archivo (si se movió o se reinstaló en otra carpeta, cuenta como que no está puesto: al
    activarlo se corrige solo)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as k:
            val, _ = winreg.QueryValueEx(k, STARTUP_NAME)
        return val == _startup_cmd()
    except Exception:
        return False


def startup_set(on):
    """Activa o desactiva el inicio con Windows. Devuelve (ok, error)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, STARTUP_NAME, 0, winreg.REG_SZ, _startup_cmd())
            else:
                try:
                    winreg.DeleteValue(k, STARTUP_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:
        return False, str(e)


def pip_install(pkg, timeout=240):
    """Instala un paquete con el mismo Python. Devuelve (ok, mensaje)."""
    exe = sys.executable or "python"
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(cand):
            exe = cand
    msg = ""
    for extra in ([], ["--user"]):
        try:
            r = subprocess.run([exe, "-m", "pip", "install", "--disable-pip-version-check"] + extra + [pkg],
                               capture_output=True, text=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0:
                return True, ""
            msg = (r.stderr or r.stdout or "").strip()[-400:]
        except Exception as e:
            msg = repr(e)
    return False, msg


def parse_geometry(geo):
    """'1280x790+10+20' -> (w, h, x, y); None si no es una geometría válida."""
    m = GEO_RE.match(geo or "")
    return tuple(int(v) for v in m.groups()) if m else None


def fit_geometry(geo, bounds, minsize=(0, 0), inside=False):
    """Ajusta una geometría guardada a la pantalla actual (puede haber cambiado de monitor):
    nunca más grande que el escritorio y siempre con la barra de título al alcance.
    bounds = (x, y, ancho, alto) del escritorio virtual. Devuelve 'WxH+X+Y' o None."""
    p = parse_geometry(geo)
    if p is None:
        return None
    w, h, x, y = p
    bx, by, bw, bh = bounds
    w = max(minsize[0], min(w, bw))
    h = max(minsize[1], min(h, bh))
    if inside:                              # ventana sin barra de título: entera dentro del escritorio
        x = min(max(x, bx), bx + bw - w)
        y = min(max(y, by), by + bh - h)
    else:
        x = min(max(x, bx - w + 120), bx + bw - 120)
        y = min(max(y, by), by + bh - 80)
    return "{0}x{1}+{2}+{3}".format(w, h, x, y)


def desktop_bounds(root):
    """(x, y, ancho, alto) de todo el escritorio (varios monitores en Windows)."""
    try:
        import ctypes
        gm = ctypes.windll.user32.GetSystemMetrics
        b = (gm(76), gm(77), gm(78), gm(79))
        if b[2] > 0 and b[3] > 0:
            return b
    except Exception:
        pass
    return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())


# =====================================================================
#  Actualizaciones (mismo sistema que widget-arena: huella sha256, copia de
#  seguridad antes de tocar nada, vuelta atrás y reinicio solo)
# =====================================================================
_UPDATE_ST = {}


def update_state():
    global _UPDATE_ST
    if not _UPDATE_ST:
        try:
            with open(UPDATE_STATE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        _UPDATE_ST = d if isinstance(d, dict) else {}
    return _UPDATE_ST


def update_state_save():
    try:
        os.makedirs(os.path.dirname(UPDATE_STATE), exist_ok=True)
        tmp = UPDATE_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(update_state(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, UPDATE_STATE)
    except OSError as e:
        log.warning("actualizar: no se pudo guardar el estado: %s", e)


def update_set_auto_apply(on):
    """«Actualizar automáticamente al abrir el programa, sin preguntar» (casilla de
    UpdateDialog). Si se activa y el aviso normal («avisarme al abrir») estaba apagado (se
    marcó antes «no preguntar más»), se reactiva también: si no, nunca llegaría a mirar si
    hay nada que aplicar solo. Apagarla, en cambio, no toca el aviso normal."""
    st = update_state()
    st["auto_apply"] = bool(on)
    if on and st.get("auto", True) is False:
        st["auto"] = True
    update_state_save()


def _sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def update_local_info():
    try:
        sha = _sha256_file(APP_FILE)
    except OSError:
        sha = ""
    return {"pack": APP_PACK, "sha256": sha, "label": APP_VERSION, "file": APP_FILE}


def _update_get(url, limit, timeout=20):
    """Descarga una dirección de la web del proyecto (y nada más: ni siquiera si hay una
    redirección a otro sitio)."""
    import urllib.request
    import ssl
    if not url.startswith(UPDATE_BASE):
        raise ValueError("dirección no permitida")

    class _Same(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith(UPDATE_BASE):
                raise ValueError("redirección a otro sitio rechazada")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    op = urllib.request.build_opener(_Same(), urllib.request.HTTPSHandler(context=ctx))
    with op.open(urllib.request.Request(url, headers={"User-Agent": "cam-ui", "Cache-Control": "no-cache"}),
                 timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("la respuesta es demasiado grande")
    return data


def update_check():
    """Mira la versión publicada. Devuelve {ok, state: newer|same|older|diverged, info, local}
    o {ok: False, error}."""
    try:
        info = json.loads(_update_get(UPDATE_BASE + "update.json?t=%d" % int(time.time()),
                                      262144).decode("utf-8"))
        meta = info["files"]["camui.py"]
        pack, sha, size = int(info["pack"]), str(meta["sha256"]).lower(), int(meta["size"])
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or size <= 0 or size > 12 * 1048576:
            raise ValueError("update.json no es válido")
    except Exception as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__}
    loc = update_local_info()
    if pack > loc["pack"]:
        st = "newer"
    elif pack < loc["pack"]:
        st = "older"
    else:
        st = "same" if sha == loc["sha256"] else "diverged"
    update_state()["last_check"] = time.time()
    update_state_save()
    return {"ok": True, "state": st, "info": info, "local": loc}


def _update_file(info, name):
    if name not in UPDATE_FILES:
        raise ValueError("archivo no permitido")
    meta = info["files"][name]
    # con la huella en la dirección no se sirve una copia vieja de la caché
    data = _update_get(UPDATE_BASE + name + "?v=" + str(meta["sha256"])[:12], int(meta["size"]))
    if len(data) != int(meta["size"]) or hashlib.sha256(data).hexdigest() != str(meta["sha256"]).lower():
        raise ValueError("%s: lo descargado no coincide con la huella publicada (se cancela; si "
                         "acaban de publicar, inténtalo de nuevo en unos minutos)" % name)
    compile(data, name, "exec")           # que al menos sea Python válido antes de tocar nada
    return data


def _update_put(path, data):
    tmp = path + ".nuevo"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _update_bkname():
    """Nombre libre para una copia (nunca pisa otra, aunque se hagan dos en el mismo segundo)."""
    base = os.path.join(UPDATE_BACKUPS, "camui.pack%d.%s" % (APP_PACK, time.strftime("%Y%m%d-%H%M%S")))
    p, i = base + ".py", 2
    while os.path.exists(p):
        p = "%s-%d.py" % (base, i)
        i += 1
    return p


def update_apply(info):
    """Descarga y comprueba la versión publicada, guarda una copia de la actual y la sustituye.
    Devuelve {backup}."""
    d = os.path.dirname(APP_FILE)
    if not os.access(d, os.W_OK) or not os.access(APP_FILE, os.W_OK):
        raise ValueError("No hay permiso para escribir en la carpeta del programa (%s)." % d)
    data = _update_file(info, "camui.py")
    m = re.search(rb"^APP_PACK\s*=\s*(\d+)", data, re.M)
    if not m or int(m.group(1)) != int(info["pack"]):
        raise ValueError("El archivo publicado no es el de la versión anunciada (se cancela).")
    os.makedirs(UPDATE_BACKUPS, exist_ok=True)
    bk = _update_bkname()
    shutil.copyfile(APP_FILE, bk)
    _update_put(APP_FILE, data)
    st = update_state()
    st.update({"last_backup": bk, "from_pack": APP_PACK, "to_pack": int(info["pack"]), "applied": time.time()})
    update_state_save()
    try:                                   # se conservan las 5 últimas copias
        olds = sorted((os.path.join(UPDATE_BACKUPS, f) for f in os.listdir(UPDATE_BACKUPS)
                       if f.startswith("camui.pack")), key=os.path.getmtime)
        for f in olds[:-5]:
            os.remove(f)
    except OSError:
        pass
    return {"backup": bk}


def update_backup_available():
    bk = update_state().get("last_backup")
    return bool(bk) and os.path.exists(bk) and os.path.abspath(os.path.dirname(bk)) == os.path.abspath(UPDATE_BACKUPS)


def update_rollback():
    """Vuelve a la copia guardada en la última actualización (y deja la actual como copia, por
    si quieres volver a ella)."""
    if not update_backup_available():
        raise ValueError("No hay una copia anterior a la que volver.")
    st = update_state()
    bk = st["last_backup"]
    with open(bk, "rb") as f:
        data = f.read()
    compile(data, "camui.py", "exec")
    cur = _update_bkname()
    shutil.copyfile(APP_FILE, cur)
    _update_put(APP_FILE, data)
    st.update({"last_backup": cur, "applied": time.time()})
    update_state_save()
    return {"backup": cur}


def update_spawn_restart(delay=3):
    """Lanza, aparte, una orden que vuelve a abrir cam-ui pasados unos segundos (da tiempo a
    que este termine de guardar y cerrarse)."""
    cmd = [sys.executable, APP_FILE] + list(sys.argv[1:])
    code = ("import subprocess, sys, time\ntime.sleep({0})\n"
           "subprocess.Popen(sys.argv[1:], close_fds=True{1})").format(
        int(delay), ", creationflags=0x08000008" if os.name == "nt" else ", start_new_session=True")
    kw = {"creationflags": 0x08000008} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-c", code] + cmd, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **kw)
    return cmd


def _ask_update_now(parent, pack_remote):
    """Diálogo «¿actualizar ahora?» con una casilla «no preguntar más». Devuelve
    (quiere_actualizar, no_preguntar_mas)."""
    res = {"go": False, "dont_ask": False}
    top = tk.Toplevel(parent)
    top.title("cam-ui")
    top.configure(bg=BG)
    top.transient(parent)
    top.resizable(False, False)
    frm = ttk.Frame(top)
    frm.pack(fill="both", expand=True, padx=16, pady=14)
    ttk.Label(frm, text="Hay una versión nueva de cam-ui (pack {0}).".format(pack_remote),
             font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(frm, style="Muted.TLabel", wraplength=360, justify="left",
             text="¿Descargarla e instalarla ahora? Se guarda una copia de la versión actual y "
                  "el programa se reinicia; tu configuración no se toca."
             ).pack(anchor="w", pady=(6, 10))
    dont = tk.BooleanVar(value=False)
    ttk.Checkbutton(frm, text="No preguntar más, prefiero actualizar a mano",
                    variable=dont).pack(anchor="w")
    bar = ttk.Frame(frm)
    bar.pack(fill="x", pady=(12, 0))

    def go():
        res["go"], res["dont_ask"] = True, bool(dont.get())
        top.destroy()

    def cancel():
        res["dont_ask"] = bool(dont.get())
        top.destroy()
    ttk.Button(bar, text="Cancelar", command=cancel).pack(side="right")
    ttk.Button(bar, text="Aceptar", command=go).pack(side="right", padx=(0, 6))
    top.bind("<Escape>", lambda e: cancel())
    top.protocol("WM_DELETE_WINDOW", cancel)
    try:
        top.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        tw, th = top.winfo_width(), top.winfo_height()
        top.geometry("+{0}+{1}".format(px + max(0, (pw - tw) // 2), py + max(0, (ph - th) // 2)))
    except Exception:
        pass
    top.grab_set()
    top.wait_window()
    return res["go"], res["dont_ask"]


class UpdateDialog:
    """Versión instalada y publicada, novedades, instalar, volver a la anterior y el aviso
    al abrir el programa. Misma idea que widget-arena, en Tkinter."""

    def __init__(self, app):
        self.app = app
        self.res = None
        self.busy = False
        top = self.top = tk.Toplevel(app.root)
        self.alive = True
        top.bind("<Destroy>", lambda e: setattr(self, "alive", False) if e.widget is top else None)
        top.title("Actualizaciones")
        top.configure(bg=BG)
        top.transient(app.root)
        top.geometry("640x520")
        top.minsize(560, 360)
        top.protocol("WM_DELETE_WINDOW", self.close)
        frm = ttk.Frame(top)
        frm.pack(fill="both", expand=True, padx=12, pady=10)
        # los botones se empaquetan los PRIMEROS, pegados abajo (side="bottom"): así tienen
        # siempre su espacio reservado y nunca desaparecen aunque el resto del contenido (p. ej.
        # las novedades, que crecen en cada versión) no quepa entero -- lo que no cabe se queda
        # dentro de su propio scroll (ver self.notes más abajo), no empuja los botones fuera.
        bar = ttk.Frame(frm)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        self.b_check = ttk.Button(bar, text="Buscar de nuevo", command=self.start_check)
        self.b_check.pack(side="left")
        self.b_back = ttk.Button(bar, text="Volver a la versión anterior", command=self._rollback)
        self.b_back.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Cerrar", command=self.close).pack(side="right")
        self.b_go = ttk.Button(bar, text="Actualizar y reiniciar", command=self._apply)
        self.b_go.pack(side="right", padx=(0, 6))
        ttk.Label(frm, text="Actualizaciones", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.lbl_local = ttk.Label(frm, text="")
        self.lbl_local.pack(anchor="w", pady=(6, 0))
        self.lbl_remote = ttk.Label(frm, text="")
        self.lbl_remote.pack(anchor="w")
        self.lbl_status = ttk.Label(frm, style="Muted.TLabel", wraplength=590, justify="left")
        self.lbl_status.pack(anchor="w", fill="x", pady=(8, 6))
        ttk.Label(frm, text="Novedades").pack(anchor="w")
        notes_box = ttk.Frame(frm)
        notes_box.pack(fill="both", expand=True, pady=(2, 8))
        self.notes = tk.Text(notes_box, wrap="word", height=8, bg=PANEL, fg=FG,
                             insertbackground=FG, relief="flat", padx=8, pady=6)
        notes_sb = ttk.Scrollbar(notes_box, orient="vertical", command=self.notes.yview)
        self.notes.configure(yscrollcommand=notes_sb.set)
        notes_sb.pack(side="right", fill="y")
        self.notes.pack(side="left", fill="both", expand=True)
        self.notes.config(state="disabled")
        self.var_auto = tk.BooleanVar(value=update_state().get("auto", True) is not False)
        ttk.Checkbutton(frm, text="Avisarme al abrir el programa si hay una versión nueva",
                        variable=self.var_auto, command=self._auto_toggled).pack(anchor="w")
        self.var_autoapply = tk.BooleanVar(value=bool(update_state().get("auto_apply")))
        ttk.Checkbutton(frm, text="Actualizar automáticamente al abrir el programa, sin preguntar",
                        variable=self.var_autoapply, command=self._autoapply_toggled).pack(anchor="w")
        ttk.Label(frm, style="Muted.TLabel", wraplength=590, justify="left",
                 text="Con esto marcado, si al abrir el programa hay una versión nueva se instala "
                      "sola (con su copia de seguridad de siempre) y el programa se reinicia solo, "
                      "sin ningún aviso que haya que confirmar."
                 ).pack(anchor="w", pady=(2, 0))
        # --- casilla oculta: vigilar y actualizar sola mientras esté abierto, sin ningún aviso
        # -- ni siquiera esta ventana -- en cuanto se publique algo nuevo. Pensada para probar
        # publicaciones seguidas, no para el uso normal: empieza invisible y solo aparece con un
        # atajo secreto (Ctrl+Shift+Alt+F12, con Win pulsada) mientras esta ventana tenga el
        # foco; una vez activada se queda visible (para poder apagarla sin tener que buscarla de
        # nuevo), y al apagarla vuelve a esconderse.
        self.var_silent_watch = tk.BooleanVar(value=bool(update_state().get("silent_watch")))
        self.chk_silent_watch = ttk.Checkbutton(
            frm, text="Vigilar sola, sin avisos: actualizar en cuanto se publique una versión nueva",
            variable=self.var_silent_watch, command=self._silent_watch_toggled)
        self.lbl_silent_watch = ttk.Label(
            frm, style="Muted.TLabel", wraplength=590, justify="left",
            text="Comprueba cada minuto mientras cam-ui esté abierto (no solo al arrancar). Si "
                 "hay una versión nueva, la instala y reinicia sola -- sin preguntar nada, sin "
                 "avisar de que se va a guardar una copia antes, sin abrir siquiera esta "
                 "ventana. Sigue guardando esa copia igual que siempre, solo que sin decirlo.")
        if self.var_silent_watch.get():
            self.chk_silent_watch.pack(anchor="w")
            self.lbl_silent_watch.pack(anchor="w", pady=(2, 0))
        self._win_held = False
        for ks in ("Super_L", "Super_R", "Win_L", "Win_R"):
            # No todas las plataformas conocen todos estos keysyms (p.ej. X11 no tiene
            # "Win_L"/"Win_R", solo "Super_L"/"Super_R"); sin el try/except, un bind() con un
            # keysym que Tcl/Tk no reconoce lanza TclError y se cae toda la ventana.
            try:
                top.bind("<KeyPress-{0}>".format(ks), self._win_key_set(True))
                top.bind("<KeyRelease-{0}>".format(ks), self._win_key_set(False))
            except tk.TclError:
                pass
        top.bind("<Control-Shift-Alt-F12>", self._reveal_silent_watch)
        self._fill_local()
        self._buttons()

    def _win_key_set(self, held):
        return lambda _e=None: setattr(self, "_win_held", held)

    def _reveal_silent_watch(self, _e=None):
        if not self._win_held or self.chk_silent_watch.winfo_ismapped():
            return
        self.chk_silent_watch.pack(anchor="w")
        self.lbl_silent_watch.pack(anchor="w", pady=(2, 0))

    def _silent_watch_toggled(self):
        on = bool(self.var_silent_watch.get())
        update_state()["silent_watch"] = on
        update_state_save()
        if not on:
            self.chk_silent_watch.pack_forget()
            self.lbl_silent_watch.pack_forget()

    def _fill_local(self):
        loc = update_local_info()
        self.lbl_local.config(text="Tu versión:  pack {0}{1}  ·  huella {2}".format(
            loc["pack"], ("  ·  " + loc["label"]) if loc["label"] else "", (loc["sha256"][:8] or "?")))
        self.lbl_remote.config(text="Publicada:  —")

    def _buttons(self):
        r = self.res or {}
        can = bool(r.get("ok")) and r.get("state") in ("newer", "diverged") and not self.busy \
            and not self.app.recording
        self.b_go.config(state="normal" if can else "disabled",
                         text="Sustituir por la publicada" if r.get("state") == "diverged"
                         else "Actualizar y reiniciar")
        self.b_back.config(state="normal" if (update_backup_available() and not self.busy
                                              and not self.app.recording) else "disabled")
        self.b_check.config(state="disabled" if self.busy else "normal")

    def start_check(self):
        self.busy = True
        self.lbl_status.config(text="Buscando la versión publicada…")
        self._buttons()
        self.app._bg(update_check, self._on_checked)

    def _auto_toggled(self):
        update_state()["auto"] = bool(self.var_auto.get())
        update_state_save()

    def _autoapply_toggled(self):
        update_set_auto_apply(self.var_autoapply.get())
        self.var_auto.set(update_state().get("auto", True) is not False)

    def _on_checked(self, r):
        self.busy = False
        self.res = r
        self._fill_local()
        if not r.get("ok"):
            self.lbl_status.config(text="No se pudo comprobar: {0}\nRevisa tu conexión e "
                                   "inténtalo de nuevo.".format(r.get("error", "")))
            self._buttons()
            return
        info, st = r["info"], r["state"]
        self.lbl_remote.config(text="Publicada:  pack {0}{1}".format(
            info.get("pack"), ("  ·  " + str(info.get("version"))) if info.get("version") else ""))
        self.notes.config(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", str(info.get("notes") or "(sin novedades indicadas)"))
        self.notes.config(state="disabled")
        self.lbl_status.config(text={
            "newer": "Hay una versión nueva (pack {0}). Se guardará una copia de la actual y el "
                    "programa se reiniciará; tu configuración no se toca.".format(info.get("pack")),
            "same": "Ya tienes la última versión.",
            "older": "Tu copia (pack {0}) es más nueva que la publicada (pack {1}): no hay nada "
                    "que actualizar.".format(r["local"]["pack"], info.get("pack")),
            "diverged": "Tienes el mismo pack que la web, pero tu archivo es distinto (cambios "
                        "propios sin publicar). Puedes sustituirlo por el publicado: se guarda una copia.",
        }[st])
        self._buttons()

    def apply_result(self, r):
        """Desde el aviso del arranque: ya se sabe que hay una versión nueva y se dijo que sí."""
        self._on_checked(r)
        self._apply(confirm=False)

    def _apply(self, confirm=True):
        r = self.res or {}
        if not r.get("ok") or r.get("state") not in ("newer", "diverged"):
            return
        if self.app.recording:
            messagebox.showinfo("Actualizar", "No se puede actualizar mientras se está "
                                "grabando. Para la grabación primero.")
            return
        if confirm:
            warn = ("\n\nTu archivo tiene cambios sin publicar; se guardará una copia por si "
                    "quieres volver.") if r["state"] == "diverged" else ""
            if not messagebox.askyesno(
                    "Actualizar", "Se descargará la versión publicada (pack {0}), se guardará "
                    "una copia de la actual y el programa se reiniciará.{1}\n\n¿Continuar?".format(
                        r["info"].get("pack"), warn)):
                return
        self.busy = True
        self.lbl_status.config(text="Descargando e instalando…")
        self._buttons()
        info = r["info"]
        self.app._bg(lambda: _update_run(update_apply, info), self._on_applied)

    def _rollback(self):
        if self.app.recording:
            messagebox.showinfo("Actualizar", "No se puede volver a la versión anterior "
                                "mientras se está grabando. Para la grabación primero.")
            return
        if not messagebox.askyesno(
                "Volver a la versión anterior", "Se restaurará la copia guardada en la última "
                "actualización y el programa se reiniciará. La versión actual queda guardada.\n\n"
                "¿Continuar?"):
            return
        self.busy = True
        self.lbl_status.config(text="Restaurando…")
        self._buttons()
        self.app._bg(lambda: _update_run(update_rollback), self._on_applied)

    def _on_applied(self, r):
        self.busy = False
        if not r.get("ok"):
            self.lbl_status.config(text="No se pudo completar: {0}\nNo se ha cambiado "
                                   "nada.".format(r.get("error", "")))
            self._buttons()
            messagebox.showwarning("Actualizar", "No se pudo completar:\n{0}\n\nNo se ha "
                                   "cambiado nada.".format(r.get("error", "")))
            return
        self.lbl_status.config(text="Listo. El programa se reinicia en unos segundos…")
        self.b_go.config(state="disabled")
        self.app.root.after(900, self.app.update_restart_app)

    def close(self):
        self.top.destroy()


def _update_run(fn, *a):
    """Envoltorio para self._bg: fn puede lanzar una excepción; aquí se convierte en
    {ok: False, error} igual que si la hubiera devuelto update_check()."""
    try:
        r = fn(*a)
        r = dict(r or {})
        r["ok"] = True
        return r
    except Exception as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__}


class PhoneCamQrDialog:
    """Ventanita flotante, siempre encima, con el QR de un emparejamiento nuevo de «Cámara del
    móvil» (ver CamApp._phonecam_show_pair_qr): aparece sola, al instante, encima de cualquier
    pestaña que haya abierta en ese momento -- antes el QR solo se veía dentro de la pestaña
    «Ajustes», así que si no se estaba ya ahí parecía que no había pasado nada. Se cierra sola en
    cuanto ese móvil se empareja de verdad (CamApp._phonecam_auto_add) o a los
    PHONECAM_PAIR_TIMEOUT segundos si nadie lo lee a tiempo -- en ambos casos por el mismo camino
    de siempre, _phonecam_clear_pair_qr(); también con su propio botón «Cerrar», con Esc, o sin
    más al cerrar la ventana."""

    def __init__(self, parent, photo, url, on_install_qr=None):
        self.alive = True
        self.photo = photo             # referencia viva: sin esto Tk se olvida de la imagen
        self.btn_install_qr = None     # solo existe sin «qrcode» -- ver más abajo
        top = self.top = tk.Toplevel(parent)
        top.title("Cámara del móvil")
        top.configure(bg=PANEL)
        top.resizable(False, False)
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Destroy>", lambda e: setattr(self, "alive", False) if e.widget is top else None)
        frm = ttk.Frame(top, padding=16)
        frm.pack()
        ttk.Label(frm, text="Un móvil está esperando", font=("Segoe UI", 12, "bold")).pack()
        if photo is not None:
            ttk.Label(frm, image=photo).pack(pady=(10, 10))
        else:
            # sin el paquete «qrcode» no hay imagen que mostrar, pero la dirección en texto
            # sigue valiendo igual (se puede teclear a mano en el móvil, aunque sea incómodo) --
            # si quien llama sabe cómo instalarlo (CamApp._phonecam_offer_qr_install), se ofrece
            # aquí mismo un botón para arreglarlo con un clic, en vez de dejarlo así para siempre
            lbl_url = ttk.Label(frm, text=url, style="Muted.TLabel", wraplength=320,
                                justify="left", cursor="hand2")
            lbl_url.pack(pady=(10, 10))
            lbl_url.bind("<Button-1>", lambda e: webbrowser.open(url))
            if on_install_qr is not None:
                self.btn_install_qr = ttk.Button(
                    frm, text="Instalar «qrcode» para ver el código",
                    command=lambda: on_install_qr(url))
                self.btn_install_qr.pack(pady=(0, 6))
        ttk.Label(frm, text="Apunta con su cámara a este código para añadirlo al mosaico.",
                 style="Muted.TLabel", wraplength=320, justify="left").pack()
        ttk.Button(frm, text="Cerrar", command=self.close).pack(pady=(14, 0))
        try:
            top.update_idletasks()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            w, h = top.winfo_width(), top.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
            top.geometry("+{0}+{1}".format(x, y))
        except Exception:
            pass

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.top.destroy()
        except Exception:
            pass


class PhoneCamCertDialog:
    """Ventanita flotante con el QR (o la dirección, si falta «qrcode») para instalar en un
    iPhone/iPad el certificado de confianza de cam-ui (ver CamApp._phonecam_show_cert_dialog).
    Solo hace falta en iOS/iPadOS: WebKit no deja usar la cámara (getUserMedia) a través de un
    certificado autofirmado normal, por mucho que ya se haya aceptado el aviso de «conexión no
    privada» -- eso basta para ver la página, pero no para la cámara (caso real, reportado por
    un usuario: la página cargaba bien, pero la cámara fallaba sin pedir permiso siquiera).
    Android/Chrome no tiene esa restricción, así que ahí no hace falta nada de esto."""

    def __init__(self, parent, photo, url, on_install_qr=None):
        self.alive = True
        self.photo = photo
        self.btn_install_qr = None
        top = self.top = tk.Toplevel(parent)
        top.title("Certificado para iPhone/iPad")
        top.configure(bg=PANEL)
        top.resizable(False, False)
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Destroy>", lambda e: setattr(self, "alive", False) if e.widget is top else None)
        frm = ttk.Frame(top, padding=16)
        frm.pack()
        ttk.Label(frm, text="Solo hace falta en iPhone/iPad",
                 font=("Segoe UI", 12, "bold")).pack()
        if photo is not None:
            ttk.Label(frm, image=photo).pack(pady=(10, 10))
        else:
            # sin el paquete «qrcode» no hay imagen que mostrar, pero la dirección en texto
            # sigue valiendo igual (se puede teclear a mano), y aquí mismo se ofrece instalarlo
            lbl_url = ttk.Label(frm, text=url, style="Muted.TLabel", wraplength=320,
                                justify="left", cursor="hand2")
            lbl_url.pack(pady=(10, 10))
            lbl_url.bind("<Button-1>", lambda e: webbrowser.open(url))
            if on_install_qr is not None:
                self.btn_install_qr = ttk.Button(
                    frm, text="Instalar «qrcode» para ver el código",
                    command=lambda: on_install_qr(url))
                self.btn_install_qr.pack(pady=(0, 6))
        ttk.Label(frm, style="Muted.TLabel", wraplength=320, justify="left",
                 text="En Android no hace falta nada de esto (con «Avanzado → Continuar» en el "
                      "aviso del navegador ya funciona). En iPhone/iPad: apunta con la Cámara a "
                      "este código y sigue las instrucciones de esa página -- solo hay que "
                      "hacerlo una vez, no hace falta repetirlo."
                 ).pack(pady=(4, 0))
        ttk.Button(frm, text="Cerrar", command=self.close).pack(pady=(14, 0))
        try:
            top.update_idletasks()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            w, h = top.winfo_width(), top.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
            top.geometry("+{0}+{1}".format(x, y))
        except Exception:
            pass

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.top.destroy()
        except Exception:
            pass


class PhoneCamDevicesDialog:
    """Lista de móviles que ya se emparejaron antes con este cam-ui y que, por eso, entran solos
    la próxima vez que abran «/add» en este mismo PC, sin tener que leer otra vez ningún QR (ver
    el comentario junto a PHONECAM_DEVICES_FILE y PhoneCamHub.add_device/known_device). Desde
    aquí se puede «olvidar» cualquiera -- a partir de ahí, ese móvil tendrá que volver a
    emparejarse con el QR de siempre, igual que si fuera nuevo."""

    def __init__(self, parent, hub):
        self.alive = True
        self.hub = hub
        self._secrets = []
        top = self.top = tk.Toplevel(parent)
        top.title("Dispositivos recordados")
        top.configure(bg=PANEL)
        top.geometry("440x320")
        top.minsize(360, 240)
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda e: self.close())
        frm = ttk.Frame(top, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, style="Muted.TLabel", wraplength=400, justify="left",
                  text="Estos móviles entran solos al abrir «/add» en este PC, sin volver a "
                       "leer ningún código QR."
                  ).pack(anchor="w", pady=(0, 8))
        listfrm = ttk.Frame(frm)
        listfrm.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(listfrm, exportselection=False)
        sb = ttk.Scrollbar(listfrm, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        self.btn_forget = ttk.Button(btns, text="Olvidar", command=self._forget_selected)
        self.btn_forget.pack(side="left")
        ttk.Button(btns, text="Cerrar", command=self.close).pack(side="right")
        self._reload()

    def _reload(self):
        self.listbox.delete(0, "end")
        self._secrets = []
        for secret, info in self.hub.list_devices():
            name = info.get("name", "Móvil") if isinstance(info, dict) else "Móvil"
            added = info.get("added", 0) if isinstance(info, dict) else 0
            when = ""
            if added:
                try:
                    when = datetime.datetime.fromtimestamp(added).strftime("%d/%m/%Y")
                except Exception:
                    when = ""
            self.listbox.insert("end", "{0}   (desde {1})".format(name, when) if when else name)
            self._secrets.append(secret)
        if not self._secrets:
            self.listbox.insert("end", "(ninguno todavía)")

    def _forget_selected(self):
        sel = self.listbox.curselection()
        if not sel or not self._secrets or sel[0] >= len(self._secrets):
            return
        self.hub.forget_device(self._secrets[sel[0]])
        self._reload()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.top.destroy()
        except Exception:
            pass


class CamApp:
    def __init__(self, root):
        self.root = root
        self._wd_t = 0.0
        self._sel_guard = False
        self.paused = False
        self._arm_watchdog(force=True)
        log.info("init: inicio")
        root.report_callback_exception = self._cb_exception
        root.title("CamWin32 · cámaras / multivista")
        root.configure(bg=BG)
        root.minsize(*MIN_FULL)
        self.eng = FFmpegEngine()
        self.vdevs, self.adevs = [], []
        self.vdev, self.adev = {}, {}
        self.cams = {}
        self.order = []
        self.sel = None
        self.previewing = False
        self._stuck_warned = False     # ver _on_stuck: ffmpeg vivo pero sin entregar ni un fotograma
        self.recording = False
        self.busy = False
        self.rec_path = None
        self.rec_final = None
        self.rec_clock0 = None
        self._loading = True
        self._bgq = deque()
        self._gen = 0                  # el botón de pánico invalida los trabajos de fondo en curso
        self._restart_job = None
        self._drag_src = None
        self._tkimg = None
        self._slow_warned = set()
        self._ph_visible = True
        self._info_txt = None
        self._last_fc = -1
        self._last_size = (0, 0)
        self._meter = [-90.0, -90.0]
        self._peak = [-90.0, -90.0]
        self._peak_t = [0.0, 0.0]
        self.editing = None            # etiqueta de la cámara cuyo encuadre se edita sobre la imagen
        self._edit_drag = None
        self._ov_btn = None
        self._img_rect = None
        self._run_labels = []          # cámaras que hay en el ffmpeg en marcha (en orden)
        self._run_color = {}
        self._color_jobs = {}
        self._pickers = []
        self._live = set()             # widgets que siguen activos mientras se graba (color)
        self._about = None
        self._cb = {"change": self._on_change, "frame": self._on_frame, "color": self._on_color}
        self._cfg = self._read_config()
        nc = self._cfg.get("net_cams")
        self.net_cams = dict((lab, d) for lab, d in nc.items()
                              if isinstance(d, dict) and d.get("spec")) if isinstance(nc, dict) else {}
        ui = self._cfg.get("ui")
        self.mode, self._geo = read_ui_cfg(ui)               # modo con el que se cerró la última vez
        ui = ui if isinstance(ui, dict) else {}
        self._pinned = bool(ui.get("pinned", False))         # «siempre encima» (solo en compacto)
        self._tray_asked = bool(ui.get("tray_asked", False))
        self._tray = None
        self._tray_rec = False
        self._tray_title_last = ""
        self._tray_q = deque()                               # órdenes del icono de bandeja (otro hilo)
        self._hidden = False
        self._mini_drag = None
        self._mini_iconified = False
        vc = self._cfg.get("vcam") if isinstance(self._cfg.get("vcam"), dict) else {}
        self._vcam_cfg = vc
        self.vcam = VirtualCam()                             # cámara virtual (videollamadas)
        self.eng.vcam = self.vcam
        self.vcam_on = False
        self._vcam_asked = False
        ic = self._cfg.get("ipout") if isinstance(self._cfg.get("ipout"), dict) else {}
        self._ip_cfg = ic
        self.ipsrv = IpServer()                              # salida en red (ver desde otro equipo)
        self.eng.ipsrv = self.ipsrv
        self.ipout_on = False
        pc = self._cfg.get("phonecam") if isinstance(self._cfg.get("phonecam"), dict) else {}
        self._phonecam_cfg = pc
        self._phonecam_q = deque()                           # móviles nuevos de verdad (otro hilo)
        self._phonecam_pair_q = deque()                      # QR de emparejamiento pendiente de mostrar
        self._phonecam_remote_q = deque()                    # aviso de «enciéndela sola» (otro hilo)
        self.phonecam = PhoneCamHub(self._phonecam_q, self._phonecam_pair_q)  # cámara del móvil
        self.ipsrv.phonecam = self.phonecam    # para el botón «Añadir cámara del móvil» de /
        self.ipsrv.phonecam_remote_q = self._phonecam_remote_q   # para que ese botón la encienda
        self.phonecam_on = False
        self._phonecam_pair_shown_at = 0.0
        self._phonecam_qr_dialog = None                      # diálogo flotante del QR (ver más abajo)
        self._phonecam_cert_dialog = None                     # diálogo del certificado (iPhone/iPad)
        self._phonecam_devices_dialog = None                  # diálogo de «Dispositivos recordados»
        self.startup_on = startup_get()                      # se comprueba de verdad, no se guarda aparte
        try:
            self.eng.startup_time = min(8.0, max(0.5, float(self._cfg.get("startup", 2.5))))
        except Exception:
            pass

        log.info("init: config leida (%d claves)", len(self._cfg))
        self._make_vars()
        log.info("init: variables listas")
        self._apply_theme()
        log.info("init: tema aplicado")
        self._build_ui()
        self._decor(self.mode == "mini")
        self._apply_mode()
        self._restore_geo()
        log.info("init: interfaz construida (modo %s)", self.mode)
        self._loading = False
        self._refresh_devices(first=True)
        log.info("init: busqueda de dispositivos lanzada")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<F5>", lambda e: self.toggle_preview())
        root.bind("<F8>", lambda e: self.take_photo())
        root.bind("<F9>", lambda e: self.toggle_rec())
        root.bind("<F12>", lambda e: self.panic_reset())
        root.bind("<F11>", lambda e: self.set_mode("normal" if self.mode != "normal" else "compact"))
        root.after(33, self._tick)
        root.bind("<Map>", self._on_map, add="+")
        root.after(900, self._tray_boot)
        root.after(12000, self.update_auto_check)
        root.after(15000, self._silent_watch_tick)
        if self._vcam_cfg.get("on"):
            self._vcam_enable(quiet=True)                    # se retoma si estaba activa al cerrar
        if self._ip_cfg.get("on"):
            self._ip_enable(quiet=True)
        self._phonecam_enable(quiet=True)   # siempre activa: sin coste real si no hay móvil conectado

    # ------------------------------------------------------------ modos: normal / compacto / mini
    def _cur_geo(self):
        """Geometría actual si la ventana está en estado normal (no maximizada ni minimizada)."""
        try:
            if self.root.state() != "normal":
                return None
            p = parse_geometry(self.root.geometry())
            lo = (100, 20) if self.mode == "mini" else (200, 150)
            if p and p[0] > lo[0] and p[1] > lo[1]:
                return self.root.geometry()
        except Exception:
            pass
        return None

    def _remember_geo(self):
        g = self._cur_geo()
        if g:
            self._geo[self.mode] = g

    def _mini_size(self):
        self.root.update_idletasks()
        return self.bar.winfo_reqwidth(), self.bar.winfo_reqheight()

    def _restore_geo(self, keep_pos=None):
        """Tamaño/posición guardados para el modo actual (por defecto: tamaño de fábrica, en la
        misma posición que la ventana anterior; la barra mini, arriba y centrada)."""
        mode = self.mode
        bounds = desktop_bounds(self.root)
        if mode == "mini":
            w, h = self._mini_size()
            p = parse_geometry(self._geo.get("mini"))
            x, y = (p[2], p[3]) if p else (bounds[0] + (bounds[2] - w) // 2, bounds[1] + 14)
            g = fit_geometry("{0}x{1}+{2}+{3}".format(w, h, x, y), bounds, (w, h), inside=True)
        else:
            minsz = MIN_COMPACT if mode == "compact" else MIN_FULL
            g = fit_geometry(self._geo.get(mode), bounds, minsz)
            if g is None:
                w, h = GEO_COMPACT if mode == "compact" else GEO_FULL
                w, h = max(w, minsz[0]), max(h, minsz[1])
                g = "{0}x{1}".format(w, h)
                if keep_pos:
                    g = fit_geometry("{0}x{1}+{2}+{3}".format(w, h, keep_pos[0], keep_pos[1]),
                                     bounds, minsz)
        try:
            self.root.geometry(g)
        except Exception:
            pass

    def _decor(self, mini):
        """Mini = ventana sin marco (una barra); el resto, ventana normal."""
        r = self.root
        if bool(r.overrideredirect()) != mini and r.winfo_ismapped():
            r.withdraw()                         # Windows exige ocultarla para cambiar el marco
            self._hidden = True
        r.overrideredirect(mini)
        r.resizable(not mini, not mini)
        r.configure(bg=LINE if mini else BG)

    def _topmost(self):
        return self.mode == "mini" or (self.mode == "compact" and self._pinned)

    def _show(self):
        r = self.root
        if self._hidden or r.state() in ("iconic", "withdrawn"):
            r.deiconify()
            self._hidden = False
        try:
            r.attributes("-topmost", bool(self._topmost()))
        except Exception:
            pass
        r.lift()
        if self.mode == "mini":
            try:
                r.focus_force()                  # para que Esc funcione sin tener que hacer clic
            except Exception:
                pass

    def _apply_mode(self):
        """Muestra solo lo que corresponde a cada modo."""
        mode = self.mode
        mini, small = mode == "mini", mode != "normal"
        for w, _m, _k in self._bar_left:
            w.pack_forget()
        for w, _m in self._bar_right:
            w.pack_forget()
        for w, modes, kw in self._bar_left:
            if mode in modes:
                w.pack(side="left", **kw)
        for w, modes in self._bar_right:
            if mode in modes:
                w.pack(side="right", padx=3)
        self.lbl_timer.config(width=11 if mini else 0)
        if mini:
            self.main.pack_forget()
            self.status.pack_forget()
            self.bar.pack_configure(fill="both", expand=True, padx=1, pady=1)
            self.bar_inner.pack_configure(padx=6, pady=5)
        else:
            self.bar.pack_configure(fill="x", expand=False, padx=0, pady=0)
            self.bar_inner.pack_configure(padx=10, pady=8)
            if not self.status.winfo_manager():
                self.status.pack(fill="x", side="bottom", ipady=3)
            if not self.main.winfo_manager():
                self.main.pack(fill="both", expand=True, before=self.status)
            if mode == "compact":
                self.left.pack_forget()
                self.lbl_hint.pack_forget()
                self.right.pack_configure(padx=0)
                self.main.pack_configure(padx=4, pady=4)
            else:
                self.left.pack(side="left", fill="y", before=self.right)
                self.right.pack_configure(padx=(8, 0))
                self.main.pack_configure(padx=8, pady=8)
                self.lbl_hint.pack(fill="x")
        # clam da a cada botón un ancho mínimo de 11 caracteres; en compacto/mini, el del texto
        for b in (self.btn_prev, self.btn_rec, self.btn_photo, self.btn_vcam, self.btn_pin, self.btn_mode,
                  self.btn_exit, self.btn_wclose, self.btn_wrest, self.btn_wmin):
            b.config(width=0 if small else -11)
        self._mode_var.set(mode)
        self._refresh_pin()
        self._refresh_buttons()
        self.root.update_idletasks()
        if mini:
            self.root.minsize(1, 1)
        elif mode == "compact":    # ancho mínimo: lo que necesita la barra de botones (+ cronómetro)
            self.root.minsize(max(MIN_COMPACT[0], self.bar.winfo_reqwidth() + 90), MIN_COMPACT[1])
        else:
            self.root.minsize(*MIN_FULL)
        self._last_fc = -1                       # fuerza redibujar la imagen al nuevo tamaño

    def set_mode(self, mode):
        """Cambia entre normal, compacto y mini (también desde el icono de la bandeja)."""
        if mode not in MODES:
            return
        if mode == self.mode:
            if mode == "mini" and self._mini_iconified:
                self._mini_back(force=True)
            else:
                self._show()
            return
        self._mini_iconified = False
        if self.editing:
            self._exit_edit()
        self._remember_geo()
        old = parse_geometry(self._geo.get(self.mode))
        self.mode = mode
        self._decor(mode == "mini")
        self._apply_mode()
        self._restore_geo(keep_pos=(old[2], old[3]) if old else None)
        self._show()
        log.info("modo %s", mode)
        self._sync_preview()
        self._tray_update(menu=True)
        self.save_config()                       # el modo se recuerda aunque se cierre a la fuerza

    def _idle_mini(self):
        """Mini sin cámara virtual ni salida en red: no hay vista previa y las cámaras quedan libres."""
        return self.mode == "mini" and not self.vcam_on and not self.ipout_on

    def _sync_preview(self):
        """Mini: sin vista previa (las cámaras quedan libres hasta que se graba).
        Otros modos: vista previa en marcha."""
        if self.recording:
            return
        if self.busy:
            self.root.after(300, self._sync_preview)
            return
        if self._idle_mini():
            if self.previewing:
                self.previewing = False
                self._bg(self.eng.stop, lambda r: self._set_idle("Listo para grabar."))
            else:
                self._set_status("Listo para grabar.")
        elif not self.previewing and not self.paused and self.order:
            self._start_preview(auto=True)

    def mini_minimize(self):
        """Minimiza la barra mini a la barra de tareas. Una ventana sin marco no se puede
        minimizar en Windows, así que se le pone el marco mientras está minimizada."""
        if self.mode != "mini":
            return
        r = self.root
        self._remember_geo()
        self._mini_iconified = True
        r.withdraw()
        r.overrideredirect(False)
        try:
            r.attributes("-topmost", False)
        except Exception:
            pass
        r.iconify()

    def _on_map(self, e):
        if e.widget is self.root and self._mini_iconified and self.mode == "mini":
            self.root.after(40, self._mini_back)       # restaurada desde la barra de tareas

    def _mini_back(self, force=False):
        """Vuelve a poner la barra mini sin marco, en su sitio (force: la restaura aunque
        siga minimizada, p. ej. desde el icono de la bandeja)."""
        if not (self.mode == "mini" and self._mini_iconified):
            return
        r = self.root
        if force and r.state() in ("iconic", "withdrawn"):
            r.deiconify()
            r.update_idletasks()
        if r.state() != "normal":
            return
        self._mini_iconified = False
        self._decor(True)
        self._restore_geo()
        self._show()

    def _on_escape(self, _e=None):
        if self.mode == "mini":
            self.set_mode("normal")
        else:
            self._exit_edit()

    def toggle_pin(self):
        self._pinned = not self._pinned
        self._refresh_pin()
        try:
            self.root.attributes("-topmost", bool(self._topmost()))
        except Exception:
            pass
        self.save_config()

    def _refresh_pin(self):
        self.btn_pin.config(text="📌  Fijada" if self._pinned else "📌  Fijar",
                            style="Accent.TButton" if self._pinned else "TButton")

    # ---- barra mini: arrastrar y menú contextual
    def _mini_press(self, e):
        if self.mode != "mini":
            return
        self._mini_drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _mini_motion(self, e):
        if self.mode == "mini" and self._mini_drag:
            self.root.geometry("+{0}+{1}".format(e.x_root - self._mini_drag[0],
                                                 e.y_root - self._mini_drag[1]))

    def _mini_release(self, _e):
        if self.mode == "mini" and self._mini_drag:
            self._mini_drag = None
            self._remember_geo()

    def _mini_menu(self, e):
        if self.mode == "mini":
            try:
                self.mode_menu.tk_popup(e.x_root, e.y_root)
            finally:
                self.mode_menu.grab_release()

    def _mini_set(self, txt):
        """Texto de estado de la barra mini (corto)."""
        m = re.search(r"\(([\d.]+ MB)\)", txt) if txt.startswith("Grabado:") else None
        if m:
            txt = "✓ Grabado · " + m.group(1)
        elif txt.startswith("Grabando"):
            txt = "Grabando…"
        elif txt.startswith("Cámara virtual activa"):
            txt = "📹 Cámara virtual ON"
        elif txt.startswith("Cámara virtual desactivada"):
            txt = "📹 Cámara virtual OFF"
        elif txt.startswith("En la red:"):
            txt = "🌐 En la red ON"
        elif txt.startswith("Salida en red desactivada"):
            txt = "🌐 En la red OFF"
        txt = txt.strip()
        if len(txt) > 20:
            txt = txt[:19] + "…"
        self.lbl_ms.config(text=txt)

    # ---- icono de la bandeja del sistema (pystray)
    def _tray_start(self):
        if self._tray is not None:
            return True
        try:
            import pystray
        except Exception:
            return False
        app = self

        def post(cmd, arg=None):
            return lambda icon=None, item=None: app._tray_q.append((cmd, arg))

        def mode_item(m):
            return pystray.MenuItem(MODE_NAMES[m], post("mode", m),
                                    checked=lambda item, m=m: app.mode == m, radio=True)
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar", post("show"), default=True, visible=False),
            mode_item("normal"), mode_item("compact"), mode_item("mini"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: "■  Parar grabación" if app.recording else "●  Grabar",
                             post("rec")),
            pystray.MenuItem("Cámara virtual", post("vcam"), checked=lambda item: app.vcam_on),
            pystray.MenuItem("En la red", post("ip"), checked=lambda item: app.ipout_on),
            pystray.MenuItem("Abrir en VLC (Chromecast)", post("vlc"),
                             enabled=lambda item: app.ipout_on),
            pystray.MenuItem("Iniciar con Windows", post("startup"),
                             checked=lambda item: app.startup_on),
            pystray.MenuItem("Reiniciar cámaras (pánico)", post("panic")),
            pystray.MenuItem("Buscar actualizaciones...", post("update")),
            pystray.MenuItem("Salir", post("quit")))
        try:
            icon = pystray.Icon("camwin32", make_tray_image(False), "CamWin32", menu)
            try:
                icon.run_detached()
            except Exception:
                threading.Thread(target=icon.run, daemon=True).start()
        except Exception:
            log.warning("no se pudo crear el icono de la bandeja:\n%s", traceback.format_exc())
            return False
        self._tray = icon
        log.info("icono de bandeja activo")
        return True

    def _tray_title(self):
        """Texto del icono de la bandeja: en Windows se ve como el «tooltip» al dejar el ratón
        encima, así que aquí es donde conviene que se vea la dirección para la salida en red."""
        parts = ["cam-ui"]
        if self.recording:
            parts.append("grabando")
        if self.ipout_on:
            parts.append("red: " + self._ip_dual_url())
        return " · ".join(parts)

    def _tray_update(self, menu=False):
        icon = self._tray
        if icon is None:
            return
        try:
            if self.recording != self._tray_rec:
                self._tray_rec = self.recording
                icon.icon = make_tray_image(self.recording)
                menu = True
            title = self._tray_title()
            if title != self._tray_title_last:
                self._tray_title_last = title
                icon.title = title
            if menu:
                icon.update_menu()
        except Exception:
            log.warning("bandeja: %r", sys.exc_info()[1])

    def toggle_startup(self):
        """Pone o quita cam-ui del inicio de Windows (solo desde el menú de la bandeja)."""
        want = not self.startup_on
        ok, err = startup_set(want)
        if ok:
            self.startup_on = want
            self._set_status("Se iniciará con Windows." if want
                             else "Ya no se inicia con Windows.")
        else:
            log.warning("inicio con Windows: no se pudo cambiar: %s", err)
            self._set_status("No se pudo cambiar el inicio con Windows: {0}".format(err[-200:]))
        self._tray_update(menu=True)

    def _tray_boot(self, force=False):
        """Arranca el icono; si falta pystray ofrece instalarlo (una sola vez)."""
        if self._tray_start():
            if force:
                self._set_status("El icono de la bandeja ya está activo.")
            return
        if self._tray_asked and not force:
            return
        self._tray_asked = True
        self.save_config()
        if messagebox.askyesno(
                "Icono en la bandeja",
                "Para cambiar de modo (normal / compacto / mini) desde un icono junto al reloj hace "
                "falta el paquete «pystray» (pequeño, se instala con pip y necesita internet).\n\n"
                "¿Instalarlo ahora?\n\n(Los modos también se cambian con el botón «Modo» o, en el "
                "modo mini, con clic derecho sobre la barra.)"):
            self._set_status("Instalando pystray…")

            def run():
                ok, msg = pip_install("pystray")
                self._tray_q.append(("installed", (ok, msg)))
            threading.Thread(target=run, daemon=True).start()

    def _tray_drain(self):
        while self._tray_q:
            cmd, arg = self._tray_q.popleft()
            try:
                if cmd == "mode":
                    self.set_mode(arg)
                elif cmd == "show":
                    if self.mode == "mini":
                        self.set_mode("normal")
                    else:
                        self._hidden = True if self.root.state() == "withdrawn" else self._hidden
                        self.root.deiconify()
                        self._show()
                elif cmd == "rec":
                    self.toggle_rec()
                elif cmd == "panic":
                    self.panic_reset()
                elif cmd == "vcam":
                    self.toggle_vcam()
                elif cmd == "vcam_off":
                    self._vcam_disable(arg)
                elif cmd == "ip":
                    self.toggle_ipout()
                elif cmd == "ip_off":
                    self._ip_disable(arg)
                elif cmd == "vlc":
                    self._open_in_vlc()
                elif cmd == "startup":
                    self.toggle_startup()
                elif cmd == "vcam_installed":
                    ok, msg = arg
                    if ok:
                        import importlib
                        importlib.invalidate_caches()
                        self._vcam_enable()
                    else:
                        self._set_status("No se pudo instalar pyvirtualcam.")
                        messagebox.showwarning("pyvirtualcam", "No se pudo instalar pyvirtualcam.\n\n{0}\n\n"
                                               "Puedes instalarlo a mano:  pip install pyvirtualcam".format(msg))
                elif cmd == "badmode":
                    self._on_badmode(*arg)
                elif cmd == "cam_offline":
                    self._on_cam_offline(arg)
                elif cmd == "probe_prog":
                    lab, i, n, m = arg
                    self._set_status("Probando «{0}»: {1}/{2} · {3}".format(lab, i, n, m))
                elif cmd == "panic_done":
                    self._panic_done(*arg)
                elif cmd == "update":
                    self.show_updates()
                elif cmd == "quit":
                    self.on_close()
                elif cmd == "installed":
                    ok, msg = arg
                    if ok:
                        import importlib
                        importlib.invalidate_caches()
                        if self._tray_start():
                            self._set_status("Icono de la bandeja activado.")
                            continue
                    self._set_status("No se pudo instalar pystray.")
                    messagebox.showwarning("pystray", "No se pudo instalar pystray.\n\n{0}\n\n"
                                           "Puedes instalarlo a mano:  pip install pystray".format(msg))
                elif cmd == "phonecam_pkg_installed":
                    ok, msg = arg
                    if ok:
                        import importlib
                        importlib.invalidate_caches()
                        self._phonecam_enable()
                    else:
                        self._set_status("No se pudo instalar el paquete para «Cámara del móvil».")
                        messagebox.showwarning(
                            "Cámara del móvil", "No se pudo instalar.\n\n{0}\n\n"
                            "Puedes instalarlo a mano:  pip install cryptography".format(msg))
                elif cmd == "phonecam_qr_installed":
                    ok, msg, qr_url = arg
                    if ok:
                        import importlib
                        importlib.invalidate_caches()
                        self._set_status("qrcode instalado.")
                        self._phonecam_show_pair_qr(qr_url)
                    else:
                        self._set_status("No se pudo instalar qrcode.")
                        messagebox.showwarning(
                            "Cámara del móvil", "No se pudo instalar.\n\n{0}\n\n"
                            "Puedes instalarlo a mano:  pip install qrcode".format(msg))
                elif cmd == "phonecam_cert_qr_installed":
                    ok, msg, _cert_url = arg
                    if ok:
                        import importlib
                        importlib.invalidate_caches()
                        self._set_status("qrcode instalado.")
                        self._phonecam_show_cert_dialog()
                    else:
                        self._set_status("No se pudo instalar qrcode.")
                        messagebox.showwarning(
                            "Certificado para iPhone/iPad", "No se pudo instalar.\n\n{0}\n\n"
                            "Puedes instalarlo a mano:  pip install qrcode".format(msg))
            except Exception:
                log.error("orden de bandeja %s:\n%s", cmd, traceback.format_exc())

    def show_about(self, *_a):
        dlg = self._about
        if dlg is not None and dlg.alive:
            dlg.lift()
            return
        self._about = AboutDialog(self.root)

    # ---- actualizaciones ----------------------------------------------------------
    def show_updates(self, *_a, check=True):
        dlg = getattr(self, "_updates", None)
        if dlg is not None and dlg.alive:
            dlg.top.lift()
            return dlg
        dlg = self._updates = UpdateDialog(self)
        if check:
            dlg.start_check()
        return dlg

    def update_auto_check(self):
        """Al abrir el programa: si hay una versión nueva, pregunta (salvo que se haya
        pedido no avisar, o que ya se haya mirado hace menos de una hora)."""
        st = update_state()
        if st.get("auto", True) is False or os.environ.get("CAMUI_OFFLINE") or self.recording:
            return
        if time.time() - float(st.get("last_check", 0) or 0) < 3600:
            return
        self._bg(update_check, self._update_probe_done)

    def _update_probe_done(self, r):
        if not r.get("ok") or r.get("state") != "newer" or self.recording:
            return
        if update_state().get("auto_apply"):
            # el mismo camino probado que si se hubiera preguntado y dicho que sí, sin
            # preguntar: se ve la ventana de «Actualizaciones» con el progreso y al acabar
            # se reinicia solo (ver UpdateDialog.apply_result / _on_applied).
            self.show_updates(check=False).apply_result(r)
            return
        go, dont_ask = _ask_update_now(self.root, r["info"].get("pack"))
        if dont_ask:
            update_state()["auto"] = False
            update_state_save()
        if go:
            self.show_updates(check=False).apply_result(r)

    def _silent_watch_tick(self):
        """«Vigilar sola, sin avisos» (casilla oculta de UpdateDialog, tras su atajo
        secreto): comprobación aparte de update_auto_check, repetida mientras el programa
        esté abierto -- no una sola vez al arrancar. Con esa casilla apagada (el caso de
        siempre) esto solo se reprograma solo, sin tocar la red para nada."""
        try:
            if (update_state().get("silent_watch") and not self.recording and not self.busy
                    and not os.environ.get("CAMUI_OFFLINE")):
                self._bg(update_check, self._silent_watch_checked)
        except Exception:
            log.error("vigilancia silenciosa de actualizaciones:\n%s", traceback.format_exc())
        finally:
            self.root.after(SILENT_WATCH_INTERVAL_MS, self._silent_watch_tick)

    def _silent_watch_checked(self, r):
        if not r.get("ok") or r.get("state") != "newer" or self.recording:
            return
        if not update_state().get("silent_watch"):
            return                        # se apagó mientras se comprobaba
        if self.busy:
            return                        # se reintenta solo en la siguiente vuelta
        log.info("vigilancia silenciosa: aplicando pack %s sin avisos", r["info"].get("pack"))
        self._bg(lambda: _update_run(update_apply, r["info"]), self._silent_watch_applied)

    def _silent_watch_applied(self, res):
        if not res.get("ok"):
            log.error("vigilancia silenciosa: no se pudo aplicar: %s", res.get("error"))
            return                        # se reintenta solo en la siguiente vuelta
        self.update_restart_app()

    def update_restart_app(self):
        """Reinicia cam-ui (pasados unos segundos) y cierra este con el guardado de siempre."""
        try:
            update_spawn_restart()
        except Exception as e:
            log.error("actualizar: no se pudo preparar el reinicio: %r", e)
            messagebox.showwarning("Actualizar", "La actualización está instalada, pero no se "
                                   "pudo reiniciar sola.\nCierra y abre de nuevo cam-ui.")
            return
        self.on_close()

    def _arm_watchdog(self, force=False):
        """Si el hilo de la interfaz no vuelve a pasar por aquí en 8 s, faulthandler
        escribe en camui.log la pila de todos los hilos (así se ve dónde se colgó)."""
        now = time.time()
        if _hang_fh is None or (not force and now - self._wd_t < 0.5):
            return
        self._wd_t = now
        try:
            faulthandler.dump_traceback_later(8, repeat=True, file=_hang_fh)
        except Exception:
            pass

    def _cb_exception(self, et, ev, tb):
        log.error("Excepcion en callback de Tk:\n%s", "".join(traceback.format_exception(et, ev, tb)))
        try:
            self._set_status("Error interno (ver camui.log): {0}".format(ev))
        except Exception:
            pass

    def _open_log(self):
        try:
            if os.name == "nt":
                os.startfile(LOGFILE)
            else:
                subprocess.Popen(["xdg-open", LOGFILE])
        except Exception:
            pass

    # ------------------------------------------------------------ variables
    def _make_vars(self):
        c = self._cfg
        t = tk.StringVar
        self.v_struct = t(value=c.get("structure", AUTO))
        self.v_trans = tk.BooleanVar(value=bool(c.get("transpose", False)))
        self.v_canvas = t(value=c.get("canvas") if c.get("canvas") in CANVASES else ADAPT)
        self.v_cwidth = t(value=str(c.get("cwidth", 1280)))
        self.v_fps = t(value=str(c.get("fps", 30)))
        self.v_quality = t(value=c.get("quality", QUALITIES[1][0]))
        self.v_preset = t(value=c.get("preset", "veryfast"))
        self.v_container = t(value=c.get("container", "mp4"))
        self.v_sync = t(value=_pair_label(SYNCS, c.get("sync", "clock")))
        self.v_dir = t(value=c.get("dir", DEF_DIR))
        self.v_name = t(value=c.get("name", "Multivista"))
        vs = (self._cfg.get("vcam") or {}).get("size") if isinstance(self._cfg.get("vcam"), dict) else None
        try:
            vsz = (int(vs[0]), int(vs[1]))
        except Exception:
            vsz = VCAM_SIZES[0][1]
        self.v_vsize = t(value=vcam_size_label(vsz))
        self.v_vsize.trace_add("write", self._on_vsize)
        ipout_cfg = self._cfg.get("ipout") if isinstance(self._cfg.get("ipout"), dict) else {}
        isz = ipout_cfg.get("size")
        try:
            iszv = (int(isz[0]), int(isz[1]))
        except Exception:
            iszv = IP_SIZES[0][1]
        # "auto": la opción «Automático» del pack 29 (ya no existe -- la forma real del mosaico se
        # respeta siempre, ver ip_fit_size); quien la eligió quería la resolución completa, así
        # que se le pone el máximo más grande en vez de volver al recomendado sin avisar.
        self.v_ipsize = t(value=IP_SIZES[-1][0] if ipout_cfg.get("auto") else ip_size_label(iszv))
        self.v_ipsize.trace_add("write", self._on_ipsize)
        iport = (self._cfg.get("ipout") or {}).get("port") if isinstance(self._cfg.get("ipout"), dict) else None
        self.v_ipport = t(value=str(iport or IP_PORT_DEFAULT))
        pport = (self._cfg.get("phonecam") or {}).get("port") \
            if isinstance(self._cfg.get("phonecam"), dict) else None
        self.v_phonecamport = t(value=str(pport or PHONECAM_PORT_DEFAULT))
        self.v_avoff = tk.IntVar(value=int(c.get("av_offset", 0)))
        self.v_abuf = tk.IntVar(value=int(c.get("abuf", 50)))
        a = c.get("audio", {})
        self.arow = {"L": AudioRow(a.get("L"), self._on_change),
                     "R": AudioRow(a.get("R"), self._on_change)}
        for v in (self.v_struct, self.v_trans, self.v_canvas, self.v_cwidth, self.v_fps,
                  self.v_quality, self.v_preset, self.v_container, self.v_sync, self.v_avoff,
                  self.v_abuf):
            v.trace_add("write", self._on_change)
        self._dummy = {"b": tk.BooleanVar(), "s": tk.StringVar(), "i": tk.IntVar(),
                       "d": tk.DoubleVar()}

    def _read_config(self):
        if not os.path.isfile(CONFIG):
            return {}
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return {}
        if cfg.get("version") in (2, 3):
            return cfg
        return self._migrate_v1(cfg)

    @staticmethod
    def _migrate_v1(old):
        """Convierte el config.json de la v1."""
        new = {"version": 3, "fps": old.get("fps", 30),
               "dir": old.get("dir", DEF_DIR), "name": old.get("name", "Multivista")}
        opts = old.get("cam_opts", {}) or {}
        vids = old.get("videos", []) or []
        vids = sorted(vids, key=lambda nme: int(opts.get(nme, {}).get("pos", 99)))
        new["order"] = vids
        new["cams"] = {n: {"use": True, "h": bool(opts.get(n, {}).get("h")),
                           "v": bool(opts.get(n, {}).get("v"))} for n in vids}
        if old.get("audio") and old["audio"] != "(Sin audio)":
            new["audio"] = {"L": {"dev": old["audio"], "chan": "left"},
                            "R": {"dev": old["audio"], "chan": "right"}}
        return new

    # ------------------------------------------------------------ tema
    def _apply_theme(self):
        r = self.root
        st = ttk.Style(r)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                     bordercolor=LINE, lightcolor=BG, darkcolor=BG, troughcolor=PANEL,
                     focuscolor=BG, font=("Segoe UI", 9))
        DIS = "#666b73"
        # clam pinta lo deshabilitado en gris claro (#d9d9d9): se redefine para el tema oscuro
        st.map(".", background=[("disabled", BG), ("active", PANEL2)],
               foreground=[("disabled", DIS)], fieldbackground=[("disabled", BG)])
        st.configure("TFrame", background=BG)
        st.configure("Bar.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Bar.TLabel", background=PANEL)
        st.configure("Title.TLabel", font=("Segoe UI", 9, "bold"))
        st.configure("TLabelframe", background=BG, bordercolor=LINE)
        st.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                     font=("Segoe UI", 9, "bold"))
        st.configure("TNotebook", background=BG, bordercolor=LINE, tabmargins=(2, 4, 2, 0))
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 6))
        st.map("TNotebook.Tab", background=[("selected", PANEL2)],
               foreground=[("selected", FG)])
        st.configure("TButton", background=PANEL2, foreground=FG, padding=(10, 5),
                     borderwidth=1)
        st.map("TButton", background=[("active", "#353a44"), ("disabled", PANEL)],
               foreground=[("disabled", "#666b73")])
        st.configure("Close.TButton", background=PANEL2, foreground=FG, padding=(9, 5), borderwidth=1)
        st.map("Close.TButton", background=[("active", REC_RED), ("disabled", PANEL)],
               foreground=[("active", "white"), ("disabled", "#666b73")])
        st.configure("TMenubutton", background=PANEL2, foreground=FG, padding=(10, 5),
                     arrowcolor=FG, borderwidth=1)
        st.map("TMenubutton", background=[("active", "#353a44"), ("disabled", PANEL)],
               foreground=[("disabled", "#666b73")])
        st.configure("Accent.TButton", background=ACC, foreground="white",
                     font=("Segoe UI", 10, "bold"), padding=(14, 7))
        st.map("Accent.TButton", background=[("active", "#2563eb"), ("disabled", PANEL)],
               foreground=[("disabled", "#666b73")])
        st.configure("Rec.TButton", background=REC_RED, foreground="white",
                     font=("Segoe UI", 10, "bold"), padding=(14, 7))
        st.map("Rec.TButton", background=[("active", "#b52a31"), ("disabled", PANEL)],
               foreground=[("disabled", "#666b73")])
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        for w in ("TCombobox", "TSpinbox", "TEntry"):
            st.configure(w, fieldbackground=PANEL, foreground=FG, insertcolor=FG,
                         arrowcolor=FG, bordercolor=LINE, padding=3)
        for w in ("TEntry", "TSpinbox"):
            st.map(w, fieldbackground=[("disabled", BG), ("readonly", PANEL)],
                   foreground=[("disabled", DIS)])
        st.configure("TCheckbutton", indicatorbackground=PANEL, indicatorforeground=FG)
        st.map("TCheckbutton", background=[("disabled", BG), ("active", BG)],
               foreground=[("disabled", DIS)], indicatorbackground=[("disabled", BG)],
               indicatorforeground=[("disabled", DIS)])
        st.map("TScale", background=[("disabled", BG)], troughcolor=[("disabled", PANEL)])
        st.map("TCombobox", fieldbackground=[("readonly", PANEL), ("disabled", BG)],
               foreground=[("disabled", DIS)],
               selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])
        st.configure("TScale", background=BG)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                     rowheight=24, bordercolor=LINE)
        st.configure("Treeview.Heading", background=PANEL2, foreground=MUTED)
        st.map("Treeview", background=[("selected", "#2f4f86"), ("disabled", PANEL)],
               foreground=[("selected", "white"), ("disabled", MUTED)],
               fieldbackground=[("disabled", PANEL)])
        r.option_add("*TCombobox*Listbox.background", PANEL)
        r.option_add("*TCombobox*Listbox.foreground", FG)
        r.option_add("*TCombobox*Listbox.selectBackground", ACC)

    # ------------------------------------------------------------ interfaz
    def _build_ui(self):
        r = self.root
        bar = ttk.Frame(r, style="Bar.TFrame")
        bar.pack(fill="x")
        inner = ttk.Frame(bar, style="Bar.TFrame")
        inner.pack(fill="x", padx=10, pady=8)
        self.bar_inner = inner
        self.btn_prev = ttk.Button(inner, text="⏸  Pausar vista previa  (F5)", style="Accent.TButton",
                                   command=self.toggle_preview)
        self.btn_rec = ttk.Button(inner, text="●  Grabar  (F9)", style="Rec.TButton",
                                  command=self.toggle_rec, state="disabled")
        self.btn_photo = ttk.Button(inner, text="Foto  (F8)", command=self.take_photo,
                                    state="disabled")
        self.btn_vcam = ttk.Button(inner, text="📹  Cámara virtual", command=self.toggle_vcam)
        self.btn_ip = ttk.Button(inner, text="🌐  En la red", command=self.toggle_ipout)
        self.lbl_timer = ttk.Label(inner, text="", style="Bar.TLabel",
                                   font=("Consolas", 12, "bold"))
        self.lbl_ms = ttk.Label(inner, text="", style="Bar.TLabel", foreground=MUTED,
                                width=20, anchor="w")
        # a la izquierda: (widget, modos en los que se ve, opciones de pack), en este orden
        N, C, M = "normal", "compact", "mini"
        self._bar_left = [(self.btn_prev, {N, C}, {}),
                          (self.btn_rec, {N, C, M}, {"padx": 8}),
                          (self.btn_photo, {N, C}, {}),
                          (self.btn_vcam, {C, M}, {"padx": 3}),   # en normal, la barra ya va justa
                          (self.btn_ip, {C, M}, {"padx": 3}),
                          (self.lbl_timer, {N, C, M}, {"padx": 10}),
                          (self.lbl_ms, {M}, {})]
        # a la derecha: (widget, modos); se empaquetan en este orden (el primero queda más a la derecha)
        self._bar_right = []

        def rbtn(text, cmd, modes):
            b = ttk.Button(inner, text=text, command=cmd)
            self._bar_right.append((b, modes))
            return b
        self.btn_exit = rbtn("Salir", self.on_close, {N})
        rbtn("Abrir carpeta", self._open_dir, {N})
        rbtn("Registro", self._open_log, {N})
        rbtn("⚠  Reiniciar", self.panic_reset, {N})
        rbtn("Diagnóstico", self._diagnose, {N})
        rbtn("☕  Acerca de", self.show_about, {N})
        rbtn("🔄  Actualizaciones", self.show_updates, {N})
        # barra mini (sin marco de ventana): cerrar, restaurar (= modo normal, como Esc) y minimizar
        self.btn_wclose = rbtn("✕", self.on_close, {M})
        self.btn_wclose.config(style="Close.TButton")
        self.btn_wrest = rbtn("⤢", lambda: self.set_mode("normal"), {M})
        self.btn_wmin = rbtn("—", self.mini_minimize, {M})
        # menú de modos (botón «Modo» y clic derecho en la barra mini)
        self._mode_var = tk.StringVar(value=self.mode)
        self.mode_menu = tk.Menu(r, tearoff=0, bg=PANEL2, fg=FG, activebackground=ACC,
                                 activeforeground="white", bd=0, selectcolor=FG)
        for m in MODES:
            self.mode_menu.add_radiobutton(label=MODE_NAMES[m], variable=self._mode_var, value=m,
                                           command=lambda m=m: self.set_mode(m))
        self.mode_menu.add_separator()
        self._vcam_var = tk.BooleanVar(value=False)
        self.mode_menu.add_checkbutton(label="Cámara virtual (OBS)", variable=self._vcam_var,
                                       command=self.toggle_vcam)
        self._ip_var = tk.BooleanVar(value=False)
        self.mode_menu.add_checkbutton(label="En la red (ver desde otro equipo)", variable=self._ip_var,
                                       command=self.toggle_ipout)
        self.mode_menu.add_command(label="Abrir en VLC (para enviar a Chromecast)…",
                                   command=self._open_in_vlc)
        self.mode_menu.add_command(label="Reiniciar cámaras (pánico)  F12", command=self.panic_reset)
        self.mode_menu.add_command(label="Icono de la bandeja…", command=lambda: self._tray_boot(True))
        self.mode_menu.add_command(label="Salir", command=self.on_close)
        self.btn_mode = ttk.Menubutton(inner, text="▭  Modo  ▾", menu=self.mode_menu)
        self._bar_right.append((self.btn_mode, {N, C}))
        self.btn_pin = ttk.Button(inner, text="📌  Fijar", command=self.toggle_pin)
        self._bar_right.append((self.btn_pin, {C}))
        # barra mini: arrastrar desde los huecos y menú con clic derecho
        for w in (bar, inner, self.lbl_timer, self.lbl_ms, self.btn_rec):
            w.bind("<ButtonPress-3>", self._mini_menu, add="+")
        for w in (bar, inner, self.lbl_timer, self.lbl_ms):
            w.bind("<ButtonPress-1>", self._mini_press, add="+")
            w.bind("<B1-Motion>", self._mini_motion, add="+")
            w.bind("<ButtonRelease-1>", self._mini_release, add="+")

        main = ttk.Frame(r)
        main.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(main, width=432)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.main, self.left, self.right, self.bar = main, left, right, bar

        self.nb = ttk.Notebook(left)
        self.nb.pack(fill="both", expand=True)
        tabs = [ttk.Frame(self.nb) for _ in range(5)]
        for tab, txt in zip(tabs, ("Cámaras", "Encuadre", "Color", "Audio", "Salida")):
            self.nb.add(tab, text=" " + txt + " ")
        self.tab_frame = tabs[1]
        inner = [self._scroll_tab(t) for t in tabs]       # cada pestaña se desplaza si no cabe
        self._build_cams_tab(inner[0])
        self._build_frame_tab(inner[1])
        self._build_color_tab(inner[2])
        self._build_audio_tab(inner[3])
        self._build_out_tab(inner[4])
        self.root.bind_all("<MouseWheel>", self._tab_wheel, add="+")
        self.root.bind_all("<Button-4>", self._tab_wheel, add="+")
        self.root.bind_all("<Button-5>", self._tab_wheel, add="+")

        self.view = tk.Canvas(right, bg="#000", highlightthickness=0)
        self.view.pack(fill="both", expand=True)
        self._ph = self.view.create_text(10, 10, fill=MUTED, font=("Segoe UI", 12),
                                         text="Marca una o varias cámaras para ver la vista previa",
                                         justify="center")
        self._img_item = self.view.create_image(0, 0, anchor="center")
        self.view.bind("<Configure>", self._on_view_resize)
        self.view.bind("<ButtonPress-1>", self._view_press)
        self.view.bind("<B1-Motion>", self._view_motion)
        self.view.bind("<ButtonRelease-1>", self._view_release)
        self.view.bind("<Double-Button-1>", self._view_double)
        self.view.bind("<MouseWheel>", self._view_wheel)
        self.view.bind("<Button-4>", self._view_wheel)
        self.view.bind("<Button-5>", self._view_wheel)
        self.root.bind("<Escape>", self._on_escape)
        mrow = ttk.Frame(right)
        mrow.pack(fill="x", pady=(6, 0))
        self.meter = tk.Canvas(mrow, bg=PANEL, height=38, highlightthickness=0)
        self.meter.pack(fill="x")
        self.lbl_info = ttk.Label(right, text="", style="Muted.TLabel")
        self.lbl_info.pack(fill="x", pady=(4, 0))
        self.lbl_hint = ttk.Label(right, style="Muted.TLabel",
                                  text="Clic en una imagen = elegir cámara  ·  doble clic = encuadrar (zoom y posición)")
        self.lbl_hint.pack(fill="x")
        sbar = ttk.Frame(r, style="Bar.TFrame")
        sbar.pack(fill="x", side="bottom")
        self.lbl_ip_bar = ttk.Label(sbar, text="", style="Bar.TLabel", anchor="e", cursor="hand2")
        self.lbl_ip_bar.pack(side="right", ipady=3, padx=(0, 8))
        self.lbl_ip_bar.bind("<Button-1>", self._open_ip_url)
        self.status = ttk.Label(sbar, text="Listo.", style="Bar.TLabel", anchor="w")
        self.status.pack(fill="x", side="left", expand=True, ipady=3)

    def _make_picker(self, tab, live=False):
        """Selector de cámara al principio de una pestaña (sincronizado con la selección)."""
        f = ttk.Frame(tab)
        f.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(f, text="Cámara").pack(side="left")
        var = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=var, state="readonly", width=36)
        cb.pack(side="left", padx=6, fill="x", expand=True)
        cb.bind("<<ComboboxSelected>>", lambda e, v=var: self._pick_from(v))
        self._pickers.append((cb, var))
        if live:
            self._live.add(str(cb))
        return cb

    def _scroll_tab(self, tab):
        """Devuelve un marco interior con barra de desplazamiento dentro de la pestaña."""
        cv = tk.Canvas(tab, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(tab, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(cv)
        win = cv.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        self._tab_canvases = getattr(self, "_tab_canvases", [])
        self._tab_canvases.append(cv)
        return inner

    def _tab_wheel(self, e):
        w = self.root.winfo_containing(e.x_root, e.y_root)
        if w is None or w.winfo_class() in ("TCombobox", "TScale", "TSpinbox", "Text",
                                            "Treeview", "TScrollbar"):
            return
        for cv in getattr(self, "_tab_canvases", []):
            if str(w).startswith(str(cv)) and cv.winfo_ismapped():
                top, bot = cv.yview()
                if top <= 0.0 and bot >= 1.0:
                    return
                d = -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1
                cv.yview_scroll(d * 3, "units")
                return

    def _build_cams_tab(self, tab):
        pad = {"padx": 8, "pady": 4}
        box = ttk.Labelframe(tab, text="1 · Marca las cámaras que quieres usar")
        box.pack(fill="x", **pad)
        tree_box = ttk.Frame(box)
        tree_box.pack(fill="x", padx=6, pady=(6, 2))
        self.tree = ttk.Treeview(tree_box, columns=("use", "name"), show="headings", height=5,
                                 selectmode="browse")
        self.tree.heading("use", text="")
        self.tree.heading("name", text="Cámara", anchor="w")
        self.tree.column("use", width=34, anchor="center", stretch=False)
        self.tree.column("name", width=300, anchor="w")
        tree_sb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_sb.set)
        tree_sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<<TreeviewSelect>>", self._tree_select)
        self.tree.bind("<Double-Button-1>", self._tree_double_click)
        row = ttk.Frame(box)
        row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(row, text="Refrescar dispositivos",
                   command=lambda: self._refresh_devices()).pack(side="left")
        ttk.Button(row, text="Añadir cámara de red…",
                   command=self._add_net_cam).pack(side="left", padx=(6, 0))
        self.btn_net_del = ttk.Button(row, text="Quitar cámara de red",
                                      command=self._del_net_cam)
        self.btn_net_del.pack(side="left", padx=(6, 0))

        mb = ttk.Labelframe(tab, text="2 · Mosaico  (clic = elegir · arrastrar = intercambiar)")
        mb.pack(fill="x", **pad)
        top = ttk.Frame(mb)
        top.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Label(top, text="Disposición  (clic en una)").pack(side="left")
        ttk.Button(top, text="▶", width=3, command=lambda: self._move(1)).pack(side="right")
        ttk.Button(top, text="◀", width=3, command=lambda: self._move(-1)).pack(side="right", padx=2)
        self.gal = tk.Canvas(mb, bg=PANEL, width=372, height=44, highlightthickness=0)
        self.gal.pack(padx=6, pady=(6, 0))
        self.gal.bind("<Button-1>", self._gal_click)
        self._gal_hits = []
        self.map = tk.Canvas(mb, bg=PANEL, width=372, height=176, highlightthickness=0)
        self.map.pack(padx=6, pady=6)
        ttk.Label(mb, style="Muted.TLabel", wraplength=372, justify="left",
                  text="Cada dibujo es una forma de repartir tus cámaras (por filas o por columnas, "
                       "con el formato real de cada una). Arrastra una celda del mapa sobre otra "
                       "para cambiar qué cámara va en cada sitio."
                  ).pack(fill="x", padx=8, pady=(0, 6))
        self.map.bind("<ButtonPress-1>", self._map_press)
        self.map.bind("<ButtonRelease-1>", self._map_release)
        self.map.bind("<Double-Button-1>", self._map_double)

        self.det = ttk.Labelframe(tab, text="3 · Ajustes de la cámara seleccionada")
        self.det.pack(fill="x", **pad)
        d = self.det
        self.det_widgets = []

        def reg(w):
            self.det_widgets.append(w)
            return w

        ttk.Label(d, text="Voltear").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.w_h = reg(ttk.Checkbutton(d, text="Horizontal", variable=self._dummy["b"]))
        self.w_h.grid(row=0, column=1, sticky="w")
        self.w_v = reg(ttk.Checkbutton(d, text="Vertical", variable=self._dummy["b"]))
        self.w_v.grid(row=0, column=2, columnspan=2, sticky="w")
        ttk.Label(d, text="Girar").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.w_rot = reg(ttk.Combobox(d, values=ROTS, width=6, state="readonly",
                                      textvariable=self._dummy["s"]))
        self.w_rot.grid(row=1, column=1, sticky="w")
        ttk.Label(d, text="Retardo ms").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.w_delay = reg(ttk.Spinbox(d, from_=-2000, to=2000, increment=10, width=6,
                                       textvariable=self._dummy["i"]))
        self.w_delay.grid(row=2, column=1, sticky="w")
        ttk.Label(d, text="(+ = se ve más tarde)", style="Muted.TLabel").grid(
            row=2, column=2, columnspan=2, sticky="w", padx=4)
        ttk.Label(d, text="Captura").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        self.w_mode = reg(ttk.Combobox(d, values=["Auto"], width=18, state="readonly",
                                       textvariable=self._dummy["s"]))
        self.w_mode.grid(row=3, column=1, columnspan=2, sticky="w")
        self.w_detect = reg(ttk.Button(d, text="Detectar", command=self._detect_modes))
        self.w_detect.grid(row=3, column=3, sticky="w", padx=4)
        self.lbl_mode_now = ttk.Label(d, text="", style="Muted.TLabel", wraplength=380, justify="left")
        self.lbl_mode_now.grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 2))
        self.w_probe = reg(ttk.Button(d, text="Probar modos (abre la cámara con cada uno)",
                                      command=self._probe_modes))
        self.w_probe.grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 0))
        self.w_reset = reg(ttk.Button(d, text="Restablecer esta cámara", command=self._reset_cam))
        self.w_reset.grid(row=6, column=0, columnspan=4, sticky="w", padx=6, pady=(4, 6))

    def _scale_row(self, parent, r, text, lo, hi, on_reset):
        ttk.Label(parent, text=text, width=11).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        sc = ttk.Scale(parent, from_=lo, to=hi, orient="horizontal", variable=self._dummy["d"])
        sc.grid(row=r, column=1, sticky="ew", padx=4)
        lbl = ttk.Label(parent, text="", width=6, anchor="e", style="Muted.TLabel")
        lbl.grid(row=r, column=2, sticky="e", padx=(0, 6))
        sc.bind("<Double-Button-1>", lambda e: on_reset())
        parent.columnconfigure(1, weight=1)
        return sc, lbl

    def _build_frame_tab(self, tab):
        pad = {"padx": 8, "pady": 4}
        self._make_picker(tab)
        self.frame_widgets = []
        lf = ttk.Labelframe(tab, text="Forma de la celda")
        lf.pack(fill="x", **pad)
        ttk.Label(lf, text="Celda").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.w_tile = ttk.Combobox(lf, values=[t[0] for t in TILES], width=28,
                                   textvariable=self._dummy["s"])
        self.w_tile.grid(row=0, column=1, sticky="w")
        ttk.Label(lf, style="Muted.TLabel", wraplength=370, justify="left",
                  text="«Como la cámara» respeta su formato real: no se pierde nada y el lienzo "
                       "se adapta. Con otra forma se recorta lo que sobra, y con el zoom y la "
                       "posición eliges qué parte se queda. Puedes escribir cualquier proporción "
                       "(p. ej. 2.2:1 o 1008:720)."
                  ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        self.w_bars = ttk.Button(lf, text="Quitar barras negras de la imagen",
                                 command=self._autocrop)
        self.w_bars.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))
        zf = ttk.Labelframe(tab, text="Zoom y posición")
        zf.pack(fill="x", **pad)
        self.sc_zoom, self.lbl_zoom = self._scale_row(
            zf, 0, "Zoom", 100, 600, lambda: self._cur_set("zoom", 100.0))
        self.sc_cx, self.lbl_cx = self._scale_row(
            zf, 1, "Horizontal", 0, 100, lambda: self._cur_set("cx", 50.0))
        self.sc_cy, self.lbl_cy = self._scale_row(
            zf, 2, "Vertical", 0, 100, lambda: self._cur_set("cy", 50.0))
        self.w_center = ttk.Button(zf, text="Centrar y quitar zoom", command=self._reset_frame)
        self.w_center.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(4, 6))
        ttk.Label(zf, style="Muted.TLabel", text="(doble clic en un deslizador = valor inicial)"
                  ).grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))
        self.w_edit = ttk.Button(tab, text="✂   Encuadrar sobre la imagen…", style="Accent.TButton",
                                 command=self._edit_toggle)
        self.w_edit.pack(fill="x", padx=8, pady=(8, 2), ipady=4)
        ttk.Label(tab, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Muestra la imagen completa de la cámara con un marco: arrástralo para "
                       "moverlo y usa la rueda del ratón para el zoom. Esc o «Listo» para volver "
                       "al mosaico. También: doble clic sobre una cámara en la vista previa."
                  ).pack(fill="x", padx=10, pady=4)
        self.frame_widgets = [self.w_tile, self.w_bars, self.sc_zoom, self.sc_cx, self.sc_cy, self.w_center,
                              self.w_edit]

    def _build_color_tab(self, tab):
        pad = {"padx": 8, "pady": 4}
        self._make_picker(tab, live=True)
        lf = ttk.Labelframe(tab, text="Corrección de color de esta cámara")
        lf.pack(fill="x", **pad)
        self.color_sc, self.color_lbl = {}, {}
        for r, (key, txt) in enumerate(COLOR_LABELS):
            sc, lbl = self._scale_row(lf, r, txt, -100, 100,
                                      lambda k=key: self._cur_color_set(k, 0.0))
            self.color_sc[key], self.color_lbl[key] = sc, lbl
            self._live.add(str(sc))
        ttk.Label(lf, style="Muted.TLabel", wraplength=370, justify="left",
                  text="Temperatura: + más cálido (naranja), − más frío (azul). Los cambios se "
                       "ven al instante, también mientras grabas. Doble clic = valor inicial."
                  ).grid(row=len(COLOR_LABELS), column=0, columnspan=3, sticky="w", padx=6,
                         pady=(2, 4))
        bf = ttk.Frame(tab)
        bf.pack(fill="x", padx=8, pady=2)
        self.w_color_reset = ttk.Button(bf, text="Restablecer color", command=self._reset_color)
        self.w_color_reset.pack(side="left")
        self.w_color_copy = ttk.Button(bf, text="Copiar a todas las cámaras",
                                       command=self._copy_color_all)
        self.w_color_copy.pack(side="left", padx=6)
        self._live.add(str(self.w_color_reset))
        self._live.add(str(self.w_color_copy))
        df = ttk.Labelframe(tab, text="Controles del driver de la cámara")
        df.pack(fill="x", **pad)
        ttk.Label(df, style="Muted.TLabel", wraplength=370, justify="left",
                  text="Exposición y balance de blancos automáticos, enfoque, ganancia… los "
                       "ofrece el propio driver, que cada fabricante implementa a su manera "
                       "(ffmpeg solo puede abrir su cuadro nativo de Windows). Úsalo para "
                       "desactivar lo automático y que la imagen no «respire»."
                  ).pack(fill="x", padx=6, pady=(6, 4))
        self.w_driver = ttk.Button(df, text="Abrir propiedades del driver…",
                                   command=self._driver_dialog)
        self.w_driver.pack(anchor="w", padx=6, pady=(0, 8))
        self.color_widgets = list(self.color_sc.values()) + [self.w_color_reset, self.w_color_copy]


    def _build_audio_tab(self, tab):
        pad = {"padx": 8, "pady": 4}
        ttk.Label(tab, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Cada canal de la salida se alimenta del dispositivo y del canal de "
                       "origen que elijas. Ej.: micro A al izquierdo y micro B al derecho."
                  ).pack(fill="x", padx=10, pady=(8, 2))
        self.adev_cmbs = []
        for side, title in (("L", "Salida IZQUIERDA (L)"), ("R", "Salida DERECHA (R)")):
            ar = self.arow[side]
            lf = ttk.Labelframe(tab, text=title)
            lf.pack(fill="x", **pad)
            ttk.Label(lf, text="Dispositivo").grid(row=0, column=0, sticky="w", padx=6, pady=3)
            cb = ttk.Combobox(lf, textvariable=ar.dev, values=[NO_AUDIO], width=38,
                              state="readonly")
            cb.grid(row=0, column=1, columnspan=3, sticky="w", pady=3)
            self.adev_cmbs.append(cb)
            ttk.Label(lf, text="Tomar de").grid(row=1, column=0, sticky="w", padx=6, pady=3)
            ttk.Combobox(lf, textvariable=ar.chan, values=[c[0] for c in CHANS], width=16,
                         state="readonly").grid(row=1, column=1, sticky="w")
            ttk.Label(lf, text="Retardo ms").grid(row=1, column=2, sticky="e", padx=4)
            ttk.Spinbox(lf, from_=0, to=1000, increment=5, width=5,
                        textvariable=ar.delay).grid(row=1, column=3, sticky="w")
            ttk.Label(lf, text="Ganancia dB").grid(row=2, column=0, sticky="w", padx=6, pady=3)
            ttk.Scale(lf, from_=-30, to=20, variable=ar.gain, orient="horizontal",
                      length=180, command=lambda v, a=ar: a.gain.set(int(float(v)))
                      ).grid(row=2, column=1, columnspan=2, sticky="w")
            ttk.Label(lf, textvariable=ar.gain, width=4).grid(row=2, column=3, sticky="w")
        pf = ttk.Labelframe(tab, text="Preajustes")
        pf.pack(fill="x", **pad)
        for txt, fn in (("Estéreo normal", self._preset_stereo), ("Mono a ambos", self._preset_mono),
                        ("Un micro por canal", self._preset_split)):
            ttk.Button(pf, text=txt, command=fn).pack(side="left", padx=6, pady=6)
        sf = ttk.Labelframe(tab, text="Sincronía audio ↔ vídeo")
        sf.pack(fill="x", **pad)
        ttk.Label(sf, text="Ajuste A/V ms").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Spinbox(sf, from_=-1000, to=1000, increment=10, width=6,
                    textvariable=self.v_avoff).grid(row=0, column=1, sticky="w")
        ttk.Label(sf, style="Muted.TLabel", justify="left", wraplength=250,
                  text="Calibra una vez: graba una palmada ante la cámara. Si en la "
                       "reproducción el sonido llega antes que la imagen, sube el valor; "
                       "si llega después, bájalo."
                  ).grid(row=0, column=2, rowspan=2, padx=8, pady=4, sticky="w")
        ttk.Label(sf, text="Búfer audio ms").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Spinbox(sf, from_=0, to=500, increment=10, width=6,
                    textvariable=self.v_abuf).grid(row=1, column=1, sticky="w")
        ttk.Label(sf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Menos búfer = menos retardo. Pon 0 si un micro da error al abrirse."
                  ).grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))

    def _build_out_tab(self, tab):
        pad = {"padx": 8, "pady": 4}
        lf = ttk.Labelframe(tab, text="Formato del vídeo")
        lf.pack(fill="x", **pad)

        def row(r, label, widget, sticky="w"):
            ttk.Label(lf, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=4)
            widget.grid(row=r, column=1, sticky=sticky, pady=4)

        row(0, "Lienzo", ttk.Combobox(lf, textvariable=self.v_canvas, width=22,
                                      values=CANVASES, state="readonly"))
        row(1, "Ancho del vídeo (px)", ttk.Combobox(lf, textvariable=self.v_cwidth, width=8,
                                                     values=["960", "1280", "1600", "1920", "2560"]))
        row(2, "FPS", ttk.Spinbox(lf, from_=5, to=60, width=6, textvariable=self.v_fps))
        self.lbl_size = ttk.Label(lf, text="", style="Muted.TLabel", wraplength=370, justify="left")
        self.lbl_size.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 2))
        ttk.Label(lf, style="Muted.TLabel", wraplength=370, justify="left",
                  text="«Adaptado al contenido» crea un vídeo con la forma justa para que ninguna "
                       "cámara pierda nada (p. ej. dos 4:3 arriba y un 16:9 abajo). Con un formato "
                       "fijo (16:9, 9:16…) el mosaico se centra y se rellena con barras negras."
                  ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        qf = ttk.Labelframe(tab, text="Codificación")
        qf.pack(fill="x", **pad)
        ttk.Label(qf, text="Calidad").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(qf, textvariable=self.v_quality, values=[q[0] for q in QUALITIES],
                     width=24, state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(qf, text="Velocidad CPU").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(qf, textvariable=self.v_preset, values=PRESETS, width=12,
                     state="readonly").grid(row=1, column=1, sticky="w")
        ttk.Label(qf, text="Contenedor").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(qf, textvariable=self.v_container, values=["mp4", "mkv"], width=8,
                     state="readonly").grid(row=2, column=1, sticky="w")
        ttk.Label(qf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="MKV es más seguro si el programa se cierra a mitad de grabación."
                  ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        vf = ttk.Labelframe(tab, text="Cámara virtual (videollamadas)")
        vf.pack(fill="x", **pad)
        self.btn_vcam2 = ttk.Button(vf, text="📹  Activar cámara virtual", command=self.toggle_vcam)
        self.btn_vcam2.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6))
        self._live.add(str(self.btn_vcam2))              # se puede apagar mientras se graba
        ttk.Label(vf, text="Tamaño").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(vf, textvariable=self.v_vsize, values=[z[0] for z in VCAM_SIZES], width=24,
                     state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(vf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Ofrece el mosaico como una cámara más (se elige «OBS Virtual Camera» en "
                       "Teams, Zoom, Meet…); también en la bandeja, el menú «Modo» y, en compacto y "
                       "mini, el botón 📹. Necesita OBS Studio instalado y "
                       "el paquete pyvirtualcam. Solo vídeo, sin audio; el mosaico se centra con "
                       "barras negras en 16:9. Solo un programa puede usar esa cámara a la vez."
                  ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        nf = ttk.Labelframe(tab, text="En la red (verlo desde otro equipo o móvil)")
        nf.pack(fill="x", **pad)
        self.btn_ip2 = ttk.Button(nf, text="🌐  Activar en la red", command=self.toggle_ipout)
        self.btn_ip2.grid(row=2, column=0, sticky="w", padx=6, pady=(2, 6))
        self._live.add(str(self.btn_ip2))                # se puede apagar mientras se graba
        self.btn_vlc = ttk.Button(nf, text="▶️  Abrir en VLC", command=self._open_in_vlc)
        self.btn_vlc.grid(row=2, column=1, sticky="w", padx=6, pady=(2, 6))
        self._live.add(str(self.btn_vlc))
        ttk.Label(nf, text="Tamaño").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(nf, textvariable=self.v_ipsize, values=[z[0] for z in IP_SIZES], width=28,
                     state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(nf, text="Puerto").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        e_ipport = ttk.Entry(nf, textvariable=self.v_ipport, width=8)
        e_ipport.grid(row=1, column=1, sticky="w")
        e_ipport.bind("<Return>", self._on_ipport_commit)
        e_ipport.bind("<FocusOut>", self._on_ipport_commit)
        self.frm_ipurl = ttk.Frame(nf)
        self.frm_ipurl.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 2))
        self._set_link_list(self.frm_ipurl, "No está activa.", [])
        ttk.Label(nf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="«Tamaño» es el máximo: se manda con la misma forma que el mosaico de aquí "
                       "(con su encuadre, sin añadir barras negras), lo más grande que quepa, y "
                       "nunca mayor que el propio mosaico -- así otro cam-ui que lo añada como "
                       "«cámara de red» lo recibe con el mismo formato. Un máximo mayor da más "
                       "resolución a cambio de más wifi/CPU. "
                       "Abre esa dirección con el navegador de otro equipo o móvil de la misma red "
                       "(wifi) para ver el mosaico en directo, con pantalla completa; también en la "
                       "bandeja, el menú «Modo» y, en compacto y mini, el botón 🌐 (clic en la "
                       "dirección de la barra de estado la abre directamente). Si hay micrófono "
                       "activado también se oye (botón «Activar sonido» en la página, con su propio "
                       "control de volumen). Solo para ver: nadie puede controlar cam-ui desde ahí, y "
                       "es independiente de grabar y de la cámara virtual (se puede usar a la vez). "
                       "Sin contraseña: cualquiera en esa red puede verlo (y oírlo) mientras esté "
                       "activa. La primera vez, Windows puede pedir permiso en el cortafuegos: dalo "
                       "para «redes privadas». «Abrir en VLC» (si está instalado) lo abre ahí en vez "
                       "del navegador; desde el propio menú de VLC Reproducción -> Renderizador se "
                       "puede enviar a un Chromecast de la misma red -- VLC hace la conversión él "
                       "solo, sin nada más que instalar aquí. VLC no sabe enviar a AirPlay (solo "
                       "existe como función del propio macOS)."
                  ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        pf = ttk.Labelframe(tab, text="Cámara del móvil (desde su navegador, sin instalar nada)")
        pf.pack(fill="x", **pad)
        ttk.Label(pf, text="Puerto").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 4))
        e_pcport = ttk.Entry(pf, textvariable=self.v_phonecamport, width=8)
        e_pcport.grid(row=0, column=1, sticky="w")
        e_pcport.bind("<Return>", self._on_phonecamport_commit)
        e_pcport.bind("<FocusOut>", self._on_phonecamport_commit)
        self.frm_phonecamurl = ttk.Frame(pf)
        self.frm_phonecamurl.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 2))
        self._set_link_list(self.frm_phonecamurl, "No está activa.", [])
        self.lbl_phonecamqr = ttk.Label(pf)
        self.lbl_phonecamqr.grid(row=2, column=0, padx=6, pady=(2, 4), sticky="w")
        ttk.Label(pf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Siempre activa: entra en el móvil a cualquiera de las direcciones de abajo "
                       "(con «/add» al final) y toca «Escanear»: en cuanto dé el permiso de "
                       "cámara, aquí mismo aparecerá un código QR para que lo lea con la propia cámara "
                       "del móvil -- en cuanto lo lea, se añade solo al mosaico, sin tocar nada más "
                       "aquí. La primera vez el navegador avisará de que la conexión «no es privada» "
                       "-- es normal (es tu propio cam-ui, no un sitio de verdad), toca «Avanzado» y "
                       "«Continuar». De momento solo vídeo, sin el sonido del móvil. Tener que ver el "
                       "QR en la pantalla del PC es lo que de verdad protege esto: nadie en la wifi "
                       "puede añadirse sin estar delante del ordenador. Sin ningún móvil conectado no "
                       "consume apenas recursos (solo escucha en el puerto, igual que «En la red»)."
                  ).grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))
        self.btn_phonecam_cert = ttk.Button(
            pf, text="📲  Instalar certificado (solo iPhone/iPad)",
            command=self._phonecam_show_cert_dialog)
        self.btn_phonecam_cert.grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))
        self._live.add(str(self.btn_phonecam_cert))       # no toca nada de la grabación en marcha
        ttk.Label(pf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="En iPhone/iPad hace falta un paso más (botón de arriba, solo una vez): "
                       "Safari no deja usar la cámara ahí sin él, por mucho que se acepte el "
                       "aviso de «conexión no privada» -- eso basta para ver la página, pero no "
                       "para la cámara. En Android no hace falta nada de esto."
                  ).grid(row=5, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))
        self.btn_phonecam_devices = ttk.Button(
            pf, text="👥  Dispositivos recordados",
            command=self._phonecam_show_devices_dialog)
        self.btn_phonecam_devices.grid(row=6, column=0, columnspan=3, sticky="w", padx=6,
                                       pady=(0, 6))
        self._live.add(str(self.btn_phonecam_devices))     # no toca nada de la grabación en marcha
        ttk.Label(pf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Un móvil que ya se emparejó una vez (con el QR de siempre) entra solo "
                       "la próxima vez que abra esa misma dirección, sin volver a leer ningún "
                       "código -- aquí se puede ver cuáles, y «olvidar» el que se quiera."
                  ).grid(row=7, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))
        df = ttk.Labelframe(tab, text="Destino")
        df.pack(fill="x", **pad)
        ttk.Label(df, text="Carpeta").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(df, textvariable=self.v_dir, width=34).grid(row=0, column=1, pady=4)
        ttk.Button(df, text="...", width=3, command=self._pick_dir).grid(row=0, column=2, padx=4)
        ttk.Label(df, text="Nombre base").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(df, textvariable=self.v_name, width=34).grid(row=1, column=1, pady=4)
        sf = ttk.Labelframe(tab, text="Avanzado")
        sf.pack(fill="x", **pad)
        ttk.Label(sf, text="Sincronía").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(sf, textvariable=self.v_sync, values=[s[0] for s in SYNCS], width=28,
                     state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(sf, style="Muted.TLabel", wraplength=380, justify="left",
                  text="«Reloj común» alinea todas las cámaras y el audio en una misma línea "
                       "de tiempo. Si una cámara se comporta raro, prueba «Marcas del dispositivo»."
                  ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))

    # ------------------------------------------------------------ dispositivos
    def _refresh_devices(self, first=False):
        self._set_status("Buscando dispositivos…")

        def work():
            return list_devices()

        def done(res):
            if isinstance(res, Exception):
                self._set_status("No se pudieron listar los dispositivos: {0}".format(res))
                return
            self.vdevs, self.adevs = res
            if not self.vdevs:                          # plan B en el hilo principal
                self.vdevs = label_devices(list_video_pygrabber())
                log.info("pygrabber: %d camaras", len(self.vdevs))
            self.vdevs = list(self.vdevs) + list(self.net_cams.values())
            self.vdev = dict((d["label"], d) for d in self.vdevs)
            self.adev = dict((d["label"], d) for d in self.adevs)
            self._apply_devices(first)

        self._bg(work, done)

    def _apply_devices(self, first):
        cc = self._cfg.get("cams", {})
        for d in self.vdevs:
            lab = d["label"]
            if lab not in self.cams:
                self.cams[lab] = CamState(lab, cc.get(lab), self._cb)
        if first:
            self.order = [n for n in self._cfg.get("order", []) if n in self.vdev and
                          self.cams[n].use]
            if not self._cfg and self.vdevs:                 # primer arranque
                self.cams[self.vdevs[0]["label"]].use = True
        self.order = [n for n in self.order if n in self.vdev and self.cams[n].use]
        for lab, cs in self.cams.items():
            if cs.use and lab in self.vdev and lab not in self.order:
                self.order.append(lab)
        vals = [NO_AUDIO] + [d["label"] for d in self.adevs]
        for cb in self.adev_cmbs:
            cb.config(values=vals)
        if first and not self._cfg.get("audio") and self.adevs:      # audio por defecto
            first_a = self.adevs[0]["label"]
            self.arow["L"].dev.set(first_a)
            self.arow["R"].dev.set(first_a)
            self.arow["L"].chan.set(_pair_label(CHANS, "left"))
            self.arow["R"].chan.set(_pair_label(CHANS, "right"))
        for ar in self.arow.values():
            if ar.dev.get() not in vals:
                ar.dev.set(NO_AUDIO)
        if self.sel not in self.order:
            self.sel = self.order[0] if self.order else None
        self._fill_tree()
        self._bind_detail()
        self._redraw_map()
        self._update_info()
        if not self.vdevs:
            self._set_status("No se detectaron cámaras. Comprueba ffmpeg.exe y las cámaras.")
        elif self.order and not self.paused and not self.previewing:
            self._set_status("Iniciando la vista previa…")
            self.root.after(300, self._auto_preview)
        else:
            self._set_status("Listo. Marca al menos una cámara.")

    def _add_net_cam(self):
        self._net_cam_dialog()

    def _edit_net_cam(self, label):
        self._net_cam_dialog(editing=label)

    def _rename_cam_state(self, old_label, new_label):
        """Mueve los ajustes (zoom, encuadre, color...) de una cámara de un nombre a otro, para
        cuando editar una cámara de red le cambia el nombre (y por tanto la etiqueta): se
        reconstruye el CamState con los mismos valores en vez de perderlos como si fuera otra
        cámara distinta."""
        old = self.cams.get(old_label)
        if old is None:
            return
        new_cs = CamState(new_label, old.to_cfg(), self._cb)
        new_cs.modes_tried = old.modes_tried
        new_cs.ok_modes = dict(old.ok_modes)
        new_cs.bad_modes = set(old.bad_modes)
        del self.cams[old_label]
        self.cams[new_label] = new_cs
        if old_label in self.order:
            self.order[self.order.index(old_label)] = new_label
        if self.sel == old_label:
            self.sel = new_label

    def _net_cam_dialog(self, editing=None):
        """Cámara de red (http/https MJPEG o rtsp): el móvil con una app como IP Webcam, en la
        misma wifi, o la «salida en red» de otro PC con cam-ui. En pruebas: no hay forma de
        probarla aquí con un teléfono real. editing=<etiqueta>: se edita esa cámara ya añadida
        (doble clic en la lista) en vez de crear una nueva -- mismo diálogo, con el nombre y la
        URL ya puestos, conservando su sitio en el mosaico y sus ajustes (zoom, color...)."""
        if self.recording or self.busy:
            return
        cur = self.net_cams.get(editing) if editing else None
        if editing and cur is None:
            return
        top = tk.Toplevel(self.root)
        top.title("Editar cámara de red" if cur else "Añadir cámara de red")
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        frm = ttk.Frame(top)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frm, text="Nombre").grid(row=0, column=0, sticky="w", pady=(0, 4))
        name_var = tk.StringVar(value=cur["name"] if cur else "Móvil")
        ttk.Entry(frm, textvariable=name_var, width=38).grid(row=0, column=1, pady=(0, 4))
        ttk.Label(frm, text="URL").grid(row=1, column=0, sticky="w")
        url_var = tk.StringVar(value=cur["spec"] if cur else "")
        e_url = ttk.Entry(frm, textvariable=url_var, width=38)
        e_url.grid(row=1, column=1)
        ttk.Label(frm, style="Muted.TLabel", wraplength=380, justify="left",
                  text="La dirección que da la app del móvil (p. ej. «IP Webcam» en Android): algo "
                       "como http://192.168.1.50:8080/video, o por rtsp "
                       "rtsp://192.168.1.50:8080/h264_ulaw.sdp. También vale la dirección de la "
                       "«salida en red» de otro PC con cam-ui -- la URL completa, terminada en "
                       "/stream.mjpg (algo como http://192.168.1.50:8554/stream.mjpg) -- para "
                       "encadenar varios equipos. El otro aparato y este ordenador deben estar "
                       "en la misma red wifi, y el otro PC debe tener «En la red» activada. Solo "
                       "vídeo, sin audio. Si esta cámara va lenta o a tirones: aquí no se puede "
                       "elegir la resolución con la que llega, pero si el origen es otro PC con "
                       "cam-ui, en SU Ajustes → «En la red» → tamaño se puede bajar la que manda "
                       "-- pesa menos de recibir aquí sin tocar nada en este equipo."
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 10))
        err = ttk.Label(frm, text="", foreground="#ff6b6b", background=BG)
        err.grid(row=3, column=0, columnspan=2, sticky="w")
        bar = ttk.Frame(frm)
        bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))

        def ok():
            name = name_var.get().strip() or "Móvil"
            url = url_var.get().strip()
            if not _is_net_spec(url):
                err.config(text="La URL debe empezar por http://, https:// o rtsp://")
                return
            label = base = "🌐 " + name
            n = 2
            while label in self.vdev and not self.vdev[label].get("net"):
                label = "{0} #{1}".format(base, n)
                n += 1
            if editing and label != editing:
                self.net_cams.pop(editing, None)
                self._rename_cam_state(editing, label)
            self.net_cams[label] = {"name": name, "alt": None, "label": label, "spec": url,
                                    "net": True}
            top.destroy()
            self._refresh_devices()
            if editing:
                self._set_status("Cámara de red «{0}» actualizada.".format(name))
            else:
                self._set_status("Cámara de red «{0}» añadida; márcala en la lista para usarla.".format(name))

        ttk.Button(bar, text="Cancelar", command=top.destroy).pack(side="right")
        ttk.Button(bar, text="Guardar" if cur else "Añadir", command=ok).pack(side="right", padx=(0, 6))
        e_url.focus_set()
        if cur:
            e_url.select_range(0, "end")
        top.bind("<Return>", lambda e: ok())
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            px, py = self.root.winfo_rootx(), self.root.winfo_rooty()
            top.geometry("+{0}+{1}".format(px + 60, py + 60))
        except Exception:
            pass

    def _del_net_cam(self):
        lab = self.sel
        if not lab or not self.vdev.get(lab, {}).get("net") or self.recording or self.busy:
            return
        if not messagebox.askokcancel("Quitar cámara de red", "¿Quitar «{0}»?".format(lab)):
            return
        self.net_cams.pop(lab, None)
        self._refresh_devices()
        self._set_status("Cámara de red quitada.")

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for d in self.vdevs:
            lab = d["label"]
            self.tree.insert("", "end", iid=lab,
                             values=("☑" if self.cams[lab].use else "☐", lab))
        if self.sel in self.vdev:
            self._set_tree_sel(self.sel)

    def _tree_click(self, e):
        if self.recording or self.busy:
            return "break"
        row = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if row and col == "#1":
            cs = self.cams[row]
            cs.use = not cs.use
            if cs.use and row not in self.order:
                self.order.append(row)
                self.sel = row
            elif not cs.use and row in self.order:
                self.order.remove(row)
                if self.sel == row:
                    self.sel = self.order[0] if self.order else None
            self.tree.set(row, "use", "☑" if cs.use else "☐")
            self._bind_detail()
            self._on_change()
            return "break"

    def _tree_double_click(self, e):
        """Doble clic en una cámara de red (añadida a mano): la abre para editar nombre/URL,
        igual que al añadirla -- en vez de tener que quitarla y volver a crearla (se pierden
        el zoom, el encuadre y el color si se hace así)."""
        if self.recording or self.busy:
            return "break"
        row = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if row and col == "#2" and self.vdev.get(row, {}).get("net"):
            self._edit_net_cam(row)
            return "break"

    def _tree_select(self, _e=None):
        # <<TreeviewSelect>> también se dispara al cambiar la selección por código:
        # sin estas guardas, _select -> selection_set -> _tree_select -> ... en bucle.
        if self._sel_guard:
            return
        s = self.tree.selection()
        if s and s[0] in self.order and s[0] != self.sel:
            self._select(s[0])

    def _set_tree_sel(self, lab):
        try:
            if tuple(self.tree.selection()) == (lab,):
                return
            self._sel_guard = True
            self.tree.selection_set(lab)
        finally:
            self.root.after(80, lambda: setattr(self, "_sel_guard", False))

    def _detect_modes(self):
        cs = self.cams.get(self.sel)
        if not cs or self.previewing or self.busy:
            if self.previewing:
                self._set_status("Detén la vista previa para detectar los modos de la cámara.")
            return
        if self.vdev.get(self.sel, {}).get("net"):
            self._set_status("Las cámaras de red no tienen modos de captura que detectar.")
            return
        spec = self.vdev[self.sel]["spec"]
        lab = self.sel
        self._set_status("Consultando modos de captura de «{0}»…".format(lab))

        def done(res):
            modes = res if isinstance(res, list) else []
            cs.modes = modes
            self.w_mode.config(values=["Auto"] + modes)
            self._set_status("{0} modos detectados.".format(len(modes)) if modes
                             else "La cámara no informó de modos; se usará «Auto».")

        self._bg(lambda: list_camera_modes(spec), done)

    def _probe_modes(self):
        """Abre la cámara con cada uno de sus modos para ver cuáles funcionan de verdad y
        cuántos fps entregan (lo que anuncia el driver y lo que da no siempre coinciden)."""
        cs = self.cams.get(self.sel)
        if not cs or self.busy or self.recording or self.sel not in self.vdev:
            if self.recording:
                self._set_status("No se pueden probar modos mientras se graba.")
            return
        if self.vdev.get(self.sel, {}).get("net"):
            self._set_status("Las cámaras de red no tienen modos de captura que probar.")
            return
        spec, lab = self.vdev[self.sel]["spec"], self.sel
        if not messagebox.askokcancel(
                "Probar modos",
                "Se abrirá «{0}» con cada uno de sus modos unos segundos (puede tardar medio minuto) "
                "para ver cuáles funcionan y cuántos fps dan de verdad.\n\nLa vista previa se detiene "
                "mientras tanto. Con poca luz las cámaras baratas bajan los fps: enciende una luz antes "
                "de probar.".format(lab)):
            return
        resume = self.previewing and not self.paused
        self.previewing = False
        self._set_status("Probando los modos de «{0}»…".format(lab))

        def work():
            self.eng.stop()
            time.sleep(RELEASE_GAP)
            modes = list(cs.modes) or list_camera_modes(spec)
            prog = lambda i, n, m: self._tray_q.append(("probe_prog", (lab, i, n, m)))
            return modes, probe_modes(self.eng.ffmpeg, spec, modes, progress=prog)

        def done(res):
            if isinstance(res, tuple):
                self._probe_apply(lab, cs, *res)
            else:
                self._set_status("No se pudieron probar los modos: {0}".format(res))
            if resume and not self._idle_mini():
                self._start_preview(auto=True)

        self._bg(work, done)

    def _probe_apply(self, lab, cs, modes, results):
        good = {r["mode"]: r["fps"] for r in results if r["fps"] > 0}
        works = {mode_key(m) for m in good}
        dead = {mode_key(r["mode"]) for r in results if r["fps"] <= 0} - works
        cs.modes_tried = True
        cs.ok_modes = good
        cs.bad_modes = {m for m in modes if mode_key(m) in dead}
        cs.modes = [m for m in modes if m not in cs.bad_modes]
        if cs.mode.get() in cs.bad_modes:
            self._loading = True
            try:
                cs.mode.set("Auto")
            finally:
                self._loading = False
        if self.sel == lab:
            self.w_mode.config(values=["Auto"] + cs.modes)
        pick = pick_auto_mode(cs.modes, self._fps(), ok=cs.ok_modes)
        L = ["PRUEBA DE MODOS  ·  «{0}»".format(lab), ""]
        for r in results:
            L.append("  {0:<34} {1}".format(r["mode"], "✓ {0:.1f} fps reales".format(r["fps"]) if r["fps"] > 0
                                           else "✗ no funciona"))
        L += ["", "RESULTADO"]
        L.append("  • «Auto» usará: {0}".format(pick or "el modo de fábrica del driver"))
        if cs.bad_modes:
            L.append("  • Descartados (la cámara los anuncia pero no los abre): {0}".format(
                ", ".join(sorted({mode_key(m).replace('|', ' ') for m in cs.bad_modes}))))
        slow = [r for r in results if r["fps"] > 0 and r["fps"] < 0.75 * float(
            _MODE_PARSE.match(r["mode"]).group(2))]
        if slow:
            L.append("  • Entregan menos fps de los que anuncian: {0}. Suele ser poca luz (exposición "
                     "larga): prueba con más luz o con un modo de menos fps (se probaron los de 15/10 "
                     "fps cuando hacía falta).".format(", ".join(r["mode"].split(" [")[0] for r in slow)))
        if not results:
            L.append("  • La cámara no informó de modos; se usará el modo de fábrica del driver.")
        log.info("prueba de modos:\n%s", "\n".join(L))
        self._set_status("Prueba de modos terminada: «Auto» usará {0}.".format(pick or "el modo de fábrica"))
        self._show_report("\n".join(L), "Prueba de modos")

    def _reset_cam(self):
        cs = self.cams.get(self.sel)
        if cs and not self.recording:
            cs.reset()

    def _reset_frame(self):
        cs = self.cams.get(self.sel)
        if cs and not self.recording:
            cs.reset_frame()

    def _reset_color(self):
        cs = self.cams.get(self.sel)
        if cs:
            cs.reset_color()

    def _copy_color_all(self):
        cs = self.cams.get(self.sel)
        if not cs:
            return
        vals = cs.color_values()
        for lab in self.order:
            o = self.cams.get(lab)
            if o is not None and o is not cs:
                for k, v in vals.items():
                    o.color[k].set(v)
        self._set_status("Color copiado a todas las cámaras.")

    def _cur_set(self, name, value):
        cs = self.cams.get(self.sel)
        if cs and not self.recording:
            getattr(cs, name).set(value)

    def _cur_color_set(self, key, value):
        cs = self.cams.get(self.sel)
        if cs:
            cs.color[key].set(value)

    # ------------------------------------------------------------ panel de cámara
    def _select(self, lab):
        self.sel = lab
        if self.editing and lab in self.order and lab != self.editing and not self.recording:
            self.editing = lab                       # el encuadre sigue a la cámara elegida
            self._schedule_restart()
        self._bind_detail()
        self._redraw_map()
        if lab in self.vdev and self.tree.exists(lab):
            self._set_tree_sel(lab)

    def _pick_from(self, var):
        lab = var.get()
        if lab in self.order:
            self._select(lab)

    @staticmethod
    def _set_state(w, enabled):
        try:
            w.state(["!disabled"] if enabled else ["disabled"])
        except Exception:
            pass

    def _bind_detail(self):
        cs = self.cams.get(self.sel) if self.sel in self.order else None
        self.det.config(text="3 · Ajustes de «{0}»".format(self.sel) if cs
                        else "3 · Ajustes de la cámara seleccionada")
        d = self._dummy
        self.w_h.config(variable=cs.h if cs else d["b"])
        self.w_v.config(variable=cs.v if cs else d["b"])
        self.w_rot.config(textvariable=cs.rot if cs else d["s"])
        self.w_delay.config(textvariable=cs.delay if cs else d["i"])
        self.w_mode.config(textvariable=cs.mode if cs else d["s"],
                           values=["Auto"] + (cs.modes if cs else []))
        self.w_tile.config(textvariable=cs.tile if cs else d["s"])
        self.sc_zoom.config(variable=cs.zoom if cs else d["d"])
        self.sc_cx.config(variable=cs.cx if cs else d["d"])
        self.sc_cy.config(variable=cs.cy if cs else d["d"])
        for key, sc in self.color_sc.items():
            sc.config(variable=cs.color[key] if cs else d["d"])
        free = bool(cs) and not self.recording
        for w in self.det_widgets + self.frame_widgets:
            self._set_state(w, free)
        for w in self.color_widgets:
            self._set_state(w, bool(cs))               # el color se puede tocar grabando
        is_net = bool(cs) and self.vdev.get(self.sel, {}).get("net")
        self._set_state(self.w_driver, free and not is_net)
        self._set_state(self.w_detect, free and not is_net)
        self._set_state(self.w_probe, free and not is_net)
        self._set_state(self.btn_net_del, bool(cs) and is_net and not self.recording)
        self.w_edit.config(text="✔   Terminar el encuadre" if self.editing
                           else "✂   Encuadrar sobre la imagen…")
        labs = list(self.order)
        for cb, var in self._pickers:
            cb.config(values=labs)
            var.set(self.sel if self.sel in labs else "")
        self._update_frame_labels()
        self._update_color_labels()

    def _update_frame_labels(self):
        cs = self.cams.get(self.sel) if self.sel in self.order else None
        if not cs:
            for lbl in (self.lbl_zoom, self.lbl_cx, self.lbl_cy):
                lbl.config(text="")
            return
        self.lbl_zoom.config(text="{0:.0f}%".format(_getf(cs.zoom, 100)))
        self.lbl_cx.config(text="{0:.0f}%".format(_getf(cs.cx, 50)))
        self.lbl_cy.config(text="{0:.0f}%".format(_getf(cs.cy, 50)))

    def _update_color_labels(self):
        cs = self.cams.get(self.sel) if self.sel in self.order else None
        for key, lbl in self.color_lbl.items():
            lbl.config(text="{0:+.0f}".format(_getf(cs.color[key])) if cs else "")

    # ------------------------------------------------------------ geometría del mosaico
    def _canvas(self):
        for lab, a, b in ASPECTS:
            if lab == self.v_canvas.get():
                return (a, b)
        return None                                  # «Adaptado al contenido»

    def _cwidth(self):
        return _even(max(320, min(3840, _geti(self.v_cwidth, 1280))))

    def _quiet_set(self, var, value):
        old, self._loading = self._loading, True
        try:
            var.set(value)
        finally:
            self._loading = old

    def _geometry(self):
        """Reparto actual de celdas: {W, H, tiles, rows, auto, labels, transpose} o None."""
        labs = [l for l in self.order if l in self.cams]
        n = len(labs)
        if not n:
            return None
        aspects = [self.cams[l].tile_aspect() for l in labs]
        canvas, width, trans = self._canvas(), self._cwidth(), bool(self.v_trans.get())
        rows = parse_structure(self.v_struct.get(), n)
        auto = rows is None
        if auto:
            if self.v_struct.get() != AUTO:          # elegida para otro nº de cámaras
                self._quiet_set(self.v_struct, AUTO)
            rows, trans = best_layout(aspects, width, canvas)
        g = layout_tiles(aspects, rows, width, trans, canvas)
        g.update(rows=rows, auto=auto, labels=labs, transpose=trans)
        return g

    # ------------------------------------------------------------ galería de disposiciones
    GAL_W, GAL_H, GAL_GAP, GAL_COLS = 46, 34, 6, 7

    def _thumb(self, c, x, y, g, sel, text=None):
        w, h = self.GAL_W, self.GAL_H
        c.create_rectangle(x, y, x + w, y + h, fill="#26406e" if sel else BG,
                           outline=ACC if sel else LINE, width=2 if sel else 1)
        top = 9 if text else 0               # la miniatura «Auto» deja sitio a su rótulo
        s = min((w - 8) / float(g["W"]), (h - 8 - top) / float(g["H"]))
        ox, oy = x + (w - g["W"] * s) / 2.0, y + top + (h - top - g["H"] * s) / 2.0
        for i, (tx, ty, tw, th) in enumerate(g["tiles"]):
            x0, y0 = ox + tx * s, oy + ty * s
            x1, y1 = x0 + tw * s, y0 + th * s
            c.create_rectangle(x0 + .5, y0 + .5, x1 - .5, y1 - .5, fill=PANEL2 if not sel else "#3a5f9e",
                               outline=MUTED)
            if x1 - x0 > 9 and y1 - y0 > 9:
                c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=str(i + 1), fill=FG,
                              font=("Segoe UI", 7, "bold"))
        if text:
            c.create_text(x + w / 2, y + 5, text=text, fill=WARN, font=("Segoe UI", 6, "bold"))

    def _redraw_gallery(self, g):
        c = self.gal
        c.delete("all")
        self._gal_hits = []
        if not g:
            c.config(height=44)
            return
        labs = g["labels"]
        aspects = [self.cams[l].tile_aspect() for l in labs]
        canvas, width = self._canvas(), self._cwidth()
        comp, tr = best_layout(aspects, width, canvas)
        cells = [(None, None, layout_tiles(aspects, comp, width, tr, canvas), "AUTO")]
        for (cp, t, gg) in layout_options(aspects, width, canvas):
            cells.append((cp, t, gg, None))
        step_x, step_y = self.GAL_W + self.GAL_GAP, self.GAL_H + self.GAL_GAP
        rows_n = (len(cells) + self.GAL_COLS - 1) // self.GAL_COLS
        c.config(height=rows_n * step_y + 4)
        cur = list(g["tiles"])
        marked = False
        for k, (cp, t, gg, txt) in enumerate(cells):
            x, y = 2 + (k % self.GAL_COLS) * step_x, 2 + (k // self.GAL_COLS) * step_y
            if cp is None:
                sel = g["auto"]
            else:
                sel = (not g["auto"]) and (not marked) and list(gg["tiles"]) == cur
                marked = marked or sel
            self._thumb(c, x, y, gg, sel, txt)
            self._gal_hits.append((x, y, x + self.GAL_W, y + self.GAL_H, cp, t, gg))

    def _gal_hit(self, e):
        for h in self._gal_hits:
            if h[0] <= e.x <= h[2] and h[1] <= e.y <= h[3]:
                return h
        return None

    def _gal_click(self, e):
        if self.recording:
            return
        h = self._gal_hit(e)
        if h is None:
            return
        cp, t = h[4], h[5]
        if cp is None:
            self.v_struct.set(AUTO)
        else:
            self.v_trans.set(bool(t))
            self.v_struct.set(structure_label(cp))
        self._redraw_map()

    # ------------------------------------------------------------ mapa del mosaico
    def _redraw_map(self):
        c = self.map
        c.delete("all")
        self._map_rects = []
        Wc, Hc = 372, 176
        g = self._geometry()
        self._redraw_gallery(g)
        if not g:
            c.create_text(Wc / 2, Hc / 2, text="Marca al menos una cámara", fill=MUTED,
                          font=("Segoe UI", 9))
            return
        s = min((Wc - 10) / float(g["W"]), (Hc - 10) / float(g["H"]))
        ox, oy = (Wc - g["W"] * s) / 2.0, (Hc - g["H"] * s) / 2.0
        c.create_rectangle(ox, oy, ox + g["W"] * s, oy + g["H"] * s, fill="#0b0c0f", outline=LINE)
        for idx, (x, y, w, h) in enumerate(g["tiles"]):
            x0, y0, x1, y1 = ox + x * s, oy + y * s, ox + (x + w) * s, oy + (y + h) * s
            self._map_rects.append((x0, y0, x1, y1))
            lab = g["labels"][idx]
            cs = self.cams.get(lab)
            sel = (lab == self.sel)
            c.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill="#2f4f86" if sel else PANEL2,
                               outline=ACC if sel else LINE, width=2 if sel else 1)
            c.create_text(x0 + 5, y0 + 3, anchor="nw", text=str(idx + 1), fill=WARN,
                          font=("Segoe UI", 11, "bold"))
            wpx = x1 - x0
            if wpx >= 60 and y1 - y0 >= 40:
                nch = max(4, int((wpx - 10) / 5.4))
                c.create_text((x0 + x1) / 2, (y0 + y1) / 2 + 2,
                              text=lab if len(lab) <= nch else lab[:nch - 1] + "…", fill=FG,
                              font=("Segoe UI", 8), width=wpx - 8)
            if wpx > 64:
                c.create_text(x0 + 5, y1 - 3, anchor="sw", text="{0}×{1}".format(w, h),
                              fill=MUTED, font=("Segoe UI", 7))
            if cs:
                flags = []
                if cs.h.get():
                    flags.append("H")
                if cs.v.get():
                    flags.append("V")
                if cs.rot.get() != "0°":
                    flags.append(cs.rot.get())
                if cs.tile_ratio() or _getf(cs.zoom, 100) > 100.5:
                    flags.append("✂")
                if any(abs(v) > 0.5 for v in cs.color_values().values()):
                    flags.append("◐")
                if flags:
                    c.create_text(x1 - 4, y1 - 3, anchor="se", text=" ".join(flags),
                                  fill=MUTED, font=("Segoe UI", 8))

    def _map_hit(self, x, y):
        for i, (x0, y0, x1, y1) in enumerate(self._map_rects):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i
        return None

    def _map_press(self, e):
        if self.recording or self.busy:
            return
        i = self._map_hit(e.x, e.y)
        if i is not None and i < len(self.order):
            self._drag_src = i
            self._select(self.order[i])
        else:
            self._drag_src = None

    def _map_release(self, e):
        if self.recording or self.busy or self._drag_src is None:
            return
        j = self._map_hit(e.x, e.y)
        i, self._drag_src = self._drag_src, None
        if j is not None and j < len(self.order) and j != i:
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self._redraw_map()
            self._on_change()

    def _map_double(self, e):
        i = self._map_hit(e.x, e.y)
        if i is not None and i < len(self.order):
            self._drag_src = None
            self._enter_edit(self.order[i])

    def _move(self, delta):
        if self.recording or self.sel not in self.order:
            return
        i = self.order.index(self.sel)
        j = i + delta
        if 0 <= j < len(self.order):
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self._redraw_map()
            self._on_change()

    # ------------------------------------------------------------ encuadre sobre la imagen
    def _schedule_restart(self, delay=60):
        if self._restart_job:
            try:
                self.root.after_cancel(self._restart_job)
            except Exception:
                pass
        self._restart_job = self.root.after(delay, self._auto_preview)

    def _edit_toggle(self):
        if self.editing:
            self._exit_edit()
        elif self.sel in self.order:
            self._enter_edit(self.sel)

    def _enter_edit(self, lab):
        if self.recording or lab not in self.order or lab not in self.cams:
            return
        self.editing = lab
        self.paused = False
        self._select(lab)
        try:
            self.nb.select(self.tab_frame)
        except Exception:
            pass
        if self._ov_btn is None:
            self._ov_btn = ttk.Button(self.view, text="✔  Listo  (Esc)", style="Accent.TButton",
                                      command=self._exit_edit)
            self._ov_btn.place(relx=1.0, x=-12, y=12, anchor="ne")
        self._bind_detail()
        self._refresh_buttons()
        self._set_status(self._edit_hint())
        self._last_fc = -1
        self._schedule_restart()

    def _exit_edit(self):
        if not self.editing:
            return
        self.editing = None
        if self._ov_btn is not None:
            try:
                self._ov_btn.destroy()
            except Exception:
                pass
            self._ov_btn = None
        self.view.delete("ov")
        self._edit_drag = None
        self._bind_detail()
        self._refresh_buttons()
        self._redraw_map()
        self._update_info()
        self._set_status("Encuadre aplicado.")
        self._last_fc = -1
        if not self.recording:
            self._schedule_restart()

    def _edit_hint(self):
        return ("Encuadre de «{0}»: arrastra el marco para moverlo, rueda del ratón = zoom, "
                "Esc para terminar.".format(self.editing))

    def _calc_img_rect(self):
        m = self.eng.meta
        w, h = m.get("pw"), m.get("ph")
        vw, vh = self.view.winfo_width(), self.view.winfo_height()
        if not w or not h or vw < 4 or vh < 4:
            return None
        nw, nh = fit_size(w, h, vw, vh)
        return ((vw - nw) / 2.0, (vh - nh) / 2.0, nw, nh)

    def _edit_window(self):
        """(cs, S, T, x0, y0, fw, fh) de la cámara en edición."""
        cs = self.cams.get(self.editing)
        if not cs:
            return None
        S, T = cs.source_aspect(), cs.tile_aspect()
        x0, y0, fw, fh = window_rect(S, T, _getf(cs.zoom, 100), _getf(cs.cx, 50), _getf(cs.cy, 50))
        return cs, S, T, x0, y0, fw, fh

    def _redraw_overlay(self):
        v = self.view
        v.delete("ov")
        r = self._img_rect = self._calc_img_rect() if (self.previewing or self.recording) else None
        if not r:
            return
        ix, iy, nw, nh = r
        if self.editing:
            ew = self._edit_window()
            if not ew:
                return
            cs, S, T, x0, y0, fw, fh = ew
            X0, Y0, X1, Y1 = ix + x0 * nw, iy + y0 * nh, ix + (x0 + fw) * nw, iy + (y0 + fh) * nh
            v.create_image(ix, iy, anchor="nw", tags="ov",
                           image=self._dim_photo(nw, nh, x0 * nw, y0 * nh, (x0 + fw) * nw, (y0 + fh) * nh))
            for k in (1, 2):                         # regla de los tercios
                v.create_line(X0 + (X1 - X0) * k / 3.0, Y0, X0 + (X1 - X0) * k / 3.0, Y1,
                              fill="#ffffff", stipple="gray50", tags="ov")
                v.create_line(X0, Y0 + (Y1 - Y0) * k / 3.0, X1, Y0 + (Y1 - Y0) * k / 3.0,
                              fill="#ffffff", stipple="gray50", tags="ov")
            v.create_rectangle(X0, Y0, X1, Y1, outline=WARN, width=2, tags="ov")
            tid = v.create_text(ix + 14, iy + 12, anchor="nw", fill=WARN, tags="ov",
                                font=("Consolas", 10, "bold"),
                                text="ENCUADRE · {0} · zoom {1:.0f}% · celda {2:.2f}:1".format(
                                    self.editing[:28], _getf(cs.zoom, 100), T))
            bb = v.bbox(tid)
            if bb:
                rid = v.create_rectangle(bb[0] - 6, bb[1] - 3, bb[2] + 6, bb[3] + 3, fill="#000000",
                                         outline="#3a3f48", tags="ov")
                v.tag_lower(rid, tid)
            return
        # modo normal: marca de la cámara elegida
        m = self.eng.meta
        if self.sel in self._run_labels and m.get("tiles") and m.get("W"):
            i = self._run_labels.index(self.sel)
            if i < len(m["tiles"]):
                sc = nw / float(m["W"])
                tx, ty, tw, th = m["tiles"][i]
                v.create_rectangle(ix + tx * sc + 1, iy + ty * sc + 1, ix + (tx + tw) * sc - 1,
                                   iy + (ty + th) * sc - 1, outline=ACC, width=2, tags="ov")

    def _dim_photo(self, nw, nh, X0, Y0, X1, Y1):
        """Velo oscuro semitransparente sobre la imagen, con un hueco donde queda el marco."""
        key = (nw, nh, int(X0), int(Y0), int(X1), int(Y1))
        if getattr(self, "_dim_key", None) != key:
            a = np.zeros((nh, nw, 4), np.uint8)
            a[..., 3] = 150
            a[max(0, int(Y0)):max(0, int(Y1)), max(0, int(X0)):max(0, int(X1)), 3] = 0
            self._dim_img = ImageTk.PhotoImage(Image.fromarray(a, "RGBA"))
            self._dim_key = key
        return self._dim_img

    def _tile_at(self, x, y):
        r = self._img_rect
        m = self.eng.meta
        if not r or not m.get("tiles") or not m.get("W") or self.editing:
            return None
        ix, iy, nw, nh = r
        sc = nw / float(m["W"])
        for i, (tx, ty, tw, th) in enumerate(m["tiles"]):
            if ix + tx * sc <= x <= ix + (tx + tw) * sc and iy + ty * sc <= y <= iy + (ty + th) * sc:
                return self._run_labels[i] if i < len(self._run_labels) else None
        return None

    def _view_press(self, e):
        self._edit_drag = None
        r = self._img_rect
        if self.editing:
            cs = self.cams.get(self.editing)
            if cs and r and r[0] <= e.x <= r[0] + r[2] and r[1] <= e.y <= r[1] + r[3]:
                self._edit_drag = (e.x, e.y, _getf(cs.cx, 50), _getf(cs.cy, 50))
            return
        lab = self._tile_at(e.x, e.y)
        if lab in self.order:
            self._select(lab)

    def _view_motion(self, e):
        if not (self.editing and self._edit_drag and self._img_rect):
            return
        ew = self._edit_window()
        if not ew:
            return
        cs, S, T, _x0, _y0, fw, fh = ew
        sx, sy, cx0, cy0 = self._edit_drag
        nw, nh = self._img_rect[2], self._img_rect[3]
        ncx = cx0 + (e.x - sx) / float(nw) * 100.0
        ncy = cy0 + (e.y - sy) / float(nh) * 100.0
        x0, y0, fw, fh = window_rect(S, T, _getf(cs.zoom, 100), ncx, ncy)
        cs.cx.set(round((x0 + fw / 2.0) * 100.0, 2))     # ya recortado a la imagen
        cs.cy.set(round((y0 + fh / 2.0) * 100.0, 2))
        self._redraw_overlay()

    def _view_release(self, _e):
        self._edit_drag = None

    def _view_double(self, e):
        if self.editing or self.recording or self.mode != "normal":
            return
        lab = self._tile_at(e.x, e.y)
        if lab in self.order:
            self._enter_edit(lab)

    def _view_wheel(self, e):
        if not (self.editing and self._img_rect):
            return
        ew = self._edit_window()
        if not ew:
            return
        cs, S, T, x0, y0, fw, fh = ew
        d = getattr(e, "delta", 0) or (120 if getattr(e, "num", 0) == 4 else -120)
        z = _getf(cs.zoom, 100)
        nz = max(100.0, min(600.0, z * (1.12 ** (d / 120.0))))
        if abs(nz - z) < 1e-6:
            return
        ix, iy, nw, nh = self._img_rect
        pu = min(1.0, max(0.0, (e.x - ix) / float(nw)))            # punto bajo el cursor
        pv = min(1.0, max(0.0, (e.y - iy) / float(nh)))
        rx, ry = (pu - x0) / fw, (pv - y0) / fh
        _a, _b, fw2, fh2 = window_rect(S, T, nz, 50, 50)
        ncx = (pu - rx * fw2 + fw2 / 2.0) * 100.0
        ncy = (pv - ry * fh2 + fh2 / 2.0) * 100.0
        x0n, y0n, fw2, fh2 = window_rect(S, T, nz, ncx, ncy)
        cs.zoom.set(round(nz, 2))
        cs.cx.set(round((x0n + fw2 / 2.0) * 100.0, 2))
        cs.cy.set(round((y0n + fh2 / 2.0) * 100.0, 2))
        self._redraw_overlay()

    def _on_frame(self, lab):
        """Cambió el formato de celda, el zoom o la posición de una cámara."""
        if self._loading:
            return
        if lab == self.sel:
            self._update_frame_labels()
        if self.editing:                       # la vista de edición no depende de estos valores
            self._redraw_overlay()
            self._redraw_map()
            self._update_info()
        else:
            self._on_change()

    # ------------------------------------------------------------ color en vivo
    def _on_color(self, lab):
        if self._loading:
            return
        if lab == self.sel:
            self._update_color_labels()
        job = self._color_jobs.pop(lab, None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._color_jobs[lab] = self.root.after(120, lambda: self._push_color(lab))
        self._redraw_map()

    def _push_color(self, lab, tries=0):
        """Envía a ffmpeg en marcha solo lo que cambió (sin reiniciar la vista previa)."""
        self._color_jobs.pop(lab, None)
        cs = self.cams.get(lab)
        if cs is None or lab not in self._run_labels:
            return
        if not (self.previewing or self.recording) or not self.eng.is_running():
            if self.busy and tries < 10:                     # ffmpeg aún arrancando
                self._color_jobs[lab] = self.root.after(
                    400, lambda: self._push_color(lab, tries + 1))
            return
        i = self._run_labels.index(lab)
        new = color_params(cs.color_values())
        old = self._run_color.get(lab) or color_params({})
        if new["temp_on"] != old["temp_on"]:
            if self.recording:                               # el filtro no está en el grafo
                self._set_status("La temperatura de color se activará en la próxima grabación.")
                new["temp_on"] = old["temp_on"]
                new["temperature"] = old["temperature"]
            else:
                self._on_change()                            # se reinicia con el filtro incluido
                return
        cmds = []
        for key in ("brightness", "contrast", "saturation", "gamma"):
            if abs(new[key] - old[key]) > 1e-6:
                cmds.append(("eq@c{0}".format(i), key, "{0:.4f}".format(new[key])))
        if new["temp_on"] and new["temperature"] != old["temperature"]:
            cmds.append(("colortemperature@t{0}".format(i), "temperature", str(new["temperature"])))
        if cmds and self.eng.send_filter_commands(cmds):
            self._run_color[lab] = new

    # ------------------------------------------------------------ barras negras
    def _autocrop(self):
        cs = self.cams.get(self.sel) if self.sel in self.order else None
        if cs is None or self.recording or self.busy or self.editing:
            return
        lab, spec = self.sel, self.vdev[self.sel]["spec"]
        was_paused = self.paused
        self.paused = True
        self.previewing = False
        self._refresh_buttons()
        self._set_status("Buscando barras negras en «{0}»… (unos segundos)".format(lab))
        ffmpeg = self.eng.ffmpeg
        fps = self._fps()
        mode = cs.mode.get()                 # las variables de Tk solo se leen en el hilo principal

        def work():
            self.eng.stop()
            cam = {"spec": spec, "mode": mode}
            self._auto_modes({"labels": [lab], "cams": [cam], "fps": fps})
            time.sleep(RELEASE_GAP)
            return detect_bars(ffmpeg, spec, cam["mode"])

        def done(res):
            self.paused = was_paused
            if isinstance(res, Exception) or res is None:
                self._set_status("No se pudo medir la imagen de «{0}».".format(lab))
            else:
                fr = bars_to_frame(res, bool(cs.h.get()), bool(cs.v.get()), cs.rot_deg())
                if fr is None:
                    self._set_status("«{0}» no tiene barras negras: ya llena toda la celda.".format(lab))
                else:
                    ratio, zoom, cx, cy = fr
                    cs.tile.set(ratio)
                    cs.zoom.set(round(zoom, 1))
                    cs.cx.set(round(cx, 1))
                    cs.cy.set(round(cy, 1))
                    x, y, w, h, iw, ih = res
                    self._set_status("Barras quitadas en «{0}»: el contenido real mide {1}×{2} ({3:.3f}:1) "
                                     "dentro de {4}×{5}.".format(lab, w, h, w / float(h), iw, ih))
            if not was_paused:
                self._schedule_restart(300)

        self._bg(work, done)

    # ------------------------------------------------------------ diagnóstico
    def _diagnose(self):
        if self.recording or self.busy:
            return
        labs = [l for l in self.order if l in self.vdev and l in self.cams]
        def _diag_mode(cs):          # se mide con el modo que la app usaría de verdad
            m = cs.mode.get()
            return (getattr(cs, "auto_mode", None) or "") if m in ("", "Auto") else m
        cams = [(l, self.vdev[l]["spec"], _diag_mode(self.cams[l])) for l in labs]
        audios, seen = [], set()
        buf = max(0, _geti(self.v_abuf, 50))
        for side in "LR":
            dev = self.adev.get(self.arow[side].to_cfg()["dev"])
            if dev and dev["spec"] not in seen:
                seen.add(dev["spec"])
                audios.append((dev["label"], dev["spec"], buf))
        if not cams and not audios:
            messagebox.showinfo("Diagnóstico", "Marca alguna cámara o elige un micrófono primero.")
            return
        if not messagebox.askokcancel(
                "Diagnóstico de rendimiento",
                "Se pausa la vista previa unos 30 s y se mide cada dispositivo (fps reales de cada "
                "cámara, cortes de audio) primero por separado y luego todos a la vez.\n\n"
                "Así se ve si el problema es de una cámara, del micro o de compartir el USB/CPU."):
            return
        was_paused = self.paused
        self.paused = True
        self.previewing = False
        self._refresh_buttons()
        self._set_status("Midiendo dispositivos… (unos 30 s)")
        ffmpeg = self.eng.ffmpeg

        def work():
            self.eng.stop()
            time.sleep(RELEASE_GAP)
            return run_diagnostics(ffmpeg, cams, audios)

        def done(res):
            self.paused = was_paused
            if isinstance(res, Exception):
                txt = "El diagnóstico falló: {0}".format(res)
            else:
                txt = res
            log.info("diagnóstico:\n%s", txt)
            self._set_status("Diagnóstico terminado.")
            self._show_report(txt)
            if not was_paused:
                self._schedule_restart(300)

        self._bg(work, done)

    def _show_report(self, txt, title="Diagnóstico"):
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=BG)
        top.geometry("760x460")
        top.minsize(420, 260)
        top.transient(self.root)
        # los botones se empaquetan los PRIMEROS, pegados abajo (side="bottom"): así tienen su
        # espacio siempre reservado y no se quedan reducidos a una rendija cuando el informe es
        # largo (con un informe de muchas líneas, antes "Copiar informe" quedaba con solo 8px de
        # alto -- apenas un hilo, prácticamente invisible e imposible de pulsar).
        bar = ttk.Frame(top)
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self._set_status("Informe copiado al portapapeles.")
        ttk.Button(bar, text="Copiar informe", command=copy).pack(side="left")
        ttk.Button(bar, text="Cerrar", command=top.destroy).pack(side="right")
        box_frame = ttk.Frame(top)
        box_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        box = tk.Text(box_frame, wrap="word", bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                      font=("Consolas", 10), padx=12, pady=10)
        box_sb = ttk.Scrollbar(box_frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=box_sb.set)
        box_sb.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        box.insert("1.0", txt)
        box.config(state="disabled")

    # ------------------------------------------------------------ driver de la cámara
    def _driver_dialog(self):
        cs = self.cams.get(self.sel) if self.sel in self.order else None
        if cs is None or self.recording or self.busy:
            return
        lab = self.sel
        spec = self.vdev[lab]["spec"]
        if not messagebox.askokcancel(
                "Propiedades del driver",
                "La vista previa se pausa un momento para que Windows muestre el cuadro de "
                "propiedades de «{0}».\n\nAjusta lo que necesites (exposición, balance de blancos, "
                "enfoque…) y ciérralo con Aceptar; la vista previa se reanuda sola.\n\n"
                "Si el cuadro no aparece, mira detrás de esta ventana. Que el ajuste se conserve "
                "depende del fabricante de la cámara.".format(lab)):
            return
        was_paused = self.paused
        self.paused = True
        self.previewing = False
        self._refresh_buttons()
        self._set_status("Abriendo las propiedades del driver…")
        ffmpeg = self.eng.ffmpeg

        def work():
            self.eng.stop()
            time.sleep(0.6)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "dshow",
                   "-show_video_device_dialog", "true", "-i", "video=" + spec,
                   "-frames:v", "5", "-f", "null", "-"]
            log.info("driver dialog: %s", subprocess.list2cmdline(cmd))
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", creationflags=CREATE_NO_WINDOW, timeout=900,
                               stdin=subprocess.DEVNULL)
            log.info("driver dialog: rc=%s %s", r.returncode, (r.stderr or "")[-300:])
            return r.returncode

        def done(res):
            self.paused = was_paused
            if isinstance(res, Exception):
                self._set_status("No se pudo abrir el cuadro del driver: {0}".format(res))
            else:
                self._set_status("Propiedades del driver cerradas.")
            if not was_paused:
                self._schedule_restart(200)

        self._bg(work, done)

    # ------------------------------------------------------------ formato real de cada cámara
    def _update_mode_label(self):
        """Dice qué resolución y fps está recibiendo de verdad la cámara seleccionada."""
        cs = self.cams.get(self.sel) if self.sel in self.order else None
        if cs is None:
            txt = ""
        elif _MODE_PARSE.match(cs.mode.get() or ""):
            txt = "Modo fijo elegido por ti: {0}".format(cs.mode.get())
        else:
            txt = "Auto: se elige el modo más rápido que ofrezca la cámara"
            idx = self._run_labels.index(self.sel) if self.sel in self._run_labels else -1
            sz, fps = self.eng.in_sizes.get(idx), self.eng.in_fps.get(idx)
            if (self.previewing or self.recording) and sz:
                txt = "Auto → recibiendo {0}×{1}{2}".format(
                    sz[0], sz[1], " a {0:g} fps".format(fps) if fps else "")
            elif getattr(cs, "auto_mode", None):
                txt = "Auto → {0}".format(cs.auto_mode)
        if txt != getattr(self, "_mode_now_txt", None):
            self._mode_now_txt = txt
            self.lbl_mode_now.config(text=txt)

    def _check_sources(self):
        """ffmpeg informa del tamaño real de cada cámara; si no era el supuesto, se recoloca.
        Si solo cambian unos pocos píxeles sin cambiar la forma (ver _same_shape -- típico de una
        cámara de red: el móvil, otro PC encadenado, una app tipo IP Webcam... cada conexión
        puede anunciar un tamaño ligerísimamente distinto, por redondeo del propio códec), se
        guarda el dato pero NO se reinicia el mosaico por eso -- antes sí, y si ese jitter se
        repetía en cada reconexión, el mosaico se quedaba reiniciándose solo sin parar nunca
        ("Ajustando el mosaico al formato real de las cámaras…" todo el rato, sin asentarse)."""
        if not (self.previewing or self.recording) or self.eng.frame_count == 0:
            return
        sizes = self.eng.in_sizes
        if not sizes:
            return
        for idx, lab in enumerate(self._run_labels):
            f = self.eng.in_fps.get(idx)
            if f and f < 15 and lab not in self._slow_warned:
                self._slow_warned.add(lab)
                log.warning("cámara lenta: «%s» anuncia %.1f fps con %s", lab, f, sizes.get(idx))
                self._set_status("⚠ «{0}» solo da {1:g} fps con este modo y frena el mosaico. "
                                 "Cámaras → Captura → «Detectar» y elige uno de 30 fps.".format(lab, f))
        changed = False
        for idx, lab in enumerate(self._run_labels):
            sz, cs = sizes.get(idx), self.cams.get(lab)
            if sz and cs is not None and cs.src != sz:
                known_by_mode = mode_size(cs.mode.get()) is not None
                jitter = cs.src is not None and _same_shape(cs.src, sz)
                cs.src = sz
                if not known_by_mode and not jitter:
                    changed = True
        if changed:
            log.info("formatos de cámara detectados: %s", sizes)
            self.save_config()
            if not self.recording and not self.editing:
                self._set_status("Ajustando el mosaico al formato real de las cámaras…")
                self._on_change()
            elif self.editing:
                self._schedule_restart()

    # ------------------------------------------------------------ audio presets
    def _first_adev(self, idx=0):
        return self.adevs[idx]["label"] if len(self.adevs) > idx else NO_AUDIO

    def _preset_stereo(self):
        d = self.arow["L"].dev.get()
        d = d if d != NO_AUDIO else self._first_adev()
        for side, ch in (("L", "left"), ("R", "right")):
            self.arow[side].dev.set(d)
            self.arow[side].chan.set(_pair_label(CHANS, ch))

    def _preset_mono(self):
        d = self.arow["L"].dev.get()
        d = d if d != NO_AUDIO else self._first_adev()
        for side in "LR":
            self.arow[side].dev.set(d)
            self.arow[side].chan.set(_pair_label(CHANS, "mix"))

    def _preset_split(self):
        a = self.arow["L"].dev.get()
        a = a if a != NO_AUDIO else self._first_adev(0)
        b = self.arow["R"].dev.get()
        if b == NO_AUDIO or b == a:
            b = self._first_adev(1)
        self.arow["L"].dev.set(a)
        self.arow["R"].dev.set(b)
        for side in "LR":
            self.arow[side].chan.set(_pair_label(CHANS, "mix"))
        if b == NO_AUDIO or a == b:
            self._set_status("Solo hay un dispositivo de audio: elige otro en la salida derecha.")

    # ------------------------------------------------------------ plan / config
    def _fps(self):
        return max(5, min(60, _geti(self.v_fps, 30)))

    def _audio_plan(self):
        rows_a = {}
        for side in "LR":
            c = self.arow[side].to_cfg()
            dev = self.adev.get(c["dev"])
            rows_a[side] = ({"spec": dev["spec"], "chan": c["chan"], "gain": c["gain"],
                             "delay": c["delay"]} if dev else None)
        rows_a["offset"] = _geti(self.v_avoff)
        rows_a["buf"] = max(0, _geti(self.v_abuf, 50))
        return rows_a

    def _plan(self):
        if self.editing and self.editing in self.order and self.editing in self.vdev:
            return self._edit_plan()
        g = self._geometry()
        if not g:
            return None
        labs = [l for l in g["labels"] if l in self.vdev]
        if len(labs) != len(g["labels"]):
            return None
        cams = [self.cams[l].to_plan(self.vdev[l]["spec"]) for l in labs]
        qual = dict(QUALITIES).get(self.v_quality.get(), 23)
        plan = {"cams": cams, "geom": g, "labels": labs,
                "run_color": dict((l, color_params(self.cams[l].color_values())) for l in labs),
                "fps": self._fps(), "sync": _pair_value(SYNCS, self.v_sync.get()),
                "crf": qual, "preset": self.v_preset.get(), "audio": self._audio_plan()}
        if self.vcam_on and self.vcam.active:
            plan["vcam"] = {"w": self.vcam.w, "h": self.vcam.h, "fps": min(30, self._fps()),
                            "pix": self.vcam.pix}
        if self.ipout_on and self.ipsrv.active:
            # la forma real del mosaico, dentro del máximo elegido -- sin barras negras añadidas
            iw, ih = ip_fit_size(g["W"], g["H"], getattr(self.ipsrv, "box", None) or
                                 (self.ipsrv.w, self.ipsrv.h))
            plan["ipout"] = {"w": iw, "h": ih, "fps": self.ipsrv.fps, "pix": self.ipsrv.pix}
        return plan

    def _edit_plan(self):
        """Vista de edición: solo la cámara elegida, entera y sin recorte, sin audio."""
        lab = self.editing
        cs = self.cams[lab]
        tw = 960
        th = _even(tw / cs.source_aspect())
        cam = cs.to_plan(self.vdev[lab]["spec"])
        cam.update(tile="auto", zoom=100.0, cx=50.0, cy=50.0)
        qual = dict(QUALITIES).get(self.v_quality.get(), 23)
        return {"cams": [cam], "geom": {"W": tw, "H": th, "tiles": [(0, 0, tw, th)]},
                "labels": [lab], "run_color": {lab: color_params(cs.color_values())},
                "fps": self._fps(), "sync": _pair_value(SYNCS, self.v_sync.get()),
                "crf": qual, "preset": self.v_preset.get(), "meter": False,
                "audio": {"L": None, "R": None, "offset": 0}}

    def save_config(self):
        cfg = {"version": 3, "order": list(self.order),
               "cams": dict((k, v.to_cfg()) for k, v in self.cams.items()),
               "structure": self.v_struct.get(), "transpose": bool(self.v_trans.get()),
               "canvas": self.v_canvas.get(), "cwidth": self._cwidth(), "fps": self._fps(),
               "quality": self.v_quality.get(), "preset": self.v_preset.get(),
               "container": self.v_container.get(),
               "sync": _pair_value(SYNCS, self.v_sync.get()),
               "dir": self.v_dir.get(), "name": self.v_name.get(),
               "av_offset": _geti(self.v_avoff), "abuf": max(0, _geti(self.v_abuf, 50)),
               "audio": {"L": self.arow["L"].to_cfg(), "R": self.arow["R"].to_cfg()}}
        self._remember_geo()
        cfg["ui"] = {"v": 2, "mode": self.mode, "pinned": bool(self._pinned),
                     "geo": dict(self._geo), "tray_asked": bool(self._tray_asked)}
        cfg["startup"] = round(float(self.eng.startup_time), 2)     # para grabar sin vista previa
        cfg["vcam"] = {"on": bool(self.vcam_on), "size": list(vcam_size_value(self.v_vsize.get()))}
        cfg["ipout"] = {"on": bool(self.ipout_on), "size": list(ip_size_value(self.v_ipsize.get())),
                        "port": self._ip_port()}
        cfg["phonecam"] = {"port": self._phonecam_port()}   # siempre activa: no hace falta guardar "on"
        # las cámaras de móvil se añaden solas cuando el móvil se conecta de verdad (su URL
        # depende de la sesión en marcha, no sobrevive a cerrar cam-ui) -- no se guardan, para
        # no dejar entradas "Sin señal" huérfanas la próxima vez que se abra el programa.
        cfg["net_cams"] = {lab: d for lab, d in self.net_cams.items() if not d.get("phone")}
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.v_dir.get() or DEF_DIR)
        if d:
            self.v_dir.set(d)

    def _open_dir(self):
        folder = self.v_dir.get() or DEF_DIR
        os.makedirs(folder, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    # ------------------------------------------------------------ cambios en vivo
    def _on_change(self, *_a):
        if self._loading:
            return
        self._update_info()
        self._redraw_map()
        if not self.recording and not self.paused:
            if self._restart_job:
                self.root.after_cancel(self._restart_job)
            self._restart_job = self.root.after(900, self._auto_preview)
            if self.previewing:
                self._set_status("Aplicando cambios…")

    def _update_info(self):
        g = self._geometry()
        if not g:
            self.lbl_size.config(text="Sin cámaras marcadas.")
            return
        txt = "Vídeo final {0}×{1} · {2} cámara(s) · reparto {3}{4}{5}".format(
            g["W"], g["H"], len(g["labels"]), structure_label(g["rows"]),
            " (auto)" if g["auto"] else "", " por columnas" if g["transpose"] else "")
        if g["W"] * g["H"] > 8300000:
            txt += "\nAviso: más de 4K; la grabación puede ir lenta. Baja el ancho."
        self.lbl_size.config(text=txt)

    # ------------------------------------------------------------ modo de captura automático
    def _engine_start(self, plan, **kw):
        """(hilo de fondo) Para ffmpeg, resuelve el modo de las cámaras en «Auto» y arranca.
        Si una cámara rechaza su modo («Could not set video options») prueba con otro y,
        por último, con el de fábrica del driver, en vez de tratarlo como cámara ocupada."""
        self.eng.stop()
        self._auto_modes(plan)
        self._preflight_net_cams(plan)
        res = self.eng.start(plan, **kw)
        ok, err = res if isinstance(res, tuple) else (False, str(res))
        if not ok and plan.get("vcam") and not self.eng._abort and \
                re.search(r"127\.0\.0\.1|tcp://|connection (?:refused|reset|timed out)", err, re.I):
            log.warning("la salida de la cámara virtual no conecta; se arranca sin ella:\n%s", err[-300:])
            plan.pop("vcam", None)
            self._tray_q.append(("vcam_off", "ffmpeg no pudo conectar con la cámara virtual (¿cortafuegos?)."))
            res = self.eng.start(plan, **kw)
        ok, err = res if isinstance(res, tuple) else (False, str(res))
        if not ok and plan.get("ipout") and not self.eng._abort and \
                re.search(r"127\.0\.0\.1|tcp://|connection (?:refused|reset|timed out)", err, re.I):
            log.warning("la salida en red no conecta; se arranca sin ella:\n%s", err[-300:])
            plan.pop("ipout", None)
            self._tray_q.append(("ip_off", "ffmpeg no pudo conectar con la salida en red (¿cortafuegos?)."))
            res = self.eng.start(plan, **kw)
        for _ in range(5):
            ok, err = res if isinstance(res, tuple) else (False, str(res))
            if ok or self.eng._abort:
                break
            if is_mode_error(err):
                if not self._drop_bad_mode(plan, err):
                    break
            elif not self._drop_bad_cam(plan, err):
                break
            res = self.eng.start(plan, **kw)
        return res

    def _preflight_net_cams(self, plan):
        """(hilo de fondo, llamado desde _engine_start antes de arrancar ffmpeg de verdad)
        Prueba de red: cada cámara de red del plan que no esté ya marcada «sin señal» se prueba
        por separado -- todas a la vez, con un margen corto (ver NET_PREFLIGHT_SECS /
        NET_PREFLIGHT_TIMEOUT_US) -- antes de integrarla tal cual en el mosaico real. La que no
        responda a tiempo (o conecte pero no entregue ni un fotograma de vídeo válido) se marca
        "_offline" desde el principio, exactamente igual que si ffmpeg ya hubiera fallado al
        abrirla (ver build_command/_offline_lavfi y _drop_bad_cam): así nunca comparte el mismo
        proceso de ffmpeg que una cámara local de verdad mientras se decide si responde o no, y
        no puede retrasar el arranque lo bastante como para desbordarle el búfer en tiempo real a
        otra (caso real: una EOS Webcam Utility real se quedaba con el búfer lleno y tiraba abajo
        todo el proceso mientras ffmpeg seguía esperando a una cámara de red que ni conectaba).
        Sin ninguna cámara de red en el plan (el caso normal) no hace nada -- ningún retraso para
        quien no la usa."""
        if self.eng._abort:
            return
        cams = plan.get("cams") or []
        labels = plan.get("labels") or []
        jobs, idx_by_key = [], {}
        for i, c in enumerate(cams):
            if c.get("_offline") or not _is_net_spec(c.get("spec")):
                continue
            key = str(i)
            jobs.append((key, "video", c["spec"], c.get("mode")))
            idx_by_key[key] = i
        if not jobs:
            return
        results = _preflight_probe(jobs, self.eng.ffmpeg)
        for key, i in idx_by_key.items():
            if self.eng._abort:
                return
            r = results.get(key) or {}
            if r.get("frames"):
                continue
            cams[i]["_offline"] = True
            lab = labels[i] if i < len(labels) else (cams[i].get("spec") or "?")
            log.warning("prueba de red: «%s» no respondió a tiempo (o sin vídeo válido); se "
                       "arranca como «sin señal»:\n%s", lab, (r.get("err") or "")[-300:])
            self._tray_q.append(("cam_offline", lab))

    def _drop_bad_cam(self, plan, err):
        """Si el error identifica una cámara (DirectShow local o de red) que ffmpeg no pudo
        abrir, la sustituye por un marcador «sin señal» (build_command / _offline_lavfi) y deja
        que el resto del mosaico siga, en vez de que todo el proceso (vista previa + grabación,
        que comparten uno solo) se caiga por esa única cámara. False si el error no identifica
        ninguna cámara del plan, o si esa cámara YA se había marcado antes en este mismo
        intento (para no reintentar sin fin si ni el propio marcador consigue arrancar)."""
        i = failing_cam_index(plan["cams"], err)
        if i is None or plan["cams"][i].get("_offline"):
            return False
        plan["cams"][i]["_offline"] = True
        labs = plan.get("labels") or []
        lab = labs[i] if i < len(labs) else (plan["cams"][i].get("spec") or "?")
        log.warning("«%s» no se pudo abrir; se sustituye por «sin señal» y se sigue:\n%s",
                   lab, err[-300:])
        self._tray_q.append(("cam_offline", lab))
        return True

    def _drop_bad_mode(self, plan, err):
        """Marca como inválido el modo de la cámara que falló y le pone otro. False si no procede."""
        kind, spec = failing_spec(err)
        if kind != "video":
            return False
        for i, cam in enumerate(plan["cams"]):
            if cam.get("spec") == spec:
                break
        else:
            return False
        bad = cam.get("mode") or ""
        if not bad:
            return False                     # ya sin opciones y aun así falla: no es culpa del modo
        lab = plan["labels"][i]
        cs = self.cams.get(lab)
        drops = plan.setdefault("_drops", {})
        drops[lab] = drops.get(lab, 0) + 1
        if cs is not None:
            cs.bad_modes.add(bad)
            alts = [m for m in cs.modes if m not in cs.bad_modes]
            # dos alternativas como mucho; después, el modo de fábrica del driver (sin opciones)
            cam["mode"] = (pick_auto_mode(alts, plan.get("fps", 30), ok=cs.ok_modes) or "") \
                if drops[lab] <= 2 else ""
        else:
            cam["mode"] = ""
        log.warning("«%s» rechaza el modo %s -> %s", lab, bad, cam["mode"] or "(modo de fábrica)")
        self._tray_q.append(("badmode", (lab, bad, cam["mode"])))
        return True

    def _auto_modes(self, plan):
        fps = plan.get("fps", 30)
        for lab, cam in zip(plan.get("labels", []), plan["cams"]):
            cs = self.cams.get(lab)
            if cs is None or not cam.get("spec"):
                continue
            explicit = bool(_MODE_PARSE.match(cam.get("mode") or ""))
            if not cs.modes and not cs.modes_tried:
                try:
                    cs.modes = list_camera_modes(cam["spec"])
                    cs.modes_tried = True
                    log.info("modos de «%s»: %s", lab, cs.modes)
                except Exception as e:
                    log.warning("no se pudieron listar los modos de «%s»: %r", lab, e)
            if explicit:
                if not cs.modes or cam["mode"] in cs.modes:
                    continue                               # el usuario eligió un modo válido
                log.warning("«%s»: el modo %s no está entre los que anuncia la cámara", lab, cam["mode"])
                self._tray_q.append(("badmode", (lab, cam["mode"], None)))
                cam["mode"] = None
            pick = pick_auto_mode([m for m in cs.modes if m not in cs.bad_modes], fps, ok=cs.ok_modes)
            if pick:
                cam["mode"] = pick
                cs.auto_mode = pick
                log.info("modo automático de «%s»: %s", lab, pick)

    # ------------------------------------------------------------ hilos de fondo
    def _bg(self, fn, done):
        self.busy = True
        self._refresh_buttons()

        name = getattr(fn, "__name__", "fn")
        gen = self._gen

        def run():
            t = time.time()
            log.info("bg: inicio %s", name)
            try:
                res = fn()
            except Exception as e:           # noqa
                log.error("bg: %s fallo:\n%s", name, traceback.format_exc())
                res = e
            log.info("bg: fin %s en %.2fs", name, time.time() - t)
            self._bgq.append((done, res, gen))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ vista previa
    def toggle_preview(self):
        """La vista previa está siempre activa; este botón solo la pausa para liberar las cámaras."""
        if self.busy or self.recording:
            return
        if self.previewing:
            self.paused = True
            self._stop_preview()
        else:
            self.paused = False
            self._start_preview()

    def _auto_preview(self):
        """Mantiene la vista previa en marcha: arranca o reinicia cuando algo cambia."""
        self._restart_job = None
        if self.recording or self.paused or self._idle_mini():
            return
        if self.busy:
            self._restart_job = self.root.after(500, self._auto_preview)
            return
        if not self.order:
            if self.previewing:
                self._stop_preview()
            return
        if self.previewing:
            self._restart_preview()
        else:
            self._start_preview(auto=True)

    def _start_preview(self, retry=0, auto=False):
        if self._idle_mini():
            return                                  # el modo mini no usa la vista previa
        plan = self._plan()
        if plan is None:
            if not auto:
                messagebox.showwarning("Sin cámaras", "Marca al menos una cámara en la pestaña Cámaras.")
            return
        self.save_config()
        self._run_labels, self._run_color = list(plan["labels"]), dict(plan["run_color"])
        self._slow_warned = set()
        self._stuck_warned = False     # cada intento nuevo merece su propio plazo (ver _on_stuck)
        self._set_status("Abriendo dispositivos…" if retry == 0 else
                         "Reintentando (los dispositivos tardan en liberarse)… {0}".format(retry))

        def done(res):
            ok, err = res if isinstance(res, tuple) else (False, str(res))
            if ok:
                self.previewing = True
                self._last_fc = -1
                self._set_status(self._edit_hint() if self.editing else
                                 "Vista previa en marcha · {0} cámara(s).".format(len(plan["cams"])))
            elif retry < 3 and retry > 0:
                self.root.after(900, lambda: self._start_preview(retry + 1))
            else:
                self.previewing = False
                self.paused = True          # evita reintentar en bucle; F5 para reanudar
                self._set_status("No se pudo iniciar. Pulsa F5 para reintentar.")
                self._start_failed("Error al iniciar la vista previa", plan, err)
            self._refresh_buttons()

        self._bg(lambda: self._engine_start(plan), done)

    def _explain_start_error(self, plan, err):
        """Texto claro: qué dispositivo falló y qué probar."""
        kind, spec = failing_spec(err)
        who = ""
        if kind == "video" and plan:
            for i, c in enumerate(plan.get("cams", [])):
                if c.get("spec") == spec:
                    who = "No se pudo abrir la cámara «{0}».\n\n".format(plan["labels"][i])
                    break
            else:
                who = "No se pudo abrir una cámara.\n\n"
        elif kind == "audio":
            who = "No se pudo abrir un dispositivo de audio.\n\n"
        tips = ""
        if is_busy_error(err):
            tips = ("\n\nCausas habituales:\n"
                    "• Otra aplicación (Teams, OBS, el navegador, otra copia de CamUI o un "
                    "ffmpeg.exe que se quedó abierto) está usando la cámara.\n"
                    "• Varias cámaras USB en el mismo hub o puerto: no hay ancho de banda para todas. "
                    "Repártelas en puertos distintos o baja la resolución/FPS.\n"
                    "• La cámara no admite el modo elegido: pon «Captura: Auto».\n"
                    "Se reintentó varias veces con esperas crecientes.")
        elif is_mode_error(err):
            tips = ("\n\nLa cámara rechazó el modo de captura (tamaño / fps / formato), también el "
                    "de fábrica del driver. Pulsa «Detectar» y «Probar modos» en la pestaña Encuadre.")
        else:
            tips = ("\n\nSi una cámara no admite el modo elegido, pon «Captura: Auto». "
                    "Prueba también «Marcas del dispositivo».")
        return who + (err or "ffmpeg terminó al iniciar.") + tips

    def _start_failed(self, title, plan, err):
        msg = self._explain_start_error(plan, err)
        if not is_busy_error(err):
            messagebox.showerror(title, msg)
            return
        pids = [p for p in ffmpeg_pids() if p != os.getpid()]
        if pids:
            q = (msg + "\n\nHay {0} proceso(s) ffmpeg.exe abiertos (PID {1}) que pueden estar reteniendo "
                 "la cámara. ¿Cerrarlos y reintentar?").format(len(pids), ", ".join(map(str, pids)))
            if messagebox.askyesno(title, q):
                for p in pids:
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", str(p)], timeout=8,
                                       capture_output=True, creationflags=CREATE_NO_WINDOW)
                    except Exception as e:
                        log.warning("taskkill %s: %r", p, e)
                log.info("ffmpeg huérfanos cerrados: %s", pids)
                self._retry_preview(1200)
            return
        if messagebox.askretrycancel(title, msg):
            self._retry_preview(300)

    def _retry_preview(self, delay=300):
        self.paused = False
        self.root.after(delay, lambda: None if (self.busy or self.previewing or self.recording)
                        else self._start_preview())

    def _on_badmode(self, lab, bad, new):
        """(hilo principal) Una cámara rechazó su modo: se quita de la lista y vuelve a «Auto»."""
        cs = self.cams.get(lab)
        if cs is None:
            return
        cs.modes = [m for m in cs.modes if m != bad]
        if cs.mode.get() == bad:
            self._loading = True                 # no reiniciar la vista previa por este cambio
            try:
                cs.mode.set("Auto")
            finally:
                self._loading = False
        if self.sel == lab:
            self.w_mode.config(values=["Auto"] + cs.modes)
        self._set_status("«{0}» no admite el modo {1}: {2}.".format(
            lab, bad, "probando {0}".format(new) if new else "se usa el modo de fábrica del driver"
            if new == "" else "vuelve a Auto"))

    def _on_cam_offline(self, lab):
        """(hilo principal) Una cámara (de red o local) no se pudo abrir al (re)arrancar: en su
        sitio del mosaico queda un marcador «sin señal» y el resto sigue. Se vuelve a intentar
        de verdad la próxima vez que se reinicie la vista previa (cambiar un ajuste, quitar y
        volver a marcar la cámara, «Reiniciar», etc.) -- esto no reintenta ella sola en caliente."""
        self._set_status("«{0}» no se pudo conectar: sigue el resto, con un aviso «sin señal» "
                         "en su sitio. Para reintentarla, vuelve a aplicar algún cambio (o "
                         "«⚠ Reiniciar»).".format(lab))

    def panic_reset(self):
        """Botón de pánico: cierra TODOS los ffmpeg (también los huérfanos que retienen una
        cámara), cancela lo que esté en curso y vuelve a empezar."""
        if getattr(self, "_panic_on", False):
            return
        if self.recording and not messagebox.askyesno(
                "Reiniciar cámaras",
                "Se está grabando. Reiniciar corta la grabación (lo grabado hasta ahora se guarda)."
                "\n\n¿Continuar?"):
            return
        self._panic_on = True
        rec_path, rec_final = (self.rec_path, self.rec_final) if self.recording else (None, None)
        self._gen += 1                           # ignora los trabajos de fondo que estén colgados
        self.eng._abort = True                   # y los reintentos del motor
        self.recording = self.previewing = False
        self.paused = False
        if self._restart_job:
            try:
                self.root.after_cancel(self._restart_job)
            except Exception:
                pass
            self._restart_job = None
        self._set_status("Reiniciando: cerrando todos los ffmpeg…")
        log.warning("PÁNICO: cerrando ffmpeg")

        def work():
            pids = set(ffmpeg_pids())
            p = self.eng.proc
            if p is not None and p.poll() is None:
                pids.add(p.pid)
            pids.discard(os.getpid())
            n = sum(1 for pid in sorted(pids) if kill_pid(pid))
            try:
                self.eng.stop(timeout_wait=2.0)
            except Exception:
                log.error("pánico, stop:\n%s", traceback.format_exc())
            shown = ""
            if rec_path:
                try:
                    shown = self._finalize(rec_path, rec_final)[1]
                except Exception:
                    log.error("pánico, finalizar:\n%s", traceback.format_exc())
            time.sleep(RELEASE_GAP + 0.5)
            self._tray_q.append(("panic_done", (n, shown)))
        threading.Thread(target=work, daemon=True).start()

    def _panic_done(self, n, shown):
        self.eng._abort = False
        self._panic_on = False
        self.busy = False
        self.rec_clock0 = None
        self._lock(False)
        msg = "Reiniciado: {0} proceso(s) ffmpeg cerrado(s).".format(n)
        if shown:
            msg += " Vídeo guardado: {0}".format(shown)
        self._set_idle(msg)
        if not self._idle_mini():
            self.root.after(300, lambda: None if (self.busy or self.previewing or self.recording)
                            else self._start_preview(auto=True))

    def _stop_preview(self):
        self.previewing = False
        self._bg(self.eng.stop, lambda r: self._set_idle("Vista previa detenida."))

    def _restart_preview(self):
        self._restart_job = None
        if not self.previewing or self.recording or self._idle_mini():
            return
        if self.busy:
            self._restart_job = self.root.after(500, self._restart_preview)
            return
        plan = self._plan()
        if plan is None:
            self._stop_preview()
            return
        self._run_labels, self._run_color = list(plan["labels"]), dict(plan["run_color"])
        self.previewing = False

        def work():
            self.eng.stop()
            time.sleep(0.5)
            return self._engine_start(plan)

        def done(res):
            ok, err = res if isinstance(res, tuple) else (False, str(res))
            self.previewing = ok
            self._last_fc = -1
            if ok:
                self._set_status(self._edit_hint() if self.editing else "Cambios aplicados.")
            else:
                self.paused = True
                self._set_idle("No se pudo aplicar el cambio. Pulsa F5 para reintentar.")
                self._start_failed("No se pudo aplicar el cambio", plan, err)
            self._refresh_buttons()

        self._bg(work, done)

    # ------------------------------------------------------------ cámara virtual
    def toggle_vcam(self, *_a):
        """Activa / desactiva la cámara virtual (el mosaico como cámara para Teams, Zoom, Meet…)."""
        if self.busy:
            self._set_status("Un momento…")
            return
        if self.vcam_on:
            self._vcam_disable()
        elif self.recording:
            self._set_status("Para activar la cámara virtual termina antes la grabación.")
            self._refresh_buttons()
        else:
            self._vcam_enable()

    def _vcam_enable(self, quiet=False):
        """Abre la cámara virtual de OBS. quiet: al arrancar, sin diálogos. True si queda activa."""
        mod = self.vcam._module or load_pyvirtualcam()
        if mod is None:
            if quiet:
                log.info("cámara virtual: falta pyvirtualcam")
                self._later_status("Cámara virtual: falta pyvirtualcam (usa el botón 📹 para instalarlo).")
            else:
                self._vcam_offer_install()
            self._refresh_buttons()
            return False
        w, h = vcam_size_value(self.v_vsize.get())
        try:
            self.vcam.open(w, h, min(30, self._fps()))
        except Exception as e:
            log.error("cámara virtual: no se pudo abrir: %s", e)
            self._refresh_buttons()
            if quiet:
                self._later_status("No se pudo abrir la cámara virtual (¿OBS instalado?). Ver el botón 📹.")
            else:
                messagebox.showerror("Cámara virtual", "No se pudo abrir la cámara virtual.\n\n{0}\n\n{1}".format(
                    str(e).strip()[-600:], VCAM_INSTALL_HELP))
            return False
        self.vcam_on = True
        self.paused = False
        self._vcam_changed("Cámara virtual activa: «{0}» {1}×{2}.".format(
            self.vcam.device or "OBS Virtual Camera", w, h), quiet)
        return True

    def _vcam_disable(self, why=None):
        if not self.vcam_on and not self.vcam.active:
            return
        self.vcam_on = False
        self.vcam.release()
        self._vcam_changed(why or "Cámara virtual desactivada.", False)
        if why:
            self._later_status(why)

    def _vcam_changed(self, msg, quiet):
        self._refresh_buttons()
        self._tray_update(menu=True)
        if quiet:
            return                         # al arrancar: la primera vista previa ya la incluye
        self.save_config()
        self._set_status(msg)
        if self.recording:
            return                         # el ffmpeg en marcha se actualiza al terminar la grabación
        if self._idle_mini():
            self._sync_preview()           # mini sin cámara virtual: se liberan las cámaras
        elif self.order:
            self._schedule_restart(200)    # reinicia la vista previa con (o sin) la salida virtual

    def _later_status(self, txt):
        self.root.after(2500, lambda: self._set_status(txt))

    def _vcam_offer_install(self):
        if self._vcam_asked and not messagebox.askyesno(
                "Cámara virtual", "pyvirtualcam sigue sin estar instalado. ¿Intentar instalarlo de nuevo?"):
            return
        self._vcam_asked = True
        if messagebox.askyesno(
                "Cámara virtual",
                "Para ofrecer el mosaico como una cámara más en Teams, Zoom, Meet… hace falta el "
                "paquete «pyvirtualcam» (pequeño; se instala con pip y necesita internet) y tener "
                "OBS Studio instalado (gratis; no hace falta abrirlo).\n\n¿Instalar pyvirtualcam ahora?"):
            self._set_status("Instalando pyvirtualcam…")

            def run():
                ok, msg = pip_install("pyvirtualcam")
                self._tray_q.append(("vcam_installed", (ok, msg)))
            threading.Thread(target=run, daemon=True).start()

    def _on_vsize(self, *_a):
        """Cambio de tamaño de la cámara virtual: si está activa se vuelve a abrir."""
        if self._loading or not self.vcam_on:
            return
        if self.recording:
            return
        self.vcam_on = False
        self.vcam.release()
        self._vcam_enable()

    # ------------------------------------------------------------ salida en red
    def toggle_ipout(self, *_a):
        """Activa / desactiva la salida en red (ver el mosaico desde otro equipo o móvil)."""
        if self.busy:
            self._set_status("Un momento…")
            return
        if self.ipout_on:
            self._ip_disable()
        elif self.recording:
            self._set_status("Para activar la salida en red termina antes la grabación.")
            self._refresh_buttons()
        else:
            self._ip_enable()

    def _ip_port(self):
        try:
            p = int(self.v_ipport.get())
            if 1024 <= p <= 65535:
                return p
        except Exception:
            pass
        return IP_PORT_DEFAULT

    def _ip_url_list(self, port):
        """Todas las direcciones donde probar, ya en formato URL, como lista suelta (una por
        tarjeta de red de este equipo, lo más fiable también desde el móvil, y al final el
        nombre del equipo -- más fácil de recordar; funciona en muchas redes Windows sin más,
        pero no siempre desde un móvil). Ver _ip_urls() para la versión en un solo texto."""
        ips = _local_ips()
        urls = ["http://{0}:{1}/".format(ip, port) for ip in ips]
        if not urls:
            urls = ["http://(la IP de este equipo):{0}/".format(port)]
        urls.append("http://{0}:{1}/".format(socket.gethostname(), port))
        return urls

    def _ip_urls(self, port):
        """Todas las direcciones donde probar, unidas en un solo texto (para la bandeja y el
        aviso del estado; en Ajustes cada una se muestra aparte, como enlace clicable -- ver
        _ip_url_list y _set_link_list)."""
        return ", ".join(self._ip_url_list(port))

    def _set_link_list(self, frame, intro, urls):
        """Rellena «frame» (un ttk.Frame vacío, en el sitio de la antigua etiqueta de una sola
        línea) con, si se da «intro», una línea de texto normal, y debajo una línea por cada
        dirección de «urls», cada una de verdad clicable: la abre en el navegador de este mismo
        equipo (para comprobar rápido que responde), con el cursor de mano al pasar por encima --
        igual que ya hacía la dirección de la barra de estado (ver _open_ip_url). Con «urls»
        vacío solo se ve «intro» (p.ej. «No está activa.»)."""
        for w in frame.winfo_children():
            w.destroy()
        row = 0
        if intro:
            ttk.Label(frame, text=intro, style="Muted.TLabel", wraplength=380,
                      justify="left").grid(row=row, column=0, sticky="w")
            row += 1
        for url in urls:
            lbl = ttk.Label(frame, text=url, style="Muted.TLabel", cursor="hand2")
            lbl.grid(row=row, column=0, sticky="w")
            lbl.bind("<Button-1>", lambda e, u=url: self._open_url(u))
            row += 1

    def _open_url(self, url):
        """Clic en cualquier dirección clicable de la interfaz: la abre en el navegador
        predeterminado de este mismo equipo, para comprobar rápido que responde."""
        try:
            webbrowser.open(url)
            self._set_status("Abriendo en el navegador: {0}".format(url))
        except Exception:
            pass

    def _ip_primary_url(self):
        """Una sola dirección (la mejor suposición: la IP, o el nombre si no hay IP) para copiar
        al portapapeles con un solo clic."""
        ips = _local_ips()
        host = ips[0] if ips else socket.gethostname()
        return "http://{0}:{1}/".format(host, self.ipsrv.port)

    def _ip_dual_url(self):
        """IP + nombre del equipo, las dos en formato URL, para sitios con poco espacio (barra
        de estado, bandeja): si una de las dos no resuelve en el otro aparato, queda la otra a
        mano sin tener que ir a Ajustes a buscarla."""
        ips = _local_ips()
        port = self.ipsrv.port
        urls = ["http://{0}:{1}/".format(ips[0], port)] if ips else []
        urls.append("http://{0}:{1}/".format(socket.gethostname(), port))
        return ", ".join(urls)

    def _open_ip_url(self, *_a):
        """Clic en la dirección de la barra de estado: la abre en el navegador predeterminado
        (de este mismo equipo, para comprobar rápido que funciona)."""
        if not self.ipout_on:
            return
        self._open_url(self._ip_primary_url())

    def _open_in_vlc(self, *_a):
        """Abre el vídeo de «En la red» directamente en VLC (si está instalado), en vez del
        navegador. Desde ahí, el propio menú de VLC Reproducción -> Renderizador ya permite
        enviarlo a un Chromecast de la misma red (VLC transcodifica él mismo; cam-ui no tiene
        que hacer nada más). VLC no tiene AirPlay integrado -- solo existe como función del
        propio macOS, así que no sirve para el Windows al que apunta cam-ui."""
        if not self.ipout_on:
            self._set_status("Antes activa «En la red».")
            return
        url = "http://127.0.0.1:{0}/stream.mjpg".format(self.ipsrv.port)
        vlc = _find_vlc()
        if not vlc:
            if messagebox.askyesno(
                    "Abrir en VLC",
                    "No se encontró VLC instalado en este equipo.\n\n"
                    "VLC puede reproducir este vídeo y, desde su propio menú Reproducción -> "
                    "Renderizador, enviarlo a un Chromecast de la misma red sin que cam-ui tenga "
                    "que hacer nada más.\n\n¿Abrir la página de descarga de VLC "
                    "(videolan.org)?"):
                webbrowser.open("https://www.videolan.org/vlc/")
            return
        try:
            subprocess.Popen([vlc, url], creationflags=CREATE_NO_WINDOW)
            self._set_status("Abriendo en VLC… (allí: Reproducción -> Renderizador, para "
                             "enviarlo a un Chromecast)")
        except Exception as e:
            messagebox.showerror("Abrir en VLC", "No se pudo abrir VLC.\n\n{0}".format(
                str(e).strip()[-300:]))

    def _ip_enable(self, quiet=False):
        """Abre la salida en red. quiet: al arrancar, sin diálogos. True si queda activa. El
        tamaño elegido es el máximo: lo que de verdad se manda lleva la forma del mosaico (ver
        _plan / ip_fit_size)."""
        w, h = ip_size_value(self.v_ipsize.get())
        port = self._ip_port()
        try:
            self.ipsrv.open(w, h, IP_FPS, port)
        except OSError as e:
            log.error("salida en red: no se pudo abrir: %s", e)
            self._refresh_buttons()
            msg = "No se pudo abrir el puerto {0} (¿ya está en uso? prueba con otro en «Ajustes»).".format(port)
            if quiet:
                self._later_status(msg)
            else:
                messagebox.showerror("En la red", msg + "\n\n{0}".format(str(e).strip()[-300:]))
            return False
        except Exception as e:
            log.error("salida en red: no se pudo abrir: %s", e)
            self._refresh_buttons()
            if quiet:
                self._later_status("No se pudo activar la salida en red.")
            else:
                messagebox.showerror("En la red", "No se pudo activar la salida en red.\n\n{0}".format(
                    str(e).strip()[-300:]))
            return False
        self.ipout_on = True
        self.paused = False
        self._ip_changed("En la red: {0}".format(self._ip_urls(port)), quiet)
        return True

    def _ip_disable(self, why=None):
        if not self.ipout_on and not self.ipsrv.active:
            return
        self.ipout_on = False
        self.ipsrv.release()
        self._ip_changed(why or "Salida en red desactivada.", False)
        if why:
            self._later_status(why)

    def _ip_changed(self, msg, quiet):
        self._refresh_buttons()
        self._tray_update(menu=True)
        if getattr(self, "frm_ipurl", None) is not None:
            if self.ipout_on:
                self._set_link_list(self.frm_ipurl, "Disponible en:",
                                    self._ip_url_list(self.ipsrv.port))
            else:
                self._set_link_list(self.frm_ipurl, "No está activa.", [])
        if quiet:
            return                         # al arrancar: la primera vista previa ya la incluye
        self.save_config()
        self._set_status(msg)
        if self.recording:
            return                         # el ffmpeg en marcha se actualiza al terminar la grabación
        if self._idle_mini():
            self._sync_preview()           # mini sin salida en red: se liberan las cámaras
        elif self.order:
            self._schedule_restart(200)    # reinicia la vista previa con (o sin) la salida en red

    def _on_ipsize(self, *_a):
        """Cambio de tamaño de la salida en red: si está activa se vuelve a abrir."""
        if self._loading or not self.ipout_on:
            return
        if self.recording:
            return
        self.ipout_on = False
        self.ipsrv.release()
        self._ip_enable()

    def _on_ipport_commit(self, *_a):
        """Cambio de puerto confirmado (Intro o al salir del campo): si está activa se reabre."""
        if self._loading or not self.ipout_on or self.recording:
            return
        if self._ip_port() == self.ipsrv.port:
            return
        self.ipout_on = False
        self.ipsrv.release()
        self._ip_enable()

    # ------------------------------------------------------------ cámara del móvil
    def _phonecam_port(self):
        try:
            p = int(self.v_phonecamport.get())
            if 1024 <= p <= 65535:
                return p
        except Exception:
            pass
        return PHONECAM_PORT_DEFAULT

    def _phonecam_url(self):
        ips = _local_ips()
        host = ips[0] if ips else socket.gethostname()
        return "https://{0}:{1}/cam?t={2}".format(host, self.phonecam.port, self.phonecam.token)

    def _phonecam_add_url(self):
        """Dirección fija (sin ningún código al final) para que un móvil se empareje -- ver el
        comentario junto a «/add» en _PhoneCamHandler. Esta es la que se enseña al activar la
        función; la de _phonecam_url() (con el token) solo se usa ya por dentro, en el QR que
        se genera para cada emparejamiento nuevo."""
        ips = _local_ips()
        host = ips[0] if ips else socket.gethostname()
        return "https://{0}:{1}/add".format(host, self.phonecam.port)

    def _phonecam_add_url_list(self):
        """Todas las direcciones de «/add», una por cada IP de este equipo (y al final el
        nombre del equipo), para que se vean todas en Ajustes -- no solo la primera que se
        adivina -- igual que ya se hace en «En la red» (ver _ip_url_list)."""
        ips = _local_ips()
        urls = ["https://{0}:{1}/add".format(ip, self.phonecam.port) for ip in ips]
        if not urls:
            urls = ["https://(la IP de este equipo):{0}/add".format(self.phonecam.port)]
        urls.append("https://{0}:{1}/add".format(socket.gethostname(), self.phonecam.port))
        return urls

    def _phonecam_cert_url(self):
        """Dirección HTTP normal (sin cifrar a propósito -- ver el comentario junto a
        _PhoneCamCertHandler) donde instalar, solo en iPhone/iPad, el certificado de confianza
        de cam-ui. Solo existe mientras «Cámara del móvil» está activa."""
        ips = _local_ips()
        host = ips[0] if ips else socket.gethostname()
        return "http://{0}:{1}/".format(host, self.phonecam.cert_port)

    def _phonecam_qr_photo(self, url):
        """Imagen (Tk) del código QR con la dirección, o None si falta el paquete «qrcode»
        (opcional: sin él solo se pierde el QR, la dirección en texto sigue funcionando igual)."""
        try:
            import qrcode
            img = qrcode.make(url, box_size=4, border=2).convert("RGB")
            return ImageTk.PhotoImage(img)
        except Exception as e:
            log.info("QR de la cámara del móvil no disponible: %r", e)
            return None

    def _phonecam_enable(self, quiet=False):
        """Enciende los dos servidores (ver PhoneCamHub). quiet: al arrancar, sin diálogos."""
        try:
            self.phonecam.start(self._phonecam_port())
        except OSError as e:
            log.error("cámara del móvil: no se pudo abrir: %s", e)
            self._refresh_buttons()
            msg = "No se pudo abrir el puerto {0} (¿ya está en uso? prueba con otro en «Ajustes»).".format(
                self._phonecam_port())
            if quiet:
                self._later_status(msg)
            else:
                messagebox.showerror("Cámara del móvil", msg + "\n\n{0}".format(str(e).strip()[-300:]))
            return False
        except ModuleNotFoundError as e:
            # «cryptography» (certificado HTTPS, ver _phonecam_ensure_cert) va en requirements.txt
            # como obligatorio, pero quien instaló cam-ui hace tiempo y desde entonces solo se ha
            # ido actualizando sola (que solo cambia camui.py, nunca los paquetes de Python) puede
            # no tenerlo igualmente -- aquí se ofrece instalarlo, en vez de dejar un callejón sin
            # salida con el nombre interno de un módulo que a quien usa cam-ui no le dice nada.
            pkg = (getattr(e, "name", None) or "cryptography").split(".")[0]
            log.error("cámara del móvil: falta el paquete «%s»: %s", pkg, e)
            self._refresh_buttons()
            if quiet:
                self._later_status(
                    "Cámara del móvil: falta el paquete «{0}» -- ábrela en Ajustes para "
                    "instalarlo.".format(pkg))
            else:
                self._phonecam_offer_install(pkg)
            return False
        except Exception as e:
            log.error("cámara del móvil: no se pudo activar: %s", e)
            self._refresh_buttons()
            if quiet:
                self._later_status("No se pudo activar la cámara del móvil.")
            else:
                messagebox.showerror("Cámara del móvil", "No se pudo activar.\n\n{0}".format(
                    str(e).strip()[-300:]))
            return False
        self.phonecam_on = True
        self._phonecam_changed(quiet)
        return True

    def _phonecam_offer_install(self, pkg="cryptography"):
        """Mismo patrón que pystray/pyvirtualcam (ver _tray_boot/_vcam_offer_install): pregunta,
        instala con pip en un hilo aparte, y si sale bien reintenta sola la activación."""
        if messagebox.askyesno(
                "Cámara del móvil",
                "Para esto hace falta el paquete «{0}» (certificado HTTPS; se instala con pip y "
                "necesita internet). No parece estar instalado -- puede pasar si cam-ui se ha ido "
                "actualizando solo desde una versión antigua, porque eso solo cambia el programa, "
                "nunca los paquetes de Python que usa.\n\n¿Instalarlo ahora?".format(pkg)):
            self._set_status("Instalando {0}…".format(pkg))

            def run():
                ok, msg = pip_install(pkg)
                self._tray_q.append(("phonecam_pkg_installed", (ok, msg)))
            threading.Thread(target=run, daemon=True).start()

    def _phonecam_disable(self):
        if not self.phonecam_on and not self.phonecam.active:
            return
        self.phonecam_on = False
        self.phonecam.stop()
        self._phonecam_changed(False)

    def _phonecam_changed(self, quiet):
        self._refresh_buttons()
        self._phonecam_clear_pair_qr()          # también deja frm_phonecamurl en su texto normal
        if quiet:
            return
        self.save_config()
        self._set_status("Cámara del móvil activada." if self.phonecam_on else
                         "Cámara del móvil desactivada.")

    def _phonecam_show_pair_qr(self, url):
        """Un móvil llegó a «/add» y ya tiene el permiso de su cámara (ver /add/ready): se
        muestra aquí el QR de esa sesión nueva para que lo lea él mismo, con su propia cámara,
        sin salir de esa página -- en cuanto lo lea, navega solo a la URL que lleva dentro. Se
        muestra en dos sitios a la vez: dentro de la pestaña «Ajustes» (de siempre) y en un
        diálogo flotante que aparece al instante encima de lo que sea que haya abierto en ese
        momento (PhoneCamQrDialog) -- así no hace falta estar ya en esa pestaña para verlo."""
        if getattr(self, "lbl_phonecamqr", None) is None or not self.phonecam_on:
            return
        photo = self._phonecam_qr_photo(url)
        self._phonecam_qr_img = photo                  # referencia viva, si no Tk la olvida
        self.lbl_phonecamqr.config(image=photo or "")
        if photo is not None:
            self._set_link_list(self.frm_phonecamurl,
                                "Un móvil está esperando: apunta su cámara a este código.", [])
        else:
            # sin el paquete «qrcode» no hay imagen que mostrar, pero la dirección en texto
            # sigue valiendo igual (se puede teclear a mano en el móvil, aunque sea incómodo) --
            # y, ya que aparece, que sea clicable también aquí mismo, por si se quiere probar
            self._set_link_list(self.frm_phonecamurl,
                                "Un móvil está esperando: abre esto a mano en su navegador:", [url])
        self._phonecam_pair_shown_at = time.time()
        self._phonecam_popup_qr(url, photo)

    def _phonecam_popup_qr(self, url, photo):
        self._phonecam_close_popup_qr()
        on_install = None if photo is not None else self._phonecam_offer_qr_install
        try:
            self._phonecam_qr_dialog = PhoneCamQrDialog(self.root, photo, url, on_install)
        except Exception as e:
            log.info("no se pudo abrir el diálogo del QR de la cámara del móvil: %r", e)
            self._phonecam_qr_dialog = None

    def _phonecam_offer_qr_install(self, url):
        """Botón «Instalar «qrcode»…» del propio diálogo del QR (ver PhoneCamQrDialog), cuando
        no hay imagen que mostrar por faltar ese paquete -- mismo patrón de pip_install que
        _phonecam_offer_install. Solo hace falta volver a pedir la imagen y reabrir el mismo
        diálogo con la misma URL: la sesión de emparejamiento sigue esperando igual."""
        if messagebox.askyesno(
                "Cámara del móvil",
                "Para mostrar el código QR (en vez de la dirección en texto, incómoda de "
                "teclear a mano) hace falta el paquete «qrcode» (pequeño; se instala con pip y "
                "necesita internet). No parece estar instalado.\n\n¿Instalarlo ahora?"):
            self._set_status("Instalando qrcode…")

            def run():
                ok, msg = pip_install("qrcode")
                self._tray_q.append(("phonecam_qr_installed", (ok, msg, url)))
            threading.Thread(target=run, daemon=True).start()

    def _phonecam_close_popup_qr(self):
        dlg = getattr(self, "_phonecam_qr_dialog", None)
        if dlg is not None:
            dlg.close()
        self._phonecam_qr_dialog = None

    def _phonecam_show_cert_dialog(self):
        """Botón «Instalar certificado (solo iPhone/iPad)» de Ajustes: abre PhoneCamCertDialog
        con el QR (o la dirección en texto) para instalarlo. Hace falta que «Cámara del móvil»
        esté encendida, porque el certificado que se instala es el de _PhoneCamCertHandler, que
        corre junto al resto de servidores de PhoneCamHub (ver PhoneCamHub.start)."""
        if not self.phonecam_on or not self.phonecam.active:
            messagebox.showinfo(
                "Certificado para iPhone/iPad",
                "Primero activa «Cámara del móvil» (botón de arriba) -- este certificado se "
                "sirve junto con ella.")
            return
        url = self._phonecam_cert_url()
        photo = self._phonecam_qr_photo(url)
        self._phonecam_cert_qr_img = photo              # referencia viva, si no Tk la olvida
        dlg = getattr(self, "_phonecam_cert_dialog", None)
        if dlg is not None:
            dlg.close()
        on_install = None if photo is not None else self._phonecam_offer_cert_qr_install
        try:
            self._phonecam_cert_dialog = PhoneCamCertDialog(self.root, photo, url, on_install)
        except Exception as e:
            log.info("no se pudo abrir el diálogo del certificado para iPhone/iPad: %r", e)
            self._phonecam_cert_dialog = None

    def _phonecam_offer_cert_qr_install(self, url):
        """Mismo patrón que _phonecam_offer_qr_install, pero para el QR del certificado: al
        terminar, en vez de reabrir el emparejamiento, reabre este mismo diálogo."""
        if messagebox.askyesno(
                "Certificado para iPhone/iPad",
                "Para mostrar el código QR (en vez de la dirección en texto, incómoda de "
                "teclear a mano) hace falta el paquete «qrcode» (pequeño; se instala con pip y "
                "necesita internet). No parece estar instalado.\n\n¿Instalarlo ahora?"):
            self._set_status("Instalando qrcode…")

            def run():
                ok, msg = pip_install("qrcode")
                self._tray_q.append(("phonecam_cert_qr_installed", (ok, msg, url)))
            threading.Thread(target=run, daemon=True).start()

    def _phonecam_show_devices_dialog(self):
        """Botón «Dispositivos recordados» de Ajustes: lista los móviles que ya se emparejaron
        antes (ver PhoneCamHub.list_devices) y deja «olvidar» cualquiera. No hace falta que
        «Cámara del móvil» esté encendida -- la lista vive en su propio archivo aparte (ver
        PHONECAM_DEVICES_FILE), no en la sesión en marcha."""
        dlg = getattr(self, "_phonecam_devices_dialog", None)
        if dlg is not None:
            dlg.close()
        try:
            self._phonecam_devices_dialog = PhoneCamDevicesDialog(self.root, self.phonecam)
        except Exception as e:
            log.info("no se pudo abrir la lista de dispositivos recordados: %r", e)
            self._phonecam_devices_dialog = None

    def _phonecam_clear_pair_qr(self):
        self._phonecam_pair_shown_at = 0.0
        if getattr(self, "lbl_phonecamqr", None) is not None:
            self.lbl_phonecamqr.config(image="")
        self._phonecam_qr_img = None
        if getattr(self, "frm_phonecamurl", None) is not None:
            if self.phonecam_on:
                self._set_link_list(
                    self.frm_phonecamurl,
                    "Accede a cualquiera de estas direcciones desde el navegador del móvil "
                    "para añadir la cámara de tu móvil a la lista de dispositivos:",
                    self._phonecam_add_url_list())
            else:
                self._set_link_list(self.frm_phonecamurl, "No está activa.", [])
        self._phonecam_close_popup_qr()

    def _on_phonecamport_commit(self, *_a):
        if self._loading or not self.phonecam_on:
            return
        if self._phonecam_port() == self.phonecam.port:
            return
        self.phonecam.stop()
        self.phonecam_on = False
        self._phonecam_enable()

    def _phonecam_auto_add(self, name, url):
        """Un móvil mandó su primer fotograma de verdad (ver PhoneCamHub.feed): se añade solo
        al mosaico, como cualquier otra cámara de red (mismo camino que «Añadir cámara de
        red…», ver _net_cam_dialog) -- se llama desde _tick(), ya en el hilo de Tk."""
        self._phonecam_clear_pair_qr()      # llegar hasta aquí significa que el QR ya se leyó
        if self.recording or self.busy:
            # igual que al añadir una cámara de red a mano: no se toca la lista mientras se
            # graba o hay una operación en curso. El móvil sigue mandando fotogramas aparte (su
            # sesión no caduca por esto); si se quiere esa conexión en concreto, basta con
            # recargar la página del móvil cuando termine la grabación.
            self._set_status("Móvil «{0}» conectado, pero hay que esperar a que termine la "
                             "grabación para añadirlo al mosaico.".format(name))
            return
        label = base = "📱 " + name
        n = 2
        while label in self.vdev:
            label = "{0} #{1}".format(base, n)
            n += 1
        cam = {"name": name, "alt": None, "label": label, "spec": url, "net": True, "phone": True}
        self.net_cams[label] = cam
        self.vdevs.append(cam)
        self.vdev[label] = cam
        self.cams[label] = CamState(label, None, self._cb)
        self.cams[label].use = True
        if label not in self.order:
            self.order.append(label)
        self.sel = label
        self._fill_tree()
        self._bind_detail()
        self._redraw_map()
        self._update_info()
        self._on_change()
        self._set_status("Móvil «{0}» conectado: añadido al mosaico.".format(name))

    # ------------------------------------------------------------ grabación
    def toggle_rec(self):
        if self.busy:
            if self.mode == "mini":
                self._set_status("Un momento…")
            return
        if self.editing and not self.recording:
            self._set_status("Termina el encuadre (Esc o «Listo») antes de grabar.")
            return
        if self.recording:
            self._stop_rec()
        elif self.previewing or self.mode == "mini":
            self._start_rec()

    def _start_rec(self):
        plan = self._plan()
        if plan is None:
            self._set_status("No hay cámaras marcadas: elígelas en el modo normal.")
            return
        folder = self.v_dir.get() or DEF_DIR
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Carpeta no válida", str(e))
            return
        base = re.sub(r'[\\/:*?"<>|]', "_", (self.v_name.get() or "Multivista").strip()) or "Multivista"
        ext = "mkv" if self.v_container.get() == "mkv" else "mp4"
        final = os.path.join(folder, "{0}_{1}.{2}".format(
            base, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"), ext))
        outfile = final if ext == "mkv" else final[:-4] + ".rec.mkv"
        self.rec_final = final
        warm = max(1.0, self.eng.startup_time + 0.3)
        self.save_config()
        self._run_labels, self._run_color = list(plan["labels"]), dict(plan["run_color"])
        self.previewing = False
        self._set_status("Abriendo dispositivos para grabar…")

        def work():
            self.eng.stop()
            time.sleep(0.5)
            return self._engine_start(plan, outfile=outfile, warmup=warm)

        def done(res):
            ok, err = res if isinstance(res, tuple) else (False, str(res))
            if ok:
                self.recording = True
                self.previewing = True
                self.rec_path = outfile
                self.rec_clock0 = None
                self._last_fc = -1
                self._lock(True)
                self._set_status("Grabando → {0}".format(outfile))
            else:
                messagebox.showerror("Error al grabar", self._explain_start_error(plan, err))
                self._set_idle("No se pudo grabar.")
                self.root.after(500, lambda: self._start_preview(1))
            self._refresh_buttons()

        self._bg(work, done)

    def _stop_rec(self):
        self._set_status("Finalizando el vídeo…")
        self.recording = False
        self.previewing = False
        path, final = self.rec_path, self.rec_final

        def work():
            self.eng.stop()
            return self._finalize(path, final)

        def done(res):
            self._lock(False)
            self.lbl_timer.config(text="")
            ok, shown, msg = res if isinstance(res, tuple) else (False, path, str(res))
            if ok:
                mb = os.path.getsize(shown) / 1048576.0
                self._set_status("Grabado: {0}  ({1:.1f} MB)".format(shown, mb))
            else:
                self._set_status("ERROR: no se pudo cerrar el vídeo. {0}".format(msg))
            self.root.after(500, lambda: self._start_preview(1))

        self._bg(work, done)

    def _finalize(self, tmp, final):
        """(ok, ruta_final, mensaje). Pasa el MKV temporal al contenedor elegido."""
        if not (tmp and os.path.isfile(tmp) and os.path.getsize(tmp) > 0):
            return False, tmp, "no se creó el archivo"
        if tmp == final:
            return True, final, ""
        ok, msg = remux_recording(tmp, final, self.eng.ffmpeg)
        if ok:
            return True, final, ""
        return True, tmp, "MP4 no generado; el vídeo quedó en MKV"

    def take_photo(self):
        frame, _ = self.eng.get_latest()
        if frame is None:
            return
        folder = self.v_dir.get() or DEF_DIR
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "Foto_{0}.png".format(
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
        try:
            Image.fromarray(np.ascontiguousarray(frame[:, :, ::-1])).save(path)
            self._set_status("Foto guardada: {0}".format(path))
        except Exception as e:
            self._set_status("No se pudo guardar la foto: {0}".format(e))

    # ------------------------------------------------------------ estado de la UI
    def _lock(self, locked):
        def rec(w):
            for ch in w.winfo_children():
                try:
                    if isinstance(ch, ttk.Widget) and str(ch) not in self._live and \
                            not isinstance(ch, (ttk.Frame, ttk.Labelframe, ttk.Label, ttk.Scrollbar)):
                        ch.state(["disabled"] if locked else ["!disabled"])
                except Exception:
                    pass
                rec(ch)
        for tab in self.nb.tabs():
            rec(self.nb.nametowidget(tab))
        self._bind_detail()          # el color sigue activo al grabar; lo demás, bloqueado

    def _set_idle(self, msg):
        self.recording = False
        self.previewing = False
        self.lbl_timer.config(text="")
        self._lock(False)
        self.lbl_info.config(text="")
        self._set_status(msg)
        self._refresh_buttons()

    def _refresh_buttons(self):
        b = self.busy
        m = self.mode != "normal"
        mini = self.mode == "mini"

        def T(full, short):
            return short if m else full
        if self.recording:
            self.btn_prev.config(text=T("▶  Vista previa  (F5)", "▶  Vista"), state="disabled")
            self.btn_rec.config(text=T("■  Parar  (F9)", "■  Parar"),
                                state="disabled" if b else "normal")
        elif self.previewing:
            self.btn_prev.config(text=T("⏸  Pausar vista previa  (F5)", "⏸  Pausa"),
                                 state="disabled" if b else "normal")
            self.btn_rec.config(text=T("●  Grabar  (F9)", "●  Grabar"),
                                state="disabled" if (b or self.editing) else "normal")
        else:
            self.btn_prev.config(text=T("▶  Reanudar vista previa  (F5)", "▶  Vista"),
                                 state="disabled" if b else "normal")
            self.btn_rec.config(text=T("●  Grabar  (F9)", "●  Grabar"),
                                state=("disabled" if b else "normal") if mini else "disabled")
        self.btn_photo.config(text=T("Foto  (F8)", "Foto"),
                              state="normal" if (self.previewing and not b) else "disabled")
        von = self.vcam_on
        vst = "disabled" if (b or (self.recording and not von)) else "normal"
        mark = "●" if von else "○"
        self.btn_vcam.config(text=("📹 " + mark) if mini else ("📹 Virtual " + mark),
                             style="Accent.TButton" if von else "TButton", state=vst)
        if getattr(self, "btn_vcam2", None) is not None:
            self.btn_vcam2.config(text="📹  Apagar cámara virtual" if von else "📹  Activar cámara virtual",
                                  style="Accent.TButton" if von else "TButton", state=vst)
        self._vcam_var.set(von)
        ion = self.ipout_on
        ist = "disabled" if (b or (self.recording and not ion)) else "normal"
        imark = "●" if ion else "○"
        self.btn_ip.config(text=("🌐 " + imark) if mini else ("🌐 Red " + imark),
                           style="Accent.TButton" if ion else "TButton", state=ist)
        if getattr(self, "btn_ip2", None) is not None:
            self.btn_ip2.config(text="🌐  Apagar en la red" if ion else "🌐  Activar en la red",
                                style="Accent.TButton" if ion else "TButton", state=ist)
        if getattr(self, "btn_vlc", None) is not None:
            self.btn_vlc.config(state="disabled" if (b or not ion) else "normal")
        self._ip_var.set(ion)
        if getattr(self, "lbl_ip_bar", None) is not None:
            self.lbl_ip_bar.config(text=("🌐 " + self._ip_dual_url()) if ion else "")
        self._tray_update()

    def _set_status(self, txt):
        self.status.config(text="  " + txt)
        if self.mode == "mini" and not self.recording or txt.startswith("Grabado"):
            self._mini_set(txt)

    # ------------------------------------------------------------ bucle de UI
    def _on_view_resize(self, e):
        self.view.coords(self._ph, e.width / 2, e.height / 2)
        self.view.coords(self._img_item, e.width / 2, e.height / 2)
        self._last_fc = -1
        self._redraw_overlay()

    def _tick(self):
        self._arm_watchdog()
        try:
            if self._tray_q:
                self._tray_drain()
            while self._phonecam_q:
                name, url = self._phonecam_q.popleft()
                self._phonecam_auto_add(name, url)
            while self._phonecam_pair_q:
                self._phonecam_show_pair_qr(self._phonecam_pair_q.popleft())
            while self._phonecam_remote_q:
                self._phonecam_remote_q.popleft()
                # un móvil pidió, desde el botón de «En la red», que se encienda sola -- sin
                # diálogos que puedan bloquear nada (quiet=True: puede llegar en cualquier
                # momento, sin que haya nadie delante del PC); si de verdad se encendió, se
                # guarda como encendida (igual que el interruptor de Ajustes) y se avisa en la
                # barra de estado, sin bloquear nada, por si hay alguien mirando
                if not self.phonecam_on and self._phonecam_enable(quiet=True):
                    self.save_config()
                    self._set_status("Un móvil encendió «Cámara del móvil» para añadirse.")
            while self._bgq:
                done, res, gen = self._bgq.popleft()
                if gen != self._gen:
                    continue                  # trabajo anterior a un reinicio de pánico
                self.busy = False
                done(res)
                self._refresh_buttons()
            if (self.previewing or self.recording) and not self.busy and not self.eng.is_running():
                self._on_died()
            elif (self.previewing and not self.busy and not self._stuck_warned and
                  self.eng.frame_count == 0 and self.eng._spawn_t and
                  time.time() - self.eng._spawn_t > STUCK_PREVIEW_SECONDS):
                self._on_stuck()
            if self.previewing and self.mode != "mini":
                self._draw_frame()
            self._check_sources()
            self._update_mode_label()
            self._tick_n = (getattr(self, "_tick_n", 0) + 1) % 3
            if self._tick_n == 0 and self.mode != "mini":   # VU a ~10 Hz (cuestan 4 ms cada vez)
                self._draw_meters()
            if self._tick_n == 0 and self.ipsrv is not None:
                self.ipsrv.hls_idle_check()       # apaga solo el ffmpeg de HLS si nadie lo mira
            if self._tick_n == 0 and self.phonecam is not None:
                self.phonecam.drop_stale()        # olvida sesiones de móvil caducadas hace rato
                if (self._phonecam_pair_shown_at and
                        time.time() - self._phonecam_pair_shown_at > PHONECAM_PAIR_TIMEOUT):
                    self._phonecam_clear_pair_qr()    # nadie lo escaneó a tiempo: se retira solo
            if self.recording:
                self._update_timer()
        except Exception as e:          # la UI nunca debe morir por un fallo de dibujo
            log.error("error en _tick:\n%s", traceback.format_exc())
            self.status.config(text="  (aviso) {0}".format(e))
        self.root.after(33, self._tick)

    def _draw_frame(self):
        frame, fc = self.eng.get_latest()
        size = (self.view.winfo_width(), self.view.winfo_height())
        if frame is None or (fc == self._last_fc and size == self._last_size):
            return
        self._last_fc, self._last_size = fc, size
        h, w = frame.shape[:2]
        nw, nh = fit_size(w, h, size[0], size[1])
        # BGR -> RGB sin copiar en NumPy; solo se reescala si la ventana es más pequeña
        pil = Image.frombuffer("RGB", (w, h), frame, "raw", "BGR", 0, 1)
        if (nw, nh) != (w, h):
            pil = pil.resize((nw, nh), getattr(Image, "Resampling", Image).BILINEAR)
        tk_img = self._tkimg
        if tk_img is not None and tk_img.width() == nw and tk_img.height() == nh:
            tk_img.paste(pil)                 # reutiliza la imagen de Tk: mucho más barato
        else:
            self._tkimg = ImageTk.PhotoImage(pil)
            self.view.itemconfig(self._img_item, image=self._tkimg)
        if self._ph_visible:
            self.view.itemconfig(self._ph, state="hidden")
            self._ph_visible = False
        self._redraw_overlay()
        m = self.eng.meta
        info = "Vista previa {0}×{1}  ·  salida {2}×{3}  ·  {4} fps  ·  audio {5}".format(
            w, h, m.get("W", 0), m.get("H", 0), self._fps(),
            "L/R" if m.get("has_audio") else "sin audio")
        if info != self._info_txt:
            self._info_txt = info
            self.lbl_info.config(text=info)

    def _draw_meters(self):
        c = self.meter
        c.delete("all")
        w = max(200, c.winfo_width())
        active = self.previewing and self.eng.meta.get("has_audio")
        now = time.time()
        x0, x1 = 22, w - 62
        span = 80.0                                     # escala: -80 dBFS ... 0 dBFS

        def xpos(db):
            return x0 + (x1 - x0) * max(0.0, min(1.0, (db + span) / span))
        for i, name in enumerate("LR"):
            raw = self.eng.levels[i] if active else -90.0
            self._meter[i] = max(raw, self._meter[i] - 4.5)
            lv = self._meter[i]
            if raw >= self._peak[i] or now - self._peak_t[i] > 1.5:
                self._peak[i], self._peak_t[i] = raw, now
            y = 3 + i * 16
            c.create_text(8, y + 6, text=name, fill=MUTED, anchor="w", font=("Segoe UI", 8, "bold"))
            c.create_rectangle(x0, y, x1, y + 12, fill=BG, outline=LINE)
            if lv > -85:
                xe = max(xpos(lv), x0 + 3)              # siempre visible si hay señal
                # tramos verde / amarillo / rojo
                for lo, hi, col in ((-80, -18, OK_GREEN), (-18, -6, WARN), (-6, 0, REC_RED)):
                    a, b = xpos(lo), min(xpos(hi), xe)
                    if b > a:
                        c.create_rectangle(a, y + 1, b, y + 11, fill=col, outline="")
            for t in (-60, -40, -20, -10, -3):          # marcas de la escala
                c.create_line(xpos(t), y + 8, xpos(t), y + 12, fill=MUTED)
            if self._peak[i] > -85:
                px = xpos(self._peak[i])
                c.create_line(px, y, px, y + 12, fill="#ffffff", width=2)
            c.create_text(x1 + 6, y + 6, anchor="w", fill=FG if lv > -85 else MUTED,
                          font=("Consolas", 8),
                          text="{0:4.0f} dB".format(lv) if lv > -85 else "  -∞ dB")

    def _update_timer(self):
        if self.rec_clock0 is None:
            if self.eng.frame_count > 0:
                self.rec_clock0 = time.time()
            else:
                self.lbl_timer.config(text="iniciando…", foreground=WARN)
                return
        el = int(time.time() - self.rec_clock0)
        try:
            mb = os.path.getsize(self.rec_path) / 1048576.0
        except Exception:
            mb = 0.0
        dot = "●" if el % 2 == 0 else "○"
        txt = "{0} {1:02d}:{2:02d}:{3:02d}".format(dot, el // 3600, (el // 60) % 60, el % 60)
        if self.mode != "mini":
            txt += "  {0:.0f} MB".format(mb)
        else:
            self.lbl_ms.config(text="{0:.0f} MB".format(mb))
        self.lbl_timer.config(text=txt, foreground=REC_RED)

    def _on_died(self):
        was_rec, path, final = self.recording, self.rec_path, self.rec_final
        err = self.eng.last_errors()
        log.error("ffmpeg murio solo (rec=%s). Ultimas lineas:\n%s", was_rec, err)
        self.eng.stop()
        if not was_rec:
            # si no se estaba grabando (solo vista previa) y el error señala justo a una
            # cámara del mosaico actual, se reintenta sola: _start_preview -> _engine_start ya
            # sustituye esa cámara por un marcador «sin señal» si vuelve a fallar (ver
            # _drop_bad_cam), así que el resto sigue en vez de dejar todo caído con un aviso.
            # Si el fallo no se puede identificar con ninguna cámara, se deja el aviso de
            # siempre: mejor un error claro que un reintento ciego sin fin.
            plan = self._plan()
            if plan and failing_cam_index(plan["cams"], err) is not None:
                log.warning("vista previa: se reintenta tras identificar la cámara caída")
                self.previewing = False
                self._start_preview(auto=True)
                return
        self._set_idle("ffmpeg se detuvo.")
        msg = "ffmpeg se detuvo de forma inesperada (¿cámara desconectada?).\n\n" + err
        if was_rec:
            ok, shown, _m = self._finalize(path, final)
            msg += "\n\nEl vídeo parcial está en:\n{0}".format(shown)
        messagebox.showerror("Error", msg)

    def _on_stuck(self):
        """ffmpeg sigue vivo (is_running() == True, así que _on_died no detecta nada) pero no ha
        entregado ni un solo fotograma en STUCK_PREVIEW_SECONDS -- _engine_start solo comprueba
        que no se cayera en 1.6s para darlo por arrancado («Vista previa en marcha»), no que de
        verdad llegara a producir algo. Suele ser una cámara lenta en iniciar (p.ej. el software
        de una réflex/EOS) o un atasco negociando con otra del mosaico: ffmpeg se queda colgado
        sin más, sin avisar de nada. Sin esto, quien lo sufre se queda con la vista previa en
        negro para siempre, sin ningún error ni forma de saber qué ha pasado (caso real: ver el
        aviso del usuario, pack 22, "no se ve la vista previa")."""
        self._stuck_warned = True
        err = self.eng.last_errors()
        log.error("vista previa: ffmpeg sigue vivo pero no entregó ningún fotograma en %gs. "
                 "Últimas líneas:\n%s", STUCK_PREVIEW_SECONDS, err)
        self.eng.stop()
        self.previewing = False
        self.paused = True          # evita reintentar en bucle; F5 para reanudar, igual que _start_preview
        self._set_idle("No se pudo iniciar: ffmpeg se quedó colgado. Pulsa F5 para reintentar.")
        msg = ("ffmpeg se quedó colgado al iniciar: llevaba más de {0:g} segundos abierto sin "
              "llegar a entregar ni un fotograma.\n\nSuele ser una cámara lenta en arrancar "
              "(por ejemplo, el software de una réflex/EOS) o un atasco con otra cámara del "
              "mosaico. Prueba a desmarcar cámaras una a una (pestaña Cámaras) hasta encontrar "
              "cuál lo cuelga, o pulsa F5 para reintentar.").format(STUCK_PREVIEW_SECONDS)
        if err:
            msg += ("\n\nÚltimas líneas de ffmpeg (puede no haber ninguna pista útil, si de "
                   "verdad se quedó colgado sin más):\n" + err)
        messagebox.showerror("Vista previa colgada", msg)

    def on_close(self):
        if self.recording and not messagebox.askyesno(
                "Grabando", "Se está grabando.\n\n¿Parar la grabación, guardar el vídeo y salir?"):
            return
        log.info("cerrando")
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        try:
            self.save_config()
            if self.recording or self.previewing:
                self.status.config(text="  Cerrando…")
                self.root.update_idletasks()
            rec_path, rec_final = (self.rec_path, self.rec_final) if self.recording else (None, None)
            self.recording = self.previewing = False
            self.eng.stop()
            if rec_path:                          # que el vídeo quede cerrado y en el contenedor elegido
                ok, shown, msg = self._finalize(rec_path, rec_final)
                log.info("grabación cerrada al salir: %s %s", shown, msg)
        finally:
            try:
                self.vcam.close()
            except Exception:
                pass
            try:
                self.ipsrv.close()
            except Exception:
                pass
            try:
                self.phonecam.stop()
            except Exception:
                pass
            try:
                if self._tray is not None:
                    self._tray.stop()
            except Exception:
                pass
            self.root.destroy()


# =====================================================================
#  Consola (CLI)
# =====================================================================
def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s or "").strip()


def _dshow_in(device, size=None, fps=None):
    a = ["-f", "dshow", "-rtbufsize", "256M"]
    if fps:
        a += ["-framerate", str(fps)]
    if size:
        a += ["-video_size", size]
    a += ["-i", "video=" + device]
    return a


def cli_list(_args):
    v, a = list_devices()
    print("Cámaras de vídeo:")
    for i, d in enumerate(v, 1):
        print("  {0}) {1}".format(i, d["label"]))
    print("Dispositivos de audio:")
    for i, d in enumerate(a, 1):
        print("  {0}) {1}".format(i, d["label"]))
    return 0


def cli_modes(args):
    v, _a = list_devices()
    d = next((x for x in v if x["label"] == args.device or x["name"] == args.device), None)
    if not d:
        print("Cámara no encontrada: {0}".format(args.device))
        return 1
    for m in list_camera_modes(d["spec"]):
        print("  " + m)
    return 0


def cli_preview(args):
    cmd = [FFPLAY, "-hide_banner", "-window_title", "CamWin32 - " + args.device]
    cmd += _dshow_in(args.device, args.size, args.fps)
    print("[Vista] {0}  (cierra la ventana para salir)".format(args.device))
    return subprocess.call(cmd)


def cli_record(args):
    out = args.out or os.path.join(DEF_DIR, "{0}_{1}.mp4".format(_safe(args.device), _stamp()))
    cmd = [FFMPEG, "-hide_banner", "-y"] + _dshow_in(args.device, args.size, args.fps)
    if args.audio:
        cmd += ["-f", "dshow", "-audio_buffer_size", "50", "-i", "audio=" + args.audio]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if args.audio:
        cmd += ["-af", "aresample=async=100:min_comp=0.02:min_hard_comp=0.2:first_pts=0", "-c:a", "aac", "-b:a", "192k"]
    if args.duration and args.duration > 0:
        cmd += ["-t", str(args.duration)]
    cmd += ["-movflags", "+faststart", out]
    print("[Grabar] {0} -> {1}".format(args.device, out))
    if not args.duration:
        print("         (pulsa 'q' en esta ventana para detener)")
    rc = subprocess.call(cmd, creationflags=CREATE_NO_WINDOW)
    print("[OK] guardado: " + out)
    return rc


def cli_photo(args):
    out = args.out or os.path.join(DEF_DIR, "{0}_{1}.jpg".format(_safe(args.device), _stamp()))
    cmd = [FFMPEG, "-hide_banner", "-y"] + _dshow_in(args.device, args.size, args.fps) + \
        ["-frames:v", "1", "-q:v", "2", out]
    subprocess.call(cmd, creationflags=CREATE_NO_WINDOW)
    print("[OK] foto: " + out)
    return 0


def cli_multi(args):
    v, a = list_devices()
    amap = dict((d["label"], d) for d in a)
    chosen = args.devices or [d["label"] for d in v]
    vmap = dict((d["label"], d) for d in v)
    cams = []
    for name in chosen:
        d = vmap.get(name) or next((x for x in v if x["name"] == name), None)
        spec = d["spec"] if d else name
        cams.append({"spec": spec, "h": False, "v": False, "rot": 0, "tile": "auto", "zoom": 100,
                     "cx": 50, "cy": 50, "delay": 0, "mode": None})
    if not cams:
        print("No hay cámaras para el multivista.")
        return 1
    n = len(cams)
    plan = {"cams": cams, "fps": args.fps or 30, "sync": args.sync, "crf": args.crf,
            "preset": "veryfast", "duration": args.duration or None}
    if args.cols or args.rows or args.cellh:                 # cuadrícula uniforme (modo clásico)
        cols, rows = grid_for(n, args.cols or 0, args.rows or 0)
        if args.cellh:
            cw, ch = _even(args.cellw), _even(args.cellh)
        else:
            aw, ah = (int(x) for x in args.aspect.split(":"))
            cw, ch = cell_size(aw, ah, args.cellw)
        plan.update(cols=cols, rows=rows, cw=cw, ch=ch)
    else:                                                    # reparto adaptado a cada cámara
        given = [parse_ratio(x) for x in (args.cam_aspects or "").split(",") if x.strip()]
        aspects = []
        for i, c in enumerate(cams):
            asp = given[i] if i < len(given) and given[i] else None
            if asp:
                c["src"] = (int(round(asp * 1000)), 1000)    # forma conocida: celda sin barras
            aspects.append(asp or 16 / 9.0)
        canvas = None
        if args.canvas.lower() not in ("adapt", "auto", "adaptado"):
            aw, ah = (int(x) for x in args.canvas.split(":"))
            canvas = (aw, ah)
        rows_s = parse_structure(args.structure, n) if args.structure else None
        rows_s = rows_s or best_structure(aspects, args.width, args.transpose, canvas)
        plan["geom"] = layout_tiles(aspects, rows_s, args.width, args.transpose, canvas)
        print("[Multivista] reparto {0} -> {1}x{2}".format(structure_label(rows_s),
                                                           plan["geom"]["W"], plan["geom"]["H"]))

    def row(label, chan):
        if not label:
            return None
        d = amap.get(label) or next((x for x in a if x["name"] == label), None)
        return {"spec": d["spec"] if d else label, "chan": chan, "gain": 0, "delay": 0}
    if args.audio:
        arow = {"L": row(args.audio, "left"), "R": row(args.audio, "right")}
    else:
        arow = {"L": row(args.audio_l, args.chan_l), "R": row(args.audio_r, args.chan_r)}
    arow["offset"] = args.av_offset
    plan["audio"] = arow
    out = args.out or os.path.join(DEF_DIR, "Multivista_{0}.mp4".format(_stamp()))
    cmd, _meta = build_command(plan, out, warmup=args.warmup, preview=False)
    print("[Multivista] {0} cámaras -> {1}".format(len(cams), out))
    if not args.duration:
        print("         (pulsa 'q' en esta ventana para detener)")
    rc = subprocess.call(cmd, creationflags=CREATE_NO_WINDOW)
    print("[OK] guardado: " + out)
    return rc


def cli_menu(args):
    while True:
        devs, _a = list_devices()
        if not devs:
            print("No se detectaron cámaras de vídeo.")
            return 1
        print("\n===== CamWin32 =====")
        for i, d in enumerate(devs, 1):
            print("  {0}) {1}".format(i, d["label"]))
        print("  M) GRABAR MULTIVISTA (las {0} cámaras en un vídeo)".format(len(devs)))
        print("  0) salir")
        sel = input("Elige: ").strip()
        if sel in ("0", ""):
            return 0
        if sel.lower() == "m":
            args.devices = None
            cli_multi(args)
            continue
        try:
            i = int(sel) - 1
            args.device = devs[i]["spec"]
        except (ValueError, IndexError):
            print("Opción no válida.")
            continue
        mode = input("  1) Vista previa   2) Grabar   3) Foto   Modo: ").strip()
        {"1": cli_preview, "2": cli_record, "3": cli_photo}.get(
            mode, lambda _a: print("Modo no válido."))(args)


def _add_common(sp):
    sp.add_argument("--size", default=None, help="tamaño de vídeo, ej 1280x720")
    sp.add_argument("--fps", type=int, default=0, help="fps (0 = auto)")
    sp.add_argument("--audio", default=None, help="dispositivo de audio")
    sp.add_argument("--duration", type=int, default=0, help="segundos (0 = hasta 'q')")
    sp.add_argument("--out", default=None, help="archivo de salida")
    sp.add_argument("--device", default=None, help="nombre de la cámara")
    return sp


def _build_parser():
    p = argparse.ArgumentParser(prog="camui.py",
                                description="CamWin32 - cámaras / multivista (GUI + consola)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="Abrir la interfaz gráfica (por defecto)").set_defaults(func=None)
    sub.add_parser("list", help="Listar cámaras y audios").set_defaults(func=cli_list)
    _add_common(sub.add_parser("modes", help="Modos de captura de una cámara")
                ).set_defaults(func=cli_modes)
    _add_common(sub.add_parser("menu", help="Menú interactivo")).set_defaults(func=cli_menu)
    _add_common(sub.add_parser("preview", help="Vista previa (1 cámara)")).set_defaults(func=cli_preview)
    _add_common(sub.add_parser("record", help="Grabar vídeo (1 cámara)")).set_defaults(func=cli_record)
    _add_common(sub.add_parser("photo", help="Foto (1 cámara)")).set_defaults(func=cli_photo)
    mp = _add_common(sub.add_parser("multi", help="Grabar varias cámaras en un vídeo"))
    mp.add_argument("--devices", nargs="*", default=None, help="subconjunto de cámaras (en orden)")
    mp.add_argument("--structure", default=None,
                    help="cámaras por fila, ej '2+1' = dos arriba y una abajo (por defecto: automático)")
    mp.add_argument("--canvas", default="adapt",
                    help="'adapt' = el lienzo se adapta al contenido; o un formato fijo: 16:9, 9:16, 1:1…")
    mp.add_argument("--width", type=int, default=1280, help="ancho del vídeo en px")
    mp.add_argument("--transpose", action="store_true", help="repartir por columnas en vez de filas")
    mp.add_argument("--cam-aspects", dest="cam_aspects", default=None,
                    help="formato de cada cámara en orden, ej '4:3,4:3,16:9' (así no hay barras)")
    mp.add_argument("--cols", type=int, default=0, help="(clásico) columnas de la cuadrícula")
    mp.add_argument("--rows", type=int, default=0, help="(clásico) filas de la cuadrícula")
    mp.add_argument("--aspect", default="16:9", help="(clásico) formato de celda, ej 16:9, 4:3, 9:16")
    mp.add_argument("--cellw", type=int, default=640, help="(clásico) ancho de celda")
    mp.add_argument("--cellh", type=int, default=0, help="(clásico) alto de celda (anula --aspect)")
    mp.add_argument("--audio-l", dest="audio_l", default=None, help="audio del canal izquierdo")
    mp.add_argument("--audio-r", dest="audio_r", default=None, help="audio del canal derecho")
    mp.add_argument("--chan-l", dest="chan_l", default="mix", choices=["left", "right", "mix"])
    mp.add_argument("--chan-r", dest="chan_r", default="mix", choices=["left", "right", "mix"])
    mp.add_argument("--av-offset", dest="av_offset", type=int, default=0, help="ms (+ = audio más tarde)")
    mp.add_argument("--sync", default="clock", choices=["clock", "device"])
    mp.add_argument("--crf", type=int, default=23)
    mp.add_argument("--warmup", type=float, default=1.5, help="s de arranque a recortar")
    mp.set_defaults(func=cli_multi)
    return p


def launch_gui():
    setup_logging()
    sys.setswitchinterval(0.001)           # los hilos lectores no esperan 5 ms por el GIL
    if tk is None:
        print("Tkinter no está disponible en este Python; usa los comandos de consola "
              "(python camui.py -h).")
        sys.exit(1)
    os.makedirs(DEF_DIR, exist_ok=True)
    try:                                   # pantallas de alta densidad: texto nítido
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.geometry("1280x790")
    CamApp(root)
    root.mainloop()


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--list":
        argv = ["list"]
    if not argv or argv[0] in ("gui", "--gui"):
        launch_gui()
        return
    setup_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    os.makedirs(DEF_DIR, exist_ok=True)
    sys.exit(func(args) or 0)


if __name__ == "__main__":
    main()
