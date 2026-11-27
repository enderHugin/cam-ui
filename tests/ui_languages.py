# ui_languages.py
"""Idiomas: la ventana principal se ve en ingles y en catalan, y un config.json guardado en otro
idioma (la calidad se guarda con su texto) se sigue entendiendo.
Hace falta un display (Xvfb en Linux): xvfb-run -a python3 tests/ui_languages.py
Cada idioma se prueba en un proceso aparte (el idioma se elige al arrancar, como en el programa)."""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if len(sys.argv) == 1:                       # proceso principal: lanza uno por idioma
    bad = 0
    for code in ("en", "ca", "es"):
        env = dict(os.environ, LANGKIT_LANG=code)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), code], env=env)
        print(code, "OK" if r.returncode == 0 else "FALLA")
        bad += r.returncode != 0
    sys.exit(1 if bad else 0)

CODE = sys.argv[1]
import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

sys.path.insert(0, ROOT)
import camui  # noqa: E402

D = {c: json.load(open(os.path.join(ROOT, "lang", c + ".json"), encoding="utf-8")) for c in ("es", "en", "ca")}
HOME = os.path.join(HERE, "_ui_languages_%s_home" % CODE)
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME)
for n_ in ("PHONECAM_CERT_FILE", "PHONECAM_KEY_FILE", "PHONECAM_CA_CERT_FILE", "PHONECAM_CA_KEY_FILE"):
    setattr(camui, n_, os.path.join(HOME, n_.lower() + ".pem"))
camui.PHONECAM_DEVICES_FILE = os.path.join(HOME, "devices.json")
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.CONFIG = os.path.join(HOME, "config.json")
camui.LOGFILE = os.path.join(HOME, "camui.log")
camui.list_devices = lambda: ([], [])
# config.json guardado con el programa en espanol: calidad «Alta», lienzo «Adaptado al contenido»
with open(camui.CONFIG, "w", encoding="utf-8") as f:
    json.dump({"version": 3, "quality": D["es"]["opt.high_large_file"],
               "canvas": D["es"]["opt.fitted_content"], "phonecam": {"port": 0}}, f)

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("  FALLA:", msg)


check(camui.lang().code == CODE, "idioma activo %r, se pedia %r" % (camui.lang().code, CODE))
root = tk.Tk()
root.geometry("1x1+0+0")
app = camui.CamApp(root)
app._tray_asked = True
root.update()

# 1. pestañas
tabs = [app.nb.tab(i, "text").strip() for i in range(app.nb.index("end"))]
want = [D[CODE][k] for k in ("ui.cameras", "ui.framing", "ui.colour", "ui.audio", "ui.output")]
check(tabs == want, "pestanas %r, se esperaba %r" % (tabs, want))


# 2. ningun texto visible de la ventana se ha quedado con un texto traducido de otro idioma
def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


texts = set()
for w in walk(root):
    try:
        t = w.cget("text")
    except tk.TclError:
        continue
    if isinstance(t, str) and t.strip():
        texts.add(t)
if CODE != "es":
    own = {v for v in D[CODE].values() if isinstance(v, str)}
    leftover = sorted(t for t in texts
                      if t in {v for k, v in D["es"].items() if isinstance(v, str)} and t not in own)
    check(not leftover, "textos sin traducir: %r" % leftover[:10])

# 3. lo guardado en espanol se entiende: calidad «Alta» -> la del idioma de ahora, mismo CRF
check(app.v_quality.get() == D[CODE]["opt.high_large_file"], "calidad %r" % app.v_quality.get())
check(dict(camui.QUALITIES).get(app.v_quality.get()) == 18, "CRF de la calidad leida")
check(app.v_canvas.get() == camui.ADAPT, "lienzo %r" % app.v_canvas.get())

# 4. frase entera del desfase del sonido
s = camui.SyncDialog._snd_text(40)
check(s == D[CODE]["sync.sound_later"].replace("{ms}", "+40"), "desfase del sonido %r" % s)

# 5. barra mini: reconoce los mensajes de estado del idioma de ahora
for status, short in ((camui._t("rec.saved", file="C:/v.mp4", mb="12.5"), camui._t("mini.saved", size="12.5 MB")),
                      (camui._t("app.recording", outfile="C:/v.mp4"), camui._t("mini.recording")),
                      (camui._t("net.network_output_turned_off"), camui._t("mini.net_off"))):
    app._mini_set(status)
    got = app.lbl_ms.cget("text")
    check(got == (short if len(short) <= 20 else short[:19] + "…"), "mini: %r -> %r" % (status, got))

# 6. plurales y frases que antes se montaban a trozos
one, many = camui._t("preview.running", n=1), camui._t("preview.running", n=3)
check(one != many and "1" in one and "3" in many, "plural: %r / %r" % (one, many))
for k in ("info.final_size", "info.cameras", "info.layout_auto_cols", "mode.receiving_fps_approx",
          "err.tips_busy", "badmode.factory", "panic.done", "driver.pause_explain"):
    v = D[CODE].get(k)
    check(v is not None and (CODE == "es" or v != D["es"].get(k) or k == "info.final_size"),
          "%s sin traducir" % k)

# 7. «Sincronizar»: cabecera del mapa (plural) y resultado por camara, en el idioma de ahora
h1, _c = camui.sync_devs_text({"phase": "directo", "rows": [{"st": "ok"}, {"st": "falta"}, {"st": "falta"}]})
check(h1 == camui._t("sync.devs_missing", n=2, total=3), "cabecera de sincronizar %r" % h1)
h2, _c = camui.sync_devs_text({"rows": [{"st": "falta"}]})
check(h2 == camui._t("sync.devs_to_sync", n=1) and h2 != camui._t("sync.devs_to_sync", n=2), "plural %r" % h2)
es_only = [k for k in ("sync.res_cam_later", "sync.rec_ready_count", "diag.conclusion", "tile.phone_wifi")
           if CODE != "es" and D[CODE].get(k) == D["es"].get(k)]
check(not es_only, "sin traducir: %r" % es_only)

try:
    app.on_close()
except Exception:
    pass
try:
    root.destroy()
except tk.TclError:
    pass
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(1 if fails else 0)
