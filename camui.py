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
import re
import sys
import json
import math
import time
import argparse
import queue
import socket
import hashlib
import shutil
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
APP_VERSION = "2026.11.06"            # ha de coincidir con el archivo VERSION del repositorio
APP_PACK = 1                          # crece en cada actualización publicada: así se sabe qué es más nuevo
UPDATE_BASE = "https://enderhugin.github.io/cam-ui/dl/"
UPDATE_STATE = os.path.join(HERE, "actualizacion.json")
UPDATE_BACKUPS = os.path.join(HERE, "backups_programa")
APP_FILE = os.path.abspath(__file__)
UPDATE_FILES = ("camui.py",)


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


def _is_net_spec(spec):
    """True si «spec» es una URL (cámara de red: el móvil con una app como IP Webcam) en vez de
    un nombre de dispositivo DirectShow."""
    return (spec or "").strip().lower().startswith(_NET_SCHEMES)


def _net_video_input(spec, opt=None):
    """Entrada de vídeo por red (http/https MJPEG o rtsp): spec es la URL completa. En pruebas:
    sin cámara real que probar desde aquí, solo validado contra servidores locales de prueba."""
    spec = spec.strip()
    pre = []
    if spec.lower().startswith(("rtsp://", "rtsps://")):
        pre += ["-rtsp_transport", "tcp"]       # evita paquetes perdidos por UDP en wifi
    return pre + ["-i", spec]


def _dshow_input(kind, spec, opt=None):
    """opt: modo de captura (vídeo) o tamaño del búfer en ms (audio; 0 = el del dispositivo)."""
    if kind == "video":
        if _is_net_spec(spec):
            return _net_video_input(spec, opt)
        return (["-f", "dshow", "-rtbufsize", "512M"] + capture_args(opt) +
                ["-i", "video=" + spec])
    buf = 50 if opt is None else int(opt)
    return (["-f", "dshow", "-rtbufsize", "64M"] +
            (["-audio_buffer_size", str(buf)] if buf > 0 else []) + ["-i", "audio=" + spec])


def _rebase(clock, t0, offset_s, audio=False):
    """Expresión setpts/asetpts. clock: línea de tiempo común (T0); device: cada
    flujo empieza en 0. offset_s>0 retrasa el flujo."""
    if clock and audio:
        # Audio: línea de tiempo CONTINUA contando muestras, anclada solo a la hora de la primera.
        # Con la hora de llegada de cada paquete (reloj de pared) cualquier parón de ffmpeg
        # desordena las marcas y aresample rellena con silencio / descarta audio.
        return "STARTPTS-({0:.6f}-{1:.6f})/TB+NB_CONSUMED_SAMPLES/SR/TB".format(t0, offset_s)
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


def video_chain(i, cam, cw, ch, fps, clock, t0):
    f = ["setpts=" + _rebase(clock, t0, cam.get("delay", 0) / 1000.0),
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

    fc = []
    # ---- vídeo ----
    for i, c in enumerate(cams):
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
        fc.append("[ar0][ar1]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[aout]")

    use_meter = preview and plan.get("meter", True)
    null_meter = plan.get("meter_mode", "null") == "null"
    meter = ("asetnsamples=n=4800:p=0,astats=metadata=1:reset=1,"
             "ametadata=mode=print:key=lavfi.astats.1.Peak_level,"
             "ametadata=mode=print:key=lavfi.astats.2.Peak_level" +
             ("[ameter]" if null_meter else ",anullsink"))
    if has_audio:
        if outfile and use_meter:
            fc.append("[aout]asplit=2[arec0][am]")
            fc.append("[arec0]atrim=start={0:.3f},asetpts=PTS-STARTPTS[arec]".format(warmup))
            fc.append("[am]" + meter)
        elif outfile:
            fc.append("[aout]atrim=start={0:.3f},asetpts=PTS-STARTPTS[arec]".format(warmup))
        elif use_meter:
            fc.append("[aout]" + meter)

    # ---- salidas de vídeo ----
    vc = plan.get("vcam")
    if vc and not vc.get("port"):
        vc = None                                  # sin socket de destino no hay cámara virtual
    outs = [k for k, on in (("rec", bool(outfile)), ("pv", bool(preview)), ("vc", bool(vc))) if on]
    names = {"rec": "mrec0", "pv": "mp0", "vc": "mvc0"}
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
#  Motor
# =====================================================================
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
        top.geometry("620x460")
        top.protocol("WM_DELETE_WINDOW", self.close)
        frm = ttk.Frame(top)
        frm.pack(fill="both", expand=True, padx=12, pady=10)
        ttk.Label(frm, text="Actualizaciones", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.lbl_local = ttk.Label(frm, text="")
        self.lbl_local.pack(anchor="w", pady=(6, 0))
        self.lbl_remote = ttk.Label(frm, text="")
        self.lbl_remote.pack(anchor="w")
        self.lbl_status = ttk.Label(frm, style="Muted.TLabel", wraplength=590, justify="left")
        self.lbl_status.pack(anchor="w", fill="x", pady=(8, 6))
        ttk.Label(frm, text="Novedades").pack(anchor="w")
        self.notes = tk.Text(frm, wrap="word", height=12, bg=PANEL, fg=FG, insertbackground=FG,
                             relief="flat", padx=8, pady=6)
        self.notes.pack(fill="both", expand=True, pady=(2, 8))
        self.notes.config(state="disabled")
        self.var_auto = tk.BooleanVar(value=update_state().get("auto", True) is not False)
        ttk.Checkbutton(frm, text="Avisarme al abrir el programa si hay una versión nueva",
                        variable=self.var_auto, command=self._auto_toggled).pack(anchor="w")
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(10, 0))
        self.b_check = ttk.Button(bar, text="Buscar de nuevo", command=self.start_check)
        self.b_check.pack(side="left")
        self.b_back = ttk.Button(bar, text="Volver a la versión anterior", command=self._rollback)
        self.b_back.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Cerrar", command=self.close).pack(side="right")
        self.b_go = ttk.Button(bar, text="Actualizar y reiniciar", command=self._apply)
        self.b_go.pack(side="right", padx=(0, 6))
        self._fill_local()
        self._buttons()

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
        if self._vcam_cfg.get("on"):
            self._vcam_enable(quiet=True)                    # se retoma si estaba activa al cerrar

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
        """Mini sin cámara virtual: no hay vista previa y las cámaras quedan libres."""
        return self.mode == "mini" and not self.vcam_on

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

    def _tray_update(self, menu=False):
        icon = self._tray
        if icon is None:
            return
        try:
            if self.recording != self._tray_rec:
                self._tray_rec = self.recording
                icon.icon = make_tray_image(self.recording)
                icon.title = "CamWin32 · grabando" if self.recording else "CamWin32"
                menu = True
            if menu:
                icon.update_menu()
        except Exception:
            log.warning("bandeja: %r", sys.exc_info()[1])

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
        go, dont_ask = _ask_update_now(self.root, r["info"].get("pack"))
        if dont_ask:
            update_state()["auto"] = False
            update_state_save()
        if go:
            self.show_updates(check=False).apply_result(r)

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
        self.status = ttk.Label(r, text="Listo.", style="Bar.TLabel", anchor="w")
        self.status.pack(fill="x", side="bottom", ipady=3)

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
        if w is None or w.winfo_class() in ("TCombobox", "TScale", "TSpinbox", "Text"):
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
        self.tree = ttk.Treeview(box, columns=("use", "name"), show="headings", height=3,
                                 selectmode="browse")
        self.tree.heading("use", text="")
        self.tree.heading("name", text="Cámara", anchor="w")
        self.tree.column("use", width=34, anchor="center", stretch=False)
        self.tree.column("name", width=300, anchor="w")
        self.tree.pack(fill="x", padx=6, pady=(6, 2))
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<<TreeviewSelect>>", self._tree_select)
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
        """Cámara de red (http/https MJPEG o rtsp): el móvil con una app como IP Webcam, en la
        misma wifi. En pruebas: no hay forma de probarla aquí con un teléfono real."""
        if self.recording or self.busy:
            return
        top = tk.Toplevel(self.root)
        top.title("Añadir cámara de red")
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        frm = ttk.Frame(top)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frm, text="Nombre").grid(row=0, column=0, sticky="w", pady=(0, 4))
        name_var = tk.StringVar(value="Móvil")
        ttk.Entry(frm, textvariable=name_var, width=38).grid(row=0, column=1, pady=(0, 4))
        ttk.Label(frm, text="URL").grid(row=1, column=0, sticky="w")
        url_var = tk.StringVar()
        e_url = ttk.Entry(frm, textvariable=url_var, width=38)
        e_url.grid(row=1, column=1)
        ttk.Label(frm, style="Muted.TLabel", wraplength=380, justify="left",
                  text="La dirección que da la app del móvil (p. ej. «IP Webcam» en Android): algo "
                       "como http://192.168.1.50:8080/video, o por rtsp "
                       "rtsp://192.168.1.50:8080/h264_ulaw.sdp. El móvil y este ordenador deben "
                       "estar en la misma red wifi. Solo vídeo, sin audio."
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
            self.net_cams[label] = {"name": name, "alt": None, "label": label, "spec": url,
                                    "net": True}
            top.destroy()
            self._refresh_devices()
            self._set_status("Cámara de red «{0}» añadida; márcala en la lista para usarla.".format(name))

        ttk.Button(bar, text="Cancelar", command=top.destroy).pack(side="right")
        ttk.Button(bar, text="Añadir", command=ok).pack(side="right", padx=(0, 6))
        e_url.focus_set()
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
        top.transient(self.root)
        box = tk.Text(top, wrap="word", bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                      font=("Consolas", 10), padx=12, pady=10)
        box.insert("1.0", txt)
        box.config(state="disabled")
        box.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=8, pady=(0, 8))

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self._set_status("Informe copiado al portapapeles.")
        ttk.Button(bar, text="Copiar informe", command=copy).pack(side="left")
        ttk.Button(bar, text="Cerrar", command=top.destroy).pack(side="right")

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
        """ffmpeg informa del tamaño real de cada cámara; si no era el supuesto, se recoloca."""
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
                cs.src = sz
                if not known_by_mode:
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
        cfg["net_cams"] = self.net_cams
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
        res = self.eng.start(plan, **kw)
        ok, err = res if isinstance(res, tuple) else (False, str(res))
        if not ok and plan.get("vcam") and not self.eng._abort and \
                re.search(r"127\.0\.0\.1|tcp://|connection (?:refused|reset|timed out)", err, re.I):
            log.warning("la salida de la cámara virtual no conecta; se arranca sin ella:\n%s", err[-300:])
            plan.pop("vcam", None)
            self._tray_q.append(("vcam_off", "ffmpeg no pudo conectar con la cámara virtual (¿cortafuegos?)."))
            res = self.eng.start(plan, **kw)
        for _ in range(5):
            ok, err = res if isinstance(res, tuple) else (False, str(res))
            if ok or self.eng._abort or not is_mode_error(err):
                break
            if not self._drop_bad_mode(plan, err):
                break
            res = self.eng.start(plan, **kw)
        return res

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
            while self._bgq:
                done, res, gen = self._bgq.popleft()
                if gen != self._gen:
                    continue                  # trabajo anterior a un reinicio de pánico
                self.busy = False
                done(res)
                self._refresh_buttons()
            if (self.previewing or self.recording) and not self.busy and not self.eng.is_running():
                self._on_died()
            if self.previewing and self.mode != "mini":
                self._draw_frame()
            self._check_sources()
            self._update_mode_label()
            self._tick_n = (getattr(self, "_tick_n", 0) + 1) % 3
            if self._tick_n == 0 and self.mode != "mini":   # VU a ~10 Hz (cuestan 4 ms cada vez)
                self._draw_meters()
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
        self._set_idle("ffmpeg se detuvo.")
        msg = "ffmpeg se detuvo de forma inesperada (¿cámara desconectada?).\n\n" + err
        if was_rec:
            ok, shown, _m = self._finalize(path, final)
            msg += "\n\nEl vídeo parcial está en:\n{0}".format(shown)
        messagebox.showerror("Error", msg)

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
